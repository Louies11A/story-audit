# -*- coding: utf-8 -*-
"""
story_audit.py: 长篇网文深度审查核心调度管线与纯模块化 Python API

架构铁律：本项目采用纯模块化 Python API 驱动设计，不提供亦不涉及任何 CLI 命令行接口。
后续所有功能开发与生态扩展均严格围绕 Python API、强类型数据契约与 Agent 工具函数展开，坚决不涉及 CLI。

串联安全 I/O、智能章节匹配器、排版扫描器、双轨账本状态机、跨章接缝器与安全回写器。
严格遵循 Python 3.8+ 标准库与零外部依赖约定。
"""

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

# 确保技能根目录在 sys.path 中，支持 python scripts/story_audit.py 直接独立调用
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.chapter_linker import extract_boundary_slices
from scripts.chapter_resolver import ChapterResolver
from scripts.genre_detector import GenreProfile, detect_genre, resolve_canonical_genre
from scripts.format_scanner import scan_typography_flaws
from scripts.ledger_engine import (
    ASSET_EVENT_DECISIONS,
    ASSET_EVENT_DIRECTIONS,
    AssetItem,
    LedgerDirtyError,
    LedgerState,
    check_dirty_state,
    categorize_asset,
    ensure_ledger_recovered,
    create_volume_checkpoint,
    extract_heuristic_asset_changes,
    extract_heuristic_assets,
    find_asset_by_identity,
    make_asset_event_id,
    make_confirmed_asset_id,
    parse_chinese_or_arabic_number,
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
from scripts.safe_io import SafeIOError
from scripts.ai_patterns_checker import scan_ai_patterns
from scripts.author_memory import AuthorMemory
from scripts.runtime_detector import VALID_MODES, detect_runtime, is_subagent_context, resolve_execution_mode
from scripts.platform_rubrics import evaluate_platform_rubric, VALID_PLATFORMS
from scripts.audit_state import (
    AuditState,
    VALID_FORESHADOWING_ACTIONS,
    VALID_FORESHADOWING_SOURCES,
    VALID_COORDINATION_SOURCES,
    VALID_RECHECK_SCOPES,
    append_version_event,
    apply_foreshadowing_adjudication,
    collect_pending_rechecks,
    get_audit_state_path,
    get_chapter_version,
    is_foreshadowing_adjudicated,
    is_chapter_version_audited,
    load_audit_state,
    make_patch_id,
    record_chapter_version,
    register_pending_recheck,
    resolve_pending_rechecks,
    save_audit_state,
    get_inherited_items,
    make_defect_id,
    merge_defect_scan,
    render_inherited_items_section,
)
from scripts.expert_results import (
    EXPERT_STATUS_COMPLETED,
    ExpertResult,
    STORE_SCHEMA_VERSION,
    build_expert_result_record,
    compute_text_fingerprint,
    expert_result_summary_entries,
    get_expert_result_store_path,
    get_expert_summary_path,
    load_expert_result_records,
    merge_expert_result_records,
    refresh_expert_records_staleness,
    render_expert_summary_markdown,
)
from scripts.types import BoundaryContext, ChapterItem, Finding, FormatFinding, PatchSpec, format_factual_fix


# F08：问题处置与规则解释。规则版本用于标记确定性规则的判定口径。
RULE_METADATA_VERSION = "2026.09"
DISPOSITION_DECISIONS = ("accepted", "deferred", "false_positive")
DISPOSITION_LABELS = {"accepted": "接受", "deferred": "暂缓", "false_positive": "误报"}
# 事实冲突类与平台门禁不得被作者偏好自动免除。
PROTECTED_DISPOSITION_CATEGORIES = ("causal", "factual", "consistency")
PROTECTED_DISPOSITION_CHECKERS = ("platform_rubric",)

# F07：流式审查运行清单目录（reports/批量审查/运行清单/）。
RUN_MANIFEST_DIRNAME = "运行清单"





def safe_console_print(msg: str, file: Any = None) -> None:
    """安全控制台输出，自动处理 Windows GBK/ANSI 环境下的 Emoji 与 Unicode 编码兼容"""
    target = file if file is not None else sys.stdout
    try:
        print(msg, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "utf-8"
        cleaned = (
            msg.replace("🚀", "[启动]")
            .replace("📊", "[看板]")
            .replace("🔴", "[阻断]")
            .replace("🟡", "[警告]")
            .replace("🟢", "[通过]")
            .replace("✅", "[完成]")
            .replace("⚠️", "[注意]")
        )
        safe_text = cleaned.encode(encoding, errors="replace").decode(encoding)
        try:
            print(safe_text, file=target)
        except Exception:
            pass

def _resolve_project_dir(project_dir: Union[str, Path]) -> Path:
    """拒绝缺省类型错误和空路径，避免意外退回进程工作目录。"""
    if not isinstance(project_dir, (str, os.PathLike)):
        raise ValueError("project_dir 必须是现有目录的字符串或 Path")
    if isinstance(project_dir, str) and not project_dir.strip():
        raise ValueError("project_dir 不能为空字符串")
    try:
        path = Path(project_dir).resolve()
    except RuntimeError as e:
        raise ValueError("project_dir 无法解析") from e
    if not path.is_dir():
        raise ValueError("project_dir 不存在或不是目录")
    return path


def _validate_chapter_index(index: Optional[float]) -> None:
    if index is None:
        return
    if isinstance(index, bool) or not isinstance(index, (int, float)):
        raise ValueError("章号必须是有限的非负数值，或用 None 选择最新章")
    try:
        valid = math.isfinite(index) and index >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("章号必须是有限的非负数值")


def _validate_boolean_options(**options: bool) -> None:
    for name, value in options.items():
        if not isinstance(value, bool):
            raise ValueError(f"{name} 必须是 bool，不能使用字符串或数值代替")


def _normalize_audit_options(platform: str, mode: str, genre: str) -> Tuple[str, str, str]:
    if not isinstance(platform, str) or platform.strip().lower() not in VALID_PLATFORMS:
        raise ValueError("platform 必须是 fanqie/qidian/zhihu/generic")
    if not isinstance(mode, str) or mode.strip().lower() not in VALID_MODES:
        raise ValueError("mode 必须是 auto/full/lean/solo")
    if genre is not None and not isinstance(genre, str):
        raise ValueError("genre 必须是题材名称字符串或 None")
    return platform.strip().lower(), mode.strip().lower(), (genre or "auto").strip()


def _report_api_error(error: Exception, silent: bool) -> None:
    if not silent:
        safe_console_print(f"[错误] {error}", file=sys.stderr)


def _resolve_precheck_mode(requested_mode: str, fallback_reason: Optional[str] = None) -> Tuple[str, str]:
    """Python 管线只执行确定性预检；保留宿主诊断但不把能力当作已执行结果。"""
    if fallback_reason is None:
        _, fallback_reason = resolve_execution_mode(requested_mode)
    reasons = [reason for reason in fallback_reason.split("; ") if reason and reason != "none"]
    if requested_mode.strip().lower() != "solo" and "python_api_deterministic_only" not in reasons:
        reasons.append("python_api_deterministic_only")
    return "solo", "; ".join(reasons) or "none"


def _select_unique_chapter(chapters: List[ChapterItem], index: Optional[float]) -> Optional[ChapterItem]:
    target = chapters[-1].index if index is None else index
    matches = [chapter for chapter in chapters if abs(chapter.index - target) < 1e-4]
    if len(matches) > 1:
        raise ValueError(f"第 {target:g} 章对应多个文件，请统一章号后重试")
    return matches[0] if matches else None


def _validate_unique_targets(chapters: List[ChapterItem]) -> None:
    """发现器已按章号排序；批量产生任何报告前拒绝有歧义的集合。"""
    for previous, current in zip(chapters, chapters[1:]):
        if abs(previous.index - current.index) < 1e-4:
            raise ValueError(f"第 {current.index:g} 章对应多个文件，请统一章号后重试")


def parse_scope_range(scope_str: str) -> Tuple[float, float]:
    """解析范围字符串，如 '31-35' 或 '1-30'"""
    if not isinstance(scope_str, str) or not scope_str or "-" not in scope_str:
        raise ValueError(f"无效的范围格式: {scope_str}，应形如 '31-35'")
    parts = scope_str.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"无效的范围格式: {scope_str}")
    try:
        start = float(parts[0].strip())
        end = float(parts[1].strip())
        _validate_chapter_index(start)
        _validate_chapter_index(end)
        return min(start, end), max(start, end)
    except ValueError as e:
        raise ValueError(f"范围解析失败: {scope_str}, {e}")


def _text_content_version(text: str) -> str:
    """计算规范化正文内容的 SHA-256，作为缺陷关闭依据的正文版本。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _format_history_chapter_number(chapter_index: float) -> str:
    """历史归档章号：整数补足三位，小数保留完整有效位。"""
    value = float(chapter_index)
    if value.is_integer():
        return f"{int(value):03d}"
    text = f"{value:g}"
    whole, _, fraction = text.partition(".")
    return f"{whole.zfill(3)}.{fraction}" if fraction else whole.zfill(3)


def _read_optional_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_optional_bytes(path: Path, original: Optional[bytes]) -> None:
    if original is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)


def _write_bytes_safe(path: Path, data: bytes) -> None:
    """按字节原子写入（临时文件 + fsync + os.replace），用于原样归档历史产物。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path_str: Optional[str] = None
    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=path.parent, prefix=f".tmp_{path.stem}_", suffix=".tmp"
        )
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path_str, path)
    except Exception as e:
        if temp_path_str and os.path.exists(temp_path_str):
            try:
                os.remove(temp_path_str)
            except OSError:
                pass
        raise SafeIOWriteError(f"写入文件失败 {path}: {e}") from e


def _flush_staged_writes(staged_writes: Dict[Path, Union[str, bytes]]) -> None:
    for path, content in staged_writes.items():
        if isinstance(content, bytes):
            _write_bytes_safe(path, content)
        else:
            write_file_safe(path, content)


def _clip_event_text(text: Any, limit: int = 300) -> str:
    """事件留痕用的文本截断，避免超长补丁把状态文件撑大。"""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _explicit_violation_rule(message: str) -> Dict[str, Any]:
    """显式标记类确定性规则的元数据（规则 id、版本、阈值、命中条件、上下文）。"""
    return {
        "rule_id": "explicit_violation",
        "rule_version": RULE_METADATA_VERSION,
        "threshold": "P0/P1 显式标记",
        "condition": "正文出现 audit:p0/p1 注释或【Px: ...】显式标记",
        "context": _clip_event_text(message, 120),
    }


def _platform_rubric_rule(platform: str, location: str, evidence: str) -> Dict[str, Any]:
    """平台门禁类确定性规则的元数据；阈值以 references/rubrics 的卡尺为准。"""
    return {
        "rule_id": f"platform_rubric:{platform}",
        "rule_version": RULE_METADATA_VERSION,
        "threshold": f"见 references/rubrics/{platform}.md 对应门禁阈值",
        "condition": f"{platform} 平台卡尺规则命中（{location or '位置未记录'}）",
        "context": _clip_event_text(evidence, 120),
    }


def _rollback_chapter_write(
    chapter_path: Path,
    original_bytes: Optional[bytes],
    created_backups: List[Path],
    backup_dir: Optional[Path] = None,
    backup_dir_existed: bool = True,
) -> bool:
    """回滚本次安全回写：恢复正文原始字节并删除本次新增备份，返回是否恢复成功。"""
    restored = False
    if original_bytes is not None:
        try:
            _write_bytes_safe(chapter_path, original_bytes)
            restored = True
        except Exception:
            restored = False
    if restored:
        for backup in created_backups:
            try:
                backup.unlink()
            except OSError:
                pass
        if backup_dir is not None and not backup_dir_existed:
            # 本次调用新建的空备份目录一并清理，失败路径不留任何残留。
            try:
                backup_dir.rmdir()
            except OSError:
                pass
    return restored


def _flush_staged_writes_with_rollback(
    staged_writes: Dict[Path, Union[str, bytes]],
    state_path: Path,
    original_state: Optional[bytes],
    original_artifacts: Dict[Path, Optional[bytes]],
) -> None:
    """产物写入失败时回滚已写产物与状态，避免与未落盘状态矛盾。"""
    try:
        _flush_staged_writes(staged_writes)
    except Exception:
        for path, original in original_artifacts.items():
            try:
                _restore_optional_bytes(path, original)
            except OSError:
                pass
        try:
            _restore_optional_bytes(state_path, original_state)
        except OSError:
            pass
        raise


def _resolve_batch_history_path(
    batch_dir: Path,
    today: str,
    s_fmt: str,
    e_fmt: str,
    initial_run_id: str,
) -> Tuple[str, Path]:
    """选择未占用的批量历史路径；uuid 注入冲突时追加递增后缀。"""
    run_id = initial_run_id
    for _ in range(100):
        candidate = batch_dir / f"{today}_{run_id}_批量审查_第{s_fmt}-{e_fmt}章.md"
        if not candidate.exists():
            return run_id, candidate
        run_id = uuid.uuid4().hex[:12]

    suffix = 1
    while True:
        candidate_run_id = f"{run_id}-{suffix}"
        candidate = batch_dir / f"{today}_{candidate_run_id}_批量审查_第{s_fmt}-{e_fmt}章.md"
        if not candidate.exists():
            return candidate_run_id, candidate
        suffix += 1


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


def get_report_history_path(reports_dir: Path, chapter_index: float, text_version: str) -> Path:
    """计算旧版本报告归档路径：reports/单章审查/历史/{桶}/第{N}章_审查报告_v{指纹前12位}.md"""
    archive_path = get_report_archive_path(reports_dir, chapter_index)
    version_tag = (text_version or "unknown")[:12]
    return (
        archive_path.parent.parent
        / "历史"
        / archive_path.parent.name
        / f"{archive_path.stem}_v{version_tag}{archive_path.suffix}"
    )


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
    effective_mode: str = "solo",
    fallback_reason: str = "none",
    platform: str = "generic",
    platform_data: Optional[Dict[str, Any]] = None,
    author_memory_text: Optional[str] = None,
    inherited_items: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造结构严格冻结契约预审包字典"""
    effective_mode, fallback_reason = _resolve_precheck_mode(requested_mode, fallback_reason)
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
            "review_stage": "deterministic_precheck",
            "expert_review_executed": False,
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
    effective_mode: str = "solo",
    fallback_reason: str = "none",
    platform: str = "generic",
    author_memory_text: Optional[str] = None,
    platform_data: Optional[Dict[str, Any]] = None,
    inherited_items: Optional[Dict[str, Any]] = None,
) -> str:
    """渲染符合统一审查报告 Schema (Markdown) 的报告内容"""
    effective_mode, fallback_reason = _resolve_precheck_mode(requested_mode, fallback_reason)
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
        "Review Stage: deterministic_precheck",
        "Expert Review: not_executed",
        f"Platform Rubric: {platform}",
        f"Genre: {gp.primary_genre}",
        f"Scope: 第{curr_chapter.index:03g}章",
        "",
        f"# 📚 长篇网文确定性预检报告：第 {curr_chapter.index} 章",
        f"> 审查时间：{now_str} | 运行模式：{effective_mode.capitalize()} (Requested: {requested_mode}, Fallback: {fallback_reason})",
        f"> 审查范围：第 {curr_chapter.index} 章 ({curr_chapter.title}) (对比承接源：{prev_info})",
        f"> 平台门禁：{platform} | 综合裁决：{verdict}",
        "> 本次仅执行规则扫描与上下文整理；专家语义审查未执行，需宿主继续核验资源、因果与剧情质量。",
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
        p_status = "未命中 P0/P1 平台规则" if platform_data.get("passed", True) else "命中 P0/P1 平台规则项"
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
    if inherited_items and (
        inherited_items.get("open_defects")
        or inherited_items.get("foreshadowing_commitments")
        or inherited_items.get("expert_results")
    ):
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
        lines.append("确定性预检未检出 P0 标记或平台规则项；未执行语义因果核验。")

    lines.extend([
        "",
        "---",
        "",
        "## 📦 二、P1 标记与资源账本快照",
    ])

    if p1_list:
        for i, item in enumerate(p1_list, 1):
            lines.append(f"### {i}. [P1 严重失误] {item}")
            lines.append(f"* **位置**：第 {curr_chapter.index} 章")
            lines.append("* **来源**：正文显式标记或平台确定性规则；具体原因与修复方案需宿主结合原文核验。")
    else:
        lines.append("确定性预检未检出 P1 标记或平台规则项；尚未核验正文资产来源与账本冲突。")

    lines.extend([
        "",
        "---",
        "",
        "## ⛓️ 三、跨章文本与转场线索",
        f"* **跨章承接**：{'有上一章承接' if boundary_ctx.has_prev_chapter else '本章为首章，无前序衔接'}",
        f"* **转场线索扫描**：{'命中转场提示词 (' + str(boundary_ctx.transition_clue) + ')' if boundary_ctx.is_pov_transition else '未命中已配置的转场提示词'}",
    ])
    if boundary_ctx.isolation_zones:
        lines.append(f"* **叙事视界隔离候选**：提示词扫描标记 {len(boundary_ctx.isolation_zones)} 处闪回/幻境候选")
    if gap_warnings:
        lines.append("* **⚠️ 序号连续性警告**：")
        for gw in gap_warnings:
            lines.append(f"  * {gw}")

    target_genre = genre_profile.primary_genre if genre_profile else "通用网文"
    poison_tip = genre_profile.red_lines[0] if (genre_profile and genre_profile.red_lines) else "由宿主结合题材判断"

    lines.extend([
        "",
        "---",
        "",
        "## 🥊 四、专家语义审查待办（需宿主执行）",
        f"* **题材参考卡尺**：【{target_genre}】；尚未核验原文是否满足该卡尺。",
        "* **待核验内容**：资源来源与消耗、人物行为与跨章因果、主线推进、情绪变化及读者体验。",
        f"* **题材风险提示**：{poison_tip}",
        "",
        "---",
        "",
        "## 📝 五、排版与句式规则扫描",
    ])

    if findings:
        for i, f in enumerate(findings, 1):
            lines.append(f"### {i}. [{f.severity} {f.flaw_type}] 行号: {f.line_number}")
            lines.append(f"* **片段**：`{f.snippet}`")
            lines.append(f"* **问题**：{f.message}")
            lines.append(f"* **建议**：{f.suggestion}")
    else:
        lines.append("本轮确定性排版规则未命中问题。")

    lines.extend([
        "",
        "---",
        "",
        "## 📊 六、账本流水与快照变动预览",
        "```diff",
        f"+ 账本资产记录数量: {len(state.assets)}",
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
    ensure_ledger_recovered(json_path)
    if not json_path.exists():
        return LedgerState()
    content, _, _ = read_file_safe(json_path)
    data = json.loads(content)
    return LedgerState.from_dict(data)


def _record_foreshadowing_tags(stash: List[Dict[str, Any]], tags: List[Dict[str, str]], source_chapter: float) -> bool:
    """记录新显式标签与实际采集章号；重复标签保留最早已记录来源。"""
    keys = {(item.get("name", "").strip(), item.get("origin", "").strip(), item.get("status", "").strip()) for item in stash}
    changed = False
    for tag in tags:
        key = (tag["name"].strip(), tag.get("origin", "").strip(), tag.get("status", "").strip())
        if key not in keys:
            keys.add(key)
            entry = dict(tag)
            entry["source_chapter"] = source_chapter
            stash.append(entry)
            changed = True
    return changed


def _collect_foreshadowing_commitments(stash: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把全部账本标签映射为承诺；显式来源原文保留，未知章号不推测。"""
    commitments: List[Dict[str, Any]] = []
    for item in stash:
        name = item.get("name", "").strip()
        if not name:
            continue
        origin = item.get("origin", "")
        source_chapter = item.get("source_chapter")
        origin_chapter = float(source_chapter) if source_chapter is not None else None
        explicit_chapter = re.match(r"^\s*(?:第\s*)?([0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万]+)\s*(?:章|$)", origin)
        if explicit_chapter:
            try:
                number = float(parse_chinese_or_arabic_number(explicit_chapter.group(1)))
                if math.isfinite(number):
                    origin_chapter = number
            except (ValueError, OverflowError):
                # origin 是自由文本；不能解析的内容仍完整保存在 note 中。
                pass
        commitments.append({
            "tag": name,
            "origin_chapter": origin_chapter,
            "status": item.get("status") or "pending",
            "note": origin or "正文显式伏笔标记，待宿主核验。",
        })
    return commitments


def _merge_foreshadowing_commitments(state: AuditState, commitments: List[Dict[str, Any]]) -> None:
    """按标签与来源去重合并承诺，不自行判断剧情是否已回收。

    已关闭/已确认（含重新开启后的历史记录）的人工裁决优先于正文标签：旧标签与重复扫描
    都不能重新激活已裁决条目，只有显式的 reopen 动作能把条目放回待办池。
    """
    def key(item: Dict[str, Any]) -> Tuple[str, Optional[float], str]:
        """章号可解析时按 (tag, 章号) 去重；章号不可用时保留来源自由文本参与身份。"""
        tag = str(item.get("tag", "")).strip()
        chapter = item.get("origin_chapter")
        if chapter is None or isinstance(chapter, bool):
            return (tag, None, str(item.get("note", "")))
        try:
            value = float(chapter)
        except (TypeError, ValueError, OverflowError):
            return (tag, None, str(item.get("note", "")))
        if not math.isfinite(value):
            return (tag, None, str(item.get("note", "")))
        return (tag, value, "")

    known = {
        key(item)
        for item in state.foreshadowing_commitments
        if isinstance(item, dict)
    }
    for item in commitments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tag", "")).strip()
        if not name:
            continue
        item_key = key(item)
        if item_key in known:
            continue
        if is_foreshadowing_adjudicated(state, name, item.get("origin_chapter")):
            continue
        known.add(item_key)
        state.foreshadowing_commitments.append(dict(item))


def _resolve_rechecks_for_audited_chapter(
    audit_state: AuditState,
    chapter_index: float,
    text_version: str,
) -> List[Dict[str, Any]]:
    """按当前版本完成审查后清除对应待办复审项，并追加可追溯的解除事件。"""
    resolved = resolve_pending_rechecks(
        audit_state,
        chapter=chapter_index,
        reason="按当前正文版本完成复审",
        source="audit",
        resolution="reaudit",
        text_version=text_version,
    )
    for item in resolved:
        append_version_event(
            audit_state,
            {
                "type": "recheck_resolved",
                "chapter": float(item.get("chapter", chapter_index)),
                "scope": str(item.get("scope") or ""),
                "recheck_id": str(item.get("id") or ""),
                "trigger_chapter": item.get("trigger_chapter"),
                "previous_text_version": str(item.get("text_version") or ""),
                "text_version": text_version,
                "resolution": "reaudit",
                "reason": "按当前正文版本完成复审",
                "source": "audit",
            },
        )
    return resolved


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
    allow_partial: bool = False,
    _chapter_snapshot: Optional[List[ChapterItem]] = None,
    _audit_state: Optional[AuditState] = None,
    _staged_writes: Optional[Dict[Path, Union[str, bytes]]] = None,
) -> int:
    """执行单章审查管线，生成预审包与归档报告，返回退出码"""
    try:
        _validate_chapter_index(target_chapter_index)
        _validate_boolean_options(strict=strict, force=force, silent=silent,
                                  author_memory=use_author_memory, allow_partial=allow_partial)
        platform, mode, genre = _normalize_audit_options(platform, mode, genre)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3
    reports_dir = project_dir / "reports"
    cache_dir = reports_dir / ".cache"

    # 0. 运行时探测与模式降级
    effective_mode, fallback_reason = _resolve_precheck_mode(mode)

    # 1. 发现章节
    resolver = ChapterResolver()
    # 快照只在本次批量调用中复用，独立调用仍重新发现文件。
    chapters = _chapter_snapshot if _chapter_snapshot is not None else resolver.discover_chapters(project_dir)
    if not chapters:
        if not silent:
            print(f"[错误] 在目录 {project_dir} 中未发现任何小说章节文件！", file=sys.stderr)
        return 3

    # 2. 定位目标章节
    try:
        curr_chapter = _select_unique_chapter(chapters, target_chapter_index)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3
    if curr_chapter is None:
        if not silent:
            print(f"[错误] 未找到指定章号: {target_chapter_index}", file=sys.stderr)
        return 3

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
    gap_warnings = resolver.diagnose_sequence_gaps(chapters, allow_partial=allow_partial)

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

    # 所有待更新状态先完整验证，不能在坏状态上继续写出账本或成功报告。
    audit_state: Optional[AuditState] = _audit_state
    try:
        state = load_ledger_state(json_path)
        if audit_state is None and write_latest_report:
            audit_state = load_audit_state(reports_dir)
        if audit_state is not None and inherited_items is None:
            inherited_items = get_inherited_items(audit_state)
        # 专家归档必须在账本与报告写入之前完成读取与结构校验：损坏时不得留下半写产物。
        expert_records = load_expert_result_records(get_expert_result_store_path(reports_dir))
    except Exception as e:
        if not silent:
            print(f"[错误] 读取持久化状态失败: {e}", file=sys.stderr)
        return 3

    # 提取正文伏笔标签更新账本伏笔池
    new_tags = scan_foreshadowing_tags(curr_text)
    has_new_tags = _record_foreshadowing_tags(state.foreshadowing_stash, new_tags, curr_chapter.index)
    foreshadowing_commitments = _collect_foreshadowing_commitments(state.foreshadowing_stash)
    # 首次标签可建立双轨；仅有人工 Markdown 时保留原稿，承诺仍进入审计状态。
    ledger_snapshot: Optional[Dict[Path, Optional[bytes]]] = None
    if has_new_tags and (json_path.exists() or not md_path.exists()):
        try:
            # 账本与审计状态必须同一提交点：先快照原字节，后续任何失败都回滚到原样。
            ledger_snapshot = {
                json_path: _read_optional_bytes(json_path),
                md_path: _read_optional_bytes(md_path),
            }
            save_ledger_state(state, json_path, md_path, force=force)
        except Exception as e:
            if not silent:
                print(f"[错误] 持久化新伏笔至账本失败: {e}", file=sys.stderr)
            return 3

    def _rollback_ledger_writes() -> None:
        """回滚本轮账本写入（含删除本次新建文件），保证失败路径不留下半写产物。"""
        if not ledger_snapshot:
            return
        for path, original in ledger_snapshot.items():
            try:
                _restore_optional_bytes(path, original)
            except OSError:
                pass

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

    # 9. 违规与严重度统计（预审包将在与持久状态合并后构建）
    p0_list: List[str] = []
    p1_list: List[str] = []
    detected_defects: List[Dict[str, Any]] = []

    detected_violations = detect_violations_in_text(curr_text)
    for v in detected_violations:
        if v["level"] == "P0":
            p0_list.append(v["message"])
        elif v["level"] == "P1":
            p1_list.append(v["message"])
        if v["level"] in ("P0", "P1"):
            detected_defects.append({
                "chapter": curr_chapter.index,
                "severity": v["level"],
                "category": "factual",
                "location": f"第{curr_chapter.index:g}章显式审计标记",
                "evidence": v["message"],
                "issue": v["message"],
                "fix": "依据原文与设定核验该显式标记，保持已知事实一致。",
                "source": "deterministic",
                "checker": "explicit_violation",
                "platform": platform,
                "status": "open",
                "rule": _explicit_violation_rule(v["message"]),
            })

    # 平台红线与严重门禁拦截
    for pf in p_findings:
        if pf.severity == "P0":
            p0_list.append(pf.issue)
        elif pf.severity == "P1":
            p1_list.append(pf.issue)
        if pf.severity in ("P0", "P1"):
            defect = pf.to_dict()
            defect["chapter"] = curr_chapter.index
            defect["source"] = "deterministic"
            defect["checker"] = "platform_rubric"
            defect["platform"] = platform
            defect["status"] = "open"
            defect["rule"] = _platform_rubric_rule(platform, pf.location, pf.evidence)
            detected_defects.append(defect)

    curr_text_version = _text_content_version(curr_text)
    for defect in detected_defects:
        defect.setdefault("source", "deterministic")
        defect.setdefault("checker", "explicit_violation")
        defect.setdefault("platform", platform)
        defect.setdefault("status", "open")
        defect.setdefault("text_version", curr_text_version)
        defect["id"] = make_defect_id(
            "deterministic",
            defect.get("checker", ""),
            defect.get("platform", platform),
            defect.get("chapter", curr_chapter.index),
            defect.get("issue", ""),
        )

    chapter_text_versions = {float(curr_chapter.index): curr_text_version}
    if audit_state is None:
        audit_state = AuditState()
        if inherited_items:
            audit_state.last_scope = inherited_items.get("last_scope", "")
            audit_state.open_defects = [
                dict(item)
                for item in inherited_items.get("open_defects", [])
                if isinstance(item, dict)
            ]
            audit_state.foreshadowing_commitments = [
                dict(item)
                for item in inherited_items.get("foreshadowing_commitments", [])
                if isinstance(item, dict)
            ]
    merge_defect_scan(
        audit_state,
        detected_defects,
        covered_chapters=[curr_chapter.index],
        checkers=["explicit_violation", "platform_rubric"],
        platform=platform,
        text_versions=chapter_text_versions,
    )
    _merge_foreshadowing_commitments(audit_state, foreshadowing_commitments)
    # 版本感知：登记本次审查的正文指纹，并按当前版本清除对应待办复审项。
    previous_version = get_chapter_version(audit_state, curr_chapter.index)
    record_chapter_version(audit_state, curr_chapter.index, curr_text_version)
    _resolve_rechecks_for_audited_chapter(
        audit_state, curr_chapter.index, curr_text_version
    )
    try:
        # 已归档的专家执行状态按当前正文重新判定过期后随继承栏目进入报告与预审包。
        expert_view = _refresh_expert_records(expert_records, project_dir)
        inherited_items = _attach_disposition_states(
            _attach_expert_summaries(get_inherited_items(audit_state), expert_view),
            project_dir,
        )
    except Exception as e:
        _rollback_ledger_writes()
        if not silent:
            print(f"[错误] 读取专家结果归档失败: {e}", file=sys.stderr)
        return 3

    # 10.5 构建预审包（问题列表与合并后的持久状态一致）
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
    bundle_content = json.dumps(bundle, ensure_ascii=False, indent=2)

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
    archived_report_path = get_report_archive_path(reports_dir, curr_chapter.index)
    pending_writes: Dict[Path, Union[str, bytes]] = {
        bundle_path: bundle_content,
        archived_report_path: report_content,
    }
    # 正文版本变化时先把旧归档报告原样搬进历史目录，杜绝静默覆盖旧裁决。
    if archived_report_path.is_file():
        if previous_version:
            # 指纹一致说明现有归档对应当前版本，可安全覆盖；否则先归档旧版本。
            needs_history = previous_version != curr_text_version
            history_version = previous_version
        else:
            # 旧状态没有章节指纹表：无法证明现有归档对应当前版本，保守归档为 unknown。
            needs_history = True
            history_version = "unknown"
        if needs_history:
            history_path = get_report_history_path(
                reports_dir, curr_chapter.index, history_version
            )
            if not history_path.exists():
                try:
                    pending_writes[history_path] = archived_report_path.read_bytes()
                except OSError as e:
                    _rollback_ledger_writes()
                    if not silent:
                        print(
                            f"[错误] 旧归档报告保护失败，已放弃本轮发布: {e}", file=sys.stderr
                        )
                    return 3
    # 汇总按当前视图重写（含正文回退到已审版本的情形），保证报告与汇总判定一致；
    # 归档 JSON 仍只由提交路径写入，读取路径不改写归档本身。
    if expert_view:
        pending_writes[get_expert_summary_path(reports_dir)] = render_expert_summary_markdown(
            expert_view, generated_at=datetime.now(timezone.utc).isoformat()
        )
    if write_latest_report:
        pending_writes[latest_report_path] = report_content

    if _staged_writes is not None:
        # 批量模式只暂存内容，待批次状态原子落盘后再统一发布。
        _staged_writes.update(pending_writes)
    elif write_latest_report:
        # 状态优先：状态保存失败时不得发布任何报告或预审包。
        state_path = get_audit_state_path(reports_dir)
        original_state = _read_optional_bytes(state_path)
        original_artifacts = {
            path: _read_optional_bytes(path) for path in pending_writes
        }
        c_idx = curr_chapter.index
        if c_idx not in audit_state.completed_chapters:
            audit_state.completed_chapters.append(c_idx)
            audit_state.completed_chapters.sort()
        audit_state.last_scope = f"{c_idx:g}"
        try:
            save_audit_state(audit_state, reports_dir)
            _flush_staged_writes_with_rollback(
                pending_writes, state_path, original_state, original_artifacts
            )
        except Exception as e:
            # 账本与状态同一提交点：发布失败时连同本轮账本写入一起回滚。
            _rollback_ledger_writes()
            if not silent:
                print(f"[错误] 发布审查产物失败: {e}", file=sys.stderr)
            return 3
    else:
        # 保留内部直接调用（非公开 API）既有行为：不写 LATEST，也不改持久状态。
        state_path = get_audit_state_path(reports_dir)
        original_state = _read_optional_bytes(state_path)
        original_artifacts = {
            path: _read_optional_bytes(path) for path in pending_writes
        }
        try:
            _flush_staged_writes_with_rollback(
                pending_writes, state_path, original_state, original_artifacts
            )
        except Exception as e:
            _rollback_ledger_writes()
            if not silent:
                print(f"[错误] 发布审查产物失败: {e}", file=sys.stderr)
            return 3

    if not silent:
        print(f"=== story-audit 深度审查报告 ===")
        print(f"Requested Mode: {mode}")
        print(f"Effective Mode: {effective_mode}")
        print(f"Fallback: {fallback_reason}")
        print("Review Stage: deterministic_precheck")
        print("Expert Review: not_executed")
        print(f"Platform Rubric: {platform}")
        print(f"Genre: {genre_profile.primary_genre}")
        print(f"Scope: 第{curr_chapter.index:03g}章")
        print(f"----------------------------------------------------------------------------------------")
        print(f"确定性预检完成：第 {curr_chapter.index} 章 ({curr_chapter.title}) [题材: {genre_profile.primary_genre} | 置信度: {genre_profile.confidence:.0%}]")
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
            print("[预检未阻断] 未检出 P0/P1 标记或平台规则项；专家语义审查未执行。")
        exit_code = 0

    if summary_collector is not None:
        p2_flaws = [f for f in findings if f.severity == "P2"]
        p3_flaws = [f for f in findings if f.severity == "P3"]
        word_count = len(re.findall(r'[一-龥\w]', curr_text))
        para_count = len([line.strip() for line in curr_text.splitlines() if line.strip()])
        status_str = "P0 阻断" if p0_list else ("P1 警告" if p1_list else "预检未阻断")
        summary_collector.update({
            "chapter_index": curr_chapter.index,
            "chapter_title": curr_chapter.title,
            "genre_profile": genre_profile,
            "word_count": word_count,
            "paragraph_count": para_count,
            "p0_list": list(p0_list),
            "p1_list": list(p1_list),
            "open_defects": list(detected_defects),
            "foreshadowing_commitments": list(foreshadowing_commitments),
            "p2_count": len(p2_flaws),
            "p3_count": len(p3_flaws),
            "findings": list(findings),
            "boundary_ctx": boundary_ctx,
            "exit_code": exit_code,
            "status": status_str,
            "archived_report_path": archived_report_path,
            # F07：流式入口与运行清单需要的正文版本与预审包定位。
            "text_version": curr_text_version,
            "pre_bundle": bundle,
            "pre_bundle_path": bundle_path,
        })

    return exit_code


def run_sync_from_md(project_dir: Path, silent: bool = False) -> int:
    """执行 sync_ledger_from_md 反向同步管线"""
    json_path, md_path = locate_ledger_paths(project_dir)
    if not md_path.is_file():
        if not silent:
            safe_console_print(f"[错误] 未找到 Markdown 账本文件: {md_path}", file=sys.stderr)
        return 3

    try:
        new_state = sync_from_markdown(md_path, json_path)
        if not silent:
            safe_console_print(f"成功将 Markdown 账本增量同步至 JSON 数据源: {json_path}")
            safe_console_print(f"当前总资产数: {len(new_state.assets)}")
        return 0
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 反向同步失败: {e}", file=sys.stderr)
        return 3


def run_checkpoint(project_dir: Path, volume: Optional[int], force: bool = False, silent: bool = False) -> int:
    """执行 checkpoint_volume 分卷封账结转管线"""
    if isinstance(volume, bool) or not isinstance(volume, int) or volume <= 0:
        if not silent:
            safe_console_print("[错误] checkpoint 结转操作必须指定正整数 volume 卷号！", file=sys.stderr)
        return 3

    json_path, _ = locate_ledger_paths(project_dir)
    if not json_path.is_file():
        if not silent:
            safe_console_print(f"[错误] 未找到账本数据源: {json_path}", file=sys.stderr)
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

        if not silent:
            safe_console_print(f"分卷封账完成！第 {volume} 卷快照已归档至: {checkpoint_file}")
        return 0
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 分卷封账失败: {e}", file=sys.stderr)
        return 3


def run_init_mode(project_dir: Path, scope_str: Optional[str] = None, force: bool = False, genre: str = "auto", silent: bool = False) -> Tuple[int, Path]:
    """执行 init_ledger 首次建账管线，集成启发式资产与伏笔抽取"""
    try:
        _validate_boolean_options(force=force, silent=silent)
        _, _, genre = _normalize_audit_options("generic", "solo", genre)
        bounds = parse_scope_range(scope_str) if scope_str is not None else None
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        if not silent:
            print(f"[错误] 未发现任何章节文件，无法建账！", file=sys.stderr)
        return 3, Path("")

    if bounds is not None:
        s_min, s_max = bounds
        target_chapters = [c for c in chapters if s_min <= c.index <= s_max]
    else:
        target_chapters = chapters

    if not target_chapters:
        if not silent:
            print(f"[错误] 范围内未发现章节！", file=sys.stderr)
        return 3, Path("")
    try:
        _validate_unique_targets(target_chapters)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    json_path, md_path = locate_ledger_paths(project_dir)
    if md_path.is_file() and json_path.is_file() and not force:
        if check_dirty_state(md_path, json_path):
            if not silent:
                print(f"[防脏写拦截] 账本存在未同步手工编辑，建账被拒绝！", file=sys.stderr)
            return 3, Path("")

    if md_path.exists() and not json_path.exists() and not force:
        if not silent:
            print("[错误] 已有 Markdown 账本但缺少 JSON，请先调用 sync_ledger_from_md() 恢复数据源。", file=sys.stderr)
        return 3, Path("")

    # 优先尝试 load_ledger_state(json_path)，继承既有 assets，仅在账本不存在时初始化新对象
    try:
        state = load_ledger_state(json_path)
    except Exception as e:
        if not silent:
            print(f"[错误] 读取账本失败，建账已终止: {e}", file=sys.stderr)
        return 3, Path("")

    all_tags: List[Dict[str, Any]] = list(state.foreshadowing_stash) if state.foreshadowing_stash else []
    existing_asset_names: Dict[str, AssetItem] = {item.name: item for item in state.assets.values()}

    total_extracted_assets = 0
    for chap in target_chapters:
        try:
            txt, _, _ = read_file_safe(chap.path)
            # 1. 扫描伏笔标签
            tags = scan_foreshadowing_tags(txt)
            _record_foreshadowing_tags(all_tags, tags, chap.index)

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
        except Exception as e:
            if not silent:
                print(f"[错误] 建账扫描第 {chap.index:g} 章失败: {e}", file=sys.stderr)
            return 3, Path("")

    state.foreshadowing_stash = all_tags
    state.last_updated_chapter = target_chapters[-1].index

    try:
        save_ledger_state(state, json_path, md_path, force=force)
    except LedgerDirtyError as e:
        if not silent:
            print(f"[错误] 保存账本遇到防脏写拦截: {e}", file=sys.stderr)
        return 3, Path("")

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
    effective_mode: str = "solo",
    fallback_reason: str = "none",
    platform: str = "generic",
    run_id: str = "",
    inherited_items: Optional[Dict[str, Any]] = None,
) -> str:
    """渲染批量审查聚合大盘报告 (Markdown)"""
    effective_mode, fallback_reason = _resolve_precheck_mode(requested_mode, fallback_reason)
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
        overall_status = "🟢 确定性预检未发现 P0/P1 标记或平台规则项"

    primary_genre = chapter_summaries[0]["genre_profile"].primary_genre if chapter_summaries and chapter_summaries[0].get("genre_profile") else "通用网文"
    lines: List[str] = [
        "=== story-audit 深度审查报告 ===",
        f"Requested Mode: {requested_mode}",
        f"Effective Mode: {effective_mode}",
        f"Fallback: {fallback_reason}",
        f"Platform Rubric: {platform}",
        f"Genre: {primary_genre}",
        f"Run ID: {run_id}",
        f"Scope: {scope_str}",
        f"Normalized Scope: {_format_history_chapter_number(s_min)}-{_format_history_chapter_number(s_max)}",
        "Review Stage: deterministic_precheck",
        "Expert Review: not_executed",
        "",
        f"# 批量连审大盘汇总报告 (范围: {scope_str} | Run ID: {run_id})",
        "",
        f"> 生成时间：{today}  ",
        f"> 审查范围：第 {_format_history_chapter_number(s_min)} 章 至 第 {_format_history_chapter_number(s_max)} 章  ",
        f"> 覆盖章节：共 {n_chaps} 章  ",
        f"> 综合判定：{overall_status}  ",
        "> 专家语义审查未执行；资源冲突、因果一致性与剧情质量仍需宿主核验。",
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
        f"| P0 标记与规则项 | {total_p0} 处 | 正文显式标记与平台确定性规则命中 |",
        f"| P1 标记与规则项 | {total_p1} 处 | 正文显式标记与平台确定性规则命中 |",
        f"| P2 排版长句/长段 | {total_p2} 处 | 单句逗号过多或单段超 120 字 |",
        f"| P3 翻译腔/描写混杂 | {total_p3} 处 | AI 连词或对话后堆砌长动作 |",
        "",
        "---",
        "",
    ]

    if inherited_items and (
        inherited_items.get("open_defects")
        or inherited_items.get("foreshadowing_commitments")
        or inherited_items.get("expert_results")
    ):
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
            assess_parts.append("篇幅较长(>4500字)")
        else:
            assess_parts.append("标准篇幅")

        if avg_plen > 70:
            assess_parts.append("段落偏密")
        elif avg_plen < 35:
            assess_parts.append("平均段长少于35字")
        else:
            assess_parts.append("平均段长35至70字")

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
        lines.append("（本批确定性规则与显式标记未命中问题；专家语义审查未执行。）\n")

    lines.extend([
        "---",
        "",
        "## 四、跨章接缝与 POV 视点一览表",
        "",
        "| 章号 | 章节名称 | 转场提示词扫描 | 接缝转场线索 | 前章上下文 | 语义核验状态 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for c in chapter_summaries:
        b_ctx: BoundaryContext = c["boundary_ctx"]
        idx_str = f"第{c['chapter_index']:03g}章"
        if not b_ctx.has_prev_chapter:
            pov_info = "首章开篇"
            clue = "-"
            prev_status = "首章无前置上下文"
            seam_rating = "无前章对照"
        else:
            if b_ctx.is_pov_transition:
                pov_info = "命中转场提示词"
                clue = b_ctx.transition_clue or "视角切换"
                seam_rating = "待宿主核验"
            elif b_ctx.isolation_zones:
                pov_info = "命中闪回或幻境提示词"
                clue = "含回忆/闪回"
                seam_rating = "待宿主核验"
            else:
                pov_info = "未命中已配置的转场提示词"
                clue = "-"
                seam_rating = "待宿主核验"

            prev_status = "已读取前章切片" if b_ctx.has_prev_chapter else "-"

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
    allow_partial: Optional[bool] = None,
) -> int:
    """执行批量连审模式，生成大盘汇总报告与紧凑看板输出"""
    try:
        _validate_boolean_options(strict=strict, force=force, silent=silent,
                                  author_memory=use_author_memory)
        if allow_partial is not None:
            _validate_boolean_options(allow_partial=allow_partial)
        platform, mode, genre = _normalize_audit_options(platform, mode, genre)
        s_min, s_max = parse_scope_range(scope_str)
        scope_str = "".join(scope_str.split())
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] {e}", file=sys.stderr)
        return 3

    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    target_chapters = [c for c in chapters if s_min <= c.index <= s_max]
    if not target_chapters:
        if not silent:
            safe_console_print(f"[错误] 范围 {scope_str} 内未找到任何章节！", file=sys.stderr)
        return 3

    try:
        _validate_unique_targets(target_chapters)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3

    n_total = len(target_chapters)
    if allow_partial is None:
        effective_allow_partial = bool(s_min > 1.0)
    else:
        effective_allow_partial = allow_partial
    reports_dir = project_dir / "reports"
    run_id = uuid.uuid4().hex[:12]

    # 批量失败必须回滚账本：批次内各章可能已经写入 资源账本.json/.md。
    batch_json_path, batch_md_path = locate_ledger_paths(project_dir)
    batch_ledger_snapshot: Dict[Path, Optional[bytes]] = {
        batch_json_path: _read_optional_bytes(batch_json_path),
        batch_md_path: _read_optional_bytes(batch_md_path),
    }

    def _rollback_batch_ledger() -> None:
        """把账本恢复到批次开始前的字节（含删除本次新建文件）。"""
        for path, original in batch_ledger_snapshot.items():
            try:
                _restore_optional_bytes(path, original)
            except OSError:
                pass

    # 运行时探测与跨批状态机继承
    effective_mode, fallback_reason = _resolve_precheck_mode(mode)
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 读取审计状态失败: {e}", file=sys.stderr)
        return 3
    inherited_items = get_inherited_items(audit_state)

    if not silent:
        safe_console_print(f"=== story-audit 深度审查报告 ===")
        safe_console_print(f"Requested Mode: {mode}")
        safe_console_print(f"Effective Mode: {effective_mode}")
        safe_console_print(f"Fallback: {fallback_reason}")
        safe_console_print("Review Stage: deterministic_precheck")
        safe_console_print("Expert Review: not_executed")
        safe_console_print(f"Platform Rubric: {platform}")
        safe_console_print(f"Genre: {genre}")
        safe_console_print(f"Scope: {scope_str}")
        safe_console_print(f"========================================================================================")
        safe_console_print(f"🚀 开始批量审查 [范围: {scope_str} | 共 {n_total} 章 | 模式: {effective_mode}]")
        if inherited_items.get("open_defects"):
            safe_console_print(f"  [继承开放缺陷]: {len(inherited_items['open_defects'])} 项")
        if inherited_items.get("foreshadowing_commitments"):
            safe_console_print(f"  [监控中伏笔池]: {len(inherited_items['foreshadowing_commitments'])} 个")
        safe_console_print(f"========================================================================================")

    chapter_summaries: List[Dict[str, Any]] = []
    staged_writes: Dict[Path, Union[str, bytes]] = {}
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
            inherited_items=None,
            allow_partial=effective_allow_partial,
            _chapter_snapshot=chapters,
            _audit_state=audit_state,
            _staged_writes=staged_writes,
        )
        if code == 3:
            _rollback_batch_ledger()
            if not silent:
                safe_console_print(f"[错误] 第 {chap.index:g} 章审查失败，批量审查已停止。", file=sys.stderr)
            return 3
        chapter_summaries.append(summary)

        if code == 2:
            has_p0 = True
        elif code == 1:
            has_p1 = True

        status_tag = summary.get("status", "完成")
        if not silent:
            safe_console_print(f"  [{idx:02d}/{n_total:02d}] 审查 第{chap.index:03g}章 《{chap.title}》 ... [{status_tag}]")

    # 打印终端紧凑汇总看板
    total_words = sum(s['word_count'] for s in chapter_summaries)
    total_paras = sum(s['paragraph_count'] for s in chapter_summaries)
    tot_p0 = sum(len(s['p0_list']) for s in chapter_summaries)
    tot_p1 = sum(len(s['p1_list']) for s in chapter_summaries)
    tot_p2 = sum(s['p2_count'] for s in chapter_summaries)
    tot_p3 = sum(s['p3_count'] for s in chapter_summaries)
    overall_label = "🔴 P0 阻断" if has_p0 else ("🟡 P1 警告" if has_p1 else "🟢 确定性预检未阻断")

    if not silent:
        safe_console_print(f"========================================================================================")
        safe_console_print(f"📊 批量连审汇总看板 [范围: {scope_str} | 覆盖: {n_total} 章]")
        safe_console_print(f"========================================================================================")
        safe_console_print(f"{'章号':<8} | {'章节标题':<24} | {'字数':>6} | {'段数':>4} | {'P0':>2} | {'P1':>2} | {'P2':>2} | {'P3':>2} | {'状态':<6}")
        safe_console_print(f"{'-'*8}-+-{'-'*24}-+-{'-'*6}-+-{'-'*4}-+-{'-'*2}-+-{'-'*2}-+-{'-'*2}-+-{'-'*2}-+-{'-'*8}")

        for s in chapter_summaries:
            raw_title = s['chapter_title']
            title_disp = raw_title[:22] + ".." if len(raw_title) > 22 else raw_title
            p0_num = len(s['p0_list'])
            p1_num = len(s['p1_list'])
            p2_num = s['p2_count']
            p3_num = s['p3_count']
            safe_console_print(f"第{s['chapter_index']:03g}章  | {title_disp:<24} | {s['word_count']:>6,} | {s['paragraph_count']:>4} | {p0_num:>2} | {p1_num:>2} | {p2_num:>2} | {p3_num:>2} | {s['status']}")

        safe_console_print(f"========================================================================================")
        safe_console_print(f"【全范围大盘】总章节: {n_total} 章 | 总字数: {total_words:,} 字 | 总段落: {total_paras:,} 段")
        safe_console_print(f"【瑕疵汇总】P0 阻断: {tot_p0} | P1 警告: {tot_p1} | P2 拖沓长句/段: {tot_p2} | P3 翻译腔/混杂: {tot_p3}")
        safe_console_print(f"【判定结论】{overall_label}")
        safe_console_print(f"========================================================================================")

    reports_dir = project_dir / "reports"
    batch_dir = reports_dir / "批量审查"
    today = datetime.now().strftime("%Y-%m-%d")
    s_fmt = _format_history_chapter_number(s_min)
    e_fmt = _format_history_chapter_number(s_max)
    run_id, batch_report_file = _resolve_batch_history_path(
        batch_dir, today, s_fmt, e_fmt, run_id
    )

    # 生成聚合大盘报告 Markdown 内容
    try:
        expert_view = _refresh_expert_records(
            load_expert_result_records(get_expert_result_store_path(reports_dir)), project_dir
        )
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 读取专家结果归档失败: {e}", file=sys.stderr)
        return 3
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
        run_id=run_id,
        inherited_items=_attach_disposition_states(
            _attach_expert_summaries(get_inherited_items(audit_state), expert_view),
            project_dir,
        ),
    )

    scope_clean = scope_str.replace(" ", "")
    scope_summary_path = reports_dir / f"BATCH_SUMMARY_SCOPE_{scope_clean}.md"
    latest_report_path = reports_dir / "LATEST_REPORT.md"
    staged_writes[scope_summary_path] = batch_summary_content
    staged_writes[batch_report_file] = batch_summary_content
    staged_writes[latest_report_path] = batch_summary_content

    # 先在内存更新跨批状态，状态原子落盘成功后才能发布任何产物。
    audit_state.last_scope = scope_str
    for chap in target_chapters:
        if chap.index not in audit_state.completed_chapters:
            audit_state.completed_chapters.append(chap.index)
    audit_state.completed_chapters.sort()

    state_path = get_audit_state_path(reports_dir)
    original_state = _read_optional_bytes(state_path)
    original_artifacts = {
        path: _read_optional_bytes(path) for path in staged_writes
    }
    try:
        save_audit_state(audit_state, reports_dir)
    except Exception as e:
        _rollback_batch_ledger()
        if not silent:
            safe_console_print(f"[错误] 保存审计状态失败: {e}", file=sys.stderr)
        return 3
    try:
        _flush_staged_writes_with_rollback(
            staged_writes, state_path, original_state, original_artifacts
        )
    except Exception as e:
        _rollback_batch_ledger()
        if not silent:
            safe_console_print(f"[错误] 写入审查产物失败: {e}", file=sys.stderr)
        return 3

    if not silent:
        safe_console_print(f"✅ 批量审查大盘报告已生成：{scope_summary_path}")
        safe_console_print(f"✅ 历史归档报告已写入：{batch_report_file}")
        safe_console_print(f"✅ 最新审查总览已更新：{latest_report_path}")
        safe_console_print(f"✅ 跨批因果状态机已原子更新：{get_audit_state_path(reports_dir)}")

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
    silent: bool = False,
) -> int:
    """执行 apply_fix 方案采纳回写管线"""
    try:
        _validate_chapter_index(chapter_idx)
        _validate_boolean_options(silent=silent)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3
    resolver = ChapterResolver()
    chapters = resolver.discover_chapters(project_dir)
    if not chapters:
        if not silent:
            safe_console_print(f"[错误] 未发现章节文件！", file=sys.stderr)
        return 3

    try:
        target_chapter = _select_unique_chapter(chapters, chapter_idx)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3

    if not target_chapter:
        if not silent:
            safe_console_print(f"[错误] 目标章节不存在: {chapter_idx}", file=sys.stderr)
        return 3

    # 构建 PatchSpec
    if patch_file:
        try:
            with open(patch_file, encoding="utf-8") as f:
                p_data = json.load(f)
            patch = PatchSpec(
                target_line=p_data["target_line"],
                context_before=p_data.get("context_before", ""),
                old_text=p_data["old_text"],
                new_text=p_data["new_text"],
                context_after=p_data.get("context_after", ""),
            )
        except Exception as e:
            if not silent:
                safe_console_print(f"[错误] 读取补丁文件失败: {e}", file=sys.stderr)
            return 3
    else:
        if target_line is None or old_text is None or new_text is None:
            if not silent:
                safe_console_print(
                    "[错误] apply_fix 必须提供完整补丁参数 (target_line, old_text, new_text) 或 patch_file！",
                    file=sys.stderr,
                )
            return 3
        patch = PatchSpec(
            target_line=target_line,
            context_before=context_before,
            old_text=old_text,
            new_text=new_text,
            context_after=context_after,
        )

    # 先验证原始字段，再进入会生成备份的回写器；显式空串仍可用于删除。
    if (
        not isinstance(patch.target_line, int)
        or isinstance(patch.target_line, bool)
        or patch.target_line <= 0
        or not isinstance(patch.old_text, str)
        or not patch.old_text
        or not isinstance(patch.new_text, str)
        or not isinstance(patch.context_before, str)
        or not isinstance(patch.context_after, str)
    ):
        if not silent:
            safe_console_print(
                "[错误] 补丁必须包含正整数 target_line、非空字符串 old_text，"
                "且 new_text 与前后锚点必须为字符串。",
                file=sys.stderr,
            )
        return 3

    reports_dir = project_dir / "reports"
    # 协调状态与正文必须先完成读取与校验，任何前置失败都不得触碰原稿。
    try:
        audit_state = load_audit_state(reports_dir)
        original_bytes = target_chapter.path.read_bytes()
        original_text, _, _ = read_file_safe(target_chapter.path)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 读取回写上下文失败: {e}", file=sys.stderr)
        return 3
    before_version = _text_content_version(original_text)

    curr_pos = chapters.index(target_chapter)
    next_chapter: Optional[ChapterItem] = (
        chapters[curr_pos + 1] if curr_pos + 1 < len(chapters) else None
    )
    next_text_version = ""
    if next_chapter is not None:
        try:
            next_text, _, _ = read_file_safe(next_chapter.path)
            next_text_version = _text_content_version(next_text)
        except Exception:
            # 下一章暂时无法读取时仍登记接缝待办，只是无法记录指纹。
            next_text_version = ""

    backup_dir = reports_dir / ".bak"
    backup_dir_existed = backup_dir.is_dir()
    existing_backups = (
        {path for path in backup_dir.glob("*") if path.is_file()}
        if backup_dir.is_dir()
        else set()
    )
    try:
        success = apply_patch_with_disambiguation(
            file_path=target_chapter.path,
            patch=patch,
            backup_dir=backup_dir,
        )
        if not success:
            if not silent:
                safe_console_print(f"[错误] 回写未成功完成。", file=sys.stderr)
            return 3
    except (PatchAnchorNotFoundError, AmbiguousPatchError, SafeWriterError) as e:
        if not silent:
            safe_console_print(f"[安全回写拒绝] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        if not silent:
            safe_console_print(f"[系统异常] 安全回写失败: {e}", file=sys.stderr)
        return 3

    # 回写成功：登记补丁事件与受影响章节的待复审项（第 N 章报告 + 第 N+1 章接缝）。
    created_backups = sorted(
        {path for path in backup_dir.glob("*") if path.is_file()} - existing_backups
    ) if backup_dir.is_dir() else []
    try:
        updated_text, _, _ = read_file_safe(target_chapter.path)
    except Exception as e:
        restored = _rollback_chapter_write(
            target_chapter.path, original_bytes, created_backups, backup_dir, backup_dir_existed
        )
        if not silent:
            detail = "已回滚原稿" if restored else "回滚失败，请依据备份人工恢复"
            safe_console_print(
                f"[错误] 回写后无法读取正文 ({e})，{detail}。", file=sys.stderr
            )
        return 3
    after_version = _text_content_version(updated_text)
    patch_id = make_patch_id(target_chapter.index, patch)
    backup_rel = ""
    if created_backups:
        try:
            backup_rel = created_backups[-1].relative_to(project_dir).as_posix()
        except ValueError:
            backup_rel = created_backups[-1].as_posix()

    register_pending_recheck(
        audit_state,
        target_chapter.index,
        "chapter",
        reason="补丁写入后本章正文版本变化，原归档报告失效，需按当前版本复审",
        text_version=after_version,
        previous_text_version=before_version,
        trigger_chapter=target_chapter.index,
        patch_id=patch_id,
        source="apply_fix",
    )
    affected_chapters = [float(target_chapter.index)]
    if next_chapter is not None:
        register_pending_recheck(
            audit_state,
            next_chapter.index,
            "seam",
            reason="上一章补丁写入后接缝检查失效，需按当前正文复审",
            text_version=next_text_version,
            previous_text_version=next_text_version,
            trigger_chapter=target_chapter.index,
            patch_id=patch_id,
            source="apply_fix",
        )
        affected_chapters.append(float(next_chapter.index))
    append_version_event(
        audit_state,
        {
            "type": "patch_applied",
            "chapter": float(target_chapter.index),
            "patch_id": patch_id,
            "target_line": patch.target_line,
            "old_text": _clip_event_text(patch.old_text),
            "new_text": _clip_event_text(patch.new_text),
            "before_text_version": before_version,
            "after_text_version": after_version,
            "backup": backup_rel,
            "affected_chapters": affected_chapters,
            "source": "apply_fix",
        },
    )

    try:
        save_audit_state(audit_state, reports_dir)
    except Exception as e:
        restored = _rollback_chapter_write(
            target_chapter.path, original_bytes, created_backups, backup_dir, backup_dir_existed
        )
        if not silent:
            detail = "已回滚原稿" if restored else "回滚失败，请依据备份人工恢复"
            safe_console_print(
                f"[错误] 保存回写协调状态失败: {e}，{detail}。", file=sys.stderr
            )
        return 3

    if not silent:
        safe_console_print(f"成功安全回写第 {target_chapter.index} 章，已生成原子备份。")
        safe_console_print(
            f"已登记补丁事件 {patch_id}，待复审章节：第 {target_chapter.index} 章"
            + (f" 与第 {next_chapter.index} 章接缝" if next_chapter is not None else "")
        )
    return 0


def run_foreshadowing_adjudication(
    project_dir: Path,
    name: str,
    action: str,
    reason: str,
    evidence: str = "",
    source: str = "author",
    chapter: Optional[float] = None,
    silent: bool = False,
) -> Tuple[int, Path]:
    """执行伏笔显式裁决管线：确认、关闭或重新开启，并登记可追溯历史。"""
    try:
        _validate_boolean_options(silent=silent)
        _validate_chapter_index(chapter)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("伏笔名称 (name) 必须为非空字符串")
        if not isinstance(action, str) or action.strip().lower() not in VALID_FORESHADOWING_ACTIONS:
            raise ValueError("伏笔动作 (action) 必须是 confirm/close/reopen 之一")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须提供裁决原因 (reason)")
        if not isinstance(evidence, str):
            raise ValueError("裁决证据 (evidence) 必须为字符串")
        if not isinstance(source, str) or source.strip().lower() not in VALID_FORESHADOWING_SOURCES:
            raise ValueError("裁决来源 (source) 只能是 author 或 expert")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    reports_dir = project_dir / "reports"
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 读取持久化状态失败: {e}", file=sys.stderr)
        return 3, Path("")

    text_version = ""
    if chapter is not None:
        try:
            chapters = ChapterResolver().discover_chapters(project_dir)
            target_chapter = _select_unique_chapter(chapters, chapter)
        except (OSError, ValueError) as e:
            _report_api_error(e, silent)
            return 3, Path("")
        if target_chapter is None:
            if not silent:
                safe_console_print(
                    f"[错误] 未找到第 {chapter:g} 章正文，无法登记裁决正文版本。", file=sys.stderr
                )
            return 3, Path("")
        try:
            chapter_text, _, _ = read_file_safe(target_chapter.path)
        except Exception as e:
            if not silent:
                safe_console_print(
                    f"[错误] 读取章节失败: {target_chapter.path}, {e}", file=sys.stderr
                )
            return 3, Path("")
        text_version = _text_content_version(chapter_text)

    try:
        outcome = apply_foreshadowing_adjudication(
            audit_state,
            name=name,
            action=action,
            reason=reason,
            evidence=evidence,
            source=source,
            chapter=chapter,
            text_version=text_version,
        )
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    if not outcome.get("changed"):
        if not outcome.get("found", True):
            if not silent:
                safe_console_print(
                    f"[错误] 未找到可裁决的伏笔「{name}」历史记录，无法重新开启。",
                    file=sys.stderr,
                )
            return 3, Path("")
        return 0, get_audit_state_path(reports_dir)

    try:
        state_path = save_audit_state(audit_state, reports_dir)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 保存伏笔裁决失败: {e}", file=sys.stderr)
        return 3, Path("")
    if not silent:
        safe_console_print(
            f"成功登记伏笔裁决：{name} -> {outcome.get('action')}，历史已写入 {state_path}"
        )
    return 0, state_path


def _load_scope_chapter_texts(
    project_dir: Path,
    results: List[ExpertResult],
) -> List[Dict[float, Optional[str]]]:
    """按专家结果范围读取当前正文文本；章号歧义直接报错，缺失章号记为 None。"""
    chapters = ChapterResolver().discover_chapters(project_dir)
    if not chapters:
        raise ValueError("未发现任何章节文件，无法核对专家结果正文指纹")
    cache: Dict[float, Optional[str]] = {}

    def current_text(chapter: float) -> Optional[str]:
        key = round(float(chapter), 6)
        if key not in cache:
            target = _select_unique_chapter(chapters, chapter)
            if target is None:
                cache[key] = None
            else:
                text, _, _ = read_file_safe(target.path)
                cache[key] = text
        return cache[key]

    scoped: List[Dict[float, Optional[str]]] = []
    for result in results:
        texts: Dict[float, Optional[str]] = {}
        for chapter in result.chapters:
            value = float(chapter)
            texts[value] = current_text(value)
        scoped.append(texts)
    return scoped


def _merge_expert_defects(
    audit_state: AuditState,
    records: List[Dict[str, Any]],
    platform: str,
) -> int:
    """把未过期、已完成专家结果的 P0/P1 发现写入持久开放缺陷（专家来源）。"""
    existing_ids = {
        str(item.get("id"))
        for item in audit_state.open_defects
        if isinstance(item, dict) and item.get("id")
    }
    added = 0
    for record in records:
        if not isinstance(record, dict) or record.get("status") != EXPERT_STATUS_COMPLETED:
            continue
        if record.get("stale"):
            # 过期结果不得覆盖当前裁决。
            continue
        chapters = record.get("chapters") or []
        if not chapters:
            continue
        chapter = float(chapters[0])
        for finding in record.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "")
            if severity not in ("P0", "P1"):
                continue
            issue = str(finding.get("issue") or "")
            # 缺陷身份与确定性缺陷保持同一约定：包含平台。同一专家发现跨平台归档会保留
            # 多条带各自平台标记的记录，用于区分不同平台门禁下的裁决，不是重复计数。
            defect_id = make_defect_id(
                "expert", str(record.get("expert") or ""), platform, chapter, issue
            )
            if defect_id in existing_ids:
                continue
            existing_ids.add(defect_id)
            audit_state.open_defects.append({
                "id": defect_id,
                "chapter": chapter,
                "severity": severity,
                "category": str(finding.get("category") or "consistency"),
                "location": str(finding.get("location") or record.get("scope") or ""),
                "evidence": str(finding.get("evidence") or ""),
                "issue": issue,
                "fix": str(finding.get("fix") or ""),
                "line_number": finding.get("line_number"),
                "flaw_type": finding.get("flaw_type"),
                "source": "expert",
                "checker": f"expert:{record.get('expert') or ''}",
                "platform": platform,
                "status": "open",
                "expert": str(record.get("expert") or ""),
                "expert_result_id": str(record.get("id") or ""),
                "scope": str(record.get("scope") or ""),
                "text_version": str(record.get("text_fingerprint") or ""),
            })
            added += 1
    return added


def _build_chapter_fingerprint_lookup(project_dir: Path):
    """构造按当前正文计算范围指纹的查询函数；章节缺失或无法唯一定位时返回 None。"""
    try:
        chapters = ChapterResolver().discover_chapters(project_dir)
    except Exception:
        chapters = []
    cache: Dict[float, Optional[str]] = {}

    def lookup(scope: List[float]) -> Optional[str]:
        scoped: Dict[float, str] = {}
        for chapter in scope:
            key = round(float(chapter), 6)
            if key not in cache:
                try:
                    target = _select_unique_chapter(chapters, float(chapter))
                except ValueError:
                    target = None
                if target is None:
                    cache[key] = None
                else:
                    try:
                        text, _, _ = read_file_safe(target.path)
                    except Exception:
                        text = None
                    cache[key] = text
            text = cache[key]
            if text is None:
                return None
            scoped[float(chapter)] = text
        if not scoped:
            return None
        return compute_text_fingerprint(scoped)

    return lookup


def _refresh_expert_records(
    records: List[Dict[str, Any]],
    project_dir: Path,
) -> List[Dict[str, Any]]:
    """按当前正文重新判定专家结果过期状态，返回呈现副本（不改写归档 JSON）。"""
    if not records:
        return []
    return refresh_expert_records_staleness(
        records, _build_chapter_fingerprint_lookup(project_dir)
    )


def _attach_expert_summaries(
    inherited_items: Dict[str, Any],
    expert_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """把按当前正文判定过期的专家执行状态挂入继承栏目，供报告与预审包呈现。"""
    if not expert_records:
        return inherited_items
    attached = dict(inherited_items or {})
    attached["expert_results"] = expert_result_summary_entries(expert_records)
    return attached


def _coerce_expert_result(raw: Any) -> ExpertResult:
    if isinstance(raw, ExpertResult):
        return raw
    if isinstance(raw, dict):
        return ExpertResult.from_dict(raw)
    raise ValueError("专家结果必须是 ExpertResult 实例或字段字典")


def run_archive_expert_results(
    project_dir: Path,
    results: Any,
    platform: str = "generic",
    silent: bool = False,
) -> Tuple[int, Path]:
    """接收宿主实际执行的专家结果，幂等归档并渲染 Markdown 汇总。"""
    try:
        _validate_boolean_options(silent=silent)
        platform, _, _ = _normalize_audit_options(platform, "solo", "auto")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    if isinstance(results, (str, bytes, bytearray)) or not isinstance(results, (list, tuple)):
        _report_api_error(ValueError("results 必须是专家结果列表"), silent)
        return 3, Path("")
    if not results:
        _report_api_error(
            ValueError("results 不能为空：底层只接收宿主实际执行的专家结果"), silent
        )
        return 3, Path("")
    try:
        parsed = [_coerce_expert_result(item) for item in results]
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    reports_dir = project_dir / "reports"
    store_path = get_expert_result_store_path(reports_dir)
    summary_path = get_expert_summary_path(reports_dir)
    try:
        existing_records = load_expert_result_records(store_path)
        scoped_texts = _load_scope_chapter_texts(project_dir, parsed)
    except (OSError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")

    timestamp = datetime.now(timezone.utc).isoformat()
    incoming: List[Dict[str, Any]] = []
    for result, texts in zip(parsed, scoped_texts):
        missing = any(text is None for text in texts.values())
        current_fingerprint = ""
        if not missing:
            current_fingerprint = compute_text_fingerprint(
                {chapter: text for chapter, text in texts.items()}
            )
        stale = False
        stale_reason = ""
        if result.text_fingerprint:
            if missing:
                stale, stale_reason = True, "无法读取该范围的正文文件，无法核对指纹"
            elif current_fingerprint != result.text_fingerprint:
                stale, stale_reason = True, "正文指纹与当前章节不一致"
        incoming.append(
            build_expert_result_record(
                result,
                current_fingerprint=current_fingerprint,
                stale=stale,
                stale_reason=stale_reason,
                platform=platform,
                recorded_at=timestamp,
            )
        )

    try:
        merged_records, record_changes = merge_expert_result_records(existing_records, incoming)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")
    defect_changes = _merge_expert_defects(audit_state, incoming, platform)

    if not record_changes and not defect_changes:
        # 幂等：重复提交不重复计数，也不重写任何产物或状态。
        return 0, summary_path

    store_content = json.dumps(
        {
            "schema_version": STORE_SCHEMA_VERSION,
            "generated_at": timestamp,
            "results": merged_records,
        },
        ensure_ascii=False,
        indent=2,
    )
    summary_content = render_expert_summary_markdown(merged_records, generated_at=timestamp)
    staged_writes = {store_path: store_content, summary_path: summary_content}
    state_path = get_audit_state_path(reports_dir)
    original_state = _read_optional_bytes(state_path)
    original_artifacts = {path: _read_optional_bytes(path) for path in staged_writes}
    try:
        save_audit_state(audit_state, reports_dir)
        _flush_staged_writes_with_rollback(
            staged_writes, state_path, original_state, original_artifacts
        )
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 专家结果归档失败: {e}", file=sys.stderr)
        return 3, Path("")
    if not silent:
        safe_console_print(f"专家结果 JSON 归档已写入：{store_path}")
        safe_console_print(f"专家结果汇总报告已写入：{summary_path}")
    return 0, summary_path


def _chapter_matches(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-4
    except (TypeError, ValueError, OverflowError):
        return False


def _chapter_current_text_version(project_dir: Path, chapter: float) -> str:
    """读取某章当前正文指纹；章节缺失或无法读取时返回空字符串。"""
    try:
        chapters = ChapterResolver().discover_chapters(project_dir)
        target = _select_unique_chapter(chapters, chapter)
    except (OSError, ValueError):
        return ""
    if target is None:
        return ""
    try:
        text, _, _ = read_file_safe(target.path)
    except Exception:
        return ""
    return _text_content_version(text)


def run_get_pending_rechecks(
    project_dir: Path,
    include_resolved: bool = False,
    include_events: bool = False,
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """查询待办复审：按当前正文重算“当前版本是否已审”，供宿主决定重扫范围。"""
    try:
        _validate_boolean_options(
            include_resolved=include_resolved, include_events=include_events, silent=silent
        )
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, {}
    try:
        audit_state = load_audit_state(project_dir / "reports")
    except Exception as e:
        _report_api_error(e, silent)
        return 3, {}

    pending: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []
    for entry in collect_pending_rechecks(audit_state, include_resolved=True):
        enriched = dict(entry)
        chapter = enriched.get("chapter")
        current_version = (
            _chapter_current_text_version(project_dir, float(chapter))
            if chapter is not None
            else ""
        )
        enriched["current_text_version"] = current_version
        enriched["current_version_audited"] = bool(
            current_version
            and is_chapter_version_audited(audit_state, chapter, current_version)
        )
        # 待办即待复审；current_version_audited 只说明该章自身版本是否仍是最新已审版本。
        enriched["needs_reaudit"] = enriched.get("status") == "pending"
        if enriched.get("status") == "pending":
            pending.append(enriched)
        else:
            resolved.append(enriched)

    payload = {
        "pending": pending,
        "resolved": resolved if include_resolved else [],
        "events": list(audit_state.version_events) if include_events else [],
        "counts": {
            "pending": len(pending),
            "resolved": len(resolved),
            "events": len(audit_state.version_events),
        },
        "affected_chapters": sorted({float(item["chapter"]) for item in pending}),
        "chapter_versions": dict(audit_state.chapter_versions),
    }
    return 0, payload


def run_resolve_recheck(
    project_dir: Path,
    chapter: Optional[float],
    scope: str = "",
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """手工解除待办复审项（作者或实际审查结论），保留解除依据与事件记录。"""
    try:
        _validate_boolean_options(silent=silent)
        _validate_chapter_index(chapter)
        if chapter is None:
            raise ValueError("必须提供章号 (chapter)")
        if not isinstance(scope, str):
            raise ValueError("作用域 (scope) 必须为字符串")
        scope_value = scope.strip()
        if scope_value and scope_value not in VALID_RECHECK_SCOPES:
            raise ValueError("作用域 (scope) 只能是 ''、chapter 或 seam")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须提供解除原因 (reason)")
        if not isinstance(source, str) or source.strip().lower() not in VALID_COORDINATION_SOURCES:
            raise ValueError("解除来源 (source) 只能是 author 或 expert")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    reports_dir = project_dir / "reports"
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")

    def matches(item: Dict[str, Any], only_pending: bool) -> bool:
        if only_pending and item.get("status") != "pending":
            return False
        if not _chapter_matches(item.get("chapter"), chapter):
            return False
        return not scope_value or str(item.get("scope") or "") == scope_value

    if not any(matches(item, True) for item in collect_pending_rechecks(audit_state)):
        # 已解除过的同目标视为幂等；完全没有记录则属于非法目标。
        if any(matches(item, False) for item in collect_pending_rechecks(audit_state, include_resolved=True)):
            return 0, get_audit_state_path(reports_dir)
        if not silent:
            safe_console_print(
                f"[错误] 未找到匹配的待办复审项: 第 {chapter:g} 章 scope={scope_value or '全部'}",
                file=sys.stderr,
            )
        return 3, Path("")

    text_version = _chapter_current_text_version(project_dir, chapter)
    resolved_items = resolve_pending_rechecks(
        audit_state,
        chapter=chapter,
        scope=scope_value or None,
        reason=reason.strip(),
        source=source.strip().lower(),
        resolution="manual_close",
        text_version=text_version,
    )
    for item in resolved_items:
        append_version_event(
            audit_state,
            {
                "type": "recheck_resolved",
                "chapter": float(item.get("chapter")),
                "scope": str(item.get("scope") or ""),
                "recheck_id": str(item.get("id") or ""),
                "trigger_chapter": item.get("trigger_chapter"),
                "previous_text_version": str(item.get("text_version") or ""),
                "text_version": text_version,
                "resolution": "manual_close",
                "reason": reason.strip(),
                "source": source.strip().lower(),
            },
        )
    try:
        state_path = save_audit_state(audit_state, reports_dir)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 保存复审解除记录失败: {e}", file=sys.stderr)
        return 3, Path("")
    if not silent:
        safe_console_print(f"已解除 {len(resolved_items)} 项待办复审：第 {chapter:g} 章")
    return 0, state_path


def run_record_issue_closure(
    project_dir: Path,
    chapter: Optional[float],
    issue: str,
    reason: str,
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """单独登记“问题经复核关闭”事件；命中开放缺陷时同步移入历史，不涉及补丁。"""
    try:
        _validate_boolean_options(silent=silent)
        _validate_chapter_index(chapter)
        if chapter is None:
            raise ValueError("必须提供章号 (chapter)")
        if not isinstance(issue, str) or not issue.strip():
            raise ValueError("必须提供问题陈述 (issue)")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须提供关闭原因 (reason)")
        if not isinstance(source, str) or source.strip().lower() not in VALID_COORDINATION_SOURCES:
            raise ValueError("关闭来源 (source) 只能是 author 或 expert")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    reports_dir = project_dir / "reports"
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")

    issue_value = issue.strip()
    source_value = source.strip().lower()
    reason_value = reason.strip()
    for event in audit_state.version_events:
        if not isinstance(event, dict) or event.get("type") != "issue_closed":
            continue
        if (
            _chapter_matches(event.get("chapter"), chapter)
            and str(event.get("issue") or "") == issue_value
            and str(event.get("reason") or "") == reason_value
            and str(event.get("source") or "") == source_value
        ):
            # 重复登记同一关闭事件：幂等空操作，不改写状态文件。
            return 0, get_audit_state_path(reports_dir)

    text_version = _chapter_current_text_version(project_dir, chapter)
    timestamp = datetime.now(timezone.utc).isoformat()
    matched: List[Dict[str, Any]] = []
    remaining: List[Any] = []
    for item in audit_state.open_defects:
        if (
            isinstance(item, dict)
            and _chapter_matches(item.get("chapter"), chapter)
            and str(item.get("issue") or "") == issue_value
        ):
            matched.append(item)
            continue
        remaining.append(item)
    if matched:
        audit_state.open_defects = remaining
        for item in matched:
            resolved = dict(item)
            resolved["status"] = "resolved"
            resolved["resolution_reason"] = reason_value
            resolved["resolution_source"] = source_value
            resolved["resolved_at"] = timestamp
            if text_version:
                resolved["text_version"] = text_version
            audit_state.resolved_items.append(resolved)
    append_version_event(
        audit_state,
        {
            "type": "issue_closed",
            "chapter": float(chapter),
            "issue": issue_value,
            "reason": reason_value,
            "source": source_value,
            "text_version": text_version,
            "closed_defects": len(matched),
            "at": timestamp,
        },
    )
    try:
        state_path = save_audit_state(audit_state, reports_dir)
    except Exception as e:
        if not silent:
            safe_console_print(f"[错误] 保存问题关闭事件失败: {e}", file=sys.stderr)
        return 3, Path("")
    if not silent:
        safe_console_print(
            f"已登记问题关闭事件：第 {chapter:g} 章 {issue_value}（联动关闭缺陷 {len(matched)} 项）"
        )
    return 0, state_path


def _normalize_asset_event(event: Any, owner: Optional[str] = None) -> Dict[str, Any]:
    """校验并规整资产变更事件；启发式无法判断的语义必须由宿主显式提供。"""
    if not isinstance(event, dict):
        raise ValueError("event 必须是预览返回的候选字典或宿主自建的事件字段字典")

    name = event.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("资产事件必须提供非空名称 (name)")
    name_value = name.strip()

    owner_value = owner if isinstance(owner, str) and owner.strip() else event.get("owner", "主角")
    if not isinstance(owner_value, str) or not owner_value.strip():
        raise ValueError("资产事件必须提供非空所有者 (owner)")

    direction = event.get("direction", "gain")
    if not isinstance(direction, str) or direction.strip().lower() not in ASSET_EVENT_DIRECTIONS:
        raise ValueError("增减方向 (direction) 只能是 gain 或 consume")
    direction_value = direction.strip().lower()

    quantity = event.get("quantity")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, (int, float))
        or not math.isfinite(float(quantity))
        or quantity <= 0
    ):
        raise ValueError("数量 (quantity) 必须是有限的正当数值")

    unit = event.get("unit", "")
    if unit is None:
        unit = ""
    if not isinstance(unit, str):
        raise ValueError("单位 (unit) 必须为字符串")

    chapter = event.get("chapter", event.get("origin_chapter"))
    if (
        isinstance(chapter, bool)
        or not isinstance(chapter, (int, float))
        or not math.isfinite(float(chapter))
        or chapter < 0
    ):
        raise ValueError("章号 (chapter) 必须是有限的非负数值")

    line_number = event.get("line_number")
    if line_number is not None and (
        isinstance(line_number, bool) or not isinstance(line_number, int) or line_number <= 0
    ):
        raise ValueError("位置行号 (line_number) 必须是正整数或 null")
    column = event.get("column")
    if column is not None and (
        isinstance(column, bool) or not isinstance(column, int) or column <= 0
    ):
        raise ValueError("位置列号 (column) 必须是正整数或 null")

    evidence = event.get("evidence", "")
    if evidence is None:
        evidence = ""
    if not isinstance(evidence, str):
        raise ValueError("原文证据 (evidence) 必须为字符串")

    category = event.get("category")
    if category is not None and not isinstance(category, str):
        raise ValueError("资产类别 (category) 必须为字符串或 null")

    candidate_source = "heuristic" if str(event.get("source") or "") == "heuristic" else "host"
    supplied_event_id = event.get("event_id")
    supplied_event_id = supplied_event_id.strip() if isinstance(supplied_event_id, str) else ""
    # 身份与事件编号必须同源：规整（含 owner 覆盖）后一律重算 event_id，
    # 原编号仅作为 supplied_event_id 留痕，避免"返回 0 但未入账"的静默 no-op。
    event_id = make_asset_event_id(
        chapter, line_number, column, name_value, owner_value, direction_value, quantity, unit
    )

    return {
        "event_id": event_id,
        "supplied_event_id": supplied_event_id,
        "name": name_value,
        "owner": owner_value.strip(),
        "direction": direction_value,
        "quantity": quantity,
        "unit": unit.strip(),
        "chapter": float(chapter),
        "line_number": line_number,
        "column": column,
        "evidence": evidence.strip(),
        "category": (category or "").strip(),
        "candidate_source": candidate_source,
    }


def run_preview_asset_changes(
    project_dir: Path,
    scope_str: Optional[str] = None,
    chapter_index: Optional[float] = None,
    owner: Optional[str] = None,
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """候选资产变更预览：只读扫描，不写账本，也不把候选写成既定事实。"""
    try:
        _validate_boolean_options(silent=silent)
        _validate_chapter_index(chapter_index)
        if scope_str is not None and chapter_index is not None:
            raise ValueError("scope_str 与 chapter_index 只能二选一")
        if scope_str is None and chapter_index is None:
            raise ValueError("必须提供 scope_str 或 chapter_index")
        if scope_str is not None and (not isinstance(scope_str, str) or not scope_str.strip()):
            raise ValueError("scope_str 必须是非空字符串或 None")
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise ValueError("owner 必须是非空字符串或 None")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, {}

    json_path, _ = locate_ledger_paths(project_dir)
    try:
        state = load_ledger_state(json_path)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, {}

    chapters = ChapterResolver().discover_chapters(project_dir)
    if not chapters:
        _report_api_error(ValueError("未发现任何章节文件，无法预览候选变更"), silent)
        return 3, {}
    try:
        if scope_str is not None:
            s_min, s_max = parse_scope_range(scope_str)
            targets = [item for item in chapters if s_min <= item.index <= s_max]
        else:
            target = _select_unique_chapter(chapters, chapter_index)
            targets = [target] if target is not None else []
        if not targets:
            raise ValueError("指定范围内未发现章节")
        _validate_unique_targets(targets)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, {}

    owner_value = owner.strip() if isinstance(owner, str) and owner.strip() else "主角"
    candidates: List[Dict[str, Any]] = []
    try:
        for chapter in targets:
            text, _, _ = read_file_safe(chapter.path)
            for change in extract_heuristic_asset_changes(
                text, chapter.index, owner=owner_value
            ):
                existing = find_asset_by_identity(state, change["name"], change["owner"])
                change["existing_asset_id"] = existing.id if existing is not None else ""
                change["existing_quantity"] = existing.quantity if existing is not None else None
                candidates.append(change)
    except (OSError, SafeIOError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}

    counts = {
        "total": len(candidates),
        "gain": sum(1 for item in candidates if item["direction"] == "gain"),
        "consume": sum(1 for item in candidates if item["direction"] == "consume"),
        "matched_existing": sum(1 for item in candidates if item["existing_asset_id"]),
    }
    return 0, {
        "candidates": candidates,
        "chapters": [float(chapter.index) for chapter in targets],
        "counts": counts,
    }


def run_confirm_asset_event(
    project_dir: Path,
    event: Any,
    decision: str = "accept",
    owner: Optional[str] = None,
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """确认或否决候选资产变更，并按 event_id 幂等提交到账本。"""
    try:
        _validate_boolean_options(silent=silent)
        if not isinstance(decision, str) or decision.strip().lower() not in ASSET_EVENT_DECISIONS:
            raise ValueError("裁决 (decision) 只能是 accept 或 reject")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须提供裁决原因 (reason)")
        if not isinstance(source, str) or source.strip().lower() not in VALID_COORDINATION_SOURCES:
            raise ValueError("裁决来源 (source) 只能是 author 或 expert")
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise ValueError("owner 必须是非空字符串或 None")
        payload = _normalize_asset_event(event, owner=owner)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    decision_value = decision.strip().lower()
    source_value = source.strip().lower()
    reason_value = reason.strip()
    json_path, md_path = locate_ledger_paths(project_dir)
    try:
        state = load_ledger_state(json_path)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")

    event_id = payload["event_id"]
    existing_event = next(
        (
            item
            for item in state.asset_events
            if isinstance(item, dict) and str(item.get("event_id") or "") == event_id
        ),
        None,
    )
    if existing_event is not None:
        if str(existing_event.get("decision") or "") == decision_value:
            # 幂等：同一事件重复提交不重复扣账、不重复写流水，也不改写账本。
            return 0, json_path
        _report_api_error(
            ValueError(
                f"事件 {event_id} 已按 {existing_event.get('decision')} 裁决，不能改判为 {decision_value}"
            ),
            silent,
        )
        return 3, Path("")

    asset = find_asset_by_identity(state, payload["name"], payload["owner"])
    unit_value = payload["unit"] or (asset.unit if asset is not None else "个")
    category_value = payload["category"] or (
        asset.category if asset is not None else categorize_asset(payload["name"])
    )
    before: Optional[Union[int, float]] = None
    after: Optional[Union[int, float]] = None
    actual_quantity: Union[int, float] = 0
    over_consume = False
    if decision_value == "reject":
        # 否决只留裁决记录，不改动任何数量。
        pass
    else:
        if asset is None:
            if payload["direction"] == "consume":
                _report_api_error(
                    ValueError(
                        f"资产 {payload['name']}（{payload['owner']}）尚无账本记录，不能直接扣账"
                    ),
                    silent,
                )
                return 3, Path("")
            asset_id = make_confirmed_asset_id(payload["name"], payload["owner"])
            if asset_id in state.assets:
                index = 2
                while f"{asset_id}_{index}" in state.assets:
                    index += 1
                asset_id = f"{asset_id}_{index}"
            asset = AssetItem(
                id=asset_id,
                name=payload["name"],
                category=category_value,
                quantity=0,
                unit=unit_value,
                owner=payload["owner"],
                current_holder=payload["owner"],
                status="ACQUIRED",
                origin_chapter=payload["chapter"],
            )
            state.assets[asset.id] = asset
        before = asset.quantity
        delta: Union[int, float] = (
            payload["quantity"] if payload["direction"] == "gain" else -payload["quantity"]
        )
        # 复用既有数量语义（含归零与丹药耗材耗尽自动流转 CONSUMED）。
        asset.modify_quantity(delta, chapter=payload["chapter"], reason=f"{reason_value}（事件 {event_id}）")
        after = asset.quantity
        # 记录实际变动量：底层对越界消耗会钳制到 0，事件流水必须与账面一致。
        applied = after - before
        actual_quantity = abs(applied) if isinstance(applied, (int, float)) else 0
        over_consume = payload["direction"] == "consume" and payload["quantity"] > before

    state.asset_events.append({
        "event_id": event_id,
        "supplied_event_id": payload["supplied_event_id"],
        "name": payload["name"],
        "owner": payload["owner"],
        "current_holder": asset.current_holder if asset is not None else payload["owner"],
        "category": category_value,
        "unit": unit_value,
        "direction": payload["direction"],
        "quantity": payload["quantity"],
        "chapter": payload["chapter"],
        "line_number": payload["line_number"],
        "column": payload["column"],
        "evidence": payload["evidence"],
        "candidate_source": payload["candidate_source"],
        "decision": decision_value,
        "source": source_value,
        "reason": reason_value,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": asset.id if asset is not None else "",
        "quantity_before": before,
        "quantity_after": after,
        "actual_quantity": actual_quantity,
        "over_consume": bool(over_consume),
        "status_after": asset.status if asset is not None else "",
    })
    state.last_updated_chapter = max(
        float(state.last_updated_chapter or 0.0), float(payload["chapter"])
    )

    try:
        save_ledger_state(state, json_path, md_path, force=False)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")
    if not silent:
        safe_console_print(
            f"资产事件 {event_id} 已按 {decision_value} 提交：{payload['owner']}/{payload['name']}"
        )
    return 0, json_path


def _find_finding_entry(
    audit_state: AuditState,
    finding_id: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """按稳定问题编号查找缺陷条目，返回 (条目, 所在列表名)。"""
    target = str(finding_id or "").strip()
    if not target:
        return None, ""
    for name, items in (("open_defects", audit_state.open_defects), ("resolved_items", audit_state.resolved_items)):
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "") == target:
                return item, name
    return None, ""


def _is_disposition_protected(entry: Dict[str, Any]) -> bool:
    """事实冲突类与平台门禁发现不得由作者偏好自动免除。"""
    category = str(entry.get("category") or "")
    checker = str(entry.get("checker") or "")
    return category in PROTECTED_DISPOSITION_CATEGORIES or checker in PROTECTED_DISPOSITION_CHECKERS


def _disposition_view(record: Dict[str, Any], current_version: str) -> Dict[str, Any]:
    view = dict(record)
    view["current_text_version"] = current_version
    view["needs_reverification"] = bool(record.get("text_version")) and (
        current_version != str(record.get("text_version") or "")
    )
    return view


def _attach_disposition_states(
    inherited_items: Dict[str, Any],
    project_dir: Path,
) -> Dict[str, Any]:
    """把处置记录及“正文变化需重新核验”视图挂入继承栏目，供报告呈现。"""
    records = [
        item
        for item in (inherited_items.get("finding_dispositions") or [])
        if isinstance(item, dict)
    ]
    if not records:
        return inherited_items
    views: Dict[str, Dict[str, Any]] = {}
    for record in records:
        chapter = record.get("chapter")
        current_version = (
            _chapter_current_text_version(project_dir, float(chapter))
            if isinstance(chapter, (int, float)) and not isinstance(chapter, bool)
            else ""
        )
        views[str(record.get("finding_id") or "")] = _disposition_view(record, current_version)
    attached = dict(inherited_items or {})
    attached["finding_dispositions"] = list(views.values())
    annotated: List[Any] = []
    for item in attached.get("open_defects") or []:
        if isinstance(item, dict):
            copy = dict(item)
            view = views.get(str(copy.get("id") or ""))
            if view is not None:
                copy["disposition"] = view
            annotated.append(copy)
        else:
            annotated.append(item)
    attached["open_defects"] = annotated
    return attached


def run_record_finding_disposition(
    project_dir: Path,
    finding_id: str,
    decision: str = "accepted",
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """登记问题处置（接受/暂缓/误报），记录处置时的正文版本。"""
    try:
        _validate_boolean_options(silent=silent)
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise ValueError("必须提供稳定问题编号 (finding_id)")
        if not isinstance(decision, str) or decision.strip().lower() not in DISPOSITION_DECISIONS:
            raise ValueError("处置结论 (decision) 只能是 accepted/deferred/false_positive")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须提供处置原因 (reason)")
        if not isinstance(source, str) or source.strip().lower() not in VALID_COORDINATION_SOURCES:
            raise ValueError("处置来源 (source) 只能是 author 或 expert")
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, Path("")

    decision_value = decision.strip().lower()
    source_value = source.strip().lower()
    reason_value = reason.strip()
    target_id = finding_id.strip()
    reports_dir = project_dir / "reports"
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")

    entry, location = _find_finding_entry(audit_state, target_id)
    if entry is None:
        _report_api_error(
            ValueError(f"未找到问题编号 {target_id}（处置只针对持久化缺陷记录）"), silent
        )
        return 3, Path("")

    chapter = entry.get("chapter")
    current_version = (
        _chapter_current_text_version(project_dir, float(chapter))
        if isinstance(chapter, (int, float)) and not isinstance(chapter, bool)
        else ""
    )
    protected = _is_disposition_protected(entry)
    exempt = decision_value == "false_positive" and not protected
    existing = next(
        (
            item
            for item in audit_state.finding_dispositions
            if isinstance(item, dict) and str(item.get("finding_id") or "") == target_id
        ),
        None,
    )
    if (
        existing is not None
        and str(existing.get("decision") or "") == decision_value
        and str(existing.get("reason") or "") == reason_value
        and str(existing.get("source") or "") == source_value
        and str(existing.get("text_version") or "") == current_version
    ):
        # 相同证据、相同结论、相同正文版本：幂等空操作，不改写状态文件。
        return 0, get_audit_state_path(reports_dir)

    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "finding_id": target_id,
        "chapter": float(chapter) if isinstance(chapter, (int, float)) and not isinstance(chapter, bool) else None,
        "severity": str(entry.get("severity") or ""),
        "category": str(entry.get("category") or ""),
        "issue": str(entry.get("issue") or ""),
        "rule": dict(entry.get("rule")) if isinstance(entry.get("rule"), dict) else {},
        "decision": decision_value,
        "source": source_value,
        "reason": reason_value,
        "decided_at": timestamp,
        "text_version": current_version,
        "exempt": exempt,
        "retained_in_open_defects": not exempt,
        "finding_location": location,
    }
    if existing is not None:
        history = existing.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "decision": str(existing.get("decision") or ""),
                "source": str(existing.get("source") or ""),
                "reason": str(existing.get("reason") or ""),
                "text_version": str(existing.get("text_version") or ""),
                "decided_at": str(existing.get("decided_at") or ""),
            }
        )
        record["history"] = history
        existing.clear()
        existing.update(record)
    else:
        record["history"] = []
        audit_state.finding_dispositions.append(record)

    if exempt and location == "open_defects":
        # 误报且非事实/门禁类：移出开放缺陷，但保留原始发现与严重度作为历史。
        audit_state.open_defects = [
            item for item in audit_state.open_defects if item is not entry
        ]
        resolved = dict(entry)
        resolved["status"] = "resolved"
        resolved["resolution"] = "false_positive_dismissed"
        resolved["resolution_reason"] = reason_value
        resolved["resolution_source"] = source_value
        resolved["resolution_text_version"] = current_version
        resolved["resolved_at"] = timestamp
        audit_state.resolved_items.append(resolved)

    try:
        state_path = save_audit_state(audit_state, reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, Path("")
    if not silent:
        safe_console_print(
            "问题 {} 处置已登记：{}{}".format(
                target_id,
                DISPOSITION_LABELS.get(decision_value, decision_value),
                "（因事实/平台门禁约束仍保留在开放缺陷）" if protected and decision_value == "false_positive" else "",
            )
        )
    return 0, state_path


def run_get_finding_dispositions(
    project_dir: Path,
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """查询问题处置记录，并按当前正文版本标记是否需要重新核验。"""
    try:
        _validate_boolean_options(silent=silent)
    except ValueError as e:
        _report_api_error(e, silent)
        return 3, {}
    reports_dir = project_dir / "reports"
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        _report_api_error(e, silent)
        return 3, {}

    dispositions: List[Dict[str, Any]] = []
    for record in audit_state.finding_dispositions:
        if not isinstance(record, dict):
            continue
        chapter = record.get("chapter")
        current_version = (
            _chapter_current_text_version(project_dir, float(chapter))
            if isinstance(chapter, (int, float)) and not isinstance(chapter, bool)
            else ""
        )
        view = _disposition_view(record, current_version)
        _entry, location = _find_finding_entry(audit_state, str(record.get("finding_id") or ""))
        view["finding_status"] = location or "missing"
        dispositions.append(view)
    return 0, {
        "dispositions": dispositions,
        "counts": {
            "total": len(dispositions),
            "needs_reverification": sum(1 for item in dispositions if item["needs_reverification"]),
            "exempt": sum(1 for item in dispositions if item.get("exempt")),
        },
    }


def get_run_manifest_path(reports_dir: Path, run_id: str) -> Path:
    """流式审查运行清单路径：reports/批量审查/运行清单/{run_id}.json"""
    return reports_dir / "批量审查" / RUN_MANIFEST_DIRNAME / f"{run_id}.json"


def _write_run_manifest(manifest_path: Path, payload: Dict[str, Any]) -> None:
    """原子写入运行清单（进度诊断产物，允许在失败时保留并标注 run_status）。"""
    write_file_safe(manifest_path, json.dumps(payload, ensure_ascii=False, indent=2))


def iter_audit_scope(
    project_dir: Union[str, Path] = ".",
    scope_str: str = "",
    platform: str = "generic",
    genre: str = "auto",
    mode: str = "auto",
    strict: bool = False,
    force: bool = False,
    author_memory: bool = False,
    allow_partial: Optional[bool] = None,
    silent: bool = False,
    on_chapter: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """逐章流式审查入口（F07）

    逐章返回结构化结果（``kind="chapter"``：章号、正文指纹、发现、预审包内容与路径、
    归档报告路径、序号与总数、执行状态），结束时返回一条 ``kind="run_summary"`` 的
    运行汇总；每次运行都会写出 ``reports/批量审查/运行清单/{run_id}.json`` 清单。

    章节报告与预审包沿用既有发布规则（状态优先 + 暂存回滚）：中途失败时已完成章节的
    结果仍会逐章返回供宿主继续专家处理，但报告不会发布，清单会显式标注
    ``run_status="failed"`` 并区分已完成/失败/未执行章节。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        scope_str: 批量范围（如 ``"1-30"``）
        platform/genre/mode/strict/force/author_memory/allow_partial/silent: 与 audit_scope 同名参数
        on_chapter: 可选回调，每产出一章结果时调用一次（回调异常会被忽略，不影响审查）

    Yields:
        Dict[str, Any]: 逐章结果与最终的 ``run_summary`` 汇总项
    """
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = uuid.uuid4().hex[:12]
    summary_item: Dict[str, Any] = {
        "kind": "run_summary",
        "run_id": run_id,
        "phase": "validation_failed",
        "run_status": "failed",
        "exit_code": 3,
        "scope": scope_str if isinstance(scope_str, str) else "",
        "total": 0,
        "completed": 0,
        "failed": 0,
        "not_executed": 0,
        "reports_published": False,
        "manifest_path": "",
        "report_paths": {},
        "error": "",
        "started_at": started_at,
    }
    try:
        p_dir = _resolve_project_dir(project_dir)
        _validate_boolean_options(strict=strict, force=force, silent=silent, author_memory=author_memory)
        if allow_partial is not None:
            _validate_boolean_options(allow_partial=allow_partial)
        platform, mode, genre = _normalize_audit_options(platform, mode, genre)
        s_min, s_max = parse_scope_range(scope_str)
        scope_clean = "".join(scope_str.split())
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        summary_item["error"] = str(e)
        summary_item["finished_at"] = datetime.now(timezone.utc).isoformat()
        yield summary_item
        return

    reports_dir = p_dir / "reports"
    manifest_path = get_run_manifest_path(reports_dir, run_id)
    summary_item["manifest_path"] = str(manifest_path)
    summary_item["scope"] = scope_clean

    def _finish_early(message: str) -> Dict[str, Any]:
        _report_api_error(ValueError(message), silent)
        summary_item["error"] = message
        summary_item["finished_at"] = datetime.now(timezone.utc).isoformat()
        return summary_item

    chapters = ChapterResolver().discover_chapters(p_dir)
    target_chapters = [item for item in chapters if s_min <= item.index <= s_max]
    if not chapters:
        yield _finish_early("未发现任何章节文件")
        return
    if not target_chapters:
        yield _finish_early(f"范围 {scope_clean} 内未发现章节")
        return
    try:
        _validate_unique_targets(target_chapters)
    except ValueError as e:
        yield _finish_early(str(e))
        return
    try:
        audit_state = load_audit_state(reports_dir)
    except Exception as e:
        yield _finish_early(f"读取审计状态失败: {e}")
        return

    total = len(target_chapters)
    effective_allow_partial = allow_partial if allow_partial is not None else bool(s_min > 1.0)
    manifest: Dict[str, Any] = {
        "run_id": run_id,
        "scope": scope_clean,
        "platform": platform,
        "mode": mode,
        "genre": genre,
        "strict": strict,
        "created_at": started_at,
        "updated_at": started_at,
        "run_status": "running",
        "exit_code": None,
        "counts": {"total": total, "completed": 0, "failed": 0, "not_executed": total},
        "chapters": [],
    }
    try:
        _write_run_manifest(manifest_path, manifest)
    except Exception as e:
        summary_item["error"] = f"运行清单写入失败: {e}"

    batch_json_path, batch_md_path = locate_ledger_paths(p_dir)
    stream_ledger_snapshot: Dict[Path, Optional[bytes]] = {
        batch_json_path: _read_optional_bytes(batch_json_path),
        batch_md_path: _read_optional_bytes(batch_md_path),
    }

    def _rollback_stream_ledger() -> None:
        for path, original in stream_ledger_snapshot.items():
            try:
                _restore_optional_bytes(path, original)
            except OSError:
                pass

    def _emit_manifest() -> None:
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _write_run_manifest(manifest_path, manifest)
        except Exception:
            pass

    staged_writes: Dict[Path, Union[str, bytes]] = {}
    has_p0 = False
    has_p1 = False
    for sequence, chap in enumerate(target_chapters, 1):
        item_started = datetime.now(timezone.utc).isoformat()
        summary: Dict[str, Any] = {}
        code = run_audit(
            p_dir,
            target_chapter_index=chap.index,
            strict=strict,
            force=force,
            write_latest_report=False,
            silent=True,
            summary_collector=summary,
            genre=genre,
            mode=mode,
            platform=platform,
            use_author_memory=author_memory,
            inherited_items=None,
            allow_partial=effective_allow_partial,
            _chapter_snapshot=chapters,
            _audit_state=audit_state,
            _staged_writes=staged_writes,
        )
        item_finished = datetime.now(timezone.utc).isoformat()
        bundle = summary.get("pre_bundle") if isinstance(summary.get("pre_bundle"), dict) else {}
        item: Dict[str, Any] = {
            "kind": "chapter",
            "run_id": run_id,
            "manifest_path": str(manifest_path),
            "sequence": sequence,
            "total": total,
            "chapter": float(chap.index),
            "chapter_title": chap.title,
            "text_version": str(summary.get("text_version") or ""),
            "exit_code": code,
            "status": "completed" if code != 3 else "failed",
            "error": "" if code != 3 else f"第 {chap.index:g} 章审查失败（exit_code=3）",
            "findings": [
                finding.to_dict() if hasattr(finding, "to_dict") else finding
                for finding in (summary.get("findings") or [])
            ],
            "p0_list": list(summary.get("p0_list") or []),
            "p1_list": list(summary.get("p1_list") or []),
            "p2_count": int(summary.get("p2_count") or 0),
            "p3_count": int(summary.get("p3_count") or 0),
            "open_defects": list(summary.get("open_defects") or []),
            "word_count": int(summary.get("word_count") or 0),
            "paragraph_count": int(summary.get("paragraph_count") or 0),
            "bundle": bundle,
            "bundle_path": str(summary.get("pre_bundle_path") or ""),
            "report_path": str(summary.get("archived_report_path") or ""),
            "report_published": False,
            "started_at": item_started,
            "finished_at": item_finished,
        }
        if code == 3:
            manifest["chapters"].append({
                "chapter": float(chap.index),
                "sequence": sequence,
                "total": total,
                "status": "failed",
                "text_version": item["text_version"],
                "report_path": item["report_path"],
                "report_published": False,
                "bundle_path": item["bundle_path"],
                "exit_code": 3,
                "started_at": item_started,
                "finished_at": item_finished,
                "error": item["error"],
            })
            for pending in target_chapters[sequence:]:
                manifest["chapters"].append({
                    "chapter": float(pending.index),
                    "sequence": target_chapters.index(pending) + 1,
                    "total": total,
                    "status": "not_executed",
                    "text_version": "",
                    "report_path": "",
                    "report_published": False,
                    "bundle_path": "",
                    "exit_code": None,
                    "started_at": "",
                    "finished_at": "",
                    "error": "",
                })
            manifest["counts"] = {
                "total": total,
                "completed": sequence - 1,
                "failed": 1,
                "not_executed": max(total - sequence, 0),
            }
            manifest["run_status"] = "failed"
            manifest["exit_code"] = 3
            _emit_manifest()
            _rollback_stream_ledger()
            if on_chapter is not None:
                try:
                    on_chapter(item)
                except Exception:
                    pass
            yield item
            summary_item.update({
                "phase": "finished",
                "run_status": "failed",
                "exit_code": 3,
                "completed": sequence - 1,
                "failed": 1,
                "not_executed": max(total - sequence, 0),
                "error": item["error"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            yield summary_item
            return

        if code == 2:
            has_p0 = True
        elif code == 1:
            has_p1 = True
        manifest["chapters"].append({
            "chapter": float(chap.index),
            "sequence": sequence,
            "total": total,
            "status": "completed",
            "text_version": item["text_version"],
            "report_path": item["report_path"],
            "report_published": False,
            "bundle_path": item["bundle_path"],
            "exit_code": code,
            "started_at": item_started,
            "finished_at": item_finished,
            "error": "",
        })
        manifest["counts"] = {
            "total": total,
            "completed": sequence,
            "failed": 0,
            "not_executed": max(total - sequence, 0),
        }
        _emit_manifest()
        if on_chapter is not None:
            try:
                on_chapter(item)
            except Exception:
                pass
        yield item

    # 全部章节审查完成：状态优先 + 暂存回滚发布（沿用 F01 语义）。
    audit_state.last_scope = scope_clean
    for chap in target_chapters:
        if chap.index not in audit_state.completed_chapters:
            audit_state.completed_chapters.append(chap.index)
    audit_state.completed_chapters.sort()
    state_path = get_audit_state_path(reports_dir)
    original_state = _read_optional_bytes(state_path)
    original_artifacts = {path: _read_optional_bytes(path) for path in staged_writes}
    publish_error = ""
    try:
        save_audit_state(audit_state, reports_dir)
        _flush_staged_writes_with_rollback(
            staged_writes, state_path, original_state, original_artifacts
        )
    except Exception as e:
        publish_error = str(e)
        _rollback_stream_ledger()

    exit_code = 0
    if not publish_error:
        exit_code = 2 if has_p0 else (1 if has_p1 and strict else 0)
    run_status = "failed" if publish_error else "completed"
    for entry in manifest["chapters"]:
        entry["report_published"] = bool(run_status == "completed" and entry["status"] == "completed")
    manifest["run_status"] = run_status
    manifest["exit_code"] = exit_code if not publish_error else 3
    _emit_manifest()

    summary_item.update({
        "phase": "finished",
        "run_status": run_status,
        "exit_code": exit_code if not publish_error else 3,
        "total": total,
        "completed": total,
        "failed": 0,
        "not_executed": 0,
        "reports_published": run_status == "completed",
        "report_paths": {str(entry["chapter"]): entry["report_path"] for entry in manifest["chapters"]},
        "error": publish_error,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    yield summary_item


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
    allow_partial: bool = False,
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
    try:
        p_dir = _resolve_project_dir(project_dir)
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
            allow_partial=allow_partial,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
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
    allow_partial: Optional[bool] = None,
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
    try:
        p_dir = _resolve_project_dir(project_dir)
        exit_code = run_scope_audit(
            project_dir=p_dir,
            scope_str=scope_str,
            strict=strict,
            force=force,
            genre=genre,
            mode=mode,
            platform=platform,
            use_author_memory=author_memory,
            silent=silent,
            allow_partial=allow_partial,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    if exit_code == 3:
        return 3, Path("")
    scope_clean = "".join(scope_str.split())
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
    try:
        p_dir = _resolve_project_dir(project_dir)
        return run_init_mode(
            project_dir=p_dir,
            scope_str=scope_str,
            force=force,
            genre=genre,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


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
    try:
        p_dir = _resolve_project_dir(project_dir)
        _validate_boolean_options(force=force, silent=silent)
        return run_checkpoint(project_dir=p_dir, volume=volume, force=force, silent=silent)
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3


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
    try:
        p_dir = _resolve_project_dir(project_dir)
        _validate_boolean_options(force=force, silent=silent)
        return run_sync_from_md(project_dir=p_dir, silent=silent)
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3


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
    try:
        p_dir = _resolve_project_dir(project_dir)
        if patch is not None and not isinstance(patch, (PatchSpec, dict)):
            raise ValueError("patch 必须是 PatchSpec 或 dict")
        if patch_file is not None:
            if not isinstance(patch_file, (str, os.PathLike)) or not str(patch_file).strip():
                raise ValueError("patch_file 必须是补丁文件路径")
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3
    if patch is not None:
        if isinstance(patch, PatchSpec):
            target_line = patch.target_line
            old_text = patch.old_text
            new_text = patch.new_text
            context_before = patch.context_before
            context_after = patch.context_after
        elif isinstance(patch, dict):
            target_line = patch.get("target_line")
            old_text = patch.get("old_text")
            new_text = patch.get("new_text")
            context_before = patch.get("context_before", "")
            context_after = patch.get("context_after", "")

    p_file_str = str(patch_file) if patch_file is not None else None
    try:
        return run_apply_fix(
            project_dir=p_dir,
            chapter_idx=chapter_index,
            target_line=target_line,
            old_text=old_text,
            new_text=new_text,
            context_before=context_before,
            context_after=context_after,
            patch_file=p_file_str,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3


def adjudicate_foreshadowing(
    project_dir: Union[str, Path] = ".",
    name: str = "",
    action: str = "",
    reason: str = "",
    evidence: str = "",
    source: str = "author",
    chapter: Optional[float] = None,
    silent: bool = False,
) -> Tuple[int, Path]:
    """伏笔显式确认 / 关闭 / 重新开启纯 Python API

    受控裁决入口：只登记作者或实际专家审查结果给出的裁决，存储层不自行判断剧情是否已回收。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        name: 伏笔标签名称（对应正文 ``audit:stash`` 标签的 name）
        action: 裁决动作，``confirm``（确认已回收）/``close``（关闭追踪）/``reopen``（重新开启）
        reason: 裁决原因（必填，用于历史追溯）
        evidence: 裁决证据（原文切片或审查结论摘要，可选）
        source: 裁决来源，``author`` 或 ``expert``
        chapter: 关联章号；``reopen`` 必填，用于登记当时的正文版本指纹
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 审计状态文件路径)；参数非法、目标缺失或写入失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_foreshadowing_adjudication(
            project_dir=p_dir,
            name=name,
            action=action,
            reason=reason,
            evidence=evidence,
            source=source,
            chapter=chapter,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def archive_expert_results(
    project_dir: Union[str, Path] = ".",
    results: Optional[Any] = None,
    platform: str = "generic",
    silent: bool = False,
) -> Tuple[int, Path]:
    """专家执行结果汇总归档纯 Python API

    只接收宿主实际执行的专家结果：按“专家 + 范围 + 正文指纹 + 发现身份”幂等归档，
    写入 ``reports/专家审查/expert_results.json`` 与 ``EXPERT_SUMMARY.md``，
    并把未过期、已完成专家结果的 P0/P1 发现并入持久开放缺陷（source=expert）。
    指纹与当前正文不一致的结果会被标记为过期，不参与当前裁决；未执行的审查不会被标记为完成。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        results: ``ExpertResult`` 实例或字段字典组成的列表（至少一条）
        platform: 本次审查对应的平台卡尺（fanqie/qidian/zhihu/generic）
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 专家结果 Markdown 汇总路径)；失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_archive_expert_results(
            project_dir=p_dir,
            results=results,
            platform=platform,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def get_pending_rechecks(
    project_dir: Union[str, Path] = ".",
    include_resolved: bool = False,
    include_events: bool = False,
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """待办复审查询纯 Python API

    返回结构：
        - ``pending``: 待办复审项（按当前正文重算 ``current_version_audited`` /
          ``needs_reaudit``，宿主据此决定重扫范围）
        - ``resolved``: 已解除的复审历史（``include_resolved=True`` 时返回）
        - ``events``: 版本事件（``include_events=True`` 时返回）
        - ``counts`` / ``affected_chapters`` / ``chapter_versions``: 统计与版本快照

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        include_resolved: 是否同时返回已解除的复审历史
        include_events: 是否同时返回补丁/关闭/解除事件
        silent: 是否静默输出

    Returns:
        Tuple[int, Dict[str, Any]]: (状态码, 查询结果)；失败时返回 ``(3, {})``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}
    try:
        return run_get_pending_rechecks(
            project_dir=p_dir,
            include_resolved=include_resolved,
            include_events=include_events,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}


def resolve_recheck(
    project_dir: Union[str, Path] = ".",
    chapter: Optional[float] = None,
    scope: str = "",
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """手工解除待办复审纯 Python API

    与审查自动解除（按当前版本完成审查）互为补充：作者或实际审查结论也可以显式解除，
    解除依据、来源与当时正文版本会写入复审历史与版本事件，重复调用幂等。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        chapter: 目标章号
        scope: 作用域，``''``（该章全部）、``chapter``（本章报告）或 ``seam``（接缝检查）
        reason: 解除原因（必填）
        source: 解除来源，``author`` 或 ``expert``
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 审计状态文件路径)；失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_resolve_recheck(
            project_dir=p_dir,
            chapter=chapter,
            scope=scope,
            reason=reason,
            source=source,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def record_issue_closure(
    project_dir: Union[str, Path] = ".",
    chapter: Optional[float] = None,
    issue: str = "",
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """登记“问题经复核关闭”事件纯 Python API

    与补丁事件分离：只登记复核结论，不触发回写。若匹配到同章的开放缺陷，会把该缺陷
    移入 ``resolved_items`` 历史并保留关闭依据；重复登记同一事件幂等。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        chapter: 关联章号
        issue: 问题陈述（与开放缺陷的 issue 对齐时联动关闭）
        reason: 关闭原因（必填）
        source: 关闭来源，``author`` 或 ``expert``
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 审计状态文件路径)；失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_record_issue_closure(
            project_dir=p_dir,
            chapter=chapter,
            issue=issue,
            reason=reason,
            source=source,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def preview_asset_changes(
    project_dir: Union[str, Path] = ".",
    scope_str: Optional[str] = None,
    chapter_index: Optional[float] = None,
    owner: Optional[str] = None,
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """资产候选变更预览纯 Python API

    只读扫描指定范围，返回结构化候选（event_id、名称、所有者、增减方向与数量、单位、
    章号、行号、原文证据、来源），并附带账本中同身份资产的现状。预览不会修改
    ``资源账本.json/.md``，候选也不会被当作既定事实；是否入账必须由宿主或作者显式确认。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        scope_str: 扫描范围（如 ``"1-3"``，与 chapter_index 二选一）
        chapter_index: 单章号（与 scope_str 二选一）
        owner: 候选归属所有者覆盖值（默认 ``主角``，启发式无法判断归属）
        silent: 是否静默输出

    Returns:
        Tuple[int, Dict[str, Any]]: (状态码, ``{"candidates", "chapters", "counts"}``)；
        失败时返回 ``(3, {})``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}
    try:
        return run_preview_asset_changes(
            project_dir=p_dir,
            scope_str=scope_str,
            chapter_index=chapter_index,
            owner=owner,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}


def confirm_asset_event(
    project_dir: Union[str, Path] = ".",
    event: Optional[Any] = None,
    decision: str = "accept",
    owner: Optional[str] = None,
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """资产变更确认/否决纯 Python API（按 event_id 幂等提交）

    接受预览返回的候选字典，也接受宿主自建的事件字段字典（名称、方向、数量、章号等）。
    ``accept`` 会复用 ``AssetItem.modify_quantity`` / ``transition`` 既有语义入账，
    ``reject`` 只登记裁决不改动数量；同一 event_id 重复提交不重复加账/扣账，改判返回错误码。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        event: 候选事件字典（预览结果或宿主自建字段）
        decision: ``accept`` 或 ``reject``
        owner: 所有者覆盖值（默认取事件内的 owner，再回退 ``主角``）
        reason: 裁决原因（必填）
        source: 裁决来源，``author`` 或 ``expert``
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 账本 JSON 路径)；失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_confirm_asset_event(
            project_dir=p_dir,
            event=event,
            decision=decision,
            owner=owner,
            reason=reason,
            source=source,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def record_finding_disposition(
    project_dir: Union[str, Path] = ".",
    finding_id: str = "",
    decision: str = "accepted",
    reason: str = "",
    source: str = "author",
    silent: bool = False,
) -> Tuple[int, Path]:
    """问题处置登记纯 Python API（F08）

    按稳定问题编号登记作者/专家处置（``accepted`` 接受 / ``deferred`` 暂缓 /
    ``false_positive`` 误报），记录处置时的正文版本与原因。事实冲突类
    （``causal``/``factual``/``consistency``）与平台门禁发现不允许被自动免除：
    这类处置会被记录但缺陷仍保留在 ``open_defects``；其余发现的 ``false_positive``
    处置会把缺陷移入历史并保留原始严重度与证据。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        finding_id: 持久化缺陷的稳定编号（``open_defects``/``resolved_items`` 中的 ``id``）
        decision: ``accepted`` / ``deferred`` / ``false_positive``
        reason: 处置原因（必填）
        source: 处置来源，``author`` 或 ``expert``
        silent: 是否静默输出

    Returns:
        Tuple[int, Path]: (状态码, 审计状态文件路径)；失败时返回 ``(3, Path(""))``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")
    try:
        return run_record_finding_disposition(
            project_dir=p_dir,
            finding_id=finding_id,
            decision=decision,
            reason=reason,
            source=source,
            silent=silent,
        )
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, Path("")


def get_finding_dispositions(
    project_dir: Union[str, Path] = ".",
    silent: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    """问题处置查询纯 Python API（F08）

    返回全部处置记录及规则元数据快照，并按当前正文指纹计算
    ``needs_reverification``：正文与处置时版本不一致的记录必须重新核验。

    Args:
        project_dir: 小说项目根目录（Path 或 str，默认当前目录）
        silent: 是否静默输出

    Returns:
        Tuple[int, Dict[str, Any]]: (状态码, ``{"dispositions", "counts"}``)；
        失败时返回 ``(3, {})``
    """
    try:
        p_dir = _resolve_project_dir(project_dir)
    except (OSError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}
    try:
        return run_get_finding_dispositions(project_dir=p_dir, silent=silent)
    except (OSError, SafeIOError, TypeError, ValueError) as e:
        _report_api_error(e, silent)
        return 3, {}


__all__ = [
    # 核心纯 Python API
    "audit_chapter",
    "audit_scope",
    "init_ledger",
    "checkpoint_volume",
    "sync_ledger_from_md",
    "apply_fix",
    # F03/F04 新增受控入口
    "adjudicate_foreshadowing",
    "archive_expert_results",
    "ExpertResult",
    "compute_text_fingerprint",
    "load_expert_result_records",
    "get_expert_result_store_path",
    "get_expert_summary_path",
    # F05 版本感知与复审协调入口
    "get_pending_rechecks",
    "resolve_recheck",
    "record_issue_closure",
    "is_chapter_version_audited",
    "get_chapter_version",
    "get_report_history_path",
    # F06 资产候选变更与确认入口
    "preview_asset_changes",
    "confirm_asset_event",
    # F07/F08 流式审查与问题处置入口
    "iter_audit_scope",
    "get_run_manifest_path",
    "record_finding_disposition",
    "get_finding_dispositions",
    # 底层执行管线与别名兼容
    "run_audit",
    "run_scope_audit",
    "run_checkpoint",
    "run_sync_from_md",
    "run_init_mode",
    "run_apply_fix",
    "run_foreshadowing_adjudication",
    "run_archive_expert_results",
    "run_get_pending_rechecks",
    "run_resolve_recheck",
    "run_record_issue_closure",
    "run_preview_asset_changes",
    "run_confirm_asset_event",
    "run_record_finding_disposition",
    "run_get_finding_dispositions",
    # 预审包与报告生成
    "build_pre_audit_bundle",
    "render_audit_report",
    "render_scope_batch_summary",
    "locate_ledger_paths",
    "load_ledger_state",
    "get_report_archive_path",
    "parse_scope_range",
    "detect_violations_in_text",
    "safe_console_print",
]
