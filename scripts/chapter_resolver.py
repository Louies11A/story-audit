"""
chapter_resolver.py: 智能章节匹配与自然排序解析器

本模块负责网文工程的目录文件探测与章节解析：
1. 纯标准库中文大写数字（含金融大写）与权位解析算法（零依赖，带状态机校验防呆）；
2. 变体章节名与特殊序位（序章/楔子/引子/拆分章）识别；
3. 全面支持中文破折号、全角连接符与分卷/前缀标签优先级处理；
4. 严格拆分标记提取，禁止误吞以“上/中/下”开头或结尾的正常标题；
5. 自然数值升序排序（Natural Sort，杜绝字典序错误）；
6. 章节跳号（支持跨多章断号检测）与重号体检诊断。
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple

from scripts.types import ChapterItem

__all__ = [
    "chinese_to_number",
    "parse_chapter_index",
    "parse_chapter_title",
    "ChapterResolver",
]

# 中文字符映射（含普通汉字数字与大写金融数字）
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
    # 金融大写数字
    "壹": 1,
    "贰": 2,
    "貮": 2,
    "叁": 3,
    "参": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}

_CHINESE_UNITS: Dict[str, int] = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
    "万": 10000,
    "萬": 10000,
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

# 全面支持的分隔符字符集：空白（含全角空格）、下划线、半角点、半角减号、全角减号、全角破折号、顿号、半角/全角冒号
_DELIM_CHARS = r"\s_.\-－—、:："


def _strip_delimiters(text: str) -> str:
    """去除字符串首尾的分隔符"""
    return re.sub(rf"^[{_DELIM_CHARS}]+|[{_DELIM_CHARS}]+$", "", text)


def _parse_sub_wan(section_str: str) -> Optional[int]:
    """
    解析万以内（千、百、十、个）的中文数字字符串，
    内置严格状态机校验防呆（拦截权位倒置、连续数字、非法零组合等）。
    """
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
    last_unit = float("inf")
    saw_zero = False
    consecutive_digits = 0

    for c in section_str:
        if c in ("零", "〇"):
            if saw_zero:
                # 连续出现零非法（如 "两千零零八"）
                return None
            if consecutive_digits > 0:
                # 数字后未跟权位直接跟零非法（如 "一零百"）
                return None
            saw_zero = True
            consecutive_digits = 0
        elif c in _CHINESE_DIGITS:
            d = _CHINESE_DIGITS[c]
            consecutive_digits += 1
            curr_digit = d
            has_curr_digit = True
            saw_zero = False
        elif c in _CHINESE_UNITS:
            unit_val = _CHINESE_UNITS[c]
            # 防呆 1：权位必须严格递减（防御 "十百", "十千", "百千", "十十", "百百" 等）
            if unit_val >= last_unit:
                return None
            # 防呆 2：零之后紧随权位非法（如 "零百", "零十"）
            if saw_zero:
                return None
            # 防呆 3：权位前不得有连续多个数字（防御 "二三十", "三四百" 等）
            if consecutive_digits > 1:
                return None
            # 防呆 4：权位前若无数字，仅允许开头的“十”/“拾”省略一（如 "十", "十二"）
            if not has_curr_digit:
                if unit_val == 10 and last_unit == float("inf"):
                    curr_digit = 1
                else:
                    return None
            section_val += curr_digit * unit_val
            last_unit = unit_val
            curr_digit = 0
            has_curr_digit = False
            consecutive_digits = 0
            saw_zero = False

    # 防呆 5：不能以零结尾（如 "一百零"）
    if saw_zero:
        return None
    # 防呆 6：结尾个位不得有连续多个数字（如 "一百二三"）
    if consecutive_digits > 1:
        return None

    if has_curr_digit:
        section_val += curr_digit

    return section_val


def chinese_to_number(text: str) -> Optional[int]:
    """
    基于纯标准库与数位权重算法将中文数字或纯阿拉伯数字字符串转换为整型数值。
    
    支持范围：
    - 纯阿拉伯数字（"31" -> 31）
    - 零/〇, 一, 二/两, 三~九 及 金融大写数字（壹~玖）
    - 权值：十/拾, 百/佰, 千/仟, 万/萬
    - 口语省略："十二" -> 12, "十" -> 10, "拾" -> 10
    - 逐位念法："〇一" -> 1, "一〇五" -> 105
    - 复合大数："两千零八" -> 2008, "一万零二百零三" -> 10203, "壹萬贰仟" -> 12000
    - 兼容前缀："第一百零五" -> 105
    - 非法数字组合状态机防御："二三十"、"十百"、"一千百" 等返回 None。
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

    # 统一金融大写 "萬" -> "万"
    cleaned = cleaned.replace("萬", "万")

    # 包含非法字符则直接返回 None
    if not all(c in _ALLOWED_CHARS for c in cleaned):
        return None

    # 分割 "万"
    if "万" in cleaned:
        parts = cleaned.split("万")
        if len(parts) > 2:
            return None
        high_str, low_str = parts[0], parts[1]
        if not high_str:
            return None
        high = _parse_sub_wan(high_str)
        low = _parse_sub_wan(low_str) if low_str else 0
        if high is None or low is None or high <= 0 or low >= 10000:
            return None
        return high * 10000 + low

    return _parse_sub_wan(cleaned)


def _extract_split_marker(text: str) -> Tuple[float, str]:
    """
    检查并提取拆分章标记（上/中/下）。
    
    严格限制剥离条件，杜绝误吞汉字标题（如《下山》、《上善若水》、《决战天下》、《落下》）：
    1. 被括号包裹（如 (上)、（中）、[下]、【上】）；
    2. 紧跟在章/回/节/集/卷之后且后紧随分隔符或结尾（如 "第31章下.txt", "第31章上 破局"）；
    3. 处于分隔符之后且后紧随分隔符或结尾（如 "第31章_上_破局", "第31章 决战天下_下"）。
    返回 (split_offset, clean_text)。
    """
    # 1. 括号包裹
    m1 = re.search(r"[（\(\[【]([上中下])[）\)\]】]", text)
    if m1:
        c = m1.group(1)
        clean = text[: m1.start()] + " " + text[m1.end() :]
        return _SPLIT_SUFFIXES[c], clean

    # 2. 紧跟在章/回/节/集/卷之后且紧随分隔符或结尾
    m2 = re.search(rf"(?<=[章节回集卷])([上中下])(?=[{_DELIM_CHARS}]|$)", text)
    if m2:
        c = m2.group(1)
        clean = text[: m2.start()] + text[m2.end() :]
        return _SPLIT_SUFFIXES[c], clean

    # 3. 处于分隔符之后且后紧随分隔符或结尾
    m3 = re.search(rf"(?<=[{_DELIM_CHARS}])([上中下])(?=[{_DELIM_CHARS}]|$)", text)
    if m3:
        c = m3.group(1)
        clean = text[: m3.start()] + text[m3.end() :]
        return _SPLIT_SUFFIXES[c], clean

    return 0.0, text


# 前缀标签正则（支持各种括号标签以及分卷标签，如 【加更】、[加更]、正文卷、VIP卷、第一卷、卷一 等）
_PREFIX_LABEL_PATTERN = re.compile(
    rf"^(?:"
    rf"【[^】]+】"
    rf"|\[[^\]]+\]"
    rf"|(?:第?\s*([0-9零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟萬]+)\s*卷)"
    rf"|(?:卷\s*([0-9零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟萬]+))"
    rf"|\S+?卷"
    rf")[{_DELIM_CHARS}]*"
)


def _strip_prefix_labels(text: str) -> Tuple[str, Optional[str]]:
    """循环剥离所有前置标签与分卷前缀，并记录可能存在的有效卷号"""
    curr = text
    found_vol: Optional[str] = None
    while True:
        m = _PREFIX_LABEL_PATTERN.match(curr)
        if not m:
            break
        v = m.group(1) or m.group(2)
        if v:
            found_vol = v
        curr = curr[m.end() :]
    return curr, found_vol


def _parse_chapter_info(filename_or_text: str) -> Tuple[Optional[float], str]:
    """
    解析章节序号与纯净标题的统一底层实现。
    
    优先级：
    1. 特殊序位（序章/楔子/引子） -> 0.0；
    2. 具体章号（第XXX章/回/节/集）优先于分卷与前置标签；
    3. 前缀标签剥离后的阿拉伯数字章号；
    4. 仅有分卷（如第一卷 破局）；
    5. 纯数字文件名主干；
    6. 非章节文档返回 (None, 文件名主干)。
    """
    if not filename_or_text or not isinstance(filename_or_text, str):
        return None, ""

    stem = Path(filename_or_text).stem.strip()
    if not stem:
        return None, ""

    # 1. 检查特殊序位（序章/楔子/引子）
    stripped_pro, _ = _strip_prefix_labels(stem)
    for prologue in _SPECIAL_PROLOGUES:
        if stripped_pro == prologue:
            return 0.0, ""
        m_pro = re.match(rf"^{prologue}(?:[{_DELIM_CHARS}]+(.*))?$", stripped_pro)
        if m_pro:
            rem = m_pro.group(1) or ""
            return 0.0, _strip_delimiters(rem)

    # 2. 提取拆分章标记
    split_offset, clean_text = _extract_split_marker(stem)

    # 3. 优先匹配具体章号（[章节回集]）——如《第一卷 第31章 破局.md》主序号优先为 31.0
    m_chap = re.search(
        r"第\s*([0-9零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟萬]+)\s*([章节回集])",
        clean_text,
    )
    if m_chap:
        raw_num = m_chap.group(1)
        num = chinese_to_number(raw_num)
        if num is not None:
            idx = round(float(num) + split_offset, 2)
            remains = clean_text[m_chap.end() :]
            title = _strip_delimiters(remains)
            return idx, title

    # 4. 次优先：前缀标签剥离后以阿拉伯数字开头（如 "031_绝处逢生", "01——决战", "【加更】031 破局"）
    stripped_text, found_vol = _strip_prefix_labels(clean_text)
    m_num = re.match(rf"^(\d+)(?:[{_DELIM_CHARS}]|$)(.*)$", stripped_text)
    if m_num:
        num = int(m_num.group(1))
        idx = round(float(num) + split_offset, 2)
        remains = m_num.group(2)
        title = _strip_delimiters(remains)
        return idx, title

    # 5. 再次优先：仅有卷号的情况（如 "第一卷 破局.md", "第一卷.md"）
    if found_vol:
        num = chinese_to_number(found_vol)
        if num is not None:
            idx = round(float(num) + split_offset, 2)
            title = _strip_delimiters(stripped_text)
            return idx, title

    # 6. 纯数字主干
    if clean_text.isdigit():
        return round(float(int(clean_text)) + split_offset, 2), ""

    # 非章节文档，返回 None 与原始文件名主干
    return None, stem


def parse_chapter_index(filename_or_text: str) -> Optional[float]:
    """
    解析章节序号。
    
    映射规则：
    - 特殊序位（序章/楔子/引子） -> 0.0
    - 上中下拆分章（第31章上/中/下） -> 31.1 / 31.2 / 31.3
    - 卷号与具体章号共存时，优先匹配具体章号（《第一卷 第31章 破局》 -> 31.0）
    - 常规章（第31章、031_破局、第三十一章等） -> 31.0
    - 无法识别时返回 None
    """
    idx, _ = _parse_chapter_info(filename_or_text)
    return idx


def parse_chapter_title(filename_or_text: str) -> str:
    """
    从文件名或文本中提取纯净标题。
    - 修复误吞缺陷，严格保护以“上/中/下”开头或结尾的正常汉字标题；
    - 支持前置标签与分卷标签剥离；
    - 若无独立标题则返回空字符串；若为非章节文档则返回文件名主干。
    """
    _, title = _parse_chapter_info(filename_or_text)
    return title


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
        skip_dirs = {".git", ".bak", "reports", "archive", ".cache", "设定", "追踪", "__pycache__"}
        for file_path in scan_dir.rglob("*"):
            if not file_path.is_file():
                continue
            rel_parent_parts = file_path.relative_to(scan_dir).parent.parts
            if any(part.startswith(".") or part.lower() in skip_dirs for part in rel_parent_parts):
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
    def diagnose_sequence_gaps(chapters: List[ChapterItem], allow_partial: bool = False) -> List[str]:
        """
        检查章序跳号（如 35 跳到 37 缺失第 36 章，或跨多章跳号如缺失 31, 32）与重号异常，返回诊断信息列表。
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
        if main_indices:
            # 检查首章是否缺失（正文章号应从第 1 章起始）
            if not allow_partial and main_indices[0] > 1:
                missing_first = list(range(1, main_indices[0]))
                if len(missing_first) == 1:
                    diagnostics.append(
                        f"[P2 章节序号异常] 章节序号不连续: 缺失第 {missing_first[0]} 章 (正文首章从第 {main_indices[0]} 章开始)"
                    )
                else:
                    missing_str = ", ".join(str(m) for m in missing_first)
                    diagnostics.append(
                        f"[P2 章节序号异常] 章节序号不连续: 缺失章节 [{missing_str}] (正文首章从第 {main_indices[0]} 章开始)"
                    )

            # 检查后续章节不连续
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