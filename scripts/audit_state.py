# -*- coding: utf-8 -*-
"""
audit_state.py: 跨批长篇因果闭环与状态机管理 (reports/.audit_state.json)

功能职责：
1. 跨批次长篇审查连续性状态机落盘与原子更新；
2. 记录已完成章节列表、当前批次以及“上一批未解决的开放缺陷与伏笔承诺”；
3. 下一批连审启动时自动将其装载为 Inherited Items，在报告中呈现供宿主核验跨批因果一致性。
"""

from __future__ import annotations

import hashlib
import json
import math
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
        if not isinstance(data, dict):
            raise ValueError("审计状态根节点必须为对象")
        for key in ("last_scope", "last_updated_at"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} 必须为字符串")
        if not isinstance(data.get("completed_chapters", []), list):
            raise ValueError("completed_chapters 必须为列表")
        chapters = data.get("completed_chapters", [])
        if any(isinstance(chapter, bool) or not math.isfinite(float(chapter)) for chapter in chapters):
            raise ValueError("completed_chapters 必须包含有限章号")
        for key in ("open_defects", "foreshadowing_commitments", "resolved_items"):
            items = data.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError(f"{key} 必须为对象列表")
            for item in items:
                for text_key in ("severity", "category", "issue", "fix", "tag", "status", "note"):
                    if text_key in item and not isinstance(item[text_key], str):
                        raise ValueError(f"{key}.{text_key} 必须为字符串")
                if "chapter" in item:
                    chapter = item["chapter"]
                    if isinstance(chapter, bool) or not math.isfinite(float(chapter)):
                        raise ValueError(f"{key}.chapter 必须为有限章号")
                origin_chapter = item.get("origin_chapter")
                if origin_chapter is not None:
                    if isinstance(origin_chapter, bool) or not isinstance(origin_chapter, (int, float, str)) or not math.isfinite(float(origin_chapter)):
                        raise ValueError(f"{key}.origin_chapter 必须为有限章号或 null")
        return cls(
            schema_version=data.get("schema_version", STATE_SCHEMA_VERSION),
            last_scope=data.get("last_scope", ""),
            last_updated_at=data.get("last_updated_at", ""),
            completed_chapters=[float(x) for x in chapters],
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
    if not state_file.exists():
        return AuditState()
    content = state_file.read_text(encoding="utf-8")
    data = json.loads(content)
    return AuditState.from_dict(data)


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


def _chapter_key(chapter: Any) -> str:
    try:
        return f"{float(chapter):g}"
    except (TypeError, ValueError):
        return str(chapter)


def _same_chapter(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-4
    except (TypeError, ValueError):
        return False


def _lookup_text_version(text_versions: Optional[Dict[float, str]], chapter: Any) -> Optional[str]:
    if not text_versions:
        return None
    for key, value in text_versions.items():
        if _same_chapter(key, chapter):
            return value
    return None


def make_defect_id(source: str, checker: str, platform: str, chapter: Any, issue: str) -> str:
    """构造确定性缺陷的稳定身份，避免同一问题在重扫时重复累积。"""
    basis = "|".join(
        [
            str(source or ""),
            str(checker or ""),
            str(platform or ""),
            _chapter_key(chapter),
            str(issue or ""),
        ]
    )
    return "defect-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def merge_defect_scan(
    state: AuditState,
    detected_defects: List[Dict[str, Any]],
    covered_chapters: List[float],
    checkers: List[str],
    platform: str,
    text_versions: Optional[Dict[float, str]] = None,
    resolved_at: Optional[str] = None,
) -> None:
    """把本轮确定性发现合并进开放缺陷，仅替换本轮检查器真正覆盖的记录。

    专家、人工、未知来源、旧 schema 缺来源记录以及其他未覆盖平台记录一律保留。
    本轮重扫未再命中的自有确定性记录进入 resolved_items，并保存关闭依据与正文版本。
    """
    covered = [float(item) for item in covered_chapters]
    covered_checkers = {str(item) for item in checkers}
    normalized: List[Dict[str, Any]] = []
    for raw in detected_defects:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        chapter = item.get("chapter")
        if chapter is None:
            continue
        try:
            chapter_value = float(chapter)
        except (TypeError, ValueError):
            continue
        if not any(_same_chapter(chapter_value, target) for target in covered):
            continue
        checker = str(item.get("checker") or "deterministic")
        item_platform = str(item.get("platform") or platform)
        item["source"] = "deterministic"
        item["checker"] = checker
        item["platform"] = item_platform
        item["chapter"] = chapter_value
        item.setdefault("status", "open")
        version = _lookup_text_version(text_versions, chapter_value)
        if version:
            item["text_version"] = version
        item["id"] = item.get("id") or make_defect_id(
            "deterministic", checker, item_platform, chapter_value, item.get("issue", "")
        )
        normalized.append(item)

    current_ids = {str(item["id"]) for item in normalized}
    resolved_at_value = resolved_at or datetime.now(timezone.utc).isoformat()
    resolved_ids = {
        str(item.get("id"))
        for item in state.resolved_items
        if isinstance(item, dict) and item.get("id")
    }

    # 重新命中的问题不再是已解决状态，避免同一身份同时出现在两个列表。
    if current_ids:
        state.resolved_items = [
            item
            for item in state.resolved_items
            if not (isinstance(item, dict) and str(item.get("id")) in current_ids)
        ]
        resolved_ids -= current_ids

    remaining: List[Dict[str, Any]] = []
    for existing in state.open_defects:
        if not isinstance(existing, dict):
            remaining.append(existing)
            continue
        chapter = existing.get("chapter")
        checker = str(existing.get("checker") or "")
        existing_platform = str(existing.get("platform") or "")
        owned = (
            existing.get("source") == "deterministic"
            and checker in covered_checkers
            and existing_platform == platform
            and chapter is not None
            and any(_same_chapter(chapter, target) for target in covered)
        )
        if not owned:
            remaining.append(existing)
            continue

        computed_id = make_defect_id(
            "deterministic", checker, existing_platform, chapter, existing.get("issue", "")
        )
        existing_id = existing.get("id") or computed_id
        if str(existing_id) in current_ids or computed_id in current_ids:
            # 本轮重新命中，由下方以本轮证据替换，避免重复。
            continue

        resolved = dict(existing)
        resolved["id"] = existing_id
        resolved["status"] = "resolved"
        resolved["resolved_at"] = resolved_at_value
        resolved["resolution_reason"] = "本轮确定性预检未再命中"
        version = _lookup_text_version(text_versions, chapter)
        if version:
            resolved["text_version"] = version
        resolved["resolution_evidence"] = (
            "checker={}; platform={}; chapter={}; text_version={}".format(
                checker, platform, _chapter_key(chapter), resolved.get("text_version", "unknown")
            )
        )
        resolved["resolution_source"] = "deterministic_rescan"
        resolved_id = str(resolved.get("id"))
        if resolved_id not in resolved_ids:
            state.resolved_items.append(resolved)
            resolved_ids.add(resolved_id)

    added_ids = set()
    for item in normalized:
        item_id = str(item["id"])
        if item_id in added_ids:
            continue
        remaining.append(item)
        added_ids.add(item_id)
    state.open_defects = remaining


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
        lines.append("无已记录的开放缺陷或伏笔承诺；未执行语义核验。\n")
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
        lines.append("| 伏笔标签 | 来源章节 | 状态 | 来源线索/人工说明 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for c in commitments:
            tag = c.get("tag", "-")
            orig = c.get("origin_chapter")
            origin_text = f"第{orig}章" if orig is not None else "来源未记录"
            st = c.get("status", "pending")
            note = c.get("note", "待后文呼应")
            lines.append(f"| {tag} | {origin_text} | {st} | {note} |")
        lines.append("")

    return "\n".join(lines)
