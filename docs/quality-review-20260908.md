# story-audit 质量审查与复验记录

审查基线：`c7e11111f929ffe2f29c2c1144d002a56b58ebf1`。工作树：`F:\program\Aopen-pro\write\.worktrees\story-audit-quality-20260908`。分支：`codex/quality-closure-20260908`。

审查代理仅修改本报告；所有复现均在 Python `tempfile.TemporaryDirectory(prefix='story-audit-review-')` 下执行。未对用户小说、原工作区、真实设定文件运行写入 API。下面的源码行号是问题首次确认时的基线行号，关闭时补充修后证据。

## 范围与方法

已阅读全部 15 个 Python 模块的结构与实现，及 README、SKILL、账本模型、审查规则和相关测试契约。使用 Python AST 建立结构地图，再针对输入、异常、持久化与报告链路逐函数审查。已实测而非仅凭静态推断的问题列入编号；启发式规则本身的主观准确性不作为缺陷。

执行方法为 `python -X utf8 -` 运行标准库临时项目脚本；所有结论保留具体输入、实际结果、根因和验收要求。原套件与端到端验证由主代理负责，修复代理负责回归测试及生产改动，审查代理独立复验。

主代理基线证据：`python -m pytest -q --cov=scripts --cov-report=term-missing`，252 passed、12 subtests passed、覆盖率 89%，用时 5.16 秒。正常六 API 链路及 GB18030、CRLF、备份逐字节一致已由主代理通过临时项目验证。以上是本轮基线，不能代替修后全量验证。

## 问题与状态总表

最终状态以本表与文末收口结论为准；下文保留分批送验、重开和复验时的原始判断。

| 编号 | 严重度 | 问题 | 状态 |
| --- | --- | --- | --- |
| R01 | P1 | 损坏的持久化状态退回空对象，后续覆盖原数据 | CLOSED |
| R02 | P1 | 批量审查忽略单章失败，汇总触发 KeyError | CLOSED |
| R03 | P1 | 建账吞掉章节读取失败，仍推进状态并报告成功 | CLOSED |
| R04 | P1 | 未执行专家却报告 full 及事实、审美成功断言 | CLOSED |
| R05 | P2 | 批量 N 章重新扫描目录 N+1 次 | CLOSED |
| R06 | P1 | 补丁字段缺失或错误类型会误删正文或写入 None | CLOSED |
| R07 | P2 | 失败的批量 API 返回先前遗留报告 | CLOSED |
| R08 | P1 | 账本单独修改所有者等列被忽略并回滚 | CLOSED |
| R09 | P1 | Markdown 坏行被当作资产删除 | CLOSED |
| R10 | P2 | 技能元数据校验失败，文档契约与现状不一致 | CLOSED |
| R11 | P1 | 闭引号后空格导致排版扫描 IndexError | CLOSED |
| R12 | P2 | 自定义所有者的默认持有人仍是主角 | CLOSED |
| R13 | P2 | 新发现伏笔未进入跨批继承状态 | CLOSED |
| R14 | P2 | 未闭合引号与括号导致扫描时间二次增长 | CLOSED |
| R15 | P2 | 跨章末窗口的长段落漏检尾句模板 | CLOSED |
| R16 | P1 | 双轨账本第二轨失败后分裂却仍被判为干净 | CLOSED |
| R17 | P1 | 重复章号时无唯一目标却静默回写其中一份 | CLOSED |
| R18 | P2 | 非法平台与非有限范围未在公开 API 前置拒绝 | CLOSED |

## R01 — 损坏状态不能当作首次初始化

- 定位：[story_audit.py:487](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:487)、[story_audit.py:607](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:607)、[ledger_engine.py:817](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:817)、[audit_state.py:60](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/audit_state.py:60)、[author_memory.py:157](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/author_memory.py:157)。
- 触发：已有账本含一个合法资产和一个非法 `category`；目标章含新 `audit:stash` 标签。或者已有 `.audit_state.json` 的 `completed_chapters` 类型非法；或者作者状态的 `preferences` 错为列表。
- 实测：`audit_chapter(..., silent=True)` 返回 0，含合法资产的原 JSON 被改写为 `assets={}`；坏审查状态被重写且旧 `open_defects` 清空；`AuthorMemory.record()` 成功但旧偏好内容消失。
- 根因：加载捕获解析/校验异常并返回初始对象，或把损坏字段无提示置空；后续保存无法区分“缺失”与“损坏”。同步账本的同类捕获也存在。单章新伏笔保存直接使用 `force=True` 扩大影响。
- 最小建议：仅在文件不存在时初始化；已有文件读失败、schema 不合法必须抛受控错误并在公开 API 映射为 3。不要在无效状态上继续生成成功报告或写回。验证资产条目与容器，禁止静默过滤损坏条目。
- 验收：JSON 语法损坏、根类型/资产分类/资产条目类型损坏、审查状态字段损坏、作者偏好容器损坏均不能覆盖；保留文件原字节，API 返回 3 或底层专用异常；合法首次初始化仍通过。
- 补充独立实测：已有有效 `资源账本.md`、JSON 缺失时，`init_ledger(..., silent=True)` 返回 0 并抹去 MD 原有资产。应把已有单轨文件视为需要同步/恢复的状态；无明确 force 时返回受控错误或安全同步，不能视为全新空账本。
- 首轮修后复验：已有 31 项 R01 回归通过，但发现同一根因仍有标量字段校验缺口，故**重开**。临时状态 `open_defects=[{'chapter':'坏章号','issue':'原未决问题'}]` 仍使 audit_chapter 在已覆盖 LATEST 后抛 ValueError；资产 `quantity='误填'` 加新 stash 后 API 返回 0 且 JSON 已变；作者旧 active 偏好 `value=7` 时 record 抛 AttributeError，但作者状态已经写入。必须校验后续会参与数值运算/字符串操作的字段，不能只验证容器和条目为 dict。完整实证已发送修复代理。

- 第二轮独立复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k 'r01 or r03' --tb=short` 得到 **46 passed, 72 deselected**（0.54 秒）。再用独立临时项目原样复验三项重开样例：坏 `open_defects.chapter`、坏资产 `quantity` 加新 stash 均返回 `(3, Path(''))` 且静默；旧 active 偏好 `value=7` 在写入前抛 `AuthorMemoryError`。逐项比较全目录清单和文件字节，原 LATEST、审查状态、双轨账本、作者状态/画像全部不变，没有新目录或临时产物。现有字段级写前校验覆盖原重开根因，R01 针对性验收通过；保留首轮重开记录。

## R02 — 批量单章失败导致二次异常

- 定位：[story_audit.py:1208](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1208)、[story_audit.py:1238](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1238)。
- 触发：临时项目第 1 章正常，第 2 章内容为 `b'\x00'`；调用 `audit_scope(p, '1-2', silent=True)`。
- 实测：抛 `KeyError('word_count')`，没有按公共契约返回状态码 3。账本防脏写使单章提前返回 3 时同样进入该路径。
- 根因：无条件收集空 `summary`，只检查状态码 1、2，之后直接索引各项统计。
- 最小建议：明确失败立即中止或生成带失败明细的部分结果，统一返回 3；只汇总成功产出的结构。失败章不进入 completed，不返回旧报告。
- 验收：首章/中间章读取失败、账本冲突、报告写入异常均受控；无 KeyError/伪成功；原状态与已有报告不会被当作本次结果。
- 首轮独立复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k 'r01 or r02 or r03 or r07' --tb=short` 为 **36 passed, 50 deselected**。另独立临时测试坏章分别位于 1/2/3，全部返回 3、空报告路径，旧 LATEST、同名批报告、审查状态逐字节保留。R02 的单章失败分支验收通过；广义写入异常的公开边界仍随 R18 验证。

## R03 — 未读到的章节被记为已建账

- 定位：[story_audit.py:899](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:899)、[story_audit.py:924](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:924)、[story_audit.py:928](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:928)。
- 触发：正常第 1 章与 `b'\x00'` 第 2 章，调用 `init_ledger(p, scope_str='1-2', silent=True)`。
- 实测：返回 0；账本 `last_updated_chapter=2.0`；报告写出“扫描章节数：2”。
- 根因：整个章节读取、标签与资产提取被 `except Exception: pass` 吞掉；随后使用目标清单最后一章推进检查点。
- 最小建议：章节失败时受控失败且不提交部分账本；保留原账本与同步视图。返回形状始终为 `(状态码, Path)`，包括保存阶段错误。
- 验收：坏首章/中间章/末章都返回 `(3, Path(''))`，已有账本原字节不变，缺失账本不产生不完整成功快照。正常流式建账仍继承已有资产。
- 首轮独立复验：首/中/末三个坏章场景均正确失败，双轨账本原字节保留，无新 reports。但注入 `save_ledger_state` 的 `LedgerDirtyError`（模拟预检查之后发生人工修改）时，`init_ledger(...,silent=True)` 仍返回裸整数 `3`，并产生控制台错误输出；保存异常分支遗漏元组返回。此分支补齐前不关闭 R03。

- 第二轮独立复验：上述 R01/R03 定向套件 **46 项全过**。独立临时项目在正常解析出合法资产后，对保存调用注入 `LedgerDirtyError('模拟校验后人工编辑冲突')`，确认保存函数实际被调用一次；`init_ledger(..., silent=True)` 返回 `(3, Path(''))`，stdout/stderr 为空，双轨账本与目录清单逐字节不变。此前漏读保护与本次保存失败契约均通过，R03 针对性验收通过。

## R04 — 执行与报告真实性

- 定位：[runtime_detector.py:101](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/runtime_detector.py:101)、[story_audit.py:400](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:400)、[story_audit.py:435](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:435)、[story_audit.py:1135](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1135)。
- 触发：`patch.dict(os.environ, {'CODEX_HOME':'dummy'}, clear=True)`，对“桌上是一杯水。”调用 `audit_chapter(mode='full', silent=True)`。
- 实测：元数据为 `Effective Mode: full`、`Fallback: none`；报告无依据声称“账本状态健康：无凭空出装或资产冲突”“主线推进平稳，核心目标清晰，有效完成本章情绪位移”。管线没有专家调用、适配器或专家结果输入。批量章节仅因未命中 POV 关键词就被称为“无缝顺承”。
- 根因：环境能力规划被当作实际执行状态；报告模板硬编码语义成功判断。P1 平台问题还被套用“补获得经过/已有道具”建议。
- 最小建议：保留宿主模式规划接口，公开 API 的实际模式/能力明确标为确定性预检；说明专家未执行及降级原因。删除无证据健康/语义断言，保留可核验扫描结果、真实平台建议、待专家核验提示；不新增 LLM 框架。
- 验收：不同宿主、full/lean/auto/solo 请求下，报告和 bundle 一致披露实际执行；无未经核验的事实/审美通过结论；保留 Requested/Effective/Fallback 等既有元数据键；README/SKILL/报告模板一致。

- 首轮独立输出复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k r04 --tb=short` 得到 **26 passed, 144 deselected**（0.79 秒）。独立将 full/lean/auto/solo 与宿主/递归降级组合成 6 组规划结果，分别通过单章和批量共 12 个临时场景，确认直接报告、每章归档、bundle、终端均披露实际 solo / deterministic_precheck / expert_review_executed=False，原降级原因完整保留。无显式标记的死亡后复活场景不再被宣称语义一致；zhihu 严重平台发现仍返回 strict=1，真实 issue/fix 保留在直接报告，空继承只说明无记录与未执行语义核验。
- **跨批持久化遗漏，R04 尚不能整体关闭**：主代理提示后已独立确认。第 1 章为“他走进庭院。\n”重复 30 次，`audit_scope('1-1', platform='zhihu', strict=True)` 的真实 Finding 为 category=platform、fix=“将全篇视角严格统一重构为‘我’的当事人主观亲历视角”等第一人称建议；`.audit_state.json` 却将其保存为 category=causal、fix=“严格依据账本与主线事实对齐，杜绝主观文学发挥”。下一批 `2-2` 的 Inherited Items 原样展示错误套用建议，真实平台修复建议缺失。单章与批量状态保存仍从 p0/p1 字符串列表硬编码类别和 fix；需持久化真实 Finding 的分类/问题/建议并复验跨批展示，不能只改直接报告模板。

- 第二轮独立持久化复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k r04 --tb=short` 得到 **28 passed, 167 deselected**（0.90 秒）。独立分别从单章 API 与批量 API 生成真实 zhihu P1 发现，逐字段比较持久化 open_defects：除追加 chapter 外，与原 Finding 全部字段一致，包括 category、issue、fix、location、evidence。下一批 bundle 的 Inherited Items 保留同一对象，报告完整展示真实第一人称建议，不再出现通用账本套话；重新审查来源章后该问题仍只保留一份，字段不变。直接输出与跨批链路现均通过，R04 针对性验收通过，首轮遗漏记录保留。

## R05 — 批量重复全目录扫描

- 定位：[story_audit.py:1167](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1167)、[story_audit.py:1211](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1211)、[story_audit.py:523](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:523)。
- 主代理实测：相同 3 行临时章节，以 `unittest.mock.patch` 包裹 `ChapterResolver.discover_chapters`。30 章发现 31 次，发现耗时 0.3406 秒/总 0.6058 秒；100 章 101 次，3.5258/4.4 秒；200 章 201 次，14.4409/16.2728 秒。
- 根因：批量已发现章节后，每章再次运行完整单章发现流程，目录遍历呈二次增长。
- 最小建议：单次构建章节快照与定位索引，内部单章流程复用；保持公开 API 兼容。
- 验收：N 章只调用发现器一次，且每一章实际生成对应报告；使用调用次数及结果一致性回归，不使用脆弱墙钟阈值。

- 独立修后复验：`python -X utf8 -m pytest -q tests/test_batch_quality.py --tb=short` 得到 **3 passed**（0.21 秒）。独立临时项目审查范围 2–4 时 discover 仅调用 1 次，三章分别落入自己的归档报告，完成章精确为 `[2,3,4]`；逐份捕获预审包确认目标路径、上一章路径、首尾 300 字正确，范围首章仍使用范围外第 1 章尾文。随后添加第 6 章进行下一批、再添加第 7 章进行独立单章，三次公开调用共 discover 3 次，新文件与上一章上下文均正确；无跨调用缓存。另在首章执行前插入批内新文件，确认它未混入本次快照、发现次数仍为 1。R05 针对性验收通过，30/100/200 章同条件最终计时由主代理补充。

- 主代理后续同条件计时证据：每章均为原基准三行短正文，mode=solo/silent；30 章 discover 1 次 / 发现 0.0126 秒 / 总 0.2592 秒，100 章 1 次 / 0.0392 秒 / 0.8695 秒，200 章 1 次 / 0.0656 秒 / 1.7163 秒。对应基线为 31/101/201 次发现，总耗时 0.6058/4.4000/16.2728 秒；每份归档报告与 completed 数量准确。这里的计时由主代理实测，审查代理独立验证的是调用次数、覆盖与上下文语义，未用脆弱墙钟阈值作验收。

## R06 — 补丁输入校验必须早于备份和写入

- 定位：[story_audit.py:1655](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1655)。
- 触发与独立实测：原稿 `前句。\n旧正文\n后句。\n`；`patch={'target_line':2,'old_text':'旧正文'}` 返回 0 并删除旧句；`new_text=None` 返回 0 并写字面 `None`；`target_line='bad'` 泄漏 ValueError。修复代理另证实 `target_line=2.9` 被截断后写入。
- 根因：缺字段默认空串；无条件 `str()`、`int()` 转换把错误输入伪装为合法补丁。
- 最小建议：四种入口（dict、JSON 文件、显式参数、PatchSpec）统一校验；target_line 为正整数且不接受 bool/小数，old_text 为非空字符串，new_text/锚点为字符串。显式 `new_text=''` 仍是合法删除。
- 验收：不合法输入一律返回 3、正文原字节不变且不新增备份；合法空串删除、UTF-8 BOM/GB18030 与 CRLF 保真、歧义拒绝均通过。
- 修复代理提交复验材料：50 项新参数回归，RED=33 failed/17 passed；定向 GREEN=85 passed。该材料尚不等于独立验收。
- 独立复验：已审查 R06 的完整源码 diff；`python -X utf8 -m pytest -q tests/test_quality_closure.py -k r06 --tb=short` 得到 **50 passed, 30 deselected**（0.40 秒）。独立临时脚本另跑 **44 组**：四入口各 10 组无效输入、UTF-8 BOM/GB18030 与 CRLF 下显式删除和多行替换。全部通过；坏输入正文原字节不变且无新 reports/备份；合法备份逐字节一致。修复符合原缺陷需求，没有新增依赖；后续全部改动后的全量回归仍必需。

## R07 — 失败不能返回旧批量报告

- 定位：[story_audit.py:1541](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1541)。
- 触发：临时项目先成功 `audit_scope(p, '1-1')`，删除临时章节，再调用相同范围。
- 独立实测：第二次 code=3，但返回 path 与首轮完全相同，且 `is_file()` 为 True。
- 根因：公开包装层按文件是否存在推断报告是否来自本次运行。
- 最小建议：使用本次执行明确返回的产物，失败无新报告时返回 `Path('')`；不要以旧文件存在作为成功依据。
- 验收：既有同名汇总/LATEST 下失败均不返回旧报告；旧报告本身可保留供历史查阅。
- 首轮独立复验：结合 R02 三种失败位置测试，均返回 `(3, Path(''))`，同名旧报告逐字节保留；针对性验收通过，最终全量回归待所有修改结束。

## R08 — Markdown 独立字段修改被恢复

- 定位：[ledger_engine.py:891](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:891)。
- 触发：正常保存 owner/holder 均为“陆离”的资产，随后只改 MD owner 为“作者新设定”，调用 `sync_from_markdown(md, json)`。
- 实测：返回 owner 仍为“陆离”，重渲染 MD 后用户编辑消失。
- 根因：changed 条件只比较 name、quantity、current_holder、status、constraints，遗漏 category、unit、owner、origin_chapter；这些列只有别的字段改变时才更新。
- 最小建议：逐一比较并更新全部支持的可编辑列，不改动未展示的 history/lend_meta。
- 验收：四个遗漏字段分别单独修改均保留，不能要求附带修改别的列；双文件与返回状态一致。

- 独立修后复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k 'r08 or r09' --tb=short` 得到 **19 passed, 106 deselected**（0.58 秒）。独立构造同时含借出热资产、另一热资产、已消耗冷资产与伏笔池的账本，分别仅修改类别、单位、所有者、初始章节，共 **4/4 通过**；新值进入 JSON 与重渲染 MD，原 history 保留并只追加一次同步记录，lend_meta、其他字段/资产及伏笔池不变。R08 针对性验收通过。

## R09 — 无效 Markdown 行触发隐式删资产

- 定位：[ledger_engine.py:859](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:859)、[ledger_engine.py:929](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:929)、[ledger_engine.py:935](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:935)。
- 触发：两项正常热资产 a、b；把 MD b 行截成 `| b | b | 装备道具 | 1 | 把 |` 后同步。
- 实测：同步成功，JSON 只余 a，b 被永久移除。
- 根因：坏行被 IndexError/ValueError 分支跳过，之后把不在 valid_asset_ids 的热资产视为用户删除；非法数量默认 1、非法枚举回退也会悄悄改造数据。
- 最小建议：先完整解析和校验资产表，任一坏行/重复 ID/非法值都拒绝提交。只有明确有效的表格才执行删除集合逻辑，保留已有冷资产保护语义。
- 验收：短行、非法数量/分类/状态、重复 ID 不改任一原文件；合法物理删除继续工作；约束与伏笔表不能误当资产行。

- 首轮独立修后复验：上述 **19 项定向测试通过**；独立测试 15 种坏表格，14 种正确返回 3 并保留全目录/文件字节，但**畸形首表头仍会删除资产，故重开 R09**。临时账本有热资产 a、b 和冷资产 c（CONSUMED），仅去掉第一张热资产表表头开头的 `|`，后续数据行及冷资产表不变；`sync_ledger_from_md(..., silent=True)` 返回 0，JSON 只剩 c，a/b 被删除。当前解析器仅在已有 col_mapping 时拒绝缺起始分隔符的行；首表头未被识别就跳过，后续合法冷表又使 valid_asset_ids 非空并触发删除。需要在允许清理资产前拒绝明显畸形的资产表头/上下文，不能把未解析的首表视为物理删除。另独立正例确认：合法删除一个热资产行、隐藏冷资产仍保留、伏笔表隔离、MD 单轨恢复 JSON 的可展示字段均通过。

- 第二轮独立复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k r09 --tb=short` 得到 **21 passed, 114 deselected**（0.58 秒）。独立重现原来的首热表缺起始 `|`、完整冷表场景，并分别对冷热表检查缺起始分隔符、删表头、数据前空行，共 **6/6** 返回 3，目录清单和原文件字节全部不变。新保护保留了正常行为：删除一个热资产并隐藏整个冷区块时，只删除指定热资产，冷历史与伏笔池保留；仅冷资产和空账本也正常同步。写前识别表头及资产区上下文已覆盖本轮重开原因，R09 针对性验收通过，首轮失败历史保留。

## R10 — 文档与技能包契约

- 定位：[SKILL.md:1](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/SKILL.md:1)、[README.md:1](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/README.md:1)、[report-template.md:1](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/references/report-template.md:1)。
- 主代理证据：官方 `skill-creator/scripts/quick_validate.py .` 基线退出 1，`Unexpected key(s) in SKILL.md frontmatter: triggers`；实际覆盖率 89% 与 README 93% 不符；无 CLI 项目仍提供 `--scope`、`--apply-fix` 等调用指引。
- 最小建议：触发词保留移入允许的 metadata，description 说明使用场景；用现有 Python API 更正文档；测试数字由实际验证证据支持。专家执行表述归 R04 同步修正。
- 验收：官方校验退出 0，触发词集合不丢失；相关说明只给实际存在的 API；测试/覆盖率注明测量证据与边界。
- 独立分批复验：已检查 README、SKILL、report-template、ledger-model、audit-rules 的 diff。实际执行 `python -X utf8 C:/Users/shoun/.codex/skills/.system/skill-creator/scripts/quick_validate.py .` 输出 **Skill is valid!**、退出 0；用 YAML 对比基线，11 个触发词顺序与集合、既有 metadata 完全保留；全部 references 与 README/SKILL 不再出现未实现的 CLI 调用。账本真实 JSON/快照路径、完整快照不裁剪、历史仅截取最近 5 条且尚无实体投影的边界与实现一致。README 最终测试/覆盖率及 R04 模式样例待收口。后续独立复读确认 README 状态码表与 SKILL Step 4 均已将防脏写归入 3，和实现一致；该文档子项通过。最终统计与模式样例完成前不整体关闭 R10。

- 后续文档独立复验：已读取更新后的 ledger-model 与模式示例。提取唯一 JSON 示例并实际执行 `LedgerState.from_dict(data).to_dict() == data`，完整往返相同；实际 ASSET_CATEGORIES/ASSET_STATUSES 均有说明，不再使用 entities 嵌套概念结构。状态迁移图已注明是专家核验概念，apply_fix 不自动补账，R16 恢复标记/副本及 force 不可绕过的说明与独立实测一致。README/SKILL 的 full→solo、python_api_deterministic_only、deterministic_precheck、not_executed 示例与 R04 实际输出一致；专家模板明确限定实际执行与证据。再次运行官方 quick_validate.py 输出 **Skill is valid!**、退出 0。最终测试/覆盖率统计待主代理，SKILL 继承说明中“校验跨批因果一致性”旧句建议同步 README 的“供宿主核验、存储本身不构成语义结论”边界后收口。

- 末轮独立复读通过：SKILL 第 131 行现明确“供宿主继续核验；存储继承本身不构成跨批因果一致性的语义结论”，与 README 第 136 行及代码一致。README 徽章和测试章节均已更新为主代理冻结版实测的 **617 passed、12 subtests passed、92%（3346 条语句、274 条未覆盖）、13.50 秒**，同时注明 Windows/Python 3.11.15、覆盖率命令和耗时边界；已删除旧测试分布及无证据的极速/并发承诺。Python 3.8 支持明确区分 AST 语法检查与真实解释器全量测试。结合前述元数据校验、API 样例、账本模型往返及真实模式披露，R10 验收完成，状态 **CLOSED**。

## R11 — 对话空格触发扫描崩溃

- 定位：[format_scanner.py:509](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/format_scanner.py:509)、[format_scanner.py:649](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/format_scanner.py:649)、[format_scanner.py:674](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/format_scanner.py:674)。
- 独立实测：`scan_typography_flaws('“你好。” 他走了。')`、`scan_typography_flaws('他说：“走。”  门开了。')`、`scan_typography_flaws('他走了。 “别去！”  我喊。')` 均抛 `IndexError: string index out of range`。
- 根因：提取句子时对内容 `.strip()`，但返回原始未裁剪区间；后续根据 end-start 计算长度索引已缩短的字符串。
- 最小建议：统一偏移和内容，不以捕获 IndexError 掩盖。保留正确的引号内外叙述切分。
- 验收：中英文引号、空格/制表符、混合多句均不崩溃；长叙述逗号仍触发，短对话逗号不误累计；公开 audit_chapter/audit_scope 端到端通过。
- 独立修后复验：已审查扫描器完整 diff；`python -X utf8 -m pytest -q tests/test_scanner_quality.py tests/test_format_scanner.py tests/test_ai_patterns_checker.py --tb=short` 得到 **79 passed**（0.39 秒）。固定种子 9135 生成 1500 段引号、空格、制表符及标点混合文本，逐句核对原文 `[start:end] == text`、偏移长度和顺序一致，并实际运行扫描，全部通过。三个原始崩溃样本加一例制表符对话通过 4 次单章 API 与 1 次批量 API；叙述与对话逗号的回归断言通过。针对性关闭。

## R12 — 默认持有人不继承自定义所有者

- 定位：[ledger_engine.py:128](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:128)、[ledger_engine.py:141](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:141)。
- 独立实测：`AssetItem(id='a',name='青钢剑',category='装备道具',quantity=1,unit='把',owner='陆离')` 得到 owner='陆离'、current_holder='主角'。
- 根因：current_holder 的非空默认值阻止 __post_init__ 使用 owner；与字段注释“默认同 owner”及多主体账本语义冲突。
- 最小建议：未指定持有人时以 owner 填充；显式持有者继续保留。
- 验收：构造、序列化往返、不同 owner、显式借出 holder 均正确；默认主角行为兼容。

- 独立修后复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k 'r12 or r14' --tb=short` 得到 **7 passed, 122 deselected**（0.25 秒）。另独立检查默认主角、自定义 owner、明确不同 holder、空 holder 回填四种构造及 JSON 序列化往返，再检查缺 current_holder 的历史字典，共 5 个场景均通过。修复仅把构造默认值置空以使用既有 owner 回填语义，明确持有者不变，R12 针对性验收通过。

## R13 — 伏笔未进入跨批继承链路

- 定位：[story_audit.py:609](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:609)、[story_audit.py:1303](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1303)、[audit_state.py:31](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/audit_state.py:31)。
- 触发：全新临时项目，第 1 章 `<!-- audit:stash name="深海旧钥匙" origin="第1章" status="pending" -->`；先 audit_scope('1-1')，再 audit_scope('2-2')。
- 独立实测：首批 `foreshadowing_commitments=[]`；第二批 bundle.inherited_items 对应字段仍空，报告不含“深海旧钥匙”。
- 根因：扫描标签只并入临时 LedgerState，且 JSON 已存在时才持久化；AuditState 更新逻辑从不写 foreshadowing_commitments，无法兑现“下一批继承开放伏笔”的契约。
- 最小建议：把已显式扫描到的规范伏笔以稳定键纳入审查状态，保留来源章节与已有状态；缺乏明确回收信号不推断自动解决。
- 验收：无预建账本和有预建账本两种项目，单章/批量后均能在下一批继承；重审去重，来源不漂移，已有未决项不丢失，报告明确仅记录显式标签。

- 独立修后复验：`python -X utf8 -m pytest -q tests -k r13 --tb=short` 得到 **32 passed, 581 deselected**（1.37 秒）。独立无预建账本/已有账本 × 单章/批量共 4 个场景验证：新标签进入下一批 bundle 与报告，来源章为真实第 1 章，同章重审不重复且不重复改写账本；旧开放问题、手工同键承诺及已解决记录原样保留，正文出现揭晓内容不擅自回收 pending。
- 另 4 个独立场景通过：先 init 后审无标签后章仍继承全量 stash，跨章重复标签保持首次发现章且自由来源原文不丢；历史未知来源保留 None 并在报告显示“来源未记录”，明确中文“第十二章”映射为 12；只有人工 MD 时原 GB18030/CRLF 字节不变、不新建覆盖 JSON，新标签仍通过审查状态继承；首次新伏笔落盘时真实调用双轨保存并注入 MD 第二轨失败，API 返回 `(3, Path(''))` 且静默，原目录/字节不变，无半份账本或成功报告。R13 针对性验收通过。

- 补充独立联合验证：主代理在集成检查中观察到 assets 为空后，按给定样本分别构造有/无“设定”目录的临时项目。第 1 章为 GB18030/CRLF，包含 `【获得：青钢剑×1柄】` 和显式伏笔，第 2 章为 UTF-8；顺序调用 init→audit(full)→scope(full)→apply_fix→checkpoint→sync，两个项目共 12 次 API 调用均返回 0，每一步真实账本都保留青钢剑 1 柄，正文替换字节准确。无“设定”目录时既有 locate_ledger_paths 约定选根目录账本；硬编码 load 不存在的“设定/资源账本.json”确实返回空状态，真实根目录数据并未丢失。已请主代理按实际定位路径核对集成夹具，不因此新增问题编号或改变生产选址逻辑。

## R14 — 未闭合符号造成二次扫描

- 定位：[ai_patterns_checker.py:51](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ai_patterns_checker.py:51)、[ai_patterns_checker.py:65](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ai_patterns_checker.py:65)、[ledger_engine.py:67](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:67)。
- 触发：单行大量未闭合 `“`，或资产文本含大量未闭合 `【`。
- 独立实测：1000/2000/4000 字符时，`mask_quotes_in_line('“'*n)` 分别 0.0160/0.0680/0.2628 秒；`extract_heuristic_assets('【'*n,1)` 分别 0.0228/0.0935/0.3621 秒。每次输入翻倍，耗时约四倍。
- 根因：每遇到未闭合开引号，Python 循环重新扫描整个剩余后缀；资产模式允许从不同开括号重复尝试长后缀。20MB 文件限额不足以控制这类二次成本。
- 最小建议：预处理闭合位置或单次状态扫描；资产括号扫描应有界。不要以任意截断整章来换速度而丢失合法后缀内容。
- 验收：长未闭合符号输入有界完成，保留同行闭合引号掩码、不跨行掩码和正确行号；正常资产提取结果不回退。用合理宽限/操作次数保障，避免毫秒级脆弱断言。
- scanner 侧独立复验通过：1500 段混合短文本的 mask 结果与基线函数完全一致，闭合对话区间与基线正则完全一致；完整 `scan_typography_flaws('“'*n)` 在 n=4000/40000 时分别 **0.003099/0.034700 秒**，保留 LONG_PARAGRAPH 发现项。新旧 79 项扫描器回归包含增长阶数、跨行掩码边界与 40k 宽松超时测试，全部通过。**ledger 未闭合括号分支仍待独立验收，R14 尚未整体关闭。**

- ledger 侧后续独立复验通过：上述 R12/R14 **7 项定向测试全过**。用 git 基线提取原正则与资产提取函数，独立固定种子 2026090814 生成并检查 **2505 个样本**，新迭代器的 match span/groupdict 与原 finditer 完全一致，完整资产候选也与基线相同。新实现完整提取在 n=4000/60000 未闭合 `【` 时三次中位数为 **0.000151/0.002258 秒**；相同前缀后追加合法 `【获得：灵石3枚】` 时为 **0.000402/0.005372 秒**，均正确保留灵石 3 枚。未截断合法后缀，scanner 与 ledger 两侧针对性验收现均通过，R14 可关闭该轮定向验证。

## R15 — 章末长自然段漏检

- 定位：[ai_patterns_checker.py:213](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ai_patterns_checker.py:213)。
- 独立实测：`text='开门。\n他走近。\n'+'路很远。'*100+'命运的齿轮转动了。'` 没有任何 AI_TRAILER 类发现；只在最终句前添加一个换行，就产生 `AI_TRAILER_SUMMARY`。
- 根因：以整行起点判断是否进入最后 600 字/后 25% 窗口，导致跨窗口的长末段完全跳过，虽然真正命中的句子位于文本最末端。
- 最小建议：按匹配实际绝对偏移筛选尾窗，保留原始物理行号以及开篇保护规则。
- 验收：同一句末尾模板置于跨窗长段和独立短段都可发现，开篇同词不误报，窗外匹配不会冒充章末。
- 独立修后复验：原始无换行/有换行两种长末段构造均准确命中 `AI_TRAILER_SUMMARY`，行号分别为 3/4。79 项扫描器测试覆盖空格缩进、窗外同词排除、同一行早期与晚期重复命中、开篇保护。源码改为从候选实际尾窗偏移搜索，并用原行内容截取 snippet；针对性关闭。

## R16 — 双轨保存失败不能留下隐形分裂

- 定位：[ledger_engine.py:779](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:779)、[ledger_engine.py:784](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:784)、[ledger_engine.py:634](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/ledger_engine.py:634)。
- 触发：临时项目先保存 quantity=1 的双轨账本，随后改为 9。通过 `unittest.mock.patch.object(ledger_engine, 'write_file_safe', side_effect=...)` 仅对 MD 路径抛 `SafeIOWriteError`，模拟第二轨写入失败。
- 独立实测：抛异常后 JSON 已变为新内容，MD 保持旧内容，`check_dirty_state(md,json)` 返回 False。
- 根因：两轨分别原子写，但整体没有失败恢复；先更新的 JSON mtime 使时间戳守卫无法看出未完成的双轨事务。
- 最小建议：先准备和校验两份输出，保留旧文件用于失败回滚或提供明确可恢复状态；发生第二轨失败不能把分裂账本视为干净。
- 验收：注入第二轨失败后两轨恢复原字节或明确阻断后续访问直至恢复；不能丢失原资产或掩盖状态不一致。正常双轨保存及 force/dirty 语义保留。

- 独立修后复验：`python -X utf8 -m pytest -q tests/test_quality_closure.py -k r16 --tb=short` 得到 **13 passed, 131 deselected**（0.49 秒），已审查提交前渲染、独占标记、二进制备份、逆序恢复、清理和读写守卫。独立原始第二轨失败场景中，JSON 的 UTF-8 BOM/CRLF 与 MD 的 GB18030/CRLF 原字节、mtime_ns、目录清单完整恢复，无标记/副本残留。
- 外部锁独立实证：在 JSON 已更新、MD 注入失败时，用 Windows `CreateFileW` 的真实独占文件句柄阻止 JSON 回滚（未模拟 os.replace 失败）；释放句柄后确认 JSON 仍为新数据，原字节和 mtime_ns 完整留在恢复标记指定副本，`check_dirty_state=True`。4 个底层路径（双轨/仅 JSON force 保存、反向同步、load）及 audit/scope/init/sync/checkpoint 五个公开 API（全部 force=True）均拒绝，报告类返回 `(3, Path(''))`，其余返回 3，静默且残留目录逐字节不变。
- 恢复闭环：仅在同一临时目录内按标记的实际副本恢复，核对两轨原字节和 mtime_ns 精确还原、dirty=False、目录无备份残留；随后正常非 force 双轨保存成功。R16 针对性验收通过。真实项目遇到未完成标记时须先检查并恢复数据，force 无法绕过该守卫；没有对真实设定调用恢复操作。

## R17 — 重复章号必须拒绝歧义目标

- 定位：[story_audit.py:1379](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1379)、[story_audit.py:533](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:533)。
- 触发：临时项目 `正文/甲卷/第001章.txt` 与 `正文/乙卷/第001章.txt` 都含“旧正文”；`apply_fix(chapter_index=1,target_line=1,old_text='旧正文',new_text='新正文')`。
- 独立实测：返回 0，两个文件变为 `['旧正文','新正文']`，没有要求/证明唯一目标。
- 根因：发现器允许重复章号并只作为 P2 警告，但纯章号公共 API 直接取第一个匹配。批量遍历再次以章号定位还可能重复审查同一文件而遗漏另一份。
- 最小建议：没有唯一目标时回写返回 3；批量出现重号拒绝目标集合或使用已定位对象而不伪称全覆盖，不必新增用户参数。
- 验收：重复章号时正文与备份完全不变；单章/批量不能把两个文件的结果覆盖到同一路径后宣称完整；唯一目标的既有功能保持。

- 独立修后复验：`python -X utf8 -m pytest -q tests/test_api_input_quality.py --tb=short` 得到 **125 passed**（0.87 秒）。独立临时项目中，跨卷同号的显式章号/默认最新章分别调用单章审查和回写，共 4 例均受控拒绝，BOM/CRLF 正文、既有报告与全目录不变；重号位于目标批次末尾时，批量与建账 2 例在首章写入前拒绝，审查状态不推进；范围外重号不阻断唯一目标，单章/批量/建账/回写 4 个正例正常，仅唯一目标被允许写回。R17 针对性验收通过。

## R18 — 公共输入错误应受控返回

- 定位：[platform_rubrics.py:46](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/platform_rubrics.py:46)、[story_audit.py:96](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:96)、[story_audit.py:1294](F:/program/Aopen-pro/write/.worktrees/story-audit-quality-20260908/scripts/story_audit.py:1294)。
- 独立实测：临时章文本“他走进庭院。”重复 30 次，`audit_chapter(platform='zhihu',strict=True)` 返回 1；拼错为 `platform='zihu'` 返回 0。`audit_scope('1-inf')` 接受范围、开始生成报告后抛 `OverflowError: cannot convert float infinity to integer`。
- 根因：公开 API 未校验平台，底层静默回退 generic；范围解析直接接受 Python float 的 inf/nan，直到文件名格式化才出错。
- 最小建议：公开入口在产生副作用前校验合法平台/模式、有限章节范围、必要参数和可用目录；错误返回状态码 3。底层规划辅助的既有兼容策略不必全部修改。
- 验收：错误枚举、NaN/Inf 范围、错误章号类型等不漏底层异常、不创建新报告/账本；合法大小写/空格规范化与正常 API 行为有明确一致契约。

- 独立修后复验：上述 **125 项公共 API 测试全过**；另外独立验证 27 个非法枚举、NaN/Inf/超大章号、非有限范围、错误类型、无效卷号/补丁来源/项目路径，六 API 均按形状返回 3，原文件字节与目录清单不变。正确 zhihu 与大小写/外空白变体在单章和批量共 4 个正例均保持 strict 返回 1，报告与 bundle 一致记录规范化平台；随后拼错 zihu 拒绝且不覆盖已存在产物。带空白反向范围正常生成对应汇总文件。独立向单章/批量报告写入与建账保存注入 SafeIOWriteError，确认实际命中后均受控返回 `(3, Path(''))`，旧可信报告及审查状态不变，无异常外泄。R18 针对性验收通过；这不代表对任意中途 I/O 失败实现了全项目事务回滚，双轨账本保存恢复另由 R16 验证。

## 首轮审查收口

本轮全模块审查已完成，最终确定 R01–R18，共 10 项 P1、8 项 P2，无已确认 P0。此后停止扩展缺陷搜寻，转入逐项修复复验与回归影响检查。未把启发式语义能力当作真实专家审查，也不声称全面无缺陷。

逐项修后复验现已完成。R01/R03、R04、R09 的首轮遗漏均在第二轮独立复验通过，历史证据保留；R14 的 scanner/ledger 两侧均已验收。R10 最后文档收口及最终全量回归证据如下。

## 最终验证与闭环结论

- **全量回归（主代理实测）**：2026-09-08 冻结生产代码与测试后，执行 `python -X utf8 -m pytest -q --cov=scripts --cov-report=term-missing`，得到 **617 passed, 12 subtests passed in 13.50s**；语句覆盖率 **92%**，共 3346 条语句、274 条未覆盖。此结果包含最后新增的四项完整流程回归，取代此前 613 项的中间统计。
- **新增完整流程（审查代理独立实测）**：`python -X utf8 -m pytest -q tests/test_workflow_quality.py --tb=short` 得到 **4 passed in 0.55s**。逐项复读断言，确认根目录/设定目录两种账本布局与 UTF-8/LF、GB18030/CRLF 两种文本组合，均覆盖 init、audit、scope、apply_fix、sync、checkpoint；验证每阶段实际账本资产保留、精确字节回写与唯一原文备份、所有者同步、完整快照、伏笔唯一来源与 pending 状态、真实 solo/未执行专家标记和静默输出。checkpoint 新建“设定”目录后也不会迁移既有根目录账本。
- **样本副本验证（主代理实测）**：原 30 章样本只在副本上运行，正确定位实际账本后提取 **180 项资产**、生成 **30 份完整报告**，init/audit 均返回 0；原章 SHA256 不变，总耗时 0.6439 秒。独立两种布局六 API 联合验证另见 R13，批量发现次数及 30/100/200 章同条件基准见 R05。
- **兼容与元数据**：主代理完成字节码编译、15 个生产模块的 Python 3.8 AST 语法检查及无外部运行时导入检查。官方 `quick_validate.py` 在主代理末轮和审查代理独立文档复验中均输出 **Skill is valid!**；审查代理已确认 11 个触发词与原 metadata 保留。
- **最终差异复核（审查代理）**：已核对基线到冻结版改动范围及 AST 函数清单，末轮定向复读 AssetItem/LedgerState、AuditState、AuthorMemory 的新增字段校验与四项流程测试，未发现需要重开原编号的问题。暂存前 `git diff --check` 与暂存后 `git -c core.safecrlf=false diff --cached --check` 均退出 0；Git 的 LF/CRLF 提示不构成检查失败。另用只读脚本确认总表编号完整、18 项均 CLOSED、严重度合计准确、报告无行尾空白，README/SKILL 最终文本与实测数字一致。审查代理仅修改本报告。

**R01–R18 共 18 项（10 项 P1、8 项 P2）全部 CLOSED，当前无待修复或待复验项。** 结论基于各项独立针对性证据、最终全量回归和以上流程验证，不表示代码全面无缺陷。Python API 执行的是确定性预审，全文事实、因果与审美的语义结论仍需宿主实际执行专家核验；Python 3.8 未运行解释器级全量测试；R16 的双轨恢复保证也不等于任意 I/O 中断下的全项目事务回滚。
