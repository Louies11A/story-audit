# -*- coding: utf-8 -*-
"""
genre_detector.py: 全题材自动探测与画像引擎

功能职责：
1. 覆盖 32 个长篇题材与 10 个短篇题材（共 42 题材全景矩阵）的特征指纹；
2. 多维度特征打分算法（关键词频、核心动词、系统意象、专有道具、角色关系称谓）；
3. 动态识别章节自然文本题材，计算主题材、置信度、二级题材标签与大类归属；
4. 输出符合契约的 GenreProfile 实体，挂载第一性原理卡尺与特异性毒点预警；
5. 提供公共接口 detect_genre(text_or_chapters, specified_genre=None) -> GenreProfile。
"""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

__all__ = [
    "GenreProfile",
    "detect_genre",
    "get_all_genres",
    "get_genre_metadata",
    "resolve_canonical_genre",
]


@dataclass
class GenreProfile:
    """题材画像与第一性原理卡尺模型"""
    primary_genre: str                                    # 主题材（如 "东方仙侠"、"追妻火葬场"）
    confidence: float                                      # 置信度 (0.0 ~ 1.0)
    secondary_genres: List[str] = field(default_factory=list)  # 二级题材标签（Top 2~3）
    category_group: str = "长篇通用"                       # 一级大类（如 "仙侠玄幻"、"都市异能"、"短篇爆发"等）
    first_principles: str = ""                             # 读者第一性原理追读期待
    red_lines: List[str] = field(default_factory=list)     # 绝不可触碰的毒点红线清单
    keywords_matched: List[str] = field(default_factory=list)  # 匹配到的核心特征词
    scores: Dict[str, float] = field(default_factory=dict) # 候选各题材综合打分

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_genre": self.primary_genre,
            "confidence": round(self.confidence, 4),
            "secondary_genres": self.secondary_genres,
            "category_group": self.category_group,
            "first_principles": self.first_principles,
            "red_lines": self.red_lines,
            "keywords_matched": self.keywords_matched,
            "scores": {k: round(v, 2) for k, v in sorted(self.scores.items(), key=lambda x: x[1], reverse=True)[:5]},
        }


# 加载题材特征数据源
_DATA_PATH = Path(__file__).resolve().parent / "genre_data.json"
if _DATA_PATH.is_file():
    with open(_DATA_PATH, "r", encoding="utf-8") as _f:
        GENRE_REGISTRY: Dict[str, Dict[str, Any]] = json.load(_f)
else:
    GENRE_REGISTRY = {}

# 构建别名全局映射表
ALIAS_TO_GENRE: Dict[str, str] = {}
for g_name, data in GENRE_REGISTRY.items():
    ALIAS_TO_GENRE[g_name] = g_name
    for al in data.get("aliases", []):
        ALIAS_TO_GENRE[al] = g_name
        ALIAS_TO_GENRE[al.replace(" ", "")] = g_name


def get_all_genres() -> List[str]:
    """返回支持的全部规范题材名称列表（42 题材）"""
    return list(GENRE_REGISTRY.keys())


def get_genre_metadata(genre_name: str) -> Dict[str, Any]:
    """获取指定题材的元数据字典，若不存在则回退至传统玄幻"""
    canonical = resolve_canonical_genre(genre_name) or "传统玄幻"
    return GENRE_REGISTRY.get(canonical, GENRE_REGISTRY.get("传统玄幻", {}))


def resolve_canonical_genre(name_or_alias: Optional[str]) -> Optional[str]:
    """将输入的任意别名、同义词或模糊名称解析为规范题材名称"""
    if not name_or_alias:
        return None
    clean = name_or_alias.strip()
    if clean in ALIAS_TO_GENRE:
        return ALIAS_TO_GENRE[clean]
    clean_no_space = clean.replace(" ", "")
    if clean_no_space in ALIAS_TO_GENRE:
        return ALIAS_TO_GENRE[clean_no_space]
    # 模糊包含匹配（优先最长匹配项，避免短词误触）
    for al, canonical in sorted(ALIAS_TO_GENRE.items(), key=lambda x: len(x[0]), reverse=True):
        if al in clean or (len(clean) >= 2 and clean in al):
            return canonical
    return None


def _extract_text(text_or_chapters: Any) -> str:
    """安全展开输入为纯文本字符串"""
    if not text_or_chapters:
        return ""
    if isinstance(text_or_chapters, str):
        return text_or_chapters
    if isinstance(text_or_chapters, Path):
        try:
            return text_or_chapters.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    if hasattr(text_or_chapters, "path"):
        try:
            p = getattr(text_or_chapters, "path")
            return Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(text_or_chapters, (list, tuple)):
        collected = []
        for item in text_or_chapters:
            t = _extract_text(item)
            if t:
                collected.append(t)
        return "\n".join(collected)
    return str(text_or_chapters)


def detect_genre(
    text_or_chapters: Any,
    specified_genre: Optional[str] = None,
) -> GenreProfile:
    """
    题材自动探测与画像主入口函数。

    参数：
        text_or_chapters: 单章文本 (str)、章节列表 (List[ChapterItem]) 或文件路径
        specified_genre: 可选的用户显式指定题材（如 "东方仙侠"、"追妻火葬场" 等）。
                         若为 "auto" 或 None 则全自动探测；若指定则强制采纳指定题材并装配卡尺。

    返回：
        GenreProfile: 包含主题材、置信度、二级标签、第一性原理和毒点红线的画像对象。
    """
    # 1. 检查显式指定题材
    if specified_genre and specified_genre.lower() not in ("auto", "none", ""):
        canonical = resolve_canonical_genre(specified_genre)
        if canonical and canonical in GENRE_REGISTRY:
            meta = GENRE_REGISTRY[canonical]
            return GenreProfile(
                primary_genre=canonical,
                confidence=1.0,
                secondary_genres=[],
                category_group=meta.get("group", "长篇通用"),
                first_principles=meta.get("drive", ""),
                red_lines=meta.get("red_lines", []),
                keywords_matched=["(用户手动指定)"],
                scores={canonical: 999.0},
            )

    text = _extract_text(text_or_chapters)
    if not text or len(text.strip()) == 0:
        default_meta = GENRE_REGISTRY.get("传统玄幻", {})
        return GenreProfile(
            primary_genre="传统玄幻",
            confidence=0.0,
            secondary_genres=[],
            category_group=default_meta.get("group", "仙侠玄幻"),
            first_principles=default_meta.get("drive", ""),
            red_lines=default_meta.get("red_lines", []),
            keywords_matched=[],
            scores={},
        )

    # 2. 多维度打分引擎
    scores: Dict[str, float] = {g: 0.0 for g in GENRE_REGISTRY}
    matched_kws_map: Dict[str, List[str]] = {g: [] for g in GENRE_REGISTRY}

    norm_text = text.lower()

    for genre, data in GENRE_REGISTRY.items():
        # A. 独特专有特征词（High Weight: 权重 5.0，具有决定性排他力）
        for kw in data.get("high_weight", []):
            kw_low = kw.lower()
            cnt = norm_text.count(kw_low)
            if cnt > 0:
                delta = 5.0 * (1.0 + math.log(cnt))
                scores[genre] += delta
                matched_kws_map[genre].append(kw)

        # B. 标准领域词汇（Standard: 权重 2.0）
        for kw in data.get("standard", []):
            kw_low = kw.lower()
            cnt = norm_text.count(kw_low)
            if cnt > 0:
                delta = 2.0 * (1.0 + math.log(cnt))
                scores[genre] += delta
                matched_kws_map[genre].append(kw)

        # C. 核心动作动词（Verbs: 权重 2.5）
        for kw in data.get("verbs", []):
            kw_low = kw.lower()
            cnt = norm_text.count(kw_low)
            if cnt > 0:
                delta = 2.5 * (1.0 + math.log(cnt))
                scores[genre] += delta
                matched_kws_map[genre].append(kw)

        # D. 角色称谓与专属关系（Roles: 权重 2.5）
        for kw in data.get("roles", []):
            kw_low = kw.lower()
            cnt = norm_text.count(kw_low)
            if cnt > 0:
                delta = 2.5 * (1.0 + math.log(cnt))
                scores[genre] += delta
                matched_kws_map[genre].append(kw)

    # 3. 排序与置信度归一化
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_genre, top_score = sorted_items[0]

    if top_score <= 0.001:
        default_meta = GENRE_REGISTRY.get("传统玄幻", {})
        return GenreProfile(
            primary_genre="传统玄幻",
            confidence=0.0,
            secondary_genres=[],
            category_group=default_meta.get("group", "仙侠玄幻"),
            first_principles=default_meta.get("drive", ""),
            red_lines=default_meta.get("red_lines", []),
            keywords_matched=[],
            scores=scores,
        )

    runner_up_score = sorted_items[1][1] if len(sorted_items) > 1 else 0.0

    margin = (top_score - runner_up_score) / max(top_score, 1.0)
    score_factor = min(top_score / 35.0, 1.0)
    raw_confidence = 0.5 * margin + 0.5 * score_factor
    confidence = max(0.1, min(0.99, raw_confidence))

    secondary_genres = [
        g for g, s in sorted_items[1:4]
        if s > 0.0 and s >= 0.25 * top_score
    ]

    top_meta = GENRE_REGISTRY[top_genre]
    return GenreProfile(
        primary_genre=top_genre,
        confidence=confidence,
        secondary_genres=secondary_genres,
        category_group=top_meta.get("group", "长篇通用"),
        first_principles=top_meta.get("drive", ""),
        red_lines=top_meta.get("red_lines", []),
        keywords_matched=matched_kws_map[top_genre][:10],
        scores=scores,
    )
