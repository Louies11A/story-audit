# -*- coding: utf-8 -*-
"""Batch 2: 安全 I/O、Windows 鲁棒性与生成器中断保护测试集"""

import codecs
import json
import os
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

import pytest

from scripts import safe_io
from scripts.expert_results import (
    STORE_SCHEMA_VERSION,
    get_expert_result_store_path,
    load_expert_result_records,
)
from scripts.safe_io import (
    SafeIOError,
    SafeIOReadError,
    SafeIOWriteError,
    create_atomic_backup,
    read_file_safe,
    write_file_safe,
)
from scripts import story_audit
from scripts.story_audit import audit_scope_stream, iter_audit_scope, run_build_context_package


# =========================================================================
# 1. SafeIOError 继承层级测试 (P2-10)
# =========================================================================
def test_safe_io_error_inheritance():
    """SafeIOError 必须继承自 OSError，兼容标准 I/O 异常捕获"""
    assert issubclass(SafeIOError, OSError)
    assert issubclass(SafeIOReadError, SafeIOError)
    assert issubclass(SafeIOWriteError, SafeIOError)
    assert issubclass(SafeIOReadError, OSError)
    assert issubclass(SafeIOWriteError, OSError)

    # 验证能被标准 except OSError 捕获
    caught = False
    try:
        raise SafeIOReadError("测试读取错误")
    except OSError as e:
        caught = True
        assert "测试读取错误" in str(e)
    assert caught


# =========================================================================
# 2. P0-02: GB18030 乱码吞噬损坏 UTF-8 BOM 文件防护测试
# =========================================================================
def test_utf8_bom_corrupted_rejects_gb18030_fallback(tmp_path):
    """带 UTF-8 BOM 但字节损坏的文件严禁回退到 GB18030，必须直接抛出 SafeIOReadError"""
    bad_file = tmp_path / "corrupted_utf8_bom.txt"
    # 写入 UTF-8 BOM + 损坏的字节（无法以 UTF-8 解码）
    bad_file.write_bytes(codecs.BOM_UTF8 + b"\xff\xfe\xaa\xbb")

    with pytest.raises(SafeIOReadError) as exc_info:
        read_file_safe(bad_file)

    msg = str(exc_info.value)
    assert "文件包含 UTF-8 BOM 但字节序列损坏，拒绝回退至 GB18030 以防止乱码破坏原稿" in msg


def test_utf8_bom_valid_decodes_correctly(tmp_path):
    """合法的 UTF-8 BOM 文件必须正确解码为 utf-8-sig"""
    good_file = tmp_path / "valid_utf8_bom.txt"
    content = "第一章 正文内容\r\n第二行内容"
    good_file.write_bytes(codecs.BOM_UTF8 + content.encode("utf-8"))

    normalized, encoding, newline = read_file_safe(good_file)
    assert normalized == "第一章 正文内容\n第二行内容"
    assert encoding == "utf-8-sig"
    assert newline == "\r\n"


def test_gb18030_normal_file_decodes_successfully(tmp_path):
    """不含 BOM 的纯 GB18030 文件仍能正常回退解码"""
    gbk_file = tmp_path / "normal_gbk.txt"
    content = "第一章 中文小说正文\r\n陆沉拔剑出鞘"
    gbk_file.write_bytes(content.encode("gb18030"))

    normalized, encoding, newline = read_file_safe(gbk_file)
    assert normalized == "第一章 中文小说正文\n陆沉拔剑出鞘"
    assert encoding == "gb18030"
    assert newline == "\r\n"


# =========================================================================
# 3. P0-03: Windows 平台 os.replace 短时文件占用重试测试
# =========================================================================
def test_atomic_replace_with_retry_windows_retries_on_permission_error():
    """在 win32 平台下遇到 PermissionError 会进行退避重试"""
    assert hasattr(safe_io, "_atomic_replace_with_retry"), "safe_io 模块中应定义 _atomic_replace_with_retry"
    calls = []

    def mock_replace(src, dst):
        calls.append(len(calls))
        if len(calls) < 3:
            raise PermissionError("文件被短暂占用")
        return None

    with patch("sys.platform", "win32"):
        with patch("os.replace", side_effect=mock_replace):
            with patch("time.sleep") as mock_sleep:
                safe_io._atomic_replace_with_retry("src.tmp", "dst.txt", max_retries=5)

    assert len(calls) == 3
    assert mock_sleep.call_count == 2
    # 退避时间 0.01 * 2^0 = 0.01, 0.01 * 2^1 = 0.02
    mock_sleep.assert_has_calls([call(0.01), call(0.02)])


def test_atomic_replace_with_retry_exceeds_max_retries():
    """重试超限后必须抛出 PermissionError"""
    assert hasattr(safe_io, "_atomic_replace_with_retry"), "safe_io 模块中应定义 _atomic_replace_with_retry"
    with patch("sys.platform", "win32"):
        with patch("os.replace", side_effect=PermissionError("持续占用")):
            with patch("time.sleep"):
                with pytest.raises(PermissionError):
                    safe_io._atomic_replace_with_retry("src.tmp", "dst.txt", max_retries=3)


# =========================================================================
# 4. P2-08: 临时文件描述符关闭与泄漏防护
# =========================================================================
def test_write_file_safe_cleans_up_fd_and_temp_file_on_error(tmp_path):
    """write_file_safe 遇到写入异常时关闭 temp_fd 并删除临时文件"""
    target = tmp_path / "out.txt"

    with patch("os.fsync", side_effect=OSError("模拟 fsync 失败")):
        with pytest.raises(SafeIOWriteError):
            write_file_safe(target, "some content")

    # 验证没有遗留的临时文件
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert tmp_files == []


def test_write_file_safe_fdopen_failure_closes_raw_fd(tmp_path):
    """os.fdopen 抛出异常时，原始 temp_fd 必须被显式关闭并删除临时文件"""
    target = tmp_path / "out_fdopen.txt"
    closed_fds = []
    real_close = os.close

    def tracking_close(fd):
        closed_fds.append(fd)
        real_close(fd)

    with patch("os.fdopen", side_effect=ValueError("模拟 fdopen 失败")):
        with patch("os.close", side_effect=tracking_close):
            with pytest.raises(SafeIOWriteError):
                write_file_safe(target, "test")

    assert len(closed_fds) >= 1
    assert list(tmp_path.glob(".tmp_*")) == []


def test_create_atomic_backup_cleans_up_on_error(tmp_path):
    """create_atomic_backup 遇到异常时清理临时文件与描述符"""
    src_file = tmp_path / "chapter.txt"
    src_file.write_text("正文内容", encoding="utf-8")
    backup_dir = tmp_path / "backup"

    with patch("os.fsync", side_effect=OSError("模拟备份 fsync 失败")):
        with pytest.raises(SafeIOWriteError):
            create_atomic_backup(src_file, backup_dir)

    assert list(backup_dir.glob(".tmp_*")) == []


# =========================================================================
# 5. P2-09: load_expert_result_records 兼容 UTF-8 BOM 测试
# =========================================================================
def test_load_expert_result_records_with_utf8_bom(tmp_path):
    """带 UTF-8 BOM 的专家结果 JSON 文件能被正常解析"""
    store_file = tmp_path / "expert_results.json"
    payload = {
        "schema_version": STORE_SCHEMA_VERSION,
        "results": [
            {
                "result_id": "exp_01",
                "expert_name": "人设专员",
                "status": "completed",
                "findings": [],
            }
        ],
    }
    raw = codecs.BOM_UTF8 + json.dumps(payload, ensure_ascii=False).encode("utf-8")
    store_file.write_bytes(raw)

    records = load_expert_result_records(store_file)
    assert len(records) == 1
    assert records[0]["result_id"] == "exp_01"
    assert records[0]["expert_name"] == "人设专员"


# =========================================================================
# 6. P0-01: iter_audit_scope 生成器中断保护与 audit_scope_stream 上下文管理器
# =========================================================================
@pytest.fixture
def mini_project():
    with TemporaryDirectory(prefix="mini_audit_") as directory:
        root = Path(directory)
        (root / "正文").mkdir()
        for index in range(1, 4):
            (root / "正文" / f"第{index:03d}章.txt").write_text(
                f"第{index}章\n陆沉在雪原上前行，第{index}章的内容。\n", encoding="utf-8"
            )
        yield root


def test_iter_audit_scope_generator_interruption_protection(mini_project):
    """提前中断生成器时，账本回滚，清单标记 interrupted，未完成章节标为 interrupted"""
    gen = iter_audit_scope(mini_project, scope_str="1-3", mode="solo", silent=True)

    first_item = next(gen)
    assert first_item["kind"] == "chapter"
    assert first_item["sequence"] == 1

    manifest_path = Path(first_item["manifest_path"])
    assert manifest_path.is_file()

    # 模拟提前中断生成器（如 break 或 close）
    gen.close()

    # 验证清单状态已写回
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_status"] == "interrupted"
    assert manifest["exit_code"] == 130
    assert manifest["updated_at"]

    # 第 1 章已完成，后续章节应标为 interrupted
    chapters = manifest["chapters"]
    assert len(chapters) == 3
    assert chapters[0]["status"] == "completed"
    for pend in chapters[1:]:
        assert pend["status"] == "interrupted"


def test_audit_scope_stream_context_manager(mini_project):
    """通过 audit_scope_stream 上下文管理器安全迭代并在 break 时触发中断保护"""
    assert audit_scope_stream is not None, "story_audit 必须导出 audit_scope_stream 上下文管理器"
    manifest_path = None
    with audit_scope_stream(mini_project, scope_str="1-3", mode="solo", silent=True) as stream:
        for item in stream:
            if item["kind"] == "chapter":
                manifest_path = Path(item["manifest_path"])
                break  # 仅消费第 1 章后提前退出

    assert manifest_path is not None
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_status"] == "interrupted"
    assert manifest["exit_code"] == 130
    assert [entry["status"] for entry in manifest["chapters"]] == [
        "completed",
        "interrupted",
        "interrupted",
    ]


def test_audit_scope_stream_exception_in_caller(mini_project):
    """调用者在使用上下文管理器时抛出异常，异常向外传递且触发中断保护"""
    manifest_path = None
    with pytest.raises(RuntimeError, match="外部调用者发生故障"):
        with audit_scope_stream(mini_project, scope_str="1-3", mode="solo", silent=True) as stream:
            for item in stream:
                if item["kind"] == "chapter":
                    manifest_path = Path(item["manifest_path"])
                    raise RuntimeError("外部调用者发生故障")

    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_status"] == "interrupted"
    assert manifest["exit_code"] == 130


def test_audit_scope_stream_normal_completion(mini_project):
    """正常迭代完全部章节时，清单标记 completed，不触发中断"""
    assert audit_scope_stream is not None, "story_audit 必须导出 audit_scope_stream 上下文管理器"
    items = []
    with audit_scope_stream(mini_project, scope_str="1-3", mode="solo", silent=True) as stream:
        for item in stream:
            items.append(item)

    summaries = [item for item in items if item["kind"] == "run_summary"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["run_status"] == "completed"
    assert summary["exit_code"] == 0

    manifest_path = Path(summary["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_status"] == "completed"
    assert manifest["exit_code"] == 0
    assert all(c["status"] == "completed" for c in manifest["chapters"])


# =========================================================================
# 7. P3-13: 验证 run_build_context_package 无重复计算行
# =========================================================================
def test_context_package_packet_bytes_single_assignment(mini_project):
    """验证 run_build_context_package 正常返回且源码无冗余赋值行"""
    import inspect
    src = inspect.getsource(run_build_context_package)
    line = 'payload["budget"]["packet_bytes"] = _package_size(payload)'
    assert src.count(line) == 1, f"packet_bytes 赋值行在 run_build_context_package 中出现次数应为 1，实际为 {src.count(line)}"
