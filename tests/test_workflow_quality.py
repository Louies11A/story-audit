# -*- coding: utf-8 -*-
"""六个公开 API 的完整流程：两种账本布局、编码、资产与伏笔连续性。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts import story_audit


@pytest.mark.parametrize("settings_directory", [False, True], ids=["root-ledger", "settings-ledger"])
@pytest.mark.parametrize("encoding,newline", [("utf-8", "\n"), ("gb18030", "\r\n")])
def test_six_public_apis_preserve_project_state(settings_directory, encoding, newline, capsys):
    with TemporaryDirectory(prefix="story_audit_workflow_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        ledger_dir = root / "设定" if settings_directory else root
        ledger_dir.mkdir(exist_ok=True)
        ledger_json = ledger_dir / "资源账本.json"
        ledger_md = ledger_dir / "资源账本.md"
        chapter = root / "正文" / "第001章.md"
        old = "陆离握紧了青钢剑。"
        new = "陆离拔出了青钢剑。"
        original = (newline.join([
            "第一章", old, "【获得：青钢剑×1柄】",
            '<!-- audit:stash name="深海旧钥匙" origin="第1章" status="pending" -->', "",
        ])).encode(encoding)
        chapter.write_bytes(original)
        (root / "正文" / "第002章.md").write_text(
            "第二章\n陆离走到了海边。\n伙伴等在船头。", encoding="utf-8",
        )

        def read_ledger():
            # 从确定存在的 API 产物读取，避免把错误路径的默认空状态误当资产丢失。
            assert ledger_json.is_file() and ledger_md.is_file()
            return json.loads(ledger_json.read_text(encoding="utf-8"))

        code, report = story_audit.init_ledger(root, scope_str="1-2", silent=True)
        assert code == 0 and report.is_file()
        initial = read_ledger()
        assets = initial["assets"]
        assert len(assets) == 1
        asset_id = next(iter(assets))
        assert assets[asset_id]["name"] == "青钢剑"
        assert assets[asset_id]["quantity"] == 1

        code, report = story_audit.audit_chapter(root, chapter_index=1, mode="full", silent=True)
        assert code == 0 and report.is_file()
        assert "Expert Review: not_executed" in report.read_text(encoding="utf-8")
        assert read_ledger()["assets"] == assets

        code, report = story_audit.audit_scope(root, scope_str="2-2", mode="full", silent=True)
        assert code == 0 and report.is_file()
        assert "深海旧钥匙" in report.read_text(encoding="utf-8")
        assert read_ledger()["assets"] == assets

        assert story_audit.apply_fix(
            root, chapter_index=1, target_line=2, old_text=old, new_text=new, silent=True,
        ) == 0
        assert chapter.read_bytes() == original.replace(old.encode(encoding), new.encode(encoding), 1)
        backups = [path for path in (root / "reports" / ".bak").rglob("*") if path.is_file()]
        assert len(backups) == 1 and backups[0].read_bytes() == original
        assert read_ledger()["assets"] == assets

        # 模拟作者只修改 Markdown 的所有者列，再通过公开 API 同步。
        rows = ledger_md.read_text(encoding="utf-8").splitlines()
        header = next(line for line in rows if line.startswith("| 资产ID |"))
        owner_column = [cell.strip() for cell in header.split("|")].index("所有者")
        edited = False
        for index, line in enumerate(rows):
            if line.startswith("| " + asset_id + " |"):
                cells = line.split("|")
                cells[owner_column] = " 陆离 "
                rows[index] = "|".join(cells)
                edited = True
        assert edited
        ledger_md.write_text("\n".join(rows) + "\n", encoding="utf-8")
        assert story_audit.sync_ledger_from_md(root, silent=True) == 0
        updated = read_ledger()
        assert set(updated["assets"]) == {asset_id}
        assert updated["assets"][asset_id]["owner"] == "陆离"
        assert updated["assets"][asset_id]["quantity"] == 1
        assert updated["foreshadowing_stash"] == initial["foreshadowing_stash"]

        assert story_audit.checkpoint_volume(root, volume=1, silent=True) == 0
        checkpoint = root / "设定" / "archive" / "volume_01_ledger.json"
        assert checkpoint.is_file()
        assert json.loads(checkpoint.read_text(encoding="utf-8")) == updated
        assert read_ledger() == updated
        assert story_audit.locate_ledger_paths(root) == (ledger_json, ledger_md)

        audit_state = json.loads((root / "reports" / ".audit_state.json").read_text(encoding="utf-8"))
        assert audit_state["completed_chapters"] == [1, 2]
        commitments = audit_state["foreshadowing_commitments"]
        assert len(commitments) == 1
        assert commitments[0]["tag"] == "深海旧钥匙"
        assert commitments[0]["origin_chapter"] == 1
        assert commitments[0]["status"] == "pending"
        bundle = json.loads((root / "reports" / ".cache" / "pre_audit_bundle.json").read_text(encoding="utf-8"))
        assert bundle["meta"]["effective_mode"] == "solo"
        assert bundle["meta"]["expert_review_executed"] is False
        assert not list(root.rglob("*.recovery.json"))
        output = capsys.readouterr()
        assert output.out == output.err == ""
