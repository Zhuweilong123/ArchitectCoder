# Agent 运行收敛与资源预算

本文归档 ArchitectCoder 当前 DevAgent 的两类运行时治理能力：

- `ExecutionBudget`：控制一次运行可消耗的硬资源；
- `ConvergenceController`：识别模型是否持续重复、失败重试或没有产生有效进展。

两者通过通用 Hook 接入 Agent 主循环。主循环只发布生命周期事件并消费控制信号，不直接实现策略细节。

## 1. 设计目标

DevAgent 使用开放式 `while True` 循环，不以固定步数作为正常任务的终止条件。这样可以支持复杂的多文件修改、验证和修复任务，避免任务仅因为达到固定步数而被截断。

开放循环并不意味着没有边界：

1. `ExecutionBudget` 防止单次运行无限消耗工具调用、时间或 Token；
2. `ConvergenceController` 防止模型在相同动作、相同失败或无进展状态中持续循环；
3. 上下文预算和压缩机制控制发送给模型的上下文规模；
4. Trace、Evidence Ledger 和运行报告记录每次决策及其依据。

## 2. 组件职责

### 2.1 ExecutionBudget

代码位置：`backend/app/agent_base/core/policy.py`

`ExecutionBudget` 是一次运行（per-run）的资源计量器和硬边界判断器，负责：

- 记录运行起始时间；
- 记录已执行的工具调用数；
- 记录模型累计 Token 使用量；
- 在下一次 LLM 调用前检查时间和 Token；
- 在工具真正执行前检查并消耗工具调用额度；
- 计算剩余 Token 和是否进入最终总结阶段。

它不负责决定模型下一步应该做什么，也不识别“重复动作”或“无效进展”。

### 2.2 ConvergenceController

代码位置：`backend/app/agent_base/convergence.py`

`ConvergenceController` 只接收工具批次的结构化结果，判断当前行为是否仍然产生有效进展：

- 同一语义动作反复执行且没有新证据；
- 相同输入和错误码反复失败；
- 连续多个批次没有产生状态变化、变更或验证结果。

它返回三种收敛结果：

| 结果 | 含义 |
|---|---|
| `continue` | 当前仍有有效进展，继续循环 |
| `recover` | 要求模型改变策略、缩小范围或执行验证 |
| `finalize` | 已超过恢复机会，停止探索并基于现有证据总结 |

收敛控制不是固定步数限制。生产任务可以运行任意多轮，只在行为失去生产力时介入。

### 2.3 RunPolicyHook

代码位置：`backend/app/agent_base/core/hooks.py`

`RunPolicyHook` 是策略组件与通用 Hook 协议之间的适配层：

| Hook 事件 | 处理内容 |
|---|---|
| `RUN_START` | 初始化本次运行的预算计量 |
| `LLM_BEFORE` | 检查时间、Token 硬上限，必要时返回 `STOP` |
| `LLM_AFTER` | 记录模型返回的 Token 使用量 |
| `TOOL_BEFORE` | 检查并消耗工具调用额度，必要时返回 `VETO` |
| `TOOL_BATCH_AFTER` | 将本批工具结果交给收敛控制器，产生 `RECOVER` 或 `FINALIZE` |

Hook 返回值使用统一的 `HookDecision`：

```python
HookDecision(
    action="recover",      # continue / replace / veto / recover / finalize / stop
    reason="repeated_action",
    message="Change the strategy and use the existing evidence.",
)
```

`HookRegistry.trigger()` 用于需要短路的控制点，例如工具拒绝或 LLM 停止；`HookRegistry.emit()` 用于工具批次结束等广播事件，确保所有观察者都能收到事件。

### 2.4 ContextBudgetManager

代码位置：`backend/app/services/context_manager.py`

`ContextBudgetManager` 负责控制单次模型请求的上下文规模：

- 根据消息和工具定义估算当前请求的 Token 占用；
- 达到 `compaction_trigger_ratio` 后，将旧工具历史折叠为结构化 checkpoint；
- 以 `max_history_tokens` 作为压缩目标，以 `max_context_tokens` 作为请求硬上限；
- 保持 Function Calling 的 assistant/tool 消息配对，避免生成非法历史；
- 完整过程仍由 Trace 和 Evidence Ledger 保存，不依赖压缩后的上下文恢复审计。

该组件不限制 Agent 的执行轮数，压缩触发只由上下文 Token 使用情况决定。

## 3. 运行流程

```text
Settings
   |
   v
create_dev_agent
   |
   +--> ExecutionBudget.from_settings()
   +--> ConvergenceController(...)
               |
               v
        AgentRuntime (per run)
               |
               v
        generic Hook events
               |
       +-------+--------+
       |                |
  resource policy   progress policy
  ExecutionBudget   ConvergenceController
       |                |
       +-------+--------+
               v
        HookDecision
               |
               v
        ReAct loop action
```

一次工具批次的关键顺序如下：

1. 模型返回工具调用；
2. 每个工具在 `TOOL_BEFORE` 经过权限和资源策略检查；
3. 工具执行结果经过 `TOOL_AFTER`，必要时只替换喂给模型的截断内容；
4. Agent 发布 `TOOL_BATCH_AFTER`；
5. 收敛控制器观察结构化结果；
6. 主循环根据 `HookDecision` 继续、追加恢复提示或进入最终总结。

## 4. Settings 统一管理

生产 DevAgent 的资源和收敛参数集中在 `backend/config/settings.py`：

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `agent_max_tool_calls` | `100` | 单次运行最大工具调用数 |
| `agent_max_run_seconds` | `600` | 单次运行最大时长 |
| `agent_per_run_execution_budget_tokens` | `200000` | 单次 Run 的执行预算上限 |
| `agent_subagent_per_run_execution_budget_tokens` | `500000` | 子代理单次 Run 的独立执行预算上限 |
| `agent_token_finalization_reserve_tokens` | `12000` | 为最终总结保留的 Token 空间 |
| `agent_convergence_budget_ratio` | `0.8` | 触发预算预警和收敛提示的比例 |
| `agent_convergence_max_stalled_rounds` | `3` | 无进展批次的最大容忍次数 |
| `agent_convergence_max_recovery_rounds` | `2` | 恢复动作的最大次数 |
| `agent_convergence_repeat_action_threshold` | `3` | 相同语义动作无新结果的触发次数 |
| `agent_context_compaction_trigger_ratio` | `0.75` | 当前请求上下文占用率达到该比例时触发压缩 |

`create_dev_agent()` 在组装阶段创建 `ExecutionBudget`，因此生产入口不会在主循环中散落资源默认值。评测或测试可以通过显式覆盖值构造隔离预算，但默认仍以 Settings 为准。

## 5. 与其他 step 参数的边界

- `ReActAgent.max_steps` 仅作为旧调用兼容参数保留，不再参与执行终止；
- `force_final_summary_on_step_limit` 仅作为旧调用兼容参数保留，不再触发步数总结；
- `ContextBudgetManager` 根据当前请求的估算 Token 占用率触发上下文压缩；
- `max_history_tokens` 作为压缩目标，`max_context_tokens` 作为请求上下文硬上限；
- 子代理和 Explorer 均使用独立的 Token 上下文管理、执行预算和收敛控制，不再使用步数终止条件。

## 6. 可观测性

策略决策必须可以被解释和复盘：

- `AgentRuntime` 保存本次运行的预算、收敛控制器和最近控制信号；
- `last_context_report` 记录 Token 使用、预算停止原因、收敛事件和压缩信息；
- `EvidenceLedger` 保存工具结果、状态、错误码、验证结果和副作用；
- Trace 保留完整工具观察，即使喂给模型的内容经过 `TruncateHook` 截断；
- 最终 `RunOutcome` 将预算停止、收敛终止、验证失败和任务完成状态统一输出。

因此，`finalize` 并不等同于“任务成功”，而是表示 Agent 停止继续探索。最终结果仍需结合工具证据、变更集和验证结果判断完成度。

## 7. 扩展约定

新增运行策略时，优先实现为独立 Hook 或策略组件：

1. 使用 `HookContext.payload` 携带结构化事件数据；
2. 使用 `HookDecision` 表达控制意图；
3. 通过 `AgentRuntime` 保存 per-run 状态，避免写入全局可变状态；
4. 只在确实需要短路时使用 `trigger()`，观察型逻辑使用 `emit()`；
5. 将策略原因写入运行报告或 Trace，避免只返回无法解释的字符串。

这样可以继续扩展权限、成本、超时、人工审批和质量门禁，而不增加 ReAct 主循环中的策略分支。
