"""
tests/test_ledger_engine.py
资源账本状态机、多主体所有权与防脏写引擎单元测试
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts.ledger_engine import (
    ASSET_CATEGORIES,
    ASSET_STATUSES,
    AssetItem,
    LedgerDirtyError,
    LedgerState,
    check_dirty_state,
    create_volume_checkpoint,
    render_ledger_markdown,
    save_ledger_state,
    scan_foreshadowing_tags,
    sync_from_markdown,
)


class TestLedgerDataModel(unittest.TestCase):
    """测试资产数据模型与状态机规范"""

    def test_constants(self):
        """验证七类资产与扩展状态集合"""
        expected_categories = {
            "资金资产", "装备道具", "丹药耗材", "功法神通",
            "身份权限", "随行战力", "全局状态",
        }
        expected_statuses = {
            "UNACQUIRED", "ACQUIRED", "EQUIPPED", "CONSUMED",
            "DAMAGED", "TRANSFERRED", "LENT_OUT", "RECLAIMED", "RESTORED",
        }
        self.assertEqual(ASSET_CATEGORIES, expected_categories)
        self.assertEqual(ASSET_STATUSES, expected_statuses)

    def test_asset_item_defaults_and_post_init(self):
        """验证 AssetItem 默认值与 current_holder 联动逻辑"""
        item = AssetItem(
            id="asset_001",
            name="斩妖剑",
            category="装备道具",
            quantity=1,
            unit="把",
            origin_chapter=1.0,
        )
        self.assertEqual(item.owner, "主角")
        self.assertEqual(item.current_holder, "主角")
        self.assertEqual(item.status, "ACQUIRED")
        self.assertIsNone(item.lend_meta)
        self.assertEqual(item.constraints, {})
        self.assertEqual(item.history, [])

    def test_asset_item_custom_owner_holder(self):
        """验证自定义所有者与当前持有者"""
        item = AssetItem(
            id="asset_002",
            name="灵兽白狐",
            category="随行战力",
            quantity=1,
            unit="只",
            owner="女主角",
            current_holder="主角",
            status="EQUIPPED",
            origin_chapter=5.0,
        )
        self.assertEqual(item.owner, "女主角")
        self.assertEqual(item.current_holder, "主角")
        self.assertEqual(item.status, "EQUIPPED")

    def test_asset_invalid_category_or_status(self):
        """验证非法资产类别或非法状态抛出异常"""
        with self.assertRaises(ValueError):
            AssetItem(
                id="inv_1",
                name="未知物",
                category="非法类别",
                quantity=1,
                unit="个",
                origin_chapter=1.0,
            )

        with self.assertRaises(ValueError):
            AssetItem(
                id="inv_2",
                name="诛仙剑",
                category="装备道具",
                quantity=1,
                unit="柄",
                status="INVALID_STATUS",
                origin_chapter=1.0,
            )

    def test_asset_serialization(self):
        """验证 AssetItem to_dict 与 from_dict 的无损转换"""
        item = AssetItem(
            id="asset_money_01",
            name="下品灵石",
            category="资金资产",
            quantity=5000,
            unit="块",
            owner="主角",
            current_holder="主角",
            status="ACQUIRED",
            origin_chapter=2.0,
            constraints={"durability": 100, "binding_env": "凡人界"},
            history=[{"action": "gain", "chapter": 2.0}],
        )
        data = item.to_dict()
        reconstructed = AssetItem.from_dict(data)
        self.assertEqual(item, reconstructed)

    def test_ledger_state_serialization(self):
        """验证 LedgerState 的序列化与反序列化"""
        item1 = AssetItem(
            id="a1",
            name="青冥剑",
            category="装备道具",
            quantity=1,
            unit="柄",
            origin_chapter=1.0,
        )
        item2 = AssetItem(
            id="a2",
            name="聚气丹",
            category="丹药耗材",
            quantity=10,
            unit="枚",
            origin_chapter=3.0,
        )
        stash = [
            {"name": "残缺阵图", "origin": "第3章", "status": "UNACQUIRED"}
        ]
        state = LedgerState(
            last_updated_chapter=10.0,
            assets={"a1": item1, "a2": item2},
            foreshadowing_stash=stash,
        )
        data = state.to_dict()
        reconstructed = LedgerState.from_dict(data)
        self.assertEqual(reconstructed.last_updated_chapter, 10.0)
        self.assertIn("a1", reconstructed.assets)
        self.assertEqual(reconstructed.assets["a1"].name, "青冥剑")
        self.assertEqual(len(reconstructed.foreshadowing_stash), 1)
        self.assertEqual(reconstructed.foreshadowing_stash[0]["name"], "残缺阵图")

    def test_asset_transition_and_history(self):
        """测试资产状态流转与历史变迁流水"""
        item = AssetItem(
            id="sword_01",
            name="秋水剑",
            category="装备道具",
            quantity=1,
            unit="柄",
            origin_chapter=1.0,
            status="ACQUIRED",
        )
        # 装备
        item.transition("EQUIPPED", chapter=2.0, reason="击败山贼后佩戴")
        self.assertEqual(item.status, "EQUIPPED")
        self.assertEqual(len(item.history), 1)
        self.assertEqual(item.history[0]["to_status"], "EQUIPPED")

        # 借出给配角
        lend_info = {"borrower": "林师妹", "due_chapter": 10.0}
        item.transition("LENT_OUT", chapter=4.0, reason="支援宗门大比", holder="林师妹", meta=lend_info)
        self.assertEqual(item.status, "LENT_OUT")
        self.assertEqual(item.current_holder, "林师妹")
        self.assertEqual(item.lend_meta, lend_info)

        # 归还
        item.transition("RECLAIMED", chapter=6.0, reason="大比结束物归原主")
        self.assertEqual(item.status, "RECLAIMED")
        self.assertEqual(item.current_holder, "主角")

        # 损坏
        item.transition("DAMAGED", chapter=8.0, reason="抵挡元婴一击碎裂")
        self.assertEqual(item.status, "DAMAGED")

        # 修复
        item.transition("RESTORED", chapter=9.0, reason="铸剑大师重铸")
        self.assertEqual(item.status, "RESTORED")

        # 转移所有权
        item.transition("TRANSFERRED", chapter=10.0, reason="赠予徒儿", holder="小徒弟")
        self.assertEqual(item.status, "TRANSFERRED")
        self.assertEqual(item.current_holder, "小徒弟")

    def test_quantity_modification(self):
        """测试耗材数量增减与消耗状态自动变迁"""
        pill = AssetItem(
            id="pill_01",
            name="大还丹",
            category="丹药耗材",
            quantity=5,
            unit="瓶",
            origin_chapter=1.0,
            status="ACQUIRED",
        )
        pill.modify_quantity(-3, chapter=2.0, reason="疗伤使用")
        self.assertEqual(pill.quantity, 2)
        self.assertEqual(pill.status, "ACQUIRED")

        # 全部耗尽
        pill.modify_quantity(-2, chapter=3.0, reason="突破金丹消耗完毕")
        self.assertEqual(pill.quantity, 0)
        self.assertEqual(pill.status, "CONSUMED")


class TestForeshadowingScan(unittest.TestCase):
    """测试伏笔缓冲池扫描器正则提取"""

    def test_scan_standard_and_optional_tags(self):
        """提取标准格式及缺省字段的伏笔标签"""
        text = (
            "第一章 穿越\n"
            "陆沉偶得一尊古朴小鼎。\n"
            '<!-- audit:stash name="九幽玄重鼎" origin="第1章" status="UNACQUIRED" -->\n'
            "山林深处似乎还藏着一本破旧经书。\n"
            '<!--   audit:stash    name="紫极太玄经"    -->\n'
            "还有一把生锈的钥匙。\n"
            '<!-- audit:stash name="青铜秘钥" origin="第1章" -->\n'
        )
        tags = scan_foreshadowing_tags(text)
        self.assertEqual(len(tags), 3)

        self.assertEqual(tags[0]["name"], "九幽玄重鼎")
        self.assertEqual(tags[0]["origin"], "第1章")
        self.assertEqual(tags[0]["status"], "UNACQUIRED")

        self.assertEqual(tags[1]["name"], "紫极太玄经")
        self.assertEqual(tags[1]["origin"], "")
        self.assertEqual(tags[1]["status"], "")

        self.assertEqual(tags[2]["name"], "青铜秘钥")
        self.assertEqual(tags[2]["origin"], "第1章")
        self.assertEqual(tags[2]["status"], "")

    def test_scan_empty_or_no_tags(self):
        """正文中无伏笔标签时返回空列表"""
        text = "普通正文内容，没有任何伏笔注释标签。"
        self.assertEqual(scan_foreshadowing_tags(text), [])


class TestDirtyWriteGuard(unittest.TestCase):
    """测试防脏写覆盖拦截器"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.json_path = self.dir_path / "ledger.json"
        self.md_path = self.dir_path / "ledger.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_check_dirty_state_missing_files(self):
        """任一文件不存在时不应判定为 dirty"""
        self.assertFalse(check_dirty_state(self.md_path, self.json_path))
        self.json_path.write_text("{}", encoding="utf-8")
        self.assertFalse(check_dirty_state(self.md_path, self.json_path))

    def test_check_dirty_state_clean_and_dirty(self):
        """测试 MD 修改时间比 JSON 新时的 dirty 判定"""
        self.json_path.write_text('{"last_updated_chapter": 1}', encoding="utf-8")
        self.md_path.write_text("# 账本", encoding="utf-8")

        # 调整时间戳：使 json 比 md 新
        now = time.time()
        os.utime(self.md_path, (now - 10, now - 10))
        os.utime(self.json_path, (now, now))
        self.assertFalse(check_dirty_state(self.md_path, self.json_path))

        # 调整时间戳：使 md 比 json 新（模拟人工编辑了 markdown）
        os.utime(self.md_path, (now + 10, now + 10))
        self.assertTrue(check_dirty_state(self.md_path, self.json_path))

    def test_save_ledger_state_clean(self):
        """正常写入状态，MD 和 JSON 均成功创建"""
        state = LedgerState(last_updated_chapter=1.0)
        save_ledger_state(state, self.json_path, self.md_path)
        self.assertTrue(self.json_path.exists())
        self.assertTrue(self.md_path.exists())
        # 验证下一次立刻检查不会误触发 dirty
        self.assertFalse(check_dirty_state(self.md_path, self.json_path))

    def test_save_ledger_state_dirty_intercepted(self):
        """当 md 比 json 新且未加 force 时，严厉拦截抛出 LedgerDirtyError"""
        state = LedgerState(last_updated_chapter=1.0)
        save_ledger_state(state, self.json_path, self.md_path)

        # 模拟人工在外部直接编辑了 md 文件
        time.sleep(0.01)
        now = time.time() + 5
        os.utime(self.md_path, (now, now))

        # 再次尝试写入新状态（force=False）
        new_state = LedgerState(last_updated_chapter=2.0)
        with self.assertRaises(LedgerDirtyError):
            save_ledger_state(new_state, self.json_path, self.md_path, force=False)

        # 验证原有 JSON 内容未被覆盖
        saved_json = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_json["last_updated_chapter"], 1.0)

    def test_save_ledger_state_force_overwrite(self):
        """当指定 force=True 时，允许覆写脏数据"""
        state = LedgerState(last_updated_chapter=1.0)
        save_ledger_state(state, self.json_path, self.md_path)

        now = time.time() + 5
        os.utime(self.md_path, (now, now))

        new_state = LedgerState(last_updated_chapter=2.0)
        save_ledger_state(new_state, self.json_path, self.md_path, force=True)
        saved_json = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_json["last_updated_chapter"], 2.0)


class TestMarkdownRendering(unittest.TestCase):
    """测试冷热资产分层 Markdown 渲染"""

    def test_render_hot_and_cold_layering(self):
        """验证热资产在顶层表格、冷资产折叠在 details 标签中"""
        hot_item1 = AssetItem(
            id="item_sword",
            name="太阿剑",
            category="装备道具",
            quantity=1,
            unit="柄",
            owner="主角",
            current_holder="主角",
            status="EQUIPPED",
            origin_chapter=1.0,
            constraints={"durability": 100},
        )
        hot_item2 = AssetItem(
            id="item_stone",
            name="中品灵石",
            category="资金资产",
            quantity=200,
            unit="块",
            owner="主角",
            current_holder="主角",
            status="ACQUIRED",
            origin_chapter=2.0,
        )
        cold_item1 = AssetItem(
            id="item_broken_pill",
            name="废弃聚气丹",
            category="丹药耗材",
            quantity=0,
            unit="枚",
            owner="主角",
            current_holder="主角",
            status="CONSUMED",
            origin_chapter=1.0,
        )
        cold_item2 = AssetItem(
            id="item_gift_blade",
            name="断水刀",
            category="装备道具",
            quantity=1,
            unit="把",
            owner="主角",
            current_holder="某师弟",
            status="TRANSFERRED",
            origin_chapter=3.0,
        )

        state = LedgerState(
            last_updated_chapter=5.0,
            assets={
                "item_sword": hot_item1,
                "item_stone": hot_item2,
                "item_broken_pill": cold_item1,
                "item_gift_blade": cold_item2,
            },
            foreshadowing_stash=[{"name": "上古卷轴", "origin": "第1章", "status": "UNACQUIRED"}],
        )

        md = render_ledger_markdown(state)
        self.assertIn("# 资源账本（截至第 5.0 章）", md)
        # 热资产在 details 标签之外
        details_start = md.find("<details>")
        self.assertTrue(details_start != -1)

        hot_section = md[:details_start]
        cold_section = md[details_start:]

        self.assertIn("太阿剑", hot_section)
        self.assertIn("中品灵石", hot_section)
        self.assertNotIn("废弃聚气丹", hot_section)
        self.assertNotIn("断水刀", hot_section)

        self.assertIn("<summary>历史已消耗与归档资产</summary>", cold_section)
        self.assertIn("废弃聚气丹", cold_section)
        self.assertIn("断水刀", cold_section)
        self.assertIn("</details>", cold_section)

    def test_render_empty_assets(self):
        """当资产列表为空时应安全友好渲染"""
        state = LedgerState(last_updated_chapter=0.0)
        md = render_ledger_markdown(state)
        self.assertIn("暂无", md)
        self.assertIn("<details>", md)
        self.assertIn("</details>", md)


class TestVolumeCheckpoint(unittest.TestCase):
    """测试分卷封账快照与归档"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.archive_dir = Path(self.temp_dir.name) / "archive"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_volume_checkpoint(self):
        """验证快照归档文件命名、创建与内容完整性"""
        item = AssetItem(
            id="v1_item",
            name="乾坤袋",
            category="装备道具",
            quantity=1,
            unit="个",
            origin_chapter=1.0,
        )
        state = LedgerState(
            last_updated_chapter=30.0,
            assets={"v1_item": item},
            foreshadowing_stash=[{"name": "神秘地图"}],
        )
        archive_file = create_volume_checkpoint(1, state, self.archive_dir)

        expected_filename = "volume_01_ledger.json"
        self.assertEqual(archive_file.name, expected_filename)
        self.assertTrue(archive_file.exists())

        loaded_data = json.loads(archive_file.read_text(encoding="utf-8"))
        self.assertEqual(loaded_data["last_updated_chapter"], 30.0)
        self.assertIn("v1_item", loaded_data["assets"])
        self.assertEqual(loaded_data["assets"]["v1_item"]["name"], "乾坤袋")


class TestSyncFromMarkdown(unittest.TestCase):
    """测试从 Markdown 反向增量同步更新 JSON"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.json_path = self.dir_path / "ledger.json"
        self.md_path = self.dir_path / "ledger.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_from_markdown_updates_and_adds(self):
        """测试解析 Markdown 表格，增量修改已有资产并添加新资产"""
        old_item = AssetItem(
            id="item_stone",
            name="下品灵石",
            category="资金资产",
            quantity=100,
            unit="块",
            origin_chapter=1.0,
            status="ACQUIRED",
        )
        initial_state = LedgerState(
            last_updated_chapter=2.0,
            assets={"item_stone": old_item},
        )
        save_ledger_state(initial_state, self.json_path, self.md_path)

        sep = "|"
        table_header = f"{sep} 资产ID {sep} 资产名称 {sep} 类别 {sep} 数量 {sep} 单位 {sep} 所有者 {sep} 当前持有者 {sep} 状态 {sep} 初始章节 {sep} 约束说明 {sep}"
        table_sep = f"{sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep}"
        row1 = f"{sep} item_stone {sep} 下品灵石 {sep} 资金资产 {sep} 800 {sep} 块 {sep} 主角 {sep} 主角 {sep} ACQUIRED {sep} 1.0 {sep} - {sep}"
        row2 = f"{sep} item_bow {sep} 穿云弓 {sep} 装备道具 {sep} 1 {sep} 张 {sep} 主角 {sep} 柳依依 {sep} LENT_OUT {sep} 3.5 {sep} durability: 90 {sep}"
        row3 = f"{sep} item_old_armor {sep} 破损皮甲 {sep} 装备道具 {sep} 0 {sep} 件 {sep} 主角 {sep} 主角 {sep} DAMAGED {sep} 1.0 {sep} durability: 0 {sep}"

        custom_md = (
            f"# 资源账本（截至第 4.0 章）\n\n"
            f"## 当前持有与生效资产（热资产）\n\n"
            f"{table_header}\n{table_sep}\n{row1}\n{row2}\n\n"
            f"<details>\n"
            f"<summary>历史已消耗与归档资产</summary>\n\n"
            f"{table_header}\n{table_sep}\n{row3}\n\n"
            f"</details>\n"
        )

        time.sleep(0.01)
        self.md_path.write_text(custom_md, encoding="utf-8")

        synced_state = sync_from_markdown(self.md_path, self.json_path)

        self.assertEqual(synced_state.last_updated_chapter, 4.0)
        self.assertEqual(len(synced_state.assets), 3)

        stone = synced_state.assets["item_stone"]
        self.assertEqual(stone.quantity, 800)

        bow = synced_state.assets["item_bow"]
        self.assertEqual(bow.name, "穿云弓")
        self.assertEqual(bow.category, "装备道具")
        self.assertEqual(bow.current_holder, "柳依依")
        self.assertEqual(bow.status, "LENT_OUT")
        self.assertEqual(bow.origin_chapter, 3.5)
        self.assertEqual(bow.constraints.get("durability"), 90)

        armor = synced_state.assets["item_old_armor"]
        self.assertEqual(armor.status, "DAMAGED")
        self.assertEqual(armor.quantity, 0)

        self.assertFalse(check_dirty_state(self.md_path, self.json_path))



    def test_sync_from_markdown_when_json_not_exists(self):
        """测试在 JSON 文件完全不存在的情况下直接从 Markdown 解析初始化账本"""
        sep = "|"
        table_header = f"{sep} 资产ID {sep} 资产名称 {sep} 类别 {sep} 数量 {sep} 单位 {sep} 所有者 {sep} 当前持有者 {sep} 状态 {sep} 初始章节 {sep} 约束说明 {sep}"
        table_sep = f"{sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep} :--- {sep}"
        row = f"{sep} init_sword {sep} 斩仙剑 {sep} 装备道具 {sep} 1 {sep} 柄 {sep} 主角 {sep} 主角 {sep} EQUIPPED {sep} 1.0 {sep} durability: 100; binding_env: 凡人界 {sep}"

        custom_md = (
            f"# 资源账本（截至第 1.0 章）\n\n"
            f"## 当前持有与生效资产（热资产）\n\n"
            f"{table_header}\n{table_sep}\n{row}\n\n"
        )
        self.md_path.write_text(custom_md, encoding="utf-8")
        self.assertFalse(self.json_path.exists())

        synced = sync_from_markdown(self.md_path, self.json_path)
        self.assertTrue(self.json_path.exists())
        self.assertEqual(synced.last_updated_chapter, 1.0)
        self.assertIn("init_sword", synced.assets)
        self.assertEqual(synced.assets["init_sword"].name, "斩仙剑")
        self.assertEqual(synced.assets["init_sword"].constraints.get("binding_env"), "凡人界")
        self.assertFalse(check_dirty_state(self.md_path, self.json_path))

    def test_save_ledger_state_without_md_path(self):
        """测试不提供 md_path 时只保存 JSON 数据源"""
        state = LedgerState(last_updated_chapter=5.0)
        save_ledger_state(state, self.json_path)
        self.assertTrue(self.json_path.exists())
        self.assertFalse(self.md_path.exists())
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["last_updated_chapter"], 5.0)

    def test_transition_invalid_status_raises(self):
        """测试流转到未知状态抛出 ValueError"""
        item = AssetItem(
            id="test_it",
            name="试炼令",
            category="身份权限",
            quantity=1,
            unit="枚",
        )
        with self.assertRaises(ValueError):
            item.transition("UNKNOWN_STATE", chapter=1.0)



    def test_modify_quantity_clamp_and_transferred_no_holder(self):
        """测试数量扣减至负数时截断为0，以及转移状态不指定新holder的情况"""
        item = AssetItem(
            id="potion",
            name="金疮药",
            category="丹药耗材",
            quantity=3,
            unit="瓶",
        )
        item.modify_quantity(-10, chapter=1.0, reason="超额扣减")
        self.assertEqual(item.quantity, 0)
        self.assertEqual(item.status, "CONSUMED")

        # TRANSFERRED 且没有 holder
        sword = AssetItem(
            id="sword",
            name="木剑",
            category="装备道具",
            quantity=1,
            unit="柄",
        )
        sword.transition("TRANSFERRED", chapter=2.0)
        self.assertEqual(sword.status, "TRANSFERRED")
        self.assertEqual(sword.owner, "主角")

    def test_ledger_state_from_dict_with_asset_instances(self):
        """测试 LedgerState.from_dict 接收已有 AssetItem 实例"""
        item = AssetItem(
            id="i1",
            name="盾牌",
            category="装备道具",
            quantity=1,
            unit="面",
        )
        state = LedgerState.from_dict({"assets": {"i1": item}})
        self.assertIn("i1", state.assets)
        self.assertEqual(state.assets["i1"].name, "盾牌")

    def test_sync_from_markdown_resilience(self):
        """测试解析损坏JSON及包含非数值格式的Markdown表格"""
        # 写入损坏的 json
        self.json_path.write_text("{corrupted_json: true", encoding="utf-8")

        table_header = "| 资产ID | 资产名称 | 类别 | 数量 | 单位 | 所有者 | 当前持有者 | 状态 | 初始章节 |"
        table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        row = "| sword_x | 铁剑 | 装备道具 | 很多 | 把 | 主角 | 主角 | ACQUIRED | 序章 |"
        empty_row = "| | | | | | | | | |"

        custom_md = f"# 资源账本\n\n{table_header}\n{table_sep}\n{row}\n{empty_row}\n"
        self.md_path.write_text(custom_md, encoding="utf-8")

        state = sync_from_markdown(self.md_path, self.json_path)
        self.assertIn("sword_x", state.assets)
        # 非法数值回退为默认 1 和 1.0
        self.assertEqual(state.assets["sword_x"].quantity, 1)
        self.assertEqual(state.assets["sword_x"].origin_chapter, 1.0)


if __name__ == '__main__':
    unittest.main()
