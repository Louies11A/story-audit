# -*- coding: utf-8 -*-
"""
story-audit: 核心数据结构与契约定义

本模块定义网文审查技能跨模块交互的标准数据模型，包括章节元信息、
统一缺陷项契约、排版格式缺陷、章节跨度边界上下文以及短句修改补丁规范。
严格依赖 Python 3.8+ 标准库 dataclasses, pathlib, typing。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "ChapterItem",
    "Finding",
    "FormatFinding",
    "BoundaryContext",
    "PatchSpec",
    "VALID_SEVERITIES",
    "VALID_FINDING_CATEGORIES",
    "format_factual_fix",
]

VALID_SEVERITIES = ("P0", "P1", "P2", "P3")
VALID_FINDING_CATEGORIES = (
    "structure",
    "character",
    "prose",
    "consistency",
    "platform",
    "factual",
    "format",
    "causal",
)


def format_factual_fix(category: str, fix: str) -> str:
    """
    铁律约束：事实与因果类缺陷 (factual / causal / consistency) 的 fix
    严格限制为事实统一方向，严禁主观文学发挥。
    """
    if category in ("factual", "causal", "consistency"):
        clean_fix = fix.strip()
        if not any(clean_fix.startswith(p) for p in ("【事实统一】", "【事实对齐】", "【状态校准】")):
            return f"【事实对齐】{clean_fix}"
    return fix


@dataclass
class ChapterItem:
    """章节元信息与文件物理定位"""
    index: float               # 序号：0 (序章), 1, 31, 31.1 (第31章上)
    title: str                 # 纯标题：如 "破局之策"
    raw_name: str              # 原始文件名：如 "第031章_破局之策.md"
    path: Path                 # 物理绝对路径


@dataclass
class Finding:
    """
    第四阶段标准统一缺陷项契约 (Unified Findings Schema)
    
    字段约束：
    - severity: P0 | P1 | P2 | P3
    - category: structure | character | prose | consistency | platform | factual | format | causal
    - location: 发生物理位置，如 "第001章 行42"
    - evidence: 原文关键切片证据
    - issue: 缺陷陈述
    - fix: 修复建议（事实与因果类严格限制为事实统一方向，严禁文学发挥）
    """
    severity: str
    category: str
    location: str
    evidence: str
    issue: str
    fix: str
    line_number: Optional[int] = None
    flaw_type: Optional[str] = None

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"无效的严重度: '{self.severity}'，可选: {VALID_SEVERITIES}")
        if self.category not in VALID_FINDING_CATEGORIES:
            raise ValueError(f"无效的分类: '{self.category}'，可选: {VALID_FINDING_CATEGORIES}")
        # 铁律：事实与因果类缺陷规范化
        if self.category in ("factual", "causal", "consistency"):
            self.fix = format_factual_fix(self.category, self.fix)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FormatFinding:
    """网文短句排版缺陷发现项（兼容第一性原理与 Unified Findings Schema）"""
    line_number: int           # 原始文本中的物理行号（1-based）
    flaw_type: str             # "LONG_PARAGRAPH" | "DRAGGING_SENTENCE" | "DIALOGUE_MIXED" | "AI_CONJUNCTION" 等
    severity: str              # "P0" | "P1" | "P2" | "P3"
    snippet: str               # 发生缺陷的原文切片（<= 60字）
    message: str               # 缺陷描述
    suggestion: str            # 短句化修改建议
    category: str = "format"
    location: str = ""
    evidence: str = ""
    issue: str = ""
    fix: str = ""

    def __post_init__(self) -> None:
        if not self.location:
            self.location = f"行 {self.line_number}"
        if not self.evidence:
            self.evidence = self.snippet
        if not self.issue:
            self.issue = self.message
        if not self.fix:
            self.fix = self.suggestion
        if self.flaw_type and (self.flaw_type.startswith("AI_") or self.flaw_type == "AI_CONJUNCTION"):
            self.category = "prose"

    def to_finding(self, chapter_index: Optional[float] = None) -> Finding:
        loc = f"第 {chapter_index:03g} 章 行 {self.line_number}" if chapter_index is not None else f"行 {self.line_number}"
        return Finding(
            severity=self.severity,
            category=self.category,
            location=loc,
            evidence=self.snippet,
            issue=self.message,
            fix=self.suggestion,
            line_number=self.line_number,
            flaw_type=self.flaw_type,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "location": self.location or f"行 {self.line_number}",
            "evidence": self.evidence or self.snippet,
            "issue": self.issue or self.message,
            "fix": self.fix or self.suggestion,
            "line_number": self.line_number,
            "flaw_type": self.flaw_type,
            "snippet": self.snippet,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class BoundaryContext:
    """章节跨度与边界上下文信息"""
    prev_tail_300: str                                    # 上章末尾 300 字（首章时为空字符串）
    curr_head_300: str                                    # 本章开头 300 字
    has_prev_chapter: bool                                # 是否存在上一章
    is_pov_transition: bool                               # 是否识别出视点转场（"与此同时"等）
    transition_clue: Optional[str] = None                # 触发转场的词句
    isolation_zones: List[Dict[str, Any]] = field(default_factory=list)  # 闪回/回忆隔离区间 [{start_line, end_line, type, clue}]


@dataclass
class PatchSpec:
    """短句化修复锚点补丁规范"""
    target_line: int           # 报告中建议修改的物理行号
    context_before: str        # 前置锚点句（前一句）
    old_text: str              # 待替换的原文字句
    new_text: str              # 采纳的新短句内容
    context_after: str         # 后置锚点句（后一句）
