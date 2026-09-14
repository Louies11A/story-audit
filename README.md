# 长篇网文深度审查系统 (story-audit)

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Tests](https://img.shields.io/badge/tests-751%20passed-brightgreen.svg)
![Coverage](https://img.shields.io/badge/coverage-92%25-brightgreen.svg)
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
- [三、五大升级模块特性](#三五大升级模块特性)
  - [1. 底层深度 AI 模式扫描器 (ai_patterns_checker)](#1-底层深度-ai-模式扫描器-ai_patterns_checker)
  - [2. 单文件作者偏好状态机 (author_memory)](#2-单文件作者偏好状态机-author_memory)
  - [3. 宿主探测与子代理递归防爆哨兵 (runtime_detector)](#3-宿主探测与子代理递归防爆哨兵-runtime_detector)
  - [4. 平台专属商业门禁卡尺 (platform_rubrics)](#4-平台专属商业门禁卡尺-platform_rubrics)
  - [5. 跨批长篇因果状态机 (audit_state)](#5-跨批长篇因果状态机-audit_state)
- [四、标准化报告契约 (Report Contract)](#四标准化报告契约-report-contract)
- [五、快速上手与 Python API 指南](#五快速上手与-python-api-指南)
  - [1. 基础单章审查与平台门禁 (`audit_chapter`)](#1-基础单章审查与平台门禁-audit_chapter)
  - [2. 批量范围连审与因果继承 (`audit_scope`)](#2-批量范围连审与因果继承-audit_scope)
  - [3. 作者画像与偏好联动 (`AuthorMemory`)](#3-作者画像与偏好联动-authormemory)
  - [4. 资源账本生命周期管理 (`init_ledger`, `checkpoint_volume`, `sync_ledger_from_md`)](#4-资源账本生命周期管理-init_ledger-checkpoint_volume-sync_ledger_from_md)
  - [5. 短句化补丁安全回写 (`apply_fix`)](#5-短句化补丁安全回写-apply_fix)
  - [6. 伏笔显式裁决 (`adjudicate_foreshadowing`)](#6-伏笔显式裁决-adjudicate_foreshadowing)
  - [7. 专家结果接入与汇总 (`ExpertResult`, `archive_expert_results`)](#7-专家结果接入与汇总-expertresult-archive_expert_results)
  - [8. 回写后的复审协调 (`get_pending_rechecks`, `resolve_recheck`, `record_issue_closure`)](#8-回写后的复审协调-get_pending_rechecks-resolve_recheck-record_issue_closure)
  - [9. 资产事件预览与幂等提交 (`preview_asset_changes`, `confirm_asset_event`)](#9-资产事件预览与幂等提交-preview_asset_changes-confirm_asset_event)
  - [10. 逐章流式审查与运行清单 (`iter_audit_scope`)](#10-逐章流式审查与运行清单-iter_audit_scope)
  - [11. 问题处置与规则解释 (`record_finding_disposition`)](#11-问题处置与规则解释-record_finding_disposition)
  - [12. 上下文预算与按需检索 (`build_context_package`, `query_asset_history`)](#12-上下文预算与按需检索-build_context_package-query_asset_history)
- [六、状态码 (Status Codes) 规范](#六状态码-status-codes-规范)
- [七、缺陷分级体系 (P0 ~ P3)](#七缺陷分级体系-p0--p3)
- [八、测试套件与工程验证](#八测试套件与工程验证)

---

## 一、核心架构理念

> **架构铁律：本项目采用纯模块化 Python API 驱动设计，不提供亦不涉及任何 CLI 命令行接口。后续所有功能开发与生态扩展均严格围绕 Python API、强类型数据契约与 Agent 工具函数展开，坚决不涉及 CLI。**

**当前实现范围：** Python API 执行确定性规则检查、账本与报告管理，不会自行调用 LLM 或启动专家。四专家矩阵由上层宿主协调器按技能规范组织；正文的完整语义一致性、人物动机和读者追读评价，需要实际专家审查后才能给出结论。

长篇网络小说创作动辄数百万字，单靠大语言模型（LLM）的模糊记忆极易产生“越写越崩、幻觉频发”的灾难。

`story-audit` 采用 **“底层 Python 零依赖确定性工具链 + 顶层多 Agent 专家矩阵对抗审判”** 的双层解耦架构：
1. **确定性防线（Zero-Dependency Deterministic Tooling）**：
   - 不依赖任何第三方运行库，使用 Python 标准库本地执行；耗时随章节与账本规模变化；
   - 负责编码嗅探保真（UTF-8/GB18030）、自然章节排序、双轨资源状态机流转、排版正则扫描、深度 AI 句式指纹检测、宿主运行时探测与递归防爆、跨批因果继承栈以及原子三行锚点安全回写。
2. **审美与商业门禁防线（Adversarial Review & Platform Rubrics）**：
   - 主审查调度器对接番茄（算法完读率）、起点（追读比）、知乎（盐言强第一人称）三大平台商业卡尺；
   - 为宿主协调器提供 4 个领域专家的职责与卡尺。宿主实际调度专家并收集证据后，才能生成对应的深度语义裁决。

---

## 二、双层协同架构图

```
                       ┌──────────────────────────────────────────────┐
                       │     用户触发指令（/story-audit 或 Python API 驱动）    │
                       └──────────────────────┬───────────────────────┘
                                              │
                                              ▼
                 ┌────────────────────────────────────────────────────────┐
                 │    【底层：Python 确定性工具链】（零外部依赖、本地执行）   │
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
                 │   - apply_fix() 实施三行锚点消歧安全回写               │
                 └────────────────────────────────────────────────────────┘
```

---

## 三、五大升级模块特性

### 1. 底层深度 AI 模式扫描器 (`ai_patterns_checker`)
按确定性规则扫描以下 6 类 AI 套路句式：
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
- **Subagent Recursion Guard**：探测子代理上下文后返回 `solo` 及降级原因，供宿主协调器避免再次嵌套派生专家；Python API 自身不启动子代理。

### 4. 平台专属商业门禁卡尺 (`platform_rubrics`)
- **番茄小说 (`references/rubrics/fanqie.md`)**：前3段核心悬念/钩子、千字情绪波动、3章翻页动力、完读率预测红线；
- **起点中文网 (`references/rubrics/qidian.md`)**：3000字爽点节点、50章实力晋阶、金手指在场率、追读比门禁；
- **知乎盐言故事 (`references/rubrics/zhihu.md`)**：强第一人称限制（“我”视角严格统一，第三人称触发 P1）、首句跳失率控制、伏笔强反转闭环、8000-13000字篇幅；
- **通用网文卡尺 (`references/rubrics/generic.md`)**：黄金三问、7 状态变化、开局同质化判定、高潮场景四阶力学（蓄能 → 假胜 → 崩解 → 反转）、对话三大病灶（机械问答、科普嘴、不分场合）。

### 5. 跨批长篇因果状态机 (`audit_state`)
- 在 `audit_scope()` 长篇批量连审时维护 `reports/.audit_state.json`；
- 记录已完成章节、当前批次以及**“上一批未解决的开放缺陷与伏笔承诺”**；
- 下一批连审启动时自动装载为 `Inherited Items`，供宿主继续核验跨批因果与伏笔；存储和继承本身不构成语义审查结论。

确定性复审只更新本轮实际运行检查器覆盖的问题。专家、人工、来源未知的旧记录及未覆盖的平台问题会继续保留；同一确定性发现重复审查不会累积。确定性问题经重扫不再命中时，状态保留关闭原因、复核依据和正文版本，供后续追溯。

审查产物采用状态优先发布：先原子保存 `.audit_state.json`，成功后才写预审包、LATEST 与归档报告。批量执行期间各章产物只在内存暂存，中途失败或最终保存失败时不落盘，避免报告与持久状态互相矛盾。

---

## 四、标准化报告契约 (Report Contract)

所有单章与批量报告头部逐字输出固定英文元数据键：

```markdown
=== story-audit 深度审查报告 ===
Requested Mode: full
Effective Mode: solo
Fallback: python_api_deterministic_only
Review Stage: deterministic_precheck
Expert Review: not_executed
Platform Rubric: fanqie
Genre: 科幻末世
Scope: 第001章
```

该示例表示 Python API 只完成了确定性预检。`Requested Mode` 记录期望模式，`Effective Mode` 记录实际执行；其他宿主或子代理限制也会出现在降级原因中。只有宿主实际执行并汇总专家结果后，才能报告专家审查完成。

统一 Findings Schema 条目包含：`severity` (P0/P1/P2/P3), `category` (structure/character/prose/consistency/platform/factual/format/causal), `location`, `evidence`, `issue`, `fix`。
铁律约束：事实与因果类缺陷的 `fix` 严格限制为事实统一方向，严禁文学发挥。

---

## 五、快速上手与 Python API 指南

确定性工具链通过 Python API 提供，可嵌入 Agent 框架、批处理脚本与上层写作工具箱；专家语义审查由宿主另行执行：

公开入口会先验证输入：项目目录必须存在，章号必须是有限的非负数值，`volume` 必须是正整数，布尔开关必须传入 `True` / `False`。`platform` 与 `mode` 接受合法值的大小写和首尾空格；未知值返回状态码 3。范围支持小数章、倒序边界和边界空格；`init_ledger(scope_str=None)` 表示全部章节，空字符串不表示全书。

章号必须能唯一定位文件。同一目标章号出现在多个卷或文件中时，单章、回写及包含该章的批量操作返回 3，需先统一章号。非法参数在生成正文备份、报告或账本前拒绝；三种返回报告的 API 失败时使用 `(3, Path(""))`，调用者应先检查状态码。

### 1. 基础单章审查与平台门禁 (`audit_chapter`)

```python
from pathlib import Path
from scripts.story_audit import audit_chapter

project_dir = Path(".")

# 默认定位最新章审查（返回状态码与归档报告路径）
status_code, report_path = audit_chapter(project_dir)

# 指定章节与目标发布平台（支持 fanqie, qidian, zhihu, generic）
status_code, report_path = audit_chapter(project_dir, chapter_index=1, platform="fanqie")

# 记录期望模式；Python API 本身只运行确定性预检，专家执行情况以报告为准
status_code, report_path = audit_chapter(project_dir, chapter_index=1, mode="full")

# 严格模式：发现 P1 级严重问题时返回状态码 1（适用于 CI/CD 质量门禁拦截）
status_code, report_path = audit_chapter(project_dir, chapter_index=1, strict=True)
```

### 2. 批量范围连审与因果继承 (`audit_scope`)

```python
from scripts.story_audit import audit_scope

# 连审第 1 章至第 5 章，生成大盘汇总并原子更新跨批因果状态机
status_code, summary_path = audit_scope(project_dir, scope_str="1-5", platform="qidian")

# 连审第 6 章至第 10 章，自动装载上一批未解决的开放缺陷与伏笔承诺作为 Inherited Items
status_code, summary_path = audit_scope(project_dir, scope_str="6-10", platform="qidian")
```

每次成功批量审查都会保存独立历史报告，保留小数章号并使用唯一运行编号。返回的范围汇总路径及 `reports/LATEST_REPORT.md` 仍提供最近结果；追溯以 `reports/批量审查/` 中的独立历史为准。

### 3. 作者画像与偏好联动 (`AuthorMemory`)

```python
from scripts.author_memory import AuthorMemory
from scripts.story_audit import audit_chapter

# 初始化作者记忆状态机
mem = AuthorMemory(project_dir)
mem.init()

# 录入作者风格偏好
mem.record(
    key="主角性格",
    value="果决冷静，杀伐果断，不圣母不多话",
    category="story_design",
)

# 审查时联动作者记忆（作为意图解释辅助，受 2048 字节硬上限保护）
status_code, report_path = audit_chapter(project_dir, chapter_index=1, author_memory=True)
```

### 4. 资源账本生命周期管理 (`init_ledger`, `checkpoint_volume`, `sync_ledger_from_md`)

```python
from scripts.story_audit import init_ledger, checkpoint_volume, sync_ledger_from_md

# 首次建账模式（继承已有资产设定，流式扫描前 30 章建立基线）
status_code, report_path = init_ledger(project_dir, scope_str="1-30")

# 分卷封账（锁定第一卷，归档当前卷快照）
status_code = checkpoint_volume(project_dir, volume=1)

# 从用户修改过的 Markdown 账本反向同步回 JSON 状态机
status_code = sync_ledger_from_md(project_dir)
```

### 5. 短句化补丁安全回写 (`apply_fix`)

```python
from scripts.story_audit import apply_fix

# 通过 Python API 传入精确的消歧三行锚点实施无损回写
status_code = apply_fix(
    project_dir=project_dir,
    chapter_index=1,
    target_line=42,
    old_text="林凡心中大惊，急忙运转功法，然而体内灵力却如泥牛入海一般毫无反应。",
    new_text="林凡心中一沉。\n周天功法骤然空转。\n体内灵力如泥牛入海，死寂无声。",
    context_before="四周黑雾骤然升腾。",
    context_after="黑影已悄然欺身至三步之内。",
)

# 亦支持直接传入 dict 补丁对象
status_code = apply_fix(
    project_dir=project_dir,
    chapter_index=1,
    patch={
        "target_line": 42,
        "old_text": "林凡心中大惊，急忙运转功法，然而体内灵力却如泥牛入海一般毫无反应。",
        "new_text": "林凡心中一沉。\n周天功法骤然空转。\n体内灵力如泥牛入海，死寂无声。",
        "context_before": "四周黑雾骤然升腾。",
        "context_after": "黑影已悄然欺身至三步之内。",
    },
)
```

### 6. 伏笔显式裁决 (`adjudicate_foreshadowing`)

```python
from scripts.story_audit import adjudicate_foreshadowing

# 关闭一条伏笔承诺：记录原因、证据与正文版本，条目进入历史并停止跟踪
status_code, state_path = adjudicate_foreshadowing(
    project_dir=project_dir,
    name="海门钥匙",
    action="close",
    reason="第 12 章已明确回收",
    evidence="第12章 钥匙插入海门锁孔。",
    source="author",
    chapter=12,
)

# 重新开启：必须说明原因并给出章节，记录重新开启的正文版本
status_code, state_path = adjudicate_foreshadowing(
    project_dir=project_dir,
    name="海门钥匙",
    action="reopen",
    reason="新增支线再次启用该伏笔",
    chapter=18,
)
```

已确认或已关闭的条目不会被正文旧标签重新激活；重复确认是幂等空操作；同名伏笔按来源章号分别登记裁决。

### 7. 专家结果接入与汇总 (`ExpertResult`, `archive_expert_results`)

宿主执行完语义审查后，用统一契约回传实际结果；底层只接收真实执行结果，不代为调用专家。

```python
from scripts.safe_io import read_file_safe
from scripts.story_audit import ExpertResult, archive_expert_results, compute_text_fingerprint
from scripts.types import Finding

chapter_text, _, _ = read_file_safe(project_dir / "正文" / "第001章.txt")
result = ExpertResult(
    expert="因果审查员",
    status="completed",              # completed / failed / not_executed
    chapters=[1],
    text_fingerprint=compute_text_fingerprint({1: chapter_text}),
    findings=[
        Finding(
            severity="P1",
            category="causal",
            location="第001章 行2",
            evidence="他握紧了钥匙。",
            issue="钥匙来源缺失",
            fix="【事实对齐】补齐来源交代。",
        )
    ],
)

status_code, summary_path = archive_expert_results(project_dir, [result])
```

完成、失败、未执行三种状态在汇总与报告中分别呈现；重复提交按身份幂等；正文指纹与当前章节不一致的结果标记为过期且不覆盖当前裁决；专家 P0/P1 以专家来源并入开放缺陷，后续确定性复审不会清除。

### 8. 回写后的复审协调 (`get_pending_rechecks`, `resolve_recheck`, `record_issue_closure`)

`apply_fix` 写回成功后会登记补丁事件，并把第 N 章报告与第 N+1 章接缝检查登记为待复审项：

```python
from scripts.audit_state import load_audit_state
from scripts.safe_io import read_file_safe
from scripts.story_audit import (
    compute_text_fingerprint,
    get_pending_rechecks,
    is_chapter_version_audited,
    record_issue_closure,
    resolve_recheck,
)

status_code, pending = get_pending_rechecks(project_dir)
# pending["pending"] 每项含 chapter、scope（chapter/seam）、当前正文版本与 needs_reaudit

# 人工解除某条待办（留原因与来源），或单独登记"问题经复核关闭"事件
resolve_recheck(project_dir, chapter=1, scope="chapter", resolution="已人工复核", source="author")
record_issue_closure(project_dir, chapter=1, issue="钥匙来源缺失", reason="已统一来源", source="author")

# 完成章号不再等于当前版本已审：用正文指纹判断
chapter_text, _, _ = read_file_safe(project_dir / "正文" / "第001章.txt")
state = load_audit_state(project_dir / "reports")
audited = is_chapter_version_audited(state, 1, compute_text_fingerprint({1: chapter_text}))
```

正文版本变化后旧归档报告会被保存到 `reports/单章审查/历史/`，不会被静默覆盖。

### 9. 资产事件预览与幂等提交 (`preview_asset_changes`, `confirm_asset_event`)

候选变更只做预览，必须经作者或宿主确认后才进入账本：

```python
from scripts.story_audit import confirm_asset_event, preview_asset_changes

status_code, preview = preview_asset_changes(project_dir, chapter_index=3)
for candidate in preview["candidates"]:
    # 逐条复核：方向、数量、所有者均可能被启发式误判
    confirm_asset_event(
        project_dir,
        event=candidate,
        decision="accept",
        owner="主角",
        reason="第 3 章明确获得",
        source="author",
    )
```

同一事件重复提交不会重复加账或扣账；改判会被拒绝；消耗未入账资产会被拒绝，避免制造负事实；两位角色的同名装备各自独立记账；裁决流水与证据保留在账本 JSON 与 Markdown。

### 10. 逐章流式审查与运行清单 (`iter_audit_scope`)

需要逐章结果（而不是一次拿到大盘报告）时使用流式入口，宿主可以边产出边组织专家审查：

```python
from scripts.story_audit import iter_audit_scope

for item in iter_audit_scope(project_dir, scope_str="1-30", platform="qidian"):
    if item["kind"] == "chapter":
        # item["chapter"] / item["text_version"] / item["findings"] /
        # item["bundle"]（该章预审包） / item["report_path"]
        ...
    else:
        # item["kind"] == "run_summary"：completed / failed / not_executed 与产物路径
        ...
```

每次运行写出 `reports/批量审查/运行清单/{run_id}.json`；中途失败时清单区分已完成、失败与未执行章节，失败运行不发布章节报告与预审包。运行成功时会同时产出与 `audit_scope` 一致的 `LATEST_REPORT.md`、范围汇总与历史归档。生成器必须消费到底，提前中断会让清单停留在 `running`。

### 11. 问题处置与规则解释 (`record_finding_disposition`)

确定性发现带稳定问题编号与规则元数据（规则 id、版本、阈值、命中条件、上下文），作者可以对规则建议登记处置：

```python
from scripts.story_audit import get_finding_dispositions, record_finding_disposition

record_finding_disposition(
    project_dir,
    finding_id="finding-...",     # 也可用 finding= 传入报告中的发现对象
    decision="false_positive",    # accepted / deferred / false_positive
    reason="该长段为刻意留白的排版效果",
    source="author",
)

status_code, data = get_finding_dispositions(project_dir)
```

处置带正文版本：正文变化后查询会返回 `needs_reverification`，处置不再自动生效。因果、事实、一致性问题与平台门禁发现不会被作者偏好免除，仍然保留在开放缺陷中。

### 12. 上下文预算与按需检索 (`build_context_package`, `query_asset_history`)

长篇连载的账本会持续增长，预审包默认只携带每项资产最近 5 条流水：

```python
from scripts.story_audit import build_context_package, query_asset_history

status_code, data = build_context_package(project_dir, chapter_index=42, budget=60_000)
# data["budget"]            预算口径、实际用量、固定开销、是否超限
# data["omissions"]         省略了什么、原因、完整证据定位与检索提示
# data["insufficient_context"]  历史被截断等上下文不足标记

# 关键证据在更早历史时按需展开（offset 越大越早）
status_code, history = query_asset_history(project_dir, name="灵石", limit=20, offset=5)
```

被省略的条目代表"本轮未携带"，不能据此推断不存在冲突；`omissions` 中给出账本相对路径与 JSON Pointer，可按需取回完整证据。

---

## 六、状态码 (Status Codes) 规范

| 状态码 | 状态说明 | 触发场景 |
| :---: | :--- | :--- |
| **`0`** | **本次规则检查放行** | 本次检查未发现 P0，且没有需严格阻断的 P1；可能仍有 P2/P3，非严格模式下也可能有 P1。不能据此推断全文语义一致。 |
| **`1`** | **严重阻断 (P1 违规)** | 在开启 `strict=True` 严格模式下，检测到 P1 级违规（资产断裂、时空错位、平台门禁严重不符等）。 |
| **`2`** | **致命阻塞 (P0 阻断)** | 本次检查发现 P0 级违规；需语义审查的问题应以实际专家证据为准。 |
| **`3`** | **运行异常 / 参数错误** | 章节缺失、读写失败、账本或状态损坏、防脏写阻断，以及参数缺失或格式非法。 |

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

全量测试位于 `tests/` 目录，包含核心模块测试、缺陷回归和公开 API 集成测试：

```bash
# 运行全量测试套件
pytest -v

# 运行覆盖率分析报告
pytest --cov=scripts --cov-report=term-missing
```

### 测试指标

2026-09-14 在 Windows、Python 3.11.15 上执行 `python -B -X utf8 -m pytest -q -p no:cacheprovider`：

- **结果**：751 项测试、12 项子测试全部通过。
- **语句覆盖率**：617 项版本时为 92%；本轮未重新采集覆盖率，不把旧数值当作当前实测。
- **本次耗时**：约 15 秒，受机器和文件系统负载影响。
- **覆盖范围**：账本与状态校验、损坏数据保护、双轨失败恢复、章节消歧、补丁字节保真、扫描边界、跨批继承、伏笔裁决、专家结果接入、复审协调、资产事件、流式运行清单、问题处置与上下文预算。
- **完整流程**：`test_workflow_quality.py` 覆盖建账、单章审查、批量审查、正文回写、Markdown 同步和分卷快照，分别验证两种账本布局及 UTF-8/LF、GB18030/CRLF。

另外通过字节码编译、15 个生产模块的 Python 3.8 语法检查及官方技能元数据校验。Python 3.8 的兼容检查使用 `ast.parse(..., feature_version=(3, 8))`，未在 Python 3.8 解释器上运行全套测试。

---

## 许可证

本项目声明采用 MIT License。欢迎网络文学创作者、AI 写作助手开发者与文学工程探索者共同维护！
