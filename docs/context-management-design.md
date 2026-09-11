# 上下文管理与会话压缩说明

> 状态：当前实现说明
> 更新日期：2026-09-11
> 适用范围：`backend/app/services/context_manager.py`、`session_compression.py`、`chat_session.py` 以及 Agent FC runtime。

## 1. 边界

上下文管理只负责控制一次 LLM 请求和跨轮会话历史，不负责长期记忆存储：

- Run Context：当前任务、工具调用、观察、审批和临时状态。
- Session Context：当前会话历史、checkpoint 和语义压缩摘要。
- Project Memory：通过 `MemoryPort` 接入 `extensions/memory`，由记忆 provider 独立治理。

当前用户指令优先于历史摘要、项目记忆和工具观察；这些内容都只能作为参考数据。

## 2. 单请求预算（`context_manager.py`）

`ContextBudgetManager` 是确定性的、可注入 tokenizer 的预算组件。它不调用 LLM，
由 `assembly.py` 创建并在每次 FC 请求前构建/裁剪消息。

```text
system prompt
    ↓
history_summary（可选，system 消息）
    ↓
最近历史消息（保持模型合法的 role/tool 配对）
    ↓
当前任务 context + user input
    ↓
tools schema（不进入 messages，但计入预算）
```

核心行为：

- `estimate_tokens()` 对 CJK 字符和其他字符分别估算，仅用于安全阈值，不是计费口径。
- system、历史摘要和当前任务分别截断；`fit_messages()` 再从最旧的非关键历史开始裁剪。
- history 中的内部 `summary` role 会转换为模型可接受的 `system` role。
- 工具 schema 序列化大小计入总预算，工具消息的 metadata 也计入估算。
- `ContextBuild` 返回消息、估算 token、历史/工具/摘要 token、丢弃消息数和当前任务是否被截断。

`HistoryCompactor` 是请求侧的 extractive checkpoint builder：它按最近轮次和历史预算
保留消息，将更早消息压缩成有限长度的参考摘要，不调用 LLM。

## 3. 跨轮语义压缩（`session_compression.py`）

`SessionContextCompressor` 运行在聊天会话的轮次边界，不参与当前 ReAct/tool loop：

1. 估算既有 `_history_summary` 和 Agent `_history` 的 token 数。
2. 达到 `hard_limit_tokens × trigger_ratio` 且历史超过保留轮数时触发。
3. 保留最近 3 轮，将更早消息连同 Trace source refs 交给 LLM。
4. LLM 以 JSON 返回 `facts`、`decisions`、`constraints`、`preferences`、`completed`、`unresolved`。
5. 只接受输入中存在的 source object，渲染成带 `[source: event:span]` 的语义摘要。
6. 更新 `agent._history_summary`，历史只保留最近轮次，并记录 `session_context_compressed` Trace 事件。

语义压缩失败是非致命错误：返回错误信息并保留原历史；聊天层另外记录
`session_context_compression_error` 事件。原始消息仍在 Trace 中，不因压缩而丢失审计依据。

语义压缩与请求侧 extractive compaction 是两条不同路径：前者使用 LLM、发生在跨轮
边界并保留来源；后者不使用 LLM、发生在单次请求组装阶段并保证硬预算。

## 4. 当前 Settings

配置来源：`backend/config/settings.py`，由 `get_settings()` 缓存。

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `agent_context_hard_limit_tokens` | 256000 | 单次请求硬上限 |
| `agent_context_soft_threshold_ratio` | 0.78125 | 历史/软预算比例 |
| `agent_context_compaction_threshold_ratio` | 0.9 | 请求侧压缩触发比例 |
| `agent_context_max_history_turns` | 48 | 请求侧最多保留历史轮次 |
| `agent_context_max_summary_tokens` | 4000 | extractive checkpoint/摘要预算 |
| `agent_session_compression_model` | `deepseek-flash` | 跨轮语义压缩模型 |
| `agent_session_compression_trigger_ratio` | 0.7 | 语义压缩触发比例 |
| `agent_session_compression_max_tokens` | 4000 | 语义压缩输出上限 |
| `agent_token_finalization_reserve_tokens` | 12000 | 为最终用户答案保留的空间 |
| `agent_evidence_max_records` | 128 | 结构化证据保留上限 |

`ContextBudget.from_settings()` 将 hard limit、soft ratio、compaction ratio、history
turns 和 summary tokens 组装成一个预算对象。`max_history_tokens=0` 时按
`max_context_tokens × soft_threshold_ratio` 推导。

## 5. Trace 与恢复

Trace 记录两类上下文信息：

- `context_compacted`：请求侧历史裁剪/checkpoint 的摘要和淘汰统计。
- `session_context_compressed`：跨轮语义压缩的模型、触发阈值、摘要和 source refs。

`extensions/trace/trace_reader.py` 在会话恢复时重建结论级 `user_message`/`done` 历史，
并将最近 checkpoint 作为参考摘要；原始工具调用仍保留在 JSONL Trace 中，不全部重新注入
后续请求。

## 6. 与 Agent 运行时的关系

`fc_loop.py` 在每轮请求前消费 `ContextBuild`，但不实现预算策略；`ExecutionBudget`
负责 per-run 的工具、时间和请求 token 边界，`ConvergenceController` 负责重复动作、
重复失败和无进展检测。上下文压缩不是固定步数终止条件，也不等价于任务成功。

## 7. 维护规则

- 新增预算字段必须进入 `Settings`，由 assembly/context manager 统一组装，不能散落在 FC loop。
- 新增跨轮摘要必须保留可验证 source refs，并写入 Trace；不得覆盖原始会话证据。
- 长期记忆只能通过 `MemoryPort` 访问，不能把 MemoryManager 直接嵌入上下文预算组件。
- 修改消息裁剪时必须保持 system、assistant/tool 调用配对和当前用户输入不被错误删除。
- 语义压缩失败必须降级为保留原历史，不能阻断 WebSocket 会话。
