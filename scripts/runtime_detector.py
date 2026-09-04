# -*- coding: utf-8 -*-
"""
runtime_detector.py: 宿主运行时环境探测与子代理递归防爆哨兵

功能职责：
1. 宿主运行时探测：
   - 探测 Codex, Claude, OpenCode, Antigravity, Generic (标准 Python 运行时)；
2. 子代理递归防爆哨兵 (Subagent Recursion Guard)：
   - 探测当前执行流是否已处于子代理上下文中；
   - 铁律：若已处于子代理上下文，严禁再次嵌套 spawn 多代理，强制平稳降级为 solo；
3. 弹性三级自适应降级调度 (full -> lean -> solo)。
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple

VALID_RUNTIMES = ("codex", "claude", "opencode", "antigravity", "generic")
VALID_MODES = ("auto", "full", "lean", "solo")

SUBAGENT_ENV_VARS = (
    "STORY_AUDIT_SUBAGENT",
    "SUBAGENT_RUN",
    "IS_SUBAGENT",
    "CLAUDE_SUBAGENT",
    "CODEX_SUBAGENT",
    "AGENT_SUBTASK",
)


def is_subagent_context() -> bool:
    """
    子代理递归防爆哨兵：
    检查环境变量特征，判断当前进程是否已运行于某个父级代理派生的子代理/工作流中。
    """
    for var in SUBAGENT_ENV_VARS:
        val = os.environ.get(var, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    return False


def detect_runtime() -> str:
    """
    探测当前运行宿主平台：
    返回 'codex' | 'claude' | 'opencode' | 'antigravity' | 'generic'
    """
    # 1. Codex 宿主环境特征
    if any(k in os.environ for k in ("CODEX_HOME", "CODEX_APP", "CODEX_SANDBOX", "CODEX_THREAD_ID")):
        return "codex"

    # 2. Claude Code 宿主环境特征
    if any(k in os.environ for k in ("CLAUDE_CODE", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")):
        return "claude"

    # 3. OpenCode 宿主环境特征
    if any(k in os.environ for k in ("OPENCODE_CLIENT", "OPENCODE_SESSION", "OPENCODE_VERSION")):
        return "opencode"

    # 4. Antigravity 宿主环境特征
    if any(k in os.environ for k in ("ANTIGRAVITY_AGENT", "ANTIGRAVITY_ENV", "ANTIGRAVITY_RUN")):
        return "antigravity"

    return "generic"


def resolve_execution_mode(
    requested_mode: str = "auto",
    runtime: Optional[str] = None,
) -> Tuple[str, str]:
    """
    自适应模式解析与弹性降级决策器。
    
    返回：
        (effective_mode, fallback_reason)
        effective_mode: 'full' | 'lean' | 'solo'
        fallback_reason: 'none' | 'subagent_recursion_guard_active' | 'runtime_not_supporting_subagents' | 'unsupported_multiagent_runtime'
    """
    req = requested_mode.lower().strip()
    if req not in VALID_MODES:
        req = "auto"

    rt = (runtime or detect_runtime()).lower().strip()
    in_subagent = is_subagent_context()

    # -------------------------------------------------------------
    # 铁律 1：子代理递归防爆哨兵生效时，强制降级为 solo
    # -------------------------------------------------------------
    if in_subagent:
        if req == "solo":
            return "solo", "none"
        return "solo", "subagent_recursion_guard_active"

    # -------------------------------------------------------------
    # 模式分支处理
    # -------------------------------------------------------------
    if req == "solo":
        return "solo", "none"

    if req == "full":
        if rt == "generic":
            return "solo", "runtime_not_supporting_subagents"
        return "full", "none"

    if req == "lean":
        if rt == "generic":
            return "solo", "runtime_not_supporting_subagents"
        return "lean", "none"

    # req == "auto" 依据环境弹性决策
    if rt in ("codex", "claude"):
        return "full", "none"
    elif rt in ("opencode", "antigravity"):
        return "lean", "none"
    else:
        # generic 单机命令行运行时
        return "solo", "unsupported_multiagent_runtime"
