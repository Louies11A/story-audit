# -*- coding: utf-8 -*-
"""F09：规模预算上下文装配与按需历史检索。"""

import json
import tempfile
from pathlib import Path

import pytest

from scripts import story_audit
from scripts.ledger_engine import AssetItem, LedgerState, save_ledger_state


def _make_project(chapters: int = 3) -> Path:
    root = Path(tempfile.mkdtemp(prefix="story_audit_f09_"))
    (root / "正文").mkdir(parents=True)
    for index in range(1, chapters + 1):
        (root / "正文" / f"第{index:03d}章.txt").write_text(
            f"第{index}章\n陆离在第{index}段旅程中继续前行。\n", encoding="utf-8"
        )
    return root


def _save_assets(project: Path, assets: dict) -> None:
    save_ledger_state(
        LedgerState(last_updated_chapter=30.0, assets=assets),
        project / "设定" / "资源账本.json",
        project / "设定" / "资源账本.md",
    )


def _asset(index: int, history_len: int, name: str = "") -> AssetItem:
    history = [
        {
            "action": "modify_quantity",
            "delta": 1,
            "from_quantity": step,
            "to_quantity": step + 1,
            "chapter": float(step + 1),
            "reason": f"第{step + 1}章清点记录",
            "timestamp": 1700000000.0 + step,
        }
        for step in range(history_len)
    ]
    return AssetItem(
        id=f"ast_{index:04d}",
        name=name or f"物资{index:03d}",
        category="装备道具",
        quantity=index,
        unit="件",
        owner="主角",
        current_holder="主角",
        status="ACQUIRED",
        origin_chapter=1.0,
        history=history,
    )


def _snapshot(project: Path):
    return {
        path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
        for path in project.rglob("*")
    }


def test_f09_omission_counts_match_package_after_budget_trimming():
    """修复 1：预算裁剪后省略清单计数必须与包内实际保留条数一致。"""
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 8)})
    code, full = story_audit.build_context_package(project, chapter_index=1, silent=True)
    assert code == 0
    full_bytes = full["budget"]["used_bytes"]

    seen_kept = set()
    seen_dropped = False
    for budget in range(full_bytes, 300, -137):
        code, payload = story_audit.build_context_package(
            project, chapter_index=1, budget=budget, silent=True
        )
        assert code == 0
        assets = payload["package"]["ledger_snapshot"]["active_assets"]
        if assets:
            kept = len(assets[0]["history"])
            seen_kept.add(kept)
            entries = [item for item in payload["omissions"] if item["kind"] == "asset_history"]
            assert len(entries) == 1
            entry = entries[0]
            assert entry["kept_entries"] == kept
            assert entry["omitted_entries"] == 8 - kept
            assert entry["budget_trimmed"] is (kept < 5)
            assert entry["full_evidence"]["json_pointer"] == "/assets/ast_0001/history"
        else:
            seen_dropped = True
            dropped = [item for item in payload["omissions"] if item["kind"] == "asset_omitted"]
            assert len(dropped) == 1
            assert dropped[0]["omitted_entries"] == 8
            assert dropped[0]["kept_entries"] == 0
            assert dropped[0]["budget_trimmed"] is True
            assert dropped[0]["reason"].startswith("超出规模预算")

    assert {3, 1, 0} <= seen_kept
    assert seen_dropped is True


def test_f09_tiny_budget_reports_fixed_overhead_and_packet_bytes():
    """修复 2/3：极小预算说明固定开销，并给出整体返回体规模与预算口径。"""
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 8)})
    code, payload = story_audit.build_context_package(
        project, chapter_index=1, budget=200, silent=True
    )
    assert code == 0
    budget = payload["budget"]
    assert budget["scope"] == "package"
    assert budget["fixed_overhead_bytes"] > 0
    assert budget["minimal_package_bytes"] == budget["fixed_overhead_bytes"]
    assert budget["used_bytes"] == budget["minimal_package_bytes"]
    assert budget["within_budget"] is False
    assert "固定开销" in budget["over_budget_reason"]
    assert payload["package"]["ledger_snapshot"]["active_assets"] == []
    assert payload["insufficient_context"]["insufficient_context"] is True

    actual_packet_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert budget["packet_bytes"] >= budget["used_bytes"]
    assert abs(budget["packet_bytes"] - actual_packet_bytes) <= 64

    code, unrestricted = story_audit.build_context_package(project, chapter_index=1, silent=True)
    assert code == 0
    assert unrestricted["budget"]["within_budget"] is True
    assert unrestricted["budget"]["over_budget_reason"] == ""


def test_f09_small_ledger_has_no_omissions():
    """小账本不产生多余省略标记。"""
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 3)})
    code, payload = story_audit.build_context_package(project, chapter_index=2, silent=True)
    assert code == 0
    assert payload["omissions"] == []
    assert payload["insufficient_context"] == {
        "history_truncated": False,
        "omitted_assets": 0,
        "insufficient_context": False,
    }
    assert payload["budget"]["requested"] is False
    assert payload["budget"]["within_budget"] is True
    assert payload["budget"]["used_bytes"] > 0
    assert "不代表不存在冲突" in payload["warning"]
    assert payload["counts"]["assets_in_package"] == 1
    assets = payload["package"]["ledger_snapshot"]["active_assets"]
    assert len(assets[0]["history"]) == 3


def test_f09_history_truncation_is_reported_with_pointer():
    """预审包最近 5 条截断必须如实列入省略清单并可定位。"""
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 8)})
    code, payload = story_audit.build_context_package(project, chapter_index=1, silent=True)
    assert code == 0
    assert payload["insufficient_context"]["history_truncated"] is True
    omission = next(item for item in payload["omissions"] if item["kind"] == "asset_history")
    assert omission["asset_id"] == "ast_0001"
    assert omission["omitted_entries"] == 3
    assert omission["kept_entries"] == 5
    assert omission["full_evidence"]["json_pointer"] == "/assets/ast_0001/history"
    assert "query_asset_history" in omission["full_evidence"]["query_hint"]
    assert len(payload["package"]["ledger_snapshot"]["active_assets"][0]["history"]) == 5


def test_f09_entity_filter_records_omitted_assets():
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 2, "青钢剑"), "ast_0002": _asset(2, 2, "灵石")})
    code, payload = story_audit.build_context_package(
        project, chapter_index=1, entities=["青钢剑"], silent=True
    )
    assert code == 0
    names = [item["name"] for item in payload["package"]["ledger_snapshot"]["active_assets"]]
    assert names == ["青钢剑"]
    filtered = [item for item in payload["omissions"] if item["kind"] == "asset_filtered"]
    assert len(filtered) == 1 and filtered[0]["asset_name"] == "灵石"
    assert payload["insufficient_context"]["omitted_assets"] == 1


def test_f09_large_sample_stays_within_budget():
    """大样本（200 资产 × 30 条历史）下装配结果必须落在预算内。"""
    project = _make_project(chapters=3)
    _save_assets(
        project,
        {f"ast_{index:04d}": _asset(index, 30) for index in range(1, 201)},
    )
    budget = 60_000
    code, payload = story_audit.build_context_package(
        project, chapter_index=1, budget=budget, silent=True
    )
    assert code == 0
    assert payload["budget"]["requested"] is True
    assert payload["budget"]["limit_bytes"] == budget
    assert payload["budget"]["used_bytes"] <= budget
    assert payload["budget"]["within_budget"] is True
    # 输出规模与自报口径一致
    size = len(json.dumps(payload["package"], ensure_ascii=False).encode("utf-8"))
    assert size == payload["budget"]["used_bytes"]
    assert payload["insufficient_context"]["insufficient_context"] is True
    kinds = {item["kind"] for item in payload["omissions"]}
    assert kinds & {"asset_history", "asset_omitted"}
    assert payload["package"]["ledger_snapshot"].get("history_budget_applied") is True


def test_f09_query_asset_history_returns_older_entries_with_locations():
    """可检索最近 5 条之外的指定历史并给出定位。"""
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 8)})
    code, payload = story_audit.query_asset_history(
        project, name="物资001", limit=3, offset=5, silent=True
    )
    assert code == 0
    assert payload["stored_history_len"] == 8
    assert payload["returned"] == 3
    assert payload["has_more"] is False
    indexes = [entry["history_index"] for entry in payload["entries"]]
    assert indexes == [2, 1, 0]
    assert payload["entries"][0]["location"]["json_pointer"] == "/assets/ast_0001/history/2"
    assert payload["entries"][0]["location"]["ledger_path"] == "设定/资源账本.json"
    assert payload["entries"][0]["reason"]

    code, filtered = story_audit.query_asset_history(
        project, name="物资001", limit=10, chapter_range="1-2", silent=True
    )
    assert code == 0
    assert filtered["matched_history_len"] == 2
    assert all(entry["chapter"] <= 2 for entry in filtered["entries"])


def test_f09_invalid_inputs_and_corrupt_ledger_have_no_side_effects():
    project = _make_project()
    _save_assets(project, {"ast_0001": _asset(1, 8)})
    before = _snapshot(project)

    assert story_audit.build_context_package(project, chapter_index=1, budget=0, silent=True) == (3, {})
    assert story_audit.build_context_package(project, chapter_index=1, budget=-5, silent=True) == (3, {})
    assert story_audit.build_context_package(project, chapter_index=1, budget=True, silent=True) == (3, {})
    assert story_audit.build_context_package(project, chapter_index=1, entities="灵石", silent=True) == (3, {})
    assert story_audit.build_context_package(project, chapter_index=1, entities=[""], silent=True) == (3, {})
    assert story_audit.build_context_package(project, chapter_index=99, silent=True) == (3, {})
    assert story_audit.query_asset_history(project, name="", silent=True) == (3, {})
    assert story_audit.query_asset_history(project, name="物资001", limit=0, silent=True) == (3, {})
    assert story_audit.query_asset_history(project, name="物资001", offset=-1, silent=True) == (3, {})
    assert story_audit.query_asset_history(
        project, name="物资001", chapter_range="bad", silent=True
    ) == (3, {})
    assert story_audit.query_asset_history(project, name="不存在", silent=True) == (3, {})
    assert _snapshot(project) == before

    json_path, _ = story_audit.locate_ledger_paths(project)
    json_path.write_text("{broken", encoding="utf-8")
    before = _snapshot(project)
    assert story_audit.build_context_package(project, chapter_index=1, silent=True) == (3, {})
    assert story_audit.query_asset_history(project, name="物资001", silent=True) == (3, {})
    assert _snapshot(project) == before
