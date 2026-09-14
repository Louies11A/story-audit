# -*- coding: utf-8 -*-
"""
expert_results.py: 宿主专家执行结果的强类型契约、幂等归档与汇总渲染

职责边界：
1. 本模块只接收宿主实际执行的专家结果，不调用任何模型，也不会替宿主判定专家是否执行；
2. 契约字段覆盖专家角色、执行状态、审查范围、正文指纹、发现列表与证据；
3. 归档按“专家 + 范围 + 正文指纹 + 发现身份”去重，重复提交不重复计数；
4. 提交指纹与当前正文不一致的结果只保留追溯记录，并标记为过期，不参与当前裁决。

严格依赖 Python 3.8+ 标准库 dataclasses、hashlib、json、pathlib。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scripts.types import Finding

EXPERT_STATUS_COMPLETED = "completed"
EXPERT_STATUS_FAILED = "failed"
EXPERT_STATUS_NOT_EXECUTED = "not_executed"
VALID_EXPERT_STATUSES = (
    EXPERT_STATUS_COMPLETED,
    EXPERT_STATUS_FAILED,
    EXPERT_STATUS_NOT_EXECUTED,
)
EXPERT_STATUS_LABELS = {
    EXPERT_STATUS_COMPLETED: "完成",
    EXPERT_STATUS_FAILED: "失败",
    EXPERT_STATUS_NOT_EXECUTED: "未执行",
}

# 与 SKILL.md 四专家矩阵对应的标准角色名；宿主也可以使用自定义角色字符串。
EXPERT_ASSET_AUDITOR = "账本专员"
EXPERT_CONTINUITY_GUARD = "事实专员"
EXPERT_STYLE_RHYTHM = "排版质检"
EXPERT_ADVERSARIAL_CRITIC = "对抗审判"

EXPERT_RESULTS_DIRNAME = "专家审查"
EXPERT_RESULT_STORE_FILENAME = "expert_results.json"
EXPERT_SUMMARY_FILENAME = "EXPERT_SUMMARY.md"
STORE_SCHEMA_VERSION = 1

_FINDING_TEXT_FIELDS = ("location", "evidence", "issue", "fix")
_FINDING_FIELDS = (
    "severity",
    "category",
    "location",
    "evidence",
    "issue",
    "fix",
    "line_number",
    "flaw_type",
)
_SEVERITY_ORDER = ("P0", "P1", "P2", "P3")


def _iterable_chapters(chapters: Any) -> List[Any]:
    if isinstance(chapters, (str, bytes, bytearray)) or not isinstance(chapters, IterableABC):
        raise ValueError("专家结果的 chapters 必须是章号序列")
    return list(chapters)


def normalize_chapters(chapters: Any) -> List[float]:
    """规范化审查范围：去重、排序并拒绝非数值或非有限章号。"""
    normalized: List[float] = []
    for raw in _iterable_chapters(chapters):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("专家结果的 chapters 只能包含数值章号")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("专家结果的 chapters 只能包含数值章号")
        if not math.isfinite(value) or value < 0:
            raise ValueError("专家结果的 chapters 只能包含有限的非负章号")
        if not any(abs(value - item) < 1e-4 for item in normalized):
            normalized.append(value)
    if not normalized:
        raise ValueError("专家结果必须包含至少一个审查章号")
    return sorted(normalized)


def format_chapter_label(chapter: Any) -> str:
    """章号展示标签：整数不带小数，小数保留原始精度。"""
    try:
        value = float(chapter)
    except (TypeError, ValueError, OverflowError):
        return str(chapter)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def format_chapter_key(chapter: Any) -> str:
    """章号稳定键：整数补足三位，小数保留有效位。"""
    try:
        value = float(chapter)
    except (TypeError, ValueError, OverflowError):
        return str(chapter)
    if value.is_integer():
        return f"{int(value):03d}"
    return f"{value:g}"


def format_scope_label(chapters: Sequence[float]) -> str:
    """渲染审查范围标签：单章为“第N章”，多章为“第A-B章”。"""
    ordered = sorted(float(item) for item in chapters)
    if not ordered:
        return "范围未记录"
    if len(ordered) == 1 or abs(ordered[0] - ordered[-1]) < 1e-4:
        return f"第{format_chapter_label(ordered[0])}章"
    return f"第{format_chapter_label(ordered[0])}-{format_chapter_label(ordered[-1])}章"


def compute_text_fingerprint(chapter_texts: Dict[float, str]) -> str:
    """计算审查范围正文指纹。

    单章指纹等于该章正文的 SHA-256，与确定性预检写入的 text_version 保持一致；
    多章范围按章号排序组合各章摘要，得到稳定的范围复合指纹。
    """
    items: List[Tuple[float, str]] = []
    for chapter, text in chapter_texts.items():
        try:
            value = float(chapter)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("正文指纹的章节键必须是数值章号")
        if not math.isfinite(value):
            raise ValueError("正文指纹的章节键必须是有限章号")
        items.append((value, str(text)))
    if not items:
        raise ValueError("计算正文指纹至少需要一个章节文本")
    items.sort(key=lambda item: item[0])
    if len(items) == 1:
        return hashlib.sha256(items[0][1].encode("utf-8")).hexdigest()
    basis = "\n".join(
        "{}={}".format(format_chapter_key(chapter), hashlib.sha256(text.encode("utf-8")).hexdigest())
        for chapter, text in items
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _finding_identity(finding: Finding) -> str:
    return "|".join(
        [
            finding.severity,
            finding.category,
            finding.location or "",
            str(finding.issue or ""),
            finding.evidence or "",
            finding.fix or "",
        ]
    )


def make_expert_result_id(
    expert: str,
    chapters: Sequence[float],
    text_fingerprint: str,
    findings: Sequence[Finding],
) -> str:
    """构造专家结果的稳定身份：专家 + 范围 + 正文指纹 + 发现身份。"""
    chapter_key = ",".join(
        format_chapter_key(item) for item in sorted(float(item) for item in chapters)
    )
    finding_keys = sorted({_finding_identity(item) for item in findings})
    basis = "|".join(
        [
            str(expert or "").strip(),
            chapter_key,
            str(text_fingerprint or "").strip(),
            ";".join(finding_keys),
        ]
    )
    return "expert-result-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def finding_from_dict(raw: Dict[str, Any]) -> Finding:
    """把字典发现项对齐到统一 Finding 契约。"""
    if not isinstance(raw, dict):
        raise ValueError("专家发现必须是对象或 Finding 实例")
    missing = [key for key in ("severity", "category", "issue", "fix") if not raw.get(key)]
    if missing:
        raise ValueError("专家发现缺少必需字段: " + ", ".join(missing))
    payload: Dict[str, Any] = {}
    for key in _FINDING_FIELDS:
        value = raw.get(key)
        if key in _FINDING_TEXT_FIELDS:
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError(f"专家发现的 {key} 必须为字符串")
        if key == "line_number" and value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("专家发现的 line_number 必须为整数或 null")
        if key == "flaw_type" and value is not None and not isinstance(value, str):
            raise ValueError("专家发现的 flaw_type 必须为字符串或 null")
        if key in ("severity", "category") and not isinstance(value, str):
            raise ValueError(f"专家发现的 {key} 必须为字符串")
        payload[key] = value
    return Finding(**payload)


def _coerce_finding(raw: Any) -> Finding:
    if isinstance(raw, Finding):
        return raw
    if isinstance(raw, dict):
        return finding_from_dict(raw)
    raise ValueError("发现列表只能包含 Finding 实例或字段字典")


@dataclass
class ExpertResult:
    """宿主实际执行的单个专家结果契约。

    字段约束：
    - expert: 专家角色标识（可使用 EXPERT_* 常量或自定义角色名）
    - status: completed | failed | not_executed
    - chapters: 审查范围章号列表（单章或范围）
    - text_fingerprint: 审查时正文指纹；completed 必填，用于过期判定
    - findings: 发现列表，复用 scripts.types.Finding 字段契约
    - evidence / reason: 专家证据与失败或未执行原因
    """

    expert: str
    status: str
    chapters: List[float] = field(default_factory=list)
    text_fingerprint: str = ""
    findings: List[Finding] = field(default_factory=list)
    evidence: str = ""
    reason: str = ""
    source: str = "host"
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.expert, str) or not self.expert.strip():
            raise ValueError("专家角色 (expert) 必须为非空字符串")
        self.expert = self.expert.strip()
        if not isinstance(self.status, str) or self.status.strip().lower() not in VALID_EXPERT_STATUSES:
            raise ValueError("执行状态 (status) 必须是 completed/failed/not_executed")
        self.status = self.status.strip().lower()
        self.chapters = normalize_chapters(self.chapters)
        if not isinstance(self.text_fingerprint, str):
            raise ValueError("正文指纹 (text_fingerprint) 必须为字符串")
        self.text_fingerprint = self.text_fingerprint.strip()
        if self.status == EXPERT_STATUS_COMPLETED and not self.text_fingerprint:
            raise ValueError("已完成的专家结果必须提供正文指纹 (text_fingerprint)")
        self.findings = [_coerce_finding(item) for item in self.findings]
        if self.status == EXPERT_STATUS_NOT_EXECUTED and self.findings:
            raise ValueError("未执行的专家结果不得包含发现，也不能被标记为完成")
        for name in ("evidence", "reason", "source", "recorded_at"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} 必须为字符串")
            setattr(self, name, value.strip())
        if not self.source:
            self.source = "host"

    @property
    def status_label(self) -> str:
        return EXPERT_STATUS_LABELS.get(self.status, self.status)

    @property
    def scope_label(self) -> str:
        return format_scope_label(self.chapters)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": make_expert_result_id(
                self.expert, self.chapters, self.text_fingerprint, self.findings
            ),
            "expert": self.expert,
            "status": self.status,
            "status_label": self.status_label,
            "scope": self.scope_label,
            "chapters": [float(item) for item in self.chapters],
            "text_fingerprint": self.text_fingerprint,
            "findings": [item.to_dict() for item in self.findings],
            "evidence": self.evidence,
            "reason": self.reason,
            "source": self.source,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExpertResult":
        if not isinstance(data, dict):
            raise ValueError("专家结果必须是对象")
        raw_findings = data.get("findings", [])
        if raw_findings is None:
            raw_findings = []
        if not isinstance(raw_findings, (list, tuple)):
            raise ValueError("findings 必须是发现列表")
        return cls(
            expert=data.get("expert", ""),
            status=data.get("status", ""),
            chapters=data.get("chapters", []),
            text_fingerprint=data.get("text_fingerprint", ""),
            findings=[item for item in raw_findings],
            evidence=data.get("evidence", ""),
            reason=data.get("reason", ""),
            source=data.get("source", "host"),
            recorded_at=data.get("recorded_at", ""),
        )


def get_expert_results_dir(reports_dir: Path) -> Path:
    """专家结果归档目录：reports/专家审查/"""
    return Path(reports_dir) / EXPERT_RESULTS_DIRNAME


def get_expert_result_store_path(reports_dir: Path) -> Path:
    """专家结果 JSON 归档路径。"""
    return get_expert_results_dir(reports_dir) / EXPERT_RESULT_STORE_FILENAME


def get_expert_summary_path(reports_dir: Path) -> Path:
    """专家结果 Markdown 汇总路径。"""
    return get_expert_results_dir(reports_dir) / EXPERT_SUMMARY_FILENAME


def load_expert_result_records(store_path: Path) -> List[Dict[str, Any]]:
    """读取专家结果归档；文件不存在返回空列表，结构损坏抛出 ValueError。"""
    path = Path(store_path)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        records = data.get("results", [])
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError("专家结果归档根节点必须是对象或列表")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("专家结果归档 results 必须是对象列表")
    return [dict(item) for item in records]


def build_expert_result_record(
    result: ExpertResult,
    *,
    current_fingerprint: str = "",
    stale: bool = False,
    stale_reason: str = "",
    platform: str = "generic",
    recorded_at: str = "",
) -> Dict[str, Any]:
    """把契约对象转换为可归档记录，附加过期判定与统计字段。"""
    record = result.to_dict()
    record["platform"] = platform
    record["current_fingerprint"] = current_fingerprint or ""
    record["stale"] = bool(stale)
    record["stale_reason"] = stale_reason or ""
    record["finding_count"] = len(result.findings)
    record["severity_counts"] = {
        severity: sum(1 for item in result.findings if item.severity == severity)
        for severity in _SEVERITY_ORDER
    }
    record["recorded_at"] = recorded_at or result.recorded_at or datetime.now(timezone.utc).isoformat()
    return record


def merge_expert_result_records(
    existing: Sequence[Dict[str, Any]],
    incoming: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """按结果身份幂等合并：重复提交不重复计数，只刷新过期判定。"""
    merged: List[Dict[str, Any]] = [dict(item) for item in existing]
    index: Dict[str, int] = {
        str(item.get("id")): position for position, item in enumerate(merged) if item.get("id")
    }
    changes = 0
    for record in incoming:
        if not isinstance(record, dict) or not record.get("id"):
            raise ValueError("专家结果缺少稳定身份 id，无法幂等归档")
        record_id = str(record["id"])
        position = index.get(record_id)
        if position is None:
            merged.append(dict(record))
            index[record_id] = len(merged) - 1
            changes += 1
            continue
        current = merged[position]
        for key in ("stale", "stale_reason", "current_fingerprint"):
            if current.get(key) != record.get(key):
                current[key] = record.get(key)
                changes += 1
    return merged, changes


def summarize_expert_records(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """统计专家执行状态：记录总数、完成、失败、未执行与过期数量。"""
    summary = {"total": len(records)}
    for status in VALID_EXPERT_STATUSES:
        summary[status] = sum(1 for item in records if item.get("status") == status)
    summary["stale"] = sum(1 for item in records if item.get("stale"))
    return summary


def refresh_expert_records_staleness(
    records: Sequence[Dict[str, Any]],
    fingerprint_lookup,
) -> List[Dict[str, Any]]:
    """按当前正文重新判定过期状态，返回呈现副本（不改写归档记录本身）。

    指纹缺失（失败/未执行）的记录维持登记时的状态；无法核对当前正文时一律视为过期，
    避免把无法复现的旧结论继续呈现为“完成”。归档 JSON 只由提交路径写入。
    """
    refreshed: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        submitted = str(item.get("text_fingerprint") or "")
        if submitted:
            try:
                chapters = [float(value) for value in (item.get("chapters") or [])]
            except (TypeError, ValueError, OverflowError):
                chapters = []
            current: Optional[str] = None
            if chapters:
                try:
                    current = fingerprint_lookup(chapters)
                except Exception:
                    current = None
            if current is None:
                item["stale"] = True
                item["stale_reason"] = "无法核对当前正文指纹，需重新执行专家审查"
                item["current_fingerprint"] = ""
            elif current == submitted:
                item["stale"] = False
                item["stale_reason"] = ""
                item["current_fingerprint"] = current
            else:
                item["stale"] = True
                item["stale_reason"] = "正文指纹与当前正文不一致，需重新执行专家审查"
                item["current_fingerprint"] = current
        refreshed.append(item)
    return refreshed


def expert_summary_signature(records: Sequence[Dict[str, Any]]) -> str:
    """专家汇总内容签名：只由执行状态、范围、发现统计与过期判定决定，忽略生成时间。"""
    basis: List[str] = []
    for record in _sorted_records(records):
        basis.append(
            "|".join(
                [
                    str(record.get("id") or ""),
                    str(record.get("expert") or ""),
                    str(record.get("status") or ""),
                    str(record.get("scope") or ""),
                    "1" if record.get("stale") else "0",
                    str(record.get("stale_reason") or ""),
                    str(record.get("finding_count") or 0),
                ]
            )
        )
    return hashlib.sha256("\n".join(basis).encode("utf-8")).hexdigest()


def _sorted_records(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        (item for item in records if isinstance(item, dict)),
        key=lambda item: (
            str(item.get("expert") or ""),
            str(item.get("scope") or ""),
            str(item.get("id") or ""),
        ),
    )


def _status_label(record: Dict[str, Any]) -> str:
    label = record.get("status_label")
    if isinstance(label, str) and label:
        return label
    status = str(record.get("status") or "")
    return EXPERT_STATUS_LABELS.get(status, status or "状态未记录")


def _display_status(record: Dict[str, Any]) -> str:
    """状态栏展示：过期结果必须显示为已过期，而不是沿用登记时的完成状态。"""
    if record.get("stale"):
        return "已过期（需重新执行专家审查）"
    return _status_label(record)


def _severity_counts(record: Dict[str, Any]) -> Dict[str, int]:
    counts = record.get("severity_counts")
    if not isinstance(counts, dict):
        findings = record.get("findings") or []
        counts = {
            severity: sum(
                1
                for item in findings
                if isinstance(item, dict) and item.get("severity") == severity
            )
            for severity in _SEVERITY_ORDER
        }
    result: Dict[str, int] = {}
    for severity in _SEVERITY_ORDER:
        try:
            result[severity] = max(int(counts.get(severity) or 0), 0)
        except (TypeError, ValueError):
            result[severity] = 0
    return result


def _finding_summary(record: Dict[str, Any]) -> str:
    counts = _severity_counts(record)
    parts = [f"{severity}×{counts[severity]}" for severity in _SEVERITY_ORDER if counts[severity]]
    return " / ".join(parts) if parts else "—"


def _record_note(record: Dict[str, Any]) -> str:
    if record.get("stale"):
        reason = str(record.get("stale_reason") or "正文指纹与当前章节不一致")
        return f"⚠️ 结果已过期：{reason}"
    reason = str(record.get("reason") or "").strip()
    return reason.replace("|", "｜") if reason else "—"


def render_expert_status_table(records: Sequence[Dict[str, Any]]) -> List[str]:
    """渲染专家执行状态表格行（含表头），供审查报告继承栏目与汇总复用。"""
    lines = [
        "| 专家 | 状态 | 审查范围 | 发现 | 备注 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for record in _sorted_records(records):
        lines.append(
            "| {} | {} | {} | {} | {} |".format(
                str(record.get("expert") or "-"),
                _display_status(record),
                str(record.get("scope") or "范围未记录"),
                _finding_summary(record),
                _record_note(record),
            )
        )
    return lines


def render_expert_summary_markdown(
    records: Sequence[Dict[str, Any]],
    *,
    generated_at: str = "",
    scope_label: str = "",
) -> str:
    """渲染专家结果 Markdown 汇总：执行状态总览 + 发现明细。"""
    counts = summarize_expert_records(records)
    now = generated_at or datetime.now(timezone.utc).isoformat()
    lines = [
        "# 🧑🔬 专家结果汇总 (Expert Results Summary)",
        "",
        "> 生成时间：{} | 范围：{} | 记录：{} 条 | 完成：{} | 失败：{} | 未执行：{} | 已过期：{}".format(
            now,
            scope_label or "全部已归档结果",
            counts["total"],
            counts[EXPERT_STATUS_COMPLETED],
            counts[EXPERT_STATUS_FAILED],
            counts[EXPERT_STATUS_NOT_EXECUTED],
            counts["stale"],
        ),
        "> 专家调用由宿主负责；本汇总仅归档并呈现实际执行结果，未执行的审查不会被标记为完成。",
        "",
        "---",
        "",
        "## 一、执行状态总览",
        "",
    ]
    lines.extend(render_expert_status_table(records))
    lines.extend(["", "---", "", "## 二、发现明细", ""])

    detail_blocks: List[str] = []
    for record in _sorted_records(records):
        heading = "### {} · {} · {}".format(
            str(record.get("expert") or "-"),
            _display_status(record),
            str(record.get("scope") or "范围未记录"),
        )
        if record.get("stale"):
            heading += " · ⚠️ 不参与当前裁决"
        detail_blocks.append(heading)
        reason = str(record.get("reason") or "").strip()
        evidence = str(record.get("evidence") or "").strip()
        if reason:
            detail_blocks.append(f"* 说明：{reason}")
        if evidence:
            detail_blocks.append(f"* 证据：{evidence}")
        findings = record.get("findings") or []
        if not findings:
            detail_blocks.append("* 本次未提交发现明细。")
        else:
            for item in findings:
                if not isinstance(item, dict):
                    continue
                detail_blocks.append(
                    "* [{}][{}] {}".format(
                        item.get("severity", "-"),
                        item.get("category", "-"),
                        str(item.get("issue") or "").strip() or "未填写问题陈述",
                    )
                )
                location = str(item.get("location") or "").strip()
                if location:
                    detail_blocks.append(f"  * 位置：{location}")
                fix = str(item.get("fix") or "").strip()
                if fix:
                    detail_blocks.append(f"  * 建议：{fix}")
                item_evidence = str(item.get("evidence") or "").strip()
                if item_evidence:
                    detail_blocks.append(f"  * 证据：{item_evidence}")
        detail_blocks.append("")

    if detail_blocks:
        lines.extend(detail_blocks)
    else:
        lines.append("暂无专家结果记录。")
        lines.append("")
    return "\n".join(lines)


def expert_result_summary_entries(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提取报告继承栏目需要的最小专家状态视图。"""
    entries: List[Dict[str, Any]] = []
    for record in _sorted_records(records):
        entries.append(
            {
                "expert": str(record.get("expert") or ""),
                "status": str(record.get("status") or ""),
                "status_label": _status_label(record),
                "scope": str(record.get("scope") or ""),
                "finding_count": int(record.get("finding_count") or 0),
                "severity_counts": _severity_counts(record),
                "stale": bool(record.get("stale")),
                "stale_reason": str(record.get("stale_reason") or ""),
                "reason": str(record.get("reason") or ""),
            }
        )
    return entries


__all__ = [
    "EXPERT_STATUS_COMPLETED",
    "EXPERT_STATUS_FAILED",
    "EXPERT_STATUS_NOT_EXECUTED",
    "VALID_EXPERT_STATUSES",
    "EXPERT_STATUS_LABELS",
    "EXPERT_ASSET_AUDITOR",
    "EXPERT_CONTINUITY_GUARD",
    "EXPERT_STYLE_RHYTHM",
    "EXPERT_ADVERSARIAL_CRITIC",
    "ExpertResult",
    "normalize_chapters",
    "format_chapter_label",
    "format_chapter_key",
    "format_scope_label",
    "compute_text_fingerprint",
    "make_expert_result_id",
    "finding_from_dict",
    "get_expert_results_dir",
    "get_expert_result_store_path",
    "get_expert_summary_path",
    "load_expert_result_records",
    "build_expert_result_record",
    "merge_expert_result_records",
    "summarize_expert_records",
    "refresh_expert_records_staleness",
    "expert_summary_signature",
    "render_expert_status_table",
    "render_expert_summary_markdown",
    "expert_result_summary_entries",
]
