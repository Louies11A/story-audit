---
name: story-audit
description: 结合第一性原理读者追读卡尺、毒舌老书虫对抗式审查、确定性双轨资源账本引擎与网文短句排版的军工级小说全维度深度审查系统。专治长篇网文资产失忆、因果断裂、大黑块窒息排版与作者自嗨毒点。
metadata:
  version: "1.0.0"
  author: "Story Audit Architecture Team"
  category: "writing-assistant"
  tags:
    - webnovel
    - quality-assurance
    - multi-agent
    - continuity-check
    - formatting
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
---

# 长篇网文深度审查技能 (story-audit)

长篇网络小说连载跨越数十万至数百万字。作者极易陷入“资产失忆、因果断裂、手机端排版大黑块窒息、作者自嗨作者自我感动”等创作陷阱。

本技能依托**“底层零依赖 Python 确定性引擎锁死资产与排版，顶层 4 专家多 Agent 矩阵对抗审判，第一性原理严把读者追读驱动力，网文短句重塑阅读美学”**，为长篇网文提供全维度的防崩盘质量护城河。

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
               │   【底层：Python 确定性引擎层】（零依赖、毫秒级执行）     │
               │  1. safe_io.py        : 编码嗅探与换行规整             │
               │  2. chapter_resolver  : 自然排序章节定位与序章容错     │
               │  3. ledger_engine.py  : 双轨账本、状态机流转与防脏写   │
               │  4. format_scanner.py : 白名单掩码(面板/口诀)+排版正则 │
               │  5. chapter_linker.py : 跨章接缝、POV 漂移与视界隔离   │
               │  6. 产出预审包 (pre_audit_bundle.json)：精简结构化数据 │
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
               │  │ 4维量化读者卡尺  │  │ 手机端大黑块扫描 │            │
               │  │ 毒舌撕碎自嗨毒点  │  │ 修复文本短句化终审│            │
               │  └─────────┬────────┘  └─────────┬────────┘            │
               │            └──────────┬──────────┘                     │
               │                       ▼                                │
               │          ┌─────────────────────────┐                   │
               │          │ 主审查协调器 (Coordinator)│                   │
               │          │ 汇总聚合为 P0~P3 分级报告│                   │
               │          │ 执行原子备份与安全回写   │                   │
               │          └─────────────────────────┘                   │
               └────────────────────────────────────────────────────────┘
```

---

## 二、4 专家职责与协同协议 (Multi-Agent Protocol)

在支持 Sub-agent 调用的环境（Claude Code、Codex 等），主协调器并发调度 4 个专家子代理，各自专注于专属领域，严禁跨权或职责越位：

### 1. 主审查协调器 (Review Coordinator)
- **调度中枢**：解析用户指令参数，调用底层 Python CLI 生成 `reports/.cache/pre_audit_bundle.json`；
- **任务分发**：为 Agent A、B、C、D 分配结构化上下文；
- **聚合裁决**：合并各专家结论，裁定全章最终严重度（P0/P1/P2/P3）；
- **报告归档**：覆写 `reports/LATEST_REPORT.md`，并按百章分卷归档入 `reports/单章审查/`；
- **安全回写**：响应用户 `--apply-fix` 指令，执行原子备份与三行锚点安全回写。

### 2. Agent A：资源审计专员 (Asset Auditor)
- **知识库依据**：`references/ledger-model.md`
- **核心职责**：
  1. 读取 `pre_audit_bundle.json` 中的账本快照与增量出装；
  2. 核对主角、战宠、队友的多主体资产状态机流转；
  3. 检索正文隐形注释 `<!-- audit:stash ... -->` 与前文暗扣，判定伏笔缓冲池；
  4. 对确认凭空出现的资产，强制输出**【方案 1：前置补源】**与**【方案 2：就地修正】**双短句范例。

### 3. Agent B：时空与事实冲突专员 (Continuity & Fact Guard)
- **知识库依据**：`references/audit-rules.md`、`references/chapter-boundary.md`
- **核心职责**：
  1. **生理伤势负荷**：严查前章重伤未愈后章一跃而起，闭环负伤代价；
  2. **时空时序限速**：严查昼夜突变与地理瞬移；
  3. **战力绝对阶梯**：执行越级判定卡尺（同境界小层交代功法优势；跨大境界逆伐**必须具备大能神念/至宝自爆等绝对规则级外力**，否则判定 P0 战力崩塌）；
  4. **跨章接缝与前三段破局**：比对上章末尾 300 字与本章开头 300 字，非转场章节必须前三段正面破局；
  5. **豁免与隔离**：识别合法 POV 转场（豁免前三段破局但防范脱轨水文）与闪回回忆区间（启动视界隔离栈）。

### 4. Agent C：排版与节奏质检官 (Style & Rhythm Coach)
- **知识库依据**：`references/short-sentence-style.md`
- **核心职责**：
  1. 核查正文四大排版缺陷（单段 >=120 字大黑块、长句拖沓、对话混排、AI 连词堆叠）；
  2. 严格执行系统属性面板、功法口诀、公文引用的白名单豁免；
  3. **文本终审过滤器**：**对 Agent A、B、D 产出的所有修复示范文本进行短句化清洗**（单句 > 25 字强制切句，书面从句改动作直叙，确保一行动作一句呼吸）。

### 5. Agent D：读者第一性与对抗审判官 (First-Principles & Adversarial Critic)
- **知识库依据**：`references/first-principles.md`
- **核心职责**：
  1. **读者第一性原理**：使用 4 维量化判定卡尺（危机与代价、信息差收益、情绪钩子、旁观者反应）冷酷评估追读驱动力，区分“有效压弹簧铺垫”与“恶意灌水”；
  2. **毒舌老书虫对抗批判**：扮演 15 年书龄全订老书虫，毒舌撕碎脸谱化低智反派、文青谜语人抒情、无脑强行憋屈、套路换皮复读四大自嗨毒点；
  3. **反俗套重构**：提供高智商博弈与动作反制的短句重构方案。

---

## 三、Solo 单会话模式自动降级流 (Solo Fallback Protocol)

当宿主环境不支持 Sub-agent 并发调用（如单一上下文会话），主协调器**自动且平稳降级为 6 阶段串行链式思辨（Sequential Chain of Thought）**，审查严密度与输出质量 100% 对齐：

```
[Stage 1: Python 确定性预检]
  └─ 运行 scripts/story_audit.py，读取 pre_audit_bundle.json 与排版/接缝/账本切片。
       │
[Stage 2: 账本专员思辨 (Agent A)]
  └─ 检查道具/货币流水 -> 探测伏笔池 -> 生成出装短句双方案。
       │
[Stage 3: 事实专员思辨 (Agent B)]
  └─ 跨章缝合比对 -> 视界隔离出入栈 -> 生理/时空/战力基线核验。
       │
[Stage 4: 对抗审判思辨 (Agent D)]
  └─ 4 维追读卡尺打分 -> 老书虫视角直击自嗨毒点 -> 构思反套路解法。
       │
[Stage 5: 短句排版终审 (Agent C)]
  └─ 过滤大黑块与 AI 连词 -> 清洗并重塑 A/B/D 的所有示范短句。
       │
[Stage 6: 协调器聚合输出]
  └─ 渲染完整 Markdown 报告 -> 覆写 LATEST_REPORT.md -> 分卷归档 -> 映射退出码。
```

---

## 四、标准化执行工作流 (Step-by-Step Execution Pipeline)

无论 Multi-Agent 还是 Solo 模式，技能执行必须严格经历以下六步：

### Step 1: 解析指令与路由判断
根据用户自然语言或命令行参数，确定执行模式：
- **单章深度审查**（默认最新章或指定章）：`python scripts/story_audit.py --chapter N`
- **批量多章连审**：`python scripts/story_audit.py --scope N-M`
- **首次全书建账**：`python scripts/story_audit.py --init --scope 1-N`
- **人工账本反向同步**：`python scripts/story_audit.py --sync-from-md`
- **分卷封账结转**：`python scripts/story_audit.py --checkpoint --volume N`
- **采纳修复方案回写**：`python scripts/story_audit.py --chapter N --apply-fix 1`

### Step 2: 驱动底层引擎生成预审包
协调器执行底层 CLI，生成 `reports/.cache/pre_audit_bundle.json`，提取行号绝对保真的清洗切片、跨章缝合文本与账本快照。

### Step 3: 执行多维度专家审查
调度 4 专家矩阵（或串行 4 角色思辨），对照 `references/` 中的 5 份规则库逐项体检，生成结构化缺陷项与短句修复文本。

### Step 4: 聚合报告与目录归档收口
严格对照 `references/report-template.md` 生成规范 Markdown：
1. 立即覆盖写入：`reports/LATEST_REPORT.md`（方便作者快速翻阅）；
2. 逐章分卷落盘：`reports/单章审查/{分卷目录}/第{章号}章_审查报告.md`；
3. 控制台打印：高亮概要与 P0/P1 警告。

### Step 5: 退出码映射与质量把关
依据报告中的最高缺陷严重度，确定返回状态码：
- **Exit Code 0 (绿灯通过)**：无缺陷，或仅存在 P2/P3 建议项；
- **Exit Code 1 (黄灯警告)**：存在 P1 级严重失误（若带 `--strict` 则阻断）；
- **Exit Code 2 (红灯阻断)**：存在 P0 级致命断裂（世界观吃书、死人复活），**坚决阻断**；
- **Exit Code 3 (系统异常)**：文件缺失、乱码或防脏写拦截。

### Step 6: 采纳修复与三行锚点安全回写
当作者回复同意采纳（如“采纳方案1”或执行 `--apply-fix 1`）时：
1. **原子备份**：自动备份原稿至 `reports/.bak/{章节名}_{时间戳}.bak`；
2. **三行锚点消歧匹配**：在 `[line-30, line+30]` 局部邻域内执行前一句+目标句+后一句唯一匹配替换；
3. **保持编码与换行**：回写严格遵循源文件的 UTF-8-SIG/GB18030 与 CRLF/LF 格式；
4. 联动更新账本 JSON 与 Markdown。

---

## 五、参考规范库导航 (References Directory)

| 规范文档 | 核心内容 | 核心专员 |
| :--- | :--- | :--- |
| `references/audit-rules.md` | 生理负荷、时空时序、战力阶梯（同境小层 vs 跨大境界规则外力）、存在性与信息不对称逻辑 | Agent B |
| `references/ledger-model.md` | 七类资产数据字典、全生命周期状态机、暗线伏笔缓冲池、多实体借还协议、百万字抗膨胀设计 | Agent A |
| `references/chapter-boundary.md` | 跨章接缝缝合模型、四大断章类型、前三段破局法则、POV 侧面烘托豁免、闪回幻境视界隔离栈 | Agent B |
| `references/short-sentence-style.md` | 移动端大黑块判定、行数保持白名单掩码契约、AI 连词禁词表、网文短句美学重构三部曲 | Agent C |
| `references/first-principles.md` | 读者追读第一性原理、4 维量化判定卡尺（危机/信息差/钩子/反应）、毒舌老书虫对抗式审查指南 | Agent D |
| `references/report-template.md` | P0~P3 分级报告统一 Markdown Schema、双方案短句修复 PatchSpec 契约、归档目录树 | 主协调器 |

---

## 六、底层工具套件清单 (Scripts Manifest)

所有底层脚本位于 `scripts/` 目录下，遵循**零第三方依赖**承诺（仅依赖 Python 3.8+ 标准库）：

1. `scripts/story_audit.py`：CLI 主入口管线、多模式参数解析、流程串联与标准退出码；
2. `scripts/safe_io.py`：编码嗅探（utf-8-sig / utf-8 / gb18030）、换行规整与原子备份；
3. `scripts/chapter_resolver.py`：自然数值排序、零依赖大写中文数字解析、序章与子章节定位；
4. `scripts/ledger_engine.py`：双轨账本同步、防脏写拦截、多实体资产状态机与分卷快照；
5. `scripts/format_scanner.py`：行数保持白名单掩码、大黑块与长难句检测、AI 连词扫描；
6. `scripts/chapter_linker.py`：跨章前后 300 字提取、显式 POV 转场识别与闪回隔离标注；
7. `scripts/safe_writer.py`：三行锚点匹配、局部邻域消歧与安全原子回写；
8. `scripts/types.py`：强类型数据模型定义（ChapterItem, FormatFinding, BoundaryContext, PatchSpec）。
