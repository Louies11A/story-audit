"""
tests.test_format_scanner: 排版白名单掩码与格式扫描器测试套件

涵盖：
1. 白名单掩码行数绝对保持契约（newline 计数严格相等，绝不漂移）；
2. 系统面板、古诗口诀、Markdown 引用的准确识别与置空；
3. 超长段落（>=120字）P2 扫描与激昂独白（感叹号>=3）降级 P3 边界验证；
4. 拖沓长句（逗号>=4 或无标点连续分句>=45字）P2 扫描边界验证；
5. 对话台词后紧塞长描写（>=80字）P3 扫描与英文引号兼容性；
6. 典型 AI 翻译腔高频连词（然而、与此同时、不由得等）P3 扫描；
7. 掩码区域免检（白名单块内不误报任何格式缺陷）；
8. 契约属性验证（1-based 行号升序、snippet<=60字、字段完整性）；
9. 双参数调用（text 与 original_text）兼容性；
10. CRLF 与空行边界情况；
11. 单行复合缺陷同时检出能力。
"""

import unittest
from typing import List

from scripts.types import FormatFinding
from scripts.format_scanner import (
    mask_special_blocks,
    scan_typography_flaws,
)


class TestFormatScanner(unittest.TestCase):
    """排版白名单掩码与格式扫描器测试套件"""

    def test_mask_special_blocks_newline_count_invariant(self):
        """铁律验证：掩码处理后换行符数量必须与原文本绝对相等，严禁删除物理行"""
        sample_text = (
            "第一章 初入仙门\n"
            "\n"
            "【系统流面板】\n"
            "【宿主：方源】\n"
            "【境界：炼气一层】\n"
            "【力量：12，敏捷：15】\n"
            "【寿元：80年，技能点：0】\n"
            "\n"
            "方源看着眼前的淡蓝色光幕，微微颔首。\n"
            "> 这是宗门秘录中的第一条训诫：\n"
            "> 凡我青云门弟子，当斩妖除魔，护佑苍生。\n"
            "\n"
            "山风吹过竹林，传来阵阵沙沙声。\n"
            "白日依山尽，黄河入海流。\n"
            "欲穷千里目，更上一层楼。\n"
            "\n"
            "这正是千百年来流传的真言。"
        )
        masked_text, masks_info = mask_special_blocks(sample_text)

        # 换行符数量必须绝对相等
        self.assertEqual(
            masked_text.count("\n"),
            sample_text.count("\n"),
            "掩码后换行符总数必须与原文本严格一致，防止行号漂移！",
        )
        # 总物理行数必须严格一致
        self.assertEqual(len(masked_text.split("\n")), len(sample_text.split("\n")))

        # 掩码信息必须捕获到了对应的区块
        self.assertTrue(len(masks_info) >= 3)
        block_types = [m["type"] for m in masks_info]
        self.assertIn("system_panel", block_types)
        self.assertIn("quote", block_types)
        self.assertIn("poem", block_types)

    def test_mask_special_blocks_crlf_preservation(self):
        """测试 CRLF 换行符文本掩码后换行数量与格式保持"""
        sample_crlf = (
            "第一行普通文本。\r\n"
            "【系统提示：宿主力量+10】\r\n"
            "> 引用内容第一行\r\n"
            "> 引用内容第二行\r\n"
            "正文结束。"
        )
        masked, masks = mask_special_blocks(sample_crlf)
        self.assertEqual(masked.count("\n"), sample_crlf.count("\n"))
        self.assertEqual(len(masks), 2)
        # 验证包含 \r\n 结构正常
        self.assertIn("\r\n", masked)

    def test_mask_system_panels(self):
        """测试系统流面板掩码识别（【、[、| 开头且包含核心关键词）"""
        text_with_panels = (
            "正文开始前的内容。\n"
            "【系统已激活】\n"
            "【宿主：萧炎】\n"
            "【体质：焚诀火体】\n"
            "【力量：250，敏捷：180】\n"
            "【法力：9999/9999】\n"
            "正文中段叙述。\n"
            "| 属性 | 基础数值 | 强化加成 |\n"
            "| 力量 | 100     | +20     |\n"
            "| 生命 | 1500    | +300    |\n"
            "正文后段。\n"
            "[系统提示：宿主消耗技能点成功升级技能]"
        )
        masked, masks = mask_special_blocks(text_with_panels)
        self.assertEqual(masked.count("\n"), text_with_panels.count("\n"))

        # 验证被掩码的行在 masked 中变成了空白行
        masked_lines = masked.split("\n")
        # 第2行到第6行（0-based 为 1 到 5）是系统面板
        for idx in range(1, 6):
            self.assertEqual(masked_lines[idx], "", f"第 {idx+1} 行系统面板应被清空")

        # 验证表格行（0-based 为 7 到 9）被清空
        for idx in range(7, 10):
            self.assertEqual(masked_lines[idx], "", f"第 {idx+1} 行表格面板应被清空")

        # 验证单行 [系统提示...]（0-based 为 11）被清空
        self.assertEqual(masked_lines[11], "")

        # 验证普通文本保留
        self.assertEqual(masked_lines[0], "正文开始前的内容。")
        self.assertEqual(masked_lines[6], "正文中段叙述。")
        self.assertEqual(masked_lines[10], "正文后段。")

    def test_mask_multiline_bracket_panel(self):
        """测试跨多行未闭合黑括号系统面板的完整掩码"""
        text = (
            "序言部分。\n"
            "【\n"
            "人物属性总览\n"
            "宿主：石昊\n"
            "境界：化灵境\n"
            "骨文宝术：原始真解\n"
            "】\n"
            "正文继续展开。"
        )
        masked, masks = mask_special_blocks(text)
        self.assertEqual(masked.count("\n"), text.count("\n"))
        self.assertEqual(len(masks), 1)
        self.assertEqual(masks[0]["type"], "system_panel")
        self.assertEqual(masks[0]["start_line"], 2)
        self.assertEqual(masks[0]["end_line"], 7)

    def test_non_panel_brackets_not_masked(self):
        """测试非系统面板的普通括号或标题不被误掩码"""
        text = (
            "【第一卷 少年行】\n"
            "这是第一卷的开场描写，讲述了一个少年的故事。\n"
            "[注：此处为历史虚构背景]\n"
            "后续正常叙述。"
        )
        masked, masks = mask_special_blocks(text)
        self.assertEqual(len(masks), 0)
        self.assertEqual(masked, text)

    def test_mask_poems_and_formulas(self):
        """测试四六言排比、古诗词对仗块掩码"""
        text_with_poems = (
            "老者手捻胡须，低声念诵着宗门无上心法：\n"
            "天地玄黄，宇宙洪荒。\n"
            "日月盈昃，辰宿列张。\n"
            "寒来暑往，秋收冬藏。\n"
            "诵毕，他目光深邃地望向远方。"
        )
        masked, masks = mask_special_blocks(text_with_poems)
        self.assertEqual(masked.count("\n"), text_with_poems.count("\n"))

        poem_masks = [m for m in masks if m["type"] == "poem"]
        self.assertTrue(len(poem_masks) > 0)
        # 诗词对应行号在 masked 中应为空
        masked_lines = masked.split("\n")
        self.assertEqual(masked_lines[1], "")
        self.assertEqual(masked_lines[2], "")
        self.assertEqual(masked_lines[3], "")
        # 非诗词行保留
        self.assertIn("老者手捻胡须", masked_lines[0])
        self.assertIn("诵毕", masked_lines[4])

    def test_mask_markdown_quotes(self):
        """测试 Markdown 引用块（> 开头）掩码"""
        text_with_quotes = (
            "密函上的字迹清晰可见：\n"
            "> 见字如面。\n"
            "> 边关军情紧急，敌军已渡黑水河，望速派援军。\n"
            "> 镇西将军李陵顿首。\n"
            "看完书信，他眉头拧成了一个川字。"
        )
        masked, masks = mask_special_blocks(text_with_quotes)
        self.assertEqual(masked.count("\n"), text_with_quotes.count("\n"))
        quote_masks = [m for m in masks if m["type"] == "quote"]
        self.assertEqual(len(quote_masks), 1)
        self.assertEqual(quote_masks[0]["start_line"], 2)
        self.assertEqual(quote_masks[0]["end_line"], 4)

    def test_scan_long_paragraph_p2(self):
        """测试单段连续字数 >= 120 字触发 LONG_PARAGRAPH (P2)"""
        # 128 个字的长段落（无大量感叹号）
        long_para = (
            "夜幕沉沉笼罩着整座孤山，狂风裹挟着枯枝败叶在半空中凄厉地呼啸旋转，"
            "远处依稀传来几声不知名妖兽的低沉嘶吼，令人听了不由得心头发紧手脚冰凉，"
            "张小凡独自一人在这伸手不见五指的漆黑密林深处摸索前行，脚下湿滑的青苔与"
            "尖锐的碎石不断考验着他的耐心与意志，每前进一步都要付出巨大的力气，"
            "但他根本不敢停下来休息哪怕片刻。"
        )
        text = f"普通短段落。\n{long_para}\n另一段正常文本。"
        flaws = scan_typography_flaws(text)

        long_para_flaws = [f for f in flaws if f.flaw_type == "LONG_PARAGRAPH"]
        self.assertTrue(len(long_para_flaws) >= 1)
        target_flaw = long_para_flaws[0]
        self.assertEqual(target_flaw.line_number, 2)
        self.assertEqual(target_flaw.severity, "P2")
        self.assertTrue(len(target_flaw.snippet) <= 60)
        self.assertIn("120", target_flaw.message)

    def test_scan_long_paragraph_boundary(self):
        """测试长段落边界：119 字不触发，120 字刚好触发"""
        # 刚好 119 字
        p119 = "一" * 119
        flaws_119 = scan_typography_flaws(p119)
        self.assertEqual(len([f for f in flaws_119 if f.flaw_type == "LONG_PARAGRAPH"]), 0)

        # 刚好 120 字
        p120 = "一" * 120
        flaws_120 = scan_typography_flaws(p120)
        p120_flaws = [f for f in flaws_120 if f.flaw_type == "LONG_PARAGRAPH"]
        self.assertEqual(len(p120_flaws), 1)
        self.assertEqual(p120_flaws[0].severity, "P2")

    def test_scan_long_paragraph_emotional_downgrade_to_p3(self):
        """测试独白情绪降级：单段 >= 120 字且包含 >= 3 个感叹号降级为 P3"""
        # 122 字，包含 4 个感叹号（激昂情绪独白）
        emotional_para = (
            "凭什么！我林动苦修十载寒暑，日夜与妖兽搏杀，凭什么所有资源都要拱手让给那个一无所长的大少爷！"
            "天道不公，宗门更是不公！今日你们将我逼上绝路，来日我若脱困，定要让这九峰十三脉为今日的偏袒"
            "付出万劫不复的惨痛代价，哪怕为此拼得粉身碎骨魂飞魄散，我也绝对在所不惜！"
        )
        text = f"{emotional_para}\n正常简短后续。"
        flaws = scan_typography_flaws(text)

        long_para_flaws = [f for f in flaws if f.flaw_type == "LONG_PARAGRAPH"]
        self.assertEqual(len(long_para_flaws), 1)
        self.assertEqual(long_para_flaws[0].severity, "P3")
        self.assertEqual(long_para_flaws[0].line_number, 1)
        self.assertIn("独白", long_para_flaws[0].message)

    def test_scan_long_paragraph_exclamation_boundary(self):
        """测试情绪独白感叹号边界：2个感叹号为 P2，3个感叹号（全角半角混杂）降级为 P3"""
        base = "天地玄黄" * 30  # 120 字
        para_2_excl = base + "！！"
        para_3_excl = base + "！!！"

        flaws_2 = scan_typography_flaws(para_2_excl)
        flaw_2 = [f for f in flaws_2 if f.flaw_type == "LONG_PARAGRAPH"][0]
        self.assertEqual(flaw_2.severity, "P2")

        flaws_3 = scan_typography_flaws(para_3_excl)
        flaw_3 = [f for f in flaws_3 if f.flaw_type == "LONG_PARAGRAPH"][0]
        self.assertEqual(flaw_3.severity, "P3")

    def test_scan_dragging_sentence_too_many_commas(self):
        """测试长拖沓句：单个完整句子内逗号 >= 4 个触发 DRAGGING_SENTENCE (P2)"""
        # 句子内有 4 个逗号
        dragging_sentence = "他缓缓拔出腰间长剑，剑锋在寒月下泛着冰冷光华，脚步随之微微挪动，周身真气骤然凝聚，随即猛然向前刺出一击。"
        text = f"前置段落。\n{dragging_sentence}\n后置段落。"
        flaws = scan_typography_flaws(text)

        drag_flaws = [f for f in flaws if f.flaw_type == "DRAGGING_SENTENCE"]
        self.assertTrue(len(drag_flaws) >= 1)
        self.assertEqual(drag_flaws[0].line_number, 2)
        self.assertEqual(drag_flaws[0].severity, "P2")
        self.assertIn("逗号", drag_flaws[0].message)
        self.assertTrue(len(drag_flaws[0].snippet) <= 60)

    def test_scan_dragging_sentence_comma_boundary(self):
        """测试逗号阈值：3个逗号不触发，4个逗号（包含英文逗号）触发 P2"""
        sent_3_commas = "他走了一步，停下来看天，又揉了揉眼睛，转身离开宗门大殿。"
        flaws_3 = scan_typography_flaws(sent_3_commas)
        self.assertEqual(len([f for f in flaws_3 if f.flaw_type == "DRAGGING_SENTENCE"]), 0)

        # 3 个中文逗号 + 1 个英文逗号 = 4 个逗号
        sent_4_commas = "他走了一步，停下来看天, 又揉了揉眼睛，叹了一口气，转身离开宗门大殿。"
        flaws_4 = scan_typography_flaws(sent_4_commas)
        drag_flaws = [f for f in flaws_4 if f.flaw_type == "DRAGGING_SENTENCE"]
        self.assertEqual(len(drag_flaws), 1)

    def test_scan_dragging_sentence_breathless_chunk(self):
        """测试长拖沓句：单个无标点分句连续字数 >= 45 字触发 DRAGGING_SENTENCE (P2)"""
        # 48 个汉字连续无任何标点
        breathless_chunk = "他一口气运转起体内那股澎湃汹涌浩瀚无边的纯阳真气直接彻底冲破了堵塞数十载的周身奇经八脉所有的顽固关隘"
        sentence = f"只见{breathless_chunk}，整个人瞬间容光焕发。"
        text = f"{sentence}"
        flaws = scan_typography_flaws(text)

        drag_flaws = [f for f in flaws if f.flaw_type == "DRAGGING_SENTENCE"]
        self.assertTrue(len(drag_flaws) >= 1)
        self.assertEqual(drag_flaws[0].severity, "P2")
        self.assertTrue(len(drag_flaws[0].snippet) <= 60)

    def test_scan_dragging_sentence_breathless_boundary(self):
        """测试无标点分句边界：44字不报，45字报 P2"""
        chunk_44 = "字" * 44
        sent_44 = f"前置，{chunk_44}。后置。"
        flaws_44 = scan_typography_flaws(sent_44)
        self.assertEqual(len([f for f in flaws_44 if f.flaw_type == "DRAGGING_SENTENCE"]), 0)

        chunk_45 = "字" * 45
        sent_45 = f"前置，{chunk_45}。后置。"
        flaws_45 = scan_typography_flaws(sent_45)
        self.assertEqual(len([f for f in flaws_45 if f.flaw_type == "DRAGGING_SENTENCE"]), 1)

    def test_scan_dialogue_mixed_p3(self):
        """测试对话混排：在台词引号后紧塞 >= 80 字描写不分行触发 DIALOGUE_MIXED (P3)"""
        # 85 字描写紧跟闭引号
        dialogue_mixed = (
            '“这次拍卖会我们必须拿下那枚筑基丹。”'
            '他转过身去深深吸了一口冰凉的空气，目光不由自主地落在了窗外熙熙攘攘的街道上，'
            '心中反复思量着家族如今所面临的巨大危机，倘若此次不能成功拍下灵药助老祖突破关隘，'
            '恐怕整个林家都会在三个月后的四大家族排位战中被彻底吞并抹去。'
        )
        text = f"开场。\n{dialogue_mixed}\n结尾。"
        flaws = scan_typography_flaws(text)

        mixed_flaws = [f for f in flaws if f.flaw_type == "DIALOGUE_MIXED"]
        self.assertTrue(len(mixed_flaws) >= 1)
        self.assertEqual(mixed_flaws[0].line_number, 2)
        self.assertEqual(mixed_flaws[0].severity, "P3")
        self.assertTrue(len(mixed_flaws[0].snippet) <= 60)
        self.assertIn("台词", mixed_flaws[0].message)

    def test_scan_dialogue_mixed_boundary(self):
        """测试对话混排字数边界：79字不报，80字报 P3"""
        d79 = '“出发！”' + ('测' * 79)
        flaws_79 = scan_typography_flaws(d79)
        self.assertEqual(len([f for f in flaws_79 if f.flaw_type == "DIALOGUE_MIXED"]), 0)

        d80 = '“出发！”' + ('测' * 80)
        flaws_80 = scan_typography_flaws(d80)
        self.assertEqual(len([f for f in flaws_80 if f.flaw_type == "DIALOGUE_MIXED"]), 1)

    def test_scan_dialogue_clean_not_mixed(self):
        """测试短动作描写的对话不触发 DIALOGUE_MIXED"""
        clean_dialogue = '“这次拍卖会我们必须拿下那枚筑基丹。”他转过身冷冷地说道。'
        text = f"{clean_dialogue}"
        flaws = scan_typography_flaws(text)
        mixed_flaws = [f for f in flaws if f.flaw_type == "DIALOGUE_MIXED"]
        self.assertEqual(len(mixed_flaws), 0)

    def test_scan_dialogue_english_quotes(self):
        """测试英文双引号包裹的对话混排亦可被检出"""
        en_dialogue_mixed = '"We must win this battle." ' + ('描' * 82)
        flaws = scan_typography_flaws(en_dialogue_mixed)
        mixed_flaws = [f for f in flaws if f.flaw_type == "DIALOGUE_MIXED"]
        self.assertEqual(len(mixed_flaws), 1)

    def test_scan_ai_conjunction_p3(self):
        """测试段落内出现典型 AI 翻译腔高频连词触发 AI_CONJUNCTION (P3)"""
        text = (
            "第一段正常叙述。\n"
            "然而敌人并没有给他喘息的机会。\n"
            "与此同时，另一边的战场也陷入了胶着。\n"
            "不可否认的是，这一招确实威力惊人。\n"
            "值得注意的是，空气中弥漫着淡淡血腥味。\n"
            "林中的微风仿佛在昭示着什么预兆。\n"
            "他看着眼前的场景，不由得倒吸了一口凉气。"
        )
        flaws = scan_typography_flaws(text)
        ai_flaws = [f for f in flaws if f.flaw_type == "AI_CONJUNCTION"]
        self.assertEqual(len(ai_flaws), 6)
        for f in ai_flaws:
            self.assertEqual(f.severity, "P3")
            self.assertTrue(len(f.snippet) <= 60)
            self.assertIn("AI", f.message)

    def test_scan_multiple_ai_conjunctions_in_single_line(self):
        """测试单行内出现多个不同的 AI 连词，均能被分别检出"""
        text = "然而事情的发展出乎预料，与此同时，城门已经被攻破。"
        flaws = scan_typography_flaws(text)
        ai_flaws = [f for f in flaws if f.flaw_type == "AI_CONJUNCTION"]
        self.assertEqual(len(ai_flaws), 2)
        conjs = [f.message for f in ai_flaws]
        self.assertTrue(any("然而" in m for m in conjs))
        self.assertTrue(any("与此同时" in m for m in conjs))

    def test_whitelist_blocks_not_triggering_flaws(self):
        """测试白名单区块中的内容（即使包含 AI 词、超长文本等）不会误报缺陷"""
        long_fake_attrs = "力量" + "9" * 150
        text = (
            "正文开始。\n"
            "【系统属性面板】\n"
            f"【宿主：李逍遥，{long_fake_attrs}】\n"
            "【然而，敏捷：999，与此同时，法力：888】\n"
            "正文结束。"
        )
        flaws = scan_typography_flaws(text)
        # 面板部分不应产生任何缺陷（第2、3、4行）
        panel_flaws = [f for f in flaws if f.line_number in (2, 3, 4)]
        self.assertEqual(len(panel_flaws), 0)

    def test_format_finding_order_and_properties(self):
        """测试返回的 FormatFinding 顺序严格按 1-based 物理行号升序排列"""
        text = (
            "第一行短文本。\n"
            "然而第二行有 AI 连词。\n"
            "第三行正常。\n"
            "与此同时第四行也是 AI 连词。\n"
            "第五行正常。"
        )
        flaws = scan_typography_flaws(text)
        line_numbers = [f.line_number for f in flaws]
        self.assertEqual(line_numbers, sorted(line_numbers))
        self.assertEqual(line_numbers, [2, 4])
        for f in flaws:
            self.assertIsInstance(f, FormatFinding)
            self.assertGreater(f.line_number, 0)
            self.assertTrue(len(f.snippet) <= 60)
            self.assertIn(f.severity, ["P2", "P3"])

    def test_dual_parameter_call_compatibility(self):
        """测试 scan_typography_flaws 传参 (masked_text, original_text) 的兼容性与准确性"""
        raw_text = (
            "前言导读。\n"
            "【系统提示：宿主获得寿元百年】\n"
            "然而他并没有因此感到欣喜，反倒更加沉重。"
        )
        masked_text, masks = mask_special_blocks(raw_text)
        flaws = scan_typography_flaws(text=masked_text, original_text=raw_text)
        self.assertEqual(len(flaws), 1)
        self.assertEqual(flaws[0].line_number, 3)
        self.assertEqual(flaws[0].flaw_type, "AI_CONJUNCTION")
        self.assertIn("然而", flaws[0].snippet)

    def test_composite_flaws_in_single_line(self):
        """测试单行同时存在超长段落、拖沓长句及 AI 连词时的完整检出"""
        # 构造一段 >120 字，包含 >=4 个逗号，且包含“然而”的段落
        base = "然而天边突然乌云翻滚，" + "狂风呼啸过林间，" + "远处的兽吼接连不断，" + "大雨倾盆而下，" + "整座山峰都在微微颤抖。"
        filler = (
            "周围的树木被狂风吹得东倒西歪，枝丫在风雨中剧烈摇曳，发出尖锐刺耳的摩擦声，"
            "让人听了心神剧颤，大地深处仿佛也在酝酿着某种毁天灭地的古老力量，久久不能平息。"
        )
        line = base + filler
        self.assertGreaterEqual(len(line), 120)

        flaws = scan_typography_flaws(line)
        types = [f.flaw_type for f in flaws]
        self.assertIn("LONG_PARAGRAPH", types)
        self.assertIn("DRAGGING_SENTENCE", types)
        self.assertIn("AI_CONJUNCTION", types)
        for f in flaws:
            self.assertEqual(f.line_number, 1)

    def test_empty_and_whitespace_input(self):
        """测试空文本和空白文本安全处理"""
        masked, masks = mask_special_blocks("")
        self.assertEqual(masked, "")
        self.assertEqual(masks, [])
        self.assertEqual(scan_typography_flaws(""), [])

        all_newlines = "\n\n\n"
        masked_nl, masks_nl = mask_special_blocks(all_newlines)
        self.assertEqual(masked_nl.count("\n"), 3)
        self.assertEqual(scan_typography_flaws(all_newlines), [])


if __name__ == "__main__":
    unittest.main()
