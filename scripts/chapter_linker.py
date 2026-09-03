"""
跨章缝合、POV 切换与视界隔离提取器 (chapter_linker.py)

功能职责：
1. 提取跨章接缝切片：提取上一章尾部 300 字与本章开头 300 字，实施首章无上文边界防御；
2. 跨章多线 POV 转场识别：扫描开篇前 300 字或前三段是否包含典型多线/侧面烘托转场短句；
3. 叙事视界隔离区间扫描：识别回忆闪回（Flashback）与心魔幻境（Illusion），提取起止行号与线索；
4. 组装并返回强类型 BoundaryContext 上下文对象。

严格遵循 Python 3.8+ 标准库与零第三方依赖原则。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from scripts.types import BoundaryContext

__all__ = [
    "POV_TRANSITION_KEYWORDS",
    "detect_pov_transition",
    "detect_narrative_isolation_zones",
    "extract_boundary_slices",
]

# 跨章 POV 视点转场规范关键词集合
POV_TRANSITION_KEYWORDS: Tuple[str, ...] = (
    "与此同时",
    "同一时刻",
    "千里之外",
    "万里之外",
    "彼时",
    "此时此刻",
    "镜头一转",
    "视线拉远",
    "数千里外",
    "深宫之中",
    "大殿之内",
)

# POV 转场正则匹配器（支持变体并按长度优先匹配）
_POV_TRANSITION_PATTERN = re.compile(
    r"(与此同时|同一时刻|千里之外|万里之外|数千里外|数百里外|数万里外|此时此刻|镜头一转|视线拉远|深宫之中|大殿之内|彼时)"
)

# 叙事视界隔离：心魔幻境（ILLUSION）进入模式
_ILLUSION_ENTRY_PATTERN = re.compile(
    r"(陷入心魔幻境|心魔幻境|心魔丛生|陷入幻境|坠入幻境|幻境之中|走火入魔.*?幻象|魔障由心生)"
)

# 叙事视界隔离：回忆闪回（FLASHBACK）进入模式
_FLASHBACK_ENTRY_PATTERN = re.compile(
    r"([一二三四五六七八九十两百千万\d]+年前(?:那一战|的那场|的那一天|发生的事)?|恍惚间|忆及往事|记忆如潮水般涌来|当年那一战|当年旧事|思绪飘回|回想起当年|蓦然回想)"
)

# 叙事视界隔离：通用退出/复位模式
_ISOLATION_EXIT_PATTERN = re.compile(
    r"(收回思绪|深吸一口气[，,]回到现实|重回现实|回到现实|幻境碎裂|猛然惊醒|回过神来|心魔消散|幻象消散|幻象破碎|清醒过来)"
)


def _extract_head_scope(text: str, max_chars: int = 300, max_paragraphs: int = 3) -> str:
    """提取章节开篇有效扫描区域（前 max_chars 字符并结合前 max_paragraphs 个非空段落）"""
    if not text:
        return ""

    stripped = text.lstrip()
    char_slice = stripped[:max_chars]

    # 提取前三段
    lines = text.splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    para_slice = "\n".join(non_empty[:max_paragraphs])

    # 取覆盖较广者以确保同时覆盖前 300 字符与前三段
    if len(para_slice) > len(char_slice):
        return para_slice
    return char_slice


def detect_pov_transition(head_text: str) -> Tuple[bool, Optional[str]]:
    """扫描章节头部（前 300 字或前三段）是否包含典型的网文多线视点/侧面烘托转场短句。

    Args:
        head_text: 章节文本或章节头部切片

    Returns:
        (True, 命中线索) 或 (False, None)
    """
    if not head_text or not head_text.strip():
        return False, None

    scan_text = _extract_head_scope(head_text, max_chars=300, max_paragraphs=3)
    match = _POV_TRANSITION_PATTERN.search(scan_text)
    if match:
        return True, match.group(1)

    return False, None


def detect_narrative_isolation_zones(text: str) -> List[Dict[str, Any]]:
    """识别章节中的回忆闪回（Flashback）或心魔幻境（Illusion）区块。

    结构化记录每个隔离区：
    {"start_line": int, "end_line": int, "type": "FLASHBACK" | "ILLUSION", "clue": str}
    （1-based 物理行号）；若直到末尾未退出，则 end_line 为文本总行数。

    Args:
        text: 待扫描的正文全文

    Returns:
        隔离区间字典列表
    """
    if not text:
        return []

    lines = text.splitlines()
    isolation_zones: List[Dict[str, Any]] = []
    current_zone: Optional[Dict[str, Any]] = None

    for line_idx, line in enumerate(lines):
        line_no = line_idx + 1

        if current_zone is None:
            # 优先检查心魔幻境（避免幻境中夹带回忆时间标记导致误判为普通闪回）
            m_ill = _ILLUSION_ENTRY_PATTERN.search(line)
            if m_ill:
                clue = m_ill.group(1)
                entry_end = m_ill.end()
                current_zone = {
                    "start_line": line_no,
                    "end_line": line_no,
                    "type": "ILLUSION",
                    "clue": clue,
                }
                # 检查同一行在进入后是否紧随退出标记
                m_exit_same = _ISOLATION_EXIT_PATTERN.search(line[entry_end:])
                if m_exit_same:
                    current_zone["end_line"] = line_no
                    isolation_zones.append(current_zone)
                    current_zone = None
                continue

            # 检查回忆闪回
            m_fb = _FLASHBACK_ENTRY_PATTERN.search(line)
            if m_fb:
                clue = m_fb.group(1)
                entry_end = m_fb.end()
                current_zone = {
                    "start_line": line_no,
                    "end_line": line_no,
                    "type": "FLASHBACK",
                    "clue": clue,
                }
                # 检查同一行在进入后是否紧随退出标记
                m_exit_same = _ISOLATION_EXIT_PATTERN.search(line[entry_end:])
                if m_exit_same:
                    current_zone["end_line"] = line_no
                    isolation_zones.append(current_zone)
                    current_zone = None
                continue
        else:
            # 当前处于隔离区间中，检测退出/复位标记
            m_exit = _ISOLATION_EXIT_PATTERN.search(line)
            if m_exit:
                current_zone["end_line"] = line_no
                isolation_zones.append(current_zone)
                current_zone = None

    # 若直到末尾未显式退出，闭合至文本总行数
    if current_zone is not None:
        current_zone["end_line"] = len(lines)
        isolation_zones.append(current_zone)

    return isolation_zones


def extract_boundary_slices(prev_text: Optional[str], curr_text: str) -> BoundaryContext:
    """提取跨章接缝切片并实施首章无上文边界防御。

    Args:
        prev_text: 上一章文本内容（若为首章或楔子则为 None 或空串）
        curr_text: 本章文本内容

    Returns:
        组装好的 BoundaryContext 数据对象
    """
    # 首章无上文边界防御：当 prev_text 为 None 或空白串时，安全返回
    if prev_text is None or not prev_text.strip():
        has_prev_chapter = False
        prev_tail_300 = ""
    else:
        has_prev_chapter = True
        prev_stripped = prev_text.rstrip()
        prev_tail_300 = prev_stripped[-300:] if len(prev_stripped) > 300 else prev_stripped

    # 提取本章头部 300 字符（剥离首部空白）
    curr_stripped = curr_text.lstrip()
    curr_head_300 = curr_stripped[:300]

    # 检测开篇多线 POV 转场
    is_pov, clue = detect_pov_transition(curr_stripped)

    # 扫描本章叙事视界隔离区间
    isolation_zones = detect_narrative_isolation_zones(curr_text)

    return BoundaryContext(
        prev_tail_300=prev_tail_300,
        curr_head_300=curr_head_300,
        has_prev_chapter=has_prev_chapter,
        is_pov_transition=is_pov,
        transition_clue=clue,
        isolation_zones=isolation_zones,
    )
