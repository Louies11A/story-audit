# -*- coding: utf-8 -*-
"""F06：资产候选变更预览、确认与幂等提交，以及批量账本原子性。"""

import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from scripts import story_audit
from scripts.ledger_engine import LedgerState, save_ledger_state
from scripts.safe_io import SafeIOWriteError


@pytest.fixture
def project():
    with TemporaryDirectory(prefix="story_audit_f06_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        (root / "正文" / "第001章.txt").write_text(
            "第一章\n陆离获得 5 枚灵石。\n", encoding="utf-8"
        )
        (root / "正文" / "第002章.txt").write_text(
            "第二章\n陆离消耗 2 枚灵石。\n", encoding="utf-8"
        )
        yield root


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def _ledger(project: Path):
    json_path, _ = story_audit.locate_ledger_paths(project)
    return json.loads(json_path.read_text(encoding="utf-8")), json_path


def _preview(project: Path, **options) -> dict:
    code, payload = story_audit.preview_asset_changes(project, silent=True, **options)
    assert code == 0
    return payload


def _asset_by_identity(state: dict, name: str, owner: str):
    matches = [
        item
        for item in state["assets"].values()
        if item["name"] == name and item["owner"] == owner
    ]
    assert len(matches) == 1, f"期望唯一的 {owner}/{name} 资产，实际 {len(matches)} 条"
    return matches[0]


def _host_event(name: str, owner: str, direction: str, quantity: float, **overrides) -> dict:
    event = {
        "name": name,
        "owner": owner,
        "direction": direction,
        "quantity": quantity,
        "unit": "个",
        "chapter": 1.0,
        "line_number": 2,
        "evidence": f"{owner}{'获得' if direction == 'gain' else '消耗'} {quantity} {name}",
        "source": "host",
    }
    event.update(overrides)
    return event


def test_f06_preview_returns_candidates_without_touching_ledger(project):
    """预览只读：返回结构化候选，不写账本、不把候选当成既定事实。"""
    before = _snapshot(project)
    payload = _preview(project, chapter_index=1)
    assert _snapshot(project) == before

    assert payload["chapters"] == [1.0]
    assert payload["counts"]["total"] == 1
    assert payload["counts"]["gain"] == 1
    candidate = payload["candidates"][0]
    assert candidate["event_id"].startswith("asset-event-")
    assert candidate["name"] == "灵石"
    assert candidate["owner"] == "主角"
    assert candidate["direction"] == "gain"
    assert candidate["quantity"] == 5
    assert candidate["unit"] == "枚"
    assert candidate["chapter"] == 1
    assert candidate["line_number"] == 2
    assert "5 枚灵石" in candidate["evidence"]
    assert candidate["source"] == "heuristic"
    assert candidate["existing_asset_id"] == ""
    assert candidate["existing_quantity"] is None

    # event_id 稳定；重复预览不产生新文件
    assert _preview(project, chapter_index=1)["candidates"][0]["event_id"] == candidate["event_id"]
    json_path, _ = story_audit.locate_ledger_paths(project)
    assert not json_path.exists()


def test_f06_preview_detects_both_directions_across_scope(project):
    payload = _preview(project, scope_str="1-2")
    assert payload["chapters"] == [1.0, 2.0]
    seen = {(item["name"], item["direction"], item["quantity"]) for item in payload["candidates"]}
    assert ("灵石", "gain", 5) in seen
    assert ("灵石", "consume", 2) in seen
    assert payload["counts"]["gain"] == 1 and payload["counts"]["consume"] == 1


def test_f06_confirm_gain_and_consume_updates_balance(project):
    """确认获取 5 枚、消耗 2 枚后余额为 3，事件与资产流水均可追溯。"""
    gain = _preview(project, chapter_index=1)["candidates"][0]
    code, ledger_path = story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )
    assert code == 0
    state, json_path = _ledger(project)
    assert ledger_path == json_path
    asset = _asset_by_identity(state, "灵石", "主角")
    assert asset["quantity"] == 5
    assert asset["unit"] == "枚"
    assert asset["category"] == "资金资产"
    assert [item["action"] for item in asset["history"]] == ["modify_quantity"]
    assert asset["history"][0]["delta"] == 5

    consume = _host_event("灵石", "主角", "consume", 2, unit="枚", chapter=2.0)
    code, _ = story_audit.confirm_asset_event(
        project,
        event=consume,
        decision="accept",
        reason="宿主确认消耗",
        source="expert",
        silent=True,
    )
    assert code == 0
    state, _ = _ledger(project)
    asset = _asset_by_identity(state, "灵石", "主角")
    assert asset["quantity"] == 3
    assert [item["action"] for item in asset["history"]] == ["modify_quantity", "modify_quantity"]
    assert asset["history"][-1]["delta"] == -2

    events = state["asset_events"]
    assert [event["decision"] for event in events] == ["accept", "accept"]
    assert events[0]["source"] == "author"
    assert events[0]["candidate_source"] == "heuristic"
    assert events[0]["quantity_before"] == 0 and events[0]["quantity_after"] == 5
    assert events[1]["source"] == "expert"
    assert events[1]["candidate_source"] == "host"
    assert events[1]["quantity_before"] == 5 and events[1]["quantity_after"] == 3
    assert events[1]["chapter"] == 2
    assert events[1]["decided_at"]
    assert events[1]["event_id"].startswith("asset-event-")
    assert state["last_updated_chapter"] == 2.0


def test_f06_replaying_same_event_is_idempotent(project):
    gain = _preview(project, chapter_index=1)["candidates"][0]
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    before = _snapshot(project)

    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    assert _snapshot(project) == before
    state, _ = _ledger(project)
    assert _asset_by_identity(state, "灵石", "主角")["quantity"] == 5
    assert len(state["asset_events"]) == 1


def test_f06_reject_records_decision_without_quantity_change(project):
    gain = _preview(project, chapter_index=1)["candidates"][0]
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="reject", reason="证据不足，暂不认定", silent=True
    )[0] == 0
    state, _ = _ledger(project)
    assert state["assets"] == {}
    events = state["asset_events"]
    assert len(events) == 1
    assert events[0]["decision"] == "reject"
    assert events[0]["reason"] == "证据不足，暂不认定"
    assert events[0]["quantity_before"] is None and events[0]["quantity_after"] is None

    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="reject", reason="证据不足，暂不认定", silent=True
    )[0] == 0
    assert _snapshot(project) == before


def test_f06_same_name_different_owners_stay_separate(project):
    for owner, quantity in (("陆离", 1), ("苏婉", 2)):
        event = _host_event("青钢剑", owner, "gain", quantity, unit="柄")
        assert story_audit.confirm_asset_event(
            project, event=event, decision="accept", reason=f"作者确认{owner}获得", silent=True
        )[0] == 0

    state, _ = _ledger(project)
    lu = _asset_by_identity(state, "青钢剑", "陆离")
    su = _asset_by_identity(state, "青钢剑", "苏婉")
    assert lu["id"] != su["id"]
    assert lu["quantity"] == 1 and su["quantity"] == 2

    consume = _host_event("青钢剑", "陆离", "consume", 1, unit="柄", chapter=3.0)
    assert story_audit.confirm_asset_event(
        project, event=consume, decision="accept", reason="作者确认陆离损坏", silent=True
    )[0] == 0
    state, _ = _ledger(project)
    assert _asset_by_identity(state, "青钢剑", "陆离")["quantity"] == 0
    assert _asset_by_identity(state, "青钢剑", "苏婉")["quantity"] == 2


def test_f06_zeroing_consumable_reuses_transition_semantics(project):
    gain = _host_event("聚气丹", "主角", "gain", 2, unit="枚")
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    consume = _host_event("聚气丹", "主角", "consume", 2, unit="枚", chapter=2.0)
    assert story_audit.confirm_asset_event(
        project, event=consume, decision="accept", reason="作者确认服用", silent=True
    )[0] == 0
    state, _ = _ledger(project)
    asset = _asset_by_identity(state, "聚气丹", "主角")
    assert asset["quantity"] == 0
    assert asset["status"] == "CONSUMED"
    assert [item["action"] for item in asset["history"]] == [
        "modify_quantity",
        "modify_quantity",
        "transition",
    ]


def test_f06_consuming_unknown_asset_and_conflicting_decision_have_no_side_effects(project):
    consume = _host_event("不存在的灵剑", "主角", "consume", 1, unit="柄")
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=consume, decision="accept", reason="直接扣账", silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before

    gain = _preview(project, chapter_index=1)["candidates"][0]
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="reject", reason="事后改判", silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before
    state, _ = _ledger(project)
    assert _asset_by_identity(state, "灵石", "主角")["quantity"] == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": 5},
        {"direction": "steal"},
        {"quantity": 0},
        {"quantity": -1},
        {"quantity": float("inf")},
        {"quantity": "5"},
        {"chapter": float("inf")},
        {"chapter": "1"},
        {"unit": 5},
        {"evidence": 5},
        {"line_number": 0},
    ],
)
def test_f06_invalid_event_payloads_have_no_side_effects(project, overrides):
    event = _host_event("灵石", "主角", "gain", 5, unit="枚")
    event.update(overrides)
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=event, decision="accept", reason="作者确认", silent=True
    ) == (3, Path(""))
    assert _snapshot(project) == before


@pytest.mark.parametrize(
    "options",
    [
        {"event": None},
        {"event": "asset-event-123"},
        {"event": {"direction": "gain", "quantity": 5, "chapter": 1}},
        {"event": _host_event("灵石", "主角", "gain", 5), "decision": "maybe"},
        {"event": _host_event("灵石", "主角", "gain", 5), "source": "model"},
        {"event": _host_event("灵石", "主角", "gain", 5), "reason": "   "},
        {"event": _host_event("灵石", "主角", "gain", 5), "owner": ""},
    ],
)
def test_f06_invalid_confirm_arguments_have_no_side_effects(project, options):
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(project, silent=True, **options) == (3, Path(""))
    assert _snapshot(project) == before


def test_f06_invalid_preview_arguments_have_no_side_effects(project):
    before = _snapshot(project)
    assert story_audit.preview_asset_changes(project, silent=True) == (3, {})
    assert story_audit.preview_asset_changes(
        project, scope_str="1-2", chapter_index=1, silent=True
    ) == (3, {})
    assert story_audit.preview_asset_changes(project, scope_str="bad", silent=True) == (3, {})
    assert story_audit.preview_asset_changes(project, chapter_index=99, silent=True) == (3, {})
    assert story_audit.preview_asset_changes(
        project, chapter_index=1, owner="   ", silent=True
    ) == (3, {})
    assert _snapshot(project) == before


def test_f06_corrupt_ledger_blocks_preview_and_confirm(project):
    json_path, _ = story_audit.locate_ledger_paths(project)
    json_path.write_text("{broken", encoding="utf-8")
    before = _snapshot(project)

    assert story_audit.preview_asset_changes(project, chapter_index=1, silent=True) == (3, {})
    assert story_audit.confirm_asset_event(
        project,
        event=_host_event("灵石", "主角", "gain", 5),
        decision="accept",
        reason="作者确认",
        silent=True,
    ) == (3, Path(""))
    assert _snapshot(project) == before

    # 事件流水结构损坏时同样零副作用
    valid = {
        "last_updated_chapter": 1.0,
        "assets": {},
        "foreshadowing_stash": [],
        "asset_events": "损坏的事件流水",
    }
    json_path.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8")
    before = _snapshot(project)
    assert story_audit.preview_asset_changes(project, chapter_index=1, silent=True) == (3, {})
    assert story_audit.confirm_asset_event(
        project,
        event=_host_event("灵石", "主角", "gain", 5),
        decision="accept",
        reason="作者确认",
        silent=True,
    ) == (3, Path(""))
    assert _snapshot(project) == before


def test_f06_legacy_ledger_without_events_still_works(project):
    settings = project / "设定"
    settings.mkdir()
    json_path = settings / "资源账本.json"
    md_path = settings / "资源账本.md"
    legacy = {
        "last_updated_chapter": 1.0,
        "assets": {
            "ast_old_1_青钢剑": {
                "id": "ast_old_1_青钢剑",
                "name": "青钢剑",
                "category": "装备道具",
                "quantity": 1,
                "unit": "柄",
                "owner": "陆离",
                "current_holder": "陆离",
                "status": "EQUIPPED",
                "origin_chapter": 1.0,
                "constraints": {},
                "history": [],
            }
        },
        "foreshadowing_stash": [],
    }
    json_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    md_path.write_text("# 资源账本（截至第 1 章）\n", encoding="utf-8")

    payload = _preview(project, chapter_index=1)
    assert payload["counts"]["total"] == 1
    assert payload["counts"]["matched_existing"] == 0

    event = _host_event("青钢剑", "陆离", "gain", 1, unit="柄", chapter=2.0)
    assert story_audit.confirm_asset_event(
        project, event=event, decision="accept", reason="作者确认补记", silent=True
    )[0] == 0
    state, _ = _ledger(project)
    asset = _asset_by_identity(state, "青钢剑", "陆离")
    assert asset["id"] == "ast_old_1_青钢剑"
    assert asset["quantity"] == 2
    assert len(state["asset_events"]) == 1


def test_f06_preview_matches_existing_identity(project):
    gain = _preview(project, chapter_index=1)["candidates"][0]
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    payload = _preview(project, chapter_index=1)
    candidate = payload["candidates"][0]
    state, _ = _ledger(project)
    assert candidate["existing_asset_id"] == _asset_by_identity(state, "灵石", "主角")["id"]
    assert candidate["existing_quantity"] == 5
    assert payload["counts"]["matched_existing"] == 1


def test_f06_confirm_rejects_dirty_markdown_without_side_effects(project):
    save_ledger_state(
        LedgerState(),
        project / "设定" / "资源账本.json",
        project / "设定" / "资源账本.md",
    )
    md_path = project / "设定" / "资源账本.md"
    md_path.write_text(md_path.read_text(encoding="utf-8") + "\n<!-- 手工编辑 -->\n", encoding="utf-8")
    newer = time.time() + 5
    os.utime(md_path, (newer, newer))
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project,
        event=_host_event("灵石", "主角", "gain", 5),
        decision="accept",
        reason="作者确认",
        silent=True,
    ) == (3, Path(""))
    assert _snapshot(project) == before


def test_a1_owner_override_recomputes_event_identity(project):
    """A1：owner 覆盖后事件编号按 (名称, 所有者) 重算，不得出现静默 no-op。"""
    candidate = _preview(project, chapter_index=1)["candidates"][0]
    assert story_audit.confirm_asset_event(
        project,
        event=candidate,
        decision="accept",
        owner="配角",
        reason="作者确认配角获得",
        silent=True,
    )[0] == 0
    state, _ = _ledger(project)
    assert _asset_by_identity(state, "灵石", "配角")["quantity"] == 5
    event = state["asset_events"][0]
    assert event["owner"] == "配角"
    assert event["event_id"] != candidate["event_id"]
    assert event["supplied_event_id"] == candidate["event_id"]

    # 再以同一候选按主角提交：必须真正入账，而不是命中配角的幂等。
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=candidate, decision="accept", reason="作者确认主角获得", silent=True
    )[0] == 0
    assert _snapshot(project) != before
    state, _ = _ledger(project)
    assert _asset_by_identity(state, "灵石", "主角")["quantity"] == 5
    assert _asset_by_identity(state, "灵石", "配角")["quantity"] == 5
    assert len(state["asset_events"]) == 2

    # 按主角重复提交仍幂等
    before = _snapshot(project)
    assert story_audit.confirm_asset_event(
        project, event=candidate, decision="accept", reason="作者确认主角获得", silent=True
    )[0] == 0
    assert _snapshot(project) == before


def test_a2_over_consume_records_actual_quantity_and_flag(project):
    """A2：越界消耗被钳制时，事件流水必须记录实际扣减量与 over_consume。"""
    gain = _host_event("聚气丹", "主角", "gain", 2, unit="枚")
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    consume = _host_event("聚气丹", "主角", "consume", 5, unit="枚", chapter=2.0)
    assert story_audit.confirm_asset_event(
        project, event=consume, decision="accept", reason="作者确认服用", silent=True
    )[0] == 0

    state, _ = _ledger(project)
    asset = _asset_by_identity(state, "聚气丹", "主角")
    assert asset["quantity"] == 0
    event = state["asset_events"][-1]
    assert event["quantity"] == 5
    assert event["quantity_before"] == 2 and event["quantity_after"] == 0
    assert event["actual_quantity"] == 2
    assert event["over_consume"] is True
    assert state["asset_events"][0]["over_consume"] is False

    # 事件求和与账面一致：2 获得 - 2 实际扣减 = 0
    applied = sum(
        item["actual_quantity"] if item["direction"] == "gain" else -item["actual_quantity"]
        for item in state["asset_events"]
        if item["decision"] == "accept"
    )
    assert applied == asset["quantity"] == 0


def test_a3_ledger_markdown_renders_decision_log_and_round_trips(project):
    """A3：账本 Markdown 渲染裁决流水，且 sync_ledger_from_md 往返不丢事件。"""
    gain = _host_event("聚气丹", "主角", "gain", 2, unit="枚")
    assert story_audit.confirm_asset_event(
        project, event=gain, decision="accept", reason="作者确认获得", silent=True
    )[0] == 0
    consume = _host_event("聚气丹", "主角", "consume", 5, unit="枚", chapter=2.0)
    assert story_audit.confirm_asset_event(
        project, event=consume, decision="accept", reason="作者确认服用", silent=True
    )[0] == 0
    _, md_path = story_audit.locate_ledger_paths(project)
    ledger_md = md_path.read_text(encoding="utf-8")
    state, _ = _ledger(project)
    assert "裁决流水" in ledger_md
    for event in state["asset_events"]:
        assert event["event_id"] in ledger_md
    assert "实际变动" in ledger_md
    assert "超扣钳制" in ledger_md
    assert "作者确认服用" in ledger_md

    before_events = state["asset_events"]
    assert story_audit.sync_ledger_from_md(project, silent=True) == 0
    reloaded, _ = _ledger(project)
    assert reloaded["asset_events"] == before_events
    assert _asset_by_identity(reloaded, "聚气丹", "主角")["quantity"] == 0
    # 往返后 Markdown 仍保留裁决流水小节
    assert "裁决流水" in md_path.read_text(encoding="utf-8")


def test_a4_balance_statements_are_not_previewed_as_gain(project):
    """A4：余额陈述不得被误判为获取候选。"""
    (project / "正文" / "第003章.txt").write_text(
        "第三章\n剩余 3 枚灵石。\n他还只剩 2 枚灵石。\n", encoding="utf-8"
    )
    payload = _preview(project, scope_str="3-3")
    assert payload["counts"]["total"] == 0

    # 对照：真实获取仍然要给候选
    (project / "正文" / "第003章.txt").write_text(
        "第三章\n陆离获得 3 枚灵石。\n他还只剩 2 枚灵石。\n", encoding="utf-8"
    )
    payload = _preview(project, scope_str="3-3")
    assert payload["counts"]["total"] == 1
    assert payload["candidates"][0]["direction"] == "gain"
    assert payload["candidates"][0]["quantity"] == 3


def test_batch_audit_failure_rolls_back_ledger_writes(project):
    """批量审查失败（中途失败或最终保存失败）时账本文件必须回滚。"""
    tag = '<!-- audit:stash name="海门钥匙" origin="第1章" status="pending" -->'
    (project / "正文" / "第001章.txt").write_text(
        "第一章\n陆离获得 5 枚灵石。\n" + tag + "\n", encoding="utf-8"
    )
    (project / "正文" / "第002章.txt").write_text(
        "第二章\n陆离消耗 2 枚灵石。\n" + tag + "\n", encoding="utf-8"
    )

    # 1) 批次最终状态保存失败
    before = _snapshot(project)
    with patch.object(story_audit, "save_audit_state", side_effect=SafeIOWriteError("模拟状态保存失败")):
        assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True) == (3, Path(""))
    assert _snapshot(project) == before
    assert not (project / "资源账本.json").exists()

    # 2) 批次中途章节读取失败（前一章账本已写入）
    (project / "正文" / "第002章.txt").write_bytes(b"\x00")
    before = _snapshot(project)
    assert story_audit.audit_scope(project, "1-2", mode="solo", silent=True) == (3, Path(""))
    assert _snapshot(project) == before
    assert not (project / "资源账本.json").exists()
