"""
test_chapter_resolver.py: 章节定位与自然排序解析器单元测试套件

涵盖：
1. 中文大写数字位权转换（纯标准库，支持零/〇、两、口语省略一十、复合大数、金融大写数字、状态机非法数字防呆）；
2. 章节序号提取（常规章、特殊序位、上中下拆分章、分卷与前缀标签优先级、破折号连接符、金融大写数字）；
3. 纯净标题提取（P0 修复：禁止误吞以“上/中/下”开头或结尾的汉字标题，支持破折号与标签剥离）；
4. 章节探测与自然排序（Natural Sort，严禁字典序）；
5. 隐藏/备份文件过滤与正文子目录优先；
6. 序号定位与断号（支持单章跳号与跨多章连续断号）/重号体检。
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
    """测试纯标准库中文大写数字位权转换算法与状态机防呆"""

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

    def test_financial_chinese_digits(self):
        """测试大写金融数字映射（壹贰叁肆伍陆柒捌玖拾佰仟萬）"""
        self.assertEqual(chinese_to_number("壹"), 1)
        self.assertEqual(chinese_to_number("贰"), 2)
        self.assertEqual(chinese_to_number("叁"), 3)
        self.assertEqual(chinese_to_number("肆"), 4)
        self.assertEqual(chinese_to_number("伍"), 5)
        self.assertEqual(chinese_to_number("陆"), 6)
        self.assertEqual(chinese_to_number("柒"), 7)
        self.assertEqual(chinese_to_number("捌"), 8)
        self.assertEqual(chinese_to_number("玖"), 9)
        self.assertEqual(chinese_to_number("拾"), 10)
        self.assertEqual(chinese_to_number("拾壹"), 11)
        self.assertEqual(chinese_to_number("壹拾"), 10)
        self.assertEqual(chinese_to_number("叁拾壹"), 31)
        self.assertEqual(chinese_to_number("肆佰伍拾陆"), 456)
        self.assertEqual(chinese_to_number("柒仟捌佰玖拾"), 7890)
        self.assertEqual(chinese_to_number("壹萬贰仟"), 12000)
        self.assertEqual(chinese_to_number("壹萬零贰佰零叁"), 10203)

    def test_invalid_chinese_number_combinations(self):
        """测试非法中文数字组合状态机防呆（二三十、十百、权位倒置、连续数字等返回 None）"""
        # 连续非零数字跟权位
        self.assertIsNone(chinese_to_number("二三十"))
        self.assertIsNone(chinese_to_number("三四百"))
        self.assertIsNone(chinese_to_number("五六千"))
        # 权位倒置与重复权位
        self.assertIsNone(chinese_to_number("十百"))
        self.assertIsNone(chinese_to_number("十千"))
        self.assertIsNone(chinese_to_number("百千"))
        self.assertIsNone(chinese_to_number("十十"))
        self.assertIsNone(chinese_to_number("百百"))
        self.assertIsNone(chinese_to_number("千千"))
        # 缺少必要数字的前置权位
        self.assertIsNone(chinese_to_number("百"))
        self.assertIsNone(chinese_to_number("仟"))
        self.assertIsNone(chinese_to_number("佰"))
        self.assertIsNone(chinese_to_number("一千百"))
        # 异常零组合与结尾非法
        self.assertIsNone(chinese_to_number("两千零零八"))
        self.assertIsNone(chinese_to_number("一百零"))
        self.assertIsNone(chinese_to_number("一百二三"))

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

    def test_chinese_dashes_and_fullwidth(self):
        """测试中文全角破折号与全角减号连接符识别"""
        self.assertEqual(parse_chapter_index("序章——诸神黄昏.md"), 0.0)
        self.assertEqual(parse_chapter_index("01——决战.md"), 1.0)
        self.assertEqual(parse_chapter_index("第1章——开端.md"), 1.0)
        self.assertEqual(parse_chapter_index("第1章－开端.md"), 1.0)
        self.assertEqual(parse_chapter_index("第1章—开端.md"), 1.0)

    def test_volume_and_prefix_tag_priority(self):
        """测试分卷与前缀标签优先级处理（具体章号优先于卷号）"""
        # 具体章号优先（主序号必须是 31.0，而不是 1.0）
        self.assertEqual(parse_chapter_index("第一卷 第31章 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("第1卷 第31章 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("卷一 第31章 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("【加更】第31章 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("正文卷 第31章 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("【加更】第一卷 第31章 破局.md"), 31.0)
        # 前置标签下的纯数字章号
        self.assertEqual(parse_chapter_index("【加更】031 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("第一卷 031 破局.md"), 31.0)
        # 仅有分卷时取卷号
        self.assertEqual(parse_chapter_index("第一卷 破局.md"), 1.0)
        self.assertEqual(parse_chapter_index("第一卷.md"), 1.0)
        # 带前缀标签的序章
        self.assertEqual(parse_chapter_index("【加更】序章——诸神黄昏.md"), 0.0)

    def test_adversarial_title_split_not_triggered(self):
        """测试正常汉字标题（含上/中/下）不误触发拆分章逻辑"""
        self.assertEqual(parse_chapter_index("第1章 下山.md"), 1.0)
        self.assertEqual(parse_chapter_index("第2章 上善若水.txt"), 2.0)
        self.assertEqual(parse_chapter_index("第3章 决战天下.md"), 3.0)
        self.assertEqual(parse_chapter_index("第4章 落下.md"), 4.0)
        self.assertEqual(parse_chapter_index("第5章 中流砥柱.md"), 5.0)
        self.assertEqual(parse_chapter_index("第6章 掌上明珠.md"), 6.0)
        self.assertEqual(parse_chapter_index("第7章 谈笑风生中.md"), 7.0)

    def test_split_chapters(self):
        """测试上中下拆分章（上->.1，中->.2，下->.3）"""
        self.assertEqual(parse_chapter_index("第31章（上） 决战前夜.txt"), 31.1)
        self.assertEqual(parse_chapter_index("第31章(中) 决战前夜.txt"), 31.2)
        self.assertEqual(parse_chapter_index("第31章（下） 决战前夜.txt"), 31.3)
        self.assertEqual(parse_chapter_index("第31章_上_决战前夜.txt"), 31.1)
        self.assertEqual(parse_chapter_index("031_下_决战前夜.txt"), 31.3)
        self.assertEqual(parse_chapter_index("第31章下.txt"), 31.3)
        # 尾部拆分标记
        self.assertEqual(parse_chapter_index("第31章 决战天下（下）.md"), 31.3)
        self.assertEqual(parse_chapter_index("第31章 决战天下_下.md"), 31.3)
        self.assertEqual(parse_chapter_index("第31章 决战天下 下.md"), 31.3)
        self.assertEqual(parse_chapter_index("第31章 决战天下——下.md"), 31.3)

    def test_financial_digits_in_chapters(self):
        """测试金融大写数字在章节序号中的解析"""
        self.assertEqual(parse_chapter_index("第壹佰零伍章 开端.md"), 105.0)
        self.assertEqual(parse_chapter_index("第叁拾壹章（上） 破局.md"), 31.1)
        self.assertEqual(parse_chapter_index("第肆拾章 大胜.md"), 40.0)

    def test_standard_chapter_indices(self):
        """测试常规阿拉伯与中文数字章节（.0）"""
        self.assertEqual(parse_chapter_index("第31章.md"), 31.0)
        self.assertEqual(parse_chapter_index("第31章 绝处逢生.txt"), 31.0)
        self.assertEqual(parse_chapter_index("第031章 绝处逢生.txt"), 31.0)
        self.assertEqual(parse_chapter_index("第三十一章 决战前夜.md"), 31.0)
        self.assertEqual(parse_chapter_index("第31回 决战紫禁之巅.txt"), 31.0)
        self.assertEqual(parse_chapter_index("第31节 突破重围.txt"), 31.0)

    def test_prefix_numeric_chapter_indices(self):
        """测试数字前缀文件名（031_破局、31 破局等）"""
        self.assertEqual(parse_chapter_index("031_绝处逢生.md"), 31.0)
        self.assertEqual(parse_chapter_index("31 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("31. 破局.md"), 31.0)
        self.assertEqual(parse_chapter_index("31.md"), 31.0)

    def test_non_chapter_filenames(self):
        """测试非章节文档返回 None"""
        self.assertIsNone(parse_chapter_index("大纲.md"))
        self.assertIsNone(parse_chapter_index("人物卡_主角.txt"))
        self.assertIsNone(parse_chapter_index("设定集"))
        self.assertIsNone(parse_chapter_index("正文卷 破局.md"))
        self.assertIsNone(parse_chapter_index("第.md"))
        self.assertIsNone(parse_chapter_index(""))
        self.assertIsNone(parse_chapter_index(None))  # type: ignore


class TestParseChapterTitle(unittest.TestCase):
    """测试章节标题提取器"""

    def test_adversarial_title_protection_p0(self):
        """
        [P0 对抗性测试] 测试严格保护以“上/中/下”开头或结尾的汉字标题，严禁误吞或截断
        """
        # 开头包含下/上/中
        self.assertEqual(parse_chapter_title("第1章 下山.md"), "下山")
        self.assertEqual(parse_chapter_title("第2章 上善若水.txt"), "上善若水")
        self.assertEqual(parse_chapter_title("第5章 中流砥柱.md"), "中流砥柱")
        # 结尾包含下/上/中（严禁截断为“决战天”、“落”等）
        self.assertEqual(parse_chapter_title("第3章 决战天下.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第4章 落下.md"), "落下")
        self.assertEqual(parse_chapter_title("第6章 掌上明珠.md"), "掌上明珠")
        self.assertEqual(parse_chapter_title("第7章 谈笑风生中.md"), "谈笑风生中")

    def test_chinese_dashes_title_extraction(self):
        """测试中文全角破折号与连接符下的标题提取"""
        self.assertEqual(parse_chapter_title("序章——诸神黄昏.md"), "诸神黄昏")
        self.assertEqual(parse_chapter_title("01——决战.md"), "决战")
        self.assertEqual(parse_chapter_title("第1章——开端.md"), "开端")
        self.assertEqual(parse_chapter_title("第1章－开端.md"), "开端")
        self.assertEqual(parse_chapter_title("第1章—开端.md"), "开端")

    def test_prefix_tags_and_volume_title_extraction(self):
        """测试前置标签与分卷标签剥离，提取纯净标题"""
        self.assertEqual(parse_chapter_title("【加更】第31章 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("正文卷 第31章 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("第一卷 第31章 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("【加更】第一卷 第31章 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("第1卷 第31章 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("【加更】031 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("第一卷 031 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("第一卷 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("第一卷.md"), "")
        self.assertEqual(parse_chapter_title("【加更】序章——诸神黄昏.md"), "诸神黄昏")

    def test_real_split_markers_stripped(self):
        """测试合法拆分标记被正确剥离，纯净标题完好保留"""
        self.assertEqual(parse_chapter_title("第31章 决战天下（下）.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章 决战天下(下).md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章 决战天下_下.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章 决战天下 下.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章 决战天下-下.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章 决战天下——下.md"), "决战天下")
        self.assertEqual(parse_chapter_title("第31章（上） 决战前夜.txt"), "决战前夜")
        self.assertEqual(parse_chapter_title("第31章(中) 决战前夜.txt"), "决战前夜")
        self.assertEqual(parse_chapter_title("第31章_上_决战前夜.txt"), "决战前夜")
        self.assertEqual(parse_chapter_title("031_下_决战前夜.txt"), "决战前夜")
        self.assertEqual(parse_chapter_title("第31章下.txt"), "")
        self.assertEqual(parse_chapter_title("第31章上 决战前夜.txt"), "决战前夜")

    def test_standard_chapter_titles(self):
        """测试常规章节独立标题提取"""
        self.assertEqual(parse_chapter_title("第31章 绝处逢生.txt"), "绝处逢生")
        self.assertEqual(parse_chapter_title("第031章_绝处逢生.txt"), "绝处逢生")
        self.assertEqual(parse_chapter_title("第三十一章 决战前夜.md"), "决战前夜")
        self.assertEqual(parse_chapter_title("031_绝处逢生.md"), "绝处逢生")
        self.assertEqual(parse_chapter_title("31 破局.md"), "破局")
        self.assertEqual(parse_chapter_title("31. 破局.md"), "破局")

    def test_special_prologue_titles(self):
        """测试特殊序位标题提取"""
        self.assertEqual(parse_chapter_title("序章 诸神黄昏.txt"), "诸神黄昏")
        self.assertEqual(parse_chapter_title("楔子_轮回之始.txt"), "轮回之始")
        self.assertEqual(parse_chapter_title("引子-九龙拉棺.txt"), "九龙拉棺")

    def test_no_independent_titles(self):
        """测试无独立标题时返回空字符串"""
        self.assertEqual(parse_chapter_title("第31章.md"), "")
        self.assertEqual(parse_chapter_title("31.md"), "")
        self.assertEqual(parse_chapter_title("序章.txt"), "")
        self.assertEqual(parse_chapter_title("楔子.md"), "")

    def test_non_chapter_filenames(self):
        """测试非章节文档防御（返回原始主干）"""
        self.assertEqual(parse_chapter_title("大纲.md"), "大纲")
        self.assertEqual(parse_chapter_title("人物卡_主角.txt"), "人物卡_主角")
        self.assertEqual(parse_chapter_title("设定集"), "设定集")


class TestChapterResolver(unittest.TestCase):
    """测试章节定位与扫描解析引擎"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_natural_sort_ordering(self):
        """测试严格数值自然升序排序（Natural Sort，1 < 2 < 10）"""
        file_names = [
            "第10章 巅峰.md",
            "第2章 启程.txt",
            "第1章 序幕.md",
            "序章 起源.md",
            "第1章（上） 初入江湖.md",
            "第1章（下） 锋芒毕露.md",
            "第20章 归途.md",
        ]
        for name in file_names:
            (self.test_dir / name).write_text("内容", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        self.assertEqual(len(chapters), 7)

        # 验证数值升序：0.0 -> 1.0 -> 1.1 -> 1.3 -> 2.0 -> 10.0 -> 20.0
        expected_indices = [0.0, 1.0, 1.1, 1.3, 2.0, 10.0, 20.0]
        actual_indices = [c.index for c in chapters]
        self.assertEqual(actual_indices, expected_indices)

    def test_filter_hidden_and_backup_files(self):
        """测试过滤隐藏文件与临时备份文件"""
        valid_files = ["第1章.md", "第2章.txt"]
        invalid_files = [".第1章.md", "~第2章.txt", "第3章.md.bak", "第4章.tmp", "notes.doc"]

        for name in valid_files + invalid_files:
            (self.test_dir / name).write_text("内容", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        self.assertEqual(len(chapters), 2)
        self.assertEqual({c.raw_name for c in chapters}, {"第1章.md", "第2章.txt"})

    def test_prefer_content_subdir(self):
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
        """测试断号（单章缺失与跨多章连续缺失）与重号体检"""
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

        # 2. 存在单章断号（1~5章中缺失第3章）
        gap_chapters = [
            ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt")),
            ChapterItem(index=2.0, title="二", raw_name="第2章.txt", path=Path("/tmp/2.txt")),
            ChapterItem(index=4.0, title="四", raw_name="第4章.txt", path=Path("/tmp/4.txt")),
            ChapterItem(index=5.0, title="五", raw_name="第5章.txt", path=Path("/tmp/5.txt")),
        ]
        gap_diagnostics = ChapterResolver.diagnose_sequence_gaps(gap_chapters)
        self.assertTrue(any("P2" in d and "缺失第 3 章" in d for d in gap_diagnostics))

        # 3. 存在跨多章连续断号（从第30章跳到第33章，缺失 31, 32）
        multi_gap_chapters = [
            ChapterItem(index=30.0, title="三十", raw_name="第30章.txt", path=Path("/tmp/30.txt")),
            ChapterItem(index=33.0, title="三十三", raw_name="第33章.txt", path=Path("/tmp/33.txt")),
        ]
        multi_gap_diag = ChapterResolver.diagnose_sequence_gaps(multi_gap_chapters)
        self.assertTrue(
            any("P2" in d and "缺失章节 [31, 32]" in d and "从第 30 章跳到第 33 章" in d for d in multi_gap_diag)
        )

        # 4. 存在重号
        dup_chapters = [
            ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt")),
            ChapterItem(index=2.0, title="二A", raw_name="第2章A.txt", path=Path("/tmp/2a.txt")),
            ChapterItem(index=2.0, title="二B", raw_name="第2章B.txt", path=Path("/tmp/2b.txt")),
            ChapterItem(index=3.0, title="三", raw_name="第3章.txt", path=Path("/tmp/3.txt")),
        ]
        dup_diagnostics = ChapterResolver.diagnose_sequence_gaps(dup_chapters)
        self.assertTrue(any("P2" in d and "发现重复章节序号: 第 2 章" in d for d in dup_diagnostics))

        # 5. 空列表与单章节防御测试
        self.assertEqual(ChapterResolver.diagnose_sequence_gaps([]), [])
        self.assertEqual(
            ChapterResolver.diagnose_sequence_gaps(
                [ChapterItem(index=1.0, title="一", raw_name="第1章.txt", path=Path("/tmp/1.txt"))]
            ),
            [],
        )


    def test_discover_chapters_parent_has_reports_or_archive(self):
        """测试上级宿主目录路径包含 reports/archive 时平铺章节不会被误杀"""
        # 创建结构: temp_dir / "reports" / "archive_sub" / "novel_project"
        novel_dir = self.test_dir / "reports" / "archive_sub" / "novel_project"
        novel_dir.mkdir(parents=True, exist_ok=True)
        (novel_dir / "第001章_启程.md").write_text("正文内容1", encoding="utf-8")
        (novel_dir / "第002章_惊变.md").write_text("正文内容2", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(novel_dir)
        self.assertEqual(len(chapters), 2, "宿主上级路径包含 reports/archive 时不应误杀平铺正文章节")
        self.assertEqual(chapters[0].index, 1.0)
        self.assertEqual(chapters[1].index, 2.0)

    def test_discover_chapters_nested_bak_in_content_dir_filtered(self):
        """测试正文子目录下嵌套的 .bak 等隐藏目录被严格过滤"""
        content_dir = self.test_dir / "正文"
        content_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "第001章_真实正文.md").write_text("真实内容", encoding="utf-8")

        # 嵌套 .bak 目录
        bak_dir = content_dir / ".bak"
        bak_dir.mkdir(parents=True, exist_ok=True)
        (bak_dir / "第002章_备份残卷.md").write_text("备份内容", encoding="utf-8")

        # 更深层嵌套 .bak
        nested_bak_dir = content_dir / "volume1" / ".bak"
        nested_bak_dir.mkdir(parents=True, exist_ok=True)
        (nested_bak_dir / "第003章_深层备份.md").write_text("深层备份内容", encoding="utf-8")

        chapters = ChapterResolver.discover_chapters(self.test_dir)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].index, 1.0)
        self.assertEqual(chapters[0].raw_name, "第001章_真实正文.md")

    def test_diagnose_sequence_gaps_missing_chapter_one(self):
        """测试正文首章缺失（main_indices[0] > 1）时的断号体检诊断"""
        # 1. 缺失单章第 1 章（正文起始于第 2 章）
        chapters_missing_ch1 = [
            ChapterItem(index=2.0, title="第二章", raw_name="第2章.txt", path=Path("/tmp/2.txt")),
            ChapterItem(index=3.0, title="第三章", raw_name="第3章.txt", path=Path("/tmp/3.txt")),
        ]
        diag1 = ChapterResolver.diagnose_sequence_gaps(chapters_missing_ch1)
        self.assertTrue(any("P2" in d and "缺失第 1 章" in d and "第 2 章" in d for d in diag1))

        # 2. 缺失多章（正文起始于第 4 章，缺失第 1, 2, 3 章）
        chapters_missing_multi = [
            ChapterItem(index=4.0, title="第四章", raw_name="第4章.txt", path=Path("/tmp/4.txt")),
            ChapterItem(index=5.0, title="第五章", raw_name="第5章.txt", path=Path("/tmp/5.txt")),
        ]
        diag2 = ChapterResolver.diagnose_sequence_gaps(chapters_missing_multi)
        self.assertTrue(any("P2" in d and "缺失章节 [1, 2, 3]" in d for d in diag2))

if __name__ == "__main__":
    unittest.main()
