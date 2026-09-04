# -*- coding: utf-8 -*-
"""
author_memory.py: 纯 Python 单文件作者偏好状态机

功能职责：
1. 状态机落盘于 设定/_author-memory-state.json，并实时生成只读视图 设定/作者画像.md；
2. 支持子命令/API：init、record(key, value, category, source)、query(categories, limit_bytes=2048)、check；
3. 严格恪守核心纪律：
   - 记忆铁律：查询结果硬上限 <= 2048 字节；
   - 边界铁律：审查中仅作为意图解释辅助，绝对不能降低 Rubric 严重度、把事实冲突判为无问题或跳过平台门禁；
   - 反近亲繁殖铁律：坚决不学习系统自身的审查警告/模板话术。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA_VERSION = 1
STATE_MAX_BYTES = 2 * 1024 * 1024  # 2MB
QUERY_HARD_LIMIT_BYTES = 2048

VALID_CATEGORIES: Dict[str, str] = {
    "prose_style": "文风与表达",
    "story_design": "故事设计",
    "workflow": "创作流程",
    "delivery": "交付格式",
    "interaction": "协作方式",
}

VALID_SOURCES: Set[str] = {
    "explicit_user",
    "accepted_suggestion",
    "repeated_correction",
    "inferred_pattern",
    "manual",
}

CONFIDENCE_LEVELS: Set[str] = {"low", "medium", "high"}

# 反近亲繁殖违禁词列表（系统自身的警告、报错、模板标记严禁反向污染为作者偏好）
FORBIDDEN_SYSTEM_PATTERNS = [
    re.compile(r'P[0-3]'),
    re.compile(r'FormatFinding', re.I),
    re.compile(r'DRAGGING_SENTENCE', re.I),
    re.compile(r'LONG_PARAGRAPH', re.I),
    re.compile(r'AI_CONJUNCTION', re.I),
    re.compile(r'AI_NOT_IS', re.I),
    re.compile(r'AI_EM_DASH', re.I),
    re.compile(r'AI_VOICE_CONTRAST', re.I),
    re.compile(r'AI_NEGATION_PARADE', re.I),
    re.compile(r'AI_TRAILER', re.I),
    re.compile(r'AI_GOD_VIEW', re.I),
    re.compile(r'致命错误'),
    re.compile(r'严重失误'),
    re.compile(r'局部瑕疵'),
    re.compile(r'润色建议'),
    re.compile(r'story[-_]audit', re.I),
    re.compile(r'红灯阻断'),
    re.compile(r'黄灯警告'),
    re.compile(r'出装审计'),
    re.compile(r'账本冲突'),
    re.compile(r'防脏写拦截'),
]


class AuthorMemoryError(ValueError):
    """作者记忆状态机异常基类"""
    pass


class AntiInbreedingViolation(AuthorMemoryError):
    """反近亲繁殖拦截异常"""
    pass


def _atomic_write_text(file_path: Path, content: str) -> None:
    """原子安全写文件，杜绝写入中断导致文件损坏"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = file_path.parent
    with tempfile.NamedTemporaryFile('w', dir=temp_dir, delete=False, encoding='utf-8') as tf:
        tf.write(content)
        temp_name = tf.name
    os.replace(temp_name, file_path)


def check_anti_inbreeding(text: str) -> None:
    """检查文本是否包含系统审计特征词，严防反向学习"""
    if not text:
        return
    for pat in FORBIDDEN_SYSTEM_PATTERNS:
        m = pat.search(text)
        if m:
            raise AntiInbreedingViolation(
                f"拒绝学习系统审查警告/内部特征词（命中违禁词: '{m.group(0)}'），坚决杜绝近亲繁殖！"
            )


class AuthorMemory:
    """作者偏好状态机控制器"""

    def __init__(self, project_dir: Optional[Path] = None) -> None:
        self.project_dir = Path(project_dir or ".").resolve()
        self.settings_dir = self.project_dir / "设定"
        self.state_file = self.settings_dir / "_author-memory-state.json"
        self.profile_file = self.settings_dir / "作者画像.md"

    def init(self) -> Path:
        """初始化空状态机与作者画像视图"""
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            initial_state: Dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "preferences": {},
            }
            self._save_state(initial_state)
        else:
            self.check()

        self.render_profile_view()
        return self.state_file

    def load_state(self) -> Dict[str, Any]:
        """读取并校验状态文件"""
        if not self.state_file.exists():
            return {
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "preferences": {},
            }
        try:
            raw = self.state_file.read_text(encoding='utf-8')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise AuthorMemoryError("状态文件格式错误：根节点必须为 JSON 对象")
            if "preferences" not in data or not isinstance(data["preferences"], dict):
                data["preferences"] = {}
            return data
        except json.JSONDecodeError as e:
            raise AuthorMemoryError(f"状态文件 JSON 解析失败: {e}")

    def _save_state(self, state: Dict[str, Any]) -> None:
        """落盘状态文件并原子更新作者画像.md"""
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_str = json.dumps(state, ensure_ascii=False, indent=2)
        if len(state_str.encode('utf-8')) > STATE_MAX_BYTES:
            raise AuthorMemoryError(f"状态文件超过最大允许容量 ({STATE_MAX_BYTES} 字节)")
        _atomic_write_text(self.state_file, state_str)
        self.render_profile_view(state)

    def record(
        self,
        key: str,
        value: str,
        category: str = "prose_style",
        source: str = "explicit_user",
        confidence: str = "high",
        pref_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        记录或更新作者偏好条目。
        严格实施反近亲繁殖过滤与分类校验。
        """
        key_clean = key.strip()
        val_clean = value.strip()
        if not key_clean or not val_clean:
            raise AuthorMemoryError("偏好 key 与 value 均不能为空！")

        # 1. 核心纪律：反近亲繁殖铁律检测
        check_anti_inbreeding(key_clean)
        check_anti_inbreeding(val_clean)

        if category not in VALID_CATEGORIES:
            raise AuthorMemoryError(
                f"无效分类: '{category}'，可选值: {list(VALID_CATEGORIES.keys())}"
            )

        if source not in VALID_SOURCES:
            raise AuthorMemoryError(
                f"无效来源: '{source}'，可选值: {list(VALID_SOURCES)}"
            )

        if confidence not in CONFIDENCE_LEVELS:
            raise AuthorMemoryError(
                f"无效置信度: '{confidence}'，可选值: {list(CONFIDENCE_LEVELS)}"
            )

        state = self.load_state()
        prefs = state.setdefault("preferences", {})

        target_id = pref_id or hashlib.md5(f"{category}:{key_clean}".encode('utf-8')).hexdigest()[:12]
        now_iso = datetime.now(timezone.utc).isoformat()

        item = {
            "id": target_id,
            "key": key_clean,
            "value": val_clean,
            "category": category,
            "source": source,
            "confidence": confidence,
            "status": "active",
            "updated_at": now_iso,
        }
        if target_id not in prefs:
            item["created_at"] = now_iso
        else:
            item["created_at"] = prefs[target_id].get("created_at", now_iso)

        prefs[target_id] = item
        self._save_state(state)
        return item

    def check(self) -> bool:
        """检查状态文件完整性与合规性"""
        if not self.state_file.exists():
            return False
        state = self.load_state()
        if state.get("schema_version") != SCHEMA_VERSION:
            raise AuthorMemoryError(f"不支持的 schema_version: {state.get('schema_version')}")

        prefs = state.get("preferences", {})
        for pid, p in prefs.items():
            if not isinstance(p, dict):
                raise AuthorMemoryError(f"偏好条目 {pid} 损坏")
            check_anti_inbreeding(p.get("key", ""))
            check_anti_inbreeding(p.get("value", ""))
            if p.get("category") not in VALID_CATEGORIES:
                raise AuthorMemoryError(f"偏好条目 {pid} 分类无效: {p.get('category')}")
        return True

    def query(
        self,
        categories: Optional[List[str]] = None,
        limit_bytes: int = QUERY_HARD_LIMIT_BYTES,
    ) -> str:
        """
        查询有效偏好，输出严格控制在 <= limit_bytes (默认 2048 字节)。
        内嵌铁律约束提示：仅作意图解释辅助，绝对不能降低 Rubric 严重度！
        """
        actual_limit = min(limit_bytes, QUERY_HARD_LIMIT_BYTES)
        state = self.load_state()
        prefs = state.get("preferences", {})

        allowed_cats = set(categories) if categories else set(VALID_CATEGORIES.keys())

        matched_items: List[Dict[str, Any]] = [
            p for p in prefs.values()
            if p.get("status") == "active" and p.get("category") in allowed_cats
        ]

        # 按置信度与更新时间倒序排列
        conf_weight = {"high": 3, "medium": 2, "low": 1}
        matched_items.sort(
            key=lambda x: (conf_weight.get(x.get("confidence", "medium"), 1), x.get("updated_at", "")),
            reverse=True
        )

        header = (
            "# 👤 作者偏好与意图上下文 (Author Memory Snapshot)\n"
            "> ⚠️ 铁律约束：本上下文仅作为作者写作意图解释辅助，绝对不能降低 Rubric 严重度、把事实冲突判为无问题或跳过平台门禁。\n\n"
        )

        lines: List[str] = [header]
        current_bytes = len(header.encode('utf-8'))

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for it in matched_items:
            grouped.setdefault(it["category"], []).append(it)

        trunc_notice = "\n<!-- 达到 2048 字节上限截断 -->"
        notice_bytes = len(trunc_notice.encode('utf-8'))
        budget_limit = actual_limit - notice_bytes

        for cat_key, cat_name in VALID_CATEGORIES.items():
            if cat_key not in grouped:
                continue
            cat_header = f"### 【{cat_name}】\n"
            cat_bytes = len(cat_header.encode('utf-8'))
            if current_bytes + cat_bytes > budget_limit:
                lines.append(trunc_notice)
                break
            lines.append(cat_header)
            current_bytes += cat_bytes

            hit_trunc = False
            for p in grouped[cat_key]:
                entry = f"- **{p['key']}**：{p['value']} *(置信度: {p.get('confidence', 'medium')})*\n"
                entry_bytes = len(entry.encode('utf-8'))
                if current_bytes + entry_bytes > budget_limit:
                    lines.append(trunc_notice)
                    hit_trunc = True
                    break
                lines.append(entry)
                current_bytes += entry_bytes

            if hit_trunc:
                break
            lines.append("\n")
            current_bytes += 1

        result_text = "".join(lines).strip()
        raw_b = result_text.encode('utf-8')
        if len(raw_b) > actual_limit:
            result_text = raw_b[:actual_limit].decode('utf-8', errors='ignore')
        return result_text

    def render_profile_view(self, state: Optional[Dict[str, Any]] = None) -> str:
        """生成只读视图 设定/作者画像.md"""
        if state is None:
            state = self.load_state()

        prefs = state.get("preferences", {})
        active_prefs = [p for p in prefs.values() if p.get("status") == "active"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# 👤 作者画像与创作偏好档案 (只读视图)",
            "",
            f"> 同步时间：{now_str}  ",
            "> 数据源：`设定/_author-memory-state.json`  ",
            "> ⚠️ **记忆纪律铁律**：  ",
            "> 1. 本偏好仅供意图理解参考，**严禁用于降低审查卡尺严重度或掩盖事实矛盾**；  ",
            "> 2. 严禁反向学习系统内部告警，严防近亲繁殖。  ",
            "",
            "---",
            "",
            "## 📋 偏好条目一览",
            "",
            "| 分类 | 偏好主题 | 具体偏好约定 | 置信度 | 来源 | 更新时间 |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        if not active_prefs:
            lines.append("| - | 暂无记录 | 请使用 `python scripts/author_memory.py record` 添加 | - | - | - |")
        else:
            active_prefs.sort(key=lambda x: (x.get("category", ""), x.get("key", "")))
            for p in active_prefs:
                cat_label = VALID_CATEGORIES.get(p.get("category", ""), p.get("category", ""))
                src_label = p.get("source", "explicit_user")
                conf = p.get("confidence", "high")
                up_time = p.get("updated_at", "")[:19].replace("T", " ")
                val_display = p.get("value", "").replace("|", "｜")
                lines.append(
                    f"| {cat_label} | {p.get('key')} | {val_display} | {conf} | {src_label} | {up_time} |"
                )

        lines.extend([
            "",
            "---",
            "",
            "## 🛠️ CLI 常用指令",
            "```bash",
            "# 录入偏好",
            "python scripts/author_memory.py record --key '主角性格' --value '果决冷静，不圣母不多话' --category story_design",
            "",
            "# 查询偏好 (受 2048 字节硬上限保护)",
            "python scripts/author_memory.py query",
            "",
            "# 状态完整性校验",
            "python scripts/author_memory.py check",
            "```",
            "",
        ])

        content = "\n".join(lines)
        _atomic_write_text(self.profile_file, content)
        return content


def main() -> int:
    if sys.platform == 'win32':
        try:
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            if hasattr(sys.stderr, 'reconfigure'):
                sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="作者偏好状态机 CLI")
    parser.add_argument("--project", default=".", help="项目根目录 (默认 .)")

    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="初始化作者记忆状态")

    # record
    record_p = subparsers.add_parser("record", help="录入或更新作者偏好")
    record_p.add_argument("--key", required=True, help="偏好主题名称")
    record_p.add_argument("--value", required=True, help="具体偏好内容")
    record_p.add_argument("--category", default="prose_style", choices=list(VALID_CATEGORIES.keys()), help="偏好分类")
    record_p.add_argument("--source", default="explicit_user", choices=list(VALID_SOURCES), help="信息来源")
    record_p.add_argument("--confidence", default="high", choices=list(CONFIDENCE_LEVELS), help="置信度")

    # query
    query_p = subparsers.add_parser("query", help="查询作者偏好")
    query_p.add_argument("--categories", help="逗号分隔的分类筛选列表")
    query_p.add_argument("--limit-bytes", type=int, default=QUERY_HARD_LIMIT_BYTES, help="最大字节限制 (硬上限 2048)")

    # check
    subparsers.add_parser("check", help="校验状态完整性与反近亲繁殖合规")

    args = parser.parse_args()

    mem = AuthorMemory(Path(args.project))

    try:
        if args.command == "init":
            f = mem.init()
            print(f"✅ 作者记忆状态已初始化：{f}")
            print(f"✅ 已生成只读视图：{mem.profile_file}")
            return 0

        elif args.command == "record":
            item = mem.record(
                key=args.key,
                value=args.value,
                category=args.category,
                source=args.source,
                confidence=args.confidence,
            )
            print(f"✅ 成功记录作者偏好 [{item['category']}] {item['key']}: {item['value']}")
            print(f"已更新只读视图: {mem.profile_file}")
            return 0

        elif args.command == "query":
            cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
            out = mem.query(categories=cats, limit_bytes=args.limit_bytes)
            print(out)
            return 0

        elif args.command == "check":
            valid = mem.check()
            if valid:
                print("✅ 作者记忆状态健康，未发现损坏或近亲繁殖违规。")
                return 0
            else:
                print("❌ 状态文件不存在或校验未通过。", file=sys.stderr)
                return 1
        else:
            parser.print_help()
            return 0
    except AntiInbreedingViolation as e:
        print(f"🚨 [反近亲繁殖拦截] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
