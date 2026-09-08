# -*- coding: utf-8 -*-
"""2026-09-08 质量闭环：通过真实临时项目验证已确认缺陷。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts.author_memory import AuthorMemory, AuthorMemoryError
from scripts.audit_state import AuditState, get_audit_state_path, render_inherited_items_section, save_audit_state
from scripts.ledger_engine import AssetItem, LedgerDirtyError, LedgerState, extract_heuristic_assets, save_ledger_state
from scripts.ledger_engine import check_dirty_state
from scripts.safe_io import SafeIOWriteError, write_file_safe
from scripts.story_audit import (
    apply_fix,
    audit_chapter,
    audit_scope,
    checkpoint_volume,
    init_ledger,
    sync_ledger_from_md,
)
from scripts.types import PatchSpec


@pytest.fixture
def temporary_project():
    """所有写入测试均与仓库内的正文和设定隔离。"""
    with TemporaryDirectory(prefix="story_audit_quality_") as directory:
        project = Path(directory)
        (project / "正文").mkdir()
        yield project


def _write_chapter(project, index, text):
    path = project / "正文" / ("第{:03d}章.txt".format(index))
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot(project):
    """同时检查原文件内容与新目录等写入副作用。"""
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _patch_arguments(project, source, data):
    if source == "dict":
        return {"patch": data}
    if source == "file":
        path = project / "patch.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"patch_file": path}
    if source == "spec":
        values = {"context_before": "", "context_after": ""}
        values.update(data)
        return {"patch": PatchSpec(**values)}
    return data


@pytest.mark.parametrize("source", ["dict", "file"])
@pytest.mark.parametrize("missing", ["target_line", "old_text", "new_text"])
def test_r06_missing_patch_fields_preserve_project(temporary_project, source, missing):
    """缺字段不能隐式变成删除补丁，也不能产生备份或报告。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n前文\n旧正文\n后文\n")
    data = {"target_line": 3, "old_text": "旧正文", "new_text": "新正文"}
    del data[missing]
    arguments = _patch_arguments(project, source, data)
    original = _snapshot(project)

    assert apply_fix(project, chapter_index=1, silent=True, **arguments) == 3
    assert _snapshot(project) == original


@pytest.mark.parametrize("failure", ["unreadable_chapter", "dirty_ledger"])
def test_r02_failed_batch_keeps_state_and_returns_no_old_report(temporary_project, failure):
    """批量遇到失败章应停止并返回 3，不登记完成或清空原开放问题。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    chapter2 = _write_chapter(project, 2, "第二章\n他走进山林。\n")
    if failure == "unreadable_chapter":
        chapter2.write_bytes(b"\x00")
    else:
        _, json_path, md_path = _write_ledger(project)
        dirty_time = json_path.stat().st_mtime + 10
        os.utime(md_path, (dirty_time, dirty_time))
    reports = project / "reports"
    state_path = save_audit_state(
        AuditState(completed_chapters=[7], open_defects=[{"chapter": 2, "issue": "已有待核问题"}]),
        reports,
    )
    state_bytes = state_path.read_bytes()
    old_report = reports / "BATCH_SUMMARY_SCOPE_1-2.md"
    old_report.write_text("上次批量报告", encoding="utf-8")
    old_report_bytes = old_report.read_bytes()

    assert audit_scope(project, scope_str="1-2", silent=True) == (3, Path(""))
    assert state_path.read_bytes() == state_bytes
    assert old_report.read_bytes() == old_report_bytes
    assert not (reports / "单章审查" / "001-100章" / "第002章_审查报告.md").exists()


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_r03_init_rejects_incomplete_scan_without_persisting(temporary_project, existing_ledger):
    """后续章节读取失败时，前面内存中提取的资产不能冒充完整建账落盘。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n【获得：灵石×10枚】\n")
    bad_chapter = _write_chapter(project, 2, "第二章\n")
    bad_chapter.write_bytes(b"\x00")
    if existing_ledger:
        _write_ledger(project)
    original = _snapshot(project)

    assert init_ledger(project, scope_str="1-2", silent=True) == (3, Path(""))
    assert _snapshot(project) == original


def test_r07_scope_failure_does_not_return_previous_report(temporary_project):
    """相同范围之前成功过，也不能给本次错误调用返回旧报告。"""
    project = temporary_project
    chapter = _write_chapter(project, 1, "第一章\n风雪停了。\n")
    first_code, first_report = audit_scope(project, scope_str="1-1", silent=True)
    assert first_code == 0
    old_report = first_report.read_bytes()
    chapter.unlink()

    assert audit_scope(project, scope_str="1-1", silent=True) == (3, Path(""))
    assert first_report.read_bytes() == old_report


@pytest.mark.parametrize("source", ["dict", "file", "fields", "spec"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("target_line", "bad"),
        ("target_line", 2.9),
        ("target_line", True),
        ("target_line", 0),
        ("target_line", -1),
        ("new_text", None),
        ("new_text", 42),
        ("context_before", None),
        ("context_after", False),
    ],
)
def test_r06_invalid_patch_values_preserve_project(temporary_project, source, field, value):
    """所有公开补丁入口采用相同的字段类型与行号校验。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n前文\n旧正文\n后文\n")
    data = {"target_line": 3, "old_text": "旧正文", "new_text": "新正文"}
    data[field] = value
    arguments = _patch_arguments(project, source, data)
    original = _snapshot(project)

    assert apply_fix(project, chapter_index=1, silent=True, **arguments) == 3
    assert _snapshot(project) == original


@pytest.mark.parametrize("source", ["dict", "file", "fields", "spec"])
@pytest.mark.parametrize("new_text", ["", "新正文\n另一行"])
def test_r06_valid_patch_preserves_explicit_delete_and_backup(temporary_project, source, new_text):
    """显式空串仍表示合法删除，正常 PatchSpec 与多行替换保持兼容。"""
    project = temporary_project
    chapter = _write_chapter(project, 1, "第一章\n前文\n旧正文\n后文\n")
    original = chapter.read_bytes()
    data = {"target_line": 3, "old_text": "旧正文", "new_text": new_text}
    arguments = _patch_arguments(project, source, data)

    assert apply_fix(project, chapter_index=1, silent=True, **arguments) == 0
    assert chapter.read_text(encoding="utf-8") == "第一章\n前文\n" + new_text + "\n后文\n"
    backups = list((project / "reports" / ".bak").glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def _write_ledger(project):
    settings = project / "设定"
    settings.mkdir(exist_ok=True)
    json_path = settings / "资源账本.json"
    md_path = settings / "资源账本.md"
    state = LedgerState(
        last_updated_chapter=1.0,
        assets={
            "kept": AssetItem(
                id="kept", name="旧钥匙", category="装备道具", quantity=1,
                unit="把", owner="陆离", current_holder="陆离",
            )
        },
    )
    save_ledger_state(state, json_path, md_path, force=True)
    return state, json_path, md_path


@pytest.mark.parametrize("operation", ["audit", "init", "sync", "checkpoint"])
@pytest.mark.parametrize("corruption", ["json", "category", "asset_shape", "stash_shape"])
def test_r01_corrupt_ledger_never_becomes_empty_state(temporary_project, operation, corruption):
    """已存在的坏账本必须阻断，不能被当作首次建账覆盖。"""
    project = temporary_project
    _write_chapter(project, 1, '第一章\n<!-- audit:stash name="新线索" origin="第1章" status="pending" -->\n')
    state, json_path, _ = _write_ledger(project)
    data = state.to_dict()
    if corruption == "category":
        data["assets"]["bad"] = dict(data["assets"]["kept"], id="bad", category="非法类别")
    elif corruption == "asset_shape":
        data["assets"]["bad"] = "损坏的资产对象"
    elif corruption == "stash_shape":
        data["foreshadowing_stash"] = "损坏的伏笔列表"
    json_path.write_text("{broken" if corruption == "json" else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    original = _snapshot(project)

    if operation == "audit":
        code, report = audit_chapter(project, silent=True)
        assert report == Path("")
    elif operation == "init":
        code, report = init_ledger(project, silent=True)
        assert report == Path("")
    elif operation == "sync":
        code = sync_ledger_from_md(project, silent=True)
    else:
        code = checkpoint_volume(project, volume=1, silent=True)
    assert code == 3
    assert _snapshot(project) == original


@pytest.mark.parametrize("operation", ["chapter", "scope"])
@pytest.mark.parametrize(
    "data",
    [
        [],
        {"completed_chapters": ["bad"], "open_defects": [{"issue": "旧问题"}]},
        {"completed_chapters": "12"},
        {"open_defects": {"old": {"issue": "旧问题"}}},
        {"open_defects": ["损坏的问题对象"]},
        {"foreshadowing_commitments": "损坏的伏笔列表"},
    ],
)
def test_r01_corrupt_audit_state_blocks_before_writes(temporary_project, operation, data):
    """错误状态结构不能被静默清空，也不能先生成本次成功报告。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    reports = project / "reports"
    reports.mkdir()
    (reports / ".audit_state.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (reports / "LATEST_REPORT.md").write_text("上一轮可信报告", encoding="utf-8")
    original = _snapshot(project)

    if operation == "chapter":
        code, report = audit_chapter(project, silent=True)
    else:
        code, report = audit_scope(project, scope_str="1-1", silent=True)
    assert code == 3
    assert report == Path("")
    assert _snapshot(project) == original


@pytest.mark.parametrize("preferences", [["旧偏好"], {"old": "损坏的偏好对象"}])
def test_r01_corrupt_author_preferences_preserve_saved_files(temporary_project, preferences):
    """录入新偏好前必须拒绝已有的损坏偏好结构。"""
    memory = AuthorMemory(temporary_project)
    memory.init()
    state = memory.load_state()
    state["preferences"] = preferences
    memory.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    original = _snapshot(temporary_project)

    with pytest.raises(AuthorMemoryError):
        memory.record("人物语气", "简短直接")
    assert _snapshot(temporary_project) == original


def test_r01_init_preserves_existing_markdown_without_json(temporary_project):
    """仅剩 Markdown 的已有账本需要先显式同步，不能自动重建覆盖。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    _, json_path, _ = _write_ledger(project)
    json_path.unlink()
    original = _snapshot(project)

    assert init_ledger(project, silent=True) == (3, Path(""))
    assert _snapshot(project) == original


@pytest.mark.parametrize(
    "field,value",
    [("quantity", "误填"), ("quantity", float("nan")), ("name", 7), ("constraints", []), ("history", {})],
)
def test_r01_corrupt_asset_fields_block_before_writes(temporary_project, field, value):
    """字段不能在转换或输出时才报错；带新伏笔的审查也必须先拒绝坏资产。"""
    project = temporary_project
    _write_chapter(project, 1, '第一章\n<!-- audit:stash name="新线索" origin="第1章" status="pending" -->\n')
    state, json_path, _ = _write_ledger(project)
    data = state.to_dict()
    data["assets"]["kept"][field] = value
    json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    original = _snapshot(project)

    assert audit_chapter(project, silent=True) == (3, Path(""))
    assert _snapshot(project) == original


@pytest.mark.parametrize(
    "data",
    [
        {"open_defects": [{"chapter": "坏章号", "issue": "原未决问题"}]},
        {"open_defects": [{"chapter": 1, "issue": 7}]},
        {"completed_chapters": [float("nan")]},
        {"last_scope": []},
    ],
)
def test_r01_corrupt_audit_fields_keep_trusted_report(temporary_project, data):
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    reports = project / "reports"
    reports.mkdir()
    get_audit_state_path(reports).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (reports / "LATEST_REPORT.md").write_text("原可信报告", encoding="utf-8")
    original = _snapshot(project)

    assert audit_chapter(project, silent=True) == (3, Path(""))
    assert _snapshot(project) == original


@pytest.mark.parametrize("field,value", [("value", 7), ("key", []), ("confidence", [])])
def test_r01_corrupt_preference_fields_rejected_before_record(temporary_project, field, value):
    memory = AuthorMemory(temporary_project)
    memory.record("旧偏好", "保留原内容")
    state = memory.load_state()
    next(iter(state["preferences"].values()))[field] = value
    memory.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    original = _snapshot(temporary_project)

    with pytest.raises(AuthorMemoryError):
        memory.record("新偏好", "简洁")
    assert _snapshot(temporary_project) == original


def test_r03_init_save_conflict_keeps_tuple_contract_and_silence(temporary_project, capsys):
    """扫描完成后的并发编辑拦截仍应遵守公开返回形状和静默约定。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    _write_ledger(project)
    original = _snapshot(project)

    with patch("scripts.story_audit.save_ledger_state", side_effect=LedgerDirtyError("模拟校验后人工编辑冲突")):
        result = init_ledger(project, silent=True)
    assert result == (3, Path(""))
    assert _snapshot(project) == original
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def _edit_asset_cell(md_path, item_id, column, value):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if "| 资产ID |" in line)
    names = [cell.strip() for cell in header.split("|")[1:-1]]
    column_index = names.index(column)
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if cells and cells[0] == item_id:
            cells[column_index] = str(value)
            lines[index] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise AssertionError("测试账本未找到目标资产行")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "column,field,value",
    [
        ("类别", "category", "规则诡器"),
        ("单位", "unit", "柄"),
        ("所有者", "owner", "作者新设定"),
        ("初始章节", "origin_chapter", 4.5),
    ],
)
def test_r08_single_column_edit_preserves_metadata(temporary_project, column, field, value):
    """只改一个过去遗漏的字段也必须生效，历史和借出信息仍保留。"""
    project = temporary_project
    state, json_path, md_path = _write_ledger(project)
    item = state.assets["kept"]
    item.status = "LENT_OUT"
    item.lend_meta = {"borrower": "借用人", "until_chapter": 8}
    item.history = [{"action": "original", "chapter": 1}]
    save_ledger_state(state, json_path, md_path, force=True)
    _edit_asset_cell(md_path, "kept", column, value)

    assert sync_ledger_from_md(project, silent=True) == 0
    saved = json.loads(json_path.read_text(encoding="utf-8"))["assets"]["kept"]
    assert saved[field] == value
    assert saved["lend_meta"] == item.lend_meta
    assert saved["history"][:-1] == item.history
    assert saved["history"][-1]["action"] == "sync_from_markdown"
    assert "| {} |".format(value) in md_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "corruption",
    ["short_row", "empty_row", "quantity", "category", "status", "chapter", "nan_quantity", "inf_chapter", "missing_id", "missing_column", "duplicate_id", "missing_end_pipe", "no_asset_table", "constraints"],
)
def test_r09_bad_markdown_never_deletes_or_rewrites_assets(temporary_project, corruption):
    """合法首行随后遇到坏行，也必须保留两轨原字节而非部分提交或删除。"""
    project = temporary_project
    state, json_path, md_path = _write_ledger(project)
    state.assets["second"] = AssetItem(id="second", name="第二把钥匙", category="装备道具", quantity=1, unit="把")
    save_ledger_state(state, json_path, md_path, force=True)
    _edit_asset_cell(md_path, "kept", "数量", 8)
    bad_fields = {
        "quantity": ("数量", "很多"),
        "category": ("类别", "未知类别"),
        "status": ("状态", "UNKNOWN"),
        "chapter": ("初始章节", "序章"),
        "nan_quantity": ("数量", "nan"),
        "inf_chapter": ("初始章节", "inf"),
        "missing_id": ("资产ID", ""),
        "constraints": ("约束说明", "没有键值对的约束说明"),
    }
    if corruption in bad_fields:
        _edit_asset_cell(md_path, "second", *bad_fields[corruption])
    else:
        lines = md_path.read_text(encoding="utf-8").splitlines()
        row_index = next(index for index, line in enumerate(lines) if line.startswith("| second |"))
        if corruption == "short_row":
            lines[row_index] = "| second | 第二把钥匙 | 装备道具 | 1 | 把 |"
        elif corruption == "empty_row":
            lines[row_index] = "|"
        elif corruption == "missing_column":
            lines = [line.replace("| 所有者 |", "| 错别列 |") for line in lines]
        elif corruption == "duplicate_id":
            lines.insert(row_index + 1, lines[row_index])
        elif corruption == "missing_end_pipe":
            lines[row_index] = lines[row_index].rstrip()[:-1]
        else:
            lines = ["此处正在补记，没有可同步的资产表。"]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    original = _snapshot(project)

    assert sync_ledger_from_md(project, silent=True) == 3
    assert _snapshot(project) == original


def test_r09_large_integer_quantity_is_preserved_exactly(temporary_project):
    """校验数值时不能先经 float 丢失大整数数量的精度。"""
    project = temporary_project
    _, json_path, md_path = _write_ledger(project)
    quantity = 9007199254740993
    _edit_asset_cell(md_path, "kept", "数量", quantity)

    assert sync_ledger_from_md(project, silent=True) == 0
    saved = json.loads(json_path.read_text(encoding="utf-8"))["assets"]["kept"]
    assert saved["quantity"] == quantity


@pytest.mark.parametrize("table_index", [0, 1])
@pytest.mark.parametrize("corruption", ["missing_start_pipe", "removed_header", "blank_before_row"])
def test_r09_malformed_table_cannot_hide_behind_another_valid_table(temporary_project, table_index, corruption):
    """冷热表中的任一张损坏，都不能借另一张合法表执行部分同步。"""
    state, json_path, md_path = _write_ledger(temporary_project)
    state.assets["second"] = AssetItem(id="second", name="第二把钥匙", category="装备道具", quantity=1, unit="把")
    state.assets["cold"] = AssetItem(id="cold", name="旧钥匙", category="装备道具", quantity=0, unit="把", status="CONSUMED")
    save_ledger_state(state, json_path, md_path, force=True)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    headers = [index for index, line in enumerate(lines) if line.startswith("| 资产ID |")]
    header_index = headers[table_index]
    if corruption == "missing_start_pipe":
        lines[header_index] = lines[header_index][1:]
    elif corruption == "removed_header":
        del lines[header_index]
    else:
        lines.insert(header_index + 2, "")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    original = _snapshot(temporary_project)

    assert sync_ledger_from_md(temporary_project, silent=True) == 3
    assert _snapshot(temporary_project) == original


@pytest.mark.parametrize("holder", [None, "借用人"])
def test_r12_default_holder_follows_owner_without_overriding_explicit_holder(holder):
    arguments = {"id": "sword", "name": "长剑", "category": "装备道具", "quantity": 1, "unit": "把", "owner": "陆离"}
    if holder is not None:
        arguments["current_holder"] = holder

    item = AssetItem(**arguments)
    assert item.current_holder == ("陆离" if holder is None else holder)


@pytest.mark.parametrize(
    "text",
    [
        "【获得：灵石×3枚】",
        "【噪声【获得：灵石×3枚】】",
        "【普通消息】\n【获得：灵石×3枚】",
        "【获得：灵石×3枚】\n【尚未闭合",
    ],
)
def test_r14_ledger_bracket_scan_preserves_valid_blocks(text):
    assets = extract_heuristic_assets(text, chapter_index=1)
    assert [(item["name"], item["quantity"], item["unit"]) for item in assets] == [("灵石", 3, "枚")]


def test_r14_ledger_bracket_scan_has_bounded_invalid_prefix():
    """给病理输入宽裕的独立进程预算，防止正则重复回溯且不截掉合法后缀。"""
    script = (
        "from scripts.ledger_engine import extract_heuristic_assets\n"
        "text = '【' * 60000 + '】\\n【获得：灵石×3枚】'\n"
        "assets = extract_heuristic_assets(text, 1)\n"
        "assert [(x['name'], x['quantity']) for x in assets] == [('灵石', 3)]\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("60000 个无效左括号的扫描超过 5 秒预算，存在重复后缀扫描")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("existing", [False, True])
def test_r16_second_ledger_write_failure_restores_original_files(temporary_project, existing):
    """第二轨写入失败时恢复原字节；首次保存也不能留下单独 JSON。"""
    project = temporary_project
    if existing:
        state, json_path, md_path = _write_ledger(project)
        # 回滚必须保留 BOM、换行与原编码，不能重渲染成相同语义的不同字节。
        json_path.write_bytes(b"\xef\xbb\xbf" + json_path.read_bytes().replace(b"\n", b"\r\n"))
        md_text = md_path.read_text(encoding="utf-8")
        md_path.write_bytes(md_text.replace("\n", "\r\n").encode("gb18030"))
        state.assets["kept"].quantity = 9
    else:
        settings = project / "设定"
        settings.mkdir()
        json_path = settings / "资源账本.json"
        md_path = settings / "资源账本.md"
        state = LedgerState()
    original = _snapshot(project)

    def fail_markdown(path, content, *args, **kwargs):
        if Path(path) == md_path:
            raise SafeIOWriteError("模拟第二轨写入失败")
        return write_file_safe(path, content, *args, **kwargs)

    with patch("scripts.ledger_engine.write_file_safe", side_effect=fail_markdown):
        with pytest.raises(SafeIOWriteError):
            save_ledger_state(state, json_path, md_path, force=True)
    assert _snapshot(project) == original


@pytest.mark.parametrize("initial_files", ["none", "json", "md", "both"])
@pytest.mark.parametrize("failed_target", ["json", "md"])
def test_r16_write_failure_preserves_each_original_file_and_timestamp(temporary_project, initial_files, failed_target):
    """首次、单轨遗留及完整账本在任一目标写失败时都恢复原树和时间戳。"""
    json_path = temporary_project / "新设定" / "账本" / "资源账本.json"
    md_path = json_path.with_suffix(".md")
    state = LedgerState()
    if initial_files != "none":
        json_path.parent.mkdir(parents=True)
        if initial_files in ("json", "both"):
            json_path.write_bytes(b"\xef\xbb\xbf{\r\n}\r\n")
            os.utime(json_path, ns=(1600000000000000000, 1600000000000000000))
        if initial_files in ("md", "both"):
            md_path.write_bytes("旧账本\r\n".encode("gb18030"))
            os.utime(md_path, ns=(1600000000010000000, 1600000000010000000))
    original = _snapshot(temporary_project)
    original_mtimes = {path: path.stat().st_mtime_ns for path in (json_path, md_path) if path.exists()}
    failing_path = json_path if failed_target == "json" else md_path

    def fail_selected_target(path, content, *args, **kwargs):
        if Path(path) == failing_path:
            raise SafeIOWriteError("模拟目标写入失败")
        return write_file_safe(path, content, *args, **kwargs)

    with patch("scripts.ledger_engine.write_file_safe", side_effect=fail_selected_target):
        with pytest.raises(SafeIOWriteError):
            save_ledger_state(state, json_path, md_path, force=True)
    assert _snapshot(temporary_project) == original
    assert {path: path.stat().st_mtime_ns for path in original_mtimes} == original_mtimes


def test_r16_backup_failure_precedes_any_target_mutation(temporary_project):
    state, json_path, md_path = _write_ledger(temporary_project)
    original = _snapshot(temporary_project)
    real_read_bytes = Path.read_bytes

    def fail_backup_read(path):
        if path == md_path:
            raise PermissionError("模拟旧 Markdown 备份读取失败")
        return real_read_bytes(path)

    with patch.object(Path, "read_bytes", fail_backup_read):
        with pytest.raises(SafeIOWriteError):
            save_ledger_state(state, json_path, md_path, force=True)
    assert _snapshot(temporary_project) == original


def test_r16_render_failure_happens_before_any_target_write(temporary_project):
    state, json_path, md_path = _write_ledger(temporary_project)
    state.assets["kept"].quantity = "误填"
    original = _snapshot(temporary_project)

    with pytest.raises((TypeError, ValueError, SafeIOWriteError)):
        save_ledger_state(state, json_path, md_path, force=True)
    assert _snapshot(temporary_project) == original


def test_r16_failed_rollback_preserves_recovery_copy_and_blocks_reuse(temporary_project):
    """若外部锁阻止回滚，应保留可恢复旧数据并持续标记冲突。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n风雪停了。\n")
    state, json_path, md_path = _write_ledger(project)
    original_json = json_path.read_bytes()
    state.assets["kept"].quantity = 9
    real_replace = os.replace

    def fail_markdown(path, content, *args, **kwargs):
        if Path(path) == md_path:
            raise SafeIOWriteError("模拟第二轨写入失败")
        return write_file_safe(path, content, *args, **kwargs)

    def fail_original_restore(source, destination):
        if Path(destination) == json_path and Path(source).read_bytes() == original_json:
            raise PermissionError("模拟原 JSON 被其他程序锁定，暂时不能回滚")
        return real_replace(source, destination)

    with patch("scripts.ledger_engine.write_file_safe", side_effect=fail_markdown), patch("os.replace", side_effect=fail_original_restore):
        with pytest.raises(SafeIOWriteError):
            save_ledger_state(state, json_path, md_path, force=True)

    assert check_dirty_state(md_path, json_path)
    assert any(path.is_file() and path != json_path and path.read_bytes() == original_json for path in json_path.parent.iterdir())
    interrupted = _snapshot(project)
    with pytest.raises(SafeIOWriteError):
        save_ledger_state(state, json_path, md_path, force=True)
    assert audit_chapter(project, force=True, silent=True) == (3, Path(""))
    assert _snapshot(project) == interrupted


@pytest.mark.parametrize("operation", ["chapter", "scope"])
@pytest.mark.parametrize("requested_mode", ["auto", "full", "lean", "solo"])
@pytest.mark.parametrize("runtime", ["codex", "generic", "subagent"])
def test_r04_python_reports_only_claim_the_executed_precheck(temporary_project, operation, requested_mode, runtime, capsys):
    """明显未加标记的语义矛盾也不能被模板宣告已经专家验证通过。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n沈舟已经死亡，遗体当夜下葬。\n")
    _write_chapter(project, 2, "第二章\n沈舟独自踏进城门。\n")
    environment = {} if runtime == "generic" else {"CODEX_APP": "1"}
    if runtime == "subagent":
        environment["STORY_AUDIT_SUBAGENT"] = "1"
    with patch.dict(os.environ, environment, clear=True):
        if operation == "chapter":
            code, report_path = audit_chapter(project, chapter_index=2, mode=requested_mode)
        else:
            code, report_path = audit_scope(project, "1-2", mode=requested_mode)
    assert code == 0
    captured = capsys.readouterr()
    assert "Expert Review: not_executed" in captured.out
    assert "Effective Mode: solo" in captured.out
    assert captured.err == ""

    reports = {report_path, *project.glob("reports/单章审查/*/*_审查报告.md")}
    for path in reports:
        report = path.read_text(encoding="utf-8")
        assert "Requested Mode: " + requested_mode in report
        assert "Effective Mode: solo" in report
        assert "Expert Review: not_executed" in report
        assert "Review Stage: deterministic_precheck" in report
        for unsupported_claim in (
            "无凭空出装或资产冲突", "主线推进平稳", "当前章节严格遵循", "未见明显恶性毒点",
            "视角平稳继承", "无缝顺承", "紧密相承", "主角主视点顺承", "绿灯合格通过",
            "(Agent A)", "(Agent B)", "(Agent C)", "(Agent D)",
        ):
            assert unsupported_claim not in report

    bundle = json.loads((project / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8"))
    meta = bundle["meta"]
    assert meta["requested_mode"] == requested_mode
    assert meta["effective_mode"] == bundle["runtime_dispatch"]["effective_mode"] == "solo"
    assert meta["review_stage"] == "deterministic_precheck"
    assert meta["expert_review_executed"] is False
    if requested_mode == "solo":
        assert meta["fallback_reason"] == "none"
    else:
        assert "python_api_deterministic_only" in meta["fallback_reason"]
        if runtime == "subagent":
            assert "subagent_recursion_guard_active" in meta["fallback_reason"]
        elif runtime == "generic":
            expected = "unsupported_multiagent_runtime" if requested_mode == "auto" else "runtime_not_supporting_subagents"
            assert expected in meta["fallback_reason"]


def test_r04_empty_inheritance_does_not_claim_causal_consistency():
    section = render_inherited_items_section({})
    assert "因果链条闭合良好" not in section
    assert "未执行语义核验" in section


def test_r04_nonblocking_platform_findings_are_not_reported_as_no_findings(temporary_project):
    _write_chapter(temporary_project, 1, "第一章\n风雪停了。\n")
    code, path = audit_chapter(temporary_project, silent=True)
    assert code == 0
    report = path.read_text(encoding="utf-8")
    assert "P3 PLATFORM_GENERIC" in report
    assert "未命中 P0/P1 平台规则" in report


@pytest.mark.parametrize("operation", ["chapter", "scope"])
def test_r04_inherited_platform_defect_preserves_its_real_metadata(temporary_project, operation):
    """平台第一人称问题跨批继承时，不能改写成因果或资产统一建议。"""
    project = temporary_project
    _write_chapter(project, 1, "第一章\n" + "他走进庭院。\n" * 30)
    _write_chapter(project, 2, "第二章\n风雪停了。\n")
    if operation == "chapter":
        result = audit_chapter(project, chapter_index=1, platform="zhihu", strict=True, silent=True)
    else:
        result = audit_scope(project, "1-1", platform="zhihu", strict=True, silent=True)
    assert result[0] == 1
    reports = project / "reports"
    bundle = json.loads((reports / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8"))
    real_finding = next(item for item in bundle["platform_diagnostics"]["findings"] if item["severity"] == "P1")
    state = json.loads(get_audit_state_path(reports).read_text(encoding="utf-8"))
    persisted = next(item for item in state["open_defects"] if item["issue"] == real_finding["issue"])
    for field in ("category", "fix", "location", "evidence"):
        assert persisted[field] == real_finding[field]

    code, report_path = audit_scope(project, "2-2", silent=True)
    assert code == 0
    report = report_path.read_text(encoding="utf-8")
    assert real_finding["fix"] in report
    assert "严格依据账本与主线事实对齐，杜绝主观文学发挥" not in report


@pytest.mark.parametrize("operation", ["chapter", "scope", "init"])
@pytest.mark.parametrize("field,value", [("name", []), ("origin", {}), ("status", 7), ("source_chapter", []), ("source_chapter", float("nan"))])
def test_r13_invalid_saved_stash_fields_fail_before_any_write(temporary_project, operation, field, value):
    """新增跨批映射会读取的旧伏笔字段必须在任何写入前验证。"""
    project = temporary_project
    _write_chapter(project, 1, '第一章\n<!-- audit:stash name="新线索" -->\n')
    state, json_path, _ = _write_ledger(project)
    data = state.to_dict()
    data["foreshadowing_stash"] = [{"name": "旧线索", "origin": "第1章", "status": "pending", field: value}]
    json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    original = _snapshot(project)
    if operation == "chapter":
        result = audit_chapter(project, silent=True)
    elif operation == "scope":
        result = audit_scope(project, "1-1", silent=True)
    else:
        result = init_ledger(project, silent=True)
    assert result == (3, Path(""))
    assert _snapshot(project) == original


def test_r13_unknown_legacy_source_is_preserved_without_inventing_a_chapter(temporary_project):
    project = temporary_project
    _write_chapter(project, 2, "第二章\n风雪停了。\n")
    _write_chapter(project, 3, "第三章\n少年踏上石阶。\n")
    state, json_path, md_path = _write_ledger(project)
    state.foreshadowing_stash = [{"name": "石碑谜语", "origin": "山门背面的石碑", "status": "pending"}]
    save_ledger_state(state, json_path, md_path, force=True)

    assert audit_chapter(project, chapter_index=2, silent=True)[0] == 0
    stored = json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))
    item = stored["foreshadowing_commitments"][0]
    assert item["origin_chapter"] is None
    assert item["note"] == "山门背面的石碑"
    code, report_path = audit_chapter(project, chapter_index=3, silent=True)
    assert code == 0
    report = report_path.read_text(encoding="utf-8")
    assert "石碑谜语" in report
    assert "来源未记录" in report
    assert "第None章" not in report


def test_r13_tag_without_origin_keeps_its_first_observed_chapter(temporary_project):
    project = temporary_project
    tag = '<!-- audit:stash name="船底声音" -->'
    _write_chapter(project, 1, "第一章\n" + tag + "\n")
    _write_chapter(project, 2, "第二章\n" + tag + "\n")
    assert audit_scope(project, "1-2", silent=True)[0] == 0
    stored = json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))
    commitments = stored["foreshadowing_commitments"]
    assert len(commitments) == 1
    assert commitments[0]["origin_chapter"] == 1
    assert commitments[0]["status"] == "pending"


@pytest.mark.parametrize("operation", ["chapter", "scope"])
@pytest.mark.parametrize("origin", [[], float("nan"), True])
def test_r13_invalid_saved_commitment_source_fails_before_any_write(temporary_project, operation, origin):
    _write_chapter(temporary_project, 1, '第一章\n<!-- audit:stash name="新线索" -->\n')
    reports = temporary_project / "reports"
    save_audit_state(AuditState(foreshadowing_commitments=[{"tag": "旧线索", "origin_chapter": origin}]), reports)
    original = _snapshot(temporary_project)
    if operation == "chapter":
        result = audit_chapter(temporary_project, silent=True)
    else:
        result = audit_scope(temporary_project, "1-1", silent=True)
    assert result == (3, Path(""))
    assert _snapshot(temporary_project) == original
