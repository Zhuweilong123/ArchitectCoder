# Agent 运行收敛与资源预算

> 状态：当前实现说明
> 更新日期：2026-09-11
> 适用范围：`backend/app/agent_base/core/policy.py`、`backend/app/agent_base/convergence.py`、`core/hooks.py` 和 `agents/react_runtime/fc_loop.py`。

## 1. 总体边界

Agent 使用开放式 ReAct 循环，不以固定步数作为正常终止条件。运行边界由三类
策略共同维护：

1. `ExecutionBudget`：限制工具调用、运行时长和单次 LLM 请求 token。
2. `ContextBudgetManager`：限制单次请求携带的消息、历史、工具 schema 和摘要。
3. `ConvergenceController`：识别重复动作、重复失败和无进展批次，要求恢复或最终化。

Trace、Evidence Ledger 和 `last_context_report` 记录这些策略的决定；最终化只表示
停止继续探索，不等于任务成功。

## 2. `ExecutionBudget`

位置：`backend/app/agent_base/core/policy.py`。

`ExecutionBudget` 的 token 限制是**单次 LLM 请求**口径，任务累计 token 仅用于观测：

- `max_tool_calls`：一次 run 的工具调用上限。
- `max_run_seconds`：一次 run 的时间上限。
- `max_total_tokens`：当前请求软目标，来自 `agent_context_soft_limit_tokens`。
- `emergency_max_total_tokens`：当前请求紧急上限，来自 `agent_context_hard_limit_tokens`。
- `token_finalization_reserve_tokens`：为最终答案保留的空间。

`from_settings()` 使用 `Settings` 统一构造预算；每次 run 的 `start()` 会重置工具和
请求计数。`before_llm()` 检查时间，`before_tool()` 检查并消耗工具额度，
`record_tokens()` 记录最近一次请求 token 和累计观测值。

预算达到软目标不会直接强制无工具回答；FC loop 会把上下文软阈值和收敛状态作为
提示/控制信号。只有收敛控制器进入 finalization 时，循环才切换到工具为空的最终回答阶段。

## 3. `ConvergenceController`

位置：`backend/app/agent_base/convergence.py`（不是 `core/convergence.py`）。

控制器按工具批次观察结构化 detail。它为动作、失败和结果分别生成稳定 fingerprint：

- 动作键：工具名 + 参数。
- 失败键：工具名、参数、状态和 error code。
- 结果键：状态、error code、观察、变更和验证结果。

有意义的成功变更、验证结果或新观察会清零 stalled/recovery 计数。否则根据当前计数
返回：

| action | 含义 |
|---|---|
| `continue` | 有新证据或状态变化，继续主循环 |
| `recover` | 重复失败/动作或无进展，要求模型改变策略、缩小范围或验证 |
| `finalize` | 超过恢复机会，停止工具探索并基于已有证据总结 |

默认构造参数：`max_stalled_rounds=3`、`max_recovery_rounds=2`、
`repeat_action_threshold=3`。控制器没有全局步数上限；正常有进展的任务可以继续运行。

## 4. Hook 接入

位置：`backend/app/agent_base/core/hooks.py`。

`AgentRuntime` 通过 contextvar 保存每次 run 的 `execution_budget`、
`convergence_controller` 和当前 `HookDecision`，避免策略状态写入全局可变单例。

关键事件：

| 事件 | 处理 |
|---|---|
| `RUN_START` | 初始化运行时预算和控制器 |
| `LLM_BEFORE` | 检查时间/请求边界 |
| `LLM_AFTER` | 记录响应 token |
| `TOOL_BEFORE` | 检查工具额度和权限，必要时 veto |
| `TOOL_AFTER` | 规范化工具结果或替换喂给模型的内容 |
| `TOOL_BATCH_AFTER` | 将整批 evidence detail 交给 `ConvergenceController.observe()` |

`trigger()` 用于 veto/replace 等短路控制；`emit()` 用于必须通知所有观察者的广播事件。
Hook 异常默认非致命；标记 `fail_closed` 的 hook 异常会转为 veto。

## 5. FC loop 的实际顺序

`backend/app/agent_base/agents/react_runtime/fc_loop.py` 每次 run 的主要阶段：

```text
构造 ExecutionBudget + ConvergenceController
        ↓
RUN_START
        ↓
ContextBudgetManager 估算/压缩请求
        ↓
LLM_BEFORE → LLM 调用 → LLM_AFTER
        ↓
TOOL_BEFORE → 工具执行 → TOOL_AFTER
        ↓
记录 EvidenceLedger
        ↓
TOOL_BATCH_AFTER → ConvergenceController.observe()
        ↓
continue / recover（追加策略提示）/ finalize（工具列表置空）
```

工具输出可以被 `TruncateHook` 截断后再喂给模型，但 Trace/Evidence 保留完整观察。
收敛事件、软阈值原因、最终化原因和计数都会写入 `last_context_report`。

## 6. 当前 Settings

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `agent_max_tool_calls` | 100 | 单次 run 工具调用上限 |
| `agent_max_run_seconds` | 600 | 单次 run 时间上限 |
| `agent_context_hard_limit_tokens` | 256000 | 请求紧急 token 上限 |
| `agent_context_soft_threshold_ratio` | 0.78125 | 请求软目标比例 |
| `agent_context_compaction_threshold_ratio` | 0.9 | 上下文压缩触发比例 |
| `agent_token_finalization_reserve_tokens` | 12000 | 最终答案预留空间 |
| `agent_convergence_max_stalled_rounds` | 3 | 无进展批次容忍次数 |
| `agent_convergence_max_recovery_rounds` | 2 | 恢复次数上限 |
| `agent_convergence_repeat_action_threshold` | 3 | 相同动作无新结果触发次数 |
| `agent_evidence_max_records` | 128 | Evidence Ledger 记录上限 |

`agent_subagent_per_run_execution_budget_tokens=500000` 只用于主 Agent 管理的子代理，
与主 Agent 的 `ExecutionBudget` 分开。上下文语义压缩的模型和触发参数属于
`agent_session_compression_*`，详见 `context-management-design.md`。

## 7. 失败与可观测性

- 时间到达时返回 `time_limit`；工具额度耗尽时返回 `tool_call_limit`。
- 重复失败先进入 recover，超过恢复机会后 finalize，并在最终答案中要求明确报告 blocker。
- 无进展连续达到 `max_stalled_rounds` 后 finalize。
- `last_context_report` 保存 token budget、convergence events、stalled/recovery 计数和最终化原因。
- `EvidenceLedger` 保存工具状态、错误码、变更和验证结果；Trace 保存完整工具观察。

## 8. 维护规则

新增运行治理应实现为独立 policy/controller/hook，通过结构化 `HookContext.payload`
传递证据，通过 `HookDecision` 表达控制意图。不要把重复检测、预算默认值或传输协议
分支重新塞回 ReAct 主循环；变更 Settings 后同步本文件和上下文管理文档。
