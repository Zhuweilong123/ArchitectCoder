# 智能体评测体系归档

> 归档版本：3.0 evaluation baseline
> 归档日期：2026-08-31
> 适用范围：`backend/app/evals` 执行代码、`backend/evals` 评测数据、评测 API、评测中心前端和运行结果治理

本文档记录当前智能体评测体系的实际构建结果、目录约定、运行链路、历史基线和未完成事项。它是评测系统的总览归档；`docs/agent-v3.0-baseline.md` 继续承担 3.0 版本整体工程基线的职责。

## 1. 建设目标

评测体系用于回答四个问题：

1. Agent 能否在固定项目上下文中完成指定任务。
2. Agent 是否修改了正确的资源，并遵守设计、源码和测试之间的边界。
3. 一次运行失败时，能否通过 Checker、Trace 和运行元数据定位原因。
4. 不同模型、版本和提示词之间，能力指标能否进行可重复的横向和纵向比较。

当前体系采用“固定项目 fixture + 隔离工作区 + Agent 执行 + 确定性 Checker + Trace/结果归档”的闭环。评测不是直接在主项目目录上运行，因此评测过程中的错误修改不会污染真实设计文件。

## 2. 当前目录和资源边界

### 2.1 评测代码

```text
backend/app/evals/          # 评测执行代码
├── models.py    # EvalCase、EvalResult、CheckerResult 等模型
├── registry.py  # 用例注册和加载
├── projects.py  # 项目清单加载、fixture 路径解析
├── runner.py    # 单用例隔离执行器
├── checkers.py  # 确定性检查器
├── batches.py   # 批次、汇总、趋势和快照归档
└── cli.py       # 命令行运行入口

backend/evals/               # 版本化评测数据
├── cases/                   # 用例定义，JSON，受控加载
├── projects/                # 项目 manifest，声明 fixture 和资源边界
├── fixtures/                # 可复制、可复现的项目快照（支持 base_fixture 覆盖层）
└── baseline.json            # 基线指标
```

### 2.2 项目固定为三类资源

每个项目 fixture 的标准结构是：

```text
<fixture>/
├── design/      # UML 设计文件，通常为 *.umlproj
├── src/         # Agent 可修改的源码
├── test/        # 项目测试和评测辅助测试
└── DESIGN.md    # 项目说明和任务上下文
```

这是当前评测体系的资源契约：

- `design/` 代表设计事实源。
- `src/` 代表实现事实源。
- `test/` 代表行为验证事实源。
- `DESIGN.md` 提供人类可读的领域背景，但默认属于受保护资源。

目前项目清单有 7 个：

| 项目 | 用途 |
|---|---|
| `radar_sim_v1` | 当前雷达信号处理基线项目 |
| `radar_sim_delay_bug_v1` | 回波延迟方向缺陷修复 |
| `radar_sim_padding_bug_v1` | PRT 脉冲序列补零缺陷修复 |
| `radar_sim_validation_v1` | 领域参数有限值和边界校验 |
| `radar_sim_noise_seed_v1` | 噪声随机种子和可复现性 |
| `radar_sim_stale_uml_v1` | 旧版 UML 向当前代码和设计迁移 |
| `radar_sim_broken_uml_v1` | 损坏 UML 文件恢复，同时保护主设计文件 |

项目 manifest 只允许相对路径，并由 `projects.py` 将 fixture 限制在 `backend/evals/fixtures/` 目录内。`base_fixture` 可选，用于声明一个完整基础快照；运行时由 `fixture_materializer.py` 先复制基础 fixture，再用当前 fixture 目录中的差异文件覆盖。默认保护路径是 `design/radar_sim_design.umlproj` 和 `DESIGN.md`，可写范围通常是 `src`、`test`，迁移项目额外允许 `legacy`。

## 3. 用例资产和分层

当前 `backend/evals/cases/` 共有 18 个用例，按元数据中的 `suite` 分组：

| 分组 | 数量 | 主要验证内容 | 是否用于正式基线 |
|---|---:|---|---|
| `baseline` | 2 | 项目理解、端到端处理流 | 是 |
| `p0` | 4 | 延迟、波形补零、全链路、领域参数 | 是 |
| `p1` | 3 | 随机性、UML-代码契约、旧版 UML 迁移 | 是 |
| `p2` | 3 | 损坏文件恢复、预算边界、只读保护 | 是 |
| `diagnostic` | 4 | 从父用例拆出的领域校验和 UML 迁移定位用例 | 否，主要用于诊断 |
| `trace-3.1` | 2 | 组件图命名迁移和连续多轮对话能力 | 否，专项回归 |

正式基线目前是前四组共 12 个用例；`diagnostic` 用例用于把一个复合失败拆成更小的能力问题，`trace-3.1` 用例用于专项回归，均不应直接和正式正向通过率混合计算。

用例 JSON 的主要字段是：

```json
{
  "id": "radar-p0-delay-001",
  "prompt": "修复指定领域缺陷并补充必要回归测试",
  "project_id": "radar_sim_delay_bug_v1",
  "hard_checkers": [],
  "checkers": [],
  "max_seconds": 180,
  "max_tool_calls": 40,
  "max_total_tokens": 50000,
  "metadata": {
    "suite": "p0",
    "capability": "echo_simulation",
    "risk": "high"
  }
}
```

当前用例的能力覆盖包括：

- 项目理解和端到端流程理解。
- 领域逻辑修复：延迟方向、PRT 补零、参数有限值、SNR 边界。
- 可复现性：随机种子注入和噪声结果稳定性。
- UML 正确性：文件可解析、图/类/组件/方法存在、关系存在、时序顺序正确。
- UML 与代码一致性及旧版设计迁移。
- 只读任务、受保护文件、损坏设计文件恢复和运行预算控制。

## 4. 单用例执行链路

```text
加载 Case
   ↓
解析 ProjectManifest，校验 fixture 边界
   ↓
复制 fixture 到临时工作区
   ↓
记录 paths_unchanged 文件的基线 hash
   ↓
创建独立 TraceSession
   ↓
创建带项目路径和任务预算的生产 DevAgent
   ↓
流式执行 Agent，收集工具调用、Token 和最终状态
   ↓
执行 hard_checkers 和 checkers
   ↓
汇总 EvalResult，追加写入 results.jsonl
   ↓
删除临时工作区，保留 Trace 路径和结果元数据
```

实现位置：`backend/app/evals/runner.py`。

隔离执行的关键行为：

- 每个用例使用独立临时目录。
- Agent 的 `source_dir`、`test_dir` 和 `project_file` 均指向临时工作区。
- `paths_unchanged` 在执行前保存 SHA-256，执行后验证文件未发生改变。
- 单用例受最大运行时间、工具调用数、总 Token 数和 LLM 调用超时共同约束。
- Trace 使用独立的 `TraceSession`，结果包含 `run_id`、`trace_id`、`trace_path`、模型和运行时指标。
- 临时工作区在用例结束后删除，因此结果中不会保留可直接继续编辑的 workspace。

## 5. Checker 体系

### 5.1 通用 Checker

| Checker | 作用 |
|---|---|
| `file_exists` | 验证目标文件存在 |
| `file_contains` | 验证文件包含指定文本 |
| `json_field` | 验证 JSON 路径上的字段值 |
| `pytest` | 在隔离工作区运行项目测试 |
| `paths_unchanged` | 验证指定文件的内容 hash 未改变 |

### 5.2 UML Checker

| Checker | 作用 |
|---|---|
| `uml_valid` | 验证 UML 项目可解析且包含有效图列表 |
| `uml_contains` | 验证图、组件、类或消息存在 |
| `uml_relation` | 验证两个 UML 元素间的关系及关系类型 |
| `uml_method` | 验证类中存在指定方法 |
| `uml_sequence` | 验证时序图中的消息标签和顺序 |

所有 Checker 当前都属于确定性检查器，执行结果包含 `passed`、`score`、`message` 和 `details`。这使得相同 fixture 在不调用 LLM 的情况下可以重复验证。

### 5.3 当前语义边界

`hard_checkers` 和 `checkers` 已在数据模型中区分，但当前 Runner 会分别执行两组检查器，并将两组结果一起计算最终 `passed` 和平均 `score`；hard checker 目前不是“失败即立即停止”的短路门禁。这个实现足以支持 MVP，但发布门禁前应进一步明确：

- hard checker 失败是否直接判定该用例不可发布。
- soft checker 是否只影响分数而不影响通过状态。
- pytest、UML 合法性和保护路径是否应统一作为强门禁。

## 6. 结果、Trace 和归档

### 6.1 单用例结果

`EvalResult` 当前包含：

- `run_id`、`case_id`、`status`、`passed`、`score`。
- `started_at`、`duration_ms`、`model`。
- `tool_calls`、`total_tokens`。
- `checker_results` 和 `error`。
- `trace_id`、`trace_path`。
- `metadata`，目前用于记录 `project_id`、`batch_id`、`version` 等上下文。

单用例结果默认追加到：

```text
<uml_dir 的父目录>/evals/results.jsonl
```

JSONL 采用追加模式，适合保留运行历史，但目前没有自动去重、版本索引或结果数据库。

### 6.2 批次结果

`EvalBatchManager` 支持按 suite 或指定 `case_ids` 启动批次，并按用例 ID 顺序串行执行。批次汇总指标包括：

- 总用例数、完成数、通过数、失败数、超时数、错误数。
- 通过率和平均得分。
- 平均耗时、总 Token、总工具调用数。
- 当前用例、开始时间和完成时间。

批次完成后追加到：

```text
<uml_dir 的父目录>/evals/batches.jsonl
```

当前批次管理器是进程内实现：同一进程只允许一个活动批次；服务重启后，已持久化批次可查询，但运行中的任务不能自动恢复。

### 6.3 快照归档

归档操作将完整批次对象复制到：

```text
<uml_dir 的父目录>/evals/archives/archive_<UTC时间>_<随机后缀>.json
```

快照包含归档 ID、创建时间、备注和完整批次结果，包括 Checker 明细、模型、Trace ID 和运行元数据。归档 ID 已包含时间标识和随机后缀；单次 Trace 文件名仍以 run/session ID 为主，时间通过 `started_at` 和 Trace 事件记录表达。当前设计不要求再把时间重复写入 Trace 文件名。

## 7. API 和前端闭环

评测 API 位于 `backend/app/api/evals.py`，并由统一认证依赖保护：

| API | 作用 |
|---|---|
| `GET /api/evals/cases` | 获取用例目录 |
| `POST /api/evals/run` | 执行单个用例 |
| `GET /api/evals/results` | 查询最近单用例结果 |
| `POST /api/evals/runs` | 按 suite 或 case IDs 启动批次 |
| `GET /api/evals/runs` | 查询批次列表 |
| `GET /api/evals/runs/{batch_id}` | 查询批次进度和明细 |
| `GET /api/evals/trends` | 查询按版本组织的趋势数据 |
| `POST /api/evals/archives` | 创建批次快照 |
| `GET /api/evals/archives` | 查询归档摘要 |

前端 `EvaluationCenter` 已接入工具栏，提供：

1. 选择评测 suite 和版本号。
2. 一键启动批次。
3. 每 2 秒轮询批次进度。
4. 查看当前批次的通过率、平均得分、平均耗时、Token、工具调用和用例明细。
5. 查看历史批次趋势列表。
6. 一键创建归档快照。

当前前端是评测中心 MVP：当前批次、趋势和归档列表均展示总数、完成数、通过数、失败数、超时数、错误数、通过率、平均得分、平均耗时、Token 和工具调用；趋势仍使用列表展示，还没有真正的折线图、版本差异标记、失败用例钻取和 Trace 直达按钮。

## 8. 历史运行基线

### 8.1 工程测试基线

在 `hello_agents` conda 环境中，后端工程测试曾达到：

```text
150 passed
```

这是评测基础设施和 Agent 工程测试的基线，不等同于模型评测通过率。

### 8.2 首轮正式评测

2026-08-31 使用配置的 DeepSeek Flash 模型运行 12 个正式基线用例，历史记录为：

| 指标 | 结果 |
|---|---:|
| 用例数 | 12 |
| 通过 | 9 |
| 失败 | 2 |
| 超时 | 1 |
| 平均 Checker 得分 | 0.822 |
| 工具调用 | 460 |
| Trace 汇总 Token | 约 3,565,346 |
| 总耗时 | 约 18.1 分钟 |

历史失败中，`radar-p0-validation-001` 只完成了部分非有限参数校验；`radar-p1-uml-stale-001` 未补齐旧 UML 的目标类、方法和时序消息；`radar-p2-budget-001` 按预期在 5 秒预算耗尽后停止。最后一个属于负向/边界行为验证，不应简单等同于普通功能失败。

后续诊断运行表明，领域参数和 SNR 校验已经可以单独通过；旧版 UML 的 API 流程和拓扑迁移仍是当前挑战能力。当前已将上述汇总转换为本地历史归档：`temp/evals/archives/archive_20260831T000000Z_historical_v3_0_initial.json`。该文件位于运行目录并被 Git 忽略，包含汇总指标但不包含原始逐用例结果，因此可以被前端趋势和归档列表读取，但不能替代完整可复查的正式归档。

## 9. 样本解释规则

评测结果应至少分成三类：

- 正向样本：目标是完成任务并通过全部发布门禁，用于计算正式通过率。
- 负向样本：目标是拒绝危险操作、保护文件、遵守只读约束或在预算耗尽时安全停止。失败通常表示安全性问题。
- 挑战样本：允许当前模型失败，用于记录能力边界、引导后续改进，不应混入正向发布通过率。

当前用例通过 `suite`、`capability`、`risk` 和部分 `expected_runtime_behavior` 表达这些信息，但尚未有统一的 `sample_type`、`expected_status` 和 `release_gate` 字段。正式上线前应将样本语义从约定升级为结构化字段。

## 10. 已完成能力评估

### 已完成

- 用例和项目 manifest 受控加载。
- 每个项目固定为 `design/src/test` 三资源边界。
- fixture 复制到临时工作区，避免污染真实项目。
- Agent 运行预算、工具调用预算和 Token 预算。
- 文件、pytest、UML 和受保护路径 Checker。
- 单用例 Trace、运行结果和 Agent Metrics 关联。
- CLI 单用例/套件运行入口。
- API 单用例运行、批次运行、趋势查询和归档。
- 前端评测中心 MVP 和一键归档。
- 正向、负向、挑战样本的解释原则已经确定。

### 尚未完成或仅有 MVP 实现

- 批次管理是进程内的，不能跨进程、跨重启恢复。
- 没有统一的评测运行 manifest，模型、系统提示词、代码 commit、依赖锁定和环境快照未形成不可变组合。
- hard checker 还没有完全落实为发布门禁语义。
- 结果 JSONL 没有 schema version、唯一性约束和写入锁。
- 归档有完整快照写入，但没有归档内容 hash、签名、导出下载和恢复接口。
- 前端趋势是列表，没有指标折线图、回归标识和失败 Trace 钻取。
- 失败诊断仍主要依赖 Checker message 和人工查看 Trace，尚未统一生成失败分类。
- Checker 对领域行为的覆盖仍少于对文件存在性、字符串和 UML 结构的覆盖。
- 没有稳定的多模型/多温度重复运行策略，随机性和置信区间尚未纳入正式报告。

## 11. 后续演进优先级

### P0：上线评测门禁前必须完成

1. 为用例增加 `sample_type`、`expected_status`、`release_gate`、`case_version` 和 `owner`。
2. 明确 hard checker 的门禁规则，至少将 pytest、UML 合法性和受保护路径设为可配置强门禁。
3. 建立 `EvalRunManifest`，固定代码 commit、模型、模型配置、提示词版本、依赖环境、fixture 版本和预算。
4. 为结果和归档增加 schema version、原子写入、文件锁和内容 hash。
5. 为批次增加取消、失败重试策略和服务重启后的状态恢复，避免“前端还在轮询但任务已丢失”。

### P1：提高评测解释能力和迭代效率

1. 前端增加通过率、得分、耗时和 Token 的折线趋势图，并标记版本回归。
2. 支持从批次 → 用例 → Checker → Trace 的逐级钻取。
3. 增加 UML-代码语义一致性、行为输出、边界值和回归测试 Checker，降低对 `file_contains` 的依赖。
4. 为失败结果生成标准化分类：模型拒答、工具选择错误、参数错误、实现不完整、测试失败、预算耗尽、基础设施错误。
5. 将 diagnostic 用例和父用例建立显式关联，自动生成“复合失败 → 能力分解”的报告。

### P2：规模化和长期治理

1. 将进程内批次管理迁移到持久化队列或数据库，支持多 Worker。
2. 增加成本估算、延迟分位数、重试率、模型路由和 Token 价格维度。
3. 支持多次重复运行、置信区间、随机种子矩阵和模型对比。
4. 建立评测集版本、fixture 版本和变更审批流程。
5. 接入 CI、Prometheus 或其他监控系统，把正向门禁和挑战集报告分开发布。

## 12. 推荐运行方式

在项目根目录执行正式基线：

```powershell
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite baseline
```

执行 P0 或单个诊断用例时：

```powershell
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --suite p0
conda run --no-capture-output -n hello_agents python -m extensions.evals.cli --ids radar-p1-uml-flow-001
```

实际执行前应确认：

- 后端 `.env` 中的模型和 API 配置已固定。
- 使用的 conda 环境为 `hello_agents`。
- 评测结果目录和 Trace 目录具有写权限。
- 运行版本号填写 Git commit 或明确的工作区标签。
- 正向、负向、挑战样本分别统计，不用单一通过率掩盖安全性和能力边界。

## 13. 归档结论

当前体系已经从“手动调用 Agent、人工查看结果”升级为可重复的评测 MVP：有固定项目、有隔离执行、有确定性判定、有 Trace、有批次指标、有前端入口和结果快照能力。

它已经足够支撑早期模型对比和能力诊断，但还不应直接视为生产级发布门禁。下一阶段最重要的工作不是继续扩充用例数量，而是先完成评测样本语义、运行 manifest、强门禁规则和可恢复批次这四项治理能力；完成后，评测结果才具备稳定的版本比较和上线决策价值。

## 14. 3.3.1–3.3.3 三轮优化归档

本节归档 2026-09-05 针对 16 个性能用例完成的三轮探索。每轮均使用生产环境相同的单次 Agent 探索预算、用户输入形式和既有工具集合；没有新增工具、没有改变主流程、没有扩大预算，也没有向用户用例注入额外 Prompt。每轮结果均写入独立 JSONL 文件，互不覆盖。

### 14.1 版本指标

| 版本 | 通过 | 通过率 | 平均得分 | 平均耗时 | 总 Token | 工具调用 | 相对 3.3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 3.3 基线 | 5/16 | 31.3% | 0.570 | 95.2 s | 2,701,897 | 496 | — |
| 3.3.1 | 6/16 | 37.5% | 0.664 | 74.6 s | 2,811,558 | 360 | +6.2 pp |
| 3.3.2 | 8/16 | 50.0% | 0.736 | 74.1 s | 2,953,943 | 398 | +18.8 pp |
| 3.3.3 | 7/16 | 43.8% | 0.674 | 75.7 s | 2,754,828 | 386 | +12.5 pp |

指标来源：

- 3.3 基线：`backend/temp/evals/performance-16-20260905.jsonl`
- 3.3.1：`backend/temp/evals/performance-16-20260905-v3.3.1.jsonl`
- 3.3.2：`backend/temp/evals/performance-16-20260905-v3.3.2.jsonl`
- 3.3.3：`backend/temp/evals/performance-16-20260905-v3.3.3.jsonl`

### 14.2 每轮改动和验证

#### 3.3.1：减少工具输出截断造成的重复探索

- 修改 `backend/app/agent_base/core/hooks.py` 的默认 `TruncateHook` 配置：`read_file` 上限调整为 6000 字符，`search_text` 为 4000，`run_task` 为 6000，`skill` 保持 20000；全局默认上限仍保持 1200。
- 目标是让 Agent 一次获得足够的代码上下文，减少因输出被截断而反复读取同一文件。
- 结果：工具调用从基线 496 降至 360，平均耗时从 95.2 s 降至 74.6 s，通过率提升 6.2 个百分点。
- 代表性 Trace：`temp/chat_log/trace_20260905_172343_96609abbfb974d73_eval.jsonl`

#### 3.3.2：在既有预算阈值触发一次收敛检查点

- 修改 `backend/app/agent_base/agents/react_agent.py`：沿用既有的工具步数/Token 预算阈值，首次达到阈值时追加一次运行时 system checkpoint。
- 约束 Agent 停止继续广泛探索，转入修改、验证和收尾，并避免重复读取已确认文件；没有修改用户原始 Prompt，也没有改变预算值。
- 结果：16 个用例中通过 8 个，为三轮最佳；平均得分 0.736，平均耗时 74.1 s。
- 代表性 Trace：`temp/chat_log/trace_20260905_174426_44913bb1773940ca_eval.jsonl`

#### 3.3.3：对工具失败提供一次性恢复指引

- 修改 `backend/app/agent_base/agents/react_agent.py`：按不同的工具名/错误码识别失败签名，每种新失败只追加一次 recovery checkpoint。
- 指引 Agent 将失败视为已知证据，禁止机械重试，要求检查目标、进行最小修复并做聚焦验证；无法继续时明确报告限制。
- 结果：通过 7/16，平均得分 0.674；总 Token 降至 2,754,828，工具调用降至 386，较 3.3.2 更节省资源，但通过率回落 6.2 个百分点。
- 代表性 Trace：`temp/chat_log/trace_20260905_180631_e90b7dd681dd4e87_eval.jsonl`

三轮代码修改均通过定向回归测试：`tests/agent_base/test_react_agent.py` 与 `tests/agent_base/test_evidence.py` 共 24 项通过。

### 14.3 结果判断

1. 3.3.2 是当前质量最好的组合：收敛检查点在不增加预算的前提下改善了通过率和平均得分。
2. 3.3.3 对资源消耗有积极作用，但尚未证明恢复指引能稳定提升成功率；后续应重点观察失败类型分布，而不是继续叠加 Prompt。
3. 三轮中反复出现的能力缺口集中在领域校验、旧 UML 同步、UML 拓扑/时序流和预算边界行为；这些属于 Agent 执行与评测样本能力问题，不是结果文件或 Trace 落盘问题。
4. 当前结果适合用于版本对比和问题定位，暂不作为单一发布门禁；应结合正向、负向、挑战用例以及每个用例的 Trace 共同判断。
