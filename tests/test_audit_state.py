# -*- coding: utf-8 -*-
"""
tests.test_audit_state: 跨批长篇因果状态机测试套件
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.audit_state import (
    AuditState,
    get_audit_state_path,
    get_inherited_items,
    load_audit_state,
    render_inherited_items_section,
    save_audit_state,
)


class TestAuditState(unittest.TestCase):
    """测试跨批因果状态机持久化与继承栈"""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="audit_state_test_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_load_state_atomic(self):
        """测试原子写入与读取状态机数据"""
        state = AuditState(
            last_scope="1-5",
            completed_chapters=[1.0, 2.0, 3.0, 4.0, 5.0],
            open_defects=[
                {
                    "chapter": 3.0,
                    "severity": "P1",
                    "category": "causal",
                    "issue": "雷达站声呐未交代交付",
                    "fix": "【事实统一】在第4章补齐声呐入库交接",
                }
            ],
            foreshadowing_commitments=[
                {
                    "tag": "军用声呐",
                    "origin_chapter": 2.0,
                    "status": "pending",
                    "note": "二号掩体声呐待打捞",
                }
            ],
        )

        state_file = save_audit_state(state, self.temp_dir)
        self.assertTrue(state_file.is_file())
        self.assertEqual(state_file, get_audit_state_path(self.temp_dir))

        loaded = load_audit_state(self.temp_dir)
        self.assertEqual(loaded.last_scope, "1-5")
        self.assertEqual(loaded.completed_chapters, [1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(len(loaded.open_defects), 1)
        self.assertEqual(loaded.open_defects[0]["issue"], "雷达站声呐未交代交付")
        self.assertEqual(len(loaded.foreshadowing_commitments), 1)

    def test_inherit_items_rendering(self):
        """测试 Inherited Items 继承栈提取与 Markdown 渲染"""
        state = AuditState(
            last_scope="1-30",
            completed_chapters=[float(i) for i in range(1, 31)],
            open_defects=[
                {
                    "chapter": 15.0,
                    "severity": "P1",
                    "category": "causal",
                    "issue": "战舰主炮备弹数量与仓库账本不符",
                    "fix": "【事实统一】核对主炮消耗弹药账本",
                }
            ],
            foreshadowing_commitments=[
                {
                    "tag": "深海古遗迹海图",
                    "origin_chapter": 29.0,
                    "status": "pending",
                    "note": "远洋航行关键线索",
                }
            ],
        )

        inherited = get_inherited_items(state)
        self.assertEqual(inherited["last_scope"], "1-30")
        self.assertEqual(len(inherited["open_defects"]), 1)
        self.assertEqual(len(inherited["foreshadowing_commitments"]), 1)

        section_md = render_inherited_items_section(inherited)
        self.assertIn("跨批因果继承与未解决缺陷 (Inherited Items)", section_md)
        self.assertIn("战舰主炮备弹数量与仓库账本不符", section_md)
        self.assertIn("深海古遗迹海图", section_md)


if __name__ == "__main__":
    unittest.main()
