# 长篇网文深度审查技能 (story-audit) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建军工级长篇网文深度审查技能（story-audit），融合确定性双轨资源账本引擎、网文短句排版检测、事实因果连续性核验、第一性原理读者追读卡尺与毒舌老书虫对抗式审查。

**Architecture:** 底层由零外部依赖的 Python 3.8+ 确定性工具套件（文件安全I/O、自然排序章节定位、账本状态机与防脏写、排版白名单掩码扫描、跨章与视界隔离提取、锚点安全回写）完成精确计算与结构化预检；顶层由 4 角色多 Agent 专家矩阵（Agent A 账本、Agent B 事实、Agent C 排版、Agent D 对抗审判）完成深度语义冲突诊断、双方案短句修复与 P0~P3 分级报告输出。

**Tech Stack:** Python 3.8+（纯标准库：json, re, pathlib, argparse, difflib, typing, unittest/pytest），Markdown, Git.

---

## 零、全局测试隔离与工程规范

1. **零外部依赖承诺**：所有脚本与测试严格使用 Python 3.8+ 标准库（仅使用 `json`, `re`, `pathlib`, `argparse`, `difflib`, `typing`, `tempfile`, `unittest`），严禁要求作者执行任何 `pip install`。
2. **测试目录绝对隔离**：所有涉及磁盘读写、文件探测、备份生成的单元测试，必须在 `setUp()` 中使用 `tempfile.TemporaryDirectory()` 创建临时沙箱，并在 `tearDown()` 中显式销毁，严禁在真实工作区残留脏文件。
3. **跨平台与行号保真**：在内存中统一规范换行符为 `\n`，白名单掩码采用等行占位替换，严禁改变原稿物理行数。

---

## 一、核心数据结构与契约定义 (`scripts/types.py`)

在开发各个子模块前，首先固化跨模块交互的标准数据模型：

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

@dataclass
class ChapterItem:
    index: float               # 序号：0 (序章), 1, 31, 31.1 (第31章上)
    title: str                 # 纯标题：如 "破局之策"
    raw_name: str              # 原始文件名：如 "第031章_破局之策.md"
    path: Path                 # 物理绝对路径

@dataclass
class FormatFinding:
    line_number: int           # 原始文本中的物理行号（1-based）
    flaw_type: str             # "LONG_PARAGRAPH" | "DRAGGING_SENTENCE" | "DIALOGUE_MIXED" | "AI_CONJUNCTION"
    severity: str              # "P2" | "P3"
    snippet: str               # 发生缺陷的原文切片（<= 60字）
    message: str               # 缺陷描述
    suggestion: str            # 短句化修改建议

@dataclass
class BoundaryContext:
    prev_tail_300: str                 # 上章末尾 300 字（首章时为空字符串）
    curr_head_300: str                 # 本章开头 300 字
    has_prev_chapter: bool             # 是否存在上一章
    is_pov_transition: bool            # 是否识别出视点转场（"与此同时"等）
    transition_clue: Optional[str]     # 触发转场的词句
    isolation_zones: List[Dict[str, Any]] # 闪回/回忆隔离区间 [{start_line, end_line, type, clue}]

@dataclass
class PatchSpec:
    target_line: int           # 报告中建议修改的物理行号
    context_before: str        # 前置锚点句（前一句）
    old_text: str              # 待替换的原文字句
    new_text: str              # 采纳的新短句内容
    context_after: str         # 后置锚点句（后一句）
```

---

## 二、预审包结构规范 (`reports/.cache/pre_audit_bundle.json`)

底层 Python 引擎最终向顶层 Agent 输出的标准结构化 JSON：

```json
{
  "meta": {
    "version": "1.0",
    "generated_at": "2026-09-03T23:30:00",
    "target_chapter": 31.0,
    "target_file": "第031章_破局.md",
    "encoding": "utf-8",
    "newline": "\n"
  },
  "sequence_diagnostics": {
    "has_gap": false,
    "gap_warnings": []
  },
  "boundary": {
    "has_prev_chapter": true,
    "prev_chapter_file": "第030章_伏击.md",
    "prev_tail_300": "...顾渊持剑而立，眼中杀意未消。",
    "curr_head_300": "与此同时，三千里外的大周皇城内...",
    "is_pov_transition": true,
    "transition_clue": "与此同时",
    "isolation_zones": [
      { "start_line": 45, "end_line": 52, "type": "FLASHBACK", "clue": "五年前那一战" }
    ]
  },
  "ledger_snapshot": {
    "active_assets": [
      {
        "id": "item_frost_sword",
        "name": "霜华剑",
        "owner": "主角",
        "current_holder": "陆雪",
        "status": "LENT_OUT"
      }
    ],
    "foreshadowing_stash": [
      { "name": "九龙玉玺", "origin": "第10章秘境暗格", "status": "CONCEALED" }
    ]
  },
  "format_scan": {
    "total_flaws": 3,
    "findings": [
      {
        "line_number": 12,
        "flaw_type": "LONG_PARAGRAPH",
        "severity": "P2",
        "snippet": "顾渊深吸了一口气，周围的寒风呼啸着刮过...",
        "message": "单段字数达 138 字，超出 120 字上限",
        "suggestion": "建议在动作推进处切分为 2~3 个短段"
      }
    ]
  }
}
```

---

## 三、逐步实施任务清单 (TDD Tasks)

### Task 0: 核心数据结构模块定义 (types.py)

**Files:**
- Create: `scripts/types.py`
- Test: `tests/test_types.py`

- [x] **Step 1: 编写数据类测试**
  测试 ChapterItem, FormatFinding, BoundaryContext, PatchSpec 的实例化与默认值。
- [x] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_types.py`
- [x] **Step 3: 实现 scripts/types.py**
- [x] **Step 4: 运行测试确认通过**
- [x] **Step 5: 提交 Git**
  `git add scripts/types.py tests/test_types.py && git commit -m "feat(core): 定义全系统核心数据类与类型注解契约"`

---

### Task 1: 统一安全文件 I/O 与原子备份器 (safe_io.py)

**Files:**
- Create: `scripts/safe_io.py`
- Test: `tests/test_safe_io.py`

- [ ] **Step 1: 编写失败的单元测试 (test_safe_io.py)**
  使用 `tempfile.TemporaryDirectory`：
  1. 测试 `utf-8-sig`、`utf-8`、`gb18030` 自动嗅探与中文字符保真；
  2. 测试 Windows `\r\n` 在内存中被规整为 `\n`；
  3. 测试损坏二进制文件读取时抛出自定义异常 `SafeIOReadError`；
  4. 测试原子备份文件生成至 `reports/.bak/{filename}_{timestamp}.bak`。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_safe_io.py`
- [ ] **Step 3: 编写 safe_io.py 最小实现**
  - `read_file_safe(path: Path) -> Tuple[str, str, str]`
  - `write_file_safe(path: Path, content: str, encoding: str, newline: str) -> None`
  - `create_atomic_backup(source_path: Path, backup_dir: Path) -> Path`
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/safe_io.py tests/test_safe_io.py && git commit -m "feat(io): 实现安全文件读写、GB18030探测回退与原子备份"`

---

### Task 2: 智能章节匹配与自然排序解析器 (chapter_resolver.py)

**Files:**
- Create: `scripts/chapter_resolver.py`
- Test: `tests/test_chapter_resolver.py`

- [ ] **Step 1: 编写失败的单元测试 (test_chapter_resolver.py)**
  使用 `tempfile.TemporaryDirectory`：
  1. 测试中文大写数字位权算法："三十一" -> 31，"一百零五" -> 105，"十二" -> 12，"两千零八" -> 2008，"〇一" -> 1；
  2. 测试特殊序位："序章"/"楔子" -> 0.0，"第31章（上）" -> 31.1；
  3. 测试自然数值排序：测试列表 `["第10章.txt", "第2章.txt", "第1章.txt", "第20章.txt"]`, 结果必须为 `[1.0, 2.0, 10.0, 20.0]`；
  4. 测试断号体检：1~5章中缺失第3章时生成 P2 警告信息；
  5. 测试按索引定位章节文件。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_chapter_resolver.py`
- [ ] **Step 3: 编写 chapter_resolver.py 最小实现**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/chapter_resolver.py tests/test_chapter_resolver.py && git commit -m "feat(resolver): 实现章节自然排序、中文大写数字转换与变体匹配"`

---

### Task 3: 排版白名单掩码与格式扫描器 (format_scanner.py)

**Files:**
- Create: `scripts/format_scanner.py`
- Test: `tests/test_format_scanner.py`

- [ ] **Step 1: 编写失败的单元测试 (test_format_scanner.py)**
  1. 测试行数保持白名单掩码：系统属性面板、古诗口诀、Markdown 引用公文替换为等行换行符，断言掩码前后 `text.count("\n")` 完全相等；
  2. 测试单段超长（>= 120 字，P2）；
  3. 测试长句拖沓（逗号 >= 4 或单分句 >= 45 字，P2）；
  4. 测试对话动作混排（对话引号后紧跟 >= 80 字动作环境描写不分行，P3 DIALOGUE_MIXED）；
  5. 测试高潮情绪独白降级：单段虽超 120 字，但连续含有 >= 3 个感叹号且为情绪咆哮时，由 P2 降级为 P3；
  6. 测试 AI 连词统计（然而、与此同时等，P3）。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_format_scanner.py`
- [ ] **Step 3: 编写 format_scanner.py 最小实现**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/format_scanner.py tests/test_format_scanner.py && git commit -m "feat(typeset): 实现行数保持掩码、对话混塞检测与独白情绪降级扫描"`

---

### Task 4: 资源账本状态机、多主体所有权与防脏写引擎 (ledger_engine.py)

**Files:**
- Create: `scripts/ledger_engine.py`
- Test: `tests/test_ledger_engine.py`

- [ ] **Step 1: 编写失败的单元测试 (test_ledger_engine.py)**
  使用 `tempfile.TemporaryDirectory`：
  1. 测试状态机：UNACQUIRED -> ACQUIRED -> EQUIPPED -> LENT_OUT -> RECLAIMED -> CONSUMED；
  2. 测试多主体所有权：owner 与 current_holder 字段分离，暂借状态合规；
  3. 测试暗线伏笔标记提取：从正文中扫描 `<!-- audit:stash name="..." origin="..." status="CONCEALED" -->`；
  4. 测试防脏写拦截 (Dirty-Write Guard)：若 Markdown mtime 晚于 JSON，`render_ledger_markdown(force=False)` 必须抛出 `LedgerDirtyError`；若带 `force=True` 则强制覆写；
  5. 测试冷热资产分层 Markdown 渲染：已消耗/冷资产渲染在 `<details><summary>历史已消耗与归档资产</summary>` 折叠区中；
  6. 测试分卷封账快照归档与净结转。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_ledger_engine.py`
- [ ] **Step 3: 编写 ledger_engine.py 最小实现**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/ledger_engine.py tests/test_ledger_engine.py && git commit -m "feat(ledger): 实现多主体账本状态机、防脏写拦截与伏笔池提取"`

---

### Task 5: 跨章缝合、POV 切换与视界隔离提取器 (chapter_linker.py)

**Files:**
- Create: `scripts/chapter_linker.py`
- Test: `tests/test_chapter_linker.py`

- [ ] **Step 1: 编写失败的单元测试 (test_chapter_linker.py)**
  1. 测试提取上章末尾 300 字与下章开头 300 字；
  2. **边界条件测试**：当审查第 1 章（`prev_text=None`）时，平稳处理，设置 `has_prev_chapter=False`，严禁崩溃；
  3. 测试 POV 转场识别：识别新章开头“与此同时”、“同一时刻”等标志，设置 `is_pov_transition=True` 并提取转场线索；
  4. 测试叙事视界隔离：检测“五年前那一战”、“恍惚间”、“陷入心魔幻境”等闪回区间。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_chapter_linker.py`
- [ ] **Step 3: 编写 chapter_linker.py 最小实现**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/chapter_linker.py tests/test_chapter_linker.py && git commit -m "feat(linker): 实现跨章缝合切片、首章边界防御、POV转场与闪回隔离标记"`

---

### Task 6: 三行锚点邻域消歧安全回写器 (safe_writer.py)

**Files:**
- Create: `scripts/safe_writer.py`
- Test: `tests/test_safe_writer.py`

- [ ] **Step 1: 编写失败的单元测试 (test_safe_writer.py)**
  使用 `tempfile.TemporaryDirectory`：
  1. 测试三行锚点唯一匹配成功回写，原文件编码与换行符严格保持；
  2. 测试重复高频动作短句（如“侧身。出刀。断魂”出现在多处），在局部邻域 `[line-30, line+30]` 内精准消歧命中；
  3. 测试邻域越界截断处理（如目标行为第 5 行时，邻域下限自动截断为 0，防止负索引报错）；
  4. 测试邻域内仍有多重匹配或无匹配时，抛出 `AmbiguousPatchError` 并拒绝篡改文件。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_safe_writer.py`
- [ ] **Step 3: 编写 safe_writer.py 最小实现**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/safe_writer.py tests/test_safe_writer.py && git commit -m "feat(writer): 实现带局部邻域消歧与越界截断的安全回写器"`

---

### Task 7: CLI 总入口、预审包构建与退出码管线 (story_audit.py)

**Files:**
- Create: `scripts/story_audit.py`
- Test: `tests/test_story_audit_cli.py`

- [ ] **Step 1: 编写失败的单元测试 (test_story_audit_cli.py)**
  使用 `tempfile.TemporaryDirectory`：
  1. 测试全量参数解析，包含 `--chapter`, `--scope`, `--init`, `--checkpoint`, `--volume`, `--sync-from-md`, `--apply-fix`, `--force`, `--strict`；
  2. 测试成功生成 `reports/.cache/pre_audit_bundle.json` 且符合规范 Schema；
  3. 测试标准退出码映射：
     - 绿灯（无违规或仅 P2/P3）-> Exit Code 0；
     - 黄灯（发现 P1 违规，若带 `--strict`）-> Exit Code 1；
     - 红灯（发现 P0 致命断裂）-> Exit Code 2；
     - 异常（文件找不到、脏写未加 force）-> Exit Code 3。
- [ ] **Step 2: 运行测试确认失败**
  `python -m unittest tests/test_story_audit_cli.py`
- [ ] **Step 3: 编写 story_audit.py 最小实现**
  串联 safe_io, chapter_resolver, format_scanner, ledger_engine, chapter_linker, safe_writer 各模块。
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交 Git**
  `git add scripts/story_audit.py tests/test_story_audit_cli.py && git commit -m "feat(cli): 实现 story_audit CLI 执行管线、预审包生成与标准退出码"`

---

### Task 8: 规范参考库与多 Agent 专家协同协议 (references/ & SKILL.md)

**Files:**
- Create: `references/audit-rules.md`
- Create: `references/ledger-model.md`
- Create: `references/chapter-boundary.md`
- Create: `references/short-sentence-style.md`
- Create: `references/first-principles.md`
- Create: `references/report-template.md`
- Create: `SKILL.md`

- [ ] **Step 1: 编写 6 份核心参考规则文档**
  从设计规范中提取出纯净、即插即用的规则字典，确保 `first-principles.md` 完整收录 4 维量化判定卡尺，`short-sentence-style.md` 包含 AI 连词禁词表。
- [ ] **Step 2: 编写 SKILL.md**
  定义技能 frontmatter、触发短语、4 专家（Agent A 账本、Agent B 事实、Agent C 排版、Agent D 对抗审判）协同协议、Solo 模式降级流程、报告归档目录收口。
- [ ] **Step 3: 全局端到端集成测试**
  运行：`python -m unittest discover tests`
  确保所有测试全部通过（PASS），0 外部依赖。
- [ ] **Step 4: 提交 Git**
  `git add references/ SKILL.md && git commit -m "feat(skill): 完成审查规范库与多 Agent 协同协议部署"`

---
