# -*- coding: utf-8 -*-
"""
story_audit.py: 长篇网文深度审查核心调度管线与纯模块化 Python API

架构铁律：本项目采用纯模块化 Python API 驱动设计，不提供亦不涉及任何 CLI 命令行接口。
后续所有功能开发与生态扩展均严格围绕 Python API、强类型数据契约与 Agent 工具函数展开，坚决不涉及 CLI。

串联安全 I/O、智能章节匹配器、排版扫描器、双轨账本状态机、跨章接缝器与安全回写器。
严格遵循 Python 3.8+ 标准库与零外部依赖约定。
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保技能根目录在 sys.path 中，支持 python scripts/story_audit.py 直接独立调用
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.chapter_linker import extract_boundary_slices
from scripts.chapter_resolver import ChapterResolver
from scripts.genre_detector import GenreProfile, detect_genre, resolve_canonical_genre
from scripts.format_scanner import scan_typography_flaws
from scripts.ledger_engine import (
    AssetItem,
    LedgerDirtyError,
    LedgerState,
    check_dirty_state,
    create_volume_checkpoint,
    extract_heuristic_assets,
    read_file_safe,
    save_ledger_state,
    scan_foreshadowing_tags,
    sync_from_markdown,
    write_file_safe,
)
from scripts.safe_writer import (
    AmbiguousPatchError,
    PatchAnchorNotFoundError,
    SafeWriterError,
    apply_patch_with_disambiguation,
)
from scripts.ai_patterns_checker import scan_ai_patterns
from scripts.author_memory import AuthorMemory
from scripts.runtime_detector import detect_runtime, is_subagent_context, resolve_execution_mode
from scripts.platform_rubrics import evaluate_platform_rubric, VALID_PLATFORMS
from scripts.audit_state import (
    AuditState,
    get_audit_state_path,
    load_audit_state,
    save_audit_state,
    get_inherited_items,
    render_inherited_items_section,
)
from scripts.types import BoundaryContext, ChapterItem, Finding, FormatFinding, PatchSpec, format_factual_fix




def parse_scope_range(scope_str: str) -> Tuple[float, float]:
    """解析范围字符串，如 '31-35' 或 '1-30'"""
    if not scope_str or "-" not in scope_str:
        raise ValueError(f"无效的范围格式: {scope_str}，应形如 '31-35'")
    parts = scope_str.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"无效的范围格式: {scope_str}")
    try:
        start = float(parts[0].strip())
        end = float(parts[1].strip())
        return min(start, end), max(start, end)
    except Exception as e:
        raise ValueError(f"范围解析失败: {scope_str}, {e}")


def get_report_archive_path(reports_dir: Path, chapter_index: float) -> Path:
    """计算单章归档路径：reports/单章审查/{001-100章等}/第{N}章_审查报告.md"""
    idx = float(chapter_index)
    if idx <= 0:
        bucket = "001-100章"
    else:
        start = int((int(idx) - 1) // 100) * 100 + 1
        end = start + 99
        bucket = f"{start:03d}-{end:03d}章"

    if idx == int(idx):
        filename = f"第{int(idx):03d}章_审查报告.md"
    else:
        filename = f"第{idx}章_审查报告.md"
    return reports_dir / "单章审查" / bucket / filename


def detect_violations_in_text(text: str) -> List[Dict[str, str]]:
    """检测正文中的显式审计标注与严重违规 (P0/P1)"""
    violations: List[Dict[str, str]] = []

    # 1. HTML 风格注释 <!-- audit:violation level="P0" message="..." --> 或 <!-- audit:p0 message="..." -->
    pat_violation = re.compile(
        r'<!--\s*audit:violation\s+level="(?P<level>P[0-3])"(?:\s+message="(?P<msg>[^"]*)")?\s*-->',
        re.IGNORECASE,
    )
    for m in pat_violation.finditer(text):
        level = m.group("level").upper()
        msg = m.group("msg") or f"{level} 违规"
        violations.append({"level": level, "message": msg})

    pat_short = re.compile(
        r'<!--\s*audit:(?P<level>p[0-3])(?:\s+message="(?P<msg>[^"]*)")?\s*-->',
        re.IGNORECASE,
    )
    for m in pat_short.finditer(text):
        level = m.group("level").upper()
        msg = m.group("msg") or f"{level} 违规"
        violations.append({"level": level, "message": msg})

    # 2. 方括号与中文括号风格标注
    pat_bracket = re.compile(
        r'(?:【|\[)(?P<level>P[0-3])(?::|：|\s+)(?P<msg>[^】\n\]]+)(?:】|\])',
        re.IGNORECASE,
    )
    for m in pat_bracket.finditer(text):
        level = m.group("level").upper()
        msg = m.group("msg").strip()
        violations.append({"level": level, "message": msg})

    return violations


def build_pre_audit_bundle(
    project_dir: Path,
    curr_chapter: ChapterItem,
    prev_chapter: Optional[ChapterItem],
    chapters: List[ChapterItem],
    state: LedgerState,
    findings: List[FormatFinding],
    boundary_ctx: BoundaryContext,
    curr_enc: str,
    curr_eol: str,
    gap_warnings: List[str],
    genre_profile: Optional[GenreProfile] = None,
    requested_mode: str = "auto",
    effective_mode: str = "full",
    fallback_reason: str = "none",
    platform: str = "generic",
    platform_data: Optional[Dict[str, Any]] = None,
    author_memory_text: Optional[str] = None,
    inherited_items: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造结构严格冻结契约预审包字典"""
    try:
        target_file_str = curr_chapter.path.relative_to(project_dir).as_posix()
    except Exception:
        target_file_str = curr_chapter.path.as_posix()

    prev_file_str: Optional[str] = None
    if prev_chapter:
        try:
            prev_file_str = prev_chapter.path.relative_to(project_dir).as_posix()
        except Exception:
            prev_file_str = prev_chapter.path.as_posix()

    active_assets: List[Dict[str, Any]] = []
    if isinstance(state.assets, dict):
        for item in state.assets.values():
            if hasattr(item, "to_dict"):
                asset_dict = item.to_dict()
            elif isinstance(item, dict):
                asset_dict = dict(item)
            else:
                continue
            # history 截断保留最近记录（最多 5 条流水）
            if "history" in asset_dict and isinstance(asset_dict["history"], list):
                asset_dict["history"] = asset_dict["history"][-5:]
            active_assets.append(asset_dict)

    if genre_profile is None:
        genre_profile = detect_genre("")

    bundle = {
        "meta": {
            "version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "target_chapter": curr_chapter.index,
            "target_file": target_file_str,
            "encoding": curr_enc,
            "newline": curr_eol,
            "genre": genre_profile.primary_genre,
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "fallback_reason": fallback_reason,
            "platform": platform,
        },
        "runtime_dispatch": {
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "fallback_reason": fallback_reason,
        },
        "platform_diagnostics": {
            "platform": platform_data.get("platform", platform),
            "passed": platform_data.get("passed", True),
            "metrics": platform_data.get("metrics", {}),
            "findings": [
                f.to_dict() if hasattr(f, "to_dict") else f
                for f in platform_data.get("findings", [])
            ],
        } if platform_data else {},
        "author_memory": author_memory_text or "",
        "inherited_items": inherited_items or {},
        "genre_diagnostics": {
            "detected_genre": genre_profile.primary_genre,
            "confidence": genre_profile.confidence,
            "category_group": genre_profile.category_group,
            "secondary_genres": genre_profile.secondary_genres,
            "first_principles": genre_profile.first_principles,
            "red_lines": genre_profile.red_lines,
            "keywords_matched": genre_profile.keywords_matched,
        },
        "sequence_diagnostics": {
            "has_gap": len(gap_warnings) > 0,
            "gap_warnings": gap_warnings,
        },
        "boundary": {
            "has_prev_chapter": boundary_ctx.has_prev_chapter,
            "prev_chapter_file": prev_file_str,
            "prev_tail_300": boundary_ctx.prev_tail_300,
            "curr_head_300": boundary_ctx.curr_head_300,
            "is_pov_transition": boundary_ctx.is_pov_transition,
            "transition_clue": boundary_ctx.transition_clue,
            "isolation_zones": boundary_ctx.isolation_zones,
        },
        "ledger_snapshot": {
            "active_assets": active_assets,
            "foreshadowing_stash": list(state.foreshadowing_stash),
        },
        "format_scan": {
            "total_flaws": len(findings),
            "findings": [
                f.to_dict() if hasattr(f, "to_dict") else {
                    "line_number": getattr(f, "line_number", 0),
                    "flaw_type": getattr(f, "flaw_type", ""),
                    "severity": getattr(f, "severity", "P2"),
                    "snippet": getattr(f, "snippet", ""),
                    "message": getattr(f, "message", ""),
                    "suggestion": getattr(f, "suggestion", ""),
                    "category": getattr(f, "category", "format"),
                    "location": getattr(f, "location", f"行 {getattr(f, 'line_number', 0)}"),
                    "evidence": getattr(f, "evidence", getattr(f, "snippet", "")),
                    "issue": getattr(f, "issue", getattr(f, "message", "")),
                    "fix": getattr(f, "fix", getattr(f, "suggestion", "")),
                }
                for f in findings
            ],
        },
    }
    return bundle


def render_audit_report(
    curr_chapter: ChapterItem,
    prev_chapter: Optional[ChapterItem],
    p0_list: List[str],
    p1_list: List[str],
    findings: List[FormatFinding],
    boundary_ctx: BoundaryContext,
    state: LedgerState,
    gap_warnings: List[str],
    genre_profile: Optional[GenreProfile] = None,
    requested_mode: str = "auto",
    effective_mode: str = "full",
    fallback_reason: str = "none",
    platform: str = "generic",
    author_memory_text: Optional[str] = None,
    platform_data: Optional[Dict[str, Any]] = None,
    inherited_items: Optional[Dict[str, Any]] = None,
) -> str:
    """渲染符合统一审查报告 Schema (Markdown) 的报告内容"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev_info = f"第 {prev_chapter.index} 章 ({prev_chapter.title})" if prev_chapter else "无 (首章/起点)"

    p2_count = sum(1 for f in findings if f.severity == "P2") + len(gap_warnings)
    p3_count = sum(1 for f in findings if f.severity == "P3")
    verdict = f"P0 致命错误: {len(p0_list)} 项 | P1 严重失误: {len(p1_list)} 项 | P2 局部瑕疵: {p2_count} 项 | P3 润色建议: {p3_count} 项"

    if genre_profile is None:
        genre_profile = detect_genre("")

    gp = genre_profile
    sec_tags = ", ".join(gp.secondary_genres) if gp.secondary_genres else "无显式交叉"
    kw_str = ", ".join(gp.keywords_matched[:8]) if gp.keywords_matched else "通用特征"

    lines = [
        "=== story-audit 深度审查报告 ===",
        f"Requested Mode: {requested_mode}",
        f"Effective Mode: {effective_mode}",
        f"Fallback: {fallback_reason}",
        f"Platform Rubric: {platform}",
        f"Genre: {gp.primary_genre}",
        f"Scope: 第{curr_chapter.index:03g}章",
        "",
        f"# 📚 长篇网文深度审查报告：第 {curr_chapter.index} 章",
        f"> 审查时间：{now_str} | 运行模式：{effective_mode.capitalize()} (Requested: {requested_mode}, Fallback: {fallback_reason})",
        f"> 审查范围：第 {curr_chapter.index} 章 ({curr_chapter.title}) (对比承接源：{prev_info})",
        f"> 平台门禁：{platform} | 综合裁决：{verdict}",
        "",
        "---",
        "",
        "## 🎯 题材诊断与读者第一性原理卡尺 (Genre Diagnostics)",
        f"* **判研题材**：**{gp.primary_genre}**（置信度: {gp.confidence:.0%} | 大类归属: {gp.category_group}）",
        f"* **二级标签**：{sec_tags}",
        f"* **核心特征词**：`{kw_str}`",
        f"* 🧭 **第一性原理追读卡尺**：",
        f"  > {gp.first_principles}",
        f"* 🚨 **题材特异性毒点预警 (绝不可踩)**：",
    ]
    for rl in gp.red_lines:
        lines.append(f"  * ⚠️ {rl}")

    # 平台专属商业门禁诊断
    if platform_data and (platform != "generic" or platform_data.get("findings")):
        p_status = "🟢 合格通过" if platform_data.get("passed", True) else "🔴 触发门禁拦截"
        lines.extend([
            "",
            "---",
            "",
            f"## 📱 平台商业门禁诊断 (Platform Diagnostics: {platform})",
            f"* **平台卡尺**：{platform}",
            f"* **门禁状态**：{p_status}",
        ])
        for k, v in platform_data.get("metrics", {}).items():
            lines.append(f"* **{k}**：`{v}`")
        p_findings = platform_data.get("findings", [])
        if p_findings:
            lines.append("* **平台门禁发现项**：")
            for pf in p_findings:
                lines.append(f"  * ⚠️ [{pf.severity}] {pf.issue} (建议: {pf.fix})")

    # 作者记忆上下文
    if author_memory_text:
        lines.extend([
            "",
            "---",
            "",
            "## 👤 作者画像与偏好联动 (Author Memory)",
            author_memory_text,
        ])

    # 跨批因果继承与开放缺陷
    if inherited_items and (inherited_items.get("open_defects") or inherited_items.get("foreshadowing_commitments")):
        lines.extend([
            "",
            "---",
            "",
            render_inherited_items_section(inherited_items),
        ])

    lines.extend([
        "",
        "---",
        "",
        "## 🚨 一、阻断性致命错误 (P0 级)",
    ])

    if p0_list:
        for i, item in enumerate(p0_list, 1):
            lines.append(f"### {i}. [P0 致命断裂] {item}")
    else:
        lines.append("✅ **绿灯：未发现 P0 级致命断裂违规。**")

    lines.extend([
        "",
        "---",
        "",
        "## 📦 二、资源账本与出装审计 (Agent A)",
    ])

    if p1_list:
        for i, item in enumerate(p1_list, 1):
            lines.append(f"### {i}. [P1 严重失误] {item}")
            lines.append(f"* **位置**：第 {curr_chapter.index} 章")
            lines.append("* **💡 短句修复双方案**：")
            lines.append("  * **【方案 1：前置补源】**：在前章末尾补充获得经过。")
            lines.append("  * **【方案 2：就地修正】**：改为使用已持有道具或替换描写。")
    else:
        lines.append("✅ **账本状态健康：无凭空出装或资产冲突。**")

    lines.extend([
        "",
        "---",
        "",
        "## ⛓️ 三、上下文一致性与跨章衔接审计 (Agent B)",
        f"* **跨章承接**：{'有上一章承接' if boundary_ctx.has_prev_chapter else '本章为首章，无前序衔接'}",
        f"* **POV 转场判定**：{'检测到视角/时空漂移 (' + str(boundary_ctx.transition_clue) + ')' if boundary_ctx.is_pov_transition else '视角平稳继承'}",
    ])
    if boundary_ctx.isolation_zones:
        lines.append(f"* **叙事视界隔离区**：发现 {len(boundary_ctx.isolation_zones)} 处闪回/幻境")
    if gap_warnings:
        lines.append("* **⚠️ 序号连续性警告**：")
        for gw in gap_warnings:
            lines.append(f"  * {gw}")

    target_genre = genre_profile.primary_genre if genre_profile else "通用网文"
    poison_tip = genre_profile.red_lines[0] if (genre_profile and genre_profile.red_lines) else "无恶性毒点"

    lines.extend([
        "",
        "---",
        "",
        "## 🥊 四、第一性原理与对抗式审查 (Agent D)",
        f"* **题材卡尺对齐**：当前章节严格遵循【{target_genre}】第一性原理驱动。",
        f"* **驱动力评估**：主线推进平稳，核心目标清晰，有效完成本章情绪位移。",
        f"* **读者自嗨盲区诊断**：未见明显恶性毒点（重点防范：{poison_tip}）。",
        "",
        "---",
        "",
        "## 📝 五、短句排版与阅读节奏审计 (Agent C)",
    ])

    if findings:
        for i, f in enumerate(findings, 1):
            lines.append(f"### {i}. [{f.severity} {f.flaw_type}] 行号: {f.line_number}")
            lines.append(f"* **片段**：`{f.snippet}`")
            lines.append(f"* **问题**：{f.message}")
            lines.append(f"* **建议**：{f.suggestion}")
    else:
        lines.append("✅ **排版规范良好：无臃肿大黑段或拖沓长句。**")

    lines.extend([
        "",
        "---",
        "",
        "## 📊 六、账本流水与快照变动预览",
        "```diff",
        f"+ 当前热资产数量: {len(state.assets)}",
        f"+ 伏笔池标记数量: {len(state.foreshadowing_stash)}",
        "```",
        "",
    ])

    return "\n".join(lines)


def locate_ledger_paths(project_dir: Path) -> Tuple[Path, Path]:
    """寻找项目中的资源账本 JSON 与 MD 路径。
    优先检查 设定/资源账本.json 与根目录下 资源账本.json 真实文件是否存在。
    """
    settings_dir = project_dir / "设定"
    settings_json = settings_dir / "资源账本.json"
    root_json = project_dir / "资源账本.json"

    if settings_json.is_file():
        return settings_json, settings_dir / "资源账本.md"
    if root_json.is_file():
        return root_json, project_dir / "资源账本.md"

    if settings_dir.is_dir():
        return settings_json, settings_dir / "资源账本.md"
    return root_json, project_dir / "资源账本.md"


def load_ledger_state(json_path: Path) -> LedgerState:
    """加载账本状态，若不存在则初始化空状态"""
    if json_path.is_file():
        try:
            content, _, _ = read_file_safe(json_path)
            data = json.loads(content)
            return LedgerState.from_dict(data)
        except Exception:
            return LedgerState()
    return LedgerState()


def run_audit(
    project_dir: Path,
    target_chapter_index: Optional[float] = None,
    strict: bool = False,
    force: bool = False,
    write_latest_report: bool = True,
    silent: bool = False,
    summary_collector: Optional[Dict[str, Any]] = None,
    genre: str = "auto",
    mode: str = "auto",
    platform: str = "generic",
    use_author_memory: bool = False,
    inherited_items: Optional[Dict[str, Any]] = None,
) -> int:
    """执行单章审查管线，生成预审包与归档报告，返回退出码"""
    reports_dir = project_dir / "reports"
    cache_dir = reports_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 0. 运行时探测与模式降级
    effective_mode, fallback_reason = resolve_execution_mode(mode)

    # 1. 发现章节
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        if not silent:
            print(f"[错误] 在目录 {project_dir} 中未发现任何小说章节文件！", file=sys.stderr)
        return 3

    # 2. 定位目标章节
    curr_chapter: Optional[ChapterItem] = None
    if target_chapter_index is not None:
        for c in chapters:
            if abs(c.index - target_chapter_index) < 1e-4:
                curr_chapter = c
                break
        if not curr_chapter:
            if not silent:
                print(f"[错误] 未找到指定章号: {target_chapter_index}", file=sys.stderr)
            return 3
    else:
        # 默认定位最新章节
        curr_chapter = chapters[-1]

    # 3. 定位上一章节
    curr_pos = chapters.index(curr_chapter)
    prev_chapter: Optional[ChapterItem] = chapters[curr_pos - 1] if curr_pos > 0 else None

    # 4. 安全读取文件
    try:
        curr_text, curr_enc, curr_eol = read_file_safe(curr_chapter.path)
    except Exception as e:
        if not silent:
            print(f"[错误] 读取目标章节失败: {curr_chapter.path}, {e}", file=sys.stderr)
        return 3

    prev_text: Optional[str] = None
    if prev_chapter:
        try:
            prev_text, _, _ = read_file_safe(prev_chapter.path)
        except Exception as e:
            if not silent:
                print(f"[警告] 读取上一章节失败: {prev_chapter.path}, {e}", file=sys.stderr)
            prev_text = None

    # 5. 跨章缝合与 POV/闪回隔离
    boundary_ctx = extract_boundary_slices(prev_text, curr_text)

    # 6. 序号体检
    gap_warnings = resolver.diagnose_sequence_gaps(chapters)

    # 5.5 题材自动探测与画像构建
    genre_profile = detect_genre(curr_text, specified_genre=genre)

    # 5.6 平台商业门禁质量卡尺评估
    platform_data = evaluate_platform_rubric(
        curr_text,
        platform=platform,
        chapter_index=curr_chapter.index,
        genre=genre_profile.primary_genre,
    )

    # 5.7 作者偏好记忆联动（受 2048 字节硬上限与铁律约束保护）
    author_mem_text: Optional[str] = None
    if use_author_memory:
        try:
            mem = AuthorMemory(project_dir)
            author_mem_text = mem.query()
        except Exception:
            author_mem_text = None

    # 7. 排版与 AI 模式扫描（动态注入题材白名单规则与深度 AI 句式检测）
    findings = scan_typography_flaws(curr_text, genre=genre_profile.primary_genre)

    # 8. 账本与防脏写检查
    json_path, md_path = locate_ledger_paths(project_dir)
    if md_path.is_file() and json_path.is_file() and not force:
        if check_dirty_state(md_path, json_path):
            if not silent:
                print(
                    f"[防脏写拦截] Markdown 账本 ({md_path}) 修改时间晚于 JSON 数据源 ({json_path})！\n"
                    f"存在未同步的手工编辑。请先调用 sync_ledger_from_md() 同步，或传入 force=True 强制覆盖。",
                    file=sys.stderr,
                )
            return 3

    state = load_ledger_state(json_path)

    # 提取正文伏笔标签更新账本伏笔池
    new_tags = scan_foreshadowing_tags(curr_text)
    has_new_tags = False
    if new_tags:
        for tag in new_tags:
            if tag not in state.foreshadowing_stash:
                state.foreshadowing_stash.append(tag)
                has_new_tags = True
        # 若发现新伏笔标签，立即持久化更新账本数据源 (P1-05)
        if has_new_tags and json_path.exists():
            try:
                save_ledger_state(state, json_path, md_path, force=True)
            except Exception as e:
                if not silent:
                    print(f"[警告] 自动持久化新伏笔至账本失败: {e}", file=sys.stderr)

    # 8.5 汇集平台卡尺违规项
    p_findings = platform_data.get("findings", [])
    for pf in p_findings:
        if pf.severity in ("P2", "P3"):
            findings.append(FormatFinding(
                line_number=1,
                flaw_type=f"PLATFORM_{platform.upper()}",
                severity=pf.severity,
                snippet=pf.evidence,
                message=pf.issue,
                suggestion=pf.fix,
                category="platform",
                location=pf.location,
                evidence=pf.evidence,
                issue=pf.issue,
                fix=pf.fix,
            ))

    # 9. 构建预审包
    bundle = build_pre_audit_bundle(
        project_dir=project_dir,
        curr_chapter=curr_chapter,
        prev_chapter=prev_chapter,
        chapters=chapters,
        state=state,
        findings=findings,
        boundary_ctx=boundary_ctx,
        curr_enc=curr_enc,
        curr_eol=curr_eol,
        gap_warnings=gap_warnings,
        genre_profile=genre_profile,
        requested_mode=mode,
        effective_mode=effective_mode,
        fallback_reason=fallback_reason,
        platform=platform,
        platform_data=platform_data,
        author_memory_text=author_mem_text,
        inherited_items=inherited_items,
    )
    bundle_path = cache_dir / "pre_audit_bundle.json"
    write_file_safe(bundle_path, json.dumps(bundle, ensure_ascii=False, indent=2))

    # 10. 违规与严重度统计
    p0_list: List[str] = []
    p1_list: List[str] = []

    detected_violations = detect_violations_in_text(curr_text)
    for v in detected_violations:
        if v["level"] == "P0":
            p0_list.append(v["message"])
        elif v["level"] == "P1":
            p1_list.append(v["message"])

    # 平台红线与严重门禁拦截
    for pf in p_findings:
        if pf.severity == "P0":
            p0_list.append(pf.issue)
        elif pf.severity == "P1":
            p1_list.append(pf.issue)

    # 11. 生成与归档审查报告
    report_content = render_audit_report(
        curr_chapter=curr_chapter,
        prev_chapter=prev_chapter,
        p0_list=p0_list,
        p1_list=p1_list,
        findings=findings,
        boundary_ctx=boundary_ctx,
        state=state,
        gap_warnings=gap_warnings,
        genre_profile=genre_profile,
        requested_mode=mode,
        effective_mode=effective_mode,
        fallback_reason=fallback_reason,
        platform=platform,
        author_memory_text=author_mem_text,
        platform_data=platform_data,
        inherited_items=inherited_items,
    )

    latest_report_path = reports_dir / "LATEST_REPORT.md"
    if write_latest_report:
        write_file_safe(latest_report_path, report_content)
        # 单章审查状态机同步 (P1-04)
        audit_state = load_audit_state(reports_dir)
        c_idx = curr_chapter.index
        if c_idx not in audit_state.completed_chapters:
            audit_state.completed_chapters.append(c_idx)
            audit_state.completed_chapters.sort()
        audit_state.last_scope = f"{c_idx:g}"
        audit_state.open_defects = [
            d for d in audit_state.open_defects
            if abs(float(d.get("chapter", -1)) - c_idx) > 1e-4
        ]
        for p0_item in p0_list:
            audit_state.open_defects.append({
                "chapter": c_idx,
                "severity": "P0",
                "category": "causal",
                "issue": p0_item,
                "fix": "严格依据账本与主线事实对齐，杜绝主观文学发挥",
            })
        for p1_item in p1_list:
            audit_state.open_defects.append({
                "chapter": c_idx,
                "severity": "P1",
                "category": "causal",
                "issue": p1_item,
                "fix": "严格依据账本与主线事实对齐，杜绝主观文学发挥",
            })
        save_audit_state(audit_state, reports_dir)

    archived_report_path = get_report_archive_path(reports_dir, curr_chapter.index)
    archived_report_path.parent.mkdir(parents=True, exist_ok=True)
    write_file_safe(archived_report_path, report_content)

    if not silent:
        print(f"=== story-audit 深度审查报告 ===")
        print(f"Requested Mode: {mode}")
        print(f"Effective Mode: {effective_mode}")
        print(f"Fallback: {fallback_reason}")
        print(f"Platform Rubric: {platform}")
        print(f"Genre: {genre_profile.primary_genre}")
        print(f"Scope: 第{curr_chapter.index:03g}章")
        print(f"----------------------------------------------------------------------------------------")
        print(f"审查完成：第 {curr_chapter.index} 章 ({curr_chapter.title}) [题材: {genre_profile.primary_genre} | 置信度: {genre_profile.confidence:.0%}]")
        if write_latest_report:
            print(f"最新报告已写入：{latest_report_path}")
        print(f"归档报告已写入：{archived_report_path}")

    # 12. 退出码映射
    exit_code = 0
    if p0_list:
        if not silent:
            print(f"[红灯阻断] 发现 {len(p0_list)} 个 P0 级致命断裂，流程中断！", file=sys.stderr)
        exit_code = 2
    elif p1_list:
        if strict:
            if not silent:
                print(f"[黄灯严格阻断] 发现 {len(p1_list)} 个 P1 级严重失误，strict=True 严格模式生效！", file=sys.stderr)
            exit_code = 1
        else:
            if not silent:
                print(f"[黄灯放行] 发现 {len(p1_list)} 个 P1 级严重失误（strict=False 宽松模式，允许通过）。")
            exit_code = 0
    else:
        if not silent:
            print("[绿灯通过] 未发现严重违规。")
        exit_code = 0

    if summary_collector is not None:
        p2_flaws = [f for f in findings if f.severity == "P2"]
        p3_flaws = [f for f in findings if f.severity == "P3"]
        word_count = len(re.findall(r'[一-龥\w]', curr_text))
        para_count = len([line.strip() for line in curr_text.splitlines() if line.strip()])
        status_str = "P0 阻断" if p0_list else ("P1 警告" if p1_list else "合格")
        summary_collector.update({
            "chapter_index": curr_chapter.index,
            "chapter_title": curr_chapter.title,
            "genre_profile": genre_profile,
            "word_count": word_count,
            "paragraph_count": para_count,
            "p0_list": list(p0_list),
            "p1_list": list(p1_list),
            "p2_count": len(p2_flaws),
            "p3_count": len(p3_flaws),
            "findings": list(findings),
            "boundary_ctx": boundary_ctx,
            "exit_code": exit_code,
            "status": status_str,
            "archived_report_path": archived_report_path,
        })

    return exit_code


def run_sync_from_md(project_dir: Path) -> int:
    """执行 sync_ledger_from_md 反向同步管线"""
    json_path, md_path = locate_ledger_paths(project_dir)
    if not md_path.is_file():
        print(f"[错误] 未找到 Markdown 账本文件: {md_path}", file=sys.stderr)
        return 3

    try:
        new_state = sync_from_markdown(md_path, json_path)
        print(f"成功将 Markdown 账本增量同步至 JSON 数据源: {json_path}")
        print(f"当前总资产数: {len(new_state.assets)}")
        return 0
    except Exception as e:
        print(f"[错误] 反向同步失败: {e}", file=sys.stderr)
        return 3


def run_checkpoint(project_dir: Path, volume: Optional[int], force: bool = False) -> int:
    """执行 checkpoint_volume 分卷封账结转管线"""
    if volume is None:
        print("[错误] checkpoint 结转操作必须指定 volume 卷号！", file=sys.stderr)
        return 3

    json_path, _ = locate_ledger_paths(project_dir)
    if not json_path.is_file():
        print(f"[错误] 未找到账本数据源: {json_path}", file=sys.stderr)
        return 3

    try:
        state = load_ledger_state(json_path)
        archive_dir = project_dir / "设定" / "archive"
        checkpoint_file = create_volume_checkpoint(volume, state, archive_dir)

        # 写入阶段封账报告
        reports_stage_dir = project_dir / "reports" / "阶段封账与里程碑"
        reports_stage_dir.mkdir(parents=True, exist_ok=True)
        vol_str = str(volume).zfill(2)
        stage_report = (
            f"# 第{volume}卷_期末结账与全卷连续性审计报告\n\n"
            f"> 结账时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"> 归档快照：{checkpoint_file.name}\n"
            f"> 结转资产总计：{len(state.assets)} 项\n"
        )
        report_file = reports_stage_dir / f"第{vol_str}卷_期末结账与全卷连续性审计报告.md"
        write_file_safe(report_file, stage_report)

        print(f"分卷封账完成！第 {volume} 卷快照已归档至: {checkpoint_file}")
        return 0
    except Exception as e:
        print(f"[错误] 分卷封账失败: {e}", file=sys.stderr)
        return 3


def run_init_mode(project_dir: Path, scope_str: Optional[str] = None, force: bool = False, genre: str = "auto", silent: bool = False) -> Tuple[int, Path]:
    """执行 init_ledger 首次建账管线，集成启发式资产与伏笔抽取"""
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        print(f"[错误] 未发现任何章节文件，无法建账！", file=sys.stderr)
        return 3, Path("")

    if scope_str:
        try:
            s_min, s_max = parse_scope_range(scope_str)
            target_chapters = [c for c in chapters if s_min <= c.index <= s_max]
        except Exception as e:
            print(f"[错误] 解析范围失败: {e}", file=sys.stderr)
            return 3, Path("")
    else:
        target_chapters = chapters

    if not target_chapters:
        print(f"[错误] 范围内未发现章节！", file=sys.stderr)
        return 3, Path("")

    json_path, md_path = locate_ledger_paths(project_dir)
    if md_path.is_file() and json_path.is_file() and not force:
        if check_dirty_state(md_path, json_path):
            print(f"[防脏写拦截] 账本存在未同步手工编辑，建账被拒绝！", file=sys.stderr)
            return 3, Path("")

    # 优先尝试 load_ledger_state(json_path)，继承既有 assets，仅在账本不存在时初始化新对象
    if json_path.is_file():
        state = load_ledger_state(json_path)
    else:
        state = LedgerState()

    all_tags: List[Dict[str, str]] = list(state.foreshadowing_stash) if state.foreshadowing_stash else []
    existing_asset_names: Dict[str, AssetItem] = {item.name: item for item in state.assets.values()}

    total_extracted_assets = 0
    for chap in target_chapters:
        try:
            txt, _, _ = read_file_safe(chap.path)
            # 1. 扫描伏笔标签
            tags = scan_foreshadowing_tags(txt)
            for t in tags:
                if t not in all_tags:
                    all_tags.append(t)

            # 2. 启发式抽取自然网文出装物资与装备
            extracted_assets = extract_heuristic_assets(txt, chap.index, genre=genre)
            for ast in extracted_assets:
                nm = ast["name"]
                if nm in existing_asset_names:
                    # 去重并保留最早来源章节
                    exist_item = existing_asset_names[nm]
                    if chap.index < exist_item.origin_chapter:
                        exist_item.origin_chapter = chap.index
                else:
                    item = AssetItem.from_dict(ast)
                    if item.id in state.assets:
                        item.id = f"{item.id}_{len(state.assets)+1}"
                    state.assets[item.id] = item
                    existing_asset_names[nm] = item
                    total_extracted_assets += 1
        except Exception:
            pass

    state.foreshadowing_stash = all_tags
    state.last_updated_chapter = target_chapters[-1].index

    try:
        save_ledger_state(state, json_path, md_path, force=force)
    except LedgerDirtyError as e:
        print(f"[错误] 保存账本遇到防脏写拦截: {e}", file=sys.stderr)
        return 3

    # 生成建账盘点报告
    reports_stage_dir = project_dir / "reports" / "阶段封账与里程碑"
    reports_stage_dir.mkdir(parents=True, exist_ok=True)
    start_idx = int(target_chapters[0].index)
    end_idx = int(target_chapters[-1].index)
    s_fmt = str(start_idx).zfill(3)
    e_fmt = str(end_idx).zfill(3)
    report_file = reports_stage_dir / f"初始建账盘点报告_第{s_fmt}-{e_fmt}章.md"

    category_counts: Dict[str, int] = {}
    for it in state.assets.values():
        category_counts[it.category] = category_counts.get(it.category, 0) + 1
    cat_summary = ", ".join(f"{k}: {v} 项" for k, v in sorted(category_counts.items())) if category_counts else "无"

    content = (
        f"# 初始建账盘点报告 (第{s_fmt}-{e_fmt}章)\n\n"
        f"> 建账时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"> 扫描章节数：{len(target_chapters)}\n"
        f"> 候选资产总数：{len(state.assets)} 项（{cat_summary}）\n"
        f"> 提取伏笔标记数：{len(all_tags)}\n\n"
        f"## 核心资产清册预览\n\n"
        f"| 资产名称 | 类别 | 数量 | 单位 | 获取章节 |\n"
        f"| :--- | :--- | :--- | :--- | :--- |\n"
    )
    for it in sorted(state.assets.values(), key=lambda x: (x.origin_chapter, x.name)):
        content += f"| {it.name} | {it.category} | {it.quantity} | {it.unit} | 第{it.origin_chapter:g}章 |\n"

    write_file_safe(report_file, content)

    if not silent:
        print(f"首次建账完成！已过账 {len(target_chapters)} 章，提取候选资产 {len(state.assets)} 项，生成双轨账本与盘点报告。")
    return 0, report_file


def render_scope_batch_summary(
    scope_str: str,
    s_min: float,
    s_max: float,
    chapter_summaries: List[Dict[str, Any]],
    strict: bool,
    requested_mode: str = "auto",
    effective_mode: str = "full",
    fallback_reason: str = "none",
    platform: str = "generic",
    inherited_items: Optional[Dict[str, Any]] = None,
) -> str:
    """渲染批量审查聚合大盘报告 (Markdown)"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_chaps = len(chapter_summaries)
    total_words = sum(c["word_count"] for c in chapter_summaries)
    total_paras = sum(c["paragraph_count"] for c in chapter_summaries)
    avg_words = int(total_words / max(n_chaps, 1))
    avg_paras = int(total_paras / max(n_chaps, 1))

    total_p0 = sum(len(c["p0_list"]) for c in chapter_summaries)
    total_p1 = sum(len(c["p1_list"]) for c in chapter_summaries)
    total_p2 = sum(c["p2_count"] for c in chapter_summaries)
    total_p3 = sum(c["p3_count"] for c in chapter_summaries)

    if total_p0 > 0:
        overall_status = "🔴 P0 致命断裂阻断"
    elif total_p1 > 0:
        overall_status = "🟡 P1 严重失误警告 (严格模式中断)" if strict else "🟡 P1 严重失误警告 (放行)"
    else:
        overall_status = "🟢 绿灯合格通过"

    primary_genre = chapter_summaries[0]["genre_profile"].primary_genre if chapter_summaries and chapter_summaries[0].get("genre_profile") else "通用网文"
    lines: List[str] = [
        "=== story-audit 深度审查报告 ===",
        f"Requested Mode: {requested_mode}",
        f"Effective Mode: {effective_mode}",
        f"Fallback: {fallback_reason}",
        f"Platform Rubric: {platform}",
        f"Genre: {primary_genre}",
        f"Scope: {scope_str}",
        "",
        f"# 批量连审大盘汇总报告 (范围: {scope_str})",
        "",
        f"> 生成时间：{today}  ",
        f"> 审查范围：第 {s_min:03g} 章 至 第 {s_max:03g} 章  ",
        f"> 覆盖章节：共 {n_chaps} 章  ",
        f"> 综合判定：{overall_status}  ",
        "",
        "---",
        "",
        "## 一、全范围总览大盘",
        "",
        "| 指标项 | 统计数值 | 评估说明 |",
        "| :--- | :--- | :--- |",
        f"| 覆盖章节总数 | {n_chaps} 章 | 设定审查连续范围 |",
        f"| 全篇总字数 | {total_words:,} 字 | 平均单章 {avg_words:,} 字 |",
        f"| 全篇总段数 | {total_paras:,} 段 | 平均单章 {avg_paras} 段 |",
        f"| P0 致命断裂 | {total_p0} 处 | 包含死亡复活、降智崩坏等红灯项 |",
        f"| P1 严重失误 | {total_p1} 处 | 包含未记录战力、凭空出装等黄灯项 |",
        f"| P2 排版长句/长段 | {total_p2} 处 | 单句逗号过多或单段超 120 字 |",
        f"| P3 翻译腔/描写混杂 | {total_p3} 处 | AI 连词或对话后堆砌长动作 |",
        "",
        "---",
        "",
    ]

    if inherited_items and (inherited_items.get("open_defects") or inherited_items.get("foreshadowing_commitments")):
        lines.append(render_inherited_items_section(inherited_items))
        lines.extend(["", "---", ""])

    lines.extend([
        "## 二、字数与段数统计走势",
        "",
        "| 章号 | 章节名称 | 总字数 | 自然段数 | 平均段长 | 走势评估 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for c in chapter_summaries:
        wc = c["word_count"]
        pc = c["paragraph_count"]
        avg_plen = round(wc / max(pc, 1), 1)
        assess_parts = []
        if wc < 2000:
            assess_parts.append("篇幅偏薄(<2000字)")
        elif wc > 4500:
            assess_parts.append("长篇饱满(>4500字)")
        else:
            assess_parts.append("标准篇幅")

        if avg_plen > 70:
            assess_parts.append("段落偏密")
        elif avg_plen < 35:
            assess_parts.append("短句快节奏")
        else:
            assess_parts.append("节奏平稳")

        lines.append(f"| 第{c['chapter_index']:03g}章 | {c['chapter_title']} | {wc:,} | {pc} | {avg_plen} 字/段 | {'；'.join(assess_parts)} |")

    lines.extend([
        "",
        "---",
        "",
        "## 三、各章 P0/P1/P2/P3 瑕疵汇总列表",
        "",
        "| 章号 | 章节名称 | P0 阻断 | P1 警告 | P2 拖沓长句/长段 | P3 连词/台词混杂 | 单章判定 | 归档报告链接 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for c in chapter_summaries:
        p0_c = len(c["p0_list"])
        p1_c = len(c["p1_list"])
        p2_c = c["p2_count"]
        p3_c = c["p3_count"]
        status = c["status"]
        rep_rel = c["archived_report_path"].as_posix()
        lines.append(f"| 第{c['chapter_index']:03g}章 | {c['chapter_title']} | {p0_c} | {p1_c} | {p2_c} | {p3_c} | {status} | [查看归档]({rep_rel}) |")

    lines.extend(["", "### 重点瑕疵条目清单", ""])
    has_any_flaw = False
    for c in chapter_summaries:
        flaws: List[FormatFinding] = c.get("findings", [])
        p0_l = c.get("p0_list", [])
        p1_l = c.get("p1_list", [])
        if p0_l or p1_l or flaws:
            has_any_flaw = True
            lines.append(f"#### 第 {c['chapter_index']:03g} 章 《{c['chapter_title']}》")
            for p0_msg in p0_l:
                lines.append(f"- **[P0 阻断]** {p0_msg}")
            for p1_msg in p1_l:
                lines.append(f"- **[P1 警告]** {p1_msg}")
            for f in flaws:
                lines.append(f"- **[{f.severity} {f.flaw_type}]** 行号 {f.line_number}: {f.message} (片段: `{f.snippet}`)")
            lines.append("")

    if not has_any_flaw:
        lines.append("（全范围章节未发现任何严重违规或排版缺陷，全绿灯通过！）\n")

    lines.extend([
        "---",
        "",
        "## 四、跨章接缝与 POV 视点一览表",
        "",
        "| 章号 | 章节名称 | POV 视点/开篇叙事 | 接缝转场线索 | 承接前章状态 | 接缝质量评估 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for c in chapter_summaries:
        b_ctx: BoundaryContext = c["boundary_ctx"]
        idx_str = f"第{c['chapter_index']:03g}章"
        if not b_ctx.has_prev_chapter:
            pov_info = "首章开篇"
            clue = "-"
            prev_status = "首章无前置上下文"
            seam_rating = "🟢 初始开篇"
        else:
            if b_ctx.is_pov_transition:
                pov_info = "多线 POV 转场"
                clue = b_ctx.transition_clue or "视角切换"
                seam_rating = "🔵 视点切换"
            elif b_ctx.isolation_zones:
                pov_info = "叙事时空切片"
                clue = "含回忆/闪回"
                seam_rating = "🟣 时空隔离"
            else:
                pov_info = "主角主视点顺承"
                clue = "-"
                seam_rating = "🟢 无缝顺承"

            prev_status = "紧密相承" if b_ctx.has_prev_chapter else "-"

        lines.append(f"| {idx_str} | {c['chapter_title']} | {pov_info} | {clue} | {prev_status} | {seam_rating} |")

    lines.append("")
    return "\n".join(lines)


def run_scope_audit(
    project_dir: Path,
    scope_str: str,
    strict: bool,
    force: bool,
    genre: str = "auto",
    mode: str = "auto",
    platform: str = "generic",
    use_author_memory: bool = False,
    silent: bool = False,
) -> int:
    """执行批量连审模式，生成大盘汇总报告与紧凑看板输出"""
    try:
        s_min, s_max = parse_scope_range(scope_str)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 3

    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    target_chapters = [c for c in chapters if s_min <= c.index <= s_max]
    if not target_chapters:
        print(f"[错误] 范围 {scope_str} 内未找到任何章节！", file=sys.stderr)
        return 3

    n_total = len(target_chapters)
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 运行时探测与跨批状态机继承
    effective_mode, fallback_reason = resolve_execution_mode(mode)
    audit_state = load_audit_state(reports_dir)
    inherited_items = get_inherited_items(audit_state)

    if not silent:
        print(f"=== story-audit 深度审查报告 ===")
    print(f"Requested Mode: {mode}")
    print(f"Effective Mode: {effective_mode}")
    print(f"Fallback: {fallback_reason}")
    print(f"Platform Rubric: {platform}")
    print(f"Genre: {genre}")
    print(f"Scope: {scope_str}")
    print(f"========================================================================================")
    print(f"🚀 开始批量审查 [范围: {scope_str} | 共 {n_total} 章 | 模式: {effective_mode}]")
    if inherited_items.get("open_defects"):
        print(f"  [继承开放缺陷]: {len(inherited_items['open_defects'])} 项")
    if inherited_items.get("foreshadowing_commitments"):
        print(f"  [监控中伏笔池]: {len(inherited_items['foreshadowing_commitments'])} 个")
    print(f"========================================================================================")

    chapter_summaries: List[Dict[str, Any]] = []
    has_p0 = False
    has_p1 = False

    for idx, chap in enumerate(target_chapters, 1):
        summary: Dict[str, Any] = {}
        # 保持各章单章归档报告写入，但禁用单章覆盖 LATEST_REPORT.md 并静默单章冗余输出
        code = run_audit(
            project_dir,
            target_chapter_index=chap.index,
            strict=strict,
            force=force,
            write_latest_report=False,
            silent=True,
            summary_collector=summary,
            genre=genre,
            mode=mode,
            platform=platform,
            use_author_memory=use_author_memory,
            inherited_items=inherited_items,
        )
        chapter_summaries.append(summary)

        if code == 2:
            has_p0 = True
        elif code == 1:
            has_p1 = True

        status_tag = summary.get("status", "完成")
        if not silent:
            print(f"  [{idx:02d}/{n_total:02d}] 审查 第{chap.index:03g}章 《{chap.title}》 ... [{status_tag}]")

    # 打印终端紧凑汇总看板
    print(f"========================================================================================")
    print(f"📊 批量连审汇总看板 [范围: {scope_str} | 覆盖: {n_total} 章]")
    print(f"========================================================================================")
    print(f"{'章号':<8} | {'章节标题':<24} | {'字数':>6} | {'段数':>4} | {'P0':>2} | {'P1':>2} | {'P2':>2} | {'P3':>2} | {'状态':<6}")
    print(f"{'-'*8}-+-{'-'*24}-+-{'-'*6}-+-{'-'*4}-+-{'-'*2}-+-{'-'*2}-+-{'-'*2}-+-{'-'*2}-+-{'-'*8}")

    for s in chapter_summaries:
        raw_title = s['chapter_title']
        title_disp = raw_title[:22] + ".." if len(raw_title) > 22 else raw_title
        p0_num = len(s['p0_list'])
        p1_num = len(s['p1_list'])
        p2_num = s['p2_count']
        p3_num = s['p3_count']
        print(f"第{s['chapter_index']:03g}章  | {title_disp:<24} | {s['word_count']:>6,} | {s['paragraph_count']:>4} | {p0_num:>2} | {p1_num:>2} | {p2_num:>2} | {p3_num:>2} | {s['status']}")

    total_words = sum(s['word_count'] for s in chapter_summaries)
    total_paras = sum(s['paragraph_count'] for s in chapter_summaries)
    tot_p0 = sum(len(s['p0_list']) for s in chapter_summaries)
    tot_p1 = sum(len(s['p1_list']) for s in chapter_summaries)
    tot_p2 = sum(s['p2_count'] for s in chapter_summaries)
    tot_p3 = sum(s['p3_count'] for s in chapter_summaries)

    overall_label = "🔴 P0 阻断" if has_p0 else ("🟡 P1 警告" if has_p1 else "🟢 合格通过")
    print(f"========================================================================================")
    print(f"【全范围大盘】总章节: {n_total} 章 | 总字数: {total_words:,} 字 | 总段落: {total_paras:,} 段")
    print(f"【瑕疵汇总】P0 阻断: {tot_p0} | P1 警告: {tot_p1} | P2 拖沓长句/段: {tot_p2} | P3 翻译腔/混杂: {tot_p3}")
    print(f"【判定结论】{overall_label}")
    print(f"========================================================================================")

    # 生成聚合大盘报告 Markdown 内容
    batch_summary_content = render_scope_batch_summary(
        scope_str=scope_str,
        s_min=s_min,
        s_max=s_max,
        chapter_summaries=chapter_summaries,
        strict=strict,
        requested_mode=mode,
        effective_mode=effective_mode,
        fallback_reason=fallback_reason,
        platform=platform,
        inherited_items=inherited_items,
    )

    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 1. 输出指定命名大盘报告：reports/BATCH_SUMMARY_SCOPE_{scope}.md
    scope_clean = scope_str.replace(" ", "")
    scope_summary_path = reports_dir / f"BATCH_SUMMARY_SCOPE_{scope_clean}.md"
    write_file_safe(scope_summary_path, batch_summary_content)

    # 2. 输出历史归档批量报告：reports/批量审查/{today}_批量审查_第{s_fmt}-{e_fmt}章.md
    batch_dir = reports_dir / "批量审查"
    batch_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    s_fmt = str(int(s_min)).zfill(3)
    e_fmt = str(int(s_max)).zfill(3)
    batch_report_file = batch_dir / f"{today}_批量审查_第{s_fmt}-{e_fmt}章.md"
    write_file_safe(batch_report_file, batch_summary_content)

    # 3. 统一将最新报告更新为本次批量审查大盘报告
    latest_report_path = reports_dir / "LATEST_REPORT.md"
    write_file_safe(latest_report_path, batch_summary_content)

    # 原子更新跨批长篇因果状态机 (reports/.audit_state.json)
    audit_state.last_scope = scope_str
    for chap in target_chapters:
        if chap.index not in audit_state.completed_chapters:
            audit_state.completed_chapters.append(chap.index)
    audit_state.completed_chapters.sort()

    # 累积本批次发现的开放 P0/P1 缺陷 (P1-01: 先清理待审章节旧记录，防止重审无限堆叠)
    target_chapter_indices = {c.index for c in target_chapters}
    audit_state.open_defects = [
        d for d in audit_state.open_defects
        if float(d.get("chapter", -1)) not in target_chapter_indices
    ]
    seen_defect_keys = {(d.get("chapter"), d.get("severity"), d.get("issue")) for d in audit_state.open_defects}

    for s in chapter_summaries:
        c_idx = s.get("chapter_index", 0)
        for p0_item in s.get("p0_list", []):
            k = (c_idx, "P0", p0_item)
            if k not in seen_defect_keys:
                seen_defect_keys.add(k)
                audit_state.open_defects.append({
                    "chapter": c_idx,
                    "severity": "P0",
                    "category": "causal",
                    "issue": p0_item,
                    "fix": "严格依据账本与主线事实对齐，杜绝主观文学发挥",
                })
        for p1_item in s.get("p1_list", []):
            k = (c_idx, "P1", p1_item)
            if k not in seen_defect_keys:
                seen_defect_keys.add(k)
                audit_state.open_defects.append({
                    "chapter": c_idx,
                    "severity": "P1",
                    "category": "causal",
                    "issue": p1_item,
                    "fix": "严格依据账本与主线事实对齐，杜绝主观文学发挥",
                })

    save_audit_state(audit_state, reports_dir)

    print(f"✅ 批量审查大盘报告已生成：{scope_summary_path}")
    print(f"✅ 历史归档报告已写入：{batch_report_file}")
    print(f"✅ 最新审查总览已更新：{latest_report_path}")
    print(f"✅ 跨批因果状态机已原子更新：{get_audit_state_path(reports_dir)}")

    if has_p0:
        return 2
    if has_p1 and strict:
        return 1
    return 0


def run_apply_fix(
    project_dir: Path,
    chapter_idx: Optional[float],
    target_line: Optional[int],
    old_text: Optional[str],
    new_text: Optional[str],
    context_before: str,
    context_after: str,
    patch_file: Optional[str],
) -> int:
    """执行 apply_fix 方案采纳回写管线"""
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        print(f"[错误] 未发现章节文件！", file=sys.stderr)
        return 3

    target_chapter: Optional[ChapterItem] = None
    if chapter_idx is not None:
        for c in chapters:
            if abs(c.index - chapter_idx) < 1e-4:
                target_chapter = c
                break
    else:
        target_chapter = chapters[-1]

    if not target_chapter:
        print(f"[错误] 目标章节不存在: {chapter_idx}", file=sys.stderr)
        return 3

    # 构建 PatchSpec
    if patch_file:
        try:
            with open(patch_file, encoding="utf-8") as f:
                p_data = json.load(f)
            patch = PatchSpec(
                target_line=int(p_data["target_line"]),
                context_before=p_data.get("context_before", ""),
                old_text=p_data["old_text"],
                new_text=p_data["new_text"],
                context_after=p_data.get("context_after", ""),
            )
        except Exception as e:
            print(f"[错误] 读取补丁文件失败: {e}", file=sys.stderr)
            return 3
    else:
        if target_line is None or old_text is None or new_text is None:
            print(
                "[错误] apply_fix 必须提供完整补丁参数 (target_line, old_text, new_text) 或 patch_file！",
                file=sys.stderr,
            )
            return 3
        patch = PatchSpec(
            target_line=target_line,
            context_before=context_before or "",
            old_text=old_text,
            new_text=new_text,
            context_after=context_after or "",
        )

    backup_dir = project_dir / "reports" / ".bak"
    try:
        success = apply_patch_with_disambiguation(
            file_path=target_chapter.path,
            patch=patch,
            backup_dir=backup_dir,
        )
        if success:
            print(f"成功安全回写第 {target_chapter.index} 章，已生成原子备份。")
            return 0
        else:
            print(f"[错误] 回写未成功完成。", file=sys.stderr)
            return 3
    except (PatchAnchorNotFoundError, AmbiguousPatchError, SafeWriterError) as e:
        print(f"[安全回写拒绝] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[系统异常] 安全回写失败: {e}", file=sys.stderr)
        return 3


# ==============================================================================
# 纯模块化 Python API 导出层 (Pure Python API Layer)
# 架构铁律：不提供亦不涉及任何 CLI 命令行接口，全功能严格通过 Python API 交付。
# ==============================================================================

def audit_chapter(
    project_dir: Union[str, Path] = ".",
    chapter_index: Optional[float] = None,
    platform: str = "generic",
    genre: str = "auto",
    mode: str = "auto",
    strict: bool = False,
    force: bool = False,
    author_memory: bool = False,
    silent: bool = False,
) -> Tuple[int, Path]:
    """单章深度审查纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        chapter_index: 审查目标章号（浮点或整数，默认 None 表示最新章）
        platform: 目标发布平台卡尺 (fanqie/qidian/zhihu/generic，默认 generic)
        genre: 网文题材类型（默认 auto 自动探测）
        mode: 审查执行模式 (auto/full/lean/solo，默认 auto)
        strict: 严格模式（发现 P1 违规时返回状态码 1）
        force: 忽略脏写警告强制覆盖
        author_memory: 是否联动作者记忆状态机

    Returns:
        Tuple[int, Path]: (状态码, 报告路径)。成功或发现缺陷时返回具体归档报告路径，失败未生成报告时返回空路径 Path("")
    """
    p_dir = Path(project_dir).resolve()
    summary: Dict[str, Any] = {}
    exit_code = run_audit(
        project_dir=p_dir,
        target_chapter_index=chapter_index,
        strict=strict,
        force=force,
        write_latest_report=True,
        silent=silent,
        summary_collector=summary,
        genre=genre,
        mode=mode,
        platform=platform,
        use_author_memory=author_memory,
    )
    report_path = summary.get("archived_report_path")
    if report_path is None or not Path(report_path).exists():
        latest = p_dir / "reports" / "LATEST_REPORT.md"
        if latest.exists() and exit_code in (0, 1, 2):
            report_path = latest
        else:
            report_path = report_path or Path("")
    return exit_code, Path(report_path)


def audit_scope(
    project_dir: Union[str, Path] = ".",
    scope_str: str = "",
    platform: str = "generic",
    genre: str = "auto",
    mode: str = "auto",
    strict: bool = False,
    force: bool = False,
    author_memory: bool = False,
    silent: bool = False,
) -> Tuple[int, Path]:
    """批量多章连审纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        scope_str: 批量范围（如 "1-2"、"31-35"）
        platform: 目标发布平台卡尺 (fanqie/qidian/zhihu/generic，默认 generic)
        genre: 网文题材类型（默认 auto 自动探测）
        mode: 审查执行模式 (auto/full/lean/solo，默认 auto)
        strict: 严格模式（发现 P1 违规时返回状态码 1）
        force: 忽略脏写警告强制覆盖
        author_memory: 是否联动作者记忆状态机

    Returns:
        Tuple[int, Path]: (状态码, 大盘汇总报告路径)
    """
    p_dir = Path(project_dir).resolve()
    exit_code = run_scope_audit(
        project_dir=p_dir,
        scope_str=scope_str,
        strict=strict,
        force=force,
        genre=genre,
        mode=mode,
        platform=platform,
        use_author_memory=author_memory,
    )
    scope_clean = scope_str.replace(" ", "")
    scope_summary_path = p_dir / "reports" / f"BATCH_SUMMARY_SCOPE_{scope_clean}.md"
    if not scope_summary_path.exists():
        latest = p_dir / "reports" / "LATEST_REPORT.md"
        if latest.exists() and exit_code in (0, 1, 2):
            scope_summary_path = latest
        else:
            scope_summary_path = Path("")
    return exit_code, scope_summary_path


def init_ledger(
    project_dir: Union[str, Path] = ".",
    scope_str: Optional[str] = None,
    force: bool = False,
    genre: str = "auto",
    silent: bool = False,
) -> Tuple[int, Path]:
    """首次全书/分卷建账纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        scope_str: 扫描章节范围（如 "1-30"，可选）
        force: 忽略脏写拦截强制覆盖
        genre: 网文题材类型（默认 auto 自动探测）
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 建账盘点报告路径)
    """
    p_dir = Path(project_dir).resolve()
    return run_init_mode(
        project_dir=p_dir,
        scope_str=scope_str,
        force=force,
        genre=genre,
        silent=silent,
    )


def checkpoint_volume(
    project_dir: Union[str, Path] = ".",
    volume: Optional[int] = None,
    force: bool = False,
    silent: bool = False,
) -> int:
    """分卷封账结转纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        volume: 卷号（整数，必填）
        force: 强制标志（保留兼容）

    Returns:
        int: 状态码 (0 成功, 3 失败)
    """
    p_dir = Path(project_dir).resolve()
    return run_checkpoint(project_dir=p_dir, volume=volume, force=force)


def sync_ledger_from_md(
    project_dir: Union[str, Path] = ".",
    force: bool = False,
    silent: bool = False,
) -> int:
    """从 Markdown 账本反向同步增量回 JSON 纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        force: 强制标志（保留兼容）

    Returns:
        int: 状态码 (0 成功, 3 失败)
    """
    p_dir = Path(project_dir).resolve()
    return run_sync_from_md(project_dir=p_dir)


def apply_fix(
    project_dir: Union[str, Path] = ".",
    chapter_index: Optional[float] = None,
    patch: Optional[Union[PatchSpec, Dict[str, Any]]] = None,
    patch_file: Optional[Union[str, Path]] = None,
    target_line: Optional[int] = None,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    context_before: str = "",
    context_after: str = "",
    silent: bool = False,
) -> int:
    """采纳修复方案并安全回写正文纯 Python API

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        chapter_index: 目标章节号
        patch: PatchSpec 实例或 dict 补丁对象
        patch_file: 补丁 JSON 文件路径
        target_line: 目标行号
        old_text: 待替换旧句
        new_text: 替换后新句
        context_before: 前一句上下文锚点
        context_after: 后一句上下文锚点

    Returns:
        int: 状态码 (0 成功, 3 失败)
    """
    p_dir = Path(project_dir).resolve()
    if patch is not None:
        if isinstance(patch, PatchSpec):
            target_line = patch.target_line
            old_text = patch.old_text
            new_text = patch.new_text
            context_before = patch.context_before
            context_after = patch.context_after
        elif isinstance(patch, dict):
            target_line = int(patch.get("target_line", 0))
            old_text = str(patch.get("old_text", ""))
            new_text = str(patch.get("new_text", ""))
            context_before = str(patch.get("context_before", ""))
            context_after = str(patch.get("context_after", ""))

    p_file_str = str(patch_file) if patch_file is not None else None
    return run_apply_fix(
        project_dir=p_dir,
        chapter_idx=chapter_index,
        target_line=target_line,
        old_text=old_text,
        new_text=new_text,
        context_before=context_before,
        context_after=context_after,
        patch_file=p_file_str,
    )


__all__ = [
    # 核心纯 Python API
    "audit_chapter",
    "audit_scope",
    "init_ledger",
    "checkpoint_volume",
    "sync_ledger_from_md",
    "apply_fix",
    # 底层执行管线与别名兼容
    "run_audit",
    "run_scope_audit",
    "run_checkpoint",
    "run_sync_from_md",
    "run_init_mode",
    "run_apply_fix",
    # 预审包与报告生成
    "build_pre_audit_bundle",
    "render_audit_report",
    "render_scope_batch_summary",
    "locate_ledger_paths",
    "load_ledger_state",
    "get_report_archive_path",
    "parse_scope_range",
    "detect_violations_in_text",
]
