import unittest
import tempfile
import re
import codecs
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.safe_io import (
    SafeIOError,
    SafeIOReadError,
    SafeIOWriteError,
    read_file_safe,
    write_file_safe,
    create_atomic_backup,
)


class TestSafeIO(unittest.TestCase):
    """统一安全文件 I/O 与原子备份器测试套件"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_utf8_sig_with_bom(self):
        """测试带 BOM 的 utf-8-sig 编码自动嗅探与换行识别"""
        target_file = self.test_dir / "第001章_带BOM.md"
        raw_text = "第一章 灵气复苏\r\n顾渊持剑而立，眼中杀意未消。\r\n"
        # 写入带有 UTF-8 BOM 头
        bom_bytes = codecs.BOM_UTF8 + raw_text.encode("utf-8")
        target_file.write_bytes(bom_bytes)

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(encoding, "utf-8-sig")
        self.assertEqual(newline, "\r\n")
        self.assertNotIn("\r\n", content)
        self.assertIn("顾渊持剑而立", content)
        self.assertEqual(content, "第一章 灵气复苏\n顾渊持剑而立，眼中杀意未消。\n")

    def test_read_utf8_standard(self):
        """测试标准无 BOM utf-8 编码读取与换行识别"""
        target_file = self.test_dir / "第002章_标准UTF8.md"
        raw_text = "第二章 宗门大比\n白衣少年神色淡漠。\n"
        target_file.write_bytes(raw_text.encode("utf-8"))

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(newline, "\n")
        self.assertEqual(content, raw_text)

    def test_read_gb18030_chinese(self):
        """测试 GB18030 汉字编码探测回退与内容完全保真"""
        target_file = self.test_dir / "第003章_GB18030.txt"
        raw_text = "第三章 龙争虎斗\r\n刀光如匹练撕裂长空，四座皆惊。\r\n"
        target_file.write_bytes(raw_text.encode("gb18030"))

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(encoding, "gb18030")
        self.assertEqual(newline, "\r\n")
        self.assertNotIn("\r\n", content)
        self.assertEqual(content, "第三章 龙争虎斗\n刀光如匹练撕裂长空，四座皆惊。\n")

    def test_read_newline_normalization(self):
        """测试 Windows CRLF 在内存中统一规整为 LF"""
        target_file = self.test_dir / "crlf_sample.md"
        raw_text = "段落一\r\n段落二\r\n段落三"
        target_file.write_bytes(raw_text.encode("utf-8"))

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(newline, "\r\n")
        self.assertEqual(content, "段落一\n段落二\n段落三")

    def test_read_isolated_cr_normalization(self):
        """测试单独回车符（孤立 \r）彻底清除并规整为 \n"""
        target_file = self.test_dir / "isolated_cr.txt"
        raw_text = "第1行\r\n第2行\r第3行\n第4行"
        target_file.write_bytes(raw_text.encode("utf-8"))

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(newline, "\r\n")
        self.assertNotIn("\r", content)
        self.assertEqual(content, "第1行\n第2行\n第3行\n第4行")

    def test_read_nonexistent_file_raises_error(self):
        """测试读取不存在的文件抛出 SafeIOReadError"""
        non_file = self.test_dir / "不存在的文件.md"
        with self.assertRaises(SafeIOReadError):
            read_file_safe(non_file)

    def test_read_corrupted_binary_file_raises_error(self):
        """测试损坏二进制文件解码失败时抛出 SafeIOReadError"""
        corrupted_file = self.test_dir / "corrupted.bin"
        corrupted_file.write_bytes(b"\xff\xff\xff\xff\xfe\xfe")

        with self.assertRaises(SafeIOReadError):
            read_file_safe(corrupted_file)

    def test_write_file_safe_creates_dirs_and_preserves_newline(self):
        """测试安全写入：自动创建深层父目录，支持 CRLF 换行写入"""
        deep_file = self.test_dir / "sub1" / "sub2" / "output.md"
        content_lf = "第十行 剑气长歌\n第二行 归隐山林"

        # 写入 CRLF
        write_file_safe(deep_file, content_lf, encoding="utf-8", newline="\r\n")
        self.assertTrue(deep_file.exists())

        raw_bytes = deep_file.read_bytes()
        self.assertIn(b"\r\n", raw_bytes)
        self.assertNotIn(b"\n\n", raw_bytes)
        self.assertEqual(
            raw_bytes.decode("utf-8"),
            "第十行 剑气长歌\r\n第二行 归隐山林"
        )

    def test_write_file_safe_gb18030(self):
        """测试安全写入 GB18030 编码文件"""
        target_file = self.test_dir / "gb_out.txt"
        content = "乾坤未定，你我皆是黑马。\n"
        write_file_safe(target_file, content, encoding="gb18030", newline="\n")

        raw_bytes = target_file.read_bytes()
        self.assertEqual(raw_bytes.decode("gb18030"), content)

    def test_write_utf8_sig_roundtrip(self):
        """验证以 utf-8-sig 写入时包含 BOM 头，且再次读取时完全无损"""
        target_file = self.test_dir / "utf8_sig_test.md"
        content_to_write = "第四章 锋芒毕露\r\n剑出如龙，寒芒乍现。\r\n"
        write_file_safe(target_file, content_to_write, encoding="utf-8-sig", newline="\r\n")

        # 验证底层二进制以 UTF-8 BOM 开头
        raw_bytes = target_file.read_bytes()
        self.assertTrue(raw_bytes.startswith(codecs.BOM_UTF8))

        # 再次用 read_file_safe 读取
        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(encoding, "utf-8-sig")
        self.assertEqual(newline, "\r\n")
        self.assertEqual(content, "第四章 锋芒毕露\n剑出如龙，寒芒乍现。\n")

    def test_empty_file(self):
        """验证 0 字节空文件读取返回 ('', 'utf-8', '\n')"""
        empty_file = self.test_dir / "empty.txt"
        empty_file.write_bytes(b"")

        content, encoding, newline = read_file_safe(empty_file)
        self.assertEqual(content, "")
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(newline, "\n")

    def test_single_line_without_newline(self):
        """验证单行无换行符文本行为"""
        target_file = self.test_dir / "single_line.txt"
        raw_text = "单行无换行测试文本内容"
        target_file.write_bytes(raw_text.encode("utf-8"))

        content, encoding, newline = read_file_safe(target_file)
        self.assertEqual(content, raw_text)
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(newline, "\n")

        # 往返写回验证
        output_file = self.test_dir / "single_line_out.txt"
        write_file_safe(output_file, content, encoding=encoding, newline=newline)
        c2, e2, n2 = read_file_safe(output_file)
        self.assertEqual(c2, raw_text)
        self.assertEqual(e2, "utf-8")
        self.assertEqual(n2, "\n")

    def test_str_path_input(self):
        """验证纯 str 字符串路径传参正常运作"""
        str_path = str(self.test_dir / "str_path_file.txt")
        str_backup_dir = str(self.test_dir / "str_backup")
        text = "纯字符串路径输入测试\n第二行内容\n"

        # 写入
        write_file_safe(str_path, text, encoding="utf-8", newline="\n")
        self.assertTrue(Path(str_path).exists())

        # 读取
        content, encoding, newline = read_file_safe(str_path)
        self.assertEqual(content, text)
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(newline, "\n")

        # 备份
        backup_path = create_atomic_backup(str_path, str_backup_dir)
        self.assertIsInstance(backup_path, Path)
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.read_bytes(), Path(str_path).read_bytes())

    def test_create_atomic_backup_generation_and_integrity(self):
        """测试原子备份生成：时间戳格式、命名规则与内容镜像一致性"""
        source_file = self.test_dir / "第031章_破局之策.md"
        source_data = "第三十一章 破局之策\n顾渊立于万仞绝壁。\n".encode("utf-8")
        source_file.write_bytes(source_data)

        backup_dir = self.test_dir / "reports" / ".bak"
        backup_path = create_atomic_backup(source_file, backup_dir)

        # 验证备份目录与备份文件存在
        self.assertTrue(backup_dir.exists())
        self.assertTrue(backup_path.exists())
        self.assertIsInstance(backup_path, Path)

        # 验证命名格式：{stem}_{YYYYMMDD_HHMMSS}{suffix}.bak
        pattern = r"^第031章_破局之策_\d{8}_\d{6}\.md\.bak$"
        self.assertRegex(backup_path.name, pattern)

        # 验证内容与源文件完全一致（字节流镜像）
        self.assertEqual(backup_path.read_bytes(), source_data)

    def test_atomic_backup_collision(self):
        """验证同一秒内连续调用时生成不重名的备份文件"""
        source_file = self.test_dir / "collision_source.md"
        source_data = "备份防碰撞测试原文内容".encode("utf-8")
        source_file.write_bytes(source_data)
        backup_dir = self.test_dir / "backup_collision"

        fixed_time = datetime(2026, 9, 3, 15, 30, 0)
        with patch("scripts.safe_io.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_time
            mock_datetime.strftime = datetime.strftime

            bak1 = create_atomic_backup(source_file, backup_dir)
            bak2 = create_atomic_backup(source_file, backup_dir)
            bak3 = create_atomic_backup(source_file, backup_dir)

        self.assertTrue(bak1.exists())
        self.assertTrue(bak2.exists())
        self.assertTrue(bak3.exists())

        # 验证文件名互不相同
        self.assertNotEqual(bak1, bak2)
        self.assertNotEqual(bak2, bak3)
        self.assertNotEqual(bak1, bak3)

        # 验证命名规范：首次无计数，同秒后续追加 _01, _02
        self.assertEqual(bak1.name, "collision_source_20260903_153000.md.bak")
        self.assertEqual(bak2.name, "collision_source_20260903_153000_01.md.bak")
        self.assertEqual(bak3.name, "collision_source_20260903_153000_02.md.bak")

        # 验证镜像数据完好
        self.assertEqual(bak1.read_bytes(), source_data)
        self.assertEqual(bak2.read_bytes(), source_data)
        self.assertEqual(bak3.read_bytes(), source_data)

    def test_write_file_safe_atomic_failure_cleanup(self):
        """验证写入异常时原文件不受破坏，且临时文件被安全清理"""
        target_file = self.test_dir / "protected_target.txt"
        initial_content = "原始绝密数据，绝不可被破坏！"
        target_file.write_text(initial_content, encoding="utf-8")

        # 模拟在原子替换阶段抛出 OSError
        with patch("os.replace", side_effect=OSError("模拟磁盘原子替换失败")):
            with self.assertRaises(SafeIOWriteError):
                write_file_safe(target_file, "非法覆写数据", encoding="utf-8")

        # 1. 验证原文件未被截断或损坏
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_text(encoding="utf-8"), initial_content)

        # 2. 验证临时文件已被安全清理（不存在 .tmp_ 前缀文件）
        temp_files = list(self.test_dir.glob(".tmp_*"))
        self.assertEqual(len(temp_files), 0, f"发现残留临时文件: {temp_files}")

        # 3. 模拟在编码写入阶段失败（例如以 ascii 编码写入非 ascii 文本）
        with self.assertRaises(SafeIOWriteError):
            write_file_safe(target_file, "中文内容无法用ascii编码", encoding="ascii")

        # 验证原文件依然完好且无残留临时文件
        self.assertEqual(target_file.read_text(encoding="utf-8"), initial_content)
        temp_files = list(self.test_dir.glob(".tmp_*"))
        self.assertEqual(len(temp_files), 0, f"编码失败后发现残留临时文件: {temp_files}")

    def test_create_atomic_backup_source_not_found(self):
        """测试源文件不存在时备份抛出 FileNotFoundError"""
        source_file = self.test_dir / "not_exist.md"
        backup_dir = self.test_dir / "backup"
        with self.assertRaises((FileNotFoundError, SafeIOReadError)):
            create_atomic_backup(source_file, backup_dir)

    def test_exception_hierarchy(self):
        """测试异常继承体系"""
        self.assertTrue(issubclass(SafeIOReadError, SafeIOError))
        self.assertTrue(issubclass(SafeIOWriteError, SafeIOError))
        self.assertTrue(issubclass(SafeIOError, Exception))


if __name__ == "__main__":
    unittest.main()
