# -*- coding: utf-8 -*-
"""F03：伏笔显式确认、关闭与重新开启的受控裁决入口。"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts import story_audit
from scripts.audit_state import AuditState, apply_foreshadowing_adjudication, save_audit_state
from scripts.ledger_engine import LedgerState, save_ledger_state


NAME = "海门钥匙"
PENDING_TAG = '<!-- audit:stash name="海门钥匙" origin="第1章" status="pending" -->'


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f03_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text(
            "第一章\n他握紧了钥匙。\n" + PENDING_TAG + "\n", encoding="utf-8"
        )
        (root / "正文" / "第002章.txt").write_text("第二章\n海面雾气未散。\n", encoding="utf-8")
        yield root


def _state(project: Path) -> dict:
    return json.loads((project / "reports" / ".audit_state.json").read_text(encoding="utf-8"))


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _stash_statuses(project: Path):
    for candidate in (project / "设定" / "资源账本.json", project / "资源账本.json"):
        if candidate.is_file():
            stash = json.loads(candidate.read_text(encoding="utf-8"))["foreshadowing_stash"]
            return sorted(str(item.get("status") or "") for item in stash)
    raise AssertionError("未找到账本文件，无法核对伏笔标签注册")


def _publish_pending_commitment(project: Path) -> dict:
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    commitments = _state(project)["foreshadowing_commitments"]
    assert [item["tag"] for item in commitments] == [NAME]
    assert commitments[0]["status"] == "pending"
    return commitments[0]


def _adjudicate(project: Path, action: str, **options):
    payload = {"name": NAME, "action": action, "reason": f"{action} 测试裁决"}
    payload.update(options)
    return story_audit.adjudicate_foreshadowing(project, silent=True, **payload)


def test_f03_tag_status_change_alone_keeps_commitment_pending(project):
    """正文标签状态改写只是线索记录；存储层不得自行判定伏笔已回收。"""
    _publish_pending_commitment(project)
    assert story_audit.apply_fix(
        project,
        chapter_index=1,
        target_line=3,
        old_text='status="pending"',
        new_text='status="resolved"',
        silent=True,
    ) == 0
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0

    state = _state(project)
    assert [item["tag"] for item in state["foreshadowing_commitments"]] == [NAME]
    assert state["foreshadowing_commitments"][0]["status"] == "pending"
    assert state["resolved_items"] == []
    # 标签扫描确实登记了新的 resolved 状态，但承诺池未被自动改写。
    assert _stash_statuses(project) == ["pending", "resolved"]


def test_f03_confirm_moves_item_to_history_and_is_idempotent(project, capsys):
    _publish_pending_commitment(project)
    code, state_path = story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="confirm",
        reason="第3章已回收暗线",
        evidence="第3章 林间旧门开启",
        source="expert",
        silent=True,
    )
    assert code == 0
    assert state_path.resolve() == (project / "reports" / ".audit_state.json").resolve()

    state = _state(project)
    assert state["foreshadowing_commitments"] == []
    records = [item for item in state["resolved_items"] if item.get("tag") == NAME]
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "resolved"
    assert record["origin_chapter"] == 1
    assert record["resolution_source"] == "expert"
    assert record["resolution_reason"] == "第3章已回收暗线"
    assert record["resolution_evidence"] == "第3章 林间旧门开启"
    assert record["adjudicated_at"]
    assert [event["action"] for event in record["adjudication_history"]] == ["confirm"]
    assert record["adjudication_history"][0]["source"] == "expert"
    assert record["adjudication_history"][0]["reason"] == "第3章已回收暗线"

    # 重复确认必须幂等：不重复计数、不重复写历史，也不改写状态文件。
    before = _snapshot(project)
    code_again, _ = story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="confirm",
        reason="第3章已回收暗线",
        source="expert",
        silent=True,
    )
    assert code_again == 0
    assert _snapshot(project) == before
    assert [item for item in _state(project)["resolved_items"] if item.get("tag") == NAME] == records
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_f03_closed_item_stays_closed_after_rescan(project):
    _publish_pending_commitment(project)
    assert _adjudicate(project, "close", reason="作者废弃该线索")[0] == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == []
    record = state["resolved_items"][0]
    assert record["status"] == "closed"
    assert record["resolution_source"] == "author"
    assert record["adjudication_history"][0]["action"] == "close"

    # 重复扫描与重复审查都不得重新激活已关闭项。
    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == []
    assert len([item for item in state["resolved_items"] if item.get("tag") == NAME]) == 1

    # 新章节里再次出现同名旧标签也不得复活该承诺。
    (project / "正文" / "第003章.txt").write_text(
        "第三章\n门后仍是钥匙的轮廓。\n" + PENDING_TAG + "\n", encoding="utf-8"
    )
    assert story_audit.audit_chapter(project, chapter_index=3, mode="solo", silent=True)[0] == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == []
    assert len([item for item in state["resolved_items"] if item.get("tag") == NAME]) == 1


def test_f03_close_with_version_chapter_still_closes_origin_entry(project):
    """chapter 既登记裁决正文版本，也保证该章号留有独立裁决记录。"""
    _publish_pending_commitment(project)
    chapter_two = project / "正文" / "第002章.txt"
    expected_version = hashlib.sha256(chapter_two.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    code, _ = story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="close",
        reason="第2章确认该线索已作废",
        chapter=2,
        silent=True,
    )
    assert code == 0
    state = _state(project)
    assert state["foreshadowing_commitments"] == []
    records = [item for item in state["resolved_items"] if item.get("tag") == NAME]
    assert len(records) == 1
    assert records[0]["origin_chapter"] == 1
    assert records[0]["text_version"] == expected_version
    assert records[0]["adjudication_history"][-1]["chapter"] == 2

    # 第 2 章尚未登记过裁决记录，需要为该章号补登记，避免该章旧标签复活。
    assert story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="close",
        reason="第2章确认该线索已作废",
        chapter=2,
        silent=True,
    )[0] == 0
    records = [item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]
    assert sorted(item["origin_chapter"] for item in records) == [1.0, 2.0]
    chapter_record = next(item for item in records if item["origin_chapter"] == 2)
    assert chapter_record["status"] == "closed"
    assert chapter_record["resolution_reason"] == "第2章确认该线索已作废"
    assert chapter_record["adjudication_history"][-1]["chapter"] == 2
    assert chapter_record["adjudication_history"][-1]["at"]

    # 第三次重复裁决幂等：不新增记录，也不改写状态文件。
    before = _snapshot(project)
    assert story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="close",
        reason="第2章确认该线索已作废",
        chapter=2,
        silent=True,
    )[0] == 0
    assert _snapshot(project) == before
    assert len([item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]) == 2

    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    assert _state(project)["foreshadowing_commitments"] == []


def test_f03_same_name_different_origin_chapters_are_closed_separately(project):
    """P2-1：同名伏笔按指定来源章号分别登记裁决，后续复审不得复活旧标签。"""
    (project / "正文" / "第005章.txt").write_text(
        '第五章\n海门再现。\n<!-- audit:stash name="海门钥匙" origin="第5章" status="pending" -->\n',
        encoding="utf-8",
    )
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    assert [item["origin_chapter"] for item in _state(project)["foreshadowing_commitments"]] == [1.0]

    assert _adjudicate(project, "confirm", reason="第1章已回收", chapter=1)[0] == 0
    records = [item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]
    assert [item["origin_chapter"] for item in records] == [1.0]

    # 第 5 章尚未进入待办池：必须为该章号登记独立裁决记录，而不是静默返回 0。
    before = _snapshot(project)
    assert _adjudicate(project, "confirm", reason="第5章已回收", chapter=5)[0] == 0
    assert _snapshot(project) != before
    records = [item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]
    assert sorted(item["origin_chapter"] for item in records) == [1.0, 5.0]
    chapter_five = next(item for item in records if item["origin_chapter"] == 5)
    assert chapter_five["status"] == "resolved"
    assert chapter_five["resolution_reason"] == "第5章已回收"
    assert chapter_five["resolution_source"] == "author"
    assert chapter_five["adjudicated_at"]
    assert chapter_five["adjudication_history"][-1]["chapter"] == 5

    # 第 5 章复审：旧标签不得重新进入待办池。
    assert story_audit.audit_chapter(project, chapter_index=5, mode="solo", silent=True)[0] == 0
    assert _state(project)["foreshadowing_commitments"] == []
    assert len([item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]) == 2

    # 重复按第 5 章裁决幂等。
    before = _snapshot(project)
    assert _adjudicate(project, "confirm", reason="第5章已回收", chapter=5)[0] == 0
    assert _snapshot(project) == before
    assert len([item for item in _state(project)["resolved_items"] if item.get("tag") == NAME]) == 2


def test_f03_state_layer_reports_changed_for_new_chapter_decision():
    """P2-1：为新来源章号登记裁决时状态层必须返回 changed=True，而不是幂等空操作。"""
    state = AuditState(
        foreshadowing_commitments=[
            {"tag": NAME, "origin_chapter": 1, "status": "pending", "note": "第1章显式标记"}
        ]
    )
    first = apply_foreshadowing_adjudication(
        state, name=NAME, action="confirm", reason="第1章已回收"
    )
    assert first["changed"] is True and first["removed"] == 1

    second = apply_foreshadowing_adjudication(
        state, name=NAME, action="confirm", reason="第5章已回收", chapter=5
    )
    assert second["changed"] is True
    assert second["idempotent"] is False
    assert [item["origin_chapter"] for item in state.resolved_items] == [1.0, 5.0]

    third = apply_foreshadowing_adjudication(
        state, name=NAME, action="confirm", reason="第5章已回收", chapter=5
    )
    assert third["changed"] is False and third["idempotent"] is True
    assert len(state.resolved_items) == 2


def test_f03_chapterless_manual_entries_keep_distinct_origin_notes(project):
    """P3-2：无有效章号时来源自由文本参与身份，不能压掉不同来源的手工条目。"""
    settings = project / "设定"
    settings.mkdir()
    save_ledger_state(
        LedgerState(
            foreshadowing_stash=[
                {"name": "石碑谜语", "origin": "山门背面的石碑", "status": "pending"},
                {"name": "石碑谜语", "origin": "老渔夫口中的传说", "status": "pending"},
            ]
        ),
        settings / "资源账本.json",
        settings / "资源账本.md",
    )

    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    entries = [
        item for item in _state(project)["foreshadowing_commitments"] if item.get("tag") == "石碑谜语"
    ]
    notes = sorted(item.get("note") for item in entries)
    assert notes == ["山门背面的石碑", "老渔夫口中的传说"]
    assert [item.get("origin_chapter") for item in entries] == [None, None]

    # 重复审查不得把两条不同来源的线索压成一条。
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    assert len(
        [
            item
            for item in _state(project)["foreshadowing_commitments"]
            if item.get("tag") == "石碑谜语"
        ]
    ) == 2

    # 同 tag 同数字章号的重复扫描仍然幂等。
    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
    keyed = [item for item in _state(project)["foreshadowing_commitments"] if item.get("tag") == NAME]
    assert len(keyed) == 1
    assert keyed[0]["origin_chapter"] == 1


def test_f03_reopen_records_traceable_version_and_history(project):
    _publish_pending_commitment(project)
    assert _adjudicate(project, "close", reason="第一版判断为废弃")[0] == 0
    chapter_two = project / "正文" / "第002章.txt"
    expected_version = hashlib.sha256(chapter_two.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    code, _ = story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="reopen",
        reason="第2章出现新线索需要继续跟踪",
        evidence="第2章 雾气中的灯影",
        source="expert",
        chapter=2,
        silent=True,
    )
    assert code == 0
    state = _state(project)
    pending = state["foreshadowing_commitments"]
    assert [item["tag"] for item in pending] == [NAME]
    assert pending[0]["status"] == "pending"
    assert pending[0]["reopen_reason"] == "第2章出现新线索需要继续跟踪"
    assert pending[0]["reopen_source"] == "expert"
    assert pending[0]["reopen_chapter"] == 2
    assert pending[0]["reopen_text_version"] == expected_version
    assert pending[0]["reopened_at"]

    record = [item for item in state["resolved_items"] if item.get("tag") == NAME][0]
    assert record["status"] == "reopened"
    assert record["reopen_reason"] == "第2章出现新线索需要继续跟踪"
    assert record["reopen_source"] == "expert"
    assert record["reopen_text_version"] == expected_version
    events = record["adjudication_history"]
    assert [event["action"] for event in events] == ["close", "reopen"]
    reopen_event = events[-1]
    assert reopen_event["at"] and reopen_event["reason"] and reopen_event["source"] == "expert"
    assert reopen_event["chapter"] == 2
    assert reopen_event["text_version"] == expected_version

    # 重新开启后重复扫描不得产生重复承诺。
    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True)[0] == 0
    assert len(_state(project)["foreshadowing_commitments"]) == 1


def test_f03_multi_origin_requires_chapter_and_targets_matching_entry(project):
    """同名伏笔来自多个章号时必须给出 chapter，且只裁决匹配来源的条目。"""
    (project / "正文" / "第003章.txt").write_text(
        '第三章\n海门再现。\n<!-- audit:stash name="海门钥匙" origin="第3章" status="pending" -->\n',
        encoding="utf-8",
    )
    assert story_audit.audit_scope(project, "1-3", mode="solo", silent=True)[0] == 0
    histories = {
        item["origin_chapter"]: item for item in _state(project)["foreshadowing_commitments"]
    }
    assert sorted(histories) == [1.0, 3.0]

    before = _snapshot(project)
    assert _adjudicate(project, "close", reason="来源不明确") == (3, Path(""))
    assert _snapshot(project) == before
    assert len(_state(project)["foreshadowing_commitments"]) == 2

    assert _adjudicate(project, "close", reason="关闭第3章来源", chapter=3)[0] == 0
    state = _state(project)
    assert [item["origin_chapter"] for item in state["foreshadowing_commitments"]] == [1.0]
    records = [item for item in state["resolved_items"] if item.get("tag") == NAME]
    assert len(records) == 1
    assert records[0]["origin_chapter"] == 3

    # 同名的另一个来源条目仍然留在待办池，重复扫描不产生重复承诺。
    assert story_audit.audit_scope(project, "1-3", mode="solo", silent=True)[0] == 0
    assert [item["origin_chapter"] for item in _state(project)["foreshadowing_commitments"]] == [1.0]


def test_f03_reopen_requires_chapter_and_existing_record(project):
    _publish_pending_commitment(project)
    assert _adjudicate(project, "close", reason="先关闭再验证重开参数")[0] == 0
    before = _snapshot(project)

    assert story_audit.adjudicate_foreshadowing(
        project, name=NAME, action="reopen", reason="忘记提供章节", silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before

    assert story_audit.adjudicate_foreshadowing(
        project,
        name=NAME,
        action="reopen",
        reason="章号不存在",
        chapter=9,
        silent=True,
    ) == (3, Path(""))
    assert _snapshot(project) == before


def test_f03_reopen_without_history_is_rejected(project):
    before = _snapshot(project)
    assert story_audit.adjudicate_foreshadowing(
        project,
        name="从未登记的伏笔",
        action="reopen",
        reason="试图凭空开启",
        chapter=2,
        silent=True,
    ) == (3, Path(""))
    assert _snapshot(project) == before


@pytest.mark.parametrize(
    "options",
    [
        {"action": "delete"},
        {"action": "confirm", "reason": "   "},
        {"action": "confirm", "reason": None},
        {"action": "confirm", "name": ""},
        {"action": "confirm", "source": "model"},
        {"action": "confirm", "chapter": float("inf")},
        {"action": "confirm", "chapter": True},
        {"action": "confirm", "chapter": "1"},
        {"action": "confirm", "evidence": None},
    ],
)
def test_f03_invalid_adjudication_has_no_side_effects(project, options):
    _publish_pending_commitment(project)
    before = _snapshot(project)
    payload = {"name": NAME, "action": "confirm", "reason": "合法原因"}
    payload.update(options)
    assert story_audit.adjudicate_foreshadowing(project, silent=True, **payload) == (3, Path(""))
    assert _snapshot(project) == before
    assert len(_state(project)["foreshadowing_commitments"]) == 1


@pytest.mark.parametrize("bad_project", [None, 123, "", "missing"])
def test_f03_invalid_project_dir_is_controlled(project, bad_project):
    before = _snapshot(project)
    target = project / "missing" if bad_project == "missing" else bad_project
    assert story_audit.adjudicate_foreshadowing(
        target, name=NAME, action="confirm", reason="合法原因", silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before


def test_f03_legacy_state_is_accepted_and_blocks_reactivation(project):
    """旧状态文件不含裁决字段时仍可加载，且历史关闭记录继续压制旧标签。"""
    reports = project / "reports"
    legacy_resolved = {"tag": NAME, "origin_chapter": 1, "status": "resolved", "note": "作者已核验"}
    legacy_commitment = {"tag": "另一条线索", "origin_chapter": 2, "status": "pending", "note": "待核验"}
    save_audit_state(
        AuditState(foreshadowing_commitments=[legacy_commitment], resolved_items=[legacy_resolved]),
        reports,
    )
    assert story_audit.audit_chapter(project, chapter_index=1, mode="solo", silent=True)[0] == 0
    state = _state(project)
    assert [item["tag"] for item in state["foreshadowing_commitments"]] == ["另一条线索"]
    assert state["resolved_items"][0]["tag"] == NAME
