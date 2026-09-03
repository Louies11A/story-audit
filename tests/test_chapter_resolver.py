"""
test_chapter_resolver.py: 章节定位与自然排序解析器单元测试套件

涵盖：
1. 中文大写数字位权转换（纯标准库，支持零/〇、两、口语省略一十、复合大数、零的变体、第一百零五）；
2. 章节序号提取（常规章、特殊序位、上中下拆分章、中文数字与阿拉伯数字）；
3. 纯净标题提取；
4. 章节探测与自然排序（Natural Sort，严禁字典序）；
5. 隐藏/备份文件过滤与正文子目录优先；
6. 序号定位与断号/重号体检。
"""

import unittest
import tempfile
from pathlib import Path

from scripts.types import ChapterItem
from scripts.chapter_resolver import (
    chinese_to_number,
    parse_chapter_index,
    parse_chapter_title,
    ChapterResolver,
)


class TestChineseToNumber(unittest.TestCase):
    """测试纯标准库中文大写数字位权转换算法"""

    def test_pure_arabic_digits(self):
        """测试纯阿拉伯数字字符串"""
        self.assertEqual(chinese_to_number("31"), 31)
        self.assertEqual(chinese_to_number("0"), 0)
        self.assertEqual(chinese_to_number("105"), 105)
        self.assertEqual(chinese_to_number("2008"), 2008)

    def test_single_digits_and_zero_variants(self):
        """测试个位数及零/〇变体"""
        self.assertEqual(chinese_to_number("零"), 0)
        self.assertEqual(chinese_to_number("〇"), 0)
        self.assertEqual(chinese_to_number("一"), 1)
        self.assertEqual(chinese_to_number("二"), 2)
        self.assertEqual(chinese_to_number("两"), 2)
        self.assertEqual(chinese_to_number("三"), 3)
        self.assertEqual(chinese_to_number("四"), 4)
        self.assertEqual(chinese_to_number("五"), 5)
        self.assertEqual(chinese_to_number("六"), 6)
        self.assertEqual(chinese_to_number("七"), 7)
        self.assertEqual(chinese_to_number("八"), 8)
        self.assertEqual(chinese_to_number("九"), 9)

    def test_colloquial_ten(self):
        """测试口语省略一十（十二->12，十九->19，十->10）"""
        self.assertEqual(chinese_to_number("十"), 10)
        self.assertEqual(chinese_to_number("十一"), 11)
        self.assertEqual(chinese_to_number("十二"), 12)
        self.assertEqual(chinese_to_number("十五"), 15)
        self.assertEqual(chinese_to_number("十九"), 19)

    def test_composite_numbers(self):
        """测试复合大数与带权位转换"""
        self.assertEqual(chinese_to_number("二十"), 20)
        self.assertEqual(chinese_to_number("三十一"), 31)
        self.assertEqual(chinese_to_number("九十九"), 99)
        self.assertEqual(chinese_to_number("一百"), 100)
        self.assertEqual(chinese_to_number("一百零五"), 105)
        self.assertEqual(chinese_to_number("第一百零五"), 105)
        self.assertEqual(chinese_to_number("两千零八"), 2008)
        self.assertEqual(chinese_to_number("两千零一十八"), 2018)
        self.assertEqual(chinese_to_number("九千九百九十九"), 9999)
        self.assertEqual(chinese_to_number("一万"), 10000)
        self.assertEqual(chinese_to_number("一万零二百零三"), 10203)

    def test_sequential_digit_variants(self):
        """测试无权位逐位念法（如〇一->1，一〇五->105）"""
        self.assertEqual(chinese_to_number("〇一"), 1)
        self.assertEqual(chinese_to_number("零一"), 1)
        self.assertEqual(chinese_to_number("一〇五"), 105)
        self.assertEqual(chinese_to_number("二〇二四"), 2024)

    def test_invalid_input(self):
        """测试非法输入与防御性处理返回 None"""
        self.assertIsNone(chinese_to_number(""))
        self.assertIsNone(chinese_to_number("   "))
        self.assertIsNone(chinese_to_number(None))  # type: ignore
        self.assertIsNone(chinese_to_number(123))   # type: ignore
        self.assertIsNone(chinese_to_number("abc"))
        self.assertIsNone(chinese_to_number("第31章"))
        self.assertIsNone(chinese_to_number("一百章"))


class TestParseChapterIndex(unittest.TestCase):
    """测试章节序号解析器"""

    def test_special_prologue_indices(self):
        """测试特殊序位映射为 0.0（序章/楔子/引子）"""
        self.assertEqual(parse_chapter_index("序章"), 0.0)
        self.assertEqual(parse_chapter_index("序章 诸神黄昏.txt"), 0.0)
        self.assertEqual(parse_chapter_index("楔子.md"), 0.0)
        self.assertEqual(parse_chapter_index("楔子_轮回之始.txt"), 0.0)
        self.assertEqual(parse_chapter_index("引子.txt"), 0.0)
        self.assertEqual(parse_chapter_index("引子 剑起星奔.md"), 0.0)
        self.assertEqual(parse_chapter_index("引子-九龙拉棺.txt"), 0.0)

    def test_split_chapters(self):
        """测试上中下拆分章（上->.1，中->.2，下->.3）"""
        self.assertEqual(parse_chapter_index("第31章（上）"), 31.1)
        self.assertEqual(parse_chapter_index("第31章(上)"), 31.1)
        self.assertEqual(parse_chapter_index("第31章 上"), 31.1)
        self.assertEqual(parse_chapter_index("第31章_上"), 31.1)
        self.assertEqual(parse_chapter_index("第三十一章（中）"), 31.2)
        self.assertEqual(parse_chapter_index("第31章(下) 大结局.txt"), 31.3)
        self.assertEqual(parse_chapter_index("031_绝处逢生_上.md"), 31.1)

    def test_regular_chapters(self):
        """测试常规章节（阿拉伯数字与中文大写数字）"""
        self.assertEqual(parse_chapter_index("第031章"), 31.0)
        self.assertEqual(parse_chapter_index("第31章 绝处逢生.txt"), 31.0)
        self.assertEqual(parse_chapter_index("第三十一章_破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("031_绝处逢生.md"), 31.0)
        self.assertEqual(parse_chapter_index("31 绝处逢生.txt"), 31.0)
        self.assertEqual(parse_chapter_index("第1章.txt"), 1.0)
        self.assertEqual(parse_chapter_index("第10章.txt"), 10.0)
        self.assertEqual(parse_chapter_index("第十一章 天骄并起.md"), 11.0)
        self.assertEqual(parse_chapter_index("第二十章 决胜千里.txt"), 20.0)
        self.assertEqual(parse_chapter_index("第2008章 重归巅峰.txt"), 2008.0)
        self.assertEqual(parse_chapter_index("第一百零五章 杀伐果断.md"), 105.0)

    def test_non_chapter_filenames(self):
        """测试非章节文件返回 None"""
        self.assertIsNone(parse_chapter_index("README.md"))
        self.assertIsNone(parse_chapter_index("设定集.txt"))
        self.assertIsNone(parse_chapter_index("人物大纲.md"))
        self.assertIsNone(parse_chapter_index("config.json"))
        self.assertIsNone(parse_chapter_index(""))
        self.assertIsNone(parse_chapter_index(None))  # type: ignore


class TestParseChapterTitle(unittest.TestCase):
    """测试章节标题提取器"""

    def test_clean_titles(self):
        """测试提取纯净标题"""
        self.assertEqual(parse_chapter_title("第31章 绝处逢生.txt"), "绝处逢生")
        self.assertEqual(parse_chapter_title("第三十一章_破局.md"), "破局")
        self.assertEqual(parse_chapter_title("031_绝处逢生.md"), "绝处逢生")
        self.assertEqual(parse_chapter_title("第31章（上） 决战前夜.txt"), "决战前夜")
        self.assertEqual(parse_chapter_title("序章 诸神黄昏.txt"), "诸神黄昏")
        self.assertEqual(parse_chapter_title("楔子 轮回.md"), "轮回")
        self.assertEqual(parse_chapter_title("引子-九龙拉棺.txt"), "九龙拉棺")

    def test_empty_or_fallback_titles(self):
        """测试无独立标题时返回空字符串或文件名主干"""
        self.assertEqual(parse_chapter_title("第031章.txt"), "")
        self.assertEqual(parse_chapter_title("第31章"), "")
        self.assertEqual(parse_chapter_title("序章.txt"), "")
        self.assertEqual(parse_chapter_title("非章节文档.md"), "非章节文档")
        self.assertEqual(parse_chapter_title(""), "")
        self.assertEqual(parse_chapter_title(None), "")  # type: ignore


class TestChapterResolver(unittest.TestCase):
    """测试 ChapterResolver 类核心逻辑"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_natural_sort_order(self):
        """测试自然数值升序排序（严禁字典序，10.0必须排在2.0之后）"""
        filenames = ["第10章.txt", "第2章.txt", "第1章.txt", "第20章.txt", "序章.txt", "第2章（上）.txt", "第2章（下）.txt"]
        for fn in filenames:
            (self.test_dir / fn).write_text("正文内容", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        indices = [c.index for c in chapters]

        # 严格验证数值排序
        expected_indices = [0.0, 1.0, 2.0, 2.1, 2.3, 10.0, 20.0]
        self.assertEqual(indices, expected_indices)
        # 显式校验第10章在第2章之后
        idx_2 = indices.index(2.0)
        idx_10 = indices.index(10.0)
        self.assertLess(idx_2, idx_10)

    def test_filtering_hidden_and_temp_files(self):
        """测试忽略隐藏文件、临时备份文件及非章节文件"""
        files_to_create = [
            "第1章.md",
            "第2章.txt",
            ".第3章.md",            # 隐藏文件
            "第3章.bak",            # 备份文件
            "第3章.tmp",            # 临时文件
            "~第4章.txt",           # 临时交换文件
            "README.md",            # 非章节文档
            "设定集.txt",            # 非章节文档
        ]
        for fn in files_to_create:
            (self.test_dir / fn).write_text("内容", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        self.assertEqual(len(chapters), 2)
        self.assertEqual([c.raw_name for c in chapters], ["第1章.md", "第2章.txt"])

    def test_discover_chapters_prefers_content_subdir(self):
        """测试若存在 正文/ 子目录则优先扫描该子目录"""
        content_dir = self.test_dir / "正文"
        content_dir.mkdir()
        (content_dir / "第1章.md").write_text("正文第一章", encoding="utf-8")
        (content_dir / "第2章.md").write_text("正文第二章", encoding="utf-8")

        # 根目录放一个干扰文件
        (self.test_dir / "第99章.md").write_text("干扰章节", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        self.assertEqual(len(chapters), 2)
        self.assertEqual([c.raw_name for c in chapters], ["第1章.md", "第2章.md"])
        self.assertEqual(chapters[0].path.parent, content_dir)

    def test_get_chapter_by_index(self):
        """测试根据数值序号定位章节"""
        (self.test_dir / "序章 诸神黄昏.txt").write_text("序章内容", encoding="utf-8")
        (self.test_dir / "第031章（上） 决战前夜.txt").write_text("上部", encoding="utf-8")
        (self.test_dir / "第031章（下） 破晓.txt").write_text("下部", encoding="utf-8")
        (self.test_dir / "第032章 新世界.txt").write_text("新章", encoding="utf-8")

        chap_0 = ChapterResolver.get_chapter_by_index(self.test_dir, 0.0)
        self.assertIsNotNone(chap_0)
        self.assertAlmostEqual(chap_0.index, 0.0, places=2)
        self.assertEqual(chap_0.title, "诸神黄昏")

        chap_31_1 = ChapterResolver.get_chapter_by_index(self.test_dir, 31.1)
        self.assertIsNotNone(chap_31_1)
        self.assertAlmostEqual(chap_31_1.index, 31.1, places=2)
        self.assertEqual(chap_31_1.title, "决战前夜")

        chap_32 = ChapterResolver.get_chapter_by_index(self.test_dir, 32.0)
        self.assertIsNotNone(chap_32)
        self.assertAlmostEqual(chap_32.index, 32.0, places=2)

        chap_none = ChapterResolver.get_chapter_by_index(self.test_dir, 999.0)
        self.assertIsNone(chap_none)

    def test_diagnose_sequence_gaps_and_duplicates(self):
        """测试断号与重号体检"""
        # 1. 正常连续无异常
        normal_chapters = [
            ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt")),
            ChapterItem(index=2.0, title="二", raw_name="第2章.txt", path=Path("/tmp/2.txt")),
            ChapterItem(index=3.0, title="三", raw_name="第3章.txt", path=Path("/tmp/3.txt")),
            ChapterItem(index=3.1, title="三上", raw_name="第3章（上）.txt", path=Path("/tmp/3_1.txt")),
            ChapterItem(index=4.0, title="四", raw_name="第4章.txt", path=Path("/tmp/4.txt")),
        ]
        diagnostics = ChapterResolver.diagnose_sequence_gaps(normal_chapters)
        self.assertEqual(diagnostics, [])

        # 2. 存在断号（1~5章中缺失第3章）
        gap_chapters = [
            ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt")),
            ChapterItem(index=2.0, title="二", raw_name="第2章.txt", path=Path("/tmp/2.txt")),
            ChapterItem(index=4.0, title="四", raw_name="第4章.txt", path=Path("/tmp/4.txt")),
            ChapterItem(index=5.0, title="五", raw_name="第5章.txt", path=Path("/tmp/5.txt")),
        ]
        gap_diagnostics = ChapterResolver.diagnose_sequence_gaps(gap_chapters)
        self.assertTrue(any("P2" in d and "3" in d for d in gap_diagnostics))

        # 3. 存在多处断号（如35跳到37，提示缺失第36章）
        gap_chapters_multi = [
            ChapterItem(index=35.0, title="三十五", raw_name="第35章.txt", path=Path("/tmp/35.txt")),
            ChapterItem(index=37.0, title="三十七", raw_name="第37章.txt", path=Path("/tmp/37.txt")),
        ]
        gap_diag_multi = ChapterResolver.diagnose_sequence_gaps(gap_chapters_multi)
        self.assertTrue(any("P2" in d and "36" in d for d in gap_diag_multi))

        # 4. 存在重号
        dup_chapters = [
            ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt")),
            ChapterItem(index=2.0, title="二A", raw_name="第2章A.txt", path=Path("/tmp/2a.txt")),
            ChapterItem(index=2.0, title="二B", raw_name="第2章B.txt", path=Path("/tmp/2b.txt")),
            ChapterItem(index=3.0, title="三", raw_name="第3章.txt", path=Path("/tmp/3.txt")),
        ]
        dup_diagnostics = ChapterResolver.diagnose_sequence_gaps(dup_chapters)
        self.assertTrue(any("P2" in d and "重复" in d for d in dup_diagnostics))

        # 5. 空列表与单章节防御测试
        self.assertEqual(ChapterResolver.diagnose_sequence_gaps([]), [])
        self.assertEqual(
            ChapterResolver.diagnose_sequence_gaps(
                [ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt"))]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
