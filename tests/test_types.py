import unittest
from pathlib import Path
from scripts.types import ChapterItem, FormatFinding, BoundaryContext, PatchSpec


class TestChapterItem(unittest.TestCase):
    """测试 ChapterItem 数据类"""

    def test_instantiation_and_attributes(self):
        p = Path("/books/novel/第031章_破局之策.md")
        item = ChapterItem(
            index=31.0,
            title="破局之策",
            raw_name="第031章_破局之策.md",
            path=p,
        )
        self.assertEqual(item.index, 31.0)
        self.assertEqual(item.title, "破局之策")
        self.assertEqual(item.raw_name, "第031章_破局之策.md")
        self.assertEqual(item.path, p)
        self.assertIsInstance(item.index, float)
        self.assertIsInstance(item.path, Path)

    def test_float_fractional_index(self):
        item = ChapterItem(
            index=31.1,
            title="破局之策（上）",
            raw_name="第031章_破局之策_上.md",
            path=Path("第031章_破局之策_上.md"),
        )
        self.assertEqual(item.index, 31.1)

    def test_equality(self):
        p = Path("test.md")
        c1 = ChapterItem(index=1.0, title="序", raw_name="test.md", path=p)
        c2 = ChapterItem(index=1.0, title="序", raw_name="test.md", path=p)
        self.assertEqual(c1, c2)


class TestFormatFinding(unittest.TestCase):
    """测试 FormatFinding 数据类"""

    def test_instantiation_and_attributes(self):
        finding = FormatFinding(
            line_number=42,
            flaw_type="LONG_PARAGRAPH",
            severity="P2",
            snippet="这是一个超过字数限制的长段落切片...",
            message="单段超过150字，破坏网文阅读节奏",
            suggestion="将长段落拆分为2-3个短句段落",
        )
        self.assertEqual(finding.line_number, 42)
        self.assertEqual(finding.flaw_type, "LONG_PARAGRAPH")
        self.assertEqual(finding.severity, "P2")
        self.assertEqual(finding.snippet, "这是一个超过字数限制的长段落切片...")
        self.assertEqual(finding.message, "单段超过150字，破坏网文阅读节奏")
        self.assertEqual(finding.suggestion, "将长段落拆分为2-3个短句段落")

    def test_equality(self):
        f1 = FormatFinding(
            line_number=10,
            flaw_type="AI_CONJUNCTION",
            severity="P3",
            snippet="然而，",
            message="滥用AI连词",
            suggestion="删去连词直接承接",
        )
        f2 = FormatFinding(
            line_number=10,
            flaw_type="AI_CONJUNCTION",
            severity="P3",
            snippet="然而，",
            message="滥用AI连词",
            suggestion="删去连词直接承接",
        )
        self.assertEqual(f1, f2)


class TestBoundaryContext(unittest.TestCase):
    """测试 BoundaryContext 数据类及默认值"""

    def test_default_values(self):
        ctx = BoundaryContext(
            prev_tail_300="",
            curr_head_300="天道无情，万物刍狗。",
            has_prev_chapter=False,
            is_pov_transition=False,
        )
        self.assertEqual(ctx.prev_tail_300, "")
        self.assertEqual(ctx.curr_head_300, "天道无情，万物刍狗。")
        self.assertFalse(ctx.has_prev_chapter)
        self.assertFalse(ctx.is_pov_transition)
        self.assertIsNone(ctx.transition_clue)
        self.assertEqual(ctx.isolation_zones, [])

    def test_explicit_values(self):
        zone = {
            "start_line": 15,
            "end_line": 28,
            "type": "flashback",
            "clue": "三年前的那个雨夜",
        }
        ctx = BoundaryContext(
            prev_tail_300="上一章末尾悬念文本...",
            curr_head_300="本章开头接续文本...",
            has_prev_chapter=True,
            is_pov_transition=True,
            transition_clue="与此同时，京都城外",
            isolation_zones=[zone],
        )
        self.assertEqual(ctx.prev_tail_300, "上一章末尾悬念文本...")
        self.assertEqual(ctx.curr_head_300, "本章开头接续文本...")
        self.assertTrue(ctx.has_prev_chapter)
        self.assertTrue(ctx.is_pov_transition)
        self.assertEqual(ctx.transition_clue, "与此同时，京都城外")
        self.assertEqual(len(ctx.isolation_zones), 1)
        self.assertEqual(ctx.isolation_zones[0]["clue"], "三年前的那个雨夜")

    def test_isolation_zones_default_factory_is_independent(self):
        ctx1 = BoundaryContext(
            prev_tail_300="", curr_head_300="", has_prev_chapter=False, is_pov_transition=False
        )
        ctx2 = BoundaryContext(
            prev_tail_300="", curr_head_300="", has_prev_chapter=False, is_pov_transition=False
        )
        ctx1.isolation_zones.append({"test": 1})
        self.assertEqual(len(ctx2.isolation_zones), 0)


class TestPatchSpec(unittest.TestCase):
    """测试 PatchSpec 数据类"""

    def test_instantiation_and_attributes(self):
        patch = PatchSpec(
            target_line=50,
            context_before="陆尘冷笑一声，握紧长剑。",
            old_text="旋即身形如鬼魅般掠出，撕裂了漆黑的夜幕，剑锋直取咽喉。",
            new_text="身如鬼魅，剑裂长夜。\n锋芒直取咽喉。",
            context_after="鲜血瞬间染红了青石阶。",
        )
        self.assertEqual(patch.target_line, 50)
        self.assertEqual(patch.context_before, "陆尘冷笑一声，握紧长剑。")
        self.assertEqual(patch.old_text, "旋即身形如鬼魅般掠出，撕裂了漆黑的夜幕，剑锋直取咽喉。")
        self.assertEqual(patch.new_text, "身如鬼魅，剑裂长夜。\n锋芒直取咽喉。")
        self.assertEqual(patch.context_after, "鲜血瞬间染红了青石阶。")


if __name__ == "__main__":
    unittest.main()
