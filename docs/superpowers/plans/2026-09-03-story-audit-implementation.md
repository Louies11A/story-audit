# 长篇网文深度审查技能 (story-audit) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建军工级长篇网文深度审查技能（story-audit），融合确定性双轨资源账本引擎、网文短句排版检测、事实因果连续性核验、第一性原理读者追读卡尺与毒舌老书虫对抗式审查。

**Architecture:** 底层由零外部依赖的 Python 3.8+ 确定性工具套件（文件安全I/O、自然排序章节定位、账本状态机与防脏写、排版白名单掩码扫描、跨章与视界隔离提取、锚点安全回写）完成精确计算与结构化预检；顶层由 4 角色多 Agent 专家矩阵（Agent A 账本、Agent B 事实、Agent C 排版、Agent D 对抗审判）完成深度语义冲突诊断、双方案短句修复与 P0~P3 分级报告输出。

**Tech Stack:** Python 3.8+（纯标准库：json, re, pathlib, argparse, difflib, typing, unittest/pytest），Markdown, Git.

---

## 目录结构规划

```
审查技能/
├── SKILL.md                          # 技能主入口（4 专家多 Agent 协议与 Solo 降级流）
├── docs/superpowers/
│   ├── specs/2026-09-03-story-audit-design.md
│   └── plans/2026-09-03-story-audit-implementation.md
├── references/                       # 规则规范库
│   ├── audit-rules.md                # 事实冲突与生理战力基线
│   ├── ledger-model.md               # 账本数据字典、伏笔池与借还状态机
│   ├── chapter-boundary.md           # 跨章接缝与 POV 视点豁免
│   ├── short-sentence-style.md       # 短句排版标准与去AI味词表
│   ├── first-principles.md           # 第一性原理 4 维量化卡尺与对抗指引
│   └── report-template.md            # P0~P3 统一报告模板
├── scripts/                          # 确定性 Python 引擎（零第三方依赖）
│   ├── safe_io.py                    # 编码探测与安全读写/原子备份
│   ├── chapter_resolver.py           # 智能章节文件定位与自然排序
│   ├── format_scanner.py             # 白名单掩码+排版正则扫描器
│   ├── ledger_engine.py              # 账本状态机、多主体所有权、防脏写同步
│   ├── chapter_linker.py             # 跨章接缝与 POV/闪回隔离提取
│   ├── safe_writer.py                # 三行锚点邻域消歧安全回写器
│   └── story_audit.py                # 主入口 CLI（流程串联与标准退出码）
└── tests/                            # 自动化测试套件
    ├── __init__.py
    ├── test_safe_io.py
    ├── test_chapter_resolver.py
    ├── test_format_scanner.py
    ├── test_ledger_engine.py
    ├── test_chapter_linker.py
    ├── test_safe_writer.py
    └── test_story_audit_cli.py
```

---

### Task 1: 统一安全文件 I/O 与原子备份器 (safe_io.py)

**Files:**
- Create: `scripts/safe_io.py`
- Test: `tests/test_safe_io.py`

- [ ] **Step 1: 编写失败的单元测试 (test_safe_io.py)**
  测试目标：
  1. 自动探测 `utf-8-sig`、`utf-8`、`gb18030` 并无损读取中文字符；
  2. 自动在内存中将 `\r\n` 规整为 `\n`；
  3. 安全写入时保留原始编码与换行符；
  4. 创建带时间戳的原子备份至 `reports/.bak/`。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_safe_io.py`
  预期：FAIL（模块尚未创建）

- [ ] **Step 3: 编写 safe_io.py 最小实现**
  实现函数：
  - `read_file_safe(path: Path) -> Tuple[str, str, str]` (返回内容、编码、换行符)
  - `write_file_safe(path: Path, content: str, encoding: str, newline: str) -> None`
  - `create_atomic_backup(source_path: Path, backup_dir: Path) -> Path`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_safe_io.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/safe_io.py tests/test_safe_io.py && git commit -m "feat(io): 实现安全文件读写与编码探测回退机制"`

---

### Task 2: 智能章节匹配与自然排序解析器 (chapter_resolver.py)

**Files:**
- Create: `scripts/chapter_resolver.py`
- Test: `tests/test_chapter_resolver.py`

- [ ] **Step 1: 编写失败的单元测试 (test_chapter_resolver.py)**
  测试目标：
  1. 零依赖中文数字解析（“第三十一章” -> 31，“第一百零五章” -> 105）；
  2. 自然排序：确保第 2 章排在第 10 章前，严禁字典序倒错；
  3. 特殊序位映射：序章/楔子/引子映射为 0，第 31 章（上/下）映射为 31.1/31.2；
  4. 章号断号与重号体检诊断；
  5. 匹配池定位（输入 31 成功匹配 `第031章_破局.md` 等各种变体）。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_chapter_resolver.py`
  预期：FAIL

- [ ] **Step 3: 编写 chapter_resolver.py 最小实现**
  实现类与核心函数：
  - `chinese_to_number(text: str) -> Optional[int]` (位权算法实现)
  - `parse_chapter_index(filename: str) -> Optional[float]`
  - `class ChapterResolver`: `discover_chapters(root_dir: Path) -> List[ChapterItem]`
  - `get_chapter_by_index(target_index: float) -> Optional[ChapterItem]`
  - `diagnose_sequence_gaps(chapters: List[ChapterItem]) -> List[str]`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_chapter_resolver.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/chapter_resolver.py tests/test_chapter_resolver.py && git commit -m "feat(resolver): 实现章节自然排序、中文大写数字转换与变体匹配"`

---

### Task 3: 排版白名单掩码与格式扫描器 (format_scanner.py)

**Files:**
- Create: `scripts/format_scanner.py`
- Test: `tests/test_format_scanner.py`

- [ ] **Step 1: 编写失败的单元测试 (test_format_scanner.py)**
  测试目标：
  1. 行数保持白名单掩码：系统属性面板、古诗口诀、Markdown 引用公文替换为等行换行符，物理行号不变；
  2. 正则检测单段超长（>= 120 字，P2）；
  3. 长句拖沓（逗号 >= 4 或单分句 >= 45 字，P2）；
  4. AI 翻译腔高频连词统计（然而、与此同时等，P3）；
  5. 准确记录缺陷发生的物理行号与原文切片。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_format_scanner.py`
  预期：FAIL

- [ ] **Step 3: 编写 format_scanner.py 最小实现**
  实现类与函数：
  - `mask_special_blocks(text: str) -> Tuple[str, List[Dict]]` (行数绝对保持掩码)
  - `scan_typography_flaws(text: str, original_text: str) -> List[FormatFinding]`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_format_scanner.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/format_scanner.py tests/test_format_scanner.py && git commit -m "feat(typeset): 实现行数保持掩码与短句排版缺陷扫描"`

---

### Task 4: 资源账本状态机、多主体所有权与防脏写引擎 (ledger_engine.py)

**Files:**
- Create: `scripts/ledger_engine.py`
- Test: `tests/test_ledger_engine.py`

- [ ] **Step 1: 编写失败的单元测试 (test_ledger_engine.py)**
  测试目标：
  1. 七类资产状态机状态流转（含 LENT_OUT 与 RECLAIMED 暂借状态）；
  2. 多主体归属管理（owner 与 current_holder 分离）；
  3. 双轨生成：由 JSON 计算真实状态，渲染生成人类可读的 `设定/资源账本.md`；
  4. 防脏写拦截 (Dirty-Write Guard)：Markdown 的 mtime 晚于 JSON 时中断并报警；
  5. 分卷封账快照归档与期末净资产结转。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_ledger_engine.py`
  预期：FAIL

- [ ] **Step 3: 编写 ledger_engine.py 最小实现**
  实现类与方法：
  - `class AssetItem`, `class LedgerState`
  - `check_dirty_state(md_path: Path, json_path: Path) -> bool`
  - `render_ledger_markdown(state: LedgerState) -> str`
  - `sync_from_markdown(md_path: Path, json_path: Path) -> None`
  - `create_volume_checkpoint(volume: int, ledger_state: LedgerState, archive_dir: Path) -> None`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_ledger_engine.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/ledger_engine.py tests/test_ledger_engine.py && git commit -m "feat(ledger): 实现七类资产多主体状态机与防脏写双轨同步"`

---

### Task 5: 跨章缝合、POV 切换与视界隔离提取器 (chapter_linker.py)

**Files:**
- Create: `scripts/chapter_linker.py`
- Test: `tests/test_chapter_linker.py`

- [ ] **Step 1: 编写失败的单元测试 (test_chapter_linker.py)**
  测试目标：
  1. 准确切片提取上章末尾最后 300 字与下章开头前 300 字；
  2. 识别新章开头是否包含转场词（“与此同时”、“同一时刻”等），标记 POV 侧面烘托豁免；
  3. 识别段落是否处于闪回/回忆/心魔幻境视界隔离状态。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_chapter_linker.py`
  预期：FAIL

- [ ] **Step 3: 编写 chapter_linker.py 最小实现**
  实现函数：
  - `extract_boundary_slices(prev_text: str, curr_text: str) -> BoundaryContext`
  - `detect_pov_transition(head_text: str) -> Tuple[bool, Optional[str]]`
  - `detect_narrative_isolation_zones(text: str) -> List[Tuple[int, int, str]]`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_chapter_linker.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/chapter_linker.py tests/test_chapter_linker.py && git commit -m "feat(linker): 实现跨章缝合切片提取、POV转场识别与闪回隔离标记"`

---

### Task 6: 三行锚点邻域消歧安全回写器 (safe_writer.py)

**Files:**
- Create: `scripts/safe_writer.py`
- Test: `tests/test_safe_writer.py`

- [ ] **Step 1: 编写失败的单元测试 (test_safe_writer.py)**
  测试目标：
  1. 上下文前后三行锚点唯一匹配与替换；
  2. 网文重复动作短句出现多次时，基于建议行号局部邻域 `[line-30, line+30]` 准确消歧；
  3. 邻域内仍有多重匹配或无匹配时，安全拒绝回写并抛出消歧异常（触发 Exit Code 3）；
  4. 回写前后自动生成原子备份并保持源文件编码与 CRLF/LF。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_safe_writer.py`
  预期：FAIL

- [ ] **Step 3: 编写 safe_writer.py 最小实现**
  实现函数：
  - `apply_patch_with_disambiguation(file_path: Path, patch_spec: PatchSpec) -> bool`

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_safe_writer.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/safe_writer.py tests/test_safe_writer.py && git commit -m "feat(writer): 实现带行号邻域消歧与原子备份的安全回写器"`

---

### Task 7: CLI 总入口、预审包构建与退出码管线 (story_audit.py)

**Files:**
- Create: `scripts/story_audit.py`
- Test: `tests/test_story_audit_cli.py`

- [ ] **Step 1: 编写失败的单元测试 (test_story_audit_cli.py)**
  测试目标：
  1. 完整命令行参数解析（--chapter, --scope, --init, --checkpoint, --sync-from-md, --apply-fix, --strict）；
  2. 正确生成 `reports/.cache/pre_audit_bundle.json` 供 Agent 消费；
  3. 退出状态码严格遵从标准（0 绿灯，1 黄灯警告，2 红灯阻断，3 系统故障）。

- [ ] **Step 2: 运行测试确认失败**
  运行：`python -m unittest tests/test_story_audit_cli.py`
  预期：FAIL

- [ ] **Step 3: 编写 story_audit.py 最小实现**
  整合前述各个模块，串联成完整 CLI 执行管线。

- [ ] **Step 4: 运行测试确认通过**
  运行：`python -m unittest tests/test_story_audit_cli.py`
  预期：PASS

- [ ] **Step 5: Git 提交**
  `git add scripts/story_audit.py tests/test_story_audit_cli.py && git commit -m "feat(cli): 实现 story_audit CLI 主管线与标准退出码"`

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
  从设计规范中提取出纯净、即插即用的规则字典与量化指标库。

- [ ] **Step 2: 编写 SKILL.md**
  定义技能 frontmatter、触发短语、4 专家多 Agent 协同协议、Solo 模式降级流程、报告归档目录收口。

- [ ] **Step 3: 全局集成验证**
  运行：`python -m unittest discover tests`
  预期：全部通过，0 外部依赖。

- [ ] **Step 4: Git 提交**
  `git add references/ SKILL.md && git commit -m "feat(skill): 完成审查规范库与多 Agent 协同协议部署"`

---
