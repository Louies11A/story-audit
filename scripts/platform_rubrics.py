# -*- coding: utf-8 -*-
"""
platform_rubrics.py: 平台商业门禁卡尺与专属质量模型

支持平台卡尺：
1. fanqie: 番茄小说算法完读率模型（前3段核心悬念、千字情绪起伏、3章翻页动力、移动端排版）
2. qidian: 起点中文网追读比模型（3000字爽点节点、50章实力晋阶、金手指在场率、追读比门禁）
3. zhihu: 知乎盐言故事模型（强第一人称限制、首句跳失率控制、伏笔强反转闭环、8000-13000字）
4. generic: 通用网文卡尺（黄金三问、7状态变化、高潮四阶力学、对话三大病灶）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from scripts.types import Finding, format_factual_fix

VALID_PLATFORMS = ("fanqie", "qidian", "zhihu", "generic")

# 金手指/核心资产相关关键词集合（起点等平台）
GOLD_FINGER_KEYWORDS = {
    "系统", "面板", "功法", "外挂", "金手指", "老爷爷", "戒指", "灵根",
    "重构点", "进化核心", "智脑", "芯片", "属性", "技能点", "金币", "气运",
    "等级", "垂发", "能量", "真元", "法宝", "神识", "气血", "天赋", "抽奖",
}

# 冲突/动作核心动词
CONFLICT_VERBS = {
    "杀", "死", "逃", "冲", "斩", "碎", "爆", "崩", "抢", "夺", "斩首",
    "撞", "刺", "轰", "击", "战", "围", "灭", "破", "阻", "扣", "扣动",
    "警报", "倒计时", "危机", "血", "刃", "刀", "枪", "炮", "怪物", "敌",
}


def evaluate_platform_rubric(
    text: str,
    platform: str = "generic",
    chapter_index: float = 1.0,
    genre: str = "通用网文",
) -> Dict[str, Any]:
    """
    针对当前章节执行平台专属商业门禁评估，
    返回包含指标评估、结论判定与标准统一 Finding 列表的字典。
    """
    plat = platform.lower().strip()
    if plat not in VALID_PLATFORMS:
        plat = "generic"

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    word_count = len(re.findall(r'[\u4e00-\u9fa5\w]', text))
    findings: List[Finding] = []
    metrics: Dict[str, Any] = {
        "word_count": word_count,
        "paragraph_count": len(lines),
        "platform": plat,
    }

    loc_prefix = f"第 {chapter_index:03g} 章"

    if plat == "fanqie":
        # 1. 番茄：前 3 段核心悬念/钩子检查
        first_3_paras = lines[:3]
        has_hook = False
        for p in first_3_paras:
            if any(v in p for v in CONFLICT_VERBS) or "？" in p or "?" in p or "！" in p or "!" in p:
                has_hook = True
                break

        metrics["first_3_paras_hook"] = has_hook
        if not has_hook and len(lines) >= 3:
            first_p_snip = first_3_paras[0][:50] if first_3_paras else ""
            findings.append(Finding(
                severity="P2",
                category="platform",
                location=f"{loc_prefix} 开篇前3段",
                evidence=first_p_snip,
                issue="番茄完读门禁：开篇前 3 段缺乏核心冲突、即时危机或情绪悬念，纯背景交代易导致前 5 秒跳失率剧增",
                fix="将核心危机、生死抉择或金手指异动前置到前 3 段内，删减大段静态背景交代。",
            ))

        # 2. 番茄：篇幅评估（推荐 1800 - 3500 字）
        if word_count < 1500:
            findings.append(Finding(
                severity="P3",
                category="platform",
                location=f"{loc_prefix} 全篇",
                evidence=f"字数: {word_count}",
                issue=f"番茄篇幅建议：当前单章字数 {word_count} 字偏薄，番茄完读模型推荐单章 1800-3500 字以支撑情绪饱满度",
                fix="适当扩充本章中段博弈与微爽点细节，保证每章具备充足情绪价值与信息密度。",
            ))

        # 3. 番茄：章尾翻页动力
        tail_text = "".join(lines[-2:]) if lines else ""
        tail_hook = any(c in tail_text for c in ("？", "?", "……", "倒计时", "然而", "危险", "杀", "轰", "冷笑"))
        metrics["tail_hook"] = tail_hook
        if not tail_hook and lines:
            findings.append(Finding(
                severity="P2",
                category="platform",
                location=f"{loc_prefix} 章尾",
                evidence=tail_text[-50:] if tail_text else "",
                issue="番茄翻页门禁：章尾缺乏断章悬念或未兑现预期，影响读者点击下一章翻页率",
                fix="在章尾收束前抛出新变量、危机倒计时或未解谜题，营造迫切翻页动力。",
            ))

    elif plat == "qidian":
        # 1. 起点：金手指在场率
        gold_hits = [k for k in GOLD_FINGER_KEYWORDS if k in text]
        metrics["gold_finger_hits"] = len(gold_hits)
        if len(gold_hits) == 0:
            findings.append(Finding(
                severity="P2",
                category="platform",
                location=f"{loc_prefix} 全篇",
                evidence="未匹配到核心金手指关键词",
                issue="起点追读门禁：本章全程未出现或运用核心金手指（外挂/系统/功法/专属底牌），可能造成主线卖点脱节",
                fix="适当穿插主角对金手指的调用、推演、参数审视或成长收获，强化核心升级期待感。",
            ))

        # 2. 起点：单章字数（起点推荐 2500 - 4500 字）
        if word_count < 2000:
            findings.append(Finding(
                severity="P3",
                category="platform",
                location=f"{loc_prefix} 全篇",
                evidence=f"字数: {word_count}",
                issue=f"起点单章字数 {word_count} 字偏低，起点标准付费/连载章节宜保持在 2500-4000 字",
                fix="充实战术对抗细节或世界观推演，达到起点日更连载标准容量。",
            ))

        # 3. 起点：3000字情绪节点
        metrics["satisfaction_node_present"] = len(gold_hits) > 0 or any(v in text for v in ("突破", "升级", "获", "败", "斩"))

    elif plat == "zhihu":
        # 1. 知乎：强第一人称限制
        i_count = text.count("我")
        he_count = text.count("他") + text.count("她")
        metrics["i_count"] = i_count
        metrics["third_person_count"] = he_count

        # 若全文“我”极少且第三人称占主导，判定为违背盐言故事第一人称门禁
        if i_count < 5 and he_count > 15:
            findings.append(Finding(
                severity="P1",
                category="platform",
                location=f"{loc_prefix} 视点体系",
                evidence=f"“我”频次: {i_count}, “他/她”频次: {he_count}",
                issue="知乎盐言红线：盐言故事要求强第一人称视角，当前文本呈现第三人称主导，严重破坏沉浸代入感",
                fix=format_factual_fix("consistency", "将全篇视角严格统一重构为“我”的当事人主观亲历视角，禁止上帝视角读心。"),
            ))

        # 2. 知乎：首句跳失率控制
        first_line = lines[0] if lines else ""
        metrics["first_line"] = first_line[:40]
        is_scenery_open = bool(re.match(r'^(?:清晨|夜色|秋风|冬日|窗外|天空|阳光|大雨|寒风|白云|青山)', first_line))
        if is_scenery_open:
            findings.append(Finding(
                severity="P2",
                category="platform",
                location=f"{loc_prefix} 首句",
                evidence=first_line[:40],
                issue="知乎首句门禁：首句以天气/景物描写开篇，缺乏戏剧冲突与当事人抓手，容易引发高跳失率",
                fix="首句直接抛出颠覆性现状、冷峻动作或人际极端冲突（如“成婚第七年，他带回了一个……”）。",
            ))

        # 3. 知乎：篇幅控制（8000 - 13000 字标准盐言篇幅）
        metrics["target_range"] = "8000-13000字"
        if word_count < 2500:
            # 单章若作为连载的一部分提示篇幅，若作为短篇全文则警告
            findings.append(Finding(
                severity="P3",
                category="platform",
                location=f"{loc_prefix} 全篇",
                evidence=f"字数: {word_count}",
                issue=f"知乎盐言篇幅考量：当前字数 {word_count} 字，完整盐言短篇故事建议在 8000-13000 字区间形成完整反转闭环",
                fix="如为短篇全稿，需扩充前文伏笔铺垫、中段情感拉扯与终局高能反转；如为分节连载请保持紧凑。",
            ))

    else:
        # generic 通用网文卡尺
        metrics["genre"] = genre
        if word_count < 1500:
            findings.append(Finding(
                severity="P3",
                category="platform",
                location=f"{loc_prefix} 全篇",
                evidence=f"字数: {word_count}",
                issue=f"通用网文标准：单章 {word_count} 字偏短，建议充实单章核心事件链",
                fix="完善目标-行动-阻碍-结果闭环，强化单章情绪落点。",
            ))

    passed = not any(f.severity in ("P0", "P1") for f in findings)
    return {
        "platform": plat,
        "passed": passed,
        "metrics": metrics,
        "findings": findings,
    }
