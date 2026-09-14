# -*- coding: utf-8 -*-
"""F07：逐章流式审查入口与运行清单。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.audit_state import get_audit_state_path
from scripts.safe_io import SafeIOWriteError


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f07_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        for index in range(1, 31):
            (root / "正文" / f"第{index:03d}章.txt").write_text(
                f"第{index}章\n陆离在第{index}段旅程里继续前行，风雪未停。\n", encoding="utf-8"
            )
        yield root


def _collect(project: Path, scope_str: str = "1-30", **options):
    return list(story_audit.iter_audit_scope(project, scope_str=scope_str, mode="solo", silent=True, **options))


def _split(items):
    chapters = [item for item in items if item["kind"] == "chapter"]
    summaries = [item for item in items if item["kind"] == "run_summary"]
    assert len(summaries) == 1
    return chapters, summaries[0]


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def test_f07_stream_returns_distinct_results_and_manifest(project):
    """30 章样本逐章返回可区分结果，并写出运行清单。"""
    chapters, summary = _split(_collect(project))
    assert len(chapters) == 30
    assert [item["sequence"] for item in chapters] == list(range(1, 31))
    assert all(item["total"] == 30 for item in chapters)
    assert len({item["text_version"] for item in chapters}) == 30
    assert all(item["status"] == "completed" for item in chapters)

    first = chapters[0]
    assert first["chapter"] == 1.0
    assert first["bundle"]["meta"]["target_chapter"] == 1.0
    assert first["shared_bundle_cache_path"].endswith("pre_audit_bundle.json")
    assert first["report_path"].endswith("第001章_审查报告.md")
    assert isinstance(first["findings"], list)
    # 共享缓存路径只有一份；逐章内容以返回值 bundle 为准
    cache_paths = {item["shared_bundle_cache_path"] for item in chapters}
    assert len(cache_paths) == 1
    assert len({item["bundle"]["meta"]["target_chapter"] for item in chapters}) == 30

    assert summary["run_status"] == "completed"
    assert summary["exit_code"] == 0
    assert summary["completed"] == 30 and summary["failed"] == 0 and summary["not_executed"] == 0
    assert summary["reports_published"] is True

    manifest_path = Path(summary["manifest_path"])
    assert manifest_path.is_file()
    assert manifest_path.parent.name == "运行清单"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["scope"] == "1-30"
    assert manifest["platform"] == "generic"
    assert manifest["run_status"] == "completed"
    assert manifest["counts"] == {"total": 30, "completed": 30, "failed": 0, "not_executed": 0}
    assert [entry["status"] for entry in manifest["chapters"]] == ["completed"] * 30
    assert all(entry["report_published"] is True for entry in manifest["chapters"])
    assert len({entry["text_version"] for entry in manifest["chapters"]}) == 30
    # 发布成功后报告与状态都已落盘
    assert (project / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md").is_file()
    assert get_audit_state_path(project / "reports").is_file()
    # A3：成功路径同时产出与 audit_scope 一致的批量汇总产物
    assert summary["batch_summary_path"].endswith("BATCH_SUMMARY_SCOPE_1-30.md")
    assert (project / "reports" / "BATCH_SUMMARY_SCOPE_1-30.md").is_file()
    latest = project / "reports" / "LATEST_REPORT.md"
    assert latest.is_file() and "批量连审大盘汇总报告" in latest.read_text(encoding="utf-8")
    history = sorted((project / "reports" / "批量审查").glob("*_批量审查_第001-030章.md"))
    assert len(history) >= 1
    assert history[0].read_text(encoding="utf-8").startswith("=== story-audit 深度审查报告 ===")


def test_f07_stream_marks_failed_and_not_executed(project):
    """中途失败时清单区分已完成/失败/未执行，且不发布报告。"""
    (project / "正文" / "第002章.txt").write_bytes(b"\x00")
    chapters, summary = _split(_collect(project, scope_str="1-3"))

    assert [item["status"] for item in chapters] == ["completed", "failed"]
    assert chapters[0]["bundle"]["meta"]["target_chapter"] == 1.0
    assert chapters[1]["error"]
    assert summary["run_status"] == "failed"
    assert summary["exit_code"] == 3
    assert (summary["completed"], summary["failed"], summary["not_executed"]) == (1, 1, 1)
    assert summary["reports_published"] is False

    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert [entry["status"] for entry in manifest["chapters"]] == [
        "completed",
        "failed",
        "not_executed",
    ]
    assert manifest["run_status"] == "failed"
    assert manifest["counts"] == {"total": 3, "completed": 1, "failed": 1, "not_executed": 1}
    assert all(entry["report_published"] is False for entry in manifest["chapters"])
    # 失败运行不得发布任何章节报告
    archive_dir = project / "reports" / "单章审查"
    assert not list(archive_dir.rglob("*_审查报告.md"))
    # A3：失败运行也不产生批量汇总产物
    assert not (project / "reports" / "LATEST_REPORT.md").exists()
    assert not list((project / "reports").glob("BATCH_SUMMARY_SCOPE_*.md"))
    assert not list((project / "reports" / "批量审查").glob("*_批量审查_第001-003章.md"))


def test_f07_stream_marks_failed_when_state_save_fails(project):
    """批次发布失败时清单标注 failed 且不发布报告/预审包。"""
    before = _snapshot(project)
    with patch.object(story_audit, "save_audit_state", side_effect=SafeIOWriteError("模拟状态保存失败")):
        chapters, summary = _split(_collect(project, scope_str="1-2"))
    assert [item["status"] for item in chapters] == ["completed", "completed"]
    assert summary["run_status"] == "failed"
    assert summary["exit_code"] == 3
    assert summary["reports_published"] is False
    assert summary["error"]

    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["run_status"] == "failed"
    assert all(entry["report_published"] is False for entry in manifest["chapters"])
    assert not list((project / "reports" / "单章审查").rglob("*_审查报告.md"))
    assert not get_audit_state_path(project / "reports").exists()
    # 除运行清单（进度诊断产物）外，项目树保持原样
    after = _snapshot(project)
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    assert all("运行清单" in key for key in changed), changed


def test_f07_batch_summary_failure_marks_run_failed(project):
    """修复 4：批量汇总渲染失败不得静默报告 completed。"""
    with patch.object(
        story_audit, "render_scope_batch_summary", side_effect=RuntimeError("注入汇总渲染失败")
    ):
        chapters, summary = _split(_collect(project, scope_str="1-2"))
    assert [item["status"] for item in chapters] == ["completed", "completed"]
    assert summary["run_status"] == "failed"
    assert summary["exit_code"] == 3
    assert summary["reports_published"] is False
    assert "批量汇总报告生成失败" in summary["error"]
    assert summary["batch_summary_path"] == "" and summary["latest_report_path"] == ""

    manifest = json.loads(Path(summary["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["run_status"] == "failed"
    assert all(entry["report_published"] is False for entry in manifest["chapters"])
    assert not (project / "reports" / "LATEST_REPORT.md").exists()
    assert not list((project / "reports").glob("BATCH_SUMMARY_SCOPE_*.md"))
    assert not list((project / "reports" / "批量审查").glob("*_批量审查_第001-002章.md"))
    assert not list((project / "reports" / "单章审查").rglob("*_审查报告.md"))


def test_f07_stream_callback_and_invalid_scope(project):
    seen = []
    items = _collect(project, scope_str="1-2", on_chapter=seen.append)
    assert [item["chapter"] for item in seen] == [1.0, 2.0]
    assert len(seen) == len([item for item in items if item["kind"] == "chapter"])

    payload = list(story_audit.iter_audit_scope(project, scope_str="bad", silent=True))
    assert len(payload) == 1
    assert payload[0]["kind"] == "run_summary"
    assert payload[0]["run_status"] == "failed"
    assert payload[0]["phase"] == "validation_failed"
    assert payload[0]["exit_code"] == 3
    assert payload[0]["error"]
