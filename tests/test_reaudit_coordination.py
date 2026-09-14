# -*- coding: utf-8 -*-
"""F05：安全回写后的报告失效、待办复审与版本事件协调。"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.audit_state import (
    AuditState,
    get_audit_state_path,
    is_chapter_version_audited,
    load_audit_state,
    save_audit_state,
)
from scripts.ledger_engine import LedgerState, save_ledger_state
from scripts.safe_io import SafeIOWriteError


OLD_TEXT = "风停了。"
NEW_TEXT = "雪停了。"


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f05_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text(
            f"第一章\n他握紧了钥匙。\n{OLD_TEXT}\n", encoding="utf-8"
        )
        (root / "正文" / "第002章.txt").write_text("第二章\n海面雾气未散。\n", encoding="utf-8")
        (root / "正文" / "第003章.txt").write_text("第三章\n灯塔亮起。\n", encoding="utf-8")
        yield root


def _state(project: Path) -> dict:
    return json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _chapter_text_version(project: Path, chapter: int) -> str:
    text = (project / "正文" / f"第{chapter:03d}章.txt").read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit(project: Path, chapters):
    for chapter in chapters:
        assert story_audit.audit_chapter(project, chapter_index=chapter, mode="solo", silent=True)[0] == 0


def _patch(project: Path, chapter: int = 1, **overrides) -> int:
    options = {
        "chapter_index": chapter,
        "target_line": 3,
        "old_text": OLD_TEXT,
        "new_text": NEW_TEXT,
    }
    options.update(overrides)
    return story_audit.apply_fix(project, silent=True, **options)


def _pending_payload(project: Path, **options) -> dict:
    code, payload = story_audit.get_pending_rechecks(project, silent=True, **options)
    assert code == 0
    return payload


def _pending_pairs(project: Path):
    return sorted(
        (item["chapter"], item["scope"]) for item in _pending_payload(project)["pending"]
    )


def test_unexpected_exception_after_ledger_write_rolls_back_ledger(project):
    """修复 6：账本写入之后的未预期异常也必须回滚账本。"""
    (project / "正文" / "第001章.txt").write_text(
        "第一章\n他握紧了钥匙。\n"
        '<!-- audit:stash name="海门钥匙" origin="第1章" status="pending" -->\n',
        encoding="utf-8",
    )
    before = _snapshot(project)
    with patch.object(
        story_audit, "render_audit_report", side_effect=RuntimeError("注入渲染异常")
    ):
        assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True) == (
            3,
            Path(""),
        )
    assert _snapshot(project) == before
    assert not (project / "资源账本.json").exists()
    assert not (project / "设定" / "资源账本.json").exists()
    assert not get_audit_state_path(project / "reports").exists()


def test_f05_legacy_state_still_preserves_old_report(project):
    """P2：旧状态没有 chapter_versions 时，首次版本变化也必须归档旧报告。"""
    _audit(project, [1, 2])
    archive_path = project / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md"
    original_report = archive_path.read_bytes()
    assert "补丁引入的新问题" not in original_report.decode("utf-8")

    # 模拟升级前状态文件：移除 F05 新增字段后原样写回。
    state_path = get_audit_state_path(project / "reports")
    legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
    for key in ("chapter_versions", "pending_rechecks", "version_events"):
        legacy_state.pop(key, None)
    state_path.write_text(json.dumps(legacy_state, ensure_ascii=False, indent=2), encoding="utf-8")

    (project / "正文" / "第001章.txt").write_text(
        '第一章\n他握紧了钥匙。\n雪停了。<!-- audit:p1 message="补丁引入的新问题" -->\n',
        encoding="utf-8",
    )
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0

    history_dir = project / "reports" / "单章审查" / "历史" / "001-100章"
    history_files = sorted(history_dir.glob("第001章_审查报告_v*.md"))
    assert len(history_files) == 1
    assert history_files[0].read_bytes() == original_report
    assert "unknown" in history_files[0].name
    assert "补丁引入的新问题" in archive_path.read_text(encoding="utf-8")

    # 版本表已重建：同版本重复审查不再产生新的历史副本。
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    assert len(sorted(history_dir.glob("第001章_审查报告_v*.md"))) == 1


@pytest.mark.parametrize("existing_ledger", [False, True], ids=["no-ledger", "existing-ledger"])
def test_f05_state_save_failure_rolls_back_ledger_writes(project, existing_ledger):
    """P3-1：状态保存失败时账本写入必须一并回滚，失败路径全仓库字节不变。"""
    if existing_ledger:
        save_ledger_state(
            LedgerState(),
            project / "设定" / "资源账本.json",
            project / "设定" / "资源账本.md",
        )
    (project / "正文" / "第001章.txt").write_text(
        "第一章\n他握紧了钥匙。\n风停了。\n"
        '<!-- audit:stash name="海门钥匙" origin="第1章" status="pending" -->\n',
        encoding="utf-8",
    )
    before = _snapshot(project)

    with patch.object(story_audit, "save_audit_state", side_effect=SafeIOWriteError("模拟状态保存失败")):
        assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True) == (3, Path(""))

    assert _snapshot(project) == before
    assert not (project / "reports" / "LATEST_REPORT.md").exists()
    assert not get_audit_state_path(project / "reports").exists()
    if existing_ledger:
        assert (project / "设定" / "资源账本.json").is_file()
        assert (project / "设定" / "资源账本.md").is_file()
    else:
        # 本轮新建的账本文件必须被删除，项目树回到初始状态。
        assert not (project / "设定" / "资源账本.json").exists()
        assert not (project / "资源账本.json").exists()
        assert not (project / "资源账本.md").exists()


def test_f05_patch_registers_chapter_and_seam_rechecks_only(project):
    """补丁写回后仅第 N 章报告与第 N+1 章接缝进入待办，无关章节不进入。"""
    _audit(project, [1, 2, 3])
    before_version = _chapter_text_version(project, 1)
    assert _patch(project) == 0
    after_version = _chapter_text_version(project, 1)
    assert before_version != after_version

    state = _state(project)
    assert _pending_pairs(project) == [(1.0, "chapter"), (2.0, "seam")]
    # 已记录的已审版本仍是补丁前的版本：完成章号不再代表当前版本已审。
    assert state["chapter_versions"]["1"] == before_version

    chapter_item = next(item for item in state["pending_rechecks"] if item["scope"] == "chapter")
    assert chapter_item["chapter"] == 1
    assert chapter_item["status"] == "pending"
    assert chapter_item["previous_text_version"] == before_version
    assert chapter_item["text_version"] == after_version
    assert chapter_item["patch_id"].startswith("patch-")
    assert chapter_item["trigger_chapter"] == 1
    assert chapter_item["created_at"] and chapter_item["updated_at"]

    seam_item = next(item for item in state["pending_rechecks"] if item["scope"] == "seam")
    assert seam_item["chapter"] == 2
    assert seam_item["trigger_chapter"] == 1
    assert seam_item["text_version"] == _chapter_text_version(project, 2)
    assert seam_item["patch_id"] == chapter_item["patch_id"]

    events = [event for event in state["version_events"] if event["type"] == "patch_applied"]
    assert len(events) == 1
    event = events[0]
    assert event["chapter"] == 1
    assert event["patch_id"] == chapter_item["patch_id"]
    assert event["before_text_version"] == before_version
    assert event["after_text_version"] == after_version
    assert event["affected_chapters"] == [1.0, 2.0]
    assert event["target_line"] == 3
    assert event["old_text"] == OLD_TEXT and event["new_text"] == NEW_TEXT
    assert (project / event["backup"]).is_file()

    payload = _pending_payload(project)
    assert payload["counts"]["pending"] == 2
    assert sorted(payload["affected_chapters"]) == [1.0, 2.0]
    assert all(item["needs_reaudit"] is True for item in payload["pending"])
    chapter_entry = next(item for item in payload["pending"] if item["scope"] == "chapter")
    assert chapter_entry["current_text_version"] == after_version
    assert chapter_entry["current_version_audited"] is False
    assert chapter_entry["needs_reaudit"] is True


def test_f05_scope_audit_clears_rechecks_for_covered_chapters(project):
    """批量连审同样按当前版本清除覆盖章节的待办复审项。"""
    _audit(project, [1, 2, 3])
    assert _patch(project) == 0
    assert _pending_pairs(project) == [(1.0, "chapter"), (2.0, "seam")]

    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
    assert _pending_payload(project)["pending"] == []
    state = _state(project)
    assert sorted(item["status"] for item in state["pending_rechecks"]) == ["resolved", "resolved"]
    assert state["chapter_versions"]["1"] == _chapter_text_version(project, 1)
    assert state["chapter_versions"]["2"] == _chapter_text_version(project, 2)


def test_f05_reaudit_clears_rechecks_and_keeps_history(project):
    """按当前版本完成审查后清除对应待办，并保留可追溯的解除记录。"""
    _audit(project, [1, 2, 3])
    assert _patch(project) == 0

    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    state = _state(project)
    chapter_item = next(item for item in state["pending_rechecks"] if item["chapter"] == 1)
    assert chapter_item["status"] == "resolved"
    assert chapter_item["resolution"] == "reaudit"
    assert chapter_item["resolution_text_version"] == _chapter_text_version(project, 1)
    assert chapter_item["resolved_at"]
    assert _pending_pairs(project) == [(2.0, "seam")]

    assert story_audit.audit_chapter(project, chapter_index=2, mode="solo", silent=True)[0] == 0
    state = _state(project)
    assert _pending_payload(project)["pending"] == []
    assert sorted(item["status"] for item in state["pending_rechecks"]) == ["resolved", "resolved"]

    payload = _pending_payload(project, include_resolved=True, include_events=True)
    assert payload["counts"]["pending"] == 0
    assert payload["counts"]["resolved"] == 2
    resolutions = [event for event in payload["events"] if event["type"] == "recheck_resolved"]
    assert sorted(event["chapter"] for event in resolutions) == [1.0, 2.0]
    assert all(event["resolution"] == "reaudit" for event in resolutions)


def test_f05_reaudit_failure_keeps_pending_and_artifacts_unchanged(project):
    """审查失败（读取失败或状态保存失败）时待办保留，产物与状态字节不变。"""
    _audit(project, [1, 2, 3])
    assert _patch(project) == 0
    state_path = get_audit_state_path(project / "reports")

    # 1) 目标章节读取失败：不得清除待办，也不得写入任何产物。
    (project / "正文" / "第001章.txt").write_bytes(b"\x00")
    before = _snapshot(project)
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True) == (3, Path(""))
    assert _snapshot(project) == before
    assert _pending_pairs(project) == [(1.0, "chapter"), (2.0, "seam")]

    # 2) 状态保存失败：待办与产物保持字节不变。
    (project / "正文" / "第001章.txt").write_text(
        f"第一章\n他握紧了钥匙。\n{NEW_TEXT}\n", encoding="utf-8"
    )
    before = _snapshot(project)
    with patch.object(story_audit, "save_audit_state", side_effect=SafeIOWriteError("模拟状态保存失败")):
        assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True) == (3, Path(""))
    assert _snapshot(project) == before
    assert state_path.read_bytes() == before["reports/.audit_state.json"]
    assert _pending_pairs(project) == [(1.0, "chapter"), (2.0, "seam")]


def test_f05_old_report_is_archived_when_version_changes(project):
    """正文版本变化后旧归档报告进入历史目录，不被静默覆盖。"""
    _audit(project, [1, 2])
    archive_path = project / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md"
    original_report = archive_path.read_text(encoding="utf-8")
    assert "补丁引入的新问题" not in original_report
    old_version = _chapter_text_version(project, 1)

    assert _patch(project, new_text='雪停了。<!-- audit:p1 message="补丁引入的新问题" -->') == 0
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0

    history_dir = project / "reports" / "单章审查" / "历史" / "001-100章"
    history_files = sorted(history_dir.glob("第001章_审查报告_v*.md"))
    assert len(history_files) == 1
    assert history_files[0].read_text(encoding="utf-8") == original_report
    assert old_version[:12] in history_files[0].name
    assert "补丁引入的新问题" in archive_path.read_text(encoding="utf-8")

    # 同版本重复审查不再产生新的历史副本。
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    assert len(sorted(history_dir.glob("第001章_审查报告_v*.md"))) == 1


def test_f05_issue_closure_and_manual_recheck_resolution_are_separate(project):
    """补丁事件与问题关闭事件分别留痕；手工解除待办可查询且幂等。"""
    _audit(project, [1, 2, 3])
    assert _patch(project) == 0

    code, state_path = story_audit.resolve_recheck(
        project,
        chapter=1,
        scope="chapter",
        reason="作者已人工复核第1章补丁",
        silent=True,
    )
    assert code == 0 and state_path == get_audit_state_path(project / "reports")
    state = _state(project)
    chapter_item = next(item for item in state["pending_rechecks"] if item["chapter"] == 1)
    assert chapter_item["status"] == "resolved"
    assert chapter_item["resolution"] == "manual_close"
    assert chapter_item["resolution_reason"] == "作者已人工复核第1章补丁"
    assert _pending_pairs(project) == [(2.0, "seam")]

    # 重复手工解除幂等：不新增事件、不改写状态文件。
    before = _snapshot(project)
    assert story_audit.resolve_recheck(
        project, chapter=1, scope="chapter", reason="作者已人工复核第1章补丁", silent=True
    )[0] == 0
    assert _snapshot(project) == before

    # 单独登记“问题经复核关闭”事件，与补丁事件分离。
    code, _ = story_audit.record_issue_closure(
        project,
        chapter=3,
        issue="灯塔描述与设定冲突",
        reason="作者已按设定修正",
        source="author",
        silent=True,
    )
    assert code == 0
    state = _state(project)
    types = sorted({event["type"] for event in state["version_events"]})
    assert types == ["issue_closed", "patch_applied", "recheck_resolved"]
    closure = next(event for event in state["version_events"] if event["type"] == "issue_closed")
    assert closure["chapter"] == 3
    assert closure["issue"] == "灯塔描述与设定冲突"
    assert closure["reason"] == "作者已按设定修正"
    assert closure["source"] == "author"
    assert closure["at"]

    before = _snapshot(project)
    assert story_audit.record_issue_closure(
        project,
        chapter=3,
        issue="灯塔描述与设定冲突",
        reason="作者已按设定修正",
        source="author",
        silent=True,
    )[0] == 0
    assert _snapshot(project) == before


def test_f05_closure_moves_matching_defect_into_history(project):
    """关闭事件命中开放缺陷时，该缺陷进入历史并保留关闭依据。"""
    _audit(project, [1])
    save_audit_state(
        AuditState(
            completed_chapters=[1.0],
            open_defects=[
                {
                    "chapter": 1.0,
                    "severity": "P1",
                    "category": "causal",
                    "issue": "钥匙来源未交代",
                    "fix": "【事实对齐】补齐来源交代。",
                    "source": "expert",
                    "status": "open",
                }
            ],
        ),
        project / "reports",
    )
    assert story_audit.record_issue_closure(
        project,
        chapter=1,
        issue="钥匙来源未交代",
        reason="复核确认已补齐来源",
        source="expert",
        silent=True,
    )[0] == 0
    state = _state(project)
    assert state["open_defects"] == []
    resolved = [item for item in state["resolved_items"] if item.get("issue") == "钥匙来源未交代"]
    assert len(resolved) == 1
    assert resolved[0]["status"] == "resolved"
    assert resolved[0]["resolution_reason"] == "复核确认已补齐来源"
    assert resolved[0]["resolution_source"] == "expert"
    assert resolved[0]["resolved_at"]


def test_f05_legacy_state_loads_and_version_predicate_is_strict(project):
    """旧状态文件可加载；完成章号不再单独代表当前版本已审。"""
    reports = project / "reports"
    reports.mkdir()
    legacy = {
        "schema_version": 1,
        "last_scope": "1",
        "completed_chapters": [1],
        "open_defects": [],
        "foreshadowing_commitments": [],
        "resolved_items": [],
    }
    get_audit_state_path(reports).write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    state = load_audit_state(reports)
    assert state.completed_chapters == [1.0]
    assert state.chapter_versions == {}
    assert state.pending_rechecks == []
    assert state.version_events == []
    assert is_chapter_version_audited(state, 1, _chapter_text_version(project, 1)) is False
    assert _pending_payload(project)["pending"] == []

    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    refreshed = load_audit_state(reports)
    assert refreshed.chapter_versions["1"] == _chapter_text_version(project, 1)
    assert is_chapter_version_audited(refreshed, 1, _chapter_text_version(project, 1)) is True
    assert is_chapter_version_audited(refreshed, 1, "0" * 64) is False


def test_f05_recheck_registration_is_idempotent_across_patches(project):
    """同一章节多次回写只更新既有待办，不重复累积待办条目。"""
    _audit(project, [1, 2])
    assert _patch(project) == 0
    first_state = _state(project)
    first_item = next(item for item in first_state["pending_rechecks"] if item["scope"] == "chapter")

    (project / "正文" / "第001章.txt").write_text(
        f"第一章\n他握紧了钥匙。\n{NEW_TEXT}\n雪落无声。\n", encoding="utf-8"
    )
    assert story_audit.apply_fix(
        project,
        chapter_index=1,
        target_line=4,
        old_text="雪落无声。",
        new_text="雪停了。",
        silent=True,
    ) == 0

    state = _state(project)
    assert len(state["pending_rechecks"]) == 2
    updated = next(item for item in state["pending_rechecks"] if item["scope"] == "chapter")
    assert updated["id"] == first_item["id"]
    assert updated["patch_id"] != first_item["patch_id"]
    assert updated["text_version"] == _chapter_text_version(project, 1)
    assert [event["type"] for event in state["version_events"]].count("patch_applied") == 2


def test_f05_apply_fix_rolls_back_when_state_cannot_be_saved(project):
    """补丁已写回但协调状态保存失败时，正文与状态必须回到原样。"""
    _audit(project, [1, 2])
    chapter = project / "正文" / "第001章.txt"
    original_bytes = chapter.read_bytes()
    before = _snapshot(project)

    with patch.object(
        story_audit, "save_audit_state", side_effect=SafeIOWriteError("模拟状态保存失败")
    ):
        assert _patch(project) == 3

    assert chapter.read_bytes() == original_bytes
    assert _snapshot(project) == before
    assert not list((project / "reports" / ".bak").glob("*.bak"))


def test_f05_invalid_coordination_inputs_have_no_side_effects(project):
    """新增入口的非法输入返回错误码且不写坏状态。"""
    _audit(project, [1, 2])
    assert _patch(project) == 0
    before = _snapshot(project)

    assert story_audit.resolve_recheck(project, chapter=99, reason="不存在", silent=True) == (3, Path(""))
    assert story_audit.resolve_recheck(project, chapter=1, reason="   ", silent=True) == (3, Path(""))
    assert story_audit.resolve_recheck(
        project, chapter=1, scope="unknown", reason="x", silent=True
    ) == (3, Path(""))
    assert story_audit.record_issue_closure(project, chapter=1, issue="", reason="x", silent=True) == (3, Path(""))
    assert story_audit.record_issue_closure(project, chapter=1, issue="问题", reason="", silent=True) == (3, Path(""))
    assert story_audit.record_issue_closure(
        project, chapter=1, issue="问题", reason="x", source="model", silent=True
    ) == (3, Path(""))
    assert story_audit.get_pending_rechecks(project / "missing", silent=True) == (3, {})
    assert _snapshot(project) == before
