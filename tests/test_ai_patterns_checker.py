# -*- coding: utf-8 -*-
"""
tests.test_ai_patterns_checker: AI 模式与套路句式深度扫描器测试套件
"""

import unittest
from scripts.ai_patterns_checker import (
    mask_quotes_in_line,
    mask_quotes_in_text,
    scan_ai_patterns,
)


class TestAIPatternsChecker(unittest.TestCase):
    """测试 AI 模式与套路句式检测"""

    def test_mask_quotes_in_line(self):
        """测试单行引号掩码不跨行且长度不变"""
        line = '林峰冷笑道：“这不是你该管的事。”随后转身离去。'
        masked = mask_quotes_in_line(line)
        self.assertEqual(len(masked), len(line))
        self.assertNotIn("这不是你该管的事", masked)
        self.assertIn("林峰冷笑道：", masked)
        self.assertIn("随后转身离去。", masked)

    def test_not_is_comparison(self):
        """测试典型 not-is 对仗句式识别与排除规则"""
        # 1. 纯叙述 not-is 触发 P2
        text_hit = "他不是在逃跑，而是在诱敌深入，将黑旗帮引入雷区。"
        findings = scan_ai_patterns(text_hit)
        not_is_flaws = [f for f in findings if f.flaw_type == "AI_NOT_IS"]
        self.assertEqual(len(not_is_flaws), 1)
        self.assertEqual(not_is_flaws[0].severity, "P2")

        # 2. 对话内 not-is 受引号掩码保护，不误判
        text_dialogue = '王龙飞摇头道：“这不是撤退，而是战术转进，大家不要慌！”'
        findings_diag = scan_ai_patterns(text_dialogue)
        self.assertEqual(len([f for f in findings_diag if f.flaw_type == "AI_NOT_IS"]), 0)

        # 3. 排除 either-or 连词短语（不是A就是B）
        text_either = "在这片末世废土上，不是你死就是我亡。"
        findings_either = scan_ai_patterns(text_either)
        self.assertEqual(len([f for f in findings_either if f.flaw_type == "AI_NOT_IS"]), 0)

        # 4. 排除反问语气（不是吗）
        text_tag = "当年也是你亲口答应的，不是吗？"
        findings_tag = scan_ai_patterns(text_tag)
        self.assertEqual(len([f for f in findings_tag if f.flaw_type == "AI_NOT_IS"]), 0)

        # 5. 反序对比（是A，不是B）触发 P2
        text_rev = "这柄战刃是精钢锻造，而不是普通的铁条拼凑。"
        findings_rev = scan_ai_patterns(text_rev)
        rev_flaws = [f for f in findings_rev if f.flaw_type == "AI_NOT_IS"]
        self.assertEqual(len(rev_flaws), 1)

    def test_em_dash_detection(self):
        """测试正文中残留破折号硬停顿检出"""
        text = "他正欲扣动扳机——远处突然传来一声刺耳的警报。"
        findings = scan_ai_patterns(text)
        em_flaws = [f for f in findings if f.flaw_type == "AI_EM_DASH"]
        self.assertEqual(len(em_flaws), 1)
        self.assertEqual(em_flaws[0].severity, "P2")

    def test_voice_contrast(self):
        """测试声音/语气反差句式识别"""
        text = "王总师声音不大，却清晰传入了指挥舱每个人的耳中。"
        findings = scan_ai_patterns(text)
        vc_flaws = [f for f in findings if f.flaw_type == "AI_VOICE_CONTRAST"]
        self.assertEqual(len(vc_flaws), 1)
        self.assertEqual(vc_flaws[0].severity, "P2")

        text2 = "沈飞语气平淡，却让在场的所有海盗心中一凛。"
        findings2 = scan_ai_patterns(text2)
        vc2_flaws = [f for f in findings2 if f.flaw_type == "AI_VOICE_CONTRAST"]
        self.assertEqual(len(vc2_flaws), 1)

    def test_negation_parade(self):
        """测试连续否定排比与先否定后肯定模板"""
        # 没有X，没有Y……
        text1 = "甲板上一片死寂，没有伴奏，没有和声，没有提词器。"
        findings1 = scan_ai_patterns(text1)
        np1 = [f for f in findings1 if f.flaw_type == "AI_NEGATION_PARADE"]
        self.assertEqual(len(np1), 1)

        # 没X，没Y……只是Z
        text2 = "他没炫技，没有华丽动作，只是沉稳地扣下扳机。"
        findings2 = scan_ai_patterns(text2)
        np2 = [f for f in findings2 if f.flaw_type == "AI_NEGATION_PARADE"]
        self.assertEqual(len(np2), 1)

    def test_trailer_ending_and_summary_in_tail_window(self):
        """测试章末窗口预告式收尾与状态总结体"""
        # 构造超过 600 字文本并在末尾注入章末总结句
        long_body = "正文叙事段落，战士们正在打扫战场，检视损管工况。\n" * 20
        tail_text = (
            long_body +
            "他走上舰桥远眺大海。\n"
            "这一夜注定无人入眠。\n"
            "殊不知一场更大的危机才刚刚开始。\n"
        )
        findings = scan_ai_patterns(tail_text)
        ts_flaws = [f for f in findings if f.flaw_type == "AI_TRAILER_SUMMARY"]
        te_flaws = [f for f in findings if f.flaw_type == "AI_TRAILER_ENDING"]
        self.assertTrue(len(ts_flaws) >= 1, "应检出章末状态总结体")
        self.assertTrue(len(te_flaws) >= 1, "应检出章末预告收尾")

        # 若出现在文章开头或中段（远离尾部窗口），不应误判为章末总结
        mid_text = (
            "谁也不知道明日天气如何，大家各自安歇。\n" +
            ("正常章节推进剧情，波澜不惊。\n" * 30)
        )
        mid_findings = scan_ai_patterns(mid_text)
        self.assertEqual(len([f for f in mid_findings if f.flaw_type in ("AI_TRAILER_SUMMARY", "AI_TRAILER_ENDING")]), 0)

    def test_god_view_exposition_and_action_list(self):
        """测试监控摄像头式纯动作清单与上帝解释腔"""
        # 1. 监控摄像头式纯动作清单 (>=5动词 + >=4逗号)
        action_line = "他伸手拉开抽屉，拿起钥匙，走到门边，推开铁门，转身看向漆黑的长廊。"
        findings_act = scan_ai_patterns(action_line)
        gv_act = [f for f in findings_act if f.flaw_type == "AI_GOD_VIEW_EXPOSITION"]
        self.assertEqual(len(gv_act), 1)

        # 2. 上帝解释腔
        god_line = "他很清楚，这意味着整个避难所的防线即将全线崩溃。"
        findings_god = scan_ai_patterns(god_line)
        gv_god = [f for f in findings_god if f.flaw_type == "AI_GOD_VIEW_EXPOSITION"]
        self.assertEqual(len(gv_god), 1)


if __name__ == "__main__":
    unittest.main()
