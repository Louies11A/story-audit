"""
story-audit: 核心数据结构与契约定义

本模块定义网文审查技能跨模块交互的标准数据模型，包括章节元信息、
排版格式缺陷、章节跨度边界上下文以及短句修改补丁规范。
严格依赖 Python 3.8+ 标准库 dataclasses, pathlib, typing。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

__all__ = [
    "ChapterItem",
    "FormatFinding",
    "BoundaryContext",
    "PatchSpec",
]


@dataclass
class ChapterItem:
    """章节元信息与文件物理定位"""
    index: float               # 序号：0 (序章), 1, 31, 31.1 (第31章上)
    title: str                 # 纯标题：如 "破局之策"
    raw_name: str              # 原始文件名：如 "第031章_破局之策.md"
    path: Path                 # 物理绝对路径


@dataclass
class FormatFinding:
    """网文短句排版缺陷发现项"""
    line_number: int           # 原始文本中的物理行号（1-based）
    flaw_type: str             # "LONG_PARAGRAPH" | "DRAGGING_SENTENCE" | "DIALOGUE_MIXED" | "AI_CONJUNCTION"
    severity: str              # "P2" | "P3"
    snippet: str               # 发生缺陷的原文切片（<= 60字）
    message: str               # 缺陷描述
    suggestion: str            # 短句化修改建议


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
