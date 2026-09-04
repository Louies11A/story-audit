# -*- coding: utf-8 -*-
"""
tests/test_story_audit_cli.py: CLI 总入口、预审包构建与退出码管线单元测试

测试覆盖：
1. 参数解析契约 (--project, --chapter, --scope, --init, --checkpoint, --volume, --sync-from-md, --apply-fix, --force, --strict)
2. 预审包标准结构生成 (reports/.cache/pre_audit_bundle.json 冻结字段与类型契约)
3. 报告归档与生成 (reports/LATEST_REPORT.md 与 reports/单章审查/001-100章/第XXX章_审查报告.md)
4. 标准退出状态码映射：
   - Exit Code 0: 绿灯（全通或仅 P2/P3，或有 P1 但未开 --strict）
   - Exit Code 1: 警告（发现 P1 且开启 --strict）
   - Exit Code 2: 阻断（发现 P0 致命断裂）
   - Exit Code 3: 系统错误（参数不合法、文件找不到、防脏写未加 --force、回写消歧失败等）
5. 辅助模式与安全回写管线：
   - --sync-from-md 反向增量同步
   - --checkpoint --volume 分卷封账结转
   - --init 首次建账模式
   - --apply-fix 安全回写与消歧防护
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.safe_io import write_file_safe, read_file_safe
from scripts.ledger_engine import LedgerState, AssetItem, save_ledger_state
from scripts.story_audit import (
    main,
    build_pre_audit_bundle,
    run_audit,
    get_report_archive_path,
    locate_ledger_paths,
    load_ledger_state,
)


class TestStoryAuditCLI(unittest.TestCase):
    """CLI 执行管线与退出码测试套件"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)

        # 构建标准小说工作区目录
        self.drafts_dir = self.project_dir / "正文" / "第一卷"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.settings_dir = self.project_dir / "设定"
        self.settings_dir.mkdir(parents=True, exist_ok=True)

        # 写入标准测试章节
        self.chap1_path = self.drafts_dir / "第001章_初入仙途.md"
        self.chap1_content = (
            "第001章 初入仙途\n\n"
            "陆离握紧了手中的青钢剑。这是他唯一的兵刃。\n\n"
            "<!-- audit:stash name=\"青钢剑\" origin=\"第1章\" status=\"EQUIPPED\" -->\n"
            "山风呼啸，前路漫漫，他必须活下去。\n"
        )
        write_file_safe(self.chap1_path, self.chap1_content)

        self.chap2_path = self.drafts_dir / "第002章_剑试深渊.md"
        self.chap2_content = (
            "第002章 剑试深渊\n\n"
            "深渊之中，雾气弥漫。陆离拔出佩剑，警惕着四周。\n\n"
            "黑暗中有猩红的眼眸亮起，杀机骤现。\n"
        )
        write_file_safe(self.chap2_path, self.chap2_content)

        # 写入初始账本
        self.ledger_json = self.settings_dir / "资源账本.json"
        self.ledger_md = self.settings_dir / "资源账本.md"
        self.state = LedgerState(
            last_updated_chapter=1.0,
            assets={
                "asset_sword": AssetItem(
                    id="asset_sword",
                    name="青钢剑",
                    category="装备道具",
                    quantity=1,
                    unit="柄",
                    owner="陆离",
                    current_holder="陆离",
                    status="EQUIPPED",
                    origin_chapter=1.0,
                )
            },
            foreshadowing_stash=[{"name": "青钢剑", "origin": "第1章", "status": "EQUIPPED"}],
        )
        save_ledger_state(self.state, self.ledger_json, self.ledger_md, force=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_audit_latest_chapter_success(self):
        """默认模式：审查最新章节（第2章），无严重违规返回 0，生成预审包与报告"""
        exit_code = main(["--project", str(self.project_dir)])
        self.assertEqual(exit_code, 0)

        # 检查预审包
        bundle_path = self.project_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        self.assertTrue(bundle_path.exists(), "预审包 pre_audit_bundle.json 必须自动生成")

        with open(bundle_path, encoding="utf-8") as f:
            bundle = json.load(f)

        # 验证冻结契约 Schema
        self.assertIn("meta", bundle)
        self.assertEqual(bundle["meta"]["version"], "1.0")
        self.assertIn("generated_at", bundle["meta"])
        self.assertEqual(bundle["meta"]["target_chapter"], 2.0)
        self.assertIn("encoding", bundle["meta"])
        self.assertIn("newline", bundle["meta"])

        self.assertIn("sequence_diagnostics", bundle)
        self.assertIn("has_gap", bundle["sequence_diagnostics"])
        self.assertIn("gap_warnings", bundle["sequence_diagnostics"])
        self.assertFalse(bundle["sequence_diagnostics"]["has_gap"])

        self.assertIn("boundary", bundle)
        self.assertTrue(bundle["boundary"]["has_prev_chapter"])
        self.assertIn("prev_tail_300", bundle["boundary"])
        self.assertIn("curr_head_300", bundle["boundary"])
        self.assertIn("is_pov_transition", bundle["boundary"])
        self.assertIn("transition_clue", bundle["boundary"])
        self.assertIn("isolation_zones", bundle["boundary"])

        self.assertIn("ledger_snapshot", bundle)
        self.assertIn("active_assets", bundle["ledger_snapshot"])
        self.assertIn("foreshadowing_stash", bundle["ledger_snapshot"])
        self.assertEqual(len(bundle["ledger_snapshot"]["active_assets"]), 1)

        self.assertIn("format_scan", bundle)
        self.assertIn("total_flaws", bundle["format_scan"])
        self.assertIn("findings", bundle["format_scan"])

        # 检查报告归档
        latest_report = self.project_dir / "reports" / "LATEST_REPORT.md"
        self.assertTrue(latest_report.exists(), "LATEST_REPORT.md 必须生成")
        archived_report = self.project_dir / "reports" / "单章审查" / "001-100章" / "第002章_审查报告.md"
        self.assertTrue(archived_report.exists(), "单章归档报告必须正确创建")

    def test_audit_specific_chapter(self):
        """指定 --chapter 参数审查第 1 章"""
        exit_code = main(["--project", str(self.project_dir), "--chapter", "1"])
        self.assertEqual(exit_code, 0)

        bundle_path = self.project_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        with open(bundle_path, encoding="utf-8") as f:
            bundle = json.load(f)
        self.assertEqual(bundle["meta"]["target_chapter"], 1.0)
        self.assertFalse(bundle["boundary"]["has_prev_chapter"])

        archived_report = self.project_dir / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md"
        self.assertTrue(archived_report.exists())

    def test_exit_code_p2_flaws_returns_0(self):
        """正文中仅有 P2/P3 排版问题时，保持 Exit Code 0（绿灯）"""
        long_para = "少年站在高崖之上眺望着无边无际的云海翻涌心中升起无限感慨天地如此广阔而人身如蝼蚁渺小不知何时才能登临绝顶傲视诸天神魔俯瞰九天十地六道轮回之苦痛与苍茫沧海桑田悠悠千载不过大梦一场！"
        p2_content = f"第003章 登高感慨\n\n{long_para}\n"
        chap3_path = self.drafts_dir / "第003章_登高感慨.md"
        write_file_safe(chap3_path, p2_content)

        exit_code = main(["--project", str(self.project_dir), "--chapter", "3"])
        self.assertEqual(exit_code, 0)

        bundle_path = self.project_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        with open(bundle_path, encoding="utf-8") as f:
            bundle = json.load(f)
        self.assertGreater(bundle["format_scan"]["total_flaws"], 0)

    def test_exit_code_p1_non_strict_returns_0_strict_returns_1(self):
        """P1 严重失误：未开 --strict 时放行(0)，开启 --strict 时中断(1)"""
        p1_content = (
            "第004章 神兵天降\n\n"
            "<!-- audit:violation level=\"P1\" message=\"凭空出装：突然出现诛仙剑\" -->\n"
            "陆离手中突然多了一把诛仙剑，神威凛凛。\n"
        )
        chap4_path = self.drafts_dir / "第004章_神兵天降.md"
        write_file_safe(chap4_path, p1_content)

        # 未开启 --strict -> 0
        code_loose = main(["--project", str(self.project_dir), "--chapter", "4"])
        self.assertEqual(code_loose, 0)

        # 开启 --strict -> 1
        code_strict = main(["--project", str(self.project_dir), "--chapter", "4", "--strict"])
        self.assertEqual(code_strict, 1)

    def test_exit_code_p0_blocked_returns_2(self):
        """P0 致命断裂：强制阻断，返回 Exit Code 2"""
        p0_content = (
            "第005章 亡者逆行\n\n"
            "<!-- audit:violation level=\"P0\" message=\"死人无伏笔复活，主线战力崩坏\" -->\n"
            "早已被斩首的青阳长老居然活生生地站在门前。\n"
        )
        chap5_path = self.drafts_dir / "第005章_亡者逆行.md"
        write_file_safe(chap5_path, p0_content)

        # 即使未加 --strict，P0 必须阻断并返回 2
        exit_code = main(["--project", str(self.project_dir), "--chapter", "5"])
        self.assertEqual(exit_code, 2)

    def test_exit_code_file_not_found_returns_3(self):
        """请求不存在的章节号，返回 Exit Code 3"""
        exit_code = main(["--project", str(self.project_dir), "--chapter", "999"])
        self.assertEqual(exit_code, 3)

    def test_exit_code_invalid_params_returns_3(self):
        """非法参数（如 checkpoint 缺少 volume）返回 Exit Code 3"""
        exit_code = main(["--project", str(self.project_dir), "--checkpoint"])
        self.assertEqual(exit_code, 3)

    def test_dirty_state_blocking_and_force_override(self):
        """防脏写测试：Markdown 比 JSON 新时阻断(3)；加 --force 强制放行(0)"""
        time.sleep(0.05)
        md_text, _, _ = read_file_safe(self.ledger_md)
        md_text += "\n<!-- 外部手工改动 -->\n"
        write_file_safe(self.ledger_md, md_text)
        new_mtime = self.ledger_json.stat().st_mtime + 5.0
        os.utime(self.ledger_md, (new_mtime, new_mtime))

        # 未加 --force：触发防脏写阻断，Exit Code 3
        exit_code = main(["--project", str(self.project_dir), "--chapter", "2"])
        self.assertEqual(exit_code, 3)

        # 加上 --force：忽略警告，Exit Code 0
        exit_code_force = main(["--project", str(self.project_dir), "--chapter", "2", "--force"])
        self.assertEqual(exit_code_force, 0)

    def test_sync_from_md_pipeline(self):
        """--sync-from-md 反向同步管线测试"""
        extra_md = (
            "# 资源账本（更新至 2.0 章）\n\n"
            "| 资产ID | 名称 | 类别 | 数量 | 单位 | 所有者 | 当前持有者 | 状态 | 初始章节 | 借出元数据 | 约束条件 | 历史流转 |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
            "| asset_sword | 青钢剑 | 装备道具 | 1 | 柄 | 陆离 | 陆离 | EQUIPPED | 1.0 | - | - | 1.0章 获得 |\n"
            "| asset_pill | 回春丹 | 丹药耗材 | 3 | 枚 | 陆离 | 陆离 | ACQUIRED | 2.0 | - | - | 2.0章 获得 |\n"
        )
        write_file_safe(self.ledger_md, extra_md)

        exit_code = main(["--project", str(self.project_dir), "--sync-from-md"])
        self.assertEqual(exit_code, 0)

        state_dict = json.loads(self.ledger_json.read_text(encoding="utf-8"))
        self.assertIn("asset_pill", state_dict["assets"])
        self.assertEqual(state_dict["assets"]["asset_pill"]["quantity"], 3)

    def test_checkpoint_volume_pipeline(self):
        """--checkpoint --volume 分卷封账结转测试"""
        exit_code = main(["--project", str(self.project_dir), "--checkpoint", "--volume", "1"])
        self.assertEqual(exit_code, 0)

        archive_path = self.project_dir / "设定" / "archive" / "volume_01_ledger.json"
        self.assertTrue(archive_path.exists())

    def test_init_mode_pipeline(self):
        """--init --scope 首次建账模式"""
        if self.ledger_json.exists(): self.ledger_json.unlink()
        if self.ledger_md.exists(): self.ledger_md.unlink()

        exit_code = main(["--project", str(self.project_dir), "--init", "--scope", "1-2"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.ledger_json.exists())
        self.assertTrue(self.ledger_md.exists())

        report_path = self.project_dir / "reports" / "阶段封账与里程碑" / "初始建账盘点报告_第001-002章.md"
        self.assertTrue(report_path.exists())

    def test_scope_batch_audit_pipeline(self):
        """--scope 31-35 批量连审模式"""
        exit_code = main(["--project", str(self.project_dir), "--scope", "1-2"])
        self.assertEqual(exit_code, 0)

        batch_reports = list((self.project_dir / "reports" / "批量审查").glob("*_批量审查_第001-002章.md"))
        self.assertTrue(len(batch_reports) >= 1)

    def test_apply_fix_pipeline_success(self):
        """--apply-fix 采纳修复方案号回写成功"""
        exit_code = main([
            "--project", str(self.project_dir),
            "--chapter", "2",
            "--apply-fix", "1",
            "--target-line", "5",
            "--old-text", "黑暗中有猩红的眼眸亮起，杀机骤现。",
            "--new-text", "黑暗中有猩红的眼眸亮起，杀意如潮。",
            "--context-before", "深渊之中，雾气弥漫。陆离拔出佩剑，警惕着四周。",
            "--context-after", "",
        ])
        self.assertEqual(exit_code, 0)

        new_content, _, _ = read_file_safe(self.chap2_path)
        self.assertIn("杀意如潮。", new_content)
        self.assertNotIn("杀机骤现。", new_content)

        bak_files = list((self.project_dir / "reports" / ".bak").glob("*.bak"))
        self.assertTrue(len(bak_files) >= 1)

    def test_apply_fix_pipeline_ambiguous_fails(self):
        """--apply-fix 遭遇消歧失败或锚点未找到时返回 Exit Code 3"""
        exit_code = main([
            "--project", str(self.project_dir),
            "--chapter", "2",
            "--apply-fix", "1",
            "--target-line", "5",
            "--old-text", "不存在的旧文本",
            "--new-text", "新文本",
        ])
        self.assertEqual(exit_code, 3)

    def test_direct_script_file_execution(self):
        """验证通过脚本物理文件直接执行 python scripts/story_audit.py 的正常运作"""
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "story_audit.py"
        res = subprocess.run(
            [sys.executable, str(script_path), "--project", str(self.project_dir), "--chapter", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("审查完成", res.stdout)

    def test_subprocess_real_execution(self):
        """验证通过 CLI 真实子进程调用的退出码与行为"""
        cmd = [
            sys.executable,
            "-m",
            "scripts.story_audit",
            "--project",
            str(self.project_dir),
            "--chapter",
            "1",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(res.returncode, 0)


    def test_init_mode_inherits_existing_assets(self):
        """测试已有资产的账本运行 --init 验证资产不丢失继承"""
        json_path, md_path = locate_ledger_paths(self.project_dir)
        state = LedgerState()
        item = AssetItem(
            id="sword_init_001",
            name="太虚古剑",
            category="装备道具",
            quantity=1,
            unit="柄",
            owner="主角",
        )
        state.assets["sword_init_001"] = item
        save_ledger_state(state, json_path, md_path, force=True)

        # 运行 --init
        exit_code = main([
            "--project", str(self.project_dir),
            "--init",
            "--force",
        ])
        self.assertEqual(exit_code, 0)

        # 验证资产未丢失，被继承保留
        reloaded_state = load_ledger_state(json_path)
        self.assertIn("sword_init_001", reloaded_state.assets)
        self.assertEqual(reloaded_state.assets["sword_init_001"].name, "太虚古剑")

    def test_locate_ledger_paths_priority_with_empty_settings_dir(self):
        """测试优先检查真实文件：根目录下存在账本但设定目录为空时，优先命中根目录真实文件"""
        test_dir = self.project_dir / "test_locate_prio"
        test_dir.mkdir(parents=True, exist_ok=True)
        # 根目录创建真实文件
        root_json = test_dir / "资源账本.json"
        root_json.write_text("{}", encoding="utf-8")
        # 仅创建空设定目录
        empty_settings = test_dir / "设定"
        empty_settings.mkdir(parents=True, exist_ok=True)

        j_path, m_path = locate_ledger_paths(test_dir)
        self.assertEqual(j_path, root_json, "必须优先匹配真实存在的根目录账本文件")

    def test_pre_audit_bundle_as_posix_and_history_truncation(self):
        """测试预审包路径统一 as_posix() 以及 history 流水截断保留最近记录"""
        from scripts.chapter_resolver import ChapterItem
        from scripts.types import BoundaryContext

        chap = ChapterItem(index=1.0, title="第一章", raw_name="第1章.txt", path=self.project_dir / "正文" / "第1章.txt")
        state = LedgerState()
        long_history = [{"action": f"event_{i}", "step": i} for i in range(10)]
        item = AssetItem(
            id="item_history_test",
            name="天罡戒",
            category="装备道具",
            quantity=1,
            unit="枚",
            history=long_history,
        )
        state.assets["item_history_test"] = item

        bundle = build_pre_audit_bundle(
            project_dir=self.project_dir,
            curr_chapter=chap,
            prev_chapter=None,
            chapters=[chap],
            state=state,
            findings=[],
            boundary_ctx=BoundaryContext(prev_tail_300="", curr_head_300="", has_prev_chapter=False, is_pov_transition=False),
            curr_enc="utf-8",
            curr_eol="\n",
            gap_warnings=[],
        )

        # 验证路径无 Windows 反斜杠
        self.assertNotIn("\\", bundle["meta"]["target_file"])
        self.assertEqual(bundle["meta"]["target_file"], "正文/第1章.txt")

        # 验证 history 截断为最近 5 条
        asset_info = bundle["ledger_snapshot"]["active_assets"][0]
        self.assertEqual(len(asset_info["history"]), 5)
        self.assertEqual(asset_info["history"][-1]["action"], "event_9")

    def test_float_chapter_matching_tolerance(self):
        """测试浮点章节匹配使用 abs(c.index - target) < 1e-4 容差"""
        # 传入带有微小浮点误差的章节号 1.0000001
        exit_code = run_audit(self.project_dir, target_chapter_index=1.0000001)
        self.assertEqual(exit_code, 0)


    def test_init_mode_heuristic_assets_integration(self):
        """测试 --init 首次建账模式将启发式抽取资产与伏笔自动并入账本"""
        # 在正文章节中写入包含出装、军工物资与自然伏笔的内容
        chap_extra_path = self.drafts_dir / "第003章_物资大丰收.md"
        chap_extra_content = (
            "第003章 物资大丰收\n\n"
            "在水下掩体中，清点战利品，清点出四台德国进口的五轴数控机床与上百吨特种防弹钢！\n"
            "搜刮到了80只重型军用防水弹药箱，还有1门76毫米速射炮与百吨大米。\n"
            "【获得：二阶双体炮艇改装蓝图×1】\n"
            "【伏笔:深海第三层异动】\n"
        )
        write_file_safe(chap_extra_path, chap_extra_content)

        if self.ledger_json.exists(): self.ledger_json.unlink()
        if self.ledger_md.exists(): self.ledger_md.unlink()

        exit_code = main(["--project", str(self.project_dir), "--init", "--force"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.ledger_json.exists())
        self.assertTrue(self.ledger_md.exists())

        state = load_ledger_state(self.ledger_json)
        # 验证账本中不再是空账本，已安全并入候选资产
        self.assertTrue(len(state.assets) >= 4, "自然网文首次建账必须安全并入候选资产，杜绝空账本")

        asset_names = {item.name: item for item in state.assets.values()}
        self.assertTrue(any("五轴数控机床" in k for k in asset_names.keys()))
        self.assertTrue(any("大米" in k for k in asset_names.keys()))
        self.assertTrue(any("弹药箱" in k for k in asset_names.keys()))

        # 验证伏笔池自动提取
        f_names = [f["name"] for f in state.foreshadowing_stash]
        self.assertIn("深海第三层异动", f_names)

        # 验证盘点报告生成且内容丰富
        stage_reports = list((self.project_dir / "reports" / "阶段封账与里程碑").glob("初始建账盘点报告_*.md"))
        self.assertTrue(len(stage_reports) >= 1)
        report_txt, _, _ = read_file_safe(stage_reports[0])
        self.assertIn("核心资产清册预览", report_txt)

    def test_scope_batch_summary_report_and_latest_report(self):
        """测试 --scope 批量连审模式自动聚合输出 BATCH_SUMMARY_SCOPE_{scope}.md 且避免无脑覆盖 LATEST_REPORT.md"""
        exit_code = main(["--project", str(self.project_dir), "--scope", "1-2"])
        self.assertEqual(exit_code, 0)

        # 1. 验证输出指定报告 reports/BATCH_SUMMARY_SCOPE_1-2.md
        batch_summary_path = self.project_dir / "reports" / "BATCH_SUMMARY_SCOPE_1-2.md"
        self.assertTrue(batch_summary_path.exists(), "必须自动生成 reports/BATCH_SUMMARY_SCOPE_{scope}.md")

        summary_txt, _, _ = read_file_safe(batch_summary_path)
        # 验证四大核心版块
        self.assertIn("## 一、全范围总览大盘", summary_txt)
        self.assertIn("## 二、字数与段数统计走势", summary_txt)
        self.assertIn("## 三、各章 P0/P1/P2/P3 瑕疵汇总列表", summary_txt)
        self.assertIn("## 四、跨章接缝与 POV 视点一览表", summary_txt)

        # 2. 验证 LATEST_REPORT.md 保存的是批量汇总报告，而不是单章被循环无脑覆盖
        latest_report = self.project_dir / "reports" / "LATEST_REPORT.md"
        self.assertTrue(latest_report.exists())
        latest_txt, _, _ = read_file_safe(latest_report)
        self.assertIn("批量连审大盘汇总报告", latest_txt)

        # 3. 验证单章归档报告依然完好归档保留
        arch_1 = self.project_dir / "reports" / "单章审查" / "001-100章" / "第001章_审查报告.md"
        arch_2 = self.project_dir / "reports" / "单章审查" / "001-100章" / "第002章_审查报告.md"
        self.assertTrue(arch_1.exists(), "单章归档报告第001章必须保留")
        self.assertTrue(arch_2.exists(), "单章归档报告第002章必须保留")




class TestStoryAuditCliGenreIntegration(unittest.TestCase):
    """测试 CLI --genre 参数传递、题材诊断画像与报告渲染集成"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp_dir.name)

        # 写入包含都市高武特征的章节
        chap_file = self.project_dir / "第001章_气血初显.txt"
        content = (
            "武道高考体测室内，高三学生排队走上气血仪。\n"
            "陈默拳力测试轰然爆发，气血值飙升突破准武者门槛，班主任震惊地瞪大了眼睛！\n"
            "【武道体测属性】\n"
            "- 气血值：145卡\n"
            "- 卡路里：3100\n"
            "- 武道等级：准武者一级\n"
            "他收回拳头，平静地走出训练馆。\n"
        )
        write_file_safe(chap_file, content)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_cli_genre_auto_detection_and_diagnostics(self):
        """测试 CLI 默认 auto 模式下自动识别题材并嵌入预审包和审查报告"""
        ret = main(["--project", str(self.project_dir), "--chapter", "1"])
        self.assertEqual(ret, 0)

        # 1. 验证 pre_audit_bundle.json 中的题材诊断
        bundle_path = self.project_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        self.assertTrue(bundle_path.exists())
        with open(bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        self.assertIn("genre_diagnostics", bundle)
        g_diag = bundle["genre_diagnostics"]
        self.assertEqual(g_diag["detected_genre"], "都市高武")
        self.assertGreater(g_diag["confidence"], 0.2)
        self.assertEqual(g_diag["category_group"], "都市异能")
        self.assertTrue(len(g_diag["first_principles"]) > 0)
        self.assertTrue(len(g_diag["red_lines"]) > 0)
        self.assertEqual(bundle["meta"]["genre"], "都市高武")

        # 2. 验证 LATEST_REPORT.md 中的题材诊断卡尺渲染
        report_path = self.project_dir / "reports" / "LATEST_REPORT.md"
        self.assertTrue(report_path.exists())
        rep_txt, _, _ = read_file_safe(report_path)
        self.assertIn("🎯 题材诊断与读者第一性原理卡尺", rep_txt)
        self.assertIn("都市高武", rep_txt)
        self.assertIn("第一性原理追读卡尺", rep_txt)
        self.assertIn("绝不可踩", rep_txt)
        self.assertIn("Agent D", rep_txt)

    def test_cli_genre_manual_override(self):
        """测试 CLI 显式传递 --genre 手动指定题材并装配专属卡尺"""
        ret = main(["--project", str(self.project_dir), "--chapter", "1", "--genre", "追妻火葬场"])
        self.assertEqual(ret, 0)

        bundle_path = self.project_dir / "reports" / ".cache" / "pre_audit_bundle.json"
        with open(bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        self.assertEqual(bundle["genre_diagnostics"]["detected_genre"], "追妻火葬场")
        self.assertEqual(bundle["genre_diagnostics"]["confidence"], 1.0)
        self.assertEqual(bundle["genre_diagnostics"]["category_group"], "短篇爆发")

        report_path = self.project_dir / "reports" / "LATEST_REPORT.md"
        rep_txt, _, _ = read_file_safe(report_path)
        self.assertIn("追妻火葬场", rep_txt)

if __name__ == "__main__":
    unittest.main()