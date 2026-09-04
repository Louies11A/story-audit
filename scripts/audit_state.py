# -*- coding: utf-8 -*-
"""
audit_state.py: 跨批长篇因果闭环与状态机管理 (reports/.audit_state.json)

功能职责：
1. 跨批次长篇审查连续性状态机落盘与原子更新；
2. 记录已完成章节列表、当前批次以及“上一批未解决的开放缺陷与伏笔承诺”；
3. 下一批连审启动时自动将其装载为 Inherited Items，在报告中显式呈现并校验跨批因果一致性。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_SCHEMA_VERSION = 1


@dataclass
class AuditState:
    schema_version: int = STATE_SCHEMA_VERSION
    last_scope: str = ""
    last_updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_chapters: List[float] = field(default_factory=list)
    open_defects: List[Dict[str, Any]] = field(default_factory=list)
    foreshadowing_commitments: List[Dict[str, Any]] = field(default_factory=list)
    resolved_items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuditState:
        return cls(
            schema_version=data.get("schema_version", STATE_SCHEMA_VERSION),
            last_scope=data.get("last_scope", ""),
            last_updated_at=data.get("last_updated_at", ""),
            completed_chapters=[float(x) for x in data.get("completed_chapters", [])],
            open_defects=list(data.get("open_defects", [])),
            foreshadowing_commitments=list(data.get("foreshadowing_commitments", [])),
            resolved_items=list(data.get("resolved_items", [])),
        )


def get_audit_state_path(reports_dir: Path) -> Path:
    """获取 reports/.audit_state.json 绝对路径"""
    return reports_dir / ".audit_state.json"


def load_audit_state(reports_dir: Path) -> AuditState:
    """加载跨批审计状态机，若不存在则返回初始空状态"""
    state_file = get_audit_state_path(reports_dir)
    if not state_file.is_file():
        return AuditState()
    try:
        content = state_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            return AuditState()
        return AuditState.from_dict(data)
    except Exception:
        return AuditState()


def save_audit_state(state: AuditState, reports_dir: Path) -> Path:
    """
    原子写入 reports/.audit_state.json。
    杜绝多进程或异常退出导致 JSON 损坏。
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    state_file = get_audit_state_path(reports_dir)
    state.last_updated_at = datetime.now(timezone.utc).isoformat()

    json_str = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)

    tf = tempfile.NamedTemporaryFile("w", dir=reports_dir, delete=False, encoding="utf-8")
    temp_path = Path(tf.name)
    try:
        tf.write(json_str)
        tf.flush()
        tf.close()
        os.replace(temp_path, state_file)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return state_file


def get_inherited_items(state: AuditState) -> Dict[str, Any]:
    """提取待下一批连审继承的开放缺陷与未决伏笔"""
    return {
        "last_scope": state.last_scope,
        "completed_count": len(state.completed_chapters),
        "open_defects": list(state.open_defects),
        "foreshadowing_commitments": list(state.foreshadowing_commitments),
    }


def render_inherited_items_section(inherited: Dict[str, Any]) -> str:
    """渲染 Markdown 格式的跨批因果继承栏目"""
    defects = inherited.get("open_defects", [])
    commitments = inherited.get("foreshadowing_commitments", [])
    last_scope = inherited.get("last_scope") or "无"

    lines = [
        "## 🔄 跨批因果继承与未解决缺陷 (Inherited Items)",
        f"> 承接前序批次：`{last_scope}` | 继承开放缺陷：{len(defects)} 项 | 监控中伏笔：{len(commitments)} 个",
        "",
    ]

    if not defects and not commitments:
        lines.append("✅ **因果链条闭合良好：前序批次无未解决开放缺陷或悬空承诺。**\n")
        return "\n".join(lines)

    if defects:
        lines.append("### ⚠️ 上一批继承的开放缺陷 (需在本批次核验或闭环)")
        lines.append("| 序号 | 来源章节 | 严重度 | 缺陷类别 | 问题陈述 | 事实统一要求 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for idx, d in enumerate(defects, 1):
            chap = d.get("chapter", "-")
            sev = d.get("severity", "P1")
            cat = d.get("category", "causal")
            issue = d.get("issue", "").replace("|", "｜")
            fix = d.get("fix", "严格依事实对齐").replace("|", "｜")
            lines.append(f"| {idx} | 第{chap}章 | {sev} | {cat} | {issue} | {fix} |")
        lines.append("")

    if commitments:
        lines.append("### 📌 跨批连带伏笔承诺 (追踪闭环池)")
        lines.append("| 伏笔标签 | 埋入章节 | 状态 | 预期兑现/闭环说明 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for c in commitments:
            tag = c.get("tag", "-")
            orig = c.get("origin_chapter", "-")
            st = c.get("status", "pending")
            note = c.get("note", "待后文呼应")
            lines.append(f"| {tag} | 第{orig}章 | {st} | {note} |")
        lines.append("")

    return "\n".join(lines)
