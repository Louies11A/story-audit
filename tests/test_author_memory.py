# -*- coding: utf-8 -*-
"""
tests.test_author_memory: 作者偏好记忆状态机测试套件
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.author_memory import (
    AntiInbreedingViolation,
    AuthorMemory,
    AuthorMemoryError,
    check_anti_inbreeding,
)


class TestAuthorMemory(unittest.TestCase):
    """测试作者偏好状态机"""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="author_mem_test_"))
        self.mem = AuthorMemory(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_and_profile_view(self):
        """测试 init 命令初始化 JSON 与只读 Markdown 视图"""
        state_file = self.mem.init()
        self.assertTrue(state_file.exists())
        self.assertTrue(self.mem.profile_file.exists())

        profile_text = self.mem.profile_file.read_text(encoding="utf-8")
        self.assertIn("作者画像与创作偏好档案", profile_text)
        self.assertIn("记忆纪律铁律", profile_text)

    def test_record_and_persistence(self):
        """测试正常记录作者偏好并同步更新画像视图"""
        self.mem.init()
        item = self.mem.record(
            key="主角性格",
            value="果决冷静，杀伐果断，不圣母不多话",
            category="story_design",
            source="explicit_user",
            confidence="high",
        )
        self.assertEqual(item["key"], "主角性格")
        self.assertEqual(item["category"], "story_design")

        # 验证 JSON 持久化
        state = self.mem.load_state()
        self.assertIn(item["id"], state["preferences"])
        self.assertEqual(state["preferences"][item["id"]]["value"], "果决冷静，杀伐果断，不圣母不多话")

        # 验证只读视图自动渲染
        profile_text = self.mem.profile_file.read_text(encoding="utf-8")
        self.assertIn("主角性格", profile_text)
        self.assertIn("果决冷静", profile_text)

    def test_anti_inbreeding_guard(self):
        """核心铁律：坚决不学习系统自身的审查警告/模板话术（反近亲繁殖）"""
        self.mem.init()

        # 包含 P0 错误特征
        with self.assertRaises(AntiInbreedingViolation):
            self.mem.record(
                key="审查建议",
                value="检测到 P0 级致命断裂，建议重写",
                category="prose_style",
            )

        # 包含 FormatFinding / 排版代码特征
        with self.assertRaises(AntiInbreedingViolation):
            self.mem.record(
                key="FormatFinding规则",
                value="DRAGGING_SENTENCE 提示单句逗号过多",
                category="delivery",
            )

        # 包含内部状态机特征
        with self.assertRaises(AntiInbreedingViolation):
            self.mem.record(
                key="出装审计发现",
                value="触发账本冲突与防脏写拦截",
                category="workflow",
            )

    def test_query_and_hard_ceiling_2048_bytes(self):
        """记忆铁律：查询结果硬上限 <= 2048 字节"""
        self.mem.init()

        # 注入大量偏好条目
        for i in range(40):
            self.mem.record(
                key=f"偏好规则_{i:02d}",
                value=f"这是第 {i:02d} 条作者创作习惯约定，用于测试字节截断保护机制，确保输出精炼。",
                category="prose_style",
            )

        output = self.mem.query(limit_bytes=2048)
        encoded_bytes = len(output.encode("utf-8"))

        self.assertLessEqual(encoded_bytes, 2048, "查询结果必须严格受控在 <= 2048 字节！")
        self.assertIn("铁律约束：本上下文仅作为作者写作意图解释辅助", output)
        self.assertIn("文风与表达", output)

    def test_check_integrity(self):
        """测试 check 命令状态健康校验"""
        self.assertFalse(self.mem.check())
        self.mem.init()
        self.assertTrue(self.mem.check())

        # 损坏分类字段应触发校验异常
        state = self.mem.load_state()
        state["preferences"]["corrupt_item"] = {
            "id": "corrupt_item",
            "key": "测试",
            "value": "内容",
            "category": "invalid_category",
        }
        self.mem._save_state(state)
        with self.assertRaises(AuthorMemoryError):
            self.mem.check()


if __name__ == "__main__":
    unittest.main()
