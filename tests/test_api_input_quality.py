# -*- coding: utf-8 -*-
"""R17/R18：真实临时项目中的章节消歧和公开 API 输入边界。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.ledger_engine import LedgerState, save_ledger_state
from scripts.safe_io import SafeIOWriteError


@pytest.fixture
def project(monkeypatch):
    with TemporaryDirectory(prefix="story_audit_input_") as directory:
        root = Path(directory) / "project"
        (root / "正文").mkdir(parents=True)
        (root / "正文" / "第001章.txt").write_text(
            "旧正文\n他走进庭院。\n他握紧了手中的缆绳。", encoding="utf-8"
        )
        # 空路径的旧实现会退回 cwd；RED 阶段也只允许它触及临时项目。
        with monkeypatch.context() as local_patch:
            local_patch.chdir(root)
            yield root


def _snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
        for p in root.rglob("*")
    }


def _invoke(project, operation, **overrides):
    options = {"silent": True}
    options.update({
        "audit": {"chapter_index": 1},
        "scope": {"scope_str": "1-1"},
        "init": {},
        "checkpoint": {"volume": 1},
        "sync": {},
        "fix": {"chapter_index": 1, "target_line": 1,
                "old_text": "旧正文", "new_text": "新正文"},
    }[operation])
    options.update(overrides)
    function = {
        "audit": story_audit.audit_chapter,
        "scope": story_audit.audit_scope,
        "init": story_audit.init_ledger,
        "checkpoint": story_audit.checkpoint_volume,
        "sync": story_audit.sync_ledger_from_md,
        "fix": story_audit.apply_fix,
    }[operation]
    return function(project, **options)


def _assert_rejected(result):
    if isinstance(result, tuple):
        assert result == (3, Path(""))
    else:
        assert result == 3


def _add_duplicate(project, index=1):
    path = project / "正文" / "乙卷" / f"第{index:03d}章.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text("旧正文\n这是另一卷中的同号章节。", encoding="utf-8")


@pytest.mark.parametrize("operation", ["audit", "fix"])
@pytest.mark.parametrize("chapter_index", [1, None, 1.0000001])
def test_r17_duplicate_target_is_rejected_before_writes(project, operation, chapter_index):
    _add_duplicate(project)
    before = _snapshot(project)
    _assert_rejected(_invoke(project, operation, chapter_index=chapter_index))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["scope", "init"])
def test_r17_batch_duplicate_is_rejected_before_first_chapter(project, operation):
    (project / "正文" / "第002章.txt").write_text("旧正文", encoding="utf-8")
    _add_duplicate(project, 2)
    before = _snapshot(project)
    _assert_rejected(_invoke(project, operation, scope_str="1-2"))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["audit", "scope", "fix"])
def test_r17_unrelated_duplicates_do_not_block_unique_target(project, operation):
    _add_duplicate(project)
    chapter = project / "正文" / "第002章.txt"
    chapter.write_text("旧正文\n他推开门。", encoding="utf-8")
    options = {"scope_str": "2-2"} if operation == "scope" else {"chapter_index": 2}
    result = _invoke(project, operation, **options)
    assert (result[0] if isinstance(result, tuple) else result) == 0
    if operation == "fix":
        assert chapter.read_text(encoding="utf-8").startswith("新正文")
        assert (project / "正文" / "第001章.txt").read_text(encoding="utf-8").startswith("旧正文")


@pytest.mark.parametrize("operation", ["audit", "scope"])
@pytest.mark.parametrize("option,value", [
    ("platform", "zihu"), ("platform", None), ("platform", 7),
    ("platform", ""), ("mode", "ful"), ("mode", None),
    ("mode", 7), ("mode", ""), ("genre", ["仙侠"]),
    ("strict", "false"), ("force", "false"), ("author_memory", "false"),
    ("allow_partial", "false"),
])
def test_r18_invalid_options_have_no_side_effects(project, operation, option, value):
    before = _snapshot(project)
    _assert_rejected(_invoke(project, operation, **{option: value}))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["audit", "fix"])
@pytest.mark.parametrize("chapter_index", [float("inf"), float("nan"), "1", True, [], -1,
                                          pytest.param(10**400, id="overflow")])
def test_r18_invalid_chapter_index_has_no_side_effects(project, operation, chapter_index):
    before = _snapshot(project)
    _assert_rejected(_invoke(project, operation, chapter_index=chapter_index))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["scope", "init"])
@pytest.mark.parametrize("scope_str", ["1-inf", "1-nan", "1-1e309", "", True, 12, []])
def test_r18_invalid_scope_has_no_side_effects(project, operation, scope_str):
    before = _snapshot(project)
    _assert_rejected(_invoke(project, operation, scope_str=scope_str))
    assert _snapshot(project) == before


def test_r18_missing_required_scope_is_rejected(project):
    before = _snapshot(project)
    _assert_rejected(_invoke(project, "scope", scope_str=None))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["audit", "scope", "init", "checkpoint", "sync", "fix"])
@pytest.mark.parametrize("bad_path", [None, 123, [], "", "\x00", "missing", "file"])
def test_r18_invalid_project_path_is_controlled(project, operation, bad_path):
    if bad_path == "missing":
        bad_path = project / "missing"
    elif bad_path == "file":
        bad_path = project / "正文" / "第001章.txt"
    before = _snapshot(project)
    _assert_rejected(_invoke(bad_path, operation))
    assert _snapshot(project) == before


@pytest.mark.parametrize("volume", [0, -1, True, 1.5, float("inf"), "1", []])
def test_r18_invalid_volume_does_not_create_checkpoint(project, volume):
    save_ledger_state(LedgerState(), project / "设定" / "资源账本.json", project / "设定" / "资源账本.md")
    before = _snapshot(project)
    _assert_rejected(_invoke(project, "checkpoint", volume=volume))
    assert _snapshot(project) == before


@pytest.mark.parametrize("options", [{"patch": []}, {"patch_file": []}])
def test_r18_invalid_patch_source_cannot_fall_back_to_other_fields(project, options):
    before = _snapshot(project)
    _assert_rejected(_invoke(project, "fix", **options))
    assert _snapshot(project) == before


@pytest.mark.parametrize("operation", ["audit", "scope"])
def test_r18_normalized_enums_keep_platform_gate_and_report_consistent(project, operation):
    (project / "正文" / "第001章.txt").write_text("他走进庭院。\n" * 30, encoding="utf-8")
    result = _invoke(project, operation, platform=" ZHIHU ", mode=" SOLO ", strict=True)
    assert result[0] == 1
    report = result[1].read_text(encoding="utf-8")
    assert "Platform Rubric: zhihu" in report
    assert "Requested Mode: solo" in report
    bundle = json.loads((project / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8"))
    assert bundle["platform_diagnostics"]["platform"] == "zhihu"


def test_r18_normalized_scope_accepts_outer_whitespace_and_reverse_bounds(project):
    (project / "正文" / "第002章.txt").write_text("他走到海边。", encoding="utf-8")
    code, report = _invoke(project, "scope", scope_str=" \t2 - 1\n")
    assert code == 0
    assert report.name == "BATCH_SUMMARY_SCOPE_2-1.md"
    assert report.is_file()


@pytest.mark.parametrize("operation", ["audit", "scope", "init"])
def test_r18_storage_failure_returns_error_and_no_success_report(project, operation):
    with patch.object(story_audit, "write_file_safe", side_effect=SafeIOWriteError("模拟报告写入失败")), \
         patch.object(story_audit, "save_ledger_state", side_effect=SafeIOWriteError("模拟账本写入失败")):
        _assert_rejected(_invoke(project, operation))
    assert not (project / "reports" / "LATEST_REPORT.md").exists()
    assert not (project / "reports" / ".audit_state.json").exists()


@pytest.mark.parametrize("scope_str", ["bad", "5-6"])
def test_r18_init_error_respects_silent(project, scope_str, capsys):
    _assert_rejected(_invoke(project, "init", scope_str=scope_str))
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
