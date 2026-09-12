# -*- coding: utf-8 -*-
"""F01/F02 功能修复回归：跨批状态合并与批量历史归档。"""

import hashlib
import json
from datetime import datetime as real_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts.audit_state import AuditState, get_audit_state_path, save_audit_state
from scripts.story_audit import audit_chapter, audit_scope


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_functional_fixes_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        yield root


class _FrozenDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls(2026, 9, 8, 12, 0, 0)
        return cls(2026, 9, 8, 12, 0, 0, tzinfo=tz)


def _write_chapter(project, name, text):
    path = project / "正文" / name
    path.write_text(text, encoding="utf-8")
    return path


def _save_state(project, defects, completed=(1,)):
    return save_audit_state(
        AuditState(
            completed_chapters=[float(item) for item in completed],
            open_defects=[dict(item) for item in defects],
        ),
        project / "reports",
    )


def _state(project):
    return json.loads(get_audit_state_path(project / "reports").read_text(encoding="utf-8"))


def _bundle(project):
    return json.loads(
        (project / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8")
    )


def _issues(items):
    return [item.get("issue") for item in items]


def _issue_set(items):
    return set(_issues(items))


def _legacy_defect(chapter, issue, source=None, **extra):
    item = {
        "chapter": chapter,
        "severity": "P1",
        "category": "causal",
        "location": "第{}章".format(chapter),
        "evidence": "{} 的旧证据".format(issue),
        "issue": issue,
        "fix": "保留原记录并等待复核。",
    }
    if source is not None:
        item["source"] = source
    item.update(extra)
    return item


def _text_version(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_f01_single_chapter_preserves_unowned_and_resolves_only_deterministic(project):
    chapter = _write_chapter(
        project,
        "第001章.txt",
        "第一章\n<!-- audit:p1 message=\"确定性规则问题\" -->\n风雪停了。\n",
    )
    _save_state(
        project,
        [
            _legacy_defect(1, "专家未复核问题", source="expert"),
            _legacy_defect(1, "人工记录问题", source="manual"),
            _legacy_defect(1, "未知来源问题", source="unknown"),
            _legacy_defect(1, "无来源旧记录"),
            _legacy_defect(
                1,
                "起点平台未覆盖问题",
                source="deterministic",
                checker="platform_rubric",
                platform="qidian",
            ),
        ],
    )

    code, report_path = audit_chapter(project, chapter_index=1, platform="generic", silent=True)
    assert code == 0
    state = _state(project)
    issues = _issue_set(state["open_defects"])
    expected = {
        "专家未复核问题",
        "人工记录问题",
        "未知来源问题",
        "无来源旧记录",
        "起点平台未覆盖问题",
        "确定性规则问题",
    }
    assert expected <= issues

    deterministic = [item for item in state["open_defects"] if item.get("issue") == "确定性规则问题"]
    assert len(deterministic) == 1
    assert deterministic[0]["source"] == "deterministic"
    assert deterministic[0]["checker"] == "explicit_violation"
    assert deterministic[0]["platform"] == "generic"
    assert deterministic[0]["id"]

    report_text = report_path.read_text(encoding="utf-8")
    assert "专家未复核问题" in report_text
    assert "起点平台未覆盖问题" in report_text
    bundle = _bundle(project)
    assert _issue_set(bundle["inherited_items"]["open_defects"]) == issues

    # 同一确定性问题的重复扫描必须保持幂等。
    code_again, _ = audit_chapter(project, chapter_index=1, platform="generic", silent=True)
    assert code_again == 0
    state_again = _state(project)
    deterministic_again = [
        item
        for item in state_again["open_defects"]
        if item.get("source") == "deterministic" and item.get("platform") == "generic"
    ]
    assert len(deterministic_again) == 1
    assert deterministic_again[0]["id"] == deterministic[0]["id"]

    plain_text = "第一章\n风雪停了。\n"
    chapter.write_text(plain_text, encoding="utf-8")
    code_resolved, report_resolved = audit_chapter(
        project, chapter_index=1, platform="generic", silent=True
    )
    assert code_resolved == 0
    state_resolved = _state(project)
    assert "确定性规则问题" not in _issue_set(state_resolved["open_defects"])
    resolved = [item for item in state_resolved["resolved_items"] if item.get("issue") == "确定性规则问题"]
    assert len(resolved) == 1
    assert resolved[0]["status"] == "resolved"
    assert resolved[0]["resolution_reason"]
    assert resolved[0]["resolution_evidence"]
    assert resolved[0]["text_version"] == _text_version(plain_text)

    remaining = _issue_set(state_resolved["open_defects"])
    for issue in (
        "专家未复核问题",
        "人工记录问题",
        "未知来源问题",
        "无来源旧记录",
        "起点平台未覆盖问题",
    ):
        assert issue in remaining

    report_resolved_text = report_resolved.read_text(encoding="utf-8")
    assert "专家未复核问题" in report_resolved_text
    assert "确定性规则问题" not in report_resolved_text
    bundle_resolved = _bundle(project)
    assert _issue_set(bundle_resolved["inherited_items"]["open_defects"]) == remaining

    code_resolved_again, _ = audit_chapter(
        project, chapter_index=1, platform="generic", silent=True
    )
    assert code_resolved_again == 0
    state_resolved_again = _state(project)
    assert "确定性规则问题" not in _issue_set(state_resolved_again["open_defects"])
    resolved_again = [
        item
        for item in state_resolved_again["resolved_items"]
        if item.get("issue") == "确定性规则问题"
    ]
    assert len(resolved_again) == 1


def test_f01_batch_preserves_unowned_and_rescan_is_idempotent(project):
    _write_chapter(
        project,
        "第001章.txt",
        "第一章\n<!-- audit:p1 message=\"批量规则问题一\" -->\n",
    )
    _write_chapter(
        project,
        "第002章.txt",
        "第二章\n<!-- audit:p1 message=\"批量规则问题二\" -->\n",
    )
    _save_state(
        project,
        [
            _legacy_defect(1, "批量专家问题", source="expert"),
            _legacy_defect(2, "批量人工问题", source="manual"),
            _legacy_defect(1, "批量未知问题", source="unknown"),
            _legacy_defect(2, "批量无来源问题"),
            _legacy_defect(
                1,
                "批量起点问题",
                source="deterministic",
                checker="platform_rubric",
                platform="qidian",
            ),
        ],
        completed=(1, 2),
    )

    code, report_path = audit_scope(project, "1-2", silent=True)
    assert code == 0
    state = _state(project)
    issues = _issue_set(state["open_defects"])
    unowned = {
        "批量专家问题",
        "批量人工问题",
        "批量未知问题",
        "批量无来源问题",
        "批量起点问题",
        "批量规则问题一",
        "批量规则问题二",
    }
    assert unowned <= issues
    deterministic = [
        item
        for item in state["open_defects"]
        if item.get("source") == "deterministic" and item.get("platform") == "generic"
    ]
    assert len(deterministic) == 2
    assert {item["checker"] for item in deterministic} == {"explicit_violation"}
    assert all(item.get("id") for item in deterministic)

    code_again, report_again = audit_scope(project, "1-2", silent=True)
    assert code_again == 0
    state_again = _state(project)
    deterministic_again = [
        item
        for item in state_again["open_defects"]
        if item.get("source") == "deterministic" and item.get("platform") == "generic"
    ]
    assert len(deterministic_again) == 2
    assert len({item["id"] for item in deterministic_again}) == 2
    for issue in (
        "批量专家问题",
        "批量人工问题",
        "批量未知问题",
        "批量无来源问题",
        "批量起点问题",
    ):
        assert _issues(state_again["open_defects"]).count(issue) == 1

    report_text = report_again.read_text(encoding="utf-8")
    for issue in unowned:
        assert issue in report_text
    bundle = _bundle(project)
    assert _issue_set(bundle["inherited_items"]["open_defects"]) == _issue_set(
        state_again["open_defects"]
    )

    # 批量重扫不再命中时，仅关闭自有确定性问题，专家/人工/旧记录保持开放。
    _write_chapter(project, "第001章.txt", "第一章\n")
    code_closed, report_closed = audit_scope(project, "1-2", silent=True)
    assert code_closed == 0
    state_closed = _state(project)
    open_closed = _issue_set(state_closed["open_defects"])
    assert "批量规则问题一" not in open_closed
    assert "批量规则问题二" in open_closed
    for issue in (
        "批量专家问题",
        "批量人工问题",
        "批量未知问题",
        "批量无来源问题",
        "批量起点问题",
    ):
        assert issue in open_closed
    resolved_closed = [
        item
        for item in state_closed["resolved_items"]
        if item.get("issue") == "批量规则问题一"
    ]
    assert len(resolved_closed) == 1
    assert resolved_closed[0]["resolution_reason"]
    assert resolved_closed[0]["text_version"] == _text_version("第一章\n")
    bundle_closed = _bundle(project)
    assert _issue_set(bundle_closed["inherited_items"]["open_defects"]) == open_closed
    report_closed_text = report_closed.read_text(encoding="utf-8")
    assert "批量规则问题一" not in report_closed_text


def test_f01_failed_batch_does_not_commit_partial_state(project):
    _write_chapter(
        project,
        "第001章.txt",
        "第一章\n<!-- audit:p1 message=\"失败前规则问题\" -->\n",
    )
    bad_chapter = _write_chapter(project, "第002章.txt", "第二章\n")
    bad_chapter.write_bytes(b"\x00")

    state_path = _save_state(
        project,
        [_legacy_defect(1, "失败前专家问题", source="expert")],
        completed=(1, 2),
    )
    before_state = state_path.read_bytes()
    reports = project / "reports"
    latest_path = reports / "LATEST_REPORT.md"
    latest_path.write_text("上一轮可信报告", encoding="utf-8")
    before_latest = latest_path.read_bytes()

    code, report_path = audit_scope(project, "1-2", silent=True)
    assert code == 3
    assert report_path == Path("")
    assert state_path.read_bytes() == before_state
    assert latest_path.read_bytes() == before_latest
    assert not (reports / "BATCH_SUMMARY_SCOPE_1-2.md").exists()


def test_f02_decimal_ranges_and_repeat_runs_keep_distinct_history(project):
    _write_chapter(project, "第001章（上）.txt", "上篇\n风雪停了。\n")
    _write_chapter(project, "第001章（中）.txt", "中篇\n风雪停了。\n")

    with patch("scripts.story_audit.datetime", _FrozenDateTime):
        code_first, _ = audit_scope(project, "1.1-1.1", silent=True)
        code_second, _ = audit_scope(project, "1.2-1.2", silent=True)
        assert code_first == code_second == 0

        history_dir = project / "reports" / "批量审查"
        first_files = list(history_dir.glob("*_批量审查_第001.1-001.1章.md"))
        second_files = list(history_dir.glob("*_批量审查_第001.2-001.2章.md"))
        assert len(first_files) == 1
        assert len(second_files) == 1

        first_bytes = first_files[0].read_bytes()
        second_bytes = second_files[0].read_bytes()
        assert first_bytes != second_bytes

        first_run_id = first_files[0].name.split("_")[1]
        second_run_id = second_files[0].name.split("_")[1]
        assert first_run_id != second_run_id
        first_text = first_bytes.decode("utf-8")
        second_text = second_bytes.decode("utf-8")
        assert "Run ID: {}".format(first_run_id) in first_text
        assert "Run ID: {}".format(second_run_id) in second_text
        assert "Scope: 1.1-1.1" in first_text
        assert "Scope: 1.2-1.2" in second_text
        assert "001.1-001.1" in first_text
        assert "001.2-001.2" in second_text

        latest = (project / "reports" / "LATEST_REPORT.md").read_bytes()
        convenience_second = (
            project / "reports" / "BATCH_SUMMARY_SCOPE_1.2-1.2.md"
        ).read_bytes()
        assert latest == second_bytes
        assert convenience_second == second_bytes

        code_repeat, _ = audit_scope(project, "1.1-1.1", silent=True)
        assert code_repeat == 0
        repeated = sorted(history_dir.glob("*_批量审查_第001.1-001.1章.md"))
        assert len(repeated) == 2
        assert any(item.read_bytes() == first_bytes for item in repeated)

        latest_after_repeat = (project / "reports" / "LATEST_REPORT.md").read_bytes()
        convenience_first = (
            project / "reports" / "BATCH_SUMMARY_SCOPE_1.1-1.1.md"
        ).read_bytes()
        assert latest_after_repeat == convenience_first


def test_f02_integer_history_glob_stays_compatible(project):
    _write_chapter(project, "第001章.txt", "第一章\n风雪停了。\n")
    _write_chapter(project, "第002章.txt", "第二章\n风雪停了。\n")

    with patch("scripts.story_audit.datetime", _FrozenDateTime):
        code, _ = audit_scope(project, "1-2", silent=True)
    assert code == 0

    matches = list(
        (project / "reports" / "批量审查").glob("*_批量审查_第001-002章.md")
    )
    assert len(matches) == 1
    assert "2026-09-08_" in matches[0].name
    assert matches[0].name.split("_")[1]
    assert matches[0].name != "2026-09-08_批量审查_第001-002章.md"