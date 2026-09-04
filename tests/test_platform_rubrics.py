# -*- coding: utf-8 -*-
"""
tests.test_platform_rubrics: 平台商业门禁质量卡尺评估测试套件
"""

import unittest
from scripts.platform_rubrics import evaluate_platform_rubric


class TestPlatformRubrics(unittest.TestCase):
    """测试四大平台专属卡尺与门禁规则"""

    def test_fanqie_rubric_first_3_paras_hook(self):
        """测试番茄小说前3段核心悬念与危机钩子门禁"""
        # 1. 缺乏冲突与钩子的纯背景交代（触发 P2）
        flat_text = (
            "清晨的阳光洒在小巷深处，微风拂过柳梢。\n"
            "这里是江城的一处老旧居民区，住着许多普普通通的工薪阶层。\n"
            "街道两旁的商铺刚刚开门营业，蒸笼里冒出腾腾的热气。\n"
            "林峰静静地坐在阳台前看着风景。\n"
        )
        res_flat = evaluate_platform_rubric(flat_text, platform="fanqie", chapter_index=1)
        self.assertFalse(res_flat["metrics"]["first_3_paras_hook"])
        p_flaws = [f for f in res_flat["findings"] if "前3段" in f.location]
        self.assertEqual(len(p_flaws), 1)
        self.assertEqual(p_flaws[0].severity, "P2")

        # 2. 开篇即爆发核心危机与动作冲突（PASS 绿灯）
        hook_text = (
            "刺耳的防空警报在深夜骤然撕裂夜空，倒计时只剩最后五分钟！\n"
            "门外传来了变异巨兽的狂暴咆哮声，铁门正在被疯狂撞击变形。\n"
            "林峰一把抓起腰间的战术猎枪，眼眸中寒芒暴涨！\n"
        )
        res_hook = evaluate_platform_rubric(hook_text, platform="fanqie", chapter_index=1)
        self.assertTrue(res_hook["metrics"]["first_3_paras_hook"])
        p_flaws_hook = [f for f in res_hook["findings"] if "前3段" in f.location]
        self.assertEqual(len(p_flaws_hook), 0)

    def test_qidian_rubric_gold_finger_presence(self):
        """测试起点中文网金手指在场率与爽点节点"""
        # 1. 全程无金手指（触发 P2 门禁预警）
        plain_text = "他今天去集市买了一袋米，又去铁匠铺修补了农具，随后回到茅草屋生火做饭。\n" * 10
        res_plain = evaluate_platform_rubric(plain_text, platform="qidian", chapter_index=1)
        gf_flaws = [f for f in res_plain["findings"] if "金手指" in f.issue]
        self.assertEqual(len(gf_flaws), 1)
        self.assertEqual(gf_flaws[0].severity, "P2")

        # 2. 包含系统面板与力量推演（PASS）
        gold_text = (
            "【系统面板加载完毕】\n"
            "宿主：林峰，当前境界：练气三层，技能点已到账十点。\n"
            "他心念微动，瞬间将功法推演到了极致，神识暴涨十倍！\n"
        ) * 5
        res_gold = evaluate_platform_rubric(gold_text, platform="qidian", chapter_index=1)
        gf_flaws_pass = [f for f in res_gold["findings"] if "金手指" in f.issue]
        self.assertEqual(len(gf_flaws_pass), 0)

    def test_zhihu_rubric_first_person_and_hook(self):
        """测试知乎盐言故事强第一人称与首句跳失率门禁"""
        # 1. 第三人称主导文本在知乎卡尺下触发 P1 严重违规
        third_person_text = (
            "他走在回家的路上，心里感到一阵凄凉。\n"
            "她没有回头看他一眼，径直上了一辆黑色的轿车。\n"
            "他想冲上去质问她，但理智告诉他一切都太迟了。\n"
        ) * 10
        res_third = evaluate_platform_rubric(third_person_text, platform="zhihu", chapter_index=1)
        self.assertFalse(res_third["passed"])
        p1_flaws = [f for f in res_third["findings"] if f.severity == "P1"]
        self.assertEqual(len(p1_flaws), 1)
        self.assertIn("强第一人称", p1_flaws[0].issue)
        # 验证事实性 fix 格式约束
        self.assertTrue(p1_flaws[0].fix.startswith("【事实对齐】"))

        # 2. 首句风景描写开篇（触发 P2 首句跳失率门禁）
        scenery_text = (
            "秋风瑟瑟，落叶落满了整座小院。\n"
            "我看着桌上冷掉的鸡汤，心中终于下定了决心。\n"
        ) * 10
        res_scene = evaluate_platform_rubric(scenery_text, platform="zhihu", chapter_index=1)
        p2_scene = [f for f in res_scene["findings"] if "首句" in f.location]
        self.assertEqual(len(p2_scene), 1)
        self.assertEqual(p2_scene[0].severity, "P2")


if __name__ == "__main__":
    unittest.main()
