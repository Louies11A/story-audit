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

from scripts.expert_results import (
    EXPERT_STATUS_COMPLETED,
    EXPERT_STATUS_FAILED,
    EXPERT_STATUS_NOT_EXECUTED,
    render_expert_status_table,
    summarize_expert_records,
)

STATE_SCHEMA_VERSION = 1

# F03：伏笔显式裁决动作与合法来源。存储层只登记作者或实际审查结果的裁决，
# 不根据正文标签或规则命中自行判定伏笔是否已回收。
VALID_FORESHADOWING_ACTIONS = ("confirm", "close", "reopen")
VALID_FORESHADOWING_SOURCES = ("author", "expert")
# 已生效裁决状态：命中后条目不得被旧标签或重复扫描重新激活。
ADJUDICATED_COMMITMENT_STATUSES = (
    "resolved",
    "closed",
    "confirmed",
    "done",
    "已确认",
    "已关闭",
    "已回收",
)
REOPENED_COMMITMENT_STATUS = "reopened"

# F05：版本感知的复审协调。待办条目按 (章号, 作用域) 唯一，解除后保留可追溯记录。
VALID_RECHECK_SCOPES = ("chapter", "seam")
VALID_COORDINATION_SOURCES = ("author", "expert")
RECHECK_STATUS_PENDING = "pending"
RECHECK_STATUS_RESOLVED = "resolved"


class ForeshadowingAdjudicationError(ValueError):
    """伏笔裁决无法执行：参数非法或目标条目不存在。"""


@dataclass
class AuditState:
    schema_version: int = STATE_SCHEMA_VERSION
    last_scope: str = ""
    last_updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_chapters: List[float] = field(default_factory=list)
    open_defects: List[Dict[str, Any]] = field(default_factory=list)
    foreshadowing_commitments: List[Dict[str, Any]] = field(default_factory=list)
    resolved_items: List[Dict[str, Any]] = field(default_factory=list)
    # F05 新增可选字段：旧状态文件缺少这些键时按空值加载，旧字段语义不变。
    chapter_versions: Dict[str, str] = field(default_factory=dict)
    pending_rechecks: List[Dict[str, Any]] = field(default_factory=list)
    version_events: List[Dict[str, Any]] = field(default_factory=list)
    # F08 新增可选字段：作者/专家问题处置记录（含处置时的正文版本）。
    finding_dispositions: List[Dict[str, Any]] = field(default_factory=list)

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
        chapter_versions = data.get("chapter_versions", {})
        if not isinstance(chapter_versions, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in chapter_versions.items()
        ):
            raise ValueError("chapter_versions 必须为字符串到字符串的章节指纹映射")
        for key in (
            "open_defects",
            "foreshadowing_commitments",
            "resolved_items",
            "pending_rechecks",
            "version_events",
            "finding_dispositions",
        ):
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
            chapter_versions=chapter_versions,
            pending_rechecks=list(data.get("pending_rechecks", [])),
            version_events=list(data.get("version_events", [])),
            finding_dispositions=list(data.get("finding_dispositions", [])),
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
        "finding_dispositions": list(state.finding_dispositions),
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


def _commitment_name(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("tag") or "").strip()


def _commitment_chapter(item: Any) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    chapter = item.get("origin_chapter")
    if chapter is None or isinstance(chapter, bool):
        return None
    try:
        value = float(chapter)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _matches_chapter_filter(item: Any, chapter: Optional[float]) -> bool:
    """章号过滤：来源章号未知的条目视为可匹配任意章号。"""
    if chapter is None:
        return True
    item_chapter = _commitment_chapter(item)
    return item_chapter is None or _same_chapter(item_chapter, chapter)


def is_foreshadowing_adjudicated(
    state: AuditState,
    name: str,
    origin_chapter: Optional[float] = None,
) -> bool:
    """判断某伏笔是否已有生效的人工裁决（已确认/已关闭）。

    已裁决条目必须压制正文旧标签与重复扫描：同一名称且来源章号一致（或来源未知）时
    不得重新进入待办池。重新开启会把历史记录状态改为 reopened，从而解除压制。
    """
    target = str(name or "").strip()
    if not target:
        return False
    for item in list(state.resolved_items) + list(state.foreshadowing_commitments):
        if _commitment_name(item) != target:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in ADJUDICATED_COMMITMENT_STATUSES:
            continue
        if origin_chapter is None:
            return True
        if _matches_chapter_filter(item, float(origin_chapter)):
            return True
    return False


def _find_adjudication_record(
    state: AuditState,
    name: str,
    origin_chapter: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """按名称与来源章号严格匹配历史裁决记录（来源未知的记录视为可匹配）。"""
    target = str(name or "").strip()
    for item in reversed(state.resolved_items):
        if not isinstance(item, dict) or _commitment_name(item) != target:
            continue
        if _matches_chapter_filter(item, origin_chapter):
            return item
    return None


def _latest_adjudication_record(state: AuditState, name: str) -> Optional[Dict[str, Any]]:
    """取同名伏笔中最近一次登记的历史裁决记录（重新开启时用于回退定位）。"""
    target = str(name or "").strip()
    for item in reversed(state.resolved_items):
        if isinstance(item, dict) and _commitment_name(item) == target:
            return item
    return None


def _append_adjudication_event(record: Dict[str, Any], event: Dict[str, Any]) -> None:
    history = record.get("adjudication_history")
    if not isinstance(history, list):
        history = []
    history.append(event)
    record["adjudication_history"] = history


def apply_foreshadowing_adjudication(
    state: AuditState,
    name: str,
    action: str,
    reason: str,
    evidence: str = "",
    source: str = "author",
    chapter: Optional[float] = None,
    text_version: Optional[str] = None,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """执行伏笔显式裁决：confirm 确认回收、close 关闭追踪、reopen 重新开启。

    返回结果字典：changed 为 False 表示幂等空操作；found 为 False 表示目标不存在。
    全部参数在改动状态前完成校验，非法输入不会留下半成品记录。
    """
    if not isinstance(name, str) or not name.strip():
        raise ForeshadowingAdjudicationError("伏笔名称 (name) 必须为非空字符串")
    if not isinstance(action, str) or action.strip().lower() not in VALID_FORESHADOWING_ACTIONS:
        raise ForeshadowingAdjudicationError(
            "伏笔动作 (action) 必须是 confirm/close/reopen 之一"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ForeshadowingAdjudicationError("必须提供裁决原因 (reason)，存储层不接受无依据的自动裁决")
    if not isinstance(source, str) or source.strip().lower() not in VALID_FORESHADOWING_SOURCES:
        raise ForeshadowingAdjudicationError("裁决来源 (source) 只能是 author 或 expert")
    if not isinstance(evidence, str):
        raise ForeshadowingAdjudicationError("裁决证据 (evidence) 必须为字符串")
    chapter_value: Optional[float] = None
    if chapter is not None:
        if isinstance(chapter, bool) or not isinstance(chapter, (int, float)):
            raise ForeshadowingAdjudicationError("章号 (chapter) 必须为有限非负数值或 None")
        chapter_value = float(chapter)
        if not math.isfinite(chapter_value) or chapter_value < 0:
            raise ForeshadowingAdjudicationError("章号 (chapter) 必须为有限非负数值或 None")
    if action.strip().lower() == "reopen":
        if chapter_value is None:
            raise ForeshadowingAdjudicationError("重新开启必须提供 chapter 以记录正文版本")
        if not isinstance(text_version, str) or not text_version.strip():
            raise ForeshadowingAdjudicationError("重新开启必须提供正文版本指纹 (text_version)")

    action = action.strip().lower()
    source = source.strip().lower()
    target_name = name.strip()
    timestamp = at or datetime.now(timezone.utc).isoformat()
    version = text_version.strip() if isinstance(text_version, str) else ""

    pending_all = [
        item
        for item in state.foreshadowing_commitments
        if isinstance(item, dict) and _commitment_name(item) == target_name
    ]
    pending = pending_all

    if action in ("confirm", "close"):
        status = "resolved" if action == "confirm" else "closed"
        known_origins: List[float] = []
        for item in pending:
            item_chapter = _commitment_chapter(item)
            if item_chapter is not None and not any(
                _same_chapter(item_chapter, known) for known in known_origins
            ):
                known_origins.append(item_chapter)
        if len(known_origins) > 1:
            # 同名伏笔来自多个章号时，必须用 chapter 指定要裁决的来源条目。
            if chapter_value is None or not any(
                _same_chapter(known, chapter_value) for known in known_origins
            ):
                raise ForeshadowingAdjudicationError(
                    "同名伏笔存在多个来源章号，请提供 chapter 指定要裁决的条目"
                )
            pending = [
                item
                for item in pending
                if _commitment_chapter(item) is None
                or _same_chapter(_commitment_chapter(item), chapter_value)
            ]
        origin: Optional[float] = next(
            (
                item_chapter
                for item_chapter in (_commitment_chapter(item) for item in pending)
                if item_chapter is not None
            ),
            chapter_value,
        )

        record = _find_adjudication_record(state, target_name, origin)
        if not pending:
            # 只有 (name, 指定章号) 确有条目时才幂等；否则必须为该章号登记独立裁决记录，
            # 避免该章旧标签在后续复审中以新的 origin 重新进入待办池。
            if record is not None:
                # 重复确认/关闭：幂等空操作，不重复计数、不重复写历史、不改写状态文件。
                return {
                    "name": target_name,
                    "action": action,
                    "changed": False,
                    "found": True,
                    "idempotent": True,
                    "removed": 0,
                    "record": record,
                }
            # 作者或实际审查结果可以对尚未登记的伏笔直接裁决；依据与来源必须完整。
            record = {
                "tag": target_name,
                "origin_chapter": origin,
                "status": status,
                "note": "人工裁决关闭追踪，未发现正文标签登记。",
            }
            state.resolved_items.append(record)
            created = True
        else:
            created = record is None
            if record is None:
                record = {
                    "tag": target_name,
                    "origin_chapter": origin,
                    "status": status,
                    "note": str(pending[0].get("note") or "").strip()
                    or "人工裁决关闭追踪。",
                }
                state.resolved_items.append(record)

        event = {
            "action": action,
            "at": timestamp,
            "reason": reason.strip(),
            "source": source,
            "evidence": evidence.strip(),
            "chapter": chapter_value,
            "text_version": version,
        }
        _append_adjudication_event(record, event)
        record["tag"] = target_name
        if record.get("origin_chapter") is None and origin is not None:
            record["origin_chapter"] = origin
        record["status"] = status
        record["adjudicated_at"] = timestamp
        record["resolution_source"] = source
        record["resolution_reason"] = reason.strip()
        record["resolution_evidence"] = evidence.strip()
        if version:
            record["text_version"] = version
        state.foreshadowing_commitments = [
            item for item in state.foreshadowing_commitments if item not in pending
        ]
        return {
            "name": target_name,
            "action": action,
            "changed": True,
            "found": True,
            "idempotent": False,
            "created": created,
            "removed": len(pending),
            "record": record,
        }

    # action == "reopen"
    record = _find_adjudication_record(state, target_name, chapter_value)
    if record is None:
        # 来源章号与本次关联章号不同（例如按新章节线索重新开启）时回退到最近一次裁决记录。
        record = _latest_adjudication_record(state, target_name)
    if record is None:
        return {
            "name": target_name,
            "action": action,
            "changed": False,
            "found": False,
            "idempotent": False,
            "removed": 0,
            "record": None,
        }
    already_pending = bool(pending_all)
    if already_pending and record.get("status") == REOPENED_COMMITMENT_STATUS:
        return {
            "name": target_name,
            "action": action,
            "changed": False,
            "found": True,
            "idempotent": True,
            "removed": 0,
            "record": record,
        }

    event = {
        "action": "reopen",
        "at": timestamp,
        "reason": reason.strip(),
        "source": source,
        "evidence": evidence.strip(),
        "chapter": chapter_value,
        "text_version": version,
    }
    _append_adjudication_event(record, event)
    record["status"] = REOPENED_COMMITMENT_STATUS
    record["reopened_at"] = timestamp
    record["reopen_reason"] = reason.strip()
    record["reopen_source"] = source
    record["reopen_chapter"] = chapter_value
    record["reopen_text_version"] = version

    if not already_pending:
        origin = _commitment_chapter(record)
        history = record.get("adjudication_history")
        state.foreshadowing_commitments.append(
            {
                "tag": target_name,
                "origin_chapter": origin,
                "status": "pending",
                "note": str(record.get("note") or "").strip() or "人工裁决重新开启追踪。",
                "reopened_at": timestamp,
                "reopen_reason": reason.strip(),
                "reopen_source": source,
                "reopen_chapter": chapter_value,
                "reopen_text_version": version,
                "adjudication_history": list(history) if isinstance(history, list) else [],
            }
        )
    return {
        "name": target_name,
        "action": action,
        "changed": True,
        "found": True,
        "idempotent": False,
        "removed": 0,
        "record": record,
    }


def make_patch_id(chapter: Any, patch: Any) -> str:
    """构造补丁稳定身份：章号 + 目标行 + 锚点 + 替换文本。"""
    basis = "|".join(
        [
            _chapter_key(chapter),
            str(getattr(patch, "target_line", "")),
            str(getattr(patch, "old_text", "")),
            str(getattr(patch, "new_text", "")),
            str(getattr(patch, "context_before", "")),
            str(getattr(patch, "context_after", "")),
        ]
    )
    return "patch-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def record_chapter_version(state: AuditState, chapter: Any, text_version: str) -> str:
    """登记某章当前已完成审查的正文指纹，返回写入键。"""
    key = _chapter_key(chapter)
    if isinstance(text_version, str) and text_version:
        state.chapter_versions[key] = text_version
    return key


def get_chapter_version(state: AuditState, chapter: Any) -> str:
    """读取某章已记录（已审）的正文指纹；未记录时返回空字符串。"""
    key = _chapter_key(chapter)
    value = state.chapter_versions.get(key)
    if isinstance(value, str) and value:
        return value
    for raw_key, raw_value in state.chapter_versions.items():
        if isinstance(raw_value, str) and raw_value and _same_chapter(raw_key, chapter):
            return raw_value
    return ""


def is_chapter_version_audited(state: AuditState, chapter: Any, text_version: Optional[str]) -> bool:
    """判断某章当前版本是否已审：章号已完成且记录指纹与给定指纹一致。

    完成章号本身不再代表当前版本已审；旧状态文件没有指纹记录时同样返回 False。
    """
    if not any(_same_chapter(chapter, item) for item in state.completed_chapters):
        return False
    if not isinstance(text_version, str) or not text_version:
        return False
    return get_chapter_version(state, chapter) == text_version


def _recheck_id(chapter: Any, scope: str) -> str:
    basis = f"{_chapter_key(chapter)}|{scope}"
    return "recheck-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def register_pending_recheck(
    state: AuditState,
    chapter: Any,
    scope: str,
    reason: str,
    text_version: str = "",
    previous_text_version: str = "",
    trigger_chapter: Any = None,
    patch_id: str = "",
    source: str = "apply_fix",
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """登记待复审项；同一 (章号, 作用域) 只保留一条，重复登记原位更新。"""
    timestamp = at or datetime.now(timezone.utc).isoformat()
    chapter_value = float(chapter)
    scope_value = str(scope)
    trigger_value = chapter_value if trigger_chapter is None else float(trigger_chapter)
    recheck_id = _recheck_id(chapter_value, scope_value)
    for item in state.pending_rechecks:
        if isinstance(item, dict) and item.get("id") == recheck_id:
            item.update(
                {
                    "chapter": chapter_value,
                    "scope": scope_value,
                    "status": RECHECK_STATUS_PENDING,
                    "reason": reason,
                    "text_version": text_version,
                    "previous_text_version": previous_text_version,
                    "trigger_chapter": trigger_value,
                    "patch_id": patch_id,
                    "source": source,
                    "updated_at": timestamp,
                }
            )
            for key in (
                "resolution",
                "resolution_reason",
                "resolution_source",
                "resolution_text_version",
                "resolved_at",
            ):
                item[key] = ""
            return item

    entry = {
        "id": recheck_id,
        "chapter": chapter_value,
        "scope": scope_value,
        "status": RECHECK_STATUS_PENDING,
        "reason": reason,
        "text_version": text_version,
        "previous_text_version": previous_text_version,
        "trigger_chapter": trigger_value,
        "patch_id": patch_id,
        "source": source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "resolution": "",
        "resolution_reason": "",
        "resolution_source": "",
        "resolution_text_version": "",
        "resolved_at": "",
    }
    state.pending_rechecks.append(entry)
    return entry


def resolve_pending_rechecks(
    state: AuditState,
    chapter: Any,
    scope: Optional[str] = None,
    reason: str = "",
    source: str = "",
    resolution: str = "reaudit",
    text_version: str = "",
    at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """清除匹配的待办复审项（保留解除依据），返回被清除的条目。"""
    timestamp = at or datetime.now(timezone.utc).isoformat()
    resolved: List[Dict[str, Any]] = []
    for item in state.pending_rechecks:
        if not isinstance(item, dict) or item.get("status") != RECHECK_STATUS_PENDING:
            continue
        if not _same_chapter(item.get("chapter"), chapter):
            continue
        if scope is not None and str(item.get("scope") or "") != str(scope):
            continue
        item["status"] = RECHECK_STATUS_RESOLVED
        item["resolution"] = resolution
        item["resolution_reason"] = reason
        item["resolution_source"] = source
        item["resolution_text_version"] = text_version
        item["resolved_at"] = timestamp
        item["updated_at"] = timestamp
        resolved.append(item)
    return resolved


def collect_pending_rechecks(
    state: AuditState,
    include_resolved: bool = False,
) -> List[Dict[str, Any]]:
    """收集待办复审项（可选含已解除历史），按章号与作用域排序返回副本。"""
    items: List[Dict[str, Any]] = []
    for item in state.pending_rechecks:
        if not isinstance(item, dict):
            continue
        if not include_resolved and item.get("status") != RECHECK_STATUS_PENDING:
            continue
        items.append(dict(item))

    def sort_key(item: Dict[str, Any]):
        try:
            chapter = float(item.get("chapter"))
        except (TypeError, ValueError, OverflowError):
            chapter = 0.0
        return (chapter, str(item.get("scope") or ""), str(item.get("id") or ""))

    return sorted(items, key=sort_key)


def append_version_event(state: AuditState, event: Dict[str, Any]) -> Dict[str, Any]:
    """追加版本事件（补丁写入、复审解除、问题关闭等），自动补时间戳。"""
    record = dict(event)
    record.setdefault("at", datetime.now(timezone.utc).isoformat())
    state.version_events.append(record)
    return record


def render_inherited_items_section(inherited: Dict[str, Any]) -> str:
    """渲染 Markdown 格式的跨批因果继承栏目"""
    defects = inherited.get("open_defects", [])
    commitments = inherited.get("foreshadowing_commitments", [])
    expert_results = inherited.get("expert_results", []) or []
    last_scope = inherited.get("last_scope") or "无"

    header = (
        f"> 承接前序批次：`{last_scope}` | 继承开放缺陷：{len(defects)} 项 "
        f"| 监控中伏笔：{len(commitments)} 个"
    )
    if expert_results:
        counts = summarize_expert_records(expert_results)
        header += (
            f" | 专家结果：完成 {counts[EXPERT_STATUS_COMPLETED]} / "
            f"失败 {counts[EXPERT_STATUS_FAILED]} / "
            f"未执行 {counts[EXPERT_STATUS_NOT_EXECUTED]}"
        )
    lines = [
        "## 🔄 跨批因果继承与未解决缺陷 (Inherited Items)",
        header,
        "",
    ]

    if not defects and not commitments and not expert_results:
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
            markers: List[str] = []
            # 专家缺陷身份包含平台（与确定性缺陷约定一致），跨平台归档会保留多条记录，
            # 此处标注平台，避免读者把同一发现的不同平台裁决误读为重复条目。
            if str(d.get("source") or "") == "expert":
                platform = str(d.get("platform") or "")
                markers.append(f"专家：{platform}" if platform else "专家")
            rule = d.get("rule") if isinstance(d.get("rule"), dict) else {}
            if rule:
                markers.append(
                    "规则 {}@{}".format(
                        rule.get("rule_id") or "-", rule.get("rule_version") or "-"
                    )
                )
            if markers:
                cat = f"{cat}（{'；'.join(markers)}）"
            issue = d.get("issue", "").replace("|", "｜")
            extras: List[str] = []
            if rule:
                for label, key in (("命中条件", "condition"), ("阈值", "threshold"), ("上下文", "context")):
                    value = str(rule.get(key) or "").strip()
                    if value:
                        extras.append(f"{label}：{value[:80]}")
            disposition = d.get("disposition") if isinstance(d.get("disposition"), dict) else {}
            if disposition:
                label = {
                    "accepted": "接受",
                    "deferred": "暂缓",
                    "false_positive": "误报",
                }.get(str(disposition.get("decision") or ""), str(disposition.get("decision") or "-"))
                if disposition.get("needs_reverification"):
                    state_text = "正文已变化，需重新核验"
                elif disposition.get("exempt"):
                    state_text = "已免除跟踪"
                else:
                    state_text = "仍保留在开放缺陷"
                extras.append(
                    "处置：{}（{}；{}）".format(
                        label, str(disposition.get("source") or "-"), state_text
                    )
                )
            if extras:
                issue = f"{issue}｜{'；'.join(extras)}"
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

    if expert_results:
        lines.append("### 🧑‍🔬 专家执行状态 (Expert Execution Status)")
        lines.extend(render_expert_status_table(expert_results))
        lines.append("")

    return "\n".join(lines)
