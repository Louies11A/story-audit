"""
统一安全文件 I/O 与原子备份器 (safe_io.py)

功能职责：
1. 自动嗅探文件编码（utf-8-sig -> utf-8 -> gb18030）；
2. 识别与在内存中规整换行符（CRLF 规整为 LF）；
3. 严格无损还原原稿换行与编码写入；
4. 原子生成镜像备份，杜绝主流程异常导致的原稿损毁。
"""

import codecs
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union


class SafeIOReadError(Exception):
    """文件读取、解码或 I/O 探测失败异常"""
    pass


def read_file_safe(path: Union[Path, str]) -> Tuple[str, str, str]:
    """安全读取文件并探测编码与换行符。

    按顺序尝试解码：utf-8-sig -> utf-8 -> gb18030
    换行符识别：若包含 \\r\\n 则 detected_newline 为 "\\r\\n"，否则为 "\\n"
    内存规整：将读取的内容在内存中统一把 \\r\\n 规整为 \\n

    Args:
        path: 文件路径（Path 或字符串）

    Returns:
        (normalized_content, detected_encoding, detected_newline)

    Raises:
        SafeIOReadError: 文件不存在、无法读取或所有编码尝试均失败时抛出
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SafeIOReadError(f"文件不存在或不是普通文件: {file_path}")

    try:
        raw_bytes = file_path.read_bytes()
    except OSError as e:
        raise SafeIOReadError(f"读取文件失败 {file_path}: {e}") from e

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

    # 4. 内存规整为 LF
    normalized_content = decoded.replace("\r\n", "\n")

    return normalized_content, detected_encoding, detected_newline


def write_file_safe(
    path: Union[Path, str],
    content: str,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> None:
    """安全写入文件，自动创建父目录并精确保持换行与编码。

    Args:
        path: 目标文件路径
        content: 待写入内容（内存中通常为 \\n 规整格式）
        encoding: 目标编码（默认 utf-8）
        newline: 目标换行符（"\\n" 或 "\\r\\n"，默认 "\\n"）
    """
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 格式化目标换行符
    if newline == "\r\n":
        final_content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        final_content = content.replace("\r\n", "\n")

    # 使用 newline="" 禁用标准库的跨平台换行隐式转换，由 final_content 精确控制
    with open(target_path, "w", encoding=encoding, newline="") as f:
        f.write(final_content)


def create_atomic_backup(
    source_path: Union[Path, str],
    backup_dir: Union[Path, str],
) -> Path:
    """创建源文件的原子镜像备份。

    备份文件名格式：{source_path.stem}_{timestamp}{source_path.suffix}.bak
    时间戳格式：YYYYMMDD_HHMMSS

    Args:
        source_path: 源文件路径
        backup_dir: 备份存储目录

    Returns:
        生成的备份文件绝对/相对 Path

    Raises:
        FileNotFoundError: 源文件不存在时抛出
        OSError: 备份创建失败时抛出
    """
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在或非普通文件: {src}")

    dst_dir = Path(backup_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{src.stem}_{timestamp}{src.suffix}.bak"
    target_backup_path = dst_dir / backup_filename

    # 使用同目录临时文件写入并原子重命名替换，保证备份生成过程具有原子性
    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=dst_dir,
        prefix=f".tmp_{src.stem}_",
        suffix=".tmp",
    )
    try:
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
    except Exception:
        if os.path.exists(temp_path_str):
            try:
                os.remove(temp_path_str)
            except OSError:
                pass
        raise

    return target_backup_path
