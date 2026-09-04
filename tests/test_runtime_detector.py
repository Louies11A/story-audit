# -*- coding: utf-8 -*-
"""
tests.test_runtime_detector: 宿主运行时探测与子代理递归防爆哨兵测试套件
"""

import os
import unittest
from unittest.mock import patch

from scripts.runtime_detector import (
    detect_runtime,
    is_subagent_context,
    resolve_execution_mode,
)


class TestRuntimeDetector(unittest.TestCase):
    """测试宿主环境自适应探测与哨兵拦截"""

    def test_detect_runtime_environments(self):
        """测试不同平台特征环境变量的精准探测"""
        # Codex
        with patch.dict(os.environ, {"CODEX_HOME": "C:\\Users\\test\\.codex"}, clear=True):
            self.assertEqual(detect_runtime(), "codex")

        # Claude Code
        with patch.dict(os.environ, {"CLAUDE_CODE": "1"}, clear=True):
            self.assertEqual(detect_runtime(), "claude")

        # OpenCode
        with patch.dict(os.environ, {"OPENCODE_CLIENT": "opencode-v1"}, clear=True):
            self.assertEqual(detect_runtime(), "opencode")

        # Antigravity
        with patch.dict(os.environ, {"ANTIGRAVITY_AGENT": "antigravity"}, clear=True):
            self.assertEqual(detect_runtime(), "antigravity")

        # Generic (纯 Shell 命令行)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(detect_runtime(), "generic")

    def test_subagent_recursion_guard_active(self):
        """核心哨兵防爆测试：处在子代理上下文中时，强制禁止嵌套 spawn，平稳降级为 solo"""
        # 1. 模拟子代理特征环境变量
        with patch.dict(os.environ, {"STORY_AUDIT_SUBAGENT": "1"}):
            self.assertTrue(is_subagent_context())

            # 请求 full 模式被强制降级
            eff_mode, reason = resolve_execution_mode("full", runtime="codex")
            self.assertEqual(eff_mode, "solo")
            self.assertEqual(reason, "subagent_recursion_guard_active")

            # 请求 auto 模式被强制降级
            eff_mode_auto, reason_auto = resolve_execution_mode("auto", runtime="claude")
            self.assertEqual(eff_mode_auto, "solo")
            self.assertEqual(reason_auto, "subagent_recursion_guard_active")

            # 原本请求 solo 模式保持 solo 且无 fallback 告警
            eff_mode_solo, reason_solo = resolve_execution_mode("solo", runtime="codex")
            self.assertEqual(eff_mode_solo, "solo")
            self.assertEqual(reason_solo, "none")

    def test_mode_resolution_matrix(self):
        """测试多环境下的弹性自适应三级降级决策矩阵"""
        with patch.dict(os.environ, {}, clear=True):
            # 1. Codex / Claude 支持 full 多代理协同
            mode, r = resolve_execution_mode("auto", runtime="codex")
            self.assertEqual((mode, r), ("full", "none"))

            mode, r = resolve_execution_mode("auto", runtime="claude")
            self.assertEqual((mode, r), ("full", "none"))

            # 2. OpenCode / Antigravity 降级为 lean
            mode, r = resolve_execution_mode("auto", runtime="opencode")
            self.assertEqual((mode, r), ("lean", "none"))

            mode, r = resolve_execution_mode("auto", runtime="antigravity")
            self.assertEqual((mode, r), ("lean", "none"))

            # 3. Generic 命令行环境降级为 solo
            mode, r = resolve_execution_mode("auto", runtime="generic")
            self.assertEqual((mode, r), ("solo", "unsupported_multiagent_runtime"))

            # 4. 在 Generic 环境下显式请求 full 触发降级
            mode, r = resolve_execution_mode("full", runtime="generic")
            self.assertEqual((mode, r), ("solo", "runtime_not_supporting_subagents"))


if __name__ == "__main__":
    unittest.main()
