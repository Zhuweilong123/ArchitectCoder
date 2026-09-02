# 上下文管理设计

> 本文归档 ArchitectCoder 当前 DevAgent 的上下文预算、会话压缩和恢复机制，
> 对应 `backend/app/services/context_manager.py` 与 `backend/app/agent_base/agents/react_agent.py`。

## 1. 设计目标

上下文管理负责控制一次 LLM 请求实际携带的信息，不负责长期知识存储。它需要：

- 在固定上下文窗口内为系统提示词、工具 schema、历史、当前任务和输出预留分配预算；
- 长会话中保留最近可执行对话，并把旧消息压缩为可恢复的 checkpoint；
- 工具循环增长时优先删除最旧的非关键消息，保留当前任务；
- 不引入额外 LLM 调用，避免上下文保护本身阻塞或产生新的不确定性。

## 2. 三层上下文边界

```text
Run Context       单次任务：当前目标、工具调用、观察结果、审批和临时状态
Session Context   当前会话：最近对话 + 历史 checkpoint
Project Memory    跨任务长期记忆：偏好、决策、约定、拒绝和洞察
```

Run Context 只在当前 Agent 循环中累积；Session Context 由 Agent history 和 Trace
checkpoint 管理；Project Memory 由独立的 `memory_system` 管理，不能绕过记忆写入治理直接
进入会话历史。

## 3. 请求构建

`ContextBudgetManager.build_messages()` 按以下顺序组装 FC 请求：

```text
system prompt
history checkpoint（可选）
最近会话消息
当前任务（workspace / 项目上下文 / 记忆 / 当前用户输入）
```

工具 schema 不放入 `messages`，但会计入上下文预算。预算默认值如下：

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `agent_context_max_tokens` | 32768 | 单次请求上下文上限 |
| `agent_context_output_reserve_tokens` | 4096 | 为模型输出预留的空间 |
| `agent_context_max_history_tokens` | 12000 | 历史消息上限 |
| `agent_context_max_history_turns` | 12 | 最多保留的历史轮次 |

默认使用轻量估算器作为安全阈值，不作为计费口径；后续可以注入模型专用 tokenizer，
不改变 Agent 接口。

## 4. 历史压缩

`HistoryCompactor` 是确定性的 extractive checkpoint builder：

1. 保留最近 `max_history_turns` 轮；
2. 在历史 token 预算内继续从后向前保留消息；
3. 被淘汰的消息生成有限长度摘要；
4. 摘要以 `system` 消息形式注入，并明确标注为参考信息而非新指令。

当前摘要不调用 LLM，因此内容可能不如语义摘要完整。它的职责是防止上下文无限增长和
支持恢复，不替代后续的任务状态摘要。

## 5. 工具循环裁剪

每次调用 LLM 前都会再次执行 `fit_messages()`：

- 先删除最旧的非 system、非当前任务消息；
- 保留当前用户任务和最新工具观察；
- 仍超预算时裁剪当前任务内容；
- 工具 schema 按实际序列化大小计入预算。

裁剪结果记录在 `last_context_report`，便于后续 Trace/评测观测。

## 6. 恢复与 Trace

当 Agent 进程内会话被重新创建时，`trace_reader.reconstruct_history()` 会恢复：

- `user_message` / `done` 组成的结论级历史；
- 最近的 `context_compacted` 事件及其 checkpoint。

`ChatTraceLogger.context_compacted()` 记录摘要、淘汰消息数和估算淘汰 token 数。工具调用
明细仍保留在原始 Trace 中，但不会全部重新注入跨轮会话历史。

## 7. 设计约束

- 上下文预算服务与 MemoryManager 解耦；
- 当前用户指令优先于历史摘要和项目记忆；
- 历史摘要、项目记忆和工具观察都属于参考数据，不自动升级为系统指令；
- 不在第一阶段引入向量数据库、独立上下文服务或复杂状态图；
- 预算和裁剪必须有单元测试，不能依赖真实模型调用。

## 8. 后续演进

P1 计划包括结构化任务状态 checkpoint、上下文分区 token 观测、压缩原因 Trace 和基于
真实任务集的上下文质量评测。语义摘要和模型专用 tokenizer 只有在评测证明轻量方案不足时
再接入。

