# -*- coding: utf-8 -*-
"""
tests/test_genre_detector.py: 题材自动探测与画像引擎单元测试
"""

import unittest
from pathlib import Path
from scripts.genre_detector import (
    GenreProfile,
    detect_genre,
    get_all_genres,
    get_genre_metadata,
    resolve_canonical_genre,
)
from scripts.types import ChapterItem


class TestGenreDetector(unittest.TestCase):
    """测试题材自动探测引擎与画像契约"""

    def test_all_genres_coverage(self):
        """验证全题材知识库完整覆盖 42 题材"""
        genres = get_all_genres()
        self.assertEqual(len(genres), 42)
        # 验证长篇与短篇核心题材均在列表中
        self.assertIn("传统玄幻", genres)
        self.assertIn("东方仙侠", genres)
        self.assertIn("都市高武", genres)
        self.assertIn("科幻末世", genres)
        self.assertIn("年代", genres)
        self.assertIn("宫斗宅斗", genres)
        self.assertIn("追妻火葬场", genres)
        self.assertIn("民俗怪谈", genres)
        self.assertIn("战神赘婿", genres)
        self.assertIn("悬疑脑洞", genres)

    def test_resolve_canonical_genre(self):
        """验证别名解析与模糊匹配"""
        self.assertEqual(resolve_canonical_genre("东方仙侠"), "东方仙侠")
        self.assertEqual(resolve_canonical_genre("仙侠"), "东方仙侠")
        self.assertEqual(resolve_canonical_genre("火葬场"), "追妻火葬场")
        self.assertEqual(resolve_canonical_genre("年代文"), "年代")
        self.assertEqual(resolve_canonical_genre("高武"), "都市高武")
        self.assertEqual(resolve_canonical_genre("规则怪谈"), "悬疑脑洞")
        self.assertEqual(resolve_canonical_genre("龙王归来"), "战神赘婿")
        self.assertIsNone(resolve_canonical_genre(""))
        self.assertIsNone(resolve_canonical_genre(None))

    def test_empty_text_fallback(self):
        """验证空文本或无特征输入时的安全降级回退"""
        res_empty = detect_genre("")
        self.assertEqual(res_empty.primary_genre, "传统玄幻")
        self.assertEqual(res_empty.confidence, 0.0)

        res_spaces = detect_genre("   \n\n  \t ")
        self.assertEqual(res_spaces.primary_genre, "传统玄幻")
        self.assertEqual(res_spaces.confidence, 0.0)

    def test_specified_genre_override(self):
        """验证显式手动指定题材强行生效"""
        text = "陈默在学校测了气血，气血值飙升到了准武者标准。"
        res = detect_genre(text, specified_genre="东方仙侠")
        self.assertEqual(res.primary_genre, "东方仙侠")
        self.assertEqual(res.confidence, 1.0)
        self.assertEqual(res.category_group, "仙侠玄幻")
        self.assertTrue(len(res.first_principles) > 0)
        self.assertTrue(len(res.red_lines) > 0)

        # 别名指定测试
        res2 = detect_genre(text, specified_genre="火葬场")
        self.assertEqual(res2.primary_genre, "追妻火葬场")
        self.assertEqual(res2.confidence, 1.0)

    def test_typical_genre_detections(self):
        """测试各典型题材自然文本样本的精准识别"""
        test_cases = [
            (
                "东方仙侠",
                "顾渊盘坐洞府之中，周身道韵流转，感受体内筑基后期的金丹雏形与灵根吞吐的长生灵气。师尊赠予的飞剑在旁轻鸣。"
            ),
            (
                "都市高武",
                "武道高考体测室内，高三学生排队走上气血仪。陈默拳力测试轰然爆发，气血值飙升突破准武者门槛，班主任震惊地瞪大了眼睛！"
            ),
            (
                "科幻末世",
                "避难所合金重门缓缓合上，外面的极寒风暴席卷废土。陈峰清点着搜刮来的重卡与物资，丧尸晶核正在重构点熔炉中闪烁。"
            ),
            (
                "年代",
                "生产队的大队长吹响了哨子，知青们纷纷拿着农具排队上工挣工分。苏晚揣着怀里的两张全国粮票和布票，朝供销社走去。"
            ),
            (
                "追妻火葬场",
                "顾泽言将离婚协议书甩在桌上，逼我签字给他的白月光让位。我看着他和白月光的合照，心如死灰，决绝离场。可是后来，他却悔疯了，跪在雨夜里发疯似的找我。"
            ),
            (
                "民俗怪谈",
                "扎纸匠老李头连夜扎了两个纸人和一双红色绣花鞋。门外传来阴森的敲门声，棺材前的白蜡烛突然变成了惨绿色，这是回魂夜走阴的凶煞！"
            ),
            (
                "战神赘婿",
                "岳母将一盆洗脚水狠狠泼在林辰脚边，大骂窝囊废赘婿。林辰眼神一冷，战神令现世，一声令下，十万战神殿将士齐聚省城！"
            ),
            (
                "宫斗宅斗",
                "庶妹仗着姨娘得宠，竟敢克扣我院里的月钱和份例。祖母冷冷看了一眼，主母下令当场掌嘴二十，夺回掌家大权！"
            ),
            (
                "悬疑脑洞",
                "纸条上的规则怪谈写得清清楚楚：午夜十二点后不可直视镜子，一旦污染度超过临界值，san值归零，异化率将无法遏制。"
            ),
            (
                "豪门总裁",
                "厉总居高临下地递过一张黑卡，冷冷道：这是五千万违约金，签了这份协议结婚合同，傅家破产的危机自会解除。"
            ),
            (
                "星光璀璨",
                "录音棚内，新歌前奏一响，在场的所有评委和金牌制作人都站了起来！天王主打歌横空出世，直接霸占新歌榜首，引爆全网热搜！"
            ),
            (
                "抗战谍战",
                "深夜密室中，发报机的滴答声急促响起。军统特工代号孤狼正在破译特高课截获的密电码，地下党联络站危在旦夕，必须立刻锄奸！"
            ),
        ]

        for expected_genre, text in test_cases:
            with self.subTest(genre=expected_genre):
                profile = detect_genre(text)
                self.assertEqual(
                    profile.primary_genre,
                    expected_genre,
                    f"样本探测失败: 预期 {expected_genre}, 实际得到 {profile.primary_genre} (得分: {profile.scores})"
                )
                self.assertTrue(profile.confidence > 0.2)
                self.assertTrue(len(profile.first_principles) > 0)
                self.assertTrue(len(profile.red_lines) > 0)
                self.assertTrue(len(profile.keywords_matched) > 0)

    def test_chapter_item_input(self, tmp_path=None):
        """测试 ChapterItem 或文件路径作为入参的兼容性"""
        import tempfile
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tf:
            tf.write("灵根筑基，金丹元婴，师尊赐飞剑，渡劫成仙。")
            tmp_file = Path(tf.name)

        try:
            chap = ChapterItem(index=1.0, title="开篇求仙", raw_name="第001章.txt", path=tmp_file)
            profile = detect_genre(chap)
            self.assertEqual(profile.primary_genre, "东方仙侠")

            # 列表形式
            profile_list = detect_genre([chap])
            self.assertEqual(profile_list.primary_genre, "东方仙侠")
        finally:
            if tmp_file.exists():
                tmp_file.unlink()

    def test_to_dict_schema(self):
        """验证 GenreProfile.to_dict 序列化契约"""
        text = "气血仪上显示气血值150，武道高考模拟考突破准武者！"
        profile = detect_genre(text)
        d = profile.to_dict()
        self.assertIn("primary_genre", d)
        self.assertIn("confidence", d)
        self.assertIn("secondary_genres", d)
        self.assertIn("category_group", d)
        self.assertIn("first_principles", d)
        self.assertIn("red_lines", d)
        self.assertIn("keywords_matched", d)
        self.assertIn("scores", d)
        self.assertIsInstance(d["scores"], dict)


if __name__ == "__main__":
    unittest.main()
