"""
tests/test_chapter_linker.py
跨章缝合、POV 切换与视界隔离提取器单元测试
"""

import unittest
from typing import Dict, Any, List

from scripts.types import BoundaryContext
from scripts.chapter_linker import (
    POV_TRANSITION_KEYWORDS,
    _extract_head_scope,
    detect_pov_transition,
    detect_narrative_isolation_zones,
    extract_boundary_slices,
)


class TestDetectPovTransition(unittest.TestCase):
    """测试跨章多线视点/侧面烘托转场识别器"""

    def test_all_canonical_pov_keywords(self):
        """验证所有官方规范中列出的典型网文转场关键词"""
        canonical_keywords = [
            "与此同时",
            "同一时刻",
            "千里之外",
            "万里之外",
            "彼时",
            "此时此刻",
            "镜头一转",
            "视线拉远",
            "数千里外",
            "深宫之中",
            "大殿之内",
        ]
        for kw in canonical_keywords:
            text = f"    {kw}，暗流汹涌，杀机毕现。\n群雄汇聚，谁也不敢先出第一剑。"
            is_pov, clue = detect_pov_transition(text)
            self.assertTrue(is_pov, f"未能识别转场关键词: {kw}")
            self.assertEqual(clue, kw, f"转场线索不匹配: 期望 {kw}, 得到 {clue}")

    def test_pov_in_first_three_paragraphs(self):
        """验证转场词出现在前三段时能被成功捕获"""
        # 出现在第二段
        text_para2 = (
            "第一段简述背景。\n\n"
            "同一时刻，数千里外的雪原之上，风暴骤起。\n\n"
            "第三段写远方的动静。\n"
        )
        is_pov, clue = detect_pov_transition(text_para2)
        self.assertTrue(is_pov)
        self.assertIn(clue, ["同一时刻", "数千里外"])

        # 出现在第三段
        text_para3 = (
            "夜黑风高。\n\n"
            "狂风呼啸穿过峡谷。\n\n"
            "与此同时，黑石要塞内已是一片火海。\n"
        )
        is_pov, clue = detect_pov_transition(text_para3)
        self.assertTrue(is_pov)
        self.assertEqual(clue, "与此同时")

    def test_negative_standard_protagonist_lead(self):
        """负样本：常规主角视角破局，不得误报为 POV 转场"""
        text = (
            "林尘猛地睁开双眼，体内九阳真气奔涌不息。\n"
            "面对眼前呼啸而来的森白骨爪，他非但没有后退，反而一步踏出！\n"
            "\"雕虫小技，也敢班门弄斧！\""
        )
        is_pov, clue = detect_pov_transition(text)
        self.assertFalse(is_pov)
        self.assertIsNone(clue)

    def test_negative_partial_token_confusion(self):
        """负样本：词素混淆防御（如'彼此'不应误触发'彼时'）"""
        text = "两人彼此对视一眼，皆看出了对方眼中的惊异之色。"
        is_pov, clue = detect_pov_transition(text)
        self.assertFalse(is_pov)
        self.assertIsNone(clue)

    def test_negative_deep_in_chapter(self):
        """负样本：转场关键词出现在章节深处（超出前300字且超出前三段），不得认定为开篇转场"""
        padding = "\n\n".join([f"第{i}段平铺直叙剧情，字数填充。" * 5 for i in range(1, 10)])
        text = f"{padding}\n\n与此同时，远方天际闪过一道雷电。"
        is_pov, clue = detect_pov_transition(text)
        self.assertFalse(is_pov)
        self.assertIsNone(clue)

    def test_empty_or_whitespace_input(self):
        """空字符串或空白文本安全防护"""
        for empty_val in ["", "   \n\t  \n"]:
            is_pov, clue = detect_pov_transition(empty_val)
            self.assertFalse(is_pov)
            self.assertIsNone(clue)

    def test_extract_head_scope_internal_empty(self):
        """测试内部提取函数空值防御"""
        self.assertEqual(_extract_head_scope(""), "")


class TestDetectNarrativeIsolationZones(unittest.TestCase):
    """测试叙事视界隔离区间扫描器（回忆闪回与心魔幻境）"""

    def test_flashback_basic_paired(self):
        """测试基础回忆闪回成对标记检测与 1-based 行号定位"""
        lines = [
            "林尘立于悬崖边，山风凛冽。",                   # line 1
            "五年前那一战，血染苍穹，宗门死伤殆尽。",           # line 2 (Flashback 进入)
            "师尊为了掩护他，孤身一人断后。",                 # line 3
            "那一抹刺目的血色，至今烙印在心头。",               # line 4
            "林尘收回思绪，眼神重新变得凌厉起来。",             # line 5 (退出)
            "当务之急，是先解决眼前的叛徒。"                  # line 6
        ]
        text = "\n".join(lines)
        zones = detect_narrative_isolation_zones(text)
        self.assertEqual(len(zones), 1)
        z = zones[0]
        self.assertEqual(z["start_line"], 2)
        self.assertEqual(z["end_line"], 5)
        self.assertEqual(z["type"], "FLASHBACK")
        self.assertIn("五年前那一战", z["clue"])

    def test_flashback_time_and_memory_clue_variants(self):
        """测试多种时间与回忆型进入线索变体"""
        clues = [
            ("三年前，他尚是一个毫无修为的凡人。", "三年前"),
            ("十年前的那场大火，烧毁了他所有的温存。", "十年前"),
            ("恍惚间，他仿佛又看到了母亲慈祥的脸庞。", "恍惚间"),
            ("忆及往事，林尘心中不禁掠过一丝悲凉。", "忆及往事"),
            ("记忆如潮水般涌来，几乎将他的理智吞没。", "记忆如潮水般涌来"),
        ]
        for clue_text, expected_sub in clues:
            text = f"前置行\n{clue_text}\n回忆细节数行\n深吸一口气，回到现实\n后续正文"
            zones = detect_narrative_isolation_zones(text)
            self.assertEqual(len(zones), 1, f"未能捕获线索: {clue_text}")
            self.assertEqual(zones[0]["type"], "FLASHBACK")
            self.assertEqual(zones[0]["start_line"], 2)
            self.assertEqual(zones[0]["end_line"], 4)
            self.assertIn(expected_sub, zones[0]["clue"])

    def test_illusion_basic_paired(self):
        """测试心魔幻境进入与碎裂退出"""
        lines = [
            "踏入古殿的瞬间，黑雾扑面而来。",                 # line 1
            "眼前景象骤变，他陷入心魔幻境之中。",               # line 2 (Illusion 进入)
            "无数白骨怨魂伸出利爪，撕扯着他的神魂。",           # line 3
            "轰然巨响，幻境碎裂，化作漫天黑光消散。",           # line 4 (退出)
            "他身形踉跄，嘴角溢出一缕殷红。"                  # line 5
        ]
        text = "\n".join(lines)
        zones = detect_narrative_isolation_zones(text)
        self.assertEqual(len(zones), 1)
        z = zones[0]
        self.assertEqual(z["start_line"], 2)
        self.assertEqual(z["end_line"], 4)
        self.assertEqual(z["type"], "ILLUSION")
        self.assertIn("心魔幻境", z["clue"])

    def test_illusion_variants_and_exits(self):
        """测试心魔幻境变体线索与多种退出标记"""
        # 心魔丛生 -> 猛然惊醒
        text1 = "前奏\n心魔丛生，魔障缠身。\n幻觉不断。\n猛然惊醒，浑身冷汗。"
        z1 = detect_narrative_isolation_zones(text1)
        self.assertEqual(len(z1), 1)
        self.assertEqual(z1[0]["type"], "ILLUSION")
        self.assertEqual(z1[0]["start_line"], 2)
        self.assertEqual(z1[0]["end_line"], 4)

        # 陷入幻境 -> 回过神来
        text2 = "阵法发动。\n他猝不及防陷入幻境之中。\n幻象丛生。\n待回过神来，四周已是一片狼藉。"
        z2 = detect_narrative_isolation_zones(text2)
        self.assertEqual(len(z2), 1)
        self.assertEqual(z2[0]["type"], "ILLUSION")
        self.assertEqual(z2[0]["start_line"], 2)
        self.assertEqual(z2[0]["end_line"], 4)

    def test_same_line_entry_and_exit(self):
        """测试同一行内进入并立即退出的边界场景"""
        # 幻境同入同出
        text_ill = "眼前骤然黑化，他陷入心魔幻境，但眨眼间幻境碎裂，重见光明。"
        z_ill = detect_narrative_isolation_zones(text_ill)
        self.assertEqual(len(z_ill), 1)
        self.assertEqual(z_ill[0]["start_line"], 1)
        self.assertEqual(z_ill[0]["end_line"], 1)
        self.assertEqual(z_ill[0]["type"], "ILLUSION")

        # 闪同同入同出
        text_fb = "恍惚间，他猛然惊醒，原来只是南柯一梦。"
        z_fb = detect_narrative_isolation_zones(text_fb)
        self.assertEqual(len(z_fb), 1)
        self.assertEqual(z_fb[0]["start_line"], 1)
        self.assertEqual(z_fb[0]["end_line"], 1)
        self.assertEqual(z_fb[0]["type"], "FLASHBACK")

    def test_unclosed_isolation_zone_extends_to_eof(self):
        """若直到文末未退出，end_line 应等于文本总行数"""
        lines = [
            "第一行普通文本",
            "五年前那一战，彻底改变了这一切。",  # line 2 进入
            "第三行仍处于回忆中",
            "第四行章节戛然而止"
        ]
        text = "\n".join(lines)
        zones = detect_narrative_isolation_zones(text)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0]["start_line"], 2)
        self.assertEqual(zones[0]["end_line"], 4)
        self.assertEqual(zones[0]["type"], "FLASHBACK")

    def test_multiple_isolation_zones_in_single_chapter(self):
        """一章中包含多个不连续的视界隔离区间"""
        lines = [
            "第一段开局。",                                  # line 1
            "忆及往事，那座青峰依旧历历在目。",               # line 2 (Flashback 进入)
            "少年时的欢笑回荡在耳畔。",                      # line 3
            "收回思绪，他迈步向前。",                         # line 4 (Flashback 退出)
            "穿过幽暗森林，前方突现迷雾。",                   # line 5
            "煞气入体，陷入心魔幻境！",                       # line 6 (Illusion 进入)
            "恶鬼咆哮。",                                  # line 7
            "猛然惊醒，方知是虚惊一场。",                     # line 8 (Illusion 退出)
            "继续赶路。"                                    # line 9
        ]
        text = "\n".join(lines)
        zones = detect_narrative_isolation_zones(text)
        self.assertEqual(len(zones), 2)

        self.assertEqual(zones[0]["start_line"], 2)
        self.assertEqual(zones[0]["end_line"], 4)
        self.assertEqual(zones[0]["type"], "FLASHBACK")

        self.assertEqual(zones[1]["start_line"], 6)
        self.assertEqual(zones[1]["end_line"], 8)
        self.assertEqual(zones[1]["type"], "ILLUSION")

    def test_empty_and_clean_text(self):
        """空文本或普通无闪回文本返回空列表"""
        self.assertEqual(detect_narrative_isolation_zones(""), [])
        clean_text = "主角一路横推，直上云霄，斩杀敌手无数，威震八荒。"
        self.assertEqual(detect_narrative_isolation_zones(clean_text), [])


class TestExtractBoundarySlices(unittest.TestCase):
    """测试跨章接缝切片提取器与边界防御"""

    def test_first_chapter_none_prev_text_defense(self):
        """首章无上文边界防御：prev_text 为 None 时严禁抛异常，返回标准零状态"""
        curr_text = "天道初开，混沌未分。林尘自顽石中降生，吐纳天地至纯灵气。"
        ctx = extract_boundary_slices(prev_text=None, curr_text=curr_text)

        self.assertIsInstance(ctx, BoundaryContext)
        self.assertFalse(ctx.has_prev_chapter)
        self.assertEqual(ctx.prev_tail_300, "")
        self.assertEqual(ctx.curr_head_300, curr_text[:300])
        self.assertFalse(ctx.is_pov_transition)
        self.assertIsNone(ctx.transition_clue)
        self.assertEqual(ctx.isolation_zones, [])

    def test_first_chapter_empty_or_whitespace_prev_text(self):
        """首章防御：prev_text 为空串或纯空白时，视为无上文"""
        for empty_prev in ["", "   ", " \n\t\r\n "]:
            ctx = extract_boundary_slices(prev_text=empty_prev, curr_text="开篇正文内容。")
            self.assertFalse(ctx.has_prev_chapter)
            self.assertEqual(ctx.prev_tail_300, "")

    def test_slice_lengths_under_300_chars(self):
        """文本少于 300 字符时，完整保留有效字符并正确剥离首尾多余空白"""
        prev_text = "上一章末尾，战局胶着。\n  \n"
        curr_text = "\n\n   下一章开篇，杀伐再起。"

        ctx = extract_boundary_slices(prev_text=prev_text, curr_text=curr_text)
        self.assertTrue(ctx.has_prev_chapter)
        self.assertEqual(ctx.prev_tail_300, "上一章末尾，战局胶着。")
        self.assertEqual(ctx.curr_head_300, "下一章开篇，杀伐再起。")

    def test_slice_lengths_over_300_chars(self):
        """文本超 300 字符时，严格截取上章末尾 300 字与下章开头 300 字"""
        prev_chars = "".join([f"尾部第{i:03d}字" for i in range(100)])  # 100 * 6 = 600 字
        curr_chars = "".join([f"头部第{i:03d}字" for i in range(100)])  # 600 字

        ctx = extract_boundary_slices(prev_text=prev_chars, curr_text=curr_chars)
        self.assertTrue(ctx.has_prev_chapter)
        self.assertEqual(len(ctx.prev_tail_300), 300)
        self.assertEqual(len(ctx.curr_head_300), 300)
        self.assertEqual(ctx.prev_tail_300, prev_chars[-300:])
        self.assertEqual(ctx.curr_head_300, curr_chars[:300])

    def test_boundary_context_integration_with_pov_and_isolation(self):
        """综合集成测试：接缝提取联动 POV 转场识别与视界隔离区间提取"""
        prev_text = "林尘一剑荡平黑水盗，留下一地狼藉，扬长而去。"
        curr_text = (
            "与此同时，千里之外的皇城之内。\n"
            "大皇子正负手而立，神色阴鸷。\n"
            "五年前那一战的余波，至今尚未平息。\n"
            "深吸一口气，回到现实，他冷声吩咐：\"动手！\""
        )
        ctx = extract_boundary_slices(prev_text=prev_text, curr_text=curr_text)
        self.assertTrue(ctx.has_prev_chapter)
        self.assertEqual(ctx.prev_tail_300, prev_text)
        self.assertTrue(ctx.is_pov_transition)
        self.assertIn("与此同时", ctx.transition_clue)
        self.assertEqual(len(ctx.isolation_zones), 1)
        self.assertEqual(ctx.isolation_zones[0]["type"], "FLASHBACK")
        self.assertEqual(ctx.isolation_zones[0]["start_line"], 3)
        self.assertEqual(ctx.isolation_zones[0]["end_line"], 4)


if __name__ == "__main__":
    unittest.main()
