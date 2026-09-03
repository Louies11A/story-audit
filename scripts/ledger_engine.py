"""
资源账本状态机、多主体所有权与防脏写引擎 (ledger_engine.py)

功能职责：
1. 统一七类资产数据模型与九种状态扩展状态机；
2. 支持多主体所有权与持有权分离（借出、归还、转让）及全流程变迁流水记账；
3. 伏笔缓冲池扫描器，正则提取正文注释中的伏笔标签；
4. 防脏写覆盖拦截器 (Dirty-Write Guard)，拦截外部未同步的 Markdown 篡改；
5. 冷热资产分层 Markdown 渲染，兼顾高频阅读与低频归档；
6. 分卷封账快照与期末结转；
7. 从 Markdown 反向增量同步更新 JSON 数据源。
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from scripts.safe_io import read_file_safe, write_file_safe

# 七类资产标准分类
ASSET_CATEGORIES: Set[str] = {
    "资金资产",
    "装备道具",
    "丹药耗材",
    "功法神通",
    "身份权限",
    "随行战力",
    "全局状态",
}

# 扩展状态机状态集合
ASSET_STATUSES: Set[str] = {
    "UNACQUIRED",   # 未获取（线索/伏笔阶段）
    "ACQUIRED",     # 已获取（在背包/仓库中）
    "EQUIPPED",     # 已装备/生效中
    "CONSUMED",     # 已消耗完毕
    "DAMAGED",      # 已破损/受损失效
    "TRANSFERRED",  # 已永久转移所有权
    "LENT_OUT",     # 已借出（所有权未变，持有者变更）
    "RECLAIMED",    # 已收回（物归原主）
    "RESTORED",     # 已修复/复原
}


class LedgerDirtyError(Exception):
    """防脏写拦截器异常：Markdown 编辑时间晚于 JSON 数据源"""
    pass


@dataclass
class AssetItem:
    """标准资产条目模型"""
    id: str                                                 # 资产唯一标识
    name: str                                               # 资产名称
    category: str                                           # 资产分类（七类资产之一）
    quantity: Union[int, float]                             # 数量
    unit: str                                               # 单位（如 "块", "把", "枚"）
    owner: str = "主角"                                     # 原始所有者（默认 "主角"）
    current_holder: str = "主角"                             # 当前实际持有人（默认同 owner）
    status: str = "ACQUIRED"                                # 当前状态
    origin_chapter: float = 1.0                             # 获取章节
    lend_meta: Optional[Dict[str, Any]] = None              # 借出元数据（借用人、时限等）
    constraints: Dict[str, Any] = field(default_factory=dict)  # 约束说明（durability, time_limit, binding_env 等）
    history: List[Dict[str, Any]] = field(default_factory=list)  # 变迁历史流水

    def __post_init__(self) -> None:
        if self.category not in ASSET_CATEGORIES:
            raise ValueError(f"未知资产分类 '{self.category}'，有效分类为: {sorted(ASSET_CATEGORIES)}")
        if self.status not in ASSET_STATUSES:
            raise ValueError(f"未知资产状态 '{self.status}'，有效状态为: {sorted(ASSET_STATUSES)}")
        if not self.current_holder:
            self.current_holder = self.owner

    def transition(
        self,
        new_status: str,
        chapter: float,
        reason: str = "",
        holder: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """执行状态流转并自动记录变迁流水日志"""
        if new_status not in ASSET_STATUSES:
            raise ValueError(f"目标状态 '{new_status}' 不在有效状态集合中: {sorted(ASSET_STATUSES)}")

        old_status = self.status

        log_entry: Dict[str, Any] = {
            "action": "transition",
            "from_status": old_status,
            "to_status": new_status,
            "chapter": chapter,
            "reason": reason,
            "timestamp": time.time(),
        }

        if new_status == "LENT_OUT":
            self.current_holder = holder or self.current_holder
            self.lend_meta = meta or {}
            log_entry["holder"] = self.current_holder
            log_entry["lend_meta"] = self.lend_meta
        elif new_status == "RECLAIMED":
            self.current_holder = self.owner
            self.lend_meta = None
            log_entry["holder"] = self.current_holder
        elif new_status == "TRANSFERRED":
            if holder:
                self.owner = holder
                self.current_holder = holder
                log_entry["holder"] = holder
        else:
            if holder:
                self.current_holder = holder
                log_entry["holder"] = holder

        self.status = new_status
        self.history.append(log_entry)

    def modify_quantity(
        self,
        delta: Union[int, float],
        chapter: float,
        reason: str = "",
    ) -> None:
        """增减资产数量，并在消耗殆尽时自动变迁为 CONSUMED 状态"""
        old_qty = self.quantity
        new_qty = self.quantity + delta
        if new_qty < 0:
            new_qty = 0
        self.quantity = new_qty

        log_entry: Dict[str, Any] = {
            "action": "modify_quantity",
            "delta": delta,
            "from_quantity": old_qty,
            "to_quantity": self.quantity,
            "chapter": chapter,
            "reason": reason,
            "timestamp": time.time(),
        }
        self.history.append(log_entry)

        if self.quantity == 0 and self.category == "丹药耗材":
            self.transition("CONSUMED", chapter=chapter, reason=f"耗尽自动归档: {reason}")

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为纯字典格式便于 JSON 序列化"""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "quantity": self.quantity,
            "unit": self.unit,
            "owner": self.owner,
            "current_holder": self.current_holder,
            "status": self.status,
            "origin_chapter": self.origin_chapter,
            "lend_meta": self.lend_meta,
            "constraints": dict(self.constraints),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AssetItem":
        """从字典反序列化构建 AssetItem 实例"""
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            category=str(d.get("category", "装备道具")),
            quantity=d.get("quantity", 1),
            unit=str(d.get("unit", "个")),
            owner=str(d.get("owner", "主角")),
            current_holder=str(d.get("current_holder", d.get("owner", "主角"))),
            status=str(d.get("status", "ACQUIRED")),
            origin_chapter=float(d.get("origin_chapter", 1.0)),
            lend_meta=d.get("lend_meta"),
            constraints=dict(d.get("constraints", {})),
            history=list(d.get("history", [])),
        )


@dataclass
class LedgerState:
    """全量资源账本状态快照"""
    last_updated_chapter: float = 0.0
    assets: Dict[str, AssetItem] = field(default_factory=dict)
    foreshadowing_stash: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典结构"""
        return {
            "last_updated_chapter": self.last_updated_chapter,
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "foreshadowing_stash": list(self.foreshadowing_stash),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LedgerState":
        """从字典反序列化为 LedgerState 实例"""
        raw_assets = d.get("assets", {})
        assets: Dict[str, AssetItem] = {}
        for k, v in raw_assets.items():
            if isinstance(v, AssetItem):
                assets[k] = v
            elif isinstance(v, dict):
                assets[k] = AssetItem.from_dict(v)

        return cls(
            last_updated_chapter=float(d.get("last_updated_chapter", 0.0)),
            assets=assets,
            foreshadowing_stash=list(d.get("foreshadowing_stash", [])),
        )


def scan_foreshadowing_tags(text: str) -> List[Dict[str, str]]:
    """扫描提取文本中的伏笔缓冲池注释标签

    提取模式：<!-- audit:stash name="..." [origin="..."] [status="..."] -->
    """
    if not text:
        return []

    pattern = re.compile(
        r'<!--\s*audit:stash\s+name="(?P<name>[^"]+)"(?:\s+origin="(?P<origin>[^"]*)")?(?:\s+status="(?P<status>[^"]*)")?\s*-->',
        re.DOTALL,
    )

    results: List[Dict[str, str]] = []
    for match in pattern.finditer(text):
        name = match.group("name")
        origin = match.group("origin") or ""
        status = match.group("status") or ""
        results.append({
            "name": name,
            "origin": origin,
            "status": status,
        })
    return results


def check_dirty_state(md_path: Path, json_path: Path) -> bool:
    """检查 Markdown 账本是否存在比 JSON 更加新的外部修改冲突

    当且仅当 md_path 与 json_path 均存在且 md_path.stat().st_mtime > json_path.stat().st_mtime 时返回 True。
    """
    md = Path(md_path)
    js = Path(json_path)
    if not md.is_file() or not js.is_file():
        return False
    return md.stat().st_mtime > js.stat().st_mtime


def _format_constraints(constraints: Dict[str, Any]) -> str:
    """格式化约束条件字段为紧凑字符串"""
    if not constraints:
        return "-"
    parts = []
    for k, v in constraints.items():
        parts.append(f"{k}: {v}")
    return "; ".join(parts)


def _parse_constraints(c_str: str) -> Dict[str, Any]:
    """从字符串反向解析约束字典"""
    if not c_str or c_str.strip() == "-":
        return {}
    res: Dict[str, Any] = {}
    items = c_str.split(";")
    for item in items:
        if ":" in item:
            k, v = item.split(":", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            if v_clean.isdigit():
                res[k_clean] = int(v_clean)
            else:
                try:
                    res[k_clean] = float(v_clean)
                except ValueError:
                    res[k_clean] = v_clean
    return res


def render_ledger_markdown(state: LedgerState) -> str:
    """冷热资产分层渲染 Markdown 账本文档

    - 热资产（状态为 EQUIPPED, ACQUIRED, LENT_OUT 且数量 > 0）：顶层表格清晰直观；
    - 冷资产（状态为 CONSUMED, DAMAGED, TRANSFERRED 或数量 <= 0）：折叠在 details 标签中；
    - 伏笔待回收池：在末尾以折叠区块展示。
    """
    lines: List[str] = [
        f"# 资源账本（截至第 {state.last_updated_chapter} 章）",
        "",
        "## 当前持有与生效资产（热资产）",
        "",
    ]

    hot_assets: List[AssetItem] = []
    cold_assets: List[AssetItem] = []

    for item in state.assets.values():
        if item.quantity <= 0 or item.status in {"CONSUMED", "DAMAGED", "TRANSFERRED"}:
            cold_assets.append(item)
        elif item.status in {"EQUIPPED", "ACQUIRED", "LENT_OUT", "RECLAIMED", "RESTORED"}:
            hot_assets.append(item)
        else:
            cold_assets.append(item)

    table_header = "| 资产ID | 资产名称 | 类别 | 数量 | 单位 | 所有者 | 当前持有者 | 状态 | 初始章节 | 约束说明 |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"

    def format_row(it: AssetItem) -> str:
        c_desc = _format_constraints(it.constraints)
        return (
            f"| {it.id} | {it.name} | {it.category} | {it.quantity} | {it.unit} | "
            f"{it.owner} | {it.current_holder} | {it.status} | {it.origin_chapter} | {c_desc} |"
        )

    if hot_assets:
        lines.append(table_header)
        lines.append(table_sep)
        for item in hot_assets:
            lines.append(format_row(item))
    else:
        lines.append("（暂无活跃资产）")

    lines.append("")
    lines.append("<details>")
    lines.append("<summary>历史已消耗与归档资产</summary>")
    lines.append("")

    if cold_assets:
        lines.append(table_header)
        lines.append(table_sep)
        for item in cold_assets:
            lines.append(format_row(item))
    else:
        lines.append("（暂无归档资产）")

    lines.append("")
    lines.append("</details>")

    if state.foreshadowing_stash:
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>伏笔待回收池</summary>")
        lines.append("")
        lines.append("| 伏笔名称 | 来源线索 | 初始状态 |")
        lines.append("| :--- | :--- | :--- |")
        for stash in state.foreshadowing_stash:
            s_name = stash.get("name", "")
            s_origin = stash.get("origin", "-") or "-"
            s_status = stash.get("status", "-") or "-"
            lines.append(f"| {s_name} | {s_origin} | {s_status} |")
        lines.append("")
        lines.append("</details>")

    lines.append("")
    return "\n".join(lines)


def save_ledger_state(
    state: LedgerState,
    json_path: Path,
    md_path: Optional[Path] = None,
    force: bool = False,
) -> None:
    """原子保存账本状态并执行防脏写拦截

    若提供了 md_path，在写入前检查 check_dirty_state：
    若 dirty 且 force=False，抛出 LedgerDirtyError；
    写入完成后同步时间戳，消除误报。
    """
    json_p = Path(json_path)
    md_p = Path(md_path) if md_path else None

    # 防脏写拦截
    if md_p is not None and not force:
        if check_dirty_state(md_p, json_p):
            raise LedgerDirtyError(
                f"检测到 Markdown 账本 ({md_p}) 修改时间晚于 JSON 数据源 ({json_p})，"
                "存在潜在外部人工编辑冲突！若需强制覆写请指定 force=True，或先执行 sync_from_markdown。"
            )

    # 保存 JSON
    json_content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    write_file_safe(json_p, json_content)

    # 若指定了 md_path，渲染并原子写入 Markdown
    if md_p is not None:
        md_content = render_ledger_markdown(state)
        write_file_safe(md_p, md_content)

        # 消除时间戳微小偏差带来的脏写误报：使 json 的 mtime 不早于 md 的 mtime
        if md_p.exists() and json_p.exists():
            md_mtime = md_p.stat().st_mtime
            json_mtime = json_p.stat().st_mtime
            if md_mtime > json_mtime:
                os.utime(json_p, (md_mtime, md_mtime))


def create_volume_checkpoint(volume: int, state: LedgerState, archive_dir: Path) -> Path:
    """创建分卷封账快照，将当前全量状态归档至 volume_{volume:02d}_ledger.json"""
    arch_dir = Path(archive_dir)
    arch_dir.mkdir(parents=True, exist_ok=True)

    archive_filename = f"volume_{volume:02d}_ledger.json"
    archive_path = arch_dir / archive_filename

    json_content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    write_file_safe(archive_path, json_content)
    return archive_path


def sync_from_markdown(md_path: Path, json_path: Path) -> LedgerState:
    """从 Markdown 账本表格反向增量解析并合并至 JSON 数据源"""
    md_p = Path(md_path)
    json_p = Path(json_path)

    md_content, _, _ = read_file_safe(md_p)

    # 读取现有 JSON 状态或初始化空状态
    if json_p.is_file():
        raw_json_str, _, _ = read_file_safe(json_p)
        try:
            raw_data = json.loads(raw_json_str)
            state = LedgerState.from_dict(raw_data)
        except Exception:
            state = LedgerState()
    else:
        state = LedgerState()

    # 解析标题中的章节信息
    chap_match = re.search(r'#\s*资源账本[（\(]截至第\s*([\d\.]+)\s*章[）\)]', md_content)
    if chap_match:
        try:
            state.last_updated_chapter = float(chap_match.group(1))
        except ValueError:
            pass

    # 解析表格行
    table_lines = [line.strip() for line in md_content.split(chr(10)) if line.strip().startswith("|")]
    col_mapping: Optional[Dict[str, int]] = None

    for line in table_lines:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue

        # 识别表头行
        if "资产ID" in cells and "资产名称" in cells:
            col_mapping = {col: idx for idx, col in enumerate(cells)}
            continue

        # 跳过分隔行
        if all(re.match(r'^:?-+:?$', c) for c in cells):
            continue

        if col_mapping is None or "资产ID" not in col_mapping:
            continue

        try:
            item_id = cells[col_mapping["资产ID"]]
            if not item_id or item_id.startswith("---"):
                continue

            name = cells[col_mapping.get("资产名称", 1)]
            category = cells[col_mapping.get("类别", 2)]
            qty_raw = cells[col_mapping.get("数量", 3)]
            unit = cells[col_mapping.get("单位", 4)]
            owner = cells[col_mapping.get("所有者", 5)]
            current_holder = cells[col_mapping.get("当前持有者", 6)]
            status = cells[col_mapping.get("状态", 7)]
            chap_raw = cells[col_mapping.get("初始章节", 8)]
            constraints_str = cells[col_mapping.get("约束说明", 9)] if "约束说明" in col_mapping else ""

            # 解析数量
            try:
                quantity = int(qty_raw) if "." not in qty_raw else float(qty_raw)
            except ValueError:
                quantity = 1

            # 解析章节
            try:
                origin_chapter = float(chap_raw)
            except ValueError:
                origin_chapter = 1.0

            constraints = _parse_constraints(constraints_str)

            if item_id in state.assets:
                # 增量更新已有条目
                existing = state.assets[item_id]
                changed = (
                    existing.name != name
                    or existing.quantity != quantity
                    or existing.current_holder != current_holder
                    or existing.status != status
                    or existing.constraints != constraints
                )
                if changed:
                    existing.name = name
                    existing.category = category if category in ASSET_CATEGORIES else existing.category
                    existing.quantity = quantity
                    existing.unit = unit
                    existing.owner = owner
                    existing.current_holder = current_holder
                    existing.status = status if status in ASSET_STATUSES else existing.status
                    existing.origin_chapter = origin_chapter
                    existing.constraints = constraints
                    existing.history.append({
                        "action": "sync_from_markdown",
                        "timestamp": time.time(),
                    })
            else:
                # 新增条目
                new_item = AssetItem(
                    id=item_id,
                    name=name,
                    category=category if category in ASSET_CATEGORIES else "装备道具",
                    quantity=quantity,
                    unit=unit,
                    owner=owner or "主角",
                    current_holder=current_holder or owner or "主角",
                    status=status if status in ASSET_STATUSES else "ACQUIRED",
                    origin_chapter=origin_chapter,
                    constraints=constraints,
                    history=[{"action": "created_from_markdown_sync", "timestamp": time.time()}],
                )
                state.assets[item_id] = new_item
        except (IndexError, ValueError):
            continue

    # 持久化回 JSON 并消除 dirty 状态
    save_ledger_state(state, json_p, md_p, force=True)
    return state
