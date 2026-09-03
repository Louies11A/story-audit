"""
chapter_resolver.py: 智能章节匹配与自然排序解析器

本模块负责网文工程的目录文件探测与章节解析：
1. 纯标准库中文大写数字与权位解析算法（零依赖）；
2. 变体章节名与特殊序位（序章/楔子/引子/拆分章）识别；
3. 自然数值升序排序（Natural Sort，杜绝字典序错误）；
4. 章节跳号与重号体检诊断。
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Set

from scripts.types import ChapterItem

__all__ = [
    "chinese_to_number",
    "parse_chapter_index",
    "parse_chapter_title",
    "ChapterResolver",
]

# 中文字符映射
_CHINESE_DIGITS: Dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_CHINESE_UNITS: Dict[str, int] = {
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
}

_ALLOWED_CHARS: Set[str] = set(_CHINESE_DIGITS.keys()) | set(_CHINESE_UNITS.keys())

# 特殊序位映射
_SPECIAL_PROLOGUES = ("序章", "楔子", "引子")

# 拆分章后缀映射
_SPLIT_SUFFIXES: Dict[str, float] = {
    "上": 0.1,
    "中": 0.2,
    "下": 0.3,
}


def _parse_sub_wan(section_str: str) -> Optional[int]:
    """解析万以内（千、百、十、个）的中文数字字符串"""
    if not section_str:
        return 0
    if section_str.isdigit():
        return int(section_str)

    # 检查是否全部是允许的字符
    if not all(c in _ALLOWED_CHARS for c in section_str):
        return None

    # 若无任何权位词，按无权位逐位念法解析（如 "〇一" -> 1, "一〇五" -> 105）
    if not any(c in _CHINESE_UNITS for c in section_str):
        return int("".join(str(_CHINESE_DIGITS[c]) for c in section_str))

    section_val = 0
    curr_digit = 0
    has_curr_digit = False

    for c in section_str:
        if c in ("零", "〇"):
            # 占位零不参与乘积计算，且不重置或置位已暂存数字
            continue
        if c in _CHINESE_DIGITS:
            curr_digit = _CHINESE_DIGITS[c]
            has_curr_digit = True
        elif c in _CHINESE_UNITS:
            unit_val = _CHINESE_UNITS[c]
            if not has_curr_digit:
                # 口语省略一十（如 "十", "十二" 等以十开头的数）
                curr_digit = 1
            section_val += curr_digit * unit_val
            curr_digit = 0
            has_curr_digit = False

    if has_curr_digit:
        section_val += curr_digit

    return section_val


def chinese_to_number(text: str) -> Optional[int]:
    """
    基于纯标准库与数位权重算法将中文数字或纯阿拉伯数字字符串转换为整型数值。
    
    支持范围：
    - 纯阿拉伯数字（"31" -> 31）
    - 零/〇, 一, 二/两, 三~九
    - 权值：十, 百, 千, 万
    - 口语省略："十二" -> 12, "十" -> 10
    - 逐位念法："〇一" -> 1, "一〇五" -> 105
    - 复合大数："两千零八" -> 2008, "一万零二百零三" -> 10203
    - 兼容前缀："第一百零五" -> 105
    - 非法字符或空字符串返回 None。
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    # 兼容前缀 "第"（如 "第一百零五" -> "一百零五"）
    if cleaned.startswith("第"):
        cleaned = cleaned[1:].strip()
        if not cleaned:
            return None

    if cleaned.isdigit():
        return int(cleaned)

    # 包含非法字符则直接返回 None
    if not all(c in _ALLOWED_CHARS for c in cleaned):
        return None

    # 分割 "万"
    if "万" in cleaned:
        parts = cleaned.split("万")
        if len(parts) > 2:
            return None
        high_str, low_str = parts[0], parts[1]
        high = 1 if not high_str else _parse_sub_wan(high_str)
        low = _parse_sub_wan(low_str) if low_str else 0
        if high is None or low is None:
            return None
        return high * 10000 + low

    return _parse_sub_wan(cleaned)


def parse_chapter_index(filename_or_text: str) -> Optional[float]:
    """
    解析章节序号。
    
    映射规则：
    - 特殊序位（序章/楔子/引子） -> 0.0
    - 上中下拆分章（第31章上/中/下） -> 31.1 / 31.2 / 31.3
    - 常规章（第31章、031_破局、第三十一章等） -> 31.0
    - 无法识别时返回 None
    """
    if not filename_or_text or not isinstance(filename_or_text, str):
        return None

    stem = Path(filename_or_text).stem.strip()
    if not stem:
        return None

    # 1. 特殊序位优先判断（序章/楔子/引子）
    for prologue in _SPECIAL_PROLOGUES:
        if stem == prologue or re.match(rf"^{prologue}[_\s\.\-、:：]", stem):
            return 0.0

    # 2. 检查拆分章标记（上/中/下）
    split_offset = 0.0
    split_match = re.search(
        r"(?:[（\(]([上中下])[）\)]|[_\s\-、]+([上中下])(?=[_\s\.\-、:：]|$)|(?<=[0-9章回节卷集])\s*([上中下])(?=[_\s\.\-、:：]|$))",
        stem,
    )
    if split_match:
        split_char = split_match.group(1) or split_match.group(2) or split_match.group(3)
        split_offset = _SPLIT_SUFFIXES.get(split_char, 0.0)

    # 3. 提取主章号
    # 模式 A: 第XXX章/回/节/卷
    m_di = re.search(r"第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*[章节回卷集]", stem)
    if m_di:
        raw_num = m_di.group(1)
        num = chinese_to_number(raw_num)
        if num is not None:
            return round(float(num) + split_offset, 2)

    # 模式 B: 开头为纯阿拉伯数字，如 "031_绝处逢生", "31 破局", "31. 破局"
    m_num_prefix = re.match(r"^(\d+)(?:[_\s\.\-、]|$)", stem)
    if m_num_prefix:
        num = int(m_num_prefix.group(1))
        return round(float(num) + split_offset, 2)

    # 模式 C: 纯数字主干
    if stem.isdigit():
        return round(float(int(stem)) + split_offset, 2)

    return None


def parse_chapter_title(filename_or_text: str) -> str:
    """
    从文件名或文本中提取纯净标题。
    若无独立标题则返回空字符串；若为非章节文档则返回文件名主干。
    """
    if not filename_or_text or not isinstance(filename_or_text, str):
        return ""

    stem = Path(filename_or_text).stem.strip()
    if not stem:
        return ""

    # 1. 特殊序位（序章/楔子/引子）
    for prologue in _SPECIAL_PROLOGUES:
        if stem == prologue:
            return ""
        m_pro = re.match(rf"^{prologue}[_\s\.\-、:：]+(.*)$", stem)
        if m_pro:
            return m_pro.group(1).strip(" _-.:：")

    # 2. 检查是否能够解析出章节序号
    idx = parse_chapter_index(stem)
    if idx is None:
        # 非章节文档，返回文件名主干
        return stem

    # 3. 剥离章号前缀及可能包含的拆分标识
    pattern = (
        r"^(?:"
        r"第\s*[0-9零〇一二两三四五六七八九十百千万]+\s*[章节回卷集]"
        r"|\d+"
        r")"
        r"(?:\s*[（\(][上中下][）\)]|\s*[-_、\s][上中下])?"
        r"[_\s\.\-、:：]*"
        r"(.*)$"
    )
    m = re.match(pattern, stem)
    if m:
        remains = m.group(1).strip(" _-.:：")
        # 清理尾部残留的拆分标记如 "_上"
        remains = re.sub(r"(?:[_\s\-、]+|(?<=\w))[（\(]?[上中下][）\)]?$", "", remains).strip(" _-.:：")
        return remains

    return stem


class ChapterResolver:
    """网文章节定位器与自然排序引擎"""

    @staticmethod
    def discover_chapters(root_dir: Path) -> List[ChapterItem]:
        """
        扫描目录下的 .md 和 .txt 文件：
        - 若存在 正文/ 子目录则优先扫描该子目录，否则扫描根目录；
        - 跳过隐藏文件（以 . 开头）和临时备份文件（.bak, .tmp, ~ 开头）；
        - 自然数值升序排序（Natural Sort）；
        - 返回 List[ChapterItem]。
        """
        root = Path(root_dir).resolve()
        content_dir = root / "正文"
        scan_dir = content_dir if content_dir.is_dir() else root

        chapters: List[ChapterItem] = []
        for file_path in scan_dir.iterdir():
            if not file_path.is_file():
                continue

            file_name = file_path.name
            # 过滤隐藏文件与临时文件
            if file_name.startswith((".", "~")):
                continue
            ext = file_path.suffix.lower()
            if ext in (".bak", ".tmp"):
                continue
            if ext not in (".md", ".txt"):
                continue

            index = parse_chapter_index(file_name)
            if index is None:
                continue

            title = parse_chapter_title(file_name)
            chapters.append(
                ChapterItem(
                    index=index,
                    title=title,
                    raw_name=file_name,
                    path=file_path.resolve(),
                )
            )

        # 自然数值升序排序，严禁字符串字典序
        chapters.sort(key=lambda item: (item.index, item.raw_name))
        return chapters

    @staticmethod
    def get_chapter_by_index(root_dir: Path, target_index: float) -> Optional[ChapterItem]:
        """根据浮点数值序号准确定位章节"""
        chapters = ChapterResolver.discover_chapters(root_dir)
        for chapter in chapters:
            if abs(chapter.index - target_index) < 1e-4:
                return chapter
        return None

    @staticmethod
    def diagnose_sequence_gaps(chapters: List[ChapterItem]) -> List[str]:
        """
        检查章序跳号（如 35 跳到 37 缺失第 36 章）与重号异常，返回诊断信息列表。
        所有异常均为 P2 级别。
        """
        diagnostics: List[str] = []
        if not chapters:
            return diagnostics

        # 1. 重号体检
        index_map: Dict[float, List[ChapterItem]] = {}
        for item in chapters:
            key = round(item.index, 2)
            index_map.setdefault(key, []).append(item)

        for key, items in index_map.items():
            if len(items) > 1:
                display_num = int(key) if key.is_integer() else key
                files_str = ", ".join(it.raw_name for it in items)
                diagnostics.append(
                    f"[P2 章节序号异常] 发现重复章节序号: 第 {display_num} 章 (文件: {files_str})"
                )

        # 2. 跳号体检（针对整数主章号）
        main_indices = sorted(set(int(item.index) for item in chapters if item.index >= 1.0))
        if len(main_indices) >= 2:
            for i in range(len(main_indices) - 1):
                curr = main_indices[i]
                nxt = main_indices[i + 1]
                if nxt > curr + 1:
                    missing = list(range(curr + 1, nxt))
                    if len(missing) == 1:
                        diagnostics.append(
                            f"[P2 章节序号异常] 章节序号不连续: 缺失第 {missing[0]} 章 (从第 {curr} 章跳到第 {nxt} 章)"
                        )
                    else:
                        missing_str = ", ".join(str(m) for m in missing)
                        diagnostics.append(
                            f"[P2 章节序号异常] 章节序号不连续: 缺失章节 [{missing_str}] (从第 {curr} 章跳到第 {nxt} 章)"
                        )

        return diagnostics
