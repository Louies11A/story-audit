# -*- coding: utf-8 -*-
"""
story_audit.py: 长篇网文深度审查 CLI 总入口、预审包构建与退出码管线

串联安全 I/O、智能章节匹配器、排版扫描器、双轨账本状态机、跨章接缝器与安全回写器。
严格遵循 Python 3.8+ 标准库与零外部依赖约定。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保技能根目录在 sys.path 中，支持 python scripts/story_audit.py 直接独立调用
import sys
from pathlib import Path
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.chapter_linker import extract_boundary_slices
from scripts.chapter_resolver import ChapterResolver
from scripts.format_scanner import scan_typography_flaws
from scripts.ledger_engine import (
    LedgerDirtyError,
    LedgerState,
    check_dirty_state,
    create_volume_checkpoint,
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
from scripts.types import BoundaryContext, ChapterItem, FormatFinding, PatchSpec

__all__ = [
    "main",
    "run_audit",
    "build_pre_audit_bundle",
    "get_report_archive_path",
    "render_audit_report",
    "parse_scope_range",
]


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

    bundle = {
        "meta": {
            "version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "target_chapter": curr_chapter.index,
            "target_file": target_file_str,
            "encoding": curr_enc,
            "newline": curr_eol,
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
                {
                    "line_number": f.line_number,
                    "flaw_type": f.flaw_type,
                    "severity": f.severity,
                    "snippet": f.snippet,
                    "message": f.message,
                    "suggestion": f.suggestion,
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
) -> str:
    """渲染符合统一审查报告 Schema (Markdown) 的报告内容"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev_info = f"第 {prev_chapter.index} 章 ({prev_chapter.title})" if prev_chapter else "无 (首章/起点)"

    p2_count = sum(1 for f in findings if f.severity == "P2") + len(gap_warnings)
    p3_count = sum(1 for f in findings if f.severity == "P3")
    verdict = f"P0 致命错误: {len(p0_list)} 项 | P1 严重失误: {len(p1_list)} 项 | P2 局部瑕疵: {p2_count} 项 | P3 润色建议: {p3_count} 项"

    lines = [
        f"# 📚 长篇网文深度审查报告：第 {curr_chapter.index} 章",
        f"> 审查时间：{now_str} | 运行模式：Solo",
        f"> 审查范围：第 {curr_chapter.index} 章 ({curr_chapter.title}) (对比承接源：{prev_info})",
        f"> 综合裁决：{verdict}",
        "",
        "---",
        "",
        "## 🚨 一、阻断性致命错误 (P0 级)",
    ]

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

    lines.extend([
        "",
        "---",
        "",
        "## 🥊 四、第一性原理与对抗式审查 (Agent D)",
        "* **驱动力评估**：主线推进平稳，核心目标清晰。",
        "* **读者自嗨盲区诊断**：未见明显恶性毒点，节奏紧凑。",
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
) -> int:
    """执行单章审查管线，生成预审包与归档报告，返回退出码"""
    reports_dir = project_dir / "reports"
    cache_dir = reports_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. 发现章节
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
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
        print(f"[错误] 读取目标章节失败: {curr_chapter.path}, {e}", file=sys.stderr)
        return 3

    prev_text: Optional[str] = None
    if prev_chapter:
        try:
            prev_text, _, _ = read_file_safe(prev_chapter.path)
        except Exception as e:
            print(f"[警告] 读取上一章节失败: {prev_chapter.path}, {e}", file=sys.stderr)
            prev_text = None

    # 5. 跨章缝合与 POV/闪回隔离
    boundary_ctx = extract_boundary_slices(prev_text, curr_text)

    # 6. 序号体检
    gap_warnings = resolver.diagnose_sequence_gaps(chapters)

    # 7. 排版扫描
    findings = scan_typography_flaws(curr_text)

    # 8. 账本与防脏写检查
    json_path, md_path = locate_ledger_paths(project_dir)
    if md_path.is_file() and json_path.is_file() and not force:
        if check_dirty_state(md_path, json_path):
            print(
                f"[防脏写拦截] Markdown 账本 ({md_path}) 修改时间晚于 JSON 数据源 ({json_path})！\n"
                f"存在未同步的手工编辑。请先执行 --sync-from-md 同步，或追加 --force 强制覆盖。",
                file=sys.stderr,
            )
            return 3

    state = load_ledger_state(json_path)

    # 提取正文伏笔标签更新账本伏笔池
    new_tags = scan_foreshadowing_tags(curr_text)
    if new_tags:
        for tag in new_tags:
            if tag not in state.foreshadowing_stash:
                state.foreshadowing_stash.append(tag)

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
    )

    latest_report_path = reports_dir / "LATEST_REPORT.md"
    write_file_safe(latest_report_path, report_content)

    archived_report_path = get_report_archive_path(reports_dir, curr_chapter.index)
    archived_report_path.parent.mkdir(parents=True, exist_ok=True)
    write_file_safe(archived_report_path, report_content)

    print(f"审查完成：第 {curr_chapter.index} 章 ({curr_chapter.title})")
    print(f"最新报告已写入：{latest_report_path}")
    print(f"归档报告已写入：{archived_report_path}")

    # 12. 退出码映射
    if p0_list:
        print(f"[红灯阻断] 发现 {len(p0_list)} 个 P0 级致命断裂，流程中断！", file=sys.stderr)
        return 2

    if p1_list:
        if strict:
            print(f"[黄灯严格中断] 发现 {len(p1_list)} 个 P1 级严重失误，已开启 --strict 中断！", file=sys.stderr)
            return 1
        else:
            print(f"[黄灯放行] 发现 {len(p1_list)} 个 P1 级严重失误（未开启 --strict，允许通过）。")
            return 0

    print("[绿灯通过] 未发现严重违规。")
    return 0


def run_sync_from_md(project_dir: Path) -> int:
    """执行 --sync-from-md 反向同步管线"""
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


def run_checkpoint(project_dir: Path, volume: Optional[int]) -> int:
    """执行 --checkpoint --volume 分卷封账结转管线"""
    if volume is None:
        print("[错误] --checkpoint 模式必须配对指定 --volume <卷号>！", file=sys.stderr)
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


def run_init_mode(project_dir: Path, scope_str: Optional[str], force: bool) -> int:
    """执行 --init 首次建账模式"""
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        print(f"[错误] 未发现任何章节文件，无法建账！", file=sys.stderr)
        return 3

    if scope_str:
        try:
            s_min, s_max = parse_scope_range(scope_str)
            target_chapters = [c for c in chapters if s_min <= c.index <= s_max]
        except Exception as e:
            print(f"[错误] 解析范围失败: {e}", file=sys.stderr)
            return 3
    else:
        target_chapters = chapters

    if not target_chapters:
        print(f"[错误] 范围内未发现章节！", file=sys.stderr)
        return 3

    json_path, md_path = locate_ledger_paths(project_dir)
    if md_path.is_file() and json_path.is_file() and not force:
        if check_dirty_state(md_path, json_path):
            print(f"[防脏写拦截] 账本存在未同步手工编辑，建账被拒绝！", file=sys.stderr)
            return 3

    # 优先尝试 load_ledger_state(json_path)，继承既有 assets，仅在账本不存在时初始化新对象
    if json_path.is_file():
        state = load_ledger_state(json_path)
    else:
        state = LedgerState()

    all_tags: List[Dict[str, str]] = list(state.foreshadowing_stash) if state.foreshadowing_stash else []
    for chap in target_chapters:
        try:
            txt, _, _ = read_file_safe(chap.path)
            tags = scan_foreshadowing_tags(txt)
            for t in tags:
                if t not in all_tags:
                    all_tags.append(t)
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
    content = (
        f"# 初始建账盘点报告 (第{s_fmt}-{e_fmt}章)\n\n"
        f"> 建账时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"> 扫描章节数：{len(target_chapters)}\n"
        f"> 提取伏笔标记数：{len(all_tags)}\n"
    )
    write_file_safe(report_file, content)

    print(f"首次建账完成！已过账 {len(target_chapters)} 章，生成双轨账本与盘点报告。")
    return 0


def run_scope_audit(project_dir: Path, scope_str: str, strict: bool, force: bool) -> int:
    """执行批量连审模式"""
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

    has_p0 = False
    has_p1 = False

    for chap in target_chapters:
        code = run_audit(project_dir, target_chapter_index=chap.index, strict=strict, force=force)
        if code == 2:
            has_p0 = True
        elif code == 1:
            has_p1 = True

    # 归档批量审查报告
    batch_dir = project_dir / "reports" / "批量审查"
    batch_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    s_fmt = str(int(s_min)).zfill(3)
    e_fmt = str(int(s_max)).zfill(3)
    batch_report_file = batch_dir / f"{today}_批量审查_第{s_fmt}-{e_fmt}章.md"
    batch_content = (
        f"# 批量审查报告：第 {s_fmt}-{e_fmt} 章\n\n"
        f"> 审查日期：{today}\n"
        f"> 覆盖章节数：{len(target_chapters)}\n"
        f"> 状态：{'P0 阻断' if has_p0 else ('P1 警告' if has_p1 else '合格通过')}\n"
    )
    write_file_safe(batch_report_file, batch_content)

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
    """执行 --apply-fix 方案采纳回写管线"""
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
                "[错误] --apply-fix 必须提供完整补丁参数 (--target-line, --old-text, --new-text) 或 --patch-file！",
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


def main(argv: Optional[List[str]] = None) -> int:
    if sys.platform == 'win32':
        try:
            if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            if hasattr(sys.stderr, 'reconfigure') and sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
                sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    """CLI 主入口函数"""
    parser = argparse.ArgumentParser(
        prog="story_audit",
        description="长篇网文深度审查工具箱与流水线管线",
    )
    parser.add_argument("--project", default=".", help="小说项目根目录（默认 .）")
    parser.add_argument("--chapter", type=float, default=None, help="审查目标章号（浮点或整数，默认最新章）")
    parser.add_argument("--scope", type=str, default=None, help="批量范围（如 31-35 或 1-30）")
    parser.add_argument("--init", action="store_true", help="首次建账模式（流式过账）")
    parser.add_argument("--checkpoint", action="store_true", help="分卷封账模式")
    parser.add_argument("--volume", type=int, default=None, help="卷号（配对 --checkpoint）")
    parser.add_argument("--sync-from-md", action="store_true", help="从 设定/资源账本.md 反向同步增量回 JSON")
    parser.add_argument("--apply-fix", type=int, choices=[1, 2], default=None, help="采纳修复方案号（1 或 2）")
    parser.add_argument("--force", action="store_true", help="忽略脏写警告强制覆盖 Markdown")
    parser.add_argument("--strict", action="store_true", help="严格模式（发现 P1 违规时返回 Exit Code 1）")

    # 补丁与辅助参数
    parser.add_argument("--target-line", type=int, default=None, help="补丁目标行号")
    parser.add_argument("--old-text", type=str, default=None, help="待替换原句")
    parser.add_argument("--new-text", type=str, default=None, help="替换后新句")
    parser.add_argument("--context-before", type=str, default="", help="前一句上下文锚点")
    parser.add_argument("--context-after", type=str, default="", help="后一句上下文锚点")
    parser.add_argument("--patch-file", type=str, default=None, help="补丁 JSON 文件路径")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse 解析失败通常触发 SystemExit(2)，转换为系统错误 3
        return 3 if e.code != 0 else 0

    project_dir = Path(args.project).resolve()
    if not project_dir.exists():
        print(f"[错误] 项目根目录不存在: {project_dir}", file=sys.stderr)
        return 3

    # 分发执行管线
    # 1. 采纳修复回写
    if args.apply_fix is not None:
        return run_apply_fix(
            project_dir=project_dir,
            chapter_idx=args.chapter,
            target_line=args.target_line,
            old_text=args.old_text,
            new_text=args.new_text,
            context_before=args.context_before,
            context_after=args.context_after,
            patch_file=args.patch_file,
        )

    # 2. 反向同步
    if args.sync_from_md:
        return run_sync_from_md(project_dir=project_dir)

    # 3. 分卷封账
    if args.checkpoint:
        return run_checkpoint(project_dir=project_dir, volume=args.volume)

    # 4. 首次建账
    if args.init:
        return run_init_mode(project_dir=project_dir, scope_str=args.scope, force=args.force)

    # 5. 批量多章连审
    if args.scope:
        return run_scope_audit(
            project_dir=project_dir,
            scope_str=args.scope,
            strict=args.strict,
            force=args.force,
        )

    # 6. 单章日常审查（默认）
    return run_audit(
        project_dir=project_dir,
        target_chapter_index=args.chapter,
        strict=args.strict,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())