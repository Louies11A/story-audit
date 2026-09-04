# -*- coding: utf-8 -*-
"""
tests/test_audit_bugs_and_enhancements.py: 针对 21 项缺陷与架构升级的 TDD 红绿回归测试集
"""

import ast
import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.types import ChapterItem, PatchSpec
from scripts.genre_detector import GenreProfile, detect_genre, _extract_text
import scripts.genre_detector as genre_mod
import scripts.story_audit as story_audit_mod
from scripts.format_scanner import scan_typography_flaws
import scripts.format_scanner as format_scanner_mod
import scripts.ai_patterns_checker as ai_checker_mod
from scripts.chapter_resolver import ChapterResolver
from scripts.safe_writer import (
    apply_patch_with_disambiguation,
    AmbiguousPatchError,
    PatchAnchorNotFoundError,
    _verify_anchor_before,
    _verify_anchor_after,
)
from scripts.author_memory import AuthorMemory, AntiInbreedingViolation, check_anti_inbreeding
from scripts.audit_state import AuditState, load_audit_state, save_audit_state
from scripts.ledger_engine import (
    AssetItem,
    LedgerState,
    save_ledger_state,
    sync_from_markdown,
    extract_heuristic_assets,
)
import scripts.ledger_engine as ledger_mod


# ==============================================================================
# 一、P0 致命级（3项）
# ==============================================================================

def test_p0_01_genre_detector_empty_registry_no_index_error():
    """[P0-01] GENRE_REGISTRY 为空或丢失时 detect_genre 不抛 IndexError，安全返回兜底 profile"""
    with patch.dict(genre_mod.GENRE_REGISTRY, {}, clear=True):
        profile = detect_genre("正文第一章：天地初开，万物复苏。")
        assert isinstance(profile, GenreProfile)
        assert profile.primary_genre == "传统玄幻"
        assert profile.confidence == 0.0


def test_p0_02_story_audit_exports_no_main():
    """[P0-02] story_audit.__all__ 不包含已废弃的 main 符号，且顶部无重复非法 __all__"""
    assert "main" not in story_audit_mod.__all__
    # 验证源代码中只存在一个最终的 __all__ 定义
    src = Path(story_audit_mod.__file__).read_text(encoding="utf-8")
    all_count = len(re.findall(r'^__all__\s*=', src, re.MULTILINE))
    assert all_count == 1, f"期望只有一个 __all__ 定义，但发现 {all_count} 个"


def test_p0_03_format_scanner_mismatched_lines_no_index_error():
    """[P0-03] scan_typography_flaws 接收行数不一致的 text 与 original_text 时不触发 IndexError"""
    text_multi = "第一行\n第二行\n第三行内容非常长" + "非常长" * 40
    orig_single = "只有一行原文字符串"
    # 以前在 masked_lines[2] 时 orig_lines 只有 1 行，触发 IndexError
    findings = scan_typography_flaws(text=text_multi, original_text=orig_single)
    assert isinstance(findings, list)


# ==============================================================================
# 二、P1 严重级（6项）
# ==============================================================================

def test_p1_01_run_scope_audit_deduplicates_open_defects(tmp_path):
    """[P1-01] run_scope_audit 多次运行相同章节，open_defects 不无限堆叠重复项"""
    project_dir = tmp_path / "novel_proj"
    project_dir.mkdir()
    (project_dir / "第001章.txt").write_text("第一章\n【获得：屠龙刀×1把】\n林冲拔出宝剑冲了上去。", encoding="utf-8")
    
    # 首次建账
    story_audit_mod.init_ledger(project_dir, silent=True)
    
    # 运行两次相同的 scope audit
    code1, rep1 = story_audit_mod.audit_scope(project_dir, scope_str="1-1", silent=True)
    state1 = load_audit_state(project_dir / "reports")
    count1 = len(state1.open_defects)
    
    code2, rep2 = story_audit_mod.audit_scope(project_dir, scope_str="1-1", silent=True)
    state2 = load_audit_state(project_dir / "reports")
    count2 = len(state2.open_defects)
    
    assert count2 == count1, f"重跑相同 scope 后 open_defects 重复堆叠: {count1} -> {count2}"


def test_p1_02_safe_writer_whitespace_anchor_no_false_positive():
    """[P1-02] 纯空白字符串前后锚点不触发 '' in prefix 假阳性消歧匹配"""
    lines = ["这是林冲拔剑的时刻。"]
    # old_text 是 '拔剑'，前置是 '这是林冲'，如果 cb 是纯空白 '   '，它不应该匹配成功
    # 因为原句中 '拔剑' 紧跟 '林冲'，前面根本没有空格
    assert _verify_anchor_before(lines, line_idx=0, start_pos=4, cb="   ") is False
    assert _verify_anchor_after(lines, line_idx=0, end_pos=6, ca="   ") is False


def test_p1_03_author_memory_anti_inbreeding_word_boundary():
    """[P1-03] 反近亲繁殖正则具备边界，不误杀 TOP3/STEP3，但精准拦截 P0/P3 等系统代号"""
    # 合法文本不应拦截
    check_anti_inbreeding("作者偏好：作品进入全站 TOP3 热度榜，按照 STEP3 流程逐步推进")
    
    # 真实系统告警应被拦截
    with pytest.raises(AntiInbreedingViolation):
        check_anti_inbreeding("发现致命错误 P0 阻断")
    with pytest.raises(AntiInbreedingViolation):
        check_anti_inbreeding("修复 P3 格式问题")
    with pytest.raises(AntiInbreedingViolation):
        check_anti_inbreeding("存在P1严重失误")


def test_p1_04_single_chapter_audit_updates_audit_state(tmp_path):
    """[P1-04] 单章审查 audit_chapter 成功后同步更新 audit_state 状态机"""
    project_dir = tmp_path / "novel_proj"
    project_dir.mkdir()
    (project_dir / "第001章.txt").write_text("第一章\n风雪山神庙。林冲提枪走出。", encoding="utf-8")
    
    story_audit_mod.init_ledger(project_dir, silent=True)
    code, rep = story_audit_mod.audit_chapter(project_dir, chapter_index=1, silent=True)
    
    state = load_audit_state(project_dir / "reports")
    assert 1.0 in state.completed_chapters or 1 in state.completed_chapters
    assert state.last_scope in ("1", "1-1", "001", "1.0")


def test_p1_05_single_chapter_audit_persists_new_foreshadowing(tmp_path):
    """[P1-05] 单章审查识别到的正文新伏笔持久化保存至 ledger.json 与 ledger.md"""
    project_dir = tmp_path / "novel_proj"
    project_dir.mkdir()
    chap_text = (
        "第一章\n"
        "林冲怀中藏着一块龙纹玉佩。<!-- audit:stash:伏笔:龙纹玉佩:第一章埋入待身世揭晓 -->\n"
        "大雪纷飞。"
    )
    (project_dir / "第001章.txt").write_text(chap_text, encoding="utf-8")
    
    story_audit_mod.init_ledger(project_dir, silent=True)
    code, rep = story_audit_mod.audit_chapter(project_dir, chapter_index=1, silent=True)
    
    # 检查账本 JSON 中是否存在该伏笔
    ledger_json, _ = story_audit_mod.locate_ledger_paths(project_dir)
    data = json.loads(ledger_json.read_text(encoding="utf-8"))
    stash = data.get("foreshadowing_stash", [])
    assert any("龙纹玉佩" in str(item) for item in stash), f"伏笔未持久化保存: {stash}"


def test_p1_06_chapter_resolver_sequence_gaps_allow_partial():
    """[P1-06] diagnose_sequence_gaps 在 allow_partial=True 时不误报首章缺失"""
    chapters = [
        ChapterItem(index=31.0, title="第31章", path=Path("031.txt"), raw_name="031.txt"),
        ChapterItem(index=32.0, title="第32章", path=Path("032.txt"), raw_name="032.txt"),
        ChapterItem(index=33.0, title="第33章", path=Path("033.txt"), raw_name="033.txt"),
    ]
    # 默认模式报错缺失 1-30
    default_gaps = ChapterResolver.diagnose_sequence_gaps(chapters)
    assert any("缺失" in g and ("1" in g or "30" in g) for g in default_gaps)
    
    # 局部切片模式不误报首章缺失
    partial_gaps = ChapterResolver.diagnose_sequence_gaps(chapters, allow_partial=True)
    assert len(partial_gaps) == 0


# ==============================================================================
# 三、P2 一般级与架构一致性（7项）
# ==============================================================================

def test_p2_01_story_audit_api_silent_and_no_cli_terms(tmp_path, capsys):
    """[P2-01] 真实验证所有 API 在 silent=True 下 0 字节泄漏，且报错文案无 CLI 标志"""
    src = Path(story_audit_mod.__file__).read_text(encoding="utf-8")
    assert "--sync-from-md" not in src
    assert "--checkpoint" not in src
    assert "--apply-fix" not in src

    # 准备测试小说工程
    project_dir = tmp_path / "silent_test_proj"
    project_dir.mkdir()
    (project_dir / "第001章.txt").write_text("第一章\n林冲拔剑上山。大雪纷飞。", encoding="utf-8")
    story_audit_mod.init_ledger(project_dir, silent=True)
    capsys.readouterr()  # 清空此前所有输出

    # 1. 测试 audit_scope 在 silent=True 时完全静默
    story_audit_mod.audit_scope(project_dir, scope_str="1-1", silent=True)
    out1, err1 = capsys.readouterr()
    assert out1 == "", f"audit_scope(silent=True) 泄露标准输出: {out1!r}"
    assert err1 == "", f"audit_scope(silent=True) 泄露错误输出: {err1!r}"

    # 2. 测试 checkpoint_volume 在 silent=True 时完全静默
    story_audit_mod.checkpoint_volume(project_dir, volume=1, silent=True)
    # 也测试非法参数时的报错静默
    story_audit_mod.checkpoint_volume(project_dir, volume=None, silent=True)
    out2, err2 = capsys.readouterr()
    assert out2 == "", f"checkpoint_volume(silent=True) 泄露输出: {out2!r}"
    assert err2 == "", f"checkpoint_volume(silent=True) 泄露错误: {err2!r}"

    # 3. 测试 sync_ledger_from_md 在 silent=True 时完全静默
    story_audit_mod.sync_ledger_from_md(project_dir, silent=True)
    out3, err3 = capsys.readouterr()
    assert out3 == "", f"sync_ledger_from_md(silent=True) 泄露输出: {out3!r}"
    assert err3 == "", f"sync_ledger_from_md(silent=True) 泄露错误: {err3!r}"

    # 4. 测试 apply_fix 在 silent=True 时完全静默 (即使修复失败)
    story_audit_mod.apply_fix(
        project_dir,
        chapter_index=1,
        target_line=1,
        old_text="不存在的文本",
        new_text="新文本",
        context_before="林冲",
        context_after="。",
        silent=True,
    )
    # 测试参数缺失报错时的静默
    story_audit_mod.apply_fix(project_dir, chapter_index=1, silent=True)
    out4, err4 = capsys.readouterr()
    assert out4 == "", f"apply_fix(silent=True) 泄露输出: {out4!r}"
    assert err4 == "", f"apply_fix(silent=True) 泄露错误: {err4!r}"

    # 5. 验证 Windows GBK 环境下 Emoji 打印不崩溃 (safe_console_print 编码容错)
    if hasattr(story_audit_mod, "safe_console_print"):
        import io
        # 模拟一个仅支持 gbk 编码的输出流
        gbk_stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        story_audit_mod.safe_console_print("🚀 开始批量审查 📊 汇总看板 🔴 P0阻断 🟡 P1警告 🟢 合格通过 ✅ 完成", file=gbk_stream)


def test_p2_02_ledger_engine_regex_precompiled():
    """[P2-02] extract_heuristic_assets 超大正则与关键词在模块顶层预编译"""
    assert hasattr(ledger_mod, "HEURISTIC_BRACKET_PATTERN") or hasattr(ledger_mod, "RE_BRACKET_ACQUISITION")
    assert hasattr(ledger_mod, "HEURISTIC_NATURAL_PATTERN") or hasattr(ledger_mod, "RE_NATURAL_ASSET")


def test_p2_03_genre_detector_extract_text_sampling():
    """[P2-03] _extract_text 增加超大文本采样截断（20000 字符）"""
    huge_text = "天地玄黄宇宙洪荒。" * 5000  # 45000 字符
    extracted = _extract_text(huge_text)
    assert len(extracted) <= 20000


def test_p2_04_sync_from_markdown_protects_cold_assets(tmp_path):
    """[P2-04] sync_from_markdown 防意外抹除冷资产 (CONSUMED / DAMAGED 等)"""
    md_file = tmp_path / "ledger.md"
    json_file = tmp_path / "ledger.json"
    
    state = LedgerState()
    # 活跃资产 A1
    state.assets["A1"] = AssetItem(id="A1", name="青龙剑", status="ACQUIRED", category="装备道具", quantity=1, unit="柄")
    # 冷资产 C1 已消耗
    state.assets["C1"] = AssetItem(id="C1", name="疗伤丹", status="CONSUMED", category="装备道具", quantity=0, unit="枚")
    save_ledger_state(state, json_file, md_file, force=True)
    
    # 模拟在 Markdown 中只有 A1，没有 C1 表格行（例如已消耗物品折叠或作者未在当前表格列出）
    md_text = (
        "# 资源账本（截至第 1.0 章）\n\n"
        "| 资产ID | 资产名称 | 类别 | 数量 | 单位 | 所有者 | 当前持有者 | 状态 | 初始章节 | 约束说明 |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| A1 | 青龙剑 | 装备道具 | 1 | 柄 | 主角 | 主角 | ACQUIRED | 1.0 | 无 |\n"
    )
    md_file.write_text(md_text, encoding="utf-8")
    
    new_state = sync_from_markdown(md_file, json_file)
    assert "A1" in new_state.assets
    # C1 作为冷资产不应被意外删除
    assert "C1" in new_state.assets, "冷资产在 Markdown 未列出时被意外抹除！"


def test_p2_05_author_memory_query_minimum_budget_protection(tmp_path):
    """[P2-05] AuthorMemory.query 增加最小预算保护，极小预算安全返回空字符串"""
    mem = AuthorMemory(tmp_path)
    mem.init()
    mem.record(key="文风", value="言简意赅", category="prose_style")
    
    # 预算仅 50 字节，无法完整呈现 Markdown 头部，应安全返回 "" 而不是半截切碎的文本
    result = mem.query(limit_bytes=50)
    assert result == ""


def test_p2_06_safe_writer_backup_to_project_root_reports(tmp_path):
    """[P2-06] safe_writer 默认备份目录回溯项目顶层 reports/.bak"""
    proj_root = tmp_path / "my_project"
    proj_root.mkdir()
    (proj_root / "reports").mkdir()
    chapter_dir = proj_root / "src" / "chapters" / "vol1"
    chapter_dir.mkdir(parents=True)
    target_file = chapter_dir / "001.txt"
    target_file.write_text("林冲拔剑。", encoding="utf-8")
    
    patch = PatchSpec(
        target_line=1,
        old_text="拔剑",
        new_text="提枪",
        context_before="林冲",
        context_after="。",
    )
    apply_patch_with_disambiguation(target_file, patch)
    
    # 检查备份目录在顶层 reports/.bak 下，而不是在 chapter_dir 下
    assert not (chapter_dir / "reports").exists()
    assert (proj_root / "reports" / ".bak").exists()


def test_p2_07_audit_state_and_author_memory_tempfile_cleanup_on_error(tmp_path):
    """[P2-07] audit_state 与 author_memory 在写入异常时通过 try...finally 清理临时文件"""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    state = AuditState()
    
    # 模拟 os.replace 抛异常
    with patch("os.replace", side_effect=OSError("Disk error")):
        with pytest.raises(OSError):
            save_audit_state(state, reports_dir)
            
    # 验证 reports_dir 中没有遗留临时文件
    leftovers = list(reports_dir.glob("tmp*"))
    assert len(leftovers) == 0, f"遗留了未清理的临时文件: {leftovers}"


# ==============================================================================
# 四、P3 优化与代码洁癖（5项）
# ==============================================================================

def test_p3_01_story_audit_no_duplicate_imports_at_top():
    """[P3-01] story_audit 顶部清理重复导入 (sys, Path)"""
    src = Path(story_audit_mod.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()[:30]
    sys_imports = [l for l in lines if "import sys" in l]
    path_imports = [l for l in lines if "from pathlib import Path" in l]
    assert len(sys_imports) <= 1, f"发现重复 import sys: {sys_imports}"
    assert len(path_imports) <= 1, f"发现重复 Path 导入: {path_imports}"


def test_p3_02_run_init_mode_returns_tuple_int_path(tmp_path):
    """[P3-02] run_init_mode 直接返回 Tuple[int, Path] 消除重复计算"""
    (tmp_path / "第001章.txt").write_text("第一章\n测试。", encoding="utf-8")
    res = story_audit_mod.run_init_mode(tmp_path, silent=True)
    assert isinstance(res, tuple)
    assert len(res) == 2
    assert isinstance(res[0], int)
    assert isinstance(res[1], Path)


def test_p3_03_format_scanner_and_ai_patterns_helper_regex_precompiled():
    """[P3-03] 辅助正则预编译常量存在"""
    assert hasattr(format_scanner_mod, "RE_NUM_OR_UNIT") or hasattr(format_scanner_mod, "RE_COMMA_SPLIT")
    assert hasattr(ai_checker_mod, "RE_NOT_IS_OR_ALSO") or hasattr(ai_checker_mod, "RE_AFFIRMATIVE_START")


def test_p3_04_runtime_and_genre_detector_clean_imports():
    """[P3-04] runtime_detector 与 genre_detector 无用导入已清理"""
    import scripts.runtime_detector as rt_mod
    import scripts.genre_detector as g_mod
    # 检查 runtime_detector 未导出/导入无用的 json
    assert not hasattr(rt_mod, "json")
    # 检查 genre_detector 未导入无用的 re
    assert "re" not in g_mod.__dict__


def test_p3_05_author_memory_profile_view_guidance(tmp_path):
    """[P3-05] 作者画像空视图指引更新为 Python API 语法"""
    mem = AuthorMemory(tmp_path)
    mem.init()
    profile_content = mem.render_profile_view()
    assert "AuthorMemory" in profile_content or "API" in profile_content
    assert "python scripts/author_memory.py record" not in profile_content

def test_allow_partial_end_to_end_chain(tmp_path):
    """[P2-API] 验证 allow_partial 在 audit_chapter、audit_scope 顶层完全贯通且自动推导"""
    project_dir = tmp_path / "partial_test_proj"
    project_dir.mkdir()
    # 仅存在 31, 32, 33 章
    (project_dir / "第031章.txt").write_text("第三十一章\n三十一章正文内容。", encoding="utf-8")
    (project_dir / "第032章.txt").write_text("第三十二章\n三十二章正文内容。", encoding="utf-8")
    (project_dir / "第033章.txt").write_text("第三十三章\n三十三章正文内容。", encoding="utf-8")
    story_audit_mod.init_ledger(project_dir, silent=True)

    # 1. 单章审查显式指定 allow_partial=True，报告中不应产生缺失 1-30 章的警告
    code, rep_path = story_audit_mod.audit_chapter(
        project_dir,
        chapter_index=31,
        allow_partial=True,
        silent=True,
    )
    rep_text = rep_path.read_text(encoding="utf-8")
    assert "缺失第 1 章" not in rep_text and "缺失章节" not in rep_text

    # 2. 批量审查 31-33 范围，未显式传参时应自动推导 allow_partial=True
    code_scope, rep_scope = story_audit_mod.audit_scope(
        project_dir,
        scope_str="31-33",
        silent=True,
    )
    scope_text = rep_scope.read_text(encoding="utf-8")
    assert "缺失第 1 章" not in scope_text and "缺失章节" not in scope_text


def test_p3_format_scanner_no_bare_han_char_regex():
    """[P3] format_scanner 中所有汉字匹配全部统一使用 RE_HAN_CHAR 预编译对象"""
    src = Path(format_scanner_mod.__file__).read_text(encoding="utf-8")
    assert "re.findall(r'[\\u4e00-\\u9fa5]'" not in src
    assert 're.findall(r"[\\u4e00-\\u9fa5]"' not in src
