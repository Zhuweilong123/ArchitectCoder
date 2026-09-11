# DevAgent 评测体系

> 文档定位：3.4 评测体系
>
> 文档状态：当前实现说明 + 截至 3.3（含 3.3.x）的历史数据指标
>
> 适用范围：`extensions/evals`、`backend/evals` 和评测中心前端；API 由 Evals 插件路由提供

本文只把 3.4 的评测结构和运行规则作为当前事实。历史性能数字统一保留至 3.3（含 3.3.1、3.3.2、3.3.3），并按采集时间归档；3.4 的新结果以 Evaluation Center 生成的运行批次、性能结果和归档快照为准，不再写入历史表。

## 1. 3.4 当前架构

3.4 的评测链路由五层组成：

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

`EvaluationCenter` 对应“运行批次 → 性能结果 → 多版本对比 → 已归档”的流程：启动并轮询批次、查看逐 Case 结果、合并同版本批次、删除本地运行数据、归档可复查快照。当前数据应从这些运行时接口读取，不应把新版本数字硬编码进设计文档。

## 4. 历史数据指标归档（截至 3.3）

以下数字按采集时间排列，范围截至 3.3（含 3.3.x）。它们仅用于回溯，不能代表 3.4 当前质量，也不能在 Case 集、模型、fixture 或预算不同的情况下直接横向比较。`—` 表示原始记录未提供该指标；“测试基线”行是工程测试，不是模型评测通过率。

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

## 5. 3.4 使用规则

1. 正式基线只统计 `understanding`、`single`、`multiturn`；专项回归单独报告。
2. 正向、负向、挑战样本分开解释，不能用一个通过率掩盖安全性和能力边界。
3. 发布判断至少同时查看 Checker、工作区快照、Trace、工具调用和运行元数据。
4. 性能比较必须固定版本、模型、Prompt、Fixture、依赖和预算；缺少这些字段的结果只能作为诊断数据。
5. 新的 3.4 数字写入运行时性能结果或正式归档，不再追加到本文的历史指标表。

## 6. 推荐运行入口

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
