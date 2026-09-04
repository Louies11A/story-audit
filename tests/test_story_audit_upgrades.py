# -*- coding: utf-8 -*-
"""
tests.test_story_audit_upgrades: 四大阶段全面升级与集成端到端测试套件
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.audit_state import load_audit_state
from scripts.author_memory import AuthorMemory
from scripts.story_audit import audit_chapter, audit_scope, run_audit, run_scope_audit


class TestStoryAuditUpgrades(unittest.TestCase):
    """测试 story-audit 全面升级管线"""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="story_audit_upgrades_"))
        self.text_dir = self.temp_dir / "正文"
        self.text_dir.mkdir(parents=True, exist_ok=True)

        # 写入测试章节 1
        (self.text_dir / "第001章_末世降临.md").write_text(
            "第001章 末世降临\n\n"
            "狂暴的海啸伴随刺耳的防空警报从天际呼啸压来，倒计时只剩最后五分钟！\n"
            "沈飞站在快艇上，冷眼看着被巨浪掀翻的黑旗帮快艇，战术机炮已经完成校射。\n"
            "【个人战舰属性面板】\n"
            "【载具：055型万吨大驱】\n"
            "【火控：已并网，重构点：120】\n\n"
            "机炮咆哮，一发精准的高爆穿甲弹瞬间击碎了冲在最前方的敌艇引擎。\n"
            "他走上舰桥，远眺着无边无际的深蓝风暴。\n",
            encoding="utf-8",
        )

        # 写入测试章节 2
        (self.text_dir / "第002章_深海阻击.md").write_text(
            "第002章 深海阻击\n\n"
            "风暴中的浪头超过了十米高，深海巨兽的阴影在水下两百米处隐隐浮现。\n"
            "沈飞神色冷静，按下了战术垂发井的启动按钮。\n"
            "八枚超音速反舰导弹呼啸升空，在夜空中拉出八道炽热的尾焰！\n"
            "轰然巨响中，滔天巨浪夹杂着巨兽的哀鸣撕裂了黑夜。\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_standardized_report_header_keys(self):
        """验证报告头部逐字输出固定英文键"""
        exit_code = run_audit(
            self.temp_dir,
            target_chapter_index=1,
            mode="full",
            platform="fanqie",
        )
        self.assertEqual(exit_code, 0)

        report_file = self.temp_dir / "reports" / "LATEST_REPORT.md"
        self.assertTrue(report_file.is_file())
        content = report_file.read_text(encoding="utf-8")

        # 校验固定英文元数据键
        self.assertIn("=== story-audit 深度审查报告 ===", content)
        self.assertIn("Requested Mode: full", content)
        self.assertIn("Effective Mode: full", content)
        self.assertIn("Fallback: none", content)
        self.assertIn("Platform Rubric: fanqie", content)
        self.assertIn("Genre: 科幻末世", content)
        self.assertIn("Scope: 第001章", content)

    def test_subagent_recursion_guard_in_audit_report(self):
        """测试在子代理环境中运行，报告元数据正确显示 solo 降级与原因"""
        with patch.dict(os.environ, {"STORY_AUDIT_SUBAGENT": "1"}):
            exit_code = run_audit(
                self.temp_dir,
                target_chapter_index=1,
                mode="full",
                platform="qidian",
            )
            self.assertEqual(exit_code, 0)

            content = (self.temp_dir / "reports" / "LATEST_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("Requested Mode: full", content)
            self.assertIn("Effective Mode: solo", content)
            self.assertIn("Fallback: subagent_recursion_guard_active", content)
            self.assertIn("Platform Rubric: qidian", content)

    def test_author_memory_integration(self):
        """测试作者偏好联动并写入预审包与审查报告"""
        # 初始化并录入偏好
        mem = AuthorMemory(self.temp_dir)
        mem.init()
        mem.record(
            key="武器风格",
            value="偏好硬核军工口径与精密机械构造描写",
            category="prose_style",
        )

        exit_code = run_audit(
            self.temp_dir,
            target_chapter_index=1,
            use_author_memory=True,
        )
        self.assertEqual(exit_code, 0)

        report_content = (self.temp_dir / "reports" / "LATEST_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("作者画像与偏好联动 (Author Memory)", report_content)
        self.assertIn("武器风格", report_content)
        self.assertIn("偏好硬核军工口径", report_content)
        self.assertIn("铁律约束", report_content)

        # 校验预审包中的作者记忆
        bundle_file = self.temp_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        bundle_data = json.loads(bundle_file.read_text(encoding="utf-8"))
        self.assertIn("武器风格", bundle_data["author_memory"])

    def test_cross_batch_state_machine_and_inherited_items(self):
        """测试跨批长篇因果闭环与 Inherited Items 状态机"""
        # 1. 运行第 1 批审查 (1-1)
        exit_code_b1 = run_scope_audit(
            self.temp_dir,
            scope_str="1-1",
            strict=False,
            force=False,
            platform="generic",
        )
        self.assertEqual(exit_code_b1, 0)

        # 验证 .audit_state.json 生成
        reports_dir = self.temp_dir / "reports"
        audit_state = load_audit_state(reports_dir)
        self.assertEqual(audit_state.last_scope, "1-1")
        self.assertIn(1.0, audit_state.completed_chapters)

        # 人为在状态机中注入上一批未决缺陷
        audit_state.open_defects.append({
            "chapter": 1.0,
            "severity": "P1",
            "category": "causal",
            "issue": "防空导弹发射后未扣除备弹库存",
            "fix": "【事实统一】在第2章核对并扣除8枚垂发导弹库存",
        })
        from scripts.audit_state import save_audit_state
        save_audit_state(audit_state, reports_dir)

        # 2. 启动第 2 批审查 (2-2)
        exit_code_b2 = run_scope_audit(
            self.temp_dir,
            scope_str="2-2",
            strict=False,
            force=False,
            platform="generic",
        )
        self.assertEqual(exit_code_b2, 0)

        # 验证第 2 批大盘报告自动继承并展示了 Inherited Items
        batch_report_file = reports_dir / "BATCH_SUMMARY_SCOPE_2-2.md"
        self.assertTrue(batch_report_file.is_file())
        batch_text = batch_report_file.read_text(encoding="utf-8")

        self.assertIn("跨批因果继承与未解决缺陷 (Inherited Items)", batch_text)
        self.assertIn("防空导弹发射后未扣除备弹库存", batch_text)
        self.assertIn("【事实统一】在第2章核对并扣除8枚垂发导弹库存", batch_text)

        # 验证状态机已原子更新第2章完成状态
        new_state = load_audit_state(reports_dir)
        self.assertEqual(new_state.last_scope, "2-2")
        self.assertIn(2.0, new_state.completed_chapters)


if __name__ == "__main__":
    unittest.main()
