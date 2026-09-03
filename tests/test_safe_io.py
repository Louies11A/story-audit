import unittest
import tempfile
import re
import codecs
from pathlib import Path
from scripts.safe_io import (
    SafeIOReadError,
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

    def test_create_atomic_backup_source_not_found(self):
        """测试源文件不存在时备份抛出 FileNotFoundError"""
        source_file = self.test_dir / "not_exist.md"
        backup_dir = self.test_dir / "backup"
        with self.assertRaises((FileNotFoundError, SafeIOReadError)):
            create_atomic_backup(source_file, backup_dir)


if __name__ == "__main__":
    unittest.main()
