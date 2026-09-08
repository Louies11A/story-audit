"""文本扫描器的偏移、尾窗与长输入性能回归。"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ai_patterns_checker import mask_quotes_in_line, mask_quotes_in_text, scan_ai_patterns
from scripts.format_scanner import scan_dragging_sentences, scan_typography_flaws


@pytest.mark.parametrize("quotes", [("“", "”"), ('"', '"')])
@pytest.mark.parametrize("gap", [" ", "  ", "\t", "\u3000"])
def test_r11_dialogue_whitespace_keeps_sentence_offsets(quotes, gap):
    line = f"{quotes[0]}你好！{quotes[1]}{gap}他走了。"
    assert scan_dragging_sentences(line) == []
    assert not any(f.flaw_type == "DRAGGING_SENTENCE" for f in scan_typography_flaws(line))


def test_r11_narrative_after_dialogue_still_reports_original_line():
    text = "出发。\n“好！”  他拿起绳索，走上甲板，弯腰检查，系紧船锚，望向海湾。"
    findings = [f for f in scan_typography_flaws(text) if f.flaw_type == "DRAGGING_SENTENCE"]
    assert len(findings) == 1
    assert findings[0].line_number == 2
    assert "绳索" in findings[0].snippet


def test_r11_dialogue_commas_do_not_leak_into_narration():
    line = "“我看过了，门锁着，窗关着，屋里没人，东西也没动。”  他点头，放下笔。"
    assert scan_dragging_sentences(line) == []


def _quote_scan_steps(text):
    """用执行步数验证增长阶数，避免把机器速度写进断言。"""
    steps = 0
    target_code = mask_quotes_in_line.__code__

    def trace(frame, event, arg):
        nonlocal steps
        if frame.f_code == target_code:
            if event == "line":
                steps += 1
            return trace
        return None

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        result = mask_quotes_in_line(text)
    finally:
        sys.settrace(previous)
    assert result == text
    return steps


@pytest.mark.parametrize("opening", ["“", "「", "【"])
def test_r14_unclosed_quote_work_grows_linearly(opening):
    small = _quote_scan_steps(opening * 256)
    large = _quote_scan_steps(opening * 512)
    assert large <= small * 3


def test_r14_full_scanner_handles_long_unclosed_quotes():
    # 宽松截止时间覆盖完整扫描路径，防止 Python 循环修复后仍有正则回溯。
    code = (
        "from scripts.format_scanner import scan_typography_flaws; "
        "result = scan_typography_flaws('“' * 40000); "
        "assert any(f.flaw_type == 'LONG_PARAGRAPH' for f in result)"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_r14_unclosed_prefix_does_not_hide_other_closed_quotes():
    prefix = "“" * 1024
    line = prefix + "他说「同行台词」仍在。"
    assert mask_quotes_in_line(line) == prefix + "他说「    」仍在。"


def test_r14_masking_never_pairs_quotes_across_lines():
    text = "“未闭合\n仍是叙述。”\n他说：“同行台词。”"
    result = mask_quotes_in_text(text)
    assert result.split("\n")[:2] == text.split("\n")[:2]
    assert len(result) == len(text)
    assert [i for i, ch in enumerate(result) if ch == "\n"] == [i for i, ch in enumerate(text) if ch == "\n"]
    assert "同行台词" not in result


@pytest.mark.parametrize("quotes", [("“", "”"), ('"', '"')])
def test_r14_closed_dialogue_still_detects_following_long_narration(quotes):
    line = f"{quotes[0]}走吧。{quotes[1]}" + "船舱里亮着灯。" * 20
    findings = [f for f in scan_typography_flaws(line) if f.flaw_type == "DIALOGUE_MIXED"]
    assert len(findings) == 1
    assert findings[0].line_number == 1


@pytest.mark.parametrize("separator", ["", "\n"])
@pytest.mark.parametrize("indent", ["", "  "])
@pytest.mark.parametrize("ending,flaw_type", [
    ("命运的齿轮转动了。", "AI_TRAILER_SUMMARY"),
    ("殊不知一场更大的危机才刚刚开始。", "AI_TRAILER_ENDING"),
])
def test_r15_tail_match_uses_its_own_offset(separator, indent, ending, flaw_type):
    text = "开门。\n他走近。\n" + indent + "路很远。" * 200 + separator + indent + ending
    findings = [f for f in scan_ai_patterns(text) if f.flaw_type == flaw_type]
    assert len(findings) == 1
    assert findings[0].line_number == (4 if separator else 3)
    assert ending in findings[0].snippet


def test_r15_ignores_matching_words_outside_tail_window():
    text = "开门。\n他走近。\n命运的齿轮转动了。" + "路很远。" * 200
    assert not any(f.flaw_type.startswith("AI_TRAILER") for f in scan_ai_patterns(text))


def test_r15_finds_late_match_even_when_same_line_has_early_match():
    ending = "命运的齿轮转动了。"
    text = "开门。\n他走近。\n" + ending + "路很远。" * 200 + ending
    findings = [f for f in scan_ai_patterns(text) if f.flaw_type == "AI_TRAILER_SUMMARY"]
    assert len(findings) == 1
    assert findings[0].snippet == ending


def test_r15_keeps_opening_paragraph_protection():
    text = "开门。\n" + "路很远。" * 200 + "命运的齿轮转动了。\n他坐下。"
    assert not any(f.flaw_type.startswith("AI_TRAILER") for f in scan_ai_patterns(text))
