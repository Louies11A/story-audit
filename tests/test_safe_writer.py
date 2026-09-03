"""
tests/test_safe_writer.py: 三行锚点邻域消歧安全回写器单元测试

测试覆盖：
1. 三行锚点唯一匹配成功回写，原文件编码与换行符严格保持（UTF-8, UTF-8-BOM, GB18030, CRLF/LF）；
2. 强制原子备份验证（默认 reports/.bak 与自定义 backup_dir，支持 str 与 Path）；
3. 单行段落内精确匹配回写；
4. 跨段落含空行的三行邻近匹配回写；
5. 首句或尾句（context_before 或 context_after 为空）的锚点匹配与回写；
6. 重复高频动作短句（“侧身。出刀。断魂”）在局部邻域 [target_line - 30, target_line + 30] 内精准消歧；
7. 邻域越界截断处理（下限截断为 0，防止负索引越界；上限截断为 len(lines)；负数与越界 target_line 防御）；
8. 局部邻域内仍有多重匹配时，抛出 AmbiguousPatchError 坚决拒绝篡改原稿；
9. 局部邻域内无匹配时，抛出 AmbiguousPatchError 坚决拒绝篡改原稿；
10. 全章 0 匹配或 old_text 为空时，抛出 PatchAnchorNotFoundError 坚决拒绝篡改原稿；
11. new_text 包含换行符（短句拆分）正确展开写入；
12. 目标文件不存在异常处理。
"""

import os
import tempfile
import unittest
from pathlib import Path

from scripts.safe_io import read_file_safe, write_file_safe, SafeIOReadError
from scripts.types import PatchSpec
from scripts.safe_writer import (
    SafeWriterError,
    AmbiguousPatchError,
    PatchAnchorNotFoundError,
    apply_patch_with_disambiguation,
)


class TestSafeWriter(unittest.TestCase):
    """测试安全回写器与邻域消歧机制"""

    def setUp(self):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)

    def tearDown(self):
        self.temp_dir_obj.cleanup()

    def test_three_line_unique_match_and_atomic_backup_default_dir(self):
        """测试三行锚点唯一匹配成功回写，并验证默认 reports/.bak 自动生成备份"""
        chapter_file = self.temp_dir / "第001章_试剑.md"
        content = (
            "第一章 试剑\n"
            "秋风萧瑟，黄叶飘零。\n"
            "陆尘冷笑一声，握紧长剑。\n"
            "旋即身形如鬼魅般掠出，撕裂了漆黑的夜幕，剑锋直取咽喉。\n"
            "鲜血瞬间染红了青石阶。\n"
            "长夜重归寂静。\n"
        )
        write_file_safe(chapter_file, content, encoding="utf-8", newline="\n")

        patch = PatchSpec(
            target_line=4,
            context_before="陆尘冷笑一声，握紧长剑。",
            old_text="旋即身形如鬼魅般掠出，撕裂了漆黑的夜幕，剑锋直取咽喉。",
            new_text="身如鬼魅，长夜裂。\n剑锋直封咽喉。",
            context_after="鲜血瞬间染红了青石阶。",
        )

        res = apply_patch_with_disambiguation(chapter_file, patch)
        self.assertTrue(res)

        # 验证文件修改结果
        new_content, enc, nl = read_file_safe(chapter_file)
        self.assertEqual(enc, "utf-8")
        self.assertEqual(nl, "\n")
        self.assertIn("身如鬼魅，长夜裂。\n剑锋直封咽喉。", new_content)
        self.assertNotIn("旋即身形如鬼魅般掠出，撕裂了漆黑的夜幕，剑锋直取咽喉。", new_content)
        self.assertIn("陆尘冷笑一声，握紧长剑。", new_content)
        self.assertIn("鲜血瞬间染红了青石阶。", new_content)

        # 验证默认备份生成在 chapter_file.parent / "reports" / ".bak"
        default_bak_dir = chapter_file.parent / "reports" / ".bak"
        self.assertTrue(default_bak_dir.is_dir())
        bak_files = list(default_bak_dir.glob("*.bak"))
        self.assertEqual(len(bak_files), 1)
        # 备份内容与原内容严格一致
        bak_content, _, _ = read_file_safe(bak_files[0])
        self.assertEqual(bak_content, content)

    def test_encoding_and_newline_preservation(self):
        """测试原文件编码与换行符严格保真（UTF-8-SIG 与 CRLF，以及 GB18030）"""
        # 1. 测试 UTF-8-SIG + CRLF
        file_crlf = self.temp_dir / "chapter_crlf.md"
        raw_text = (
            "前置背景设定。\r\n"
            "林中树影摇曳。\r\n"
            "旧文本待修改。\r\n"
            "后置背景描述。\r\n"
        )
        # 写入 utf-8-sig 和 \r\n
        write_file_safe(file_crlf, raw_text, encoding="utf-8-sig", newline="\r\n")

        patch1 = PatchSpec(
            target_line=3,
            context_before="林中树影摇曳。",
            old_text="旧文本待修改。",
            new_text="新短句已替换。",
            context_after="后置背景描述。",
        )
        custom_bak_dir = str(self.temp_dir / "custom_backups")  # 传入字符串类型
        apply_patch_with_disambiguation(file_crlf, patch1, backup_dir=custom_bak_dir)

        # 检查修改后编码与换行符
        _, enc, nl = read_file_safe(file_crlf)
        self.assertEqual(enc, "utf-8-sig")
        self.assertEqual(nl, "\r\n")
        raw_bytes = file_crlf.read_bytes()
        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw_bytes)

        # 2. 测试 GB18030
        file_gb = self.temp_dir / "chapter_gb.md"
        raw_gb_text = (
            "大漠孤烟直。\n"
            "长河落日圆。\n"
            "铁骑突出刀枪鸣。\n"
            "四海翻腾云水怒。\n"
        )
        write_file_safe(file_gb, raw_gb_text, encoding="gb18030", newline="\n")

        patch2 = PatchSpec(
            target_line=3,
            context_before="长河落日圆。",
            old_text="铁骑突出刀枪鸣。",
            new_text="金戈铁马入梦来。",
            context_after="四海翻腾云水怒。",
        )
        apply_patch_with_disambiguation(file_gb, patch2, backup_dir=Path(custom_bak_dir))

        new_gb_content, enc2, nl2 = read_file_safe(file_gb)
        self.assertEqual(enc2, "gb18030")
        self.assertEqual(nl2, "\n")
        self.assertIn("金戈铁马入梦来。", new_gb_content)

    def test_single_line_exact_match(self):
        """测试单行段落内的精确匹配与精准替换"""
        chapter_file = self.temp_dir / "single_line.md"
        content = (
            "第一段开场。\n"
            "风声呼啸，他在暗处默默等待，待到目标靠近，突然拔刀斩出，正中要害，敌人应声倒地。\n"
            "第二段收尾。\n"
        )
        write_file_safe(chapter_file, content, encoding="utf-8", newline="\n")

        patch = PatchSpec(
            target_line=2,
            context_before="风声呼啸，他在暗处默默等待，",
            old_text="待到目标靠近，突然拔刀斩出，正中要害，",
            new_text="猎物逼近！\n长刀出鞘！",
            context_after="敌人应声倒地。",
        )

        res = apply_patch_with_disambiguation(chapter_file, patch)
        self.assertTrue(res)

        new_content, _, _ = read_file_safe(chapter_file)
        self.assertIn("风声呼啸，他在暗处默默等待，猎物逼近！\n长刀出鞘！敌人应声倒地。", new_content)
        self.assertNotIn("待到目标靠近，突然拔刀斩出，正中要害，", new_content)

    def test_three_line_with_empty_lines(self):
        """测试网文排版中包含空行的段落间三行顺序邻近匹配"""
        chapter_file = self.temp_dir / "empty_lines.md"
        content = (
            "前情提要。\n"
            "\n"
            "萧炎深吸一口气，掌心青莲地心火升腾而起。\n"
            "\n"
            "接着恐怖的高温骤然爆发开来，将周围数十丈内的青石地板悉数熔化成赤红岩浆。\n"
            "\n"
            "纳兰嫣然绝美的容颜瞬间惨白如纸。\n"
            "\n"
            "全场一片死寂。\n"
        )
        write_file_safe(chapter_file, content, encoding="utf-8", newline="\n")

        patch = PatchSpec(
            target_line=5,
            context_before="萧炎深吸一口气，掌心青莲地心火升腾而起。",
            old_text="接着恐怖的高温骤然爆发开来，将周围数十丈内的青石地板悉数熔化成赤红岩浆。",
            new_text="异火暴涌！\n十丈石阶，尽化赤浆！",
            context_after="纳兰嫣然绝美的容颜瞬间惨白如纸。",
        )

        res = apply_patch_with_disambiguation(chapter_file, patch)
        self.assertTrue(res)

        new_content, _, _ = read_file_safe(chapter_file)
        self.assertIn("异火暴涌！\n十丈石阶，尽化赤浆！", new_content)

    def test_first_or_last_line_empty_context(self):
        """测试首行（无前置锚点）或尾行（无后置锚点）的情形"""
        chapter_file = self.temp_dir / "boundary_lines.md"
        content = (
            "开篇第一句很拖沓的长句子。\n"
            "第二句承接前文。\n"
            "末尾最后一句非常啰嗦的结语。\n"
        )
        write_file_safe(chapter_file, content, encoding="utf-8", newline="\n")

        # 1. 首行修改（context_before 为空）
        patch_head = PatchSpec(
            target_line=1,
            context_before="",
            old_text="开篇第一句很拖沓的长句子。",
            new_text="开局短句。",
            context_after="第二句承接前文。",
        )
        apply_patch_with_disambiguation(chapter_file, patch_head)

        # 2. 尾行修改（context_after 为空）
        patch_tail = PatchSpec(
            target_line=3,
            context_before="第二句承接前文。",
            old_text="末尾最后一句非常啰嗦的结语。",
            new_text="终局完结。",
            context_after="",
        )
        apply_patch_with_disambiguation(chapter_file, patch_tail)

        final_content, _, _ = read_file_safe(chapter_file)
        self.assertEqual(final_content, "开局短句。\n第二句承接前文。\n终局完结。\n")

    def test_neighborhood_disambiguation_high_frequency_action(self):
        """测试重复高频动作短句（如“侧身。出刀。断魂”）在局部邻域 [line-30, line+30] 内精准消歧命中"""
        chapter_file = self.temp_dir / "high_freq.md"
        lines = []
        # 构建 120 行文本，在第 20 行与第 80 行分别构造重复短句
        for i in range(1, 121):
            if i == 19:
                lines.append("侧身。")
            elif i == 20:
                lines.append("出刀。")
            elif i == 21:
                lines.append("断魂。")
            elif i == 79:
                lines.append("侧身。")
            elif i == 80:
                lines.append("出刀。")
            elif i == 81:
                lines.append("断魂。")
            else:
                lines.append(f"这是第 {i:03d} 行叙述内容。")

        write_file_safe(chapter_file, "\n".join(lines), encoding="utf-8", newline="\n")

        # 1. 目标消歧修改第 20 行
        patch1 = PatchSpec(
            target_line=20,
            context_before="侧身。",
            old_text="出刀。",
            new_text="拔刀怒斩！",
            context_after="断魂。",
        )
        res1 = apply_patch_with_disambiguation(chapter_file, patch1)
        self.assertTrue(res1)

        content1, _, _ = read_file_safe(chapter_file)
        res_lines1 = content1.split("\n")
        # 验证第 20 行（index 19）被替换
        self.assertEqual(res_lines1[19], "拔刀怒斩！")
        # 验证第 80 行（index 79）保持原样
        self.assertEqual(res_lines1[79], "出刀。")

        # 2. 再针对第 80 行进行消歧修改
        patch2 = PatchSpec(
            target_line=80,
            context_before="侧身。",
            old_text="出刀。",
            new_text="残影掠空，一刀两断！",
            context_after="断魂。",
        )
        res2 = apply_patch_with_disambiguation(chapter_file, patch2)
        self.assertTrue(res2)

        content2, _, _ = read_file_safe(chapter_file)
        res_lines2 = content2.split("\n")
        self.assertEqual(res_lines2[19], "拔刀怒斩！")
        self.assertEqual(res_lines2[79], "残影掠空，一刀两断！")

    def test_neighborhood_bounds_clamping(self):
        """测试邻域越界截断处理：下限截断为 0，防止负索引报错；上限截断为 len(lines)"""
        chapter_file = self.temp_dir / "bounds.md"
        # 40 行文本，在第 4 行与第 38 行构造重复短句
        lines = []
        for i in range(1, 41):
            if i == 3:
                lines.append("侧身。")
            elif i == 4:
                lines.append("出刀。")
            elif i == 5:
                lines.append("断魂。")
            elif i == 37:
                lines.append("侧身。")
            elif i == 38:
                lines.append("出刀。")
            elif i == 39:
                lines.append("断魂。")
            else:
                lines.append(f"第 {i:02d} 行填充文字。")

        write_file_safe(chapter_file, "\n".join(lines), encoding="utf-8", newline="\n")

        # target_line = 4，下限计算 4 - 30 - 1 = -27 -> 必须截断为 0，防止负索引引发异常
        patch_head = PatchSpec(
            target_line=4,
            context_before="侧身。",
            old_text="出刀。",
            new_text="雷霆出鞘！",
            context_after="断魂。",
        )
        res_head = apply_patch_with_disambiguation(chapter_file, patch_head)
        self.assertTrue(res_head)

        content, _, _ = read_file_safe(chapter_file)
        res_lines = content.split("\n")
        self.assertEqual(res_lines[3], "雷霆出鞘！")
        self.assertEqual(res_lines[37], "出刀。")

        # target_line = 38，上限计算 38 + 30 = 68 -> 必须截断为 40
        patch_tail = PatchSpec(
            target_line=38,
            context_before="侧身。",
            old_text="出刀。",
            new_text="封喉绝杀！",
            context_after="断魂。",
        )
        res_tail = apply_patch_with_disambiguation(chapter_file, patch_tail)
        self.assertTrue(res_tail)

        content2, _, _ = read_file_safe(chapter_file)
        res_lines2 = content2.split("\n")
        self.assertEqual(res_lines2[37], "封喉绝杀！")

    def test_ambiguous_patch_in_neighborhood_raises_and_preserves_file(self):
        """测试邻域内仍有多重匹配时，抛出 AmbiguousPatchError 并拒绝篡改原稿"""
        chapter_file = self.temp_dir / "ambiguous.md"
        # 在第 15 行和第 25 行出现重复，若 target_line = 20，两处都在 [0, 50] 邻域内
        lines = []
        for i in range(1, 60):
            if i == 14 or i == 24:
                lines.append("侧身。")
            elif i == 15 or i == 25:
                lines.append("出刀。")
            elif i == 16 or i == 26:
                lines.append("断魂。")
            else:
                lines.append(f"行 {i}")
        orig_content = "\n".join(lines)
        write_file_safe(chapter_file, orig_content, encoding="utf-8", newline="\n")

        patch = PatchSpec(
            target_line=20,
            context_before="侧身。",
            old_text="出刀。",
            new_text="篡改试图！",
            context_after="断魂。",
        )

        with self.assertRaises(AmbiguousPatchError) as ctx:
            apply_patch_with_disambiguation(chapter_file, patch)

        self.assertIn("多重匹配", str(ctx.exception))

        # 坚决验证原稿未被篡改
        curr_content, _, _ = read_file_safe(chapter_file)
        self.assertEqual(curr_content, orig_content)

    def test_no_match_in_neighborhood_raises_and_preserves_file(self):
        """测试全章有多重匹配但局部邻域内无匹配时，抛出 AmbiguousPatchError 并拒绝篡改原稿"""
        chapter_file = self.temp_dir / "no_local_match.md"
        lines = []
        # 在第 10 行和第 20 行出现，但 target_line 建议在第 90 行（邻域 [59, 120] 毫无匹配）
        for i in range(1, 120):
            if i == 9 or i == 19:
                lines.append("侧身。")
            elif i == 10 or i == 20:
                lines.append("出刀。")
            elif i == 11 or i == 21:
                lines.append("断魂。")
            else:
                lines.append(f"常规段落 {i}")
        orig_content = "\n".join(lines)
        write_file_safe(chapter_file, orig_content, encoding="utf-8", newline="\n")

        patch = PatchSpec(
            target_line=90,
            context_before="侧身。",
            old_text="出刀。",
            new_text="篡改试图！",
            context_after="断魂。",
        )

        with self.assertRaises(AmbiguousPatchError) as ctx:
            apply_patch_with_disambiguation(chapter_file, patch)

        self.assertIn("无匹配", str(ctx.exception))

        # 验证原稿未被篡改
        curr_content, _, _ = read_file_safe(chapter_file)
        self.assertEqual(curr_content, orig_content)

    def test_patch_anchor_not_found_raises_and_preserves_file(self):
        """测试全章 0 匹配时，抛出 PatchAnchorNotFoundError 并拒绝篡改原稿"""
        chapter_file = self.temp_dir / "not_found.md"
        orig_content = "第一章 凡人修仙\n山村少年韩立，走出大山。\n踏入修仙界。\n"
        write_file_safe(chapter_file, orig_content, encoding="utf-8", newline="\n")

        # 1. 锚点不存在
        patch = PatchSpec(
            target_line=2,
            context_before="根本不存在的前置锚点。",
            old_text="根本不存在的待替换文本。",
            new_text="尝试写入。",
            context_after="根本不存在的后置锚点。",
        )

        with self.assertRaises(PatchAnchorNotFoundError):
            apply_patch_with_disambiguation(chapter_file, patch)

        # 2. old_text 为空
        patch_empty_old = PatchSpec(
            target_line=2,
            context_before="第一章",
            old_text="",
            new_text="尝试写入。",
            context_after="踏入修仙界。",
        )
        with self.assertRaises(PatchAnchorNotFoundError):
            apply_patch_with_disambiguation(chapter_file, patch_empty_old)

        # 验证原稿未被篡改
        curr_content, _, _ = read_file_safe(chapter_file)
        self.assertEqual(curr_content, orig_content)

    def test_file_not_found_raises(self):
        """测试传入不存在的文件时抛出异常"""
        non_existent = self.temp_dir / "ghost.md"
        patch = PatchSpec(1, "前", "旧", "新", "后")
        with self.assertRaises((SafeIOReadError, SafeWriterError)):
            apply_patch_with_disambiguation(non_existent, patch)


if __name__ == "__main__":
    unittest.main()
