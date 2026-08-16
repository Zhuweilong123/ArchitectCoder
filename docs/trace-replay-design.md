# Trace 回放机制设计

> 本文归档 UML Designer 的 trace（会话结构化日志）回放机制的设计与实现，
> 作为后续迭代（L3 混合回放、回归测试接入等）的参考基线。

## 1. 背景与目标

Agent 在对话式开发中通过 `BaseAgentsLLM` 自动记录 JSONL trace（记录 LLM 原始往返、工具调用、审核等）。仅靠查看 trace 还不足以支撑以下诉求：

- **离线复现 bug**：上次某轮优化崩了 / 输出了坏 JSON，想无成本地重跑一遍看卡在哪。
- **回归测试**：把一段 trace 固化成断言，验证下游解析/布局逻辑没被改坏。
- **模型/提示词 A/B**：用当时的 prompt 重跑，度量不同模型版本的漂移。

因此引入「回放」：让 agent 循环重跑一遍，但**替换掉不确定、昂贵、有副作用的组件**（LLM 与工具）。

## 2. 三种回放语义

| 级别 | 语义 | 是否调 LLM | 是否执行工具 | 用途 | 状态 |
|---|---|---|---|---|---|
| **L1 Mock** | 喂回记录的 LLM 返回 + mock 工具结果 | ❌ | ❌（mock） | bug 复现、离线回归、零成本调试 | ✅ 已实现 |
| **L2 Rerun** | 用记录 prompt 真调 LLM，工具仍 mock | ✅ | ❌（mock） | 模型/提示词 A/B、度量漂移 | ✅ 已实现 |
| **L3 混合** | 真调 LLM + 真实工具（可 mock 破坏性工具） | ✅ | ✅（可选） | 迭代推理逻辑但需真实副作用 | ⬜ 未实现 |

## 3. 架构总览

回放在 **LLM 边界**做替换，而非重写 ReAct 循环：

```
ReActAgent 循环
  ├─ llm.ainvoke_with_tools(...)   ──▶  ReplayLLM（L1） 或 真实 BaseAgentsLLM（L2）
  └─ registry.aexecute_tool_with_params(...) ──▶  MockToolRegistry（按序吐记录结果）
```

- LLM 是循环里唯一真正不确定、昂贵、外部的组件；工具结果已记录，mock 掉即可。
- 在边界替换能**一次覆盖所有 agent**（ReActAgent、reflection_agent、uml_optimizer_v2、pipeline），不用每个循环单独写回放逻辑。
- **最小回放原语**：按 monotonic 顺序遍历 `llm_request`，按 `span_id` 配对 `llm_response`，用游标顺序 pop。

## 4. trace 记录格式

- **文件**：`temp/chat_log/trace_{session_id}.jsonl`（与 `chat_{session_id}.md` 同目录）。
- **写入**：`backend/app/services/chat_trace.py` 的 `ChatTraceLogger`；全局 hook 在 `backend/app/agent_base/core/llm.py` 的 `_trace_hook` 注册/路由。
- **事件类型**：

| event_type | 含义 |
|---|---|
| `session_start` / `session_end` | 会话边界（`TraceSession` 路径写入；agent chat 路径补了 `start()`） |
| `user_message` | 用户消息（轮次分隔符） |
| `llm_request` / `llm_response` | LLM 原始往返（prompt/completion/model/tokens/span_path），按 `span_id` 配对 |
| `agent_step` | ReAct 单步 |
| `tool_call` / `tool_result` | 工具调用与返回，按 `span_id` 配对 |
| `review_request` / `review_response` | 人工审核回路 |
| `done` / `error` | 最终答案 / 错误 |

每条事件含 `trace_id / span_id / parent_span_id / ts_ms / monotonic_ns` 因果链。

## 5. 已实现

### 5.1 M1 — TraceViewer（可视化查看/调试）

- **后端** `backend/app/services/trace_reader.py`：`list_traces()` / `read_trace()`（复用 `chat_trace._chat_log_dir`，防路径穿越）。
- **后端** `backend/app/api/trace.py`：`GET /api/trace/list`、`GET /api/trace/{session_id}`。
- **前端** `frontend/src/components/TraceViewer/`：Drawer，左会话列表 + 右时间轴；按 `user_message` 分轮次、按 `span_id` 配对 LLM/工具；支持「自动播放」逐条高亮滚动。
- **入口**：Toolbar「Trace」按钮 → `uiStore.traceVisible`。

### 5.2 M2 — 确定性回放引擎

- **后端** `backend/app/services/replay.py`：
  - `ReplayLLM` — 假 LLM，实现 `ainvoke_with_tools` / `ainvoke`，游标顺序 pop 记录的 `llm_response`。
  - `MockToolRegistry` — 假工具注册表，`aexecute_tool_with_params` 顺序 pop 记录的 `tool_result`；`get_openai_specs()` 返回从 trace 提取的真实 schema。
  - `replay_agent_session(session_id, *, mode="mock")` — 整段会话逐轮重放，逐字对比 `final_answer` 与记录 `done.answer`。
- **端点**：`POST /api/trace/{session_id}/replay?mode=mock|rerun`。
- **前端**：「回放执行」按钮 + `Mock / Rerun(真LLM)` Segmented 切换；结果弹窗展示每轮匹配状态、**逐词 diff**（`diff` 库）与两侧完整答案。

### 5.3 验证

真实 8 轮会话 mock 回放：`all_matched=True`，LLM 17/17、工具 9/9 **逐字匹配**。

## 6. 关键设计点

### 6.1 span_path 过滤（最重要的踩坑）

trace 文件记录的是**整个进程的全部 LLM 调用**，不只 ReAct 步级调用，三类混在一起：

1. **步级 `ainvoke_with_tools`**：`span_path` 为单段（如 `DevAgent`）→ **回放要消费**。
2. **工具内部嵌套 LLM 调用**（`explore_project` 摘要 / `optimize_v2` 两阶段）：`span_path` 含 `/`（如 `DevAgent/optimize_uml/scope_analysis`）→ 工具被 mock，不消费。
3. **`done` 后异步内存归档**（`_archive_task_to_memory`）：`span_path` 空或含 `/` → 不消费。

**修复**：`ReplayLLM` 只消费「单段非空 `span_path`」的 `llm_response`（`_is_step_level`）。
不加此过滤，游标会错位（曾出现 12/40 消费、最终答案整体错位一格）。

### 6.2 游标顺序匹配 vs 内容哈希

用**游标顺序 pop** 而非按请求内容哈希匹配：LLM 非确定，重跑时请求内容可能与记录不完全一致，但**调用顺序稳定**。

### 6.3 rerun 的 tool schema 提取

L2 rerun 下真 LLM 需要真实 tool schema 才能发起工具调用。schema 已记录在 trace 的 `llm_request.tools`，直接从首个步级请求提取（`_step_level_tool_specs`），**无需重建真实工具注册表**——rerun 依然零副作用（工具全 mock）。

### 6.4 上下文口径对齐

「模型实际收到的完整上下文」以 **faithful 口径**为准：

```
llm_request.messages（工具返回为 [:2000] 截断版）+ tools + tool_choice
  + temperature / max_tokens / response_format / timeout
```

工具返回的完整版单独存于 `tool_result.observation`，二者通过显式字段区分：

- `llm_request` 补 `response_format` / `timeout` 字段；
- `tool_result` 补 `fed_truncated`（bool）/ `fed_length`（int），标记喂回模型前是否被截断（截断长度常量 `OBSERVATION_FEED_LIMIT = 2000`，位于 `react_agent.py`）。

Viewer 据此展示：LLM 卡头部参数徽标 + 「工具 schema」折叠项；工具卡在截断时标注「模型仅收到前 N 字」。

## 7. 已知边界与后续

- **L3 混合回放**：需要重建真实工具注册表（`agent_chat_ws._create_dev_agent`），并对破坏性工具做可选 mock，是更大改动。
- **tool 游标错位边界**：原始运行中 tool_call 参数非法 JSON 时，ReAct 循环跳过工具但仍写 `tool_result`，回放 tool 游标会错位（罕见）。
- **同步流式路径无 trace**：`think()` / `stream_invoke()`（`llm.py:347`）未打 trace。
- **optimize_v2 独立 trace 分文件**：无 `user_message` 事件，回放按单轮空消息兜底。

## 8. 文件索引

| 文件 | 职责 |
|---|---|
| `backend/app/services/chat_trace.py` | trace 记录（ChatTraceLogger / TraceSession / trace_span / 全局 hook） |
| `backend/app/agent_base/core/llm.py` | BaseAgentsLLM + `_trace_hook` 转发 |
| `backend/app/services/trace_reader.py` | 读取解析 JSONL（list / read） |
| `backend/app/services/replay.py` | 回放引擎（ReplayLLM / MockToolRegistry / replay_agent_session） |
| `backend/app/api/trace.py` | `/api/trace/*` 端点 |
| `backend/app/services/agent_chat_ws.py` | agent 对话 WS，记录 tool_call/result/done，补 `start()` |
| `frontend/src/components/TraceViewer/` | 前端查看/回放 UI |
