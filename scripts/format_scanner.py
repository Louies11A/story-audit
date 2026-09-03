"""
排版白名单掩码与格式扫描器 (format_scanner.py)

功能职责：
1. 白名单掩码（行数绝对保持契约）：
   - 系统流面板、古诗口诀与偈语、Markdown 引用公文书信等区块等行置空；
   - 铁律：masked_text.count("\n") == text.count("\n")，杜绝物理行号漂移。
2. 格式缺陷扫描器：
   - LONG_PARAGRAPH (P2)：单段连续字数 >= 120 字；
     * 独白情绪降级：段内含 >= 3 个感叹号（！或 !）时，降级为 P3 提示。
   - DRAGGING_SENTENCE (P2)：单个完整句子内逗号 >= 4 个，或单分句无标点连续字数 >= 45 字。
   - DIALOGUE_MIXED (P3)：对话台词引号后紧塞 >= 80 字外貌/动作/心理描写不分行。
   - AI_CONJUNCTION (P3)：典型 AI 翻译腔高频连词（然而、与此同时、不可否认的是等）。
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from scripts.types import FormatFinding

# 系统流面板核心关键词
SYSTEM_PANEL_KEYWORDS = {
    "力量", "敏捷", "境界", "寿元", "技能点", "宿主", "体质", "生命", "法力",
    "智力", "精神", "根骨", "气血", "功法", "经验", "天赋", "战力", "防御",
    "攻击", "状态", "属性", "等级", "法宝", "耐力", "潜能", "悟性", "幸运",
    "修为", "真元", "神识", "灵力", "元力", "魂力", "战技", "灵根", "系统",
    "面板", "抽奖", "积分", "金币", "声望", "掉落"
}

# AI 翻译腔高频连词库
AI_CONJUNCTION_WORDS = (
    "然而",
    "与此同时",
    "不可否认的是",
    "值得注意的是",
    "仿佛在昭示着什么",
    "不由得",
    "毫无疑问",
    "显而易见的是",
    "显而易见",
    "不难看出",
    "正如前文所述",
    "毋庸置疑",
    "总而言之",
    "换句话说",
)

# 标点符号与切分模式（包含所有常见中英文标点及空白）
PUNCTUATION_SPLIT_PATTERN = re.compile(r'[。！？!?,，、；;：:—…“”"\'’（）()《》【】\s]+')


def _make_snippet(text: str, max_len: int = 60) -> str:
    """截取不超过 max_len 的文本片段"""
    clean = text.strip()
    if len(clean) <= max_len:
        return clean
    return clean[:max_len]


def _analyze_couplet_line(line: str) -> Optional[int]:
    """
    分析单行是否为经典的双半句对仗（例如：天地玄黄，宇宙洪荒。 或 白日依山尽，黄河入海流。）
    若符合，返回每半句汉字数 K (K in 3..8)；若不符合，返回 None。
    """
    s = line.strip()
    if not s:
        return None
    # 排除包含对话引号或特殊标记
    if any(c in s for c in ('"', '“', '”', '>', '#', '|', '-', '*', '`', '：', ':')):
        return None

    sub_parts = re.split(r'[，；,;]', s)
    sub_parts = [p.strip() for p in sub_parts if p.strip()]
    if len(sub_parts) == 2:
        h1 = len(re.findall(r'[\u4e00-\u9fa5]', sub_parts[0]))
        h2 = len(re.findall(r'[\u4e00-\u9fa5]', sub_parts[1]))
        if h1 == h2 and h1 in (3, 4, 5, 6, 7, 8):
            return h1
    return None


def _analyze_single_poem_line(line: str) -> Optional[int]:
    """
    分析单行是否为单半句成行的诗句（例如 4/5/6/7/8 言单行断句）。
    若符合，返回汉字数 K；若不符合，返回 None。
    """
    s = line.strip()
    if not s:
        return None
    if any(c in s for c in ('"', '“', '”', '>', '#', '|', '-', '*', '`', '：', ':')):
        return None

    if s[-1] in "，。！？；":
        # 句内不得再有逗号句号等停顿标点
        if any(p in s[:-1] for p in "，。！？；,;!?"):
            return None
        han_chars = re.findall(r'[\u4e00-\u9fa5]', s)
        total_han = len(han_chars)
        if total_han in (4, 5, 6, 7, 8):
            return total_han
    return None


def mask_special_blocks(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    白名单区块掩码处理器。
    
    规则：
    1. 保持行数绝对一致（masked_text.count('\\n') == text.count('\\n')），绝不增删物理行；
    2. 识别系统流面板（以【、[、| 开头且包含核心关键词）；
    3. 识别古诗口诀与偈语（四六言排比、诗词对仗块）；
    4. 识别引用公文与书信（> 开头的 Markdown 引用块）；
    5. 将匹配到的区块在 masked_text 中置为空行（保留原行尾换行符），
       并收集对应元数据 masks_info。
    """
    if not text:
        return "", []

    lines = text.split("\n")
    n_lines = len(lines)
    masked_line_indices: Set[int] = set()
    masks_info: List[Dict[str, Any]] = []

    # 1. 扫描 Markdown 引用块（> 开头）
    i = 0
    while i < n_lines:
        line_s = lines[i].lstrip()
        if line_s.startswith(">"):
            start_idx = i
            while i < n_lines and lines[i].lstrip().startswith(">"):
                i += 1
            end_idx = i - 1
            for k in range(start_idx, end_idx + 1):
                masked_line_indices.add(k)
            masks_info.append({
                "type": "quote",
                "start_line": start_idx + 1,
                "end_line": end_idx + 1,
                "raw_content": "\n".join(lines[start_idx:end_idx + 1]),
            })
        else:
            i += 1

    # 2. 扫描系统流面板
    # 特征：以【、[、| 开头且含有关键词的连续块
    i = 0
    while i < n_lines:
        if i in masked_line_indices:
            i += 1
            continue

        line_s = lines[i].lstrip()
        if line_s.startswith(("【", "[", "|")):
            start_idx = i
            # 探索该连续块的范围
            if line_s.startswith("|"):
                # 表格块：连续以 | 开头
                while i < n_lines and lines[i].lstrip().startswith("|"):
                    i += 1
            elif line_s.startswith("【") and not line_s.endswith("】") and "】" not in line_s:
                # 跨多行未闭合的【 ... 】
                while i < n_lines and "】" not in lines[i]:
                    i += 1
                if i < n_lines:
                    i += 1
            elif line_s.startswith("[") and not line_s.endswith("]") and "]" not in line_s:
                # 跨多行未闭合的 [ ... ]
                while i < n_lines and "]" not in lines[i]:
                    i += 1
                if i < n_lines:
                    i += 1
            else:
                # 单行或连续类似格式行
                while i < n_lines:
                    curr_s = lines[i].lstrip()
                    if not curr_s:
                        break
                    if curr_s.startswith(("【", "[", "|", "-", "*")):
                        i += 1
                    elif "：" in curr_s or ":" in curr_s:
                        i += 1
                    else:
                        break

            end_idx = i - 1
            block_content = "\n".join(lines[start_idx:end_idx + 1])
            # 检验是否包含系统关键词
            if any(kw in block_content for kw in SYSTEM_PANEL_KEYWORDS):
                for k in range(start_idx, end_idx + 1):
                    masked_line_indices.add(k)
                masks_info.append({
                    "type": "system_panel",
                    "start_line": start_idx + 1,
                    "end_line": end_idx + 1,
                    "raw_content": block_content,
                })
        else:
            i += 1

    # 3. 扫描古诗口诀与偈语（连续 >= 2 行同构对仗句或同字数单行诗句）
    i = 0
    while i < n_lines:
        if i in masked_line_indices or not lines[i].strip():
            i += 1
            continue

        # 检查是否为双句对仗（例如 4+4, 5+5, 7+7 等）
        c_len = _analyze_couplet_line(lines[i])
        if c_len is not None:
            start_idx = i
            while i < n_lines and i not in masked_line_indices:
                curr_c_len = _analyze_couplet_line(lines[i])
                if curr_c_len == c_len:
                    i += 1
                else:
                    break
            end_idx = i - 1
            if end_idx - start_idx + 1 >= 2:
                for k in range(start_idx, end_idx + 1):
                    masked_line_indices.add(k)
                masks_info.append({
                    "type": "poem",
                    "start_line": start_idx + 1,
                    "end_line": end_idx + 1,
                    "raw_content": "\n".join(lines[start_idx:end_idx + 1]),
                })
                continue
            else:
                # 只有单行对仗，不构成多行对仗块，重置游标继续其他检查
                i = start_idx

        # 检查是否为单句断行诗（如 5言绝句连续4行等）
        s_len = _analyze_single_poem_line(lines[i])
        if s_len is not None:
            start_idx = i
            while i < n_lines and i not in masked_line_indices:
                curr_s_len = _analyze_single_poem_line(lines[i])
                if curr_s_len == s_len:
                    i += 1
                else:
                    break
            end_idx = i - 1
            # 单句断行必须至少 2 行且标点有交替或一致对仗特征
            if end_idx - start_idx + 1 >= 2:
                has_comma_tail = any(lines[k].strip().endswith("，") for k in range(start_idx, end_idx + 1))
                if has_comma_tail or (end_idx - start_idx + 1 >= 4):
                    for k in range(start_idx, end_idx + 1):
                        masked_line_indices.add(k)
                    masks_info.append({
                        "type": "poem",
                        "start_line": start_idx + 1,
                        "end_line": end_idx + 1,
                        "raw_content": "\n".join(lines[start_idx:end_idx + 1]),
                    })
                    continue

        i += 1

    # 生成 masked_text，对掩码行置空但严格保留换行符
    masked_lines = []
    for idx, orig_line in enumerate(lines):
        if idx in masked_line_indices:
            masked_lines.append("\r" if orig_line.endswith("\r") else "")
        else:
            masked_lines.append(orig_line)

    masked_text = "\n".join(masked_lines)

    # 铁律自检：换行符绝对保持
    assert masked_text.count("\n") == text.count("\n"), "Masked text newline count mismatch!"

    # 按照起始行号排序 masks_info
    masks_info.sort(key=lambda x: x["start_line"])

    return masked_text, masks_info


def _extract_sentences(line: str) -> List[Tuple[int, int, str]]:
    """
    将单行文本切分为完整句子（以 。！？!? 及其闭合引号/标点为界，包含无句末标点的行尾）。
    返回 [(start, end, sentence_text), ...]
    """
    sentences = []
    start = 0
    i = 0
    n = len(line)
    while i < n:
        if line[i] in "。！？!?":
            # 吸收后续连续的标点和闭引号
            while i < n and line[i] in "。！？!?……”:\"'’ ":
                i += 1
            s = line[start:i].strip()
            if s:
                sentences.append((start, i, s))
            start = i
        else:
            i += 1
    if start < n:
        s = line[start:n].strip()
        if s:
            sentences.append((start, n, s))
    return sentences


def scan_typography_flaws(text: str, original_text: str = "") -> List[FormatFinding]:
    """
    排版格式缺陷扫描器。
    
    检测规则：
    1. LONG_PARAGRAPH (P2): 单段连续字数 >= 120 字；
       - 独白情绪降级: 段落内包含 >= 3 个感叹号（！或 !）时降级为 P3。
    2. DRAGGING_SENTENCE (P2): 句内包含逗号 >= 4 个，或单分句无标点连续字数 >= 45 字。
    3. DIALOGUE_MIXED (P3): 对话台词闭引号后紧塞 >= 80 字描写不分行。
    4. AI_CONJUNCTION (P3): 出现典型 AI 翻译腔高频连词。
    
    返回：按 1-based 物理行号升序排列的 FormatFinding 列表。
    """
    if not text and not original_text:
        return []

    # 确定原始文本与待扫描文本
    if not original_text:
        original_text = text
        masked_text, _ = mask_special_blocks(original_text)
    elif text == original_text:
        masked_text, _ = mask_special_blocks(original_text)
    else:
        masked_text = text

    orig_lines = original_text.split("\n")
    masked_lines = masked_text.split("\n")

    findings: List[FormatFinding] = []

    for line_idx, masked_line in enumerate(masked_lines):
        clean_masked = masked_line.rstrip("\r").strip()
        if not clean_masked:
            continue

        line_number = line_idx + 1
        orig_line = orig_lines[line_idx].rstrip("\r")

        # -------------------------------------------------------------
        # 1. 检测 LONG_PARAGRAPH (P2 / P3 降级)
        # -------------------------------------------------------------
        char_count = len(clean_masked)
        if char_count >= 120:
            exclamations = clean_masked.count("！") + clean_masked.count("!")
            if exclamations >= 3:
                findings.append(FormatFinding(
                    line_number=line_number,
                    flaw_type="LONG_PARAGRAPH",
                    severity="P3",
                    snippet=_make_snippet(orig_line),
                    message=f"检测到激昂情绪独白长段落（字数: {char_count}，含 {exclamations} 个感叹号），已降级为 P3 提示",
                    suggestion="建议根据情绪转折点适当断段，保持高潮阅读张力与适度呼吸感。"
                ))
            else:
                findings.append(FormatFinding(
                    line_number=line_number,
                    flaw_type="LONG_PARAGRAPH",
                    severity="P2",
                    snippet=_make_snippet(orig_line),
                    message=f"单段文本过长（当前字数: {char_count} 字，超过 120 字阈值），影响移动端阅读节奏",
                    suggestion="建议将单段长文本拆分为2-3个短自然段，加快阅读节奏，提升移动端视觉呼吸感。"
                ))

        # -------------------------------------------------------------
        # 2. 检测 DRAGGING_SENTENCE (P2)
        # -------------------------------------------------------------
        sentences = _extract_sentences(clean_masked)
        for _, _, s_text in sentences:
            # 条件 A：句内包含逗号 >= 4 个
            comma_count = s_text.count("，") + s_text.count(",")
            if comma_count >= 4:
                findings.append(FormatFinding(
                    line_number=line_number,
                    flaw_type="DRAGGING_SENTENCE",
                    severity="P2",
                    snippet=_make_snippet(s_text),
                    message=f"单句内逗号过多（含 {comma_count} 个逗号），存在拖沓长句现象",
                    suggestion="建议在动作转换或语义停顿处使用句号断句，拆为2-3个独立短句。"
                ))
                continue

            # 条件 B：单分句无标点连续字数 >= 45 字
            clauses = PUNCTUATION_SPLIT_PATTERN.split(s_text)
            for clause in clauses:
                if len(clause) >= 45:
                    findings.append(FormatFinding(
                        line_number=line_number,
                        flaw_type="DRAGGING_SENTENCE",
                        severity="P2",
                        snippet=_make_snippet(clause),
                        message=f"单个分句连续 {len(clause)} 字无停顿标点，阅读易产生窒息感",
                        suggestion="建议在分句中适当增加逗号或句号断句，避免长句窒息感。"
                    ))
                    break

        # -------------------------------------------------------------
        # 3. 检测 DIALOGUE_MIXED (P3)
        # -------------------------------------------------------------
        dialogue_matches = list(re.finditer(r'(?:“([^”]+)”|"([^"]+)")', clean_masked))
        for idx, d_match in enumerate(dialogue_matches):
            end_of_dialogue = d_match.end()
            if idx + 1 < len(dialogue_matches):
                next_start = dialogue_matches[idx + 1].start()
                desc_text = clean_masked[end_of_dialogue:next_start].strip()
            else:
                desc_text = clean_masked[end_of_dialogue:].strip()

            if len(desc_text) >= 80:
                start_context = max(0, d_match.start())
                snippet_text = orig_line[start_context:start_context + 60]
                findings.append(FormatFinding(
                    line_number=line_number,
                    flaw_type="DIALOGUE_MIXED",
                    severity="P3",
                    snippet=_make_snippet(snippet_text),
                    message=f"台词引号后塞入 {len(desc_text)} 字超长动作/心理描写未分行",
                    suggestion="建议将台词后的大段外貌、动作或心理描写独立拆分至新段落，凸显台词冲击力。"
                ))
                break

        # -------------------------------------------------------------
        # 4. 检测 AI_CONJUNCTION (P3)
        # -------------------------------------------------------------
        ai_hits = []
        occupied_spans: List[Tuple[int, int]] = []
        sorted_conjs = sorted(AI_CONJUNCTION_WORDS, key=len, reverse=True)
        for conj in sorted_conjs:
            for m in re.finditer(re.escape(conj), clean_masked):
                span = (m.start(), m.end())
                if not any(s[0] <= span[0] < s[1] or s[0] < span[1] <= s[1] for s in occupied_spans):
                    occupied_spans.append(span)
                    ai_hits.append((m.start(), conj))

        ai_hits.sort(key=lambda x: x[0])
        for pos, conj in ai_hits:
            snippet_start = max(0, pos - 15)
            snippet_text = orig_line[snippet_start:snippet_start + 60]
            findings.append(FormatFinding(
                line_number=line_number,
                flaw_type="AI_CONJUNCTION",
                severity="P3",
                snippet=_make_snippet(snippet_text),
                message=f"检测到典型 AI 翻译腔/过度连词「{conj}」",
                suggestion=f"建议删去生硬连词「{conj}」，直接以人物动作、感官细节或视线转移推进，增强网文沉浸感。"
            ))

    # 排序：按 1-based 物理行号升序排列
    findings.sort(key=lambda f: f.line_number)

    return findings
