# -*- coding: utf-8 -*-
"""
tests/test_batch1_scanners_and_resolver.py:
针对 Batch 1 (文本扫描算法与章节解析器修复) 的专用单元测试集
"""

import pytest
from scripts.ai_patterns_checker import scan_ai_patterns, REVERSE_NOT_IS_PATTERN
from scripts.chapter_resolver import parse_chapter_index, parse_chapter_title
from scripts.format_scanner import scan_typography_flaws, _find_quote_intervals
from scripts.genre_detector import detect_genre, resolve_canonical_genre, _count_keyword_occurrences


# ==============================================================================
# 1. scripts/ai_patterns_checker.py 修复测试
# ==============================================================================

class TestAIPatternsCheckerFixes:
    def test_p0_01_snippet_slice_with_indentation(self):
        """[P0-01] 带有前置全角或半角缩进时，AI模式切片坐标不应左偏截断"""
        # 行首有2个全角空格，正文触发不是……而是……
        line = "　　那不是真的，而是虚假的幻影。"
        findings = scan_ai_patterns(line)
        assert len(findings) >= 1
        not_is_finding = next((f for f in findings if f.flaw_type == "AI_NOT_IS"), None)
        assert not_is_finding is not None
        # snippet 必须包含完整的“不是真的，而是虚假的幻影”或“并不是真的，而是虚假的幻影”，绝不能由于坐标左偏变成“是真的...”
        assert "不是真的" in not_is_finding.snippet or "并不是真的" in not_is_finding.snippet
        assert not_is_finding.snippet.startswith("不是") or not_is_finding.snippet.startswith("并不是")

    def test_p0_03_trailer_ending_at_head_of_tail_window_line(self):
        """[P0-03] 章末尾部窗口边缘行，行首的章末特征不应因 tail_start 偏移而漏检"""
        # 构造跨越尾部窗口阈值的文本
        filler = ("普通正文叙述段落。" * 20 + "\n") * 2  # 362 字符
        # trailer_line 跨过阈值 (总长约 777，阈值 582，cur 约 612，tail_start 落在行内或整行)
        filler2 = "普通正文叙述段落。" * 25 + "\n"  # 226 字符
        trailer_line = "殊不知暗中的危机早已降临。\n"
        tail_filler = "普通正文叙述段落。" * 20 + "\n"
        full_text = filler + filler2 + trailer_line + tail_filler

        findings = scan_ai_patterns(full_text)
        te_findings = [f for f in findings if f.flaw_type in ("AI_TRAILER_ENDING", "AI_TRAILER_SUMMARY")]
        assert len(te_findings) >= 1
        assert any("殊不知" in f.snippet for f in te_findings)

    def test_p2_01_reverse_not_is_pattern_lookbehind(self):
        """[P2-01] 反向否定正则不应将‘自’、‘光’、‘本’等单字误当否定前缀阻断"""
        # ‘自己是...不是...’应该被检出为 AI_NOT_IS
        text1 = "自己是剑修，而不是法修。"
        findings1 = scan_ai_patterns(text1)
        assert any(f.flaw_type == "AI_NOT_IS" for f in findings1)

        # ‘光是...不是...’应该被检出
        text2 = "光是灵石，而不是丹药。"
        findings2 = scan_ai_patterns(text2)
        assert any(f.flaw_type == "AI_NOT_IS" for f in findings2)

        # 真正的双字阻断词，如‘但是’、‘原本’应正常阻断
        text_blocked1 = "但是剑修，而不是法修。"
        assert not any(f.flaw_type == "AI_NOT_IS" for f in scan_ai_patterns(text_blocked1))

        text_blocked2 = "原本是灵石，而不是丹药。"
        assert not any(f.flaw_type == "AI_NOT_IS" for f in scan_ai_patterns(text_blocked2))

    def test_action_verb_comma_short_circuit(self):
        """动作清单：动词多但逗号少时不误触发，且能正常短路跳过"""
        text = "他伸手拿起长剑抽出剑刃握住剑柄指向前方。"
        findings = scan_ai_patterns(text)
        assert not any(f.flaw_type == "AI_GOD_VIEW_EXPOSITION" for f in findings)


# ==============================================================================
# 2. scripts/chapter_resolver.py 修复测试
# ==============================================================================

class TestChapterResolverFixes:
    def test_p0_02_prefix_label_no_greedy_swallow_juan(self):
        """[P0-02] ‘01 卷土重来.md’严禁将标题中的‘卷’误吞为分卷标记导致标题变‘土重来’"""
        idx = parse_chapter_index("01 卷土重来.md")
        title = parse_chapter_title("01 卷土重来.md")
        assert idx == 1.0
        assert title == "卷土重来"

        assert parse_chapter_index("01-卷土重来.md") == 1.0
        assert parse_chapter_title("01-卷土重来.md") == "卷土重来"

    def test_p2_05_floating_point_chapter_number(self):
        """[P2-05] 支持浮点章号，如 3.1 破局.md 解析为 3.1 和‘破局’"""
        idx = parse_chapter_index("3.1 破局.md")
        title = parse_chapter_title("3.1 破局.md")
        assert idx == 3.1
        assert title == "破局"

        idx2 = parse_chapter_index("03.2 逆转乾坤.txt")
        title2 = parse_chapter_title("03.2 逆转乾坤.txt")
        assert idx2 == 3.2
        assert title2 == "逆转乾坤"


# ==============================================================================
# 3. scripts/format_scanner.py 修复测试
# ==============================================================================

class TestFormatScannerFixes:
    def test_p2_03_corner_brackets_quotes_support(self):
        """[P2-03] _find_quote_intervals 支持中文直角引号「」与『』"""
        line = "他低声道：「毫无疑问，我们必须赢。」随后拔剑。"
        intervals = _find_quote_intervals(line, closed_only=True)
        assert len(intervals) == 1
        assert line[intervals[0][0]:intervals[0][1]] == "「毫无疑问，我们必须赢。」"

        line2 = "『总而言之，绝不能退。』"
        intervals2 = _find_quote_intervals(line2, closed_only=True)
        assert len(intervals2) == 1
        assert line2[intervals2[0][0]:intervals2[0][1]] == "『总而言之，绝不能退。』"

    def test_p1_02_dialogue_quote_masks_ai_conjunction(self):
        """[P1-02] 连词扫描台词掩码：人物台词引号内的连词不报 AI_CONJUNCTION"""
        text = (
            "第五行正文正常叙述。\n"
            "第六行正文正常叙述。\n"
            "第七行正文正常叙述。\n"
            "他冷笑道：“毫无疑问，这是个陷阱。”\n"
            "师妹接着说：「总而言之，我们必须撤退。」\n"
            "掌门点头道：『不可否认的是，对手太强了。』"
        )
        flaws = scan_typography_flaws(text)
        ai_flaws = [f for f in flaws if f.flaw_type == "AI_CONJUNCTION"]
        assert len(ai_flaws) == 0

    def test_p1_01_chapter_opening_transition_conjunction_exempt(self):
        """[P1-01] 章节开篇前 3 行段首转场‘与此同时，’不报 AI_CONJUNCTION"""
        # 第 1 行作为段首转场
        text1 = "与此同时，城外的妖兽大军已经兵临城下。\n少年缓缓拔出长剑。"
        flaws1 = scan_typography_flaws(text1)
        assert not any(f.flaw_type == "AI_CONJUNCTION" and "与此同时" in f.snippet for f in flaws1)

        # 第 3 行作为段首转场
        text3 = (
            "天地昏暗，狂风呼啸。\n"
            "守军严阵以待。\n"
            "与此同时，另一边的战场也陷入了胶着。\n"
            "战斗正式爆发。"
        )
        flaws3 = scan_typography_flaws(text3)
        assert not any(f.flaw_type == "AI_CONJUNCTION" and "与此同时" in f.snippet for f in flaws3)

        # 第 4 行（非前3行）的段首转场连词仍应被报出
        text4 = (
            "第一行正文叙述。\n"
            "第二行正文叙述。\n"
            "第三行正文叙述。\n"
            "与此同时，城外的局势发生了突变。"
        )
        flaws4 = scan_typography_flaws(text4)
        assert any(f.flaw_type == "AI_CONJUNCTION" and "与此同时" in f.snippet for f in flaws4)

        # 句中（非段首）的‘与此同时’仍应被报出
        text_mid = "战斗十分激烈，与此同时，敌人派出了援军。"
        flaws_mid = scan_typography_flaws(text_mid)
        assert any(f.flaw_type == "AI_CONJUNCTION" and "与此同时" in f.snippet for f in flaws_mid)


# ==============================================================================
# 4. scripts/genre_detector.py 修复测试
# ==============================================================================

class TestGenreDetectorFixes:
    def test_p2_04_ascii_word_boundary_no_through_match_hr(self):
        """[P2-04] 对纯 ASCII 英文单词使用 \b 边界，避免 through 误判为职场婚恋的 HR"""
        english_text = (
            "He walked through the door and saw three men. "
            "The threat was growing through the entire facility. "
            "They pushed through the crowd with great effort."
        )
        profile = detect_genre(english_text)
        assert profile.primary_genre != "职场婚恋"
        assert "HR" not in profile.keywords_matched

        cnt = _count_keyword_occurrences(english_text.lower(), "HR")
        assert cnt == 0

        cnt_real = _count_keyword_occurrences("Our company HR sent an email.".lower(), "HR")
        assert cnt_real == 1

    def test_p3_06_wuxia_aliases_resolution(self):
        """[P3-06] 将‘武侠’、‘传统武侠’、‘新武侠’映射到相应标准分类（东方仙侠），不返回 None"""
        assert resolve_canonical_genre("武侠") == "东方仙侠"
        assert resolve_canonical_genre("传统武侠") == "东方仙侠"
        assert resolve_canonical_genre("新武侠") == "东方仙侠"
