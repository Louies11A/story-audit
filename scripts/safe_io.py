"""
统一安全文件 I/O 与原子备份器 (safe_io.py)

功能职责：
1. 自动嗅探文件编码（utf-8-sig -> utf-8 -> gb18030）；
2. 识别与在内存中规整换行符（CRLF 及孤立 CR 规整为 LF）；
3. 严格无损还原原稿换行与编码写入，采用同目录临时文件原子替换；
4. 原子生成镜像备份，支持时间戳碰撞自动递增，杜绝主流程异常导致的原稿损毁。
"""

import codecs
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union


class SafeIOError(Exception):
    """SafeIO 基础异常类"""
    pass


class SafeIOReadError(SafeIOError):
    """文件读取、解码或 I/O 探测失败异常"""
    pass


class SafeIOWriteError(SafeIOError):
    """文件写入、原子替换或目录创建失败异常"""
    pass


__all__ = [
    "SafeIOError",
    "SafeIOReadError",
    "SafeIOWriteError",
    "MAX_SAFE_FILE_SIZE",
    "read_file_safe",
    "write_file_safe",
    "create_atomic_backup",
]


MAX_SAFE_FILE_SIZE = 20 * 1024 * 1024  # 20MB 上限


def read_file_safe(
    path: Union[Path, str],
    max_size: int = MAX_SAFE_FILE_SIZE,
) -> Tuple[str, str, str]:
    """安全读取文件并探测编码与换行符。

    增加文件大小上限（默认 20MB）及二进制空字节 \x00 探测。
    按顺序尝试解码：utf-8-sig -> utf-8 -> gb18030
    换行符识别：若包含 \r\n 则 detected_newline 为 "\r\n"，否则为 "\n"
    内存规整：将读取的内容在内存中把 CRLF 与孤立 CR 统一规整为 \n

    Args:
        path: 文件路径（Path 或字符串）
        max_size: 最大允许读取的文件字节大小，默认 20MB

    Returns:
        (normalized_content, detected_encoding, detected_newline)

    Raises:
        SafeIOReadError: 文件不存在、超大、包含二进制空字节或所有编码尝试均失败时抛出
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SafeIOReadError(f"文件不存在或不是普通文件: {file_path}")

    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        raise SafeIOReadError(f"获取文件状态失败 {file_path}: {e}") from e

    if file_size > max_size:
        raise SafeIOReadError(
            f"文件大小超出上限 ({file_size} 字节 > {max_size} 字节): {file_path}"
        )

    try:
        raw_bytes = file_path.read_bytes()
    except OSError as e:
        raise SafeIOReadError(f"读取文件失败 {file_path}: {e}") from e

    if b"\x00" in raw_bytes:
        raise SafeIOReadError(
            f"文件疑似二进制文件（包含空字节 \\x00），拒绝读取: {file_path}"
        )

    decoded: Union[str, None] = None
    detected_encoding: str = ""

    # 1. 优先探测 UTF-8 BOM
    if raw_bytes.startswith(codecs.BOM_UTF8):
        try:
            decoded = raw_bytes.decode("utf-8-sig")
            detected_encoding = "utf-8-sig"
        except UnicodeDecodeError:
            decoded = None

    # 2. 回退顺序：utf-8 -> gb18030
    if decoded is None:
        for enc in ("utf-8", "gb18030"):
            try:
                decoded = raw_bytes.decode(enc)
                detected_encoding = enc
                break
            except UnicodeDecodeError:
                continue

    if decoded is None:
        raise SafeIOReadError(
            f"无法通过探测编码 (utf-8-sig, utf-8, gb18030) 解码文件: {file_path}"
        )

    # 3. 换行符识别
    detected_newline = "\r\n" if "\r\n" in decoded else "\n"

    # 4. 内存规整为 LF（包含孤立 \r 彻底清除）
    normalized_content = re.sub(r"\r\n|\r", "\n", decoded)

    return normalized_content, detected_encoding, detected_newline


def write_file_safe(
    path: Union[Path, str],
    content: str,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> None:
    """安全原子写入文件，自动创建父目录并精确保持换行与编码。

    采用同目录临时文件 + fsync + os.replace 原子替换机制，杜绝直接截断目标文件风险。

    Args:
        path: 目标文件路径（Path 或字符串）
        content: 待写入内容（内存中通常为 \n 规整格式）
        encoding: 目标编码（默认 utf-8）
        newline: 目标换行符（"\n" 或 "\r\n"，默认 "\n"）

    Raises:
        SafeIOWriteError: 写入失败、原子替换失败或目录创建失败时抛出
    """
    target_path = Path(path)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SafeIOWriteError(f"创建目标父目录失败 {target_path.parent}: {e}") from e

    # 格式化目标换行符（孤立 \r 统一归整）
    if newline == "\r\n":
        final_content = re.sub(r"\r\n|\r", "\n", content).replace("\n", "\r\n")
    else:
        final_content = re.sub(r"\r\n|\r", "\n", content)

    temp_path_str: Union[str, None] = None
    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=target_path.parent,
            prefix=f".tmp_{target_path.stem}_",
            suffix=".tmp",
        )
        with os.fdopen(temp_fd, "w", encoding=encoding, newline="") as f:
            f.write(final_content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path_str, target_path)
    except Exception as e:
        if temp_path_str and os.path.exists(temp_path_str):
            try:
                os.remove(temp_path_str)
            except OSError:
                pass
        if isinstance(e, SafeIOWriteError):
            raise
        raise SafeIOWriteError(f"写入文件失败 {target_path}: {e}") from e


def create_atomic_backup(
    source_path: Union[Path, str],
    backup_dir: Union[Path, str],
) -> Path:
    """创建源文件的原子镜像备份。

    备份文件名格式：
    首次备份：{source_path.stem}_{timestamp}{source_path.suffix}.bak
    若已存在同名备份：{source_path.stem}_{timestamp}_{counter:02d}{source_path.suffix}.bak
    时间戳格式：YYYYMMDD_HHMMSS

    Args:
        source_path: 源文件路径（Path 或字符串）
        backup_dir: 备份存储目录（Path 或字符串）

    Returns:
        生成的备份文件 Path

    Raises:
        FileNotFoundError: 源文件不存在或不是普通文件时抛出
        SafeIOWriteError: 备份创建、写入或替换失败时抛出
    """
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在或非普通文件: {src}")

    dst_dir = Path(backup_dir)
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SafeIOWriteError(f"创建备份目录失败 {dst_dir}: {e}") from e

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{src.stem}_{timestamp}{src.suffix}.bak"
    target_backup_path = dst_dir / base_filename

    # 时间戳碰撞防御：若同名备份文件已存在，自增后缀 _01, _02...
    if target_backup_path.exists():
        counter = 1
        while True:
            candidate_name = f"{src.stem}_{timestamp}_{counter:02d}{src.suffix}.bak"
            candidate_path = dst_dir / candidate_name
            if not candidate_path.exists():
                target_backup_path = candidate_path
                break
            counter += 1

    temp_path_str: Union[str, None] = None
    try:
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=dst_dir,
            prefix=f".tmp_{src.stem}_",
            suffix=".tmp",
        )
        with os.fdopen(temp_fd, "wb") as temp_file:
            with open(src, "rb") as src_file:
                while True:
                    chunk = src_file.read(64 * 1024)
                    if not chunk:
                        break
                    temp_file.write(chunk)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path_str, target_backup_path)
    except Exception as e:
        if temp_path_str and os.path.exists(temp_path_str):
            try:
                os.remove(temp_path_str)
            except OSError:
                pass
        if isinstance(e, (FileNotFoundError, SafeIOError)):
            raise
        raise SafeIOWriteError(f"创建备份失败 {target_backup_path}: {e}") from e

    return target_backup_path
