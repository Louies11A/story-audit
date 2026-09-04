# -*- coding: utf-8 -*-
"""
ai_patterns_checker.py: 毫秒级深度 AI 模式与套路句式扫描器

纯 Python 3.8+ 标准库实现，零外部依赖。
深度扫描典型 AI 写作指纹：
  1. not-is-comparison（“不是……而是……”对仗句式、反序对比“是……而不是……”）
  2. em-dash（正文中残留的破折号“——”硬停顿）
  3. voice-contrast（声音/神态反差句式，如“声音不大，却清晰传入……”、“语气平淡，却让所有人心中一凛”）
  4. negation-parade（连续否定排比句式，如“没有X，没有Y……”、“没X，没Y……只是Z”）
  5. trailer-ending / trailer-summary（章末出戏总结体与预告式收尾）
  6. god-view-exposition（Gate G 上帝解释腔/替读者划重点/监控摄像头式纯动作清单）

提供公共函数 scan_ai_patterns(text: str) -> List[FormatFinding]，支持行号定位与严重度分级。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

from scripts.types import FormatFinding

__all__ = [
    "scan_ai_patterns",
    "mask_quotes_in_line",
    "mask_quotes_in_text",
]

QUOTE_PAIRS: List[Tuple[str, str]] = [
    ("“", "”"),
    ('"', '"'),
    ("‘", "’"),
    ("'", "'"),
    ("「", "」"),
    ("『", "』"),
    ("【", "】"),
]

def mask_quotes_in_line(line: str) -> str:
    """
    单行内对话与括号掩码：保留换行符与原始行长，将引号内字符替换为等宽占位符。
    严禁跨行配对，避免未闭合引号导致大段正文被静默穿透。
    """
    if not line:
        return line

    chars = list(line)
    n = len(chars)

    for open_q, close_q in QUOTE_PAIRS:
        i = 0
        while i < n:
            if chars[i] == open_q:
                # 寻找同行的配对闭合引号
                if open_q == close_q:
                    # 单一符号引号（如 " 或 '）
                    j = -1
                    for k in range(i + 1, n):
                        if chars[k] == close_q:
                            j = k
                            break
                else:
                    j = -1
                    for k in range(i + 1, n):
                        if chars[k] == close_q:
                            j = k
                            break

                if j != -1:
                    # 掩码闭合区间内内容，保留两端引号字符位置以便调试
                    for k in range(i + 1, j):
                        if chars[k] != "\n" and chars[k] != "\r":
                            chars[k] = " "
                    i = j + 1
                else:
                    # 找不到闭引号则不跨行掩码
                    i += 1
            else:
                i += 1

    return "".join(chars)


def mask_quotes_in_text(text: str) -> str:
    """逐行对多行文本进行引号掩码"""
    lines = text.split("\n")
    masked_lines = [mask_quotes_in_line(l) for l in lines]
    return "\n".join(masked_lines)


def _make_snippet(text: str, max_len: int = 60) -> str:
    """生成简洁的上下文切片（<= max_len 字符）"""
    cleaned = " ".join(text.replace("\n", " ").replace("\r", "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len - 3] + "..."


# ==============================================================================
# 正则表达式与模式特征定义
# ==============================================================================

# 1. 声音反差腔 (voice-contrast)
# 例：“声音不大，却清晰传入……”、“语气平淡，却让所有人心中一凛”
VOICE_CONTRAST_PATTERN = re.compile(
    r'(?:(?:声音|语调|声线)(?:并)?不[大高响亮急重多]|(?:语气|神情|神色)[极甚]?(?:平淡|平静|冰冷|漠然|平和))'
    r'[^。！？!?\n]{0,16}[，,]?[^。！？!?\n]{0,12}[却但偏]'
)

# 2. 连续否定排比 (negation-parade)
# 例：“没有伴奏，没有和声，没有提词器。”
# 例：“他没炫技，没有那种架势，只是唱……”
NEGATION_PARADE_1 = re.compile(r'(?:没有[^。！？!?\n，,、]{1,14}[，,、]\s*){2,}')
NEGATION_PARADE_2 = re.compile(
    r'(?<![沉淹埋出隐湮吞覆漫泯])没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,14}[，,]\s*'
    r'没(?!有?过?多久)(?:有)?[^。！？!?\n，,]{1,16}[，,。.][^。！？!?\n，,]{0,6}只(?:是|会|有)'
)

# 3. 反序对比 (reverse not-is: “是A，不是B”)
REVERSE_NOT_IS_PATTERN = re.compile(
    r'(?<![还是只是可是但是于是倒是像是若是要是在是便是老是总是更是最是算怕凡或即自竟原本当仍许净光单尽不])'
    r'是([^，,。！？!?\n]{1,15})[，,]\s*(?:而)?不是([^。！？!?\n]{1,25})'
)

# 4. 不是……而是…… / 不是……是…… (not-is-comparison)
NOT_IS_1 = re.compile(r'(?<![难道也并不])不是([^，,。！？!?\n]{1,25})[，,]\s*而是([^。！？!?\n]{1,30})')
NOT_IS_2 = re.compile(r'(?<![难道也并不])不是([^，,。！？!?\n]{1,25})[，,]\s*是([^。！？!?\n]{1,30})')

# 5. 章末出戏预告式收尾 (trailer-ending)
TRAILER_ENDING_PATTERN = re.compile(
    r'(?:没人知道|谁也不知道|谁也没想到|殊不知|(?:这)?才刚刚开(?:始|头)|'
    r'正(?:朝着|向着)[^。！？!?\n]{0,24}(?:压|涌|袭|逼)(?:了?过去|了?过来|来)|'
    r'(?<!正式)拉开(?:序幕|帷幕)|即将(?:开始|来临|降临))'
)

# 6. 章末状态总结体 (trailer-summary)
TRAILER_SUMMARY_PATTERN = re.compile(
    r'(?:这一(?:夜|天|刻|战|年|局|役)[，,]?[^。！？!?，,\n]{0,6}(?<!命中)(?<!是)注定[^。！？!?\n]{0,8}[。！!]|'
    r'就这样[，,][^。！？!?，,\n]{0,8}(?:一切|全部)[^。！？!?，,\n]{0,4}(?:结束了|落幕|收场)[。！!]|'
    r'这一切[，,]?[^。！？!?，,\n]{0,6}(?:都)?(?:说明|意味着|结束了)(?!的)(?:(?!什么)[^。！？!?\n]){0,6}[。！!]|'
    r'(?:新的篇章|新的旅程|崭新的篇章|新的人生)[^。！？!?\n]{0,6}(?:开始|拉开|展开)|'
    r'命运[^。！？!?\n]{0,6}齿轮)'
)

# 7. 监控摄像头式纯动作清单 (action-list)
ACTION_VERBS = (
    "伸手|抬手|探手|拿起|拿过|取出|取过|掏出|摸出|抓起|攥住|握住|捏住|按住|"
    "推开|拉开|打开|关上|放下|递给|挑开|掀开|扯开|拧开|倒出|端起|转身|回头|"
    "抬头|低头|弯腰|俯身|走到|走向|坐下|站起|看向|看着|盯着|扫过"
)
ACTION_VERB_RE = re.compile(ACTION_VERBS)

# 8. 上帝解释腔/替读者划重点 (god-view exposition)
GOD_VIEW_EXPOSITION_PATTERN = re.compile(
    r'(?:(?:他|她|我|众人)?(?:很清楚|心里很清楚|心知肚明)[，,]?(?:这意味着|这代表着)|'
    r'替读者划重点|(?:殊不知|殊不料)[，,]?[^。！？!?\n]{0,20}(?:都在|已然|早就在)|'
    r'不得不承认[，,]?这是)'
)


def _is_either_or(subtext: str) -> bool:
    """排除连词短语：'不是A就是B' 或 '不是A也是B'"""
    return bool(re.search(r'不是[^，,。！？!?\n]{1,20}(?:就是|也是)', subtext))


def _is_tag_question(subtext: str) -> bool:
    """排除反问尾巴：'不是……吗/吧/嘛' 或 '……，是吗/是吧'"""
    s = subtext.strip()
    return s.endswith(("吗", "吧", "嘛", "？", "?"))


def _is_confirmation_tag(subtext: str) -> bool:
    """排除段首承接确认词：'是的，……' / '是啊，……'"""
    s = subtext.strip()
    return bool(re.match(r'^(?:是的|是啊|是呢|是的啊)[，,。.！!]', s))


def scan_ai_patterns(text: str) -> List[FormatFinding]:
    """
    全量深度扫描文本中的 AI 模式与套路句式。
    返回按 1-based 物理行号升序排列的 FormatFinding 列表。
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    total_lines = len(lines)
    findings: List[FormatFinding] = []

    # 确定章末检测窗口（最后 600 字符，且至少处于全章后 25% 区域，严禁覆盖开篇段落）
    non_empty_line_indices = [idx for idx, l in enumerate(lines) if l.strip()]
    char_offset_threshold = max(int(len(text) * 0.75), len(text) - 600)
    opening_line_limit = non_empty_line_indices[1] if len(non_empty_line_indices) >= 3 else 0
    current_char_offset = 0

    for line_idx, orig_line in enumerate(lines):
        line_num = line_idx + 1
        line_len = len(orig_line) + 1  # 包含 \n
        clean_orig = orig_line.rstrip("\r").strip()
        if not clean_orig:
            current_char_offset += line_len
            continue

        # 引号外正文掩码
        masked_line = mask_quotes_in_line(orig_line.rstrip("\r"))
        is_in_tail_window = (
            line_idx > opening_line_limit and current_char_offset >= char_offset_threshold
        )

        # -------------------------------------------------------------
        # 1. 检测 em-dash (正文中残留的破折号“——”硬停顿)
        # -------------------------------------------------------------
        if "——" in masked_line:
            # 排除纯分隔线（如连续多个破折号组成的分割线）
            if not re.match(r'^[—\-\s=]+$', clean_orig):
                findings.append(FormatFinding(
                    line_number=line_num,
                    flaw_type="AI_EM_DASH",
                    severity="P2",
                    snippet=_make_snippet(clean_orig),
                    message="检测到正文残留破折号「——」硬停顿，影响移动端阅读气流",
                    suggestion="建议按功能改写为逗号、破折省略或以人物具体动作承接，保持行文连贯流畅。"
                ))

        # -------------------------------------------------------------
        # 2. 检测 voice-contrast (声音反差腔)
        # -------------------------------------------------------------
        m_vc = VOICE_CONTRAST_PATTERN.search(masked_line)
        if m_vc:
            hit_text = clean_orig[max(0, m_vc.start() - 5):min(len(clean_orig), m_vc.end() + 15)]
            findings.append(FormatFinding(
                line_number=line_num,
                flaw_type="AI_VOICE_CONTRAST",
                severity="P2",
                snippet=_make_snippet(hit_text),
                message="检测到典型 AI 音量/语气反差句式（声音不大/语气平淡……却……）",
                suggestion="建议删去音量或神态反差铺垫，直接描写话语在场内激起的具象反应或后续动作。"
            ))

        # -------------------------------------------------------------
        # 3. 检测 negation-parade (连续否定排比)
        # -------------------------------------------------------------
        m_np1 = NEGATION_PARADE_1.search(masked_line)
        m_np2 = NEGATION_PARADE_2.search(masked_line)
        if m_np1:
            findings.append(FormatFinding(
                line_number=line_num,
                flaw_type="AI_NEGATION_PARADE",
                severity="P2",
                snippet=_make_snippet(clean_orig[m_np1.start():m_np1.end() + 10]),
                message="检测到连续否定排比句式（没有X，没有Y……）",
                suggestion="建议精简连续否定排比，直接描写当下核心在场事实或具象画面。"
            ))
        elif m_np2:
            findings.append(FormatFinding(
                line_number=line_num,
                flaw_type="AI_NEGATION_PARADE",
                severity="P2",
                snippet=_make_snippet(clean_orig[m_np2.start():m_np2.end() + 10]),
                message="检测到先否定后肯定模板句式（没X，没Y……只是Z）",
                suggestion="建议删去多余否定铺垫，直接陈述肯定事实与核心动作。"
            ))

        # -------------------------------------------------------------
        # 4. 检测 not-is-comparison 与 reverse not-is
        # -------------------------------------------------------------
        # 4.1 不是……而是……
        m_not_is_1 = NOT_IS_1.search(masked_line)
        if m_not_is_1:
            matched_span = m_not_is_1.group(0)
            if not _is_either_or(matched_span) and not _is_tag_question(matched_span):
                findings.append(FormatFinding(
                    line_number=line_num,
                    flaw_type="AI_NOT_IS",
                    severity="P2",
                    snippet=_make_snippet(clean_orig[m_not_is_1.start():m_not_is_1.end()]),
                    message="检测到典型 AI 对仗句式「不是……而是……」",
                    suggestion="建议删去否定前置铺垫，直接陈述肯定事实或通过具体动作细节展现。"
                ))
        else:
            # 4.2 不是……是……
            m_not_is_2 = NOT_IS_2.search(masked_line)
            if m_not_is_2:
                matched_span = m_not_is_2.group(0)
                if (not _is_either_or(matched_span)
                        and not _is_tag_question(matched_span)
                        and not _is_confirmation_tag(matched_span)):
                    findings.append(FormatFinding(
                        line_number=line_num,
                        flaw_type="AI_NOT_IS",
                        severity="P2",
                        snippet=_make_snippet(clean_orig[m_not_is_2.start():m_not_is_2.end()]),
                        message="检测到典型 AI 对仗句式「不是……是……」",
                        suggestion="建议删去否定前置铺垫，直接陈述肯定事实或展开行动描写。"
                    ))

        # 4.3 反序对比：是……而不是……
        m_rev = REVERSE_NOT_IS_PATTERN.search(masked_line)
        if m_rev:
            matched_span = m_rev.group(0)
            if not _is_tag_question(matched_span) and "是不是" not in matched_span:
                findings.append(FormatFinding(
                    line_number=line_num,
                    flaw_type="AI_NOT_IS",
                    severity="P2",
                    snippet=_make_snippet(clean_orig[m_rev.start():m_rev.end()]),
                    message="检测到典型 AI 反序对比句式「是……而不是……」",
                    suggestion="建议删去冗余的否定尾巴，保留主干陈述，精炼叙事。"
                ))

        # -------------------------------------------------------------
        # 5. 检测 trailer-ending 与 trailer-summary (仅在章末窗口)
        # -------------------------------------------------------------
        if is_in_tail_window:
            m_te = TRAILER_ENDING_PATTERN.search(masked_line)
            if m_te:
                findings.append(FormatFinding(
                    line_number=line_num,
                    flaw_type="AI_TRAILER_ENDING",
                    severity="P2",
                    snippet=_make_snippet(clean_orig[m_te.start():m_te.end() + 20]),
                    message="检测到章末预告式总结收尾（没人知道/殊不知/才刚刚开始等）",
                    suggestion="建议删去全知叙述者的剧透预告，将视角锁定在角色当下体验，留白让读者自然翻页。"
                ))

            m_ts = TRAILER_SUMMARY_PATTERN.search(masked_line)
            if m_ts:
                findings.append(FormatFinding(
                    line_number=line_num,
                    flaw_type="AI_TRAILER_SUMMARY",
                    severity="P2",
                    snippet=_make_snippet(clean_orig[m_ts.start():m_ts.end() + 20]),
                    message="检测到章末状态总结体（这一夜注定……/这一切都结束了/命运的齿轮等）",
                    suggestion="建议删去机械的状态盖章句，以角色具体的动作、环境定格或事件余波收尾。"
                ))

        # -------------------------------------------------------------
        # 6. 检测 god-view-exposition (动作清单 / 上帝解释腔)
        # -------------------------------------------------------------
        # 6.1 监控摄像头式动作清单
        action_hits = list(ACTION_VERB_RE.finditer(masked_line))
        comma_count = masked_line.count("，") + masked_line.count(",") + masked_line.count("、")
        if len(action_hits) >= 5 and comma_count >= 4:
            findings.append(FormatFinding(
                line_number=line_num,
                flaw_type="AI_GOD_VIEW_EXPOSITION",
                severity="P2",
                snippet=_make_snippet(clean_orig),
                message=f"检测到监控摄像头式纯动作清单（单段堆叠 {len(action_hits)} 个通用动词且缺少视线焦点）",
                suggestion="建议融入人物主观视线与心理反应，注入动作意图，避免机械动作步骤流水账罗列。"
            ))

        # 6.2 上帝解释腔
        m_gv = GOD_VIEW_EXPOSITION_PATTERN.search(masked_line)
        if m_gv:
            findings.append(FormatFinding(
                line_number=line_num,
                flaw_type="AI_GOD_VIEW_EXPOSITION",
                severity="P2",
                snippet=_make_snippet(clean_orig[m_gv.start():m_gv.end() + 20]),
                message="检测到 Gate G 上帝解释腔/替读者划重点句式",
                suggestion="建议撤回全知作者视角解说，通过场内客观事实呈现，交由读者自行领会。"
            ))

        current_char_offset += line_len

    findings.sort(key=lambda f: f.line_number)
    return findings


def main() -> int:
    if sys.platform == 'win32':
        try:
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            if hasattr(sys.stderr, 'reconfigure'):
                sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="AI 模式与套路句式扫描器")
    parser.add_argument("file", help="待扫描的文本或章节文件")
    parser.add_argument("--fail-on-p2", action="store_true", help="发现 P2 缺陷时退出码为 1")
    args = parser.parse_args()

    target_path = Path(args.file)
    if not target_path.is_file():
        print(f"错误: 文件不存在: {target_path}", file=sys.stderr)
        return 2

    text = target_path.read_text(encoding="utf-8", errors="replace")
    findings = scan_ai_patterns(text)

    if not findings:
        print(f"✅ {target_path.name}: 未检出高危 AI 套路句式。")
        return 0

    print(f"🚨 {target_path.name}: 发现 {len(findings)} 处 AI 套路模式：")
    for f in findings:
        print(f"  - [{f.severity}] 行 {f.line_number} ({f.flaw_type}): {f.message}")
        print(f"    原文: `{f.snippet}`")
        print(f"    建议: {f.suggestion}")

    if args.fail_on_p2 and any(f.severity in ("P0", "P1", "P2") for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
