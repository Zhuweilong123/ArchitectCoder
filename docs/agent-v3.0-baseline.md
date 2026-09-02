# 智能体 3.0 基线归档

> 版本：3.0 baseline
> 归档日期：2026-08-31
> 适用范围：`backend` Agent、工具系统、会话编排、UML/KG 协作链路

本文档将 P0、P1、P2 优化作为 3.0 版本的基础能力归档。后续功能默认建立在本基线上，不再回退到旧的全局 Trace、无边界文件写入、无预算 Agent 执行模型。

评测体系的完整结构、用例分层、运行链路、历史结果和后续治理计划见：[评测体系归档](evaluation-system-archive.md)。

## 1. 版本提交基线

| 层级 | Commit | 内容 |
|---|---|---|
| P0/P1 基础 | `5a7e7fc` | Trace、鉴权、路径安全、会话并发、审核生命周期、文件安全、工具路由与任务隔离 |
| P1 深化 | `2e458d8` | ChangeSet、KG 刷新、运行时间/token/LLM 超时预算、审核 session/project 绑定 |
| P2 治理 | `b8afd54` | ToolResult、模型路由、Agent 指标、MemoryManager 生命周期、生产模式校验 |

当前 3.0 代码基线位于本地 `dev` 分支的 `b8afd54`。

## 2. 3.0 总体架构

```text
WebSocket / HTTP API
        │
        ▼
Session Registry ── session lease / history / trace
        │
        ▼
ReActAgent
  ├─ tool routing
  ├─ step / tool / token / wall-time budgets
  ├─ duplicate-call circuit breaker
  └─ structured ToolResult
        │
        ├─ File Tools ── workspace allowlist ── atomic write ── ChangeSet
        ├─ UML Review ── stable id/token ── session/project binding
        ├─ Knowledge Graph ── background incremental refresh
        ├─ Task DAG ── session-scoped tasks/worktrees
        └─ Memory ── recall/archive lifecycle management
```

核心原则：

1. Agent 的工具调用必须可限制、可观察、可回放。
2. 文件和 UML 变更必须有工作区边界、版本冲突检测和变更记录。
3. 会话、Trace、Review、Task 必须按 session/project 隔离。
4. 工具执行结果和失败原因必须能够被程序识别，而不仅是自然语言文本。

## 3. P0 基础安全与可靠性

### 3.1 Trace 隔离与生命周期

- Trace hook 使用 `ContextVar`，按协程隔离并发 WebSocket 会话。
- `ChatTraceLogger.close()` 先写入 `session_end`，再关闭 logger。
- Trace 保留 `trace_id/span_id/parent_span_id/span_path`，支持子 Agent 调用链定位。
- 连接断开时只清理当前连接的 hook，不再清空其他会话的全局状态。

涉及：`app/services/chat_trace.py`、`app/services/agent_chat_ws.py`。

### 3.2 API 与工作区安全

- HTTP 的 optimize、Trace、metrics 接口接入统一 Bearer Token 鉴权。
- WebSocket 接入独立的 `require_ws_auth()` 鉴权流程。
- Agent 提供的 `source_dir/test_dir/project_file` 先经过工作区白名单校验，再交给底层文件工具。
- 默认允许仓库目录和 `uml_dir`；外部源码目录必须通过 `WORKSPACE_ROOTS` 显式配置。
- `BashTool` 保留高危命令拒绝和敏感命令人工审核，并额外阻止命令串联、重定向、嵌套 Shell、命令替换和内联解释器代码。

### 3.3 会话和审核并发安全

- 同一个 `session_id` 同时只允许一个 Agent run 持有执行租约。
- Review ID 使用单调整数和不可猜测 token，不再使用 pending list 下标，避免 reset 后旧响应完成新请求。
- Review 请求携带 `session_id/project_id`，WebSocket resolve 时校验 session 归属。
- 连接断开会取消当前连接产生的未完成审核请求。

### 3.4 文件修改安全

- `write_file/edit_file` 使用同目录临时文件 + `os.replace()` 原子替换。
- 支持 `expected_sha256`，检测 Agent 读取后文件是否被其他进程修改。
- 写入结果返回新的 SHA-256，便于后续工具链进行版本衔接。

## 4. P1 执行闭环

### 4.1 ChangeSet 变更边界

`ChangeSet` 位于 `app/services/change_set.py`，为一个 Agent run 记录：

- 文件路径
- before 是否存在
- before SHA-256
- after SHA-256
- before 内容快照

当前工作方式是“即时写入 + 变更日志 + 可回滚”：

- 文件工具写入时自动记录 ChangeSet。
- Agent run 成功结束时执行 `commit()`。
- 需要恢复时执行 `rollback()`。
- 变更包含 `.umlproj/.uml` 时，在提交边界触发后台 KG 重建。

注意：当前还不是审批通过后才落盘的两阶段暂存模型；后续可在此基础上增加 staged workspace、Diff Viewer 和显式 commit。

### 4.2 Agent 预算与熔断

Agent 当前支持：

- 最大 Agent 步数
- 最大工具调用次数
- 相同工具/参数的重复调用熔断
- 单次 LLM 调用超时
- 单次 run 墙上时间预算
- 累计 token 预算

预算触发后向 Agent 返回可解释的阻断信息，并结束当前 run，避免无限循环和成本失控。

### 4.3 工具路由与并行

- 简单闲聊不向模型暴露工具 schema。
- UML/架构任务优先暴露 UML、KG、文件和审核工具。
- 代码/测试任务暴露文件、Bash、Task、Subagent 等工具。
- 不确定请求保留全量工具，避免启发式误判导致任务无法完成。
- 明确声明 `read_only + can_parallel` 的工具可在同一轮并行执行，目前主要覆盖 `read_file` 和 `glob`。
- 其他副作用工具默认串行。

### 4.4 Task 隔离与 KG 刷新

- Task DAG 和 Git worktree 按 session/project scope 生成独立目录。
- Agent 直接修改 UML 文件后，ChangeSet 在成功提交边界触发 KG 后台刷新。
- 原有 `save_project()` 的 KG rebuild hook 继续保留。

## 5. P2 治理能力

### 5.1 结构化工具结果

新增 `ToolResult`：

```json
{
  "status": "success|error|blocked",
  "data": "工具结果",
  "error_code": "",
  "retryable": false
}
```

兼容策略：旧的字符串 API 继续返回 `result.text`；ReAct 进度详情额外记录 `status/error_code/retryable`。

### 5.2 模型路由

`model_router.py` 根据任务复杂度选择模型：

- 简单问答：`deepseek_model_flash`
- UML、架构、代码、重构、一致性分析或长文本任务：`deepseek_model`

路由器返回模型、tier 和选择原因，后续可继续接入成本、失败率和上下文长度策略。

### 5.3 可观测性

新增 `AgentMetrics` 和受保护接口：

```text
GET /api/metrics
```

当前统计：

- Agent run 总数及成功/失败数
- 工具调用总数及按状态统计
- 按工具名称统计调用次数
- 工具累计耗时

### 5.4 记忆生命周期

- Recall 完成后显式关闭 `MemoryManager`。
- 后台归档完成或异常后显式关闭 `MemoryManager`。
- 保留原有记忆提取失败不影响主 Agent 流程的容错策略。

### 5.5 生产模式校验

新增 `STRICT_PRODUCTION` 配置：启用后必须同时满足：

- `debug=false`
- `internal_api_token` 非空

否则服务启动失败，避免生产环境无鉴权运行。

### 5.6 评测 MVP

3.0 基线新增 `app/evals/` 评测能力，提供受保护的评测接口：

- `GET /api/evals/cases`：列出 `backend/evals/cases/*.json` 中的评测用例。
- `POST /api/evals/run`：按 `case_id` 在临时工作区执行 Agent，并持久化结果。
- `GET /api/evals/results`：读取最近的 JSONL 评测结果。
- `POST /api/evals/runs`：按 suite 或 case_ids 异步启动评测批次。
- `GET /api/evals/runs/{batch_id}`：查询批次进度、用例明细和当前指标。
- `GET /api/evals/trends`：读取按版本记录的批次趋势。
- `POST /api/evals/archives`、`GET /api/evals/archives`：创建和查询评测快照归档。

每个用例可声明 fixture、最大时长、工具调用数、token 预算以及以下确定性检查器：
`file_exists`、`file_contains`、`json_field`、`pytest`、`paths_unchanged` 以及 UML 专用检查器。评测运行会关联独立 Trace，结果写入 `evals/results.jsonl`，并记录到 Agent Metrics。

评测固定使用生产 DevAgent，项目文件指向临时评测工作区，避免评测过程修改全局设计目录。正式接入真实任务集时，应将 fixture 放入受控目录，并补充成功率、工具选择、成本、延迟和回归阈值。

### 5.7 radar_sim_v1 领域评测集

评测项目基于 `project/src` 冻结为 `radar_sim_v1`，包含四个雷达信号处理组件、六张 UML 图和 37 个原始测试。根目录的旧版 `radar_design_0730.umlproj` 单独作为 UML 迁移 fixture，不与当前基线混用。

首批用例位于 `backend/evals/cases/`，当前共 18 个：2 个 baseline、4 个 P0、3 个 P1、3 个 P2、4 个 diagnostic，以及 2 个 `trace-3.1` 对话评测用例。fixture 位于 `backend/evals/fixtures/`，项目清单位于 `backend/evals/projects/`。每个项目固定为同级的 `design/`、`src/`、`test/` 三类资源，分别表示设计、源码和测试。P0 fixture 包含延迟方向、PRT 补零和非有限参数三个可复现缺陷；P1 fixture 包含噪声 seed 可复现性契约，用于验证 Agent 是否真正完成修复。

UML 检查器已支持项目有效性、组件/类/方法存在性、关系存在性和时序消息顺序；`paths_unchanged` 用于验证只读任务和受保护文件不被修改。

### 5.8 首轮真实模型结果

2026-08-31 使用 `backend/.env` 配置的 `deepseek-v4-flash` 顺序运行全部 12 个用例：9 个通过、2 个失败、1 个按预算超时，平均 Checker 得分 0.822。完成用例耗时约 18.1 分钟，工具调用 460 次；从 Trace 中汇总的 LLM token 约 3,565,346。

失败项：`radar-p0-validation-001` 仅完成部分非有限参数校验；`radar-p1-uml-stale-001` 未补齐旧 UML 的目标类、方法和时序消息。`radar-p2-budget-001` 按预期在 5 秒预算耗尽后停止。

### 5.9 评测中心 MVP

前端已增加“评测中心”入口，形成“选择 suite/版本 → 一键启动 → 轮询进度 → 查看指标和用例明细 → 一键归档”的闭环。当前指标包括通过率、平均得分、平均耗时、Token、工具调用次数，并保留每个用例的状态、模型和 Trace ID。

批次汇总持久化到 `temp/evals/batches.jsonl`，快照持久化到 `temp/evals/archives/archive_*.json`。归档记录包含版本、评测集、批次结果、Checker 明细和运行元数据；Trace 仍通过原有 Trace API 按 `trace_id` 查询。

评测结果按用途区分为正向样本、负向样本和挑战样本：负向样本用于验证拒绝、保护和预算边界；挑战样本允许当前模型失败，用于记录能力边界，不应与正向样本混合计算发布通过率。

## 6. 配置基线

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `AGENT_MAX_STEPS` | `50` | Agent 最大循环步数 |
| `AGENT_MAX_TOOL_CALLS` | `100` | 单次 run 最大工具调用数 |
| `AGENT_MAX_REPEATED_TOOL_CALLS` | `3` | 相同工具参数最大重复次数 |
| `AGENT_MAX_RUN_SECONDS` | `600` | 单次 run 墙上时间预算 |
| `AGENT_MAX_TOTAL_TOKENS` | `100000` | 单次 run token 预算 |
| `AGENT_LLM_TIMEOUT_SECONDS` | `120` | 单次 LLM 调用超时 |
| `WORKSPACE_ROOTS` | 空 | 外部工作区白名单，逗号分隔 |
| `STRICT_PRODUCTION` | `false` | 生产安全配置强校验 |
| `INTERNAL_API_TOKEN` | 空 | HTTP/WebSocket Bearer Token |

## 7. 验证基线

在 `hello_agents` conda 环境中执行：

```powershell
conda run --no-capture-output -n hello_agents python -m pytest -q
```

当前基线结果：

- 150 passed
- Python 静态编译通过
- `git diff --check` 通过

## 8. 上下文与记忆治理状态

当前 DevAgent 已具备显式上下文预算、会话历史压缩、Trace checkpoint 恢复，以及记忆写入
门禁、来源追踪、召回筛选、注入安全边界和持久化维护状态。详细设计见：

- [`context-management-design.md`](context-management-design.md)
- [`memory-system-design.md`](memory-system-design.md)

## 9. 3.0 后续演进项

以下内容不影响 3.0 基线使用，但属于后续深化：

1. ChangeSet 两阶段暂存、审批后提交、完整 Diff 和跨文件回滚。
2. Redis/数据库持久化会话、审核和分布式租约，支持多 Worker 与服务重启恢复。
3. 进程/容器级 Bash 沙箱，替代单纯的命令过滤。
4. optimize_v2 与统一 Agent ChangeSet、Review、Trace、KG 提交流程完全合并。
5. 记忆冲突版本管理、异步归档幂等、隐私过滤和自动过期；来源、置信度、召回治理和持久化维护已在当前版本落地。
6. 基于真实任务集的 Agent Eval，包括成功率、工具选择、成本、延迟和回归测试。
7. 更完整的 token/cost/latency 指标，并接入 Prometheus 等监控系统。
