# DevAgent 评测体系

> 文档定位：4.0 评测体系
>
> 文档状态：当前实现说明 + 截至 3.3（含 3.3.x）的历史数据指标（2026-09-11 更新）
>
> 适用范围：`extensions/evals`、`backend/evals` 和评测中心前端；API 由 Evals 插件路由提供

本文只把当前 4.0 的评测结构和运行规则作为当前事实。3.3（含 3.3.1、3.3.2、3.3.3）性能数字作为历史对照归档；4.0 的新结果以 Evaluation Center 生成的运行批次、性能结果和归档快照为准。

## 1. 4.0 当前架构

4.0 的评测链路由五层组成：

```text
版本化 Case/Project/Fixture
          ↓
Provider + Registry 加载评测资产
          ↓
Runner 在隔离工作区复用生产 DevAgent 执行
          ↓
确定性 Checker 校验文件、UML、测试、Trace 和保护路径
          ↓
EvalResult → Batch → Performance JSONL → Archive
```

### 1.1 代码与数据边界

```text
extensions/evals/
├── models.py                # EvalCase、EvalTurn、EvalResult、CheckerResult、ProjectManifest
├── registry.py              # Case 目录加载
├── projects.py              # ProjectManifest 和 fixture 路径解析
├── fixture_materializer.py  # base fixture + overlay 的隔离物化
├── runner.py                # 单用例执行、预算、Trace、结果和工作区快照
├── checkers.py              # 确定性检查器
├── batches.py               # 批次、汇总、合并和快照归档
├── performance.py           # 性能 JSONL 浏览、删除和归档
└── provider.py              # Eval Provider 适配层

backend/evals/
├── cases/                   # 版本化用例 JSON
├── projects/                # 项目 manifest
├── fixtures/                # 可复制的设计/源码/测试快照
└── baseline.json            # 受控的历史基线资产
```

`extensions/evals` 只负责执行机制，`backend/evals` 只负责评测输入和受控基线。路径统一由 `extensions.evals.paths` 解析，运行代码不应自行拼接评测数据目录。

### 1.2 Case、Project 和 Fixture 契约

Case 支持单轮 `prompt` 和共享 Agent 历史的多轮 `turns`，并声明：

- `project_id`、检查器、单轮时间上限、工具调用上限和 Token 上限；
- `suite`、`capability`、`operation`、`sample_type`、`release_gate` 等统计元数据；
- `hard_checkers` 与普通 `checkers`，分别表达发布门禁证据和诊断/评分证据。

ProjectManifest 固定 fixture 的版本、入口设计文件、`source_dir`、`test_dir`、保护路径和允许写入路径。所有路径必须是 fixture 根目录下的相对路径。

Fixture 采用 `design/`、`src/`、`test/` 三类资源边界，可选 `base_fixture` 先复制完整基础快照，再叠加当前项目的差异文件。物化过程只复制普通文件，不使用跨平台链接。

当前目录包含 18 个用例：

| Suite | 数量 | 作用 | 正式基线 |
|---|---:|---|---|
| `understanding` | 4 | 项目、组件、图和时序理解 | 是 |
| `single` | 8 | 单轮读取、创建、更新、删除和跨资源联动 | 是 |
| `multiturn` | 4 | 共享历史的多轮任务及首轮零工具行为 | 是 |
| `trace-3.1` | 2 | Trace 衍生的专项回归 | 否 |

正式基线只统计前三个 suite 的 16 个用例；`trace-3.1` 始终作为专项回归单独查看。

### 1.3 单用例执行

`EvalRunner` 在执行前完成 fixture、manifest 和目录布局校验，然后：

1. 将 fixture 物化到独立临时工作区，并记录 `paths_unchanged` 文件的 SHA-256；
2. 通过生产组装函数创建 DevAgent，复用生产工具、提示词和执行协调器；
3. 为每个用例创建独立 `TraceSession`，收集工具调用、模型、Token、预算停止原因和最终答案；
4. 多轮 Case 复用 Agent、工作区和历史，但每个用户轮次使用独立的生产任务预算；
5. 执行 hard/普通 Checker，生成带 Checker 明细和元数据的 `EvalResult`；
6. 将最终物化工作区复制到 `temp/evals/artifacts/<run_id>`，便于复查和重放。

评测只改变工作区和审批适配器，不注入评测专用工具或额外用户 Prompt。评测失败不会修改主项目目录。

### 1.4 Checker 语义

通用 Checker 包括 `file_exists`、`file_contains`、`json_field`、`pytest` 和 `paths_unchanged`；UML Checker 包括 `uml_valid`、`uml_contains`、`uml_relation`、`uml_method` 和 `uml_sequence`。结果统一包含 `passed`、`score`、`message` 和 `details`。

Runner 会分别执行两组 Checker，并把结果合并到最终结果中；当前 hard checker 是结果证据和门禁输入，不采用失败即停止的短路执行。发布流程应按 Case 的 `release_gate` 和 Checker 配置解释结果，不能只看自然语言回答或单一平均分。

### 1.5 EvalResult 与 EvalSummary

`EvalResult.status` 当前包括 `running`、`passed`、`failed`、`timeout`、`budget_exceeded`、`budget_finalized` 和 `error`。`budget_finalized` 表示 Agent 在预算边界完成了可交付的最终答案；只有 Checker 通过时才计入 `passed`，否则同时计入 `failed`，不能把预算收敛误报为成功。

`EvalSummary` 是批次和性能视图共用的聚合模型，除总数、通过率、平均分、耗时、Token 和工具调用外，还固定汇总：

- `budget_exceeded`、`budget_finalized`、`errors` 及 `failure_categories`；
- `prompt_tokens`、`cached_prompt_tokens`、`prompt_cache_requests`、`prompt_cache_hit_rate`；
- `prompt_prefix_chars`、`reused_prompt_prefix_chars`、`prompt_prefix_requests`、`prompt_prefix_reuse_rate`。

缓存和前缀复用指标来自 Trace 中的实际 usage 事件；没有有效请求或 Token 时返回 `null`，前端应按“无数据”展示，而不是当作 0% 命中。Runner 还在结果元数据中记录评测契约版本、执行路径、预算范围和 Trace 关联，便于跨版本比较时确认口径一致。

## 2. 运行结果治理

### 2.1 结果层级

| 层级 | 内容 | 持久化位置 |
|---|---|---|
| 单次结果 | `EvalResult`、Checker 明细、Trace 关联 | `temp/evals/results/results.jsonl` |
| 运行批次 | 同一次执行的 Case 集合和 `EvalSummary` | `temp/evals/batches.jsonl` |
| 性能结果 | 同版本完成批次合并后的 canonical JSONL | `temp/evals/results/performance-*.jsonl` |
| 归档快照 | 完整批次、结果、备注和归档 ID | `temp/evals/archives/archive_*.json` |
| 工作区快照 | 用于审计的最终 fixture 状态 | `temp/evals/artifacts/<run_id>/` |
| Trace | 工具、LLM、任务和执行摘要事件 | `temp/evals/traces/` |

`baseline.json` 是仓库内受控资产；运行批次和性能 JSONL 是运行时数据。合并批次不会自动修改基线，只有显式的基线提升或归档操作才会写入基线/归档数据。

### 2.2 Batch、Performance 和 Archive 边界

- 一个执行请求对应一个 runtime batch；批次按 Case ID 顺序执行，同一进程只允许一个活动批次。
- 单个批次只有覆盖完整正式基线用例集时才能转换为性能结果；多个批次除需合并后覆盖完整基线外，还必须使用相同版本；同一 `case_id` 的完全相同结果去重，冲突结果拒绝合并。
- 合并只生成性能 JSONL，不伪造新的执行批次，也不改变 `baseline.json`。
- 批次和性能结果可以删除，但删除不影响代码仓、受控基线和已归档快照。
- 归档保存完整快照，包含 Checker、模型、Trace ID、运行元数据和汇总指标；归档快照不可由删除运行结果反向修改。

每次正式运行还应记录代码 commit、模型和配置、提示词版本、Case/Fixture 版本、依赖环境、预算、Trace ID 以及结果 schema 版本，保证结果可解释、可复查。

## 3. API 与评测中心

评测 API 由 `extensions/evals/full_api.py` 和 `extensions/evals/api.py` 提供，
由 `backend/app/main.py` 通过插件管理器挂载，并由统一认证依赖保护：

| API | 作用 |
|---|---|
| `GET /api/evals/cases` | 获取 Case 目录 |
| `GET /api/evals/baseline` | 读取受控基线 |
| `GET /api/evals/repository` | 获取当前分支、commit 和工作区 dirty 状态 |
| `POST /api/evals/baseline/archive` | 将当前基线生成不可变归档 |
| `POST /api/evals/run` | 执行单个 Case |
| `GET /api/evals/results` | 查询单次结果 |
| `POST /api/evals/runs` | 按 suite 或 Case ID 启动批次 |
| `GET /api/evals/runs` | 查询批次列表 |
| `GET /api/evals/runs/{batch_id}` | 查询批次进度和明细 |
| `DELETE /api/evals/runs/{batch_id}` | 删除已完成批次 |
| `POST /api/evals/runs/merge` | 合并同版本批次并生成性能结果 |
| `GET /api/evals/trends` | 查询版本趋势摘要 |
| `GET /api/evals/performance` | 查询性能结果列表 |
| `GET /api/evals/performance/detail` | 查询性能结果明细 |
| `DELETE /api/evals/performance` | 删除性能 JSONL |
| `POST /api/evals/performance/archive` | 归档性能结果 |
| `POST /api/evals/archives` | 创建批次快照 |
| `GET /api/evals/archives` | 查询归档摘要 |
| `GET /api/evals/trace-cases/projects` | 获取 Trace Case Factory 可选项目 |
| `GET /api/evals/trace-cases/drafts` | 查询 Trace Case 草稿 |
| `POST/PUT/DELETE /api/evals/trace-cases/drafts...` | 创建、编辑、删除、校验、预览 fixture、捕获 fixture 和发布 Trace Case |

`EvaluationCenter` 对应“运行批次 → 性能结果 → 多版本对比 → 已归档”的流程：启动并轮询批次、查看逐 Case 结果、合并同版本批次、删除本地运行数据、归档可复查快照。当前数据应从这些运行时接口读取，不应把新版本数字硬编码进设计文档。

前端请求边界由 `frontend/src/services/evaluationCenterApi.ts` 统一编排：评测中心一次性加载 Case、基线、仓库版本、趋势、归档和性能结果，Trace Case Factory 单独加载 Trace、项目和草稿；具体 HTTP 请求仍集中在 `frontend/src/services/api.ts`。组件不应直接拼接评测 API 或绕过服务层。

## 4. 4.0 性能优化归档

### 4.1 三个版本的性能结果

以下结果来自同一套 16 个正式性能用例，模型均为 `deepseek-v4-flash`，分别对应运行时性能 JSONL：

| 版本快照 | 性能结果文件 | 通过率 | 平均分 | 平均耗时 | Total Tokens | Tool Calls | 状态 |
|---|---|---:|---:|---:|---:|---:|---|
| `dev-4.0@f9f38828de949b817b51b65e9bad9e6773801ab3` | `performance-merged-20260910T055742Z-6a0faaea.jsonl` | 56.25% (9/16) | 0.8509 | 55.9 s | 3,464,360 | 386 | 9 passed / 5 failed / 2 error |
| `dev-4.0@d3e28ad07551263321e848d22aa2c6605149bd2b` | `performance-merged-20260910T121123Z-8e2c7fd9.jsonl` | 56.25% (9/16) | 0.9128 | 51.3 s | 3,327,351 | 372 | 9 passed / 7 failed |
| `dev-4.0@df516876c10578a9d0c64a772eed273804edd9ad` | `performance-merged-20260911T043356Z-b731394e.jsonl` | 68.75% (11/16) | 0.9269 | 40.0 s | 2,995,190 | 380 | 11 passed / 5 failed |

相邻版本变化：

- `f9f3882 → d3e28ad`：通过率不变，但平均分提升约 7.3%，平均耗时下降约 8.2%，Token 下降约 4.0%，`error` 从 2 个降为 0 个。
- `d3e28ad → df51687`：通过率提升 12.5 个百分点，平均分提升约 1.5%，平均耗时下降约 22%，Token 下降约 10%。

### 4.2 主要提升原因

1. **工具失败恢复和变更安全性增强（最直接的能力收益）**

   `39e844c` 为 `apply_changes` 增加结构化错误码、换行归一化、SHA 校验、回滚和
   可执行恢复指令；ReAct 循环对补丁不匹配、重复失败和并发变更要求重新读取后再修改。
   `f156a33`、`bf24beb` 又增强了子代理结果、路径恢复和工具协议容错。该组改动解释了
   第一阶段错误数归零，以及 `create-remove-target` 由错误变为通过、多个失败用例得分上升。

2. **上下文压缩和执行终态治理降低了长任务尾部成本**

   `f9f3882` 引入会话语义压缩、请求级 Token 预算和稳定工具 schema；随后合入的会话压缩、
   失败恢复、Agent 启动、终态 checkpoint 和最终结果发布边界，使多轮任务更少重复探索，
   并能稳定产出最终答案。数据上表现为 Prompt 请求、总 Token 和平均耗时持续下降；
   `greeting-component`、`read-e2e` 在第三个快照恢复通过。

3. **评测结果管线从“能运行”变为“可准确归因”**

   `ea3dec1` 及后续 Evals 重构统一了 Checker 执行、单轮/多轮结果完成、工作区快照、
   批次汇总和性能转换规则。它们不一定直接提升 Agent 写代码的能力，但减少了错误状态、
   不完整结果和评分偏差，因此 `f9f3882 → d3e28ad` 的平均分增长不能全部解释为模型能力增长。

### 4.3 归因边界与剩余瓶颈

- `d3e28ad` 自身主要是前端全局 UML 优化摘要调整，当前 16 个后端评测用例不直接覆盖该路径；
  性能变化来自该快照之前累计合入的工具、运行时和评测修复。
- `df51687` 自身是文档提交，没有运行时代码；第三个快照的提升来自此前已合入的后端执行、
  上下文、Trace 和 Evals 重构，不能归因于 README 更新。
- 三次运行均为单次 16 用例测量，LLM 具有随机性，尚未形成重复运行置信区间；趋势可信，
  但单个用例的通过/失败变化仍需多次运行确认。
- 第三个快照仍有 5 个失败，集中在 `paths_unchanged`：多轮 revision、noise seed、debug/legacy
  删除和 update delay。当前首要瓶颈是受保护路径、测试文件修改边界和多轮变更规划，而不是
  基础文件读写能力。

## 5. 历史数据指标归档（截至 3.3）

以下数字按采集时间排列，范围截至 3.3（含 3.3.x）。它们仅用于回溯，不能代表 4.0 当前质量，也不能在 Case 集、模型、fixture 或预算不同的情况下直接横向比较。`—` 表示原始记录未提供该指标；“测试基线”行是工程测试，不是模型评测通过率。

| 时间 | 版本/记录 | 场景 | 结果/用例 | 通过率 | 平均分 | 耗时 | Total Tokens | Tool Calls | 其他指标 |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| 日期未记录 | 工程测试基线 | `hello_agents` 评测基础设施和 Agent 测试 | 150 passed | — | — | — | — | — | 工程测试通过数 |
| 2026-08-31 | 首轮正式评测 | DeepSeek Flash，12 个正式用例 | 9 通过 / 2 失败 / 1 超时 | 75.0% | 0.822 | 约 18.1 分钟 | 约 3,565,346 | 460 | 含领域校验、旧 UML、预算边界诊断样本 |
| 2026-09-01 | `dev-3.0` 能力基线（`a1122e8`） | 16 用例完整运行 | 10 通过 / 1 失败 / 5 超时 | 62.50% | 66.67% | 1,930.2 s | 6,639,458 | 602 | 正式用例 12 个：8 通过、1 失败、3 超时 |
| 2026-09-04 | `remove_01` 修复前 | `radar_trace_remove_v1`，7 轮对话 | 超时，6/7 轮完成 | 0% | 0.0 | 300.6 s | 380,710 | 97 | 7 个工具错误，19 个 Checker 已执行 |
| 2026-09-05 | `remove_01` 修复后 | `radar_trace_remove_v1`，7 轮对话 | 通过，7/7 轮完成 | 100% | 1.0 | 191.0 s | 352,542 | 86 | 1 个工具错误，43/43 Checker 通过 |
| 2026-09-05 | 3.3 基线 | 16 性能用例 | 5 通过 / 11 未通过 | 31.3% | 0.570 | 95.2 s（平均） | 2,701,897 | 496 | — |
| 2026-09-05 | 3.3.1 | 16 性能用例 | 6 通过 / 10 未通过 | 37.5% | 0.664 | 74.6 s（平均） | 2,811,558 | 360 | 相对 3.3：通过率 +6.2 pp |
| 2026-09-05 | 3.3.2 | 16 性能用例 | 8 通过 / 8 未通过 | 50.0% | 0.736 | 74.1 s（平均） | 2,953,943 | 398 | 相对 3.3：通过率 +18.8 pp |
| 2026-09-05 | 3.3.3 | 16 性能用例 | 7 通过 / 9 未通过 | 43.8% | 0.674 | 75.7 s（平均） | 2,754,828 | 386 | 配套定向回归测试 24 项通过 |
| 2026-09-05 | 工具/评测基础设施回归 | `remove_01` 修复相关集成测试 | 39 passed | — | — | — | — | — | 记录于修复后评测报告 |
| 2026-09-06 | `dev-3.0` 16 用例基线快照 | `backend/evals/baseline.json` | 6 通过 / 9 失败 / 0 超时 / 1 错误 | 37.5% | 0.7756 | 1,227.2 s | 2,904,977 | 420 | 版本标记早于 3.3，快照登记时间晚于部分 3.3 实验 |

历史表中的 3.3.x 指标来自独立性能 JSONL；`dev-3.0` 快照来自仓库内 `backend/evals/baseline.json`。两者 Case 集和运行目的不同，不能合并计算总通过率。

## 6. 4.0 使用规则

1. 正式基线只统计 `understanding`、`single`、`multiturn`；专项回归单独报告。
2. 正向、负向、挑战样本分开解释，不能用一个通过率掩盖安全性和能力边界。
3. 发布判断至少同时查看 Checker、工作区快照、Trace、工具调用和运行元数据。
4. 性能比较必须固定版本、模型、Prompt、Fixture、依赖和预算；缺少这些字段的结果只能作为诊断数据。
5. 新的 4.0 数字写入运行时性能结果或本节归档，不再追加到 3.3 历史指标表。

## 7. 推荐运行入口

在项目根目录分别执行正式基线 suite：

```powershell
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite understanding
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite single
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite multiturn
```

执行专项 Trace 回归或单个 Case：

```powershell
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite trace-3.1
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --ids radar-understanding-component-map-001
```

运行前确认模型配置、`hello_agents` 环境、结果/Trace 目录写权限和 Git 版本标签均已固定。需要复盘更早的设计讨论时，通过 Git 历史查看，不在当前文档恢复已删除的旧归档文件。
