# -*- coding: utf-8 -*-
"""R13：显式伏笔跨调用继承，保留作者记录，持久化失败不假报成功。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.audit_state import AuditState, save_audit_state
from scripts.ledger_engine import LedgerState, save_ledger_state
from scripts.safe_io import SafeIOWriteError


TAG = '<!-- audit:stash name="深海旧钥匙" origin="第1章" status="pending" -->'


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_foreshadowing_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text("少年推开舱门。\n" + TAG, encoding="utf-8")
        (root / "正文" / "第002章.txt").write_text("海风迎面吹来。\n伙伴等在船头。", encoding="utf-8")
        yield root


def _state(project):
    return json.loads((project / "reports" / ".audit_state.json").read_text(encoding="utf-8"))


def _snapshot(project):
    return {
        p.relative_to(project).as_posix(): p.read_bytes() if p.is_file() else None
        for p in project.rglob("*")
    }


def _audit_first(project, operation):
    if operation == "chapter":
        return story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)
    return story_audit.audit_scope(project, "1-1", mode="solo", silent=True)


@pytest.mark.parametrize("operation", ["chapter", "scope"])
@pytest.mark.parametrize("initialized", [False, True])
def test_r13_explicit_stash_is_inherited_after_first_audit(project, operation, initialized):
    if initialized:
        assert story_audit.init_ledger(project, silent=True)[0] == 0
    assert _audit_first(project, operation)[0] == 0
    commitments = _state(project)["foreshadowing_commitments"]
    assert len(commitments) == 1
    assert commitments[0]["tag"] == "深海旧钥匙"
    assert commitments[0]["origin_chapter"] == 1
    assert commitments[0]["status"] == "pending"

    code, report = story_audit.audit_scope(project, "2-2", mode="solo", silent=True)
    assert code == 0
    assert "深海旧钥匙" in report.read_text(encoding="utf-8")
    bundle = json.loads((project / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8"))
    assert "深海旧钥匙" in json.dumps(bundle["inherited_items"], ensure_ascii=False)
    assert _state(project)["foreshadowing_commitments"] == commitments

    # 重审与没有再次出现的下一章都不能自动认定已回收。
    assert _audit_first(project, operation)[0] == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == commitments
    assert state["resolved_items"] == []


def test_r13_identical_tags_in_multiple_chapters_are_not_duplicated(project):
    (project / "正文" / "第002章.txt").write_text("少年握紧缆绳。\n" + TAG, encoding="utf-8")
    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
    commitments = _state(project)["foreshadowing_commitments"]
    assert len(commitments) == 1
    assert commitments[0]["tag"] == "深海旧钥匙"
    assert commitments[0]["origin_chapter"] == 1


def test_r13_existing_author_commitment_and_resolved_items_are_preserved(project):
    manual = {"tag": "深海旧钥匙", "origin_chapter": 1, "status": "pending", "note": "作者指定第30章核验"}
    resolved = {"tag": "旧海图", "origin_chapter": 0, "status": "resolved", "note": "作者已核验"}
    save_audit_state(AuditState(foreshadowing_commitments=[manual], resolved_items=[resolved]), project / "reports")
    assert _audit_first(project, "chapter")[0] == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == [manual]
    assert state["resolved_items"] == [resolved]


def test_r13_markdown_without_json_is_preserved_while_stash_is_inherited(project):
    (project / "设定").mkdir()
    md = project / "设定" / "资源账本.md"
    md.write_text("# 作者旧账本\n\n保留尚未同步的资产记录。", encoding="utf-8")
    original = md.read_bytes()
    assert _audit_first(project, "chapter")[0] == 0
    assert md.read_bytes() == original
    assert not md.with_suffix(".json").exists()
    assert _state(project)["foreshadowing_commitments"][0]["tag"] == "深海旧钥匙"
    code, report = story_audit.audit_scope(project, "2-2", mode="solo", silent=True)
    assert code == 0
    assert "深海旧钥匙" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_r13_stash_save_failure_returns_error_before_success_report(project, existing_ledger):
    if existing_ledger:
        save_ledger_state(LedgerState(), project / "设定" / "资源账本.json", project / "设定" / "资源账本.md")
    before = _snapshot(project)
    with patch.object(story_audit, "save_ledger_state", side_effect=SafeIOWriteError("模拟伏笔保存失败")) as save:
        result = _audit_first(project, "chapter")
    assert save.call_count == 1
    assert result == (3, Path(""))
    assert _snapshot(project) == before
