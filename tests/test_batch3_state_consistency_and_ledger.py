# -*- coding: utf-8 -*-
"""Batch 3: 状态因果一致性、账本双轨同步与数字解析增强测试。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pytest

from scripts import story_audit
from scripts.audit_state import (
    AuditState,
    _matches_chapter_filter,
    is_foreshadowing_adjudicated,
    save_audit_state,
)
from scripts.ledger_engine import (
    AssetItem,
    LedgerState,
    parse_chinese_or_arabic_number,
    save_ledger_state,
)


@pytest.fixture
def temp_project():
    with TemporaryDirectory(prefix="batch3_test_") as td:
        root = Path(td)
        (root / "正文").mkdir(parents=True)
        (root / "reports").mkdir(parents=True)
        (root / "正文" / "第001章.txt").write_text("第一章\n初入江湖。\n", encoding="utf-8")
        (root / "正文" / "第002章.txt").write_text("第二章\n风起云涌。\n", encoding="utf-8")
        yield root


# ============================================================================
# 1. 伏笔显式裁决双轨同步 (P0-02)
# ============================================================================

def test_foreshadowing_adjudication_syncs_to_ledger_confirm(temp_project):
    """P0-02: confirm 裁决成功后，同步更新账本中对应条目状态为 RESOLVED 并记录时间戳。"""
    save_audit_state(
        AuditState(foreshadowing_commitments=[{"tag": "青铜钥匙", "origin_chapter": 1.0, "status": "pending"}]),
        temp_project / "reports",
    )
    ledger = LedgerState(
        foreshadowing_stash=[
            {"name": "青铜钥匙", "origin": "第1章", "status": "pending", "source_chapter": 1.0}
        ]
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(ledger, json_path, md_path)

    code, state_path = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="青铜钥匙",
        action="confirm",
        reason="第1章已回收",
        evidence="第1章 开门",
        chapter=1,
        silent=True,
    )
    assert code == 0
    assert state_path.is_file()

    # 验证账本双轨同步
    ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
    stash_item = ledger_data["foreshadowing_stash"][0]
    assert stash_item["status"] == "RESOLVED"
    assert "adjudicated_at" in stash_item
    assert stash_item["adjudicated_at"].strip() != ""

    # 验证 Markdown 账本也同步更新
    md_text = md_path.read_text(encoding="utf-8")
    assert "RESOLVED" in md_text


def test_foreshadowing_adjudication_syncs_to_ledger_close_and_reopen(temp_project):
    """P0-02: close 裁决同步为 CLOSED，reopen 裁决同步为 PENDING。"""
    save_audit_state(
        AuditState(foreshadowing_commitments=[{"tag": "残破地图", "origin_chapter": 1.0, "status": "pending"}]),
        temp_project / "reports",
    )
    ledger = LedgerState(
        foreshadowing_stash=[
            {"name": "残破地图", "origin": "第1章", "status": "pending", "source_chapter": 1.0}
        ]
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(ledger, json_path, md_path)

    # 1. close -> CLOSED
    code, _ = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="残破地图",
        action="close",
        reason="放弃追索",
        chapter=1,
        silent=True,
    )
    assert code == 0
    ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert ledger_data["foreshadowing_stash"][0]["status"] == "CLOSED"

    # 2. reopen -> PENDING
    code, _ = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="残破地图",
        action="reopen",
        reason="重新发现线索",
        chapter=1,
        silent=True,
    )
    assert code == 0
    ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert ledger_data["foreshadowing_stash"][0]["status"] == "PENDING"


def test_foreshadowing_adjudication_prefers_matching_chapter_over_no_chapter(temp_project):
    """P0-02: 指定 chapter 时优先匹配对应 chapter，无精确匹配时匹配无章号项。"""
    save_audit_state(
        AuditState(
            foreshadowing_commitments=[
                {"tag": "密室暗道", "origin_chapter": 1.0, "status": "pending"},
                {"tag": "密室暗道", "origin_chapter": None, "status": "pending", "note": "传闻"},
            ]
        ),
        temp_project / "reports",
    )
    ledger = LedgerState(
        foreshadowing_stash=[
            {"name": "密室暗道", "origin": "第1章", "status": "pending", "source_chapter": 1.0},
            {"name": "密室暗道", "origin": "古籍中的传闻", "status": "pending"},
        ]
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(ledger, json_path, md_path)

    # 指定 chapter=1: 应该精确匹配第1章条目，不影响无章号项
    code, _ = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="密室暗道",
        action="close",
        reason="第1章暗道已坍塌",
        chapter=1,
        silent=True,
    )
    assert code == 0
    ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert ledger_data["foreshadowing_stash"][0]["status"] == "CLOSED"
    assert ledger_data["foreshadowing_stash"][1]["status"] == "pending"

    # 再次针对无章号项裁决 (chapter=2，在没有第2章项时匹配无章号项)
    code, _ = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="密室暗道",
        action="confirm",
        reason="古籍传闻已证实",
        chapter=2,
        silent=True,
    )
    assert code == 0
    ledger_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert ledger_data["foreshadowing_stash"][1]["status"] == "RESOLVED"


def test_foreshadowing_adjudication_without_ledger_succeeds(temp_project):
    """P0-02: 当项目不存在账本文件时，伏笔裁决依然平稳执行。"""
    save_audit_state(
        AuditState(foreshadowing_commitments=[{"tag": "神秘石碑", "origin_chapter": 1.0, "status": "pending"}]),
        temp_project / "reports",
    )
    code, state_path = story_audit.run_foreshadowing_adjudication(
        temp_project,
        name="神秘石碑",
        action="confirm",
        reason="已参透",
        chapter=1,
        silent=True,
    )
    assert code == 0
    assert state_path.is_file()


# ============================================================================
# 2. run_scope_audit 异常回滚遗漏 (P0-03)
# ============================================================================

def test_run_scope_audit_rolls_back_ledger_on_expert_refresh_exception(temp_project):
    """P0-03: 在循环结束后 _refresh_expert_records 异常时，必须调用 _rollback_batch_ledger。"""
    # 在第1章写入伏笔标签，促使批量审查在章节审查阶段向账本写入新条目
    (temp_project / "正文" / "第001章.txt").write_text(
        '第一章\n<!-- audit:stash name="龙泉宝剑" origin="第1章" status="pending" -->\n',
        encoding="utf-8"
    )
    initial_ledger = LedgerState(
        last_updated_chapter=0.0,
        assets={
            "ast_001": AssetItem(id="ast_001", name="破伤风之刃", category="装备道具", quantity=1, unit="把")
        }
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(initial_ledger, json_path, md_path)
    initial_json_bytes = json_path.read_bytes()

    orig_refresh = story_audit._refresh_expert_records
    calls = []
    def selective_refresh(*args, **kwargs):
        calls.append(1)
        # 前两次调用来自各章单章审查，第三次调用来自循环结束后的聚合大盘阶段
        if len(calls) > 2:
            raise RuntimeError("专家记录存储在聚合阶段损坏")
        return orig_refresh(*args, **kwargs)

    with patch("scripts.story_audit._refresh_expert_records", side_effect=selective_refresh):
        code = story_audit.run_scope_audit(
            temp_project,
            scope_str="1-2",
            strict=False,
            force=True,
            silent=True,
        )
        assert code == 3

    # 验证账本已被回滚至初始状态（未留下龙泉宝剑等脏数据）
    assert json_path.read_bytes() == initial_json_bytes


# ============================================================================
# 3. run_query_asset_history 布尔值拦截与同名资产歧义 (P1-07 / P2-11)
# ============================================================================

@pytest.mark.parametrize("bad_range", [
    [True, 10],
    [1, False],
    [True, False],
    (True, 5),
])
def test_query_asset_history_rejects_bool_in_chapter_range(temp_project, bad_range, capsys):
    """P1-07: chapter_range 中包含 bool 类型必须被拦截并抛出错误。"""
    ledger = LedgerState(
        assets={
            "ast_001": AssetItem(id="ast_001", name="灵石", category="资金资产", quantity=100, unit="枚")
        }
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(ledger, json_path, md_path)

    # 1. 静默模式返回 (3, {})
    code, payload = story_audit.query_asset_history(
        temp_project,
        name="灵石",
        chapter_range=bad_range,
        silent=True,
    )
    assert code == 3
    assert payload == {}

    # 2. 非静默模式输出包含特定错误信息
    code, _ = story_audit.query_asset_history(
        temp_project,
        name="灵石",
        chapter_range=bad_range,
        silent=False,
    )
    assert code == 3
    captured = capsys.readouterr()
    assert "chapter_range 范围元素不能是布尔值" in captured.err


def test_query_asset_history_ambiguous_same_name_assets(temp_project, capsys):
    """P2-11: owner is None 且存在同名资产时，返回 ambiguous=True 与 candidates 列表。"""
    ledger = LedgerState(
        assets={
            "ast_001": AssetItem(id="ast_001", name="玄铁重剑", category="装备道具", quantity=1, unit="把", owner="杨过"),
            "ast_002": AssetItem(id="ast_002", name="玄铁重剑", category="装备道具", quantity=1, unit="把", owner="独孤求败"),
        }
    )
    json_path, md_path = story_audit.locate_ledger_paths(temp_project)
    save_ledger_state(ledger, json_path, md_path)

    # 1. 未指定 owner: 存在歧义
    code, payload = story_audit.query_asset_history(
        temp_project,
        name="玄铁重剑",
        owner=None,
        silent=False,
    )
    assert code == 0
    assert payload["ambiguous"] is True
    assert len(payload["candidates"]) == 2
    candidate_owners = {c["owner"] for c in payload["candidates"]}
    assert candidate_owners == {"杨过", "独孤求败"}
    assert "ambiguity_message" in payload
    assert "建议" in payload["ambiguity_message"]
    captured = capsys.readouterr()
    assert "提示" in captured.err or "提示" in captured.out or "歧义" in captured.err

    # 2. 指定 owner="杨过": 无歧义
    code, payload_specific = story_audit.query_asset_history(
        temp_project,
        name="玄铁重剑",
        owner="杨过",
        silent=True,
    )
    assert code == 0
    assert payload_specific["ambiguous"] is False
    assert payload_specific["candidates"] == []
    assert payload_specific["asset_id"] == "ast_001"


# ============================================================================
# 4. _matches_chapter_filter 无章号历史不压制后文新伏笔 (P1-02)
# ============================================================================

def test_matches_chapter_filter_un_chaptered_history_does_not_suppress_new_chapter():
    """P1-02: 当前项有明确章号时，历史无章号项不再通配。"""
    un_chaptered_item = {"tag": "青铜钥匙", "origin_chapter": None, "status": "resolved"}
    chaptered_item = {"tag": "青铜钥匙", "origin_chapter": 1.0, "status": "resolved"}

    # 当前检测项有明确章号 3.0: 历史无章号项不应匹配
    assert _matches_chapter_filter(un_chaptered_item, 3.0) is False

    # 当前检测项无明确章号 (None): 历史无章号项仍泛匹配
    assert _matches_chapter_filter(un_chaptered_item, None) is True

    # 明确章号匹配
    assert _matches_chapter_filter(chaptered_item, 1.0) is True
    assert _matches_chapter_filter(chaptered_item, 2.0) is False


def test_is_foreshadowing_adjudicated_integration():
    """P1-02 集成测试: 历史无章号已裁决伏笔不会误压制第3章新出现的同名伏笔。"""
    state = AuditState(
        resolved_items=[
            {"tag": "神秘古玉", "origin_chapter": None, "status": "resolved"}
        ]
    )
    # 第3章新伏笔不应被历史无章号条目判定为已裁决
    assert is_foreshadowing_adjudicated(state, "神秘古玉", origin_chapter=3.0) is False

    # 但无章号检查依然被判定为已裁决
    assert is_foreshadowing_adjudicated(state, "神秘古玉", origin_chapter=None) is True


# ============================================================================
# 5. parse_chinese_or_arabic_number 增强 (P2-05)
# ============================================================================

@pytest.mark.parametrize("input_str, expected", [
    ("半", 0.5),
    ("约半", 0.5),
    ("近半", 0.5),
    ("半多", 0.5),
    ("半余", 0.5),
    ("个半", 0.5),
])
def test_parse_chinese_or_arabic_number_half(input_str, expected):
    """P2-05: 支持“半”及前后修饰符。"""
    assert parse_chinese_or_arabic_number(input_str) == expected


@pytest.mark.parametrize("input_str, expected", [
    ("两千五", 2500),
    ("三万五", 35000),
    ("四百八", 480),
    ("一千二", 1200),
    ("两万三千五", 23500),
    ("五百一", 510),
    ("四万八", 48000),
    ("一百二", 120),
    ("七千九", 7900),
    ("约三万五", 35000),
    ("两千五多", 2500),
    # 既有传统格式不受影响
    ("两千五百", 2500),
    ("一百零五", 105),
    ("三千零五", 3005),
    ("两万", 20000),
    ("500", 500),
])
def test_parse_chinese_or_arabic_number_colloquial(input_str, expected):
    """P2-05: 民间口语省略尾随单位解析。"""
    assert parse_chinese_or_arabic_number(input_str) == expected
