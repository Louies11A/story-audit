"""
三行锚点邻域消歧安全回写器 (safe_writer.py)

功能职责：
1. 严格使用 Python 3.8+ 标准库与项目基础设施（scripts.safe_io, scripts.types.PatchSpec）；
2. 探测原文件编码与换行符，并在最终写回时严格保真；
3. 强制在修改前创建原子镜像备份至指定或默认 backup_dir；
4. 实现三行锚点对齐匹配：支持单行精确匹配与跨行顺序邻近匹配（允许段落间空行）；
5. 实现带边界截断的局部邻域消歧机制（[max(0, target_line - 30 - 1), min(len(lines), target_line + 30)]）：
   - 全章匹配数 == 0 -> PatchAnchorNotFoundError
   - 全章匹配数 == 1 -> 直接定位替换
   - 全章匹配数 > 1  -> 局部邻域消歧：
     - 局部邻域内恰有 1 个匹配 -> 成功定位替换
     - 局部邻域内仍有多重匹配或无匹配 -> AmbiguousPatchError，坚决拒绝篡改原稿。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from scripts.safe_io import (
    create_atomic_backup,
    read_file_safe,
    write_file_safe,
)
from scripts.types import PatchSpec

__all__ = [
    "SafeWriterError",
    "AmbiguousPatchError",
    "PatchAnchorNotFoundError",
    "apply_patch_with_disambiguation",
]


class SafeWriterError(Exception):
    """安全回写器基础异常"""
    pass


class AmbiguousPatchError(SafeWriterError):
    """补丁锚点存在歧义（多重匹配或邻域消歧失败）异常"""
    pass


class PatchAnchorNotFoundError(SafeWriterError):
    """未找到补丁锚点异常"""
    pass


@dataclass(frozen=True)
class _PatchMatch:
    """描述一个潜在的补丁匹配位置"""
    line_idx: int     # 0-based 行索引
    start_pos: int    # 在该行文本中的起始字符偏移
    end_pos: int      # 在该行文本中的结束字符偏移
    match_type: str   # "single_line" | "three_line"


def _find_single_line_matches(lines: List[str], patch: PatchSpec) -> List[_PatchMatch]:
    """在单行内部检索包含 context_before, old_text, context_after 的精确匹配"""
    matches: List[_PatchMatch] = []
    old_text = patch.old_text
    if not old_text:
        return matches

    cb = patch.context_before
    ca = patch.context_after

    for i, line in enumerate(lines):
        if old_text not in line:
            continue

        start_search = 0
        while True:
            pos = line.find(old_text, start_search)
            if pos == -1:
                break
            end_pos = pos + len(old_text)

            # 验证前置锚点：如果在同一行，cb 必须出现在 old_text 之前
            cb_ok = True
            if cb:
                if cb not in line[:pos] and cb.strip() not in line[:pos]:
                    cb_ok = False

            # 验证后置锚点：如果在同一行，ca 必须出现在 old_text 之后
            ca_ok = True
            if ca:
                if ca not in line[end_pos:] and ca.strip() not in line[end_pos:]:
                    ca_ok = False

            if cb_ok and ca_ok:
                matches.append(
                    _PatchMatch(
                        line_idx=i,
                        start_pos=pos,
                        end_pos=end_pos,
                        match_type="single_line",
                    )
                )

            start_search = pos + 1

    return matches


def _find_three_line_matches(lines: List[str], patch: PatchSpec) -> List[_PatchMatch]:
    """在顺序邻近行中检索包含 context_before, old_text, context_after 的三行结构（允许段间空行）"""
    matches: List[_PatchMatch] = []
    old_text = patch.old_text
    if not old_text:
        return matches

    cb = patch.context_before
    ca = patch.context_after

    for i, line in enumerate(lines):
        if old_text not in line:
            continue

        # 1. 检验前置锚点（向前寻找最近的非空行）
        cb_ok = True
        if cb:
            prev_idx = i - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            if prev_idx < 0:
                cb_ok = False
            else:
                prev_line = lines[prev_idx]
                if cb not in prev_line and cb.strip() not in prev_line:
                    cb_ok = False

        if not cb_ok:
            continue

        # 2. 检验后置锚点（向后寻找最近的非空行）
        ca_ok = True
        if ca:
            next_idx = i + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            if next_idx >= len(lines):
                ca_ok = False
            else:
                next_line = lines[next_idx]
                if ca not in next_line and ca.strip() not in next_line:
                    ca_ok = False

        if not ca_ok:
            continue

        # 3. 前后锚点均满足，记录该行中所有的 old_text 出现点
        start_search = 0
        while True:
            pos = line.find(old_text, start_search)
            if pos == -1:
                break
            end_pos = pos + len(old_text)
            matches.append(
                _PatchMatch(
                    line_idx=i,
                    start_pos=pos,
                    end_pos=end_pos,
                    match_type="three_line",
                )
            )
            start_search = pos + 1

    return matches


def _find_all_matches(lines: List[str], patch: PatchSpec) -> List[_PatchMatch]:
    """汇总单行与三行匹配项并去重保持物理顺序"""
    single_matches = _find_single_line_matches(lines, patch)
    three_matches = _find_three_line_matches(lines, patch)

    seen = set()
    unique_matches: List[_PatchMatch] = []
    for m in single_matches + three_matches:
        key = (m.line_idx, m.start_pos, m.end_pos)
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)

    unique_matches.sort(key=lambda x: (x.line_idx, x.start_pos))
    return unique_matches


def apply_patch_with_disambiguation(
    file_path: Union[Path, str],
    patch: PatchSpec,
    backup_dir: Optional[Path] = None,
) -> bool:
    """带行号邻域消歧与原子备份的安全回写器。

    执行流程：
    1. 使用 read_file_safe 读取原文件，探测其真实 encoding 与 newline；
    2. 检索三行锚点对齐结构（兼容单行精确匹配与跨行顺序邻近匹配）；
    3. 全章匹配数判定与邻域消歧约束：
       - 若全章匹配数 == 0：抛出 PatchAnchorNotFoundError；
       - 若全章匹配数 == 1：直接定位成功；
       - 若全章匹配数 > 1：进入局部邻域消歧：
         邻域截断范围 [max(0, target_line - 30 - 1), min(len(lines), target_line + 30)]；
         若局部邻域内恰有 1 个匹配，成功消歧定位；
         若局部邻域内仍有多重匹配或无匹配，抛出 AmbiguousPatchError 坚决拒绝篡改原稿；
    4. 强制原子备份：调用 create_atomic_backup 生成镜像备份至 backup_dir；
    5. 执行内存替换，调用 write_file_safe 按原始 encoding 与 newline 原子落盘写入。

    Args:
        file_path: 待修改的目标文件路径
        patch: 补丁规范规格对象
        backup_dir: 可选备份目录，默认为 file_path.parent / "reports" / ".bak"

    Returns:
        bool: 成功修改落盘返回 True

    Raises:
        PatchAnchorNotFoundError: 全章未找到任何符合锚点的匹配
        AmbiguousPatchError: 全章有多重匹配且局部邻域无法唯一消歧
        SafeWriterError / SafeIOError: 读取或写入底层异常
    """
    target_file = Path(file_path)

    # 1. 安全读取并嗅探编码与换行符
    normalized_content, detected_encoding, detected_newline = read_file_safe(target_file)
    lines = normalized_content.split("\n")

    # 2. 检索所有匹配候选
    all_matches = _find_all_matches(lines, patch)
    total_count = len(all_matches)

    selected_match: _PatchMatch

    if total_count == 0:
        raise PatchAnchorNotFoundError(
            f"未在文件 {target_file.name} 中检索到匹配的锚点补丁: {patch}"
        )
    elif total_count == 1:
        selected_match = all_matches[0]
    else:
        # 3. 邻域消歧约束（Neighborhood Disambiguation）
        # 计算 0-based 切片范围，彻底杜绝负索引越界
        start_idx = max(0, patch.target_line - 30 - 1)
        end_idx = max(0, min(len(lines), patch.target_line + 30))

        local_matches = [
            m for m in all_matches
            if start_idx <= m.line_idx < end_idx
        ]

        if len(local_matches) == 1:
            selected_match = local_matches[0]
        elif len(local_matches) == 0:
            raise AmbiguousPatchError(
                f"全章存在 {total_count} 处匹配，但在建议行号 {patch.target_line} 的局部邻域 "
                f"[{start_idx + 1}, {end_idx}] 内无匹配，拒绝修改原稿"
            )
        else:
            raise AmbiguousPatchError(
                f"全章存在 {total_count} 处匹配，且在建议行号 {patch.target_line} 的局部邻域 "
                f"[{start_idx + 1}, {end_idx}] 内仍存在 {len(local_matches)} 处多重匹配，无法唯一消歧，拒绝修改原稿"
            )

    # 4. 强制原子备份（在正式修改写入前必须执行）
    if backup_dir is None:
        actual_backup_dir = target_file.parent / "reports" / ".bak"
    else:
        actual_backup_dir = Path(backup_dir)

    create_atomic_backup(target_file, actual_backup_dir)

    # 5. 执行内存替换
    tgt_idx = selected_match.line_idx
    old_line = lines[tgt_idx]
    new_line = (
        old_line[:selected_match.start_pos]
        + patch.new_text
        + old_line[selected_match.end_pos:]
    )
    lines[tgt_idx] = new_line
    new_content = "\n".join(lines)

    # 6. 安全原子写回，严格保持原始编码与换行符
    write_file_safe(
        target_file,
        new_content,
        encoding=detected_encoding,
        newline=detected_newline,
    )

    return True
