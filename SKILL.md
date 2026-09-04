---
name: story-audit
description: 结合第一性原理读者追读卡尺、四大平台商业门禁、毒舌老书虫对抗式审查、确定性双轨资源账本引擎与网文短句排版的军工级小说全维度深度审查系统。专治长篇网文资产失忆、因果断裂、大黑块窒息排版、深度 AI 模式套路与作者自嗨毒点。
metadata:
  version: "2.0.0"
  author: "Story Audit Architecture Team"
  category: "writing-assistant"
  tags:
    - webnovel
    - quality-assurance
    - multi-agent
    - continuity-check
    - formatting
    - ai-patterns
    - platform-rubrics
    - author-memory
triggers:
  - "/story-audit"
  - "/审查"
  - "网文审查"
  - "长篇审查"
  - "深度审查"
  - "审本章"
  - "检查这章"
  - "查漏补缺"
  - "核对账本"
  - "排版质检"
  - "去AI味审查"
---

# 长篇网文深度审查技能 (story-audit)

> **架构铁律：本项目采用纯模块化 Python API 驱动设计，不提供亦不涉及任何 CLI 命令行接口。后续所有功能开发与生态扩展均严格围绕 Python API、强类型数据契约与 Agent 工具函数展开，坚决不涉及 CLI。**

长篇网络小说连载跨越数十万至数百万字。作者极易陷入“资产失忆、因果断裂、手机端排版大黑块窒息、典型 AI 对仗套路腔、作者自嗨作者自我感动”等创作陷阱。

本技能依托**“底层零依赖 Python 确定性引擎锁死资产与排版，深度 AI 模式毫秒级扫描，四大平台商业门禁卡尺，宿主运行时探测与子代理递归防爆哨兵，单文件作者偏好状态机，顶层 4 专家多 Agent 矩阵对抗审判，第一性原理严把读者追读驱动力，网文短句重塑阅读美学”**，为长篇网文提供全维度的防崩盘质量护城河。

---

## 一、双层协同架构与 4 专家矩阵

系统采用“底层确定性工具预检 + 顶层专家矩阵深度诊断”的双层架构：

```
                     ┌──────────────────────────────────────────────┐
                     │       用户触发（/story-audit 或审查指令）       │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
               ┌────────────────────────────────────────────────────────┐
               │   【底层：Python 确定性工具链】（零依赖、毫秒级执行）     │
               │  1. safe_io.py        : 编码嗅探与换行规整             │
               │  2. chapter_resolver  : 自然排序章节定位与序章容错     │
               │  3. ledger_engine.py  : 双轨账本、状态机流转与防脏写   │
               │  4. format_scanner.py : 白名单掩码(面板/口诀)+排版正则 │
               │  5. ai_patterns_checker: 深度 AI 套路句式扫描           │
               │  6. chapter_linker.py : 跨章接缝、POV 漂移与视界隔离   │
               │  7. runtime_detector  : 宿主探测与子代理递归防爆哨兵   │
               │  8. platform_rubrics  : 四大平台商业门禁卡尺评估       │
               │  9. author_memory.py  : 单文件作者偏好状态机与只读画像 │
               │ 10. audit_state.py    : 跨批长篇因果状态机与继承栈     │
               │ 11. 产出预审包 (pre_audit_bundle.json)：精简结构化数据 │
               └────────────────────────────┬───────────────────────────┘
                                            │ 传递精简上下文
                                            ▼
               ┌────────────────────────────────────────────────────────┐
               │     【顶层：多 Agent 专家审查矩阵】（深度语义、网文审美）     │
               │                                                        │
               │  ┌──────────────────┐  ┌──────────────────┐            │
               │  │ Agent A: 账本专员 │  │ Agent B: 事实专员 │            │
               │  │ (Asset Auditor)  │  │(Continuity Guard)│            │
               │  │ 专抓凭空出装/漏项 │  │ 专抓战力/因果/时空│            │
               │  │ 伏笔识别/双方案修复│ │ 闪回隔离/跨章承接  │            │
               │  └─────────┬────────┘  └─────────┬────────┘            │
               │            │                     │                     │
               │            ├─────────────────────┤                     │
               │            ▼                     ▼                     │
               │  ┌──────────────────┐  ┌──────────────────┐            │
               │  │ Agent D: 对抗审判 │  │ Agent C: 排版质检 │            │
               │  │(Adversarial Critic│  │ (Style & Rhythm)│            │
               │  │ 平台门禁与追读卡尺│  │ 手机端大黑块扫描 │            │
               │  │ 毒舌撕碎自嗨毒点  │  │ AI套路/断句重塑   │            │
               │  └──────────────────┘  └──────────────────┘            │
               └────────────────────────────┬───────────────────────────┘
                                            │ 聚合输出与落盘归档
                                            ▼
               ┌────────────────────────────────────────────────────────┐
               │  【报告输出与三行锚点安全回写】                         │
               │  1. 统一报告元数据英文键头部输出                        │
               │  2. 报告归档至 reports/单章审查/{分卷}/第N章_审查报告.md│
               │  3. 最新报告刷新 reports/LATEST_REPORT.md               │
               │  4. 跨批大盘报告 reports/BATCH_SUMMARY_SCOPE_{scope}.md │
               │  5. safe_writer.py 实施三行锚点消歧安全回写 (--apply-fix)│
               └────────────────────────────────────────────────────────┘
```

---

## 二、四大升级特性详解

### 1. 底层深度 AI 模式与套路扫描 (`scripts/ai_patterns_checker.py`)
- **not-is-comparison**：“不是……而是……”对仗句式，反序对比“是……而不是……”（自动剥离引号内台词，排除确认词与反问）；
- **em-dash**：正文中残留破折号“——”硬停顿（建议功能性改写为逗号或动作承接）；
- **voice-contrast**：音量与神态反差腔（“声音不大，却清晰传入……”、“语气平淡，却让所有人心中一凛”）；
- **negation-parade**：连续否定排比（“没有伴奏，没有和声，没有提词器”；“没X，没Y……只是Z”）；
- **trailer-ending / trailer-summary**：章末出戏预告式收尾（“他不知道的是……”、“才刚刚开始”）与状态总结体（“这一夜注定无人入眠”、“命运的齿轮”）；
- **god-view-exposition**：Gate G 上帝解释腔/替读者划重点，以及监控摄像头式纯动作清单（单段连续堆叠 5+ 通用动词）。

### 2. 平台专属卡尺目录 (`references/rubrics/`) 与商业门禁
- **番茄小说 (`references/rubrics/fanqie.md`)**：算法完读率模型（前3段核心悬念/钩子、千字情绪波动、3章翻页动力、完读率预测红线）；
- **起点中文网 (`references/rubrics/qidian.md`)**：追读比模型（3000字爽点节点、50章实力晋阶、金手指在场率、追读比门禁）；
- **知乎盐言故事 (`references/rubrics/zhihu.md`)**：强第一人称限制（“我”视角严格统一，第三人称触发 P1）、首句跳失率控制、伏笔强反转闭环、8000-13000字篇幅；
- **通用网文卡尺 (`references/rubrics/generic.md`)**：黄金三问（主角是谁/要干什么/阻碍是什么）、章节推进 7 状态变化、开局同质化判定、高潮场景四阶力学（蓄能 → 假胜 → 崩解 → 反转）、对话三大病灶（机械问答、科普嘴、不分场合）。

### 3. 宿主运行时探测与子代理递归防爆哨兵 (`scripts/runtime_detector.py`)
- 自适应探测 Codex, Claude, OpenCode, Antigravity, Generic 环境；
- **Subagent Recursion Guard**：探测自身是否已处于子代理上下文中；若已处于子代理环境，强制禁止嵌套再次 spawn，平稳降级为 solo，彻底杜绝递归死锁。

### 4. 单文件作者偏好状态机 (`scripts/author_memory.py`)
- 状态机落盘于 `设定/_author-memory-state.json`，只读视图落盘于 `设定/作者画像.md`；
- **记忆铁律**：查询结果硬上限 ≤ 2048 字节；
- **边界铁律**：仅作意图解释辅助，**绝对不能降低 Rubric 严重度、把事实冲突判为无问题或跳过平台门禁**；
- **反近亲繁殖铁律**：严禁学习系统内部警告、报错与模板话术。

### 5. 跨批长篇因果状态机 (`scripts/audit_state.py`)
- 在长篇批量连审（`--scope`）时原子维护 `reports/.audit_state.json`；
- 记录已完成章节、当前批次以及**“上一批未解决的开放缺陷与伏笔承诺”**；
- 下一批连审启动时自动装载为 `Inherited Items`，校验跨批因果一致性。

---

## 三、标准化报告契约 (Report Contract)

所有单章与批量报告头部逐字输出固定英文元数据键：

```markdown
=== story-audit 深度审查报告 ===
Requested Mode: full
Effective Mode: full
Fallback: none
Platform Rubric: fanqie
Genre: 科幻末世
Scope: 第001章
```

统一 Findings Schema 条目包含：`severity` (P0/P1/P2/P3), `category` (structure/character/prose/consistency/platform/factual/format/causal), `location`, `evidence`, `issue`, `fix`。
铁律约束：事实与因果类缺陷的 `fix` 严格限制为事实统一方向，严禁文学发挥。

---

## 四、标准化审查作业管线 (SOP)

### Step 1: 判定审查场景与参数准备（Python API）
- **日常单章审查**：`audit_chapter(project_dir, chapter_index=N, mode="auto", platform="generic", author_memory=True)`
- **批量多章连审**：`audit_scope(project_dir, scope_str="N-M", platform="generic")`
- **首次全书建账**：`init_ledger(project_dir, scope_str="1-N")`
- **人工账本反向同步**：`sync_ledger_from_md(project_dir)`
- **分卷封账结转**：`checkpoint_volume(project_dir, volume=N)`
- **采纳修复方案回写**：`apply_fix(project_dir, chapter_index=N, patch=...)`

### Step 2: 驱动底层引擎生成预审包
协调器调用底层 Python API（如 `audit_chapter` 或 `build_pre_audit_bundle`），生成 `reports/.cache/pre_audit_bundle.json`，提取行号绝对保真的清洗切片、跨章缝合文本、账本快照、平台诊断与作者画像。

### Step 3: 聚合报告与目录归档收口
严格对照统一 Schema 生成规范 Markdown：
1. 立即覆盖写入：`reports/LATEST_REPORT.md`（方便作者快速翻阅）；
2. 逐章分卷落盘：`reports/单章审查/{分卷目录}/第{章号}章_审查报告.md`；
3. 批量审查输出：`reports/BATCH_SUMMARY_SCOPE_{scope}.md` 与历史归档；
4. 原子更新跨批因果状态机：`reports/.audit_state.json`。

### Step 4: 状态码映射与质量把关
- **Status Code 0 (绿灯通过)**：无缺陷，或仅存在 P2/P3 建议项；
- **Status Code 1 (黄灯警告)**：存在 P1 级严重失误（若 `strict=True` 则阻断）；
- **Status Code 2 (红灯阻断)**：存在 P0 级致命断裂（世界观吃书、死人复活），**坚决阻断**；
- **Status Code 3 (系统异常)**：文件缺失、乱码或防脏写拦截。

---

## 五、底层确定性工具套件清单 (Scripts Manifest)

遵循**零第三方依赖**承诺（仅依赖 Python 3.8+ 标准库）：

1. `scripts/story_audit.py`：纯模块化 Python API 核心调度管线、流程串联与标准状态码；
2. `scripts/ai_patterns_checker.py`：毫秒级深度 AI 套路句式与模式扫描器；
3. `scripts/platform_rubrics.py`：四大平台（番茄/起点/知乎/通用）商业门禁卡尺评估；
4. `scripts/runtime_detector.py`：宿主运行时探测与子代理递归防爆哨兵；
5. `scripts/author_memory.py`：单文件作者偏好状态机与只读画像（含反近亲繁殖校验）；
6. `scripts/audit_state.py`：跨批长篇因果状态机与 Inherited Items 继承栈原子管理；
7. `scripts/safe_io.py`：编码嗅探（utf-8-sig / utf-8 / gb18030）、换行规整与原子备份；
8. `scripts/chapter_resolver.py`：自然数值排序、零依赖大写中文数字解析、序章与子章节定位；
9. `scripts/ledger_engine.py`：双轨账本同步、防脏写拦截、多实体资产状态机与分卷快照；
10. `scripts/format_scanner.py`：行数保持白名单掩码、大黑块与长难句检测、集成深度 AI 扫描；
11. `scripts/chapter_linker.py`：跨章前后 300 字提取、显式 POV 转场识别与闪回隔离标注；
12. `scripts/safe_writer.py`：三行锚点匹配、局部邻域消歧与安全原子回写；
13. `scripts/types.py`：强类型数据模型定义（ChapterItem, Finding, FormatFinding, BoundaryContext, PatchSpec）；
14. `scripts/genre_detector.py`：全题材多维度自动探测与画像引擎（42 题材特征指纹）；
15. `scripts/genre_data.json`：42 题材结构化特征指纹与毒点卡尺数据源。
