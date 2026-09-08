# -*- coding: utf-8 -*-
"""R05：批量审查只发现一次章节，每次新调用重新发现。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.chapter_resolver import ChapterResolver


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_batch_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        for index in range(1, 4):
            _write_chapter(root, index)
        yield root


def _write_chapter(project, index):
    (project / "正文" / f"第{index:03d}章_旅程{index}.txt").write_text(
        f"第{index}章\n少年推开舱门。\n第{index}次出海，他握紧了手中的缆绳。", encoding="utf-8"
    )


def test_r05_one_discovery_covers_every_requested_chapter(project):
    with patch.object(ChapterResolver, "discover_chapters", wraps=ChapterResolver.discover_chapters) as discover:
        code, report = story_audit.audit_scope(project, "1-3", mode="solo", silent=True)
    assert code == 0
    assert discover.call_count == 1
    assert report.is_file()
    for index in range(1, 4):
        archived = story_audit.get_report_archive_path(project / "reports", index)
        text = archived.read_text(encoding="utf-8")
        assert f"旅程{index}" in text
    state = json.loads((project / "reports" / ".audit_state.json").read_text(encoding="utf-8"))
    assert state["completed_chapters"] == [1, 2, 3]


def test_r05_new_api_call_sees_new_chapters(project):
    with patch.object(ChapterResolver, "discover_chapters", wraps=ChapterResolver.discover_chapters) as discover:
        assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
        _write_chapter(project, 4)
        code, report = story_audit.audit_scope(project, "3-4", mode="solo", silent=True)
        assert code == 0
        assert "旅程4" in report.read_text(encoding="utf-8")
        assert discover.call_count == 2
        _write_chapter(project, 5)
        code, report = story_audit.audit_chapter(project, mode="solo", silent=True)
        assert code == 0
        assert "旅程5" in report.read_text(encoding="utf-8")
        assert discover.call_count == 3


def test_r05_batch_does_not_rediscover_midway(project):
    original_run = story_audit.run_audit
    added = False

    def add_later_chapter(*args, **kwargs):
        nonlocal added
        if not added:
            _write_chapter(project, 4)
            added = True
        return original_run(*args, **kwargs)

    with patch.object(ChapterResolver, "discover_chapters", wraps=ChapterResolver.discover_chapters) as discover, \
         patch.object(story_audit, "run_audit", side_effect=add_later_chapter):
        code, report = story_audit.audit_scope(project, "1-4", mode="solo", silent=True)
    assert code == 0
    assert discover.call_count == 1
    assert "旅程4" not in report.read_text(encoding="utf-8")
    assert not story_audit.get_report_archive_path(project / "reports", 4).exists()
