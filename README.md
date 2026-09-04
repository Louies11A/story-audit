# 长篇网文深度审查系统 (story-audit)

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-230%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen.svg)]()
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20external-orange.svg)]()

> **专治长篇网络小说连载中的五大核心绝症**：
> 1. **资产失忆**：灵石宝物凭空出装、战力暗改、境界缩水、随身重宝神秘失踪；
> 2. **因果断裂**：跨章时空错位、伤势痊愈无交代、闪回幻境与客观现实混淆；
> 3. **视觉窒息与AI套路**：手机阅读大黑块、跨屏长自然段、AI 翻译腔、典型 not-is 对仗句式、章末出戏总结体；
> 4. **平台算法脱节**：番茄完读率断崖、起点追读比崩塌、知乎盐言第一人称穿帮；
> 5. **作者自嗨**：缺乏预期管理、读者情绪价值坍塌、毒点自嗨导致暴跌追读。

---

## 目录

- [一、核心架构理念](#一核心架构理念)
- [二、双层协同架构图](#二双层协同架构图)
- [三、四大升级模块特性](#三四大升级模块特性)
  - [1. 底层深度 AI 模式扫描器 (ai_patterns_checker)](#1-底层深度-ai-模式扫描器-ai_patterns_checker)
  - [2. 单文件作者偏好状态机 (author_memory)](#2-单文件作者偏好状态机-author_memory)
  - [3. 宿主探测与子代理递归防爆哨兵 (runtime_detector)](#3-宿主探测与子代理递归防爆哨兵-runtime_detector)
  - [4. 平台专属商业门禁卡尺 (platform_rubrics)](#4-平台专属商业门禁卡尺-platform_rubrics)
  - [5. 跨批长篇因果状态机 (audit_state)](#5-跨批长篇因果状态机-audit_state)
- [四、标准化报告契约 (Report Contract)](#四标准化报告契约-report-contract)
- [五、快速上手与 CLI 指南](#五快速上手与-cli-指南)
  - [1. 基础单章审查与平台门禁](#1-基础单章审查与平台门禁)
  - [2. 批量范围连审与因果继承](#2-批量范围连审与因果继承)
  - [3. 作者画像与偏好联动](#3-作者画像与偏好联动)
  - [4. 资源账本生命周期管理](#4-资源账本生命周期管理)
  - [5. 短句化补丁安全回写](#5-短句化补丁安全回写)
- [六、CLI 退出码 (Exit Codes) 规范](#六cli-退出码-exit-codes-规范)
- [七、缺陷分级体系 (P0 ~ P3)](#七缺陷分级体系-p0--p3)
- [八、测试套件与工程验证](#八测试套件与工程验证)

---

## 一、核心架构理念

长篇网络小说创作动辄数百万字，单靠大语言模型（LLM）的模糊记忆极易产生“越写越崩、幻觉频发”的灾难。

`story-audit` 采用 **“底层 Python 零依赖确定性工具链 + 顶层多 Agent 专家矩阵对抗审判”** 的双层解耦架构：
1. **确定性防线（Zero-Dependency Deterministic Tooling）**：
   - 不依赖任何第三方库，纯标准库毫秒级执行；
   - 负责编码嗅探保真（UTF-8/GB18030）、自然章节排序、双轨资源状态机流转、排版正则扫描、深度 AI 句式指纹检测、宿主运行时探测与递归防爆、跨批因果继承栈以及原子三行锚点安全回写。
2. **审美与商业门禁防线（Adversarial Review & Platform Rubrics）**：
   - 主审查调度器对接番茄（算法完读率）、起点（追读比）、知乎（盐言强第一人称）三大平台商业卡尺；
   - 调度 4 个细分领域专家 Agent，分别持专属卡尺进行深度语义对抗审判，以真实读者的追读期待为第一性原理，无情撕碎逻辑硬伤与自嗨毒点。

---

## 二、双层协同架构图

```
                       ┌──────────────────────────────────────────────┐
                       │     用户触发指令（/story-audit 或 CLI 启动）    │
                       └──────────────────────┬───────────────────────┘
                                              │
                                              ▼
                 ┌────────────────────────────────────────────────────────┐
                 │    【底层：Python 确定性工具链】（零外部依赖、毫秒执行）   │
                 │   1. safe_io.py        : 智能编码嗅探、换行规整与原子写盘 │
                 │   2. chapter_resolver  : 智能提取章号、自然排序与断号体检 │
                 │   3. ledger_engine.py  : 双轨资产账本、状态机流转与防脏写 │
                 │   4. format_scanner.py : 白名单掩码(面板/口诀)+排版扫描  │
                 │   5. ai_patterns_checker: 深度 AI 套路句式与对仗扫描     │
                 │   6. chapter_linker.py : 跨章接缝、POV 漂移与叙事隔离区  │
                 │   7. runtime_detector  : 运行时探测与子代理递归防爆哨兵   │
                 │   8. platform_rubrics  : 四大平台商业门禁质量卡尺评估     │
                 │   9. author_memory.py  : 单文件作者偏好状态机与只读画像   │
                 │  10. audit_state.py    : 跨批长篇因果状态机与继承栈       │
                 │  11. 导出精简预审包 (pre_audit_bundle.json)            │
                 └────────────────────────────┬───────────────────────────┘
                                              │ 传递精简结构化上下文
                                              ▼
                 ┌────────────────────────────────────────────────────────┐
                 │       【顶层：多 Agent 专家矩阵】（深度语义与审美审查）   │
                 │                                                        │
                 │   Agent A (账本专员) : 资产一致性、出装合法性、伏笔标记 │
                 │   Agent B (事实专员) : 时空连续性、伤势负荷、POV 承接   │
                 │   Agent C (排版质检) : 手机端大黑块、AI 套路腔、短句气流 │
                 │   Agent D (对抗审判) : 平台商业门禁、读者第一性原理卡尺 │
                 └────────────────────────────┬───────────────────────────┘
                                              │ 聚合审查输出
                                              ▼
                 ┌────────────────────────────────────────────────────────┐
                 │         【报告归档落盘与三行锚点安全回写】              │
                 │   - 报告头部固定英文元数据键规范化输出                 │
                 │   - 单章归档: reports/单章审查/{分卷}/第N章_审查报告.md│
                 │   - 最新总览: reports/LATEST_REPORT.md                 │
                 │   - 批量汇总: reports/BATCH_SUMMARY_SCOPE_{scope}.md   │
                 │   - 跨批因果状态机: reports/.audit_state.json          │
                 │   - safe_writer.py 实施三行锚点消歧安全回写 (--apply-fix)│
                 └────────────────────────────────────────────────────────┘
```

---

## 三、四大升级模块特性

### 1. 底层深度 AI 模式扫描器 (`ai_patterns_checker`)
毫秒级深度扫描 6 大高危 AI 套路句式：
- **`not-is-comparison`**：“不是……而是……”对仗句式，反序对比“是……而不是……”；
- **`em-dash`**：正文中残留破折号“——”硬停顿；
- **`voice-contrast`**：音量与神态反差腔（“声音不大，却清晰传入……”、“语气平淡，却让所有人心中一凛”）；
- **`negation-parade`**：连续否定排比（“没有伴奏，没有和声，没有提词器”；“没X，没Y……只是Z”）；
- **`trailer-ending / trailer-summary`**：章末出戏预告式收尾与状态总结体（“他不知道的是……”、“这一夜注定无人入眠”）；
- **`god-view-exposition`**：Gate G 上帝解释腔/替读者划重点，以及监控摄像头式纯动作清单。

### 2. 单文件作者偏好状态机 (`author_memory`)
- 落盘路径：`设定/_author-memory-state.json` 与只读视图 `设定/作者画像.md`；
- **记忆铁律**：查询结果硬上限 ≤ 2048 字节；
- **边界铁律**：仅作意图解释辅助，**绝对不能降低 Rubric 严重度、把事实冲突判为无问题或跳过平台门禁**；
- **反近亲繁殖铁律**：坚决不学习系统内部警告、报错与模板话术（自动拦截 P0/P1/FormatFinding 等特征词）。

### 3. 宿主探测与子代理递归防爆哨兵 (`runtime_detector`)
- 自适应探测环境：Codex, Claude, OpenCode, Antigravity, Generic (Shell)；
- **Subagent Recursion Guard**：探测自身是否已处于子代理上下文中；若已处于子代理环境，强制禁止嵌套再次 spawn，平稳降级为 solo，杜绝死锁崩溃。

### 4. 平台专属商业门禁卡尺 (`platform_rubrics`)
- **番茄小说 (`references/rubrics/fanqie.md`)**：前3段核心悬念/钩子、千字情绪波动、3章翻页动力、完读率预测红线；
- **起点中文网 (`references/rubrics/qidian.md`)**：3000字爽点节点、50章实力晋阶、金手指在场率、追读比门禁；
- **知乎盐言故事 (`references/rubrics/zhihu.md`)**：强第一人称限制（“我”视角严格统一，第三人称触发 P1）、首句跳失率控制、伏笔强反转闭环、8000-13000字篇幅；
- **通用网文卡尺 (`references/rubrics/generic.md`)**：黄金三问、7 状态变化、开局同质化判定、高潮场景四阶力学（蓄能 → 假胜 → 崩解 → 反转）、对话三大病灶（机械问答、科普嘴、不分场合）。

### 5. 跨批长篇因果状态机 (`audit_state`)
- 在长篇批量连审（`--scope`）时原子维护 `reports/.audit_state.json`；
- 记录已完成章节、当前批次以及**“上一批未解决的开放缺陷与伏笔承诺”**；
- 下一批连审启动时自动装载为 `Inherited Items`，校验跨批因果一致性。

---

## 四、标准化报告契约 (Report Contract)

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

## 五、快速上手与 CLI 指南

所有操作均可通过 `python scripts/story_audit.py` 命令执行：

### 1. 基础单章审查与平台门禁

```bash
# 默认定位最新章审查
python scripts/story_audit.py

# 指定章节与目标发布平台（支持 fanqie, qidian, zhihu, generic）
python scripts/story_audit.py --chapter 1 --platform fanqie

# 指定执行模式（full 多代理协同 / lean 精简 / solo 单机，默认 auto 自动探测降级）
python scripts/story_audit.py --chapter 1 --mode full

# 严格模式：发现 P1 级严重问题时直接返回非零退出码（适用于 CI/CD 门禁）
python scripts/story_audit.py --chapter 1 --strict
```

### 2. 批量范围连审与因果继承

```bash
# 连审第 1 章至第 5 章，生成大盘汇总并原子更新跨批因果状态机
python scripts/story_audit.py --scope 1-5 --platform qidian

# 连审第 6 章至第 10 章，自动装载上一批未解决的开放缺陷与伏笔承诺作为 Inherited Items
python scripts/story_audit.py --scope 6-10 --platform qidian
```

### 3. 作者画像与偏好联动

```bash
# 初始化作者记忆状态机
python scripts/author_memory.py init

# 录入作者风格偏好
python scripts/author_memory.py record --key "主角性格" --value "果决冷静，杀伐果断，不圣母不多话" --category story_design

# 审查时联动作者记忆（作为意图解释辅助）
python scripts/story_audit.py --chapter 1 --author-memory
```

### 4. 资源账本生命周期管理

```bash
# 首次建账模式（继承已有资产设定，流式扫描前 30 章建立基线）
python scripts/story_audit.py --init --scope 1-30

# 分卷封账（锁定第一卷，归档当前卷快照）
python scripts/story_audit.py --checkpoint --volume 1

# 从用户修改过的 Markdown 账本反向同步回 JSON 状态机
python scripts/story_audit.py --sync-from-md
```

### 5. 短句化补丁安全回写

```bash
# 通过参数传入精确的消歧三行锚点实施无损回写
python scripts/story_audit.py \
  --chapter 1 \
  --target-line 42 \
  --old-text "林凡心中大惊，急忙运转功法，然而体内灵力却如泥牛入海一般毫无反应。" \
  --new-text "林凡心中一沉。\n周天功法骤然空转。\n体内灵力如泥牛入海，死寂无声。" \
  --context-before "四周黑雾骤然升腾。" \
  --context-after "黑影已悄然欺身至三步之内。"
```

---

## 六、CLI 退出码 (Exit Codes) 规范

| 退出码 | 状态说明 | 触发场景 |
| :---: | :--- | :--- |
| **`0`** | **审查通过 / 仅轻微瑕疵** | 全书无 P0 缺陷；或存在 P2/P3 问题但在常规模式下运行。 |
| **`1`** | **严重阻断 (P1 违规)** | 在开启 `--strict` 严格模式下，检测到 P1 级违规（资产断裂、时空错位、平台门禁严重不符等）。 |
| **`2`** | **致命阻塞 (P0 阻断)** | 发现死亡复活、主线硬伤或账本脏写冲突。 |
| **`3`** | **运行异常 / 参数错误** | 指定章节不存在、目录找不到正文、参数缺失或格式非法。 |

---

## 七、缺陷分级体系 (P0 ~ P3)

- **🚨 P0（致命死穴 - 立即停笔整改）**：
  - 核心随身宝物/装备凭空出装或凭空消失；
  - 死亡角色无转世复活设定原地复活；
  - 账本状态机流转非法；
  - 目录探测全量失效或数据覆盖风险。
- **⚠️ P1（严重硬伤 - 本章发布前必须修复）**：
  - 跨章时空错位（上一章深夜重伤，下一章清晨毫无痕迹生龙活虎）；
  - 战斗等级体系越级崩坏，前后设定数值矛盾；
  - 知乎盐言故事出现第三人称主导（破坏强第一人称限制）；
  - 关键伏笔标签遗失或断层。
- **🔍 P2（一般缺陷 / 局部瑕疵）**：
  - 单自然段超过 120 字（手机端大黑块窒息排版）；
  - 典型 AI 对仗句式（不是……而是……、声音不大却清晰传入等）；
  - 正文残留破折号“——”硬停顿；
  - 番茄前 3 段无核心冲突/悬念；
  - 起点单章金手指缺席。
- **💡 P3（优化建议 - 润色提升）**：
  - 连续出现“然而”、“与此同时”等 AI 翻译腔粘滞连词；
  - 单章字数偏薄或偏厚（不符平台推荐区间）。

---

## 八、测试套件与工程验证

本项目践行严格的测试驱动开发（TDD）规范，全量测试位于 `tests/` 目录：

```bash
# 运行全量测试套件
pytest -v

# 运行覆盖率分析报告
pytest --cov=scripts --cov-report=term-missing
```

### 测试指标

- **用例总数**：**230 项测试** 全部通过（100% Pass Rate）；
- **执行时间**：~ 2.1 秒（极速并发执行）；
- **用例覆盖分布**：
  - `test_ai_patterns_checker.py`：7 项测试（深度 AI 句式指纹、反序对比、掩码排除等）
  - `test_author_memory.py`：5 项测试（初始化、反近亲繁殖过滤、2048 字节硬上限等）
  - `test_runtime_detector.py`：3 项测试（宿主环境探测、子代理递归防爆哨兵等）
  - `test_platform_rubrics.py`：3 项测试（番茄、起点、知乎专属门禁卡尺）
  - `test_audit_state.py`：2 项测试（跨批状态机原子持久化与 Inherited Items 继承）
  - `test_story_audit_upgrades.py`：4 项测试（端到端固定英文元数据键头部、模式自适应降级等）
  - 原生套件（`test_format_scanner`, `test_ledger_engine`, `test_safe_writer`, 等）：206 项测试全部通过。

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。欢迎网络文学创作者、AI 写作助手开发者与文学工程探索者共同维护！
