# Trace 回放机制设计

> 本文归档 ArchitectCoder 的 trace（会话结构化日志）回放机制的设计与实现，
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
| **L3 Live** | 真调 LLM + 只读工具真实、其余 mock（`tool_policy=full` 可全真） | ✅ | ✅（部分真实） | 度量「真实执行」下的漂移、对当前代码库 A/B | ✅ 已实现 |

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

- **文件**：`temp/chat_log/trace_{session_id}.jsonl`。
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
  - `MockToolRegistry` — 假工具注册表，`aexecute_tool_with_params` 顺序 pop 记录的 `tool_result`；`get_openai_specs()` 返回从 trace 提取的真实 schema；`graceful`（rerun 专用）耗尽时返回占位而非抛错。
  - `replay_agent_session(session_id, *, mode="mock", until_turn=None, tool_policy="readonly")` — 整段会话逐轮重放，逐字对比 `final_answer` 与记录 `done.answer`；用 `arun_stream` 采集每轮步级明细（`steps`），并从 trace 还原原始侧（`recorded_steps`）。
- **端点**：`POST /api/trace/{session_id}/replay?mode=mock|rerun|live&turn=N&tool_policy=readonly|full`（`turn` 为单步执行的累计轮次，1-based；`tool_policy` 仅 live 生效）。
- **前端**：「回放执行」按钮 + `Mock / Rerun(真LLM) / Live(真工具)` Segmented 切换；结果弹窗展示每轮匹配状态、**逐词 diff**（`diff` 库）、步级时间线；每轮「单步执行」只累计跑到第 N 轮，「执行全部」跑完所有轮。

### 5.3 单步执行 + 步级明细 + 左右对比

- **单步执行（累积语义）**：`replay_agent_session(until_turn=N)` 截断到第 N 轮，但复用**同一个 agent 实例逐轮 `arun`**，历史自然累积到第 N 轮——等价于全量重放的前 N 轮前缀（mock 下逐字一致；rerun 下省后续 token）。前端每轮「单步执行」按钮触发 `turn=N`。
- **步级明细 `steps`**：回放用 `agent.arun_stream`（而非 `arun`）采集每步 `ReActProgress`，每轮返回 `[{step, thought, actions, tool_calls, is_final}]`，`tool_calls` 每项 `{name, arguments, observation}`。mock 下与记录逐字一致；rerun 下即真 LLM 实际轨迹（含发散）。
- **原始侧 `recorded_steps`**：`_split_turn_events`（按 `user_message` 切轮）+ `_extract_recorded_steps`（`agent_step` 定步序，`tool_call`/`tool_result` 按 `span_id` 关联补齐参数与观察）从 trace 还原原始运行侧，与 `steps` 同构。
- **左右对比（仅 rerun）**：rerun 下前端渲染双列 `Timeline`（左=原始工具调用、右=回放工具调用）；mock 下两列逐字相同，故保持单列，避免冗余。

### 5.4 验证

真实 8 轮会话 mock 回放：`all_matched=True`，LLM 17/17、工具 9/9 **逐字匹配**。

被回放污染的旧 trace 在加入 `span_path != "replay"` 防御过滤后（见 6.5），mock 回放恢复 `6/6/6` 对齐、`all_matched=True`。

### 5.5 L3 — live 混合回放（真 LLM + 只读工具真实执行）

- **语义**：真调 LLM，`read_file`/`glob` **真实执行**（读到当前项目真实状态），其余工具（`write_file`/`edit_file`/`bash`/子代理/`submit_uml_review`）按记录 mock。
- **后端** `replay.py`：
  - `HybridToolRegistry` — `get_openai_specs()` 返回完整记录 schema（LLM 决策空间与原运行一致），`aexecute_tool_with_params` 按 `real_policy` 决定真实执行或 mock；mock 侧**按工具名分队列** pop，真实工具不消费队列，交错调用不错位。
  - `_build_live_registry(events, source_dir, test_dir, design_dir, tool_policy)` — 构建只装真实工具（read_file/glob；`full` 加 write/edit/bash）的 `ToolRegistry`；bash 不传 review_manager → 敏感命令 fail-closed、高危直接拒。
  - `_reconstruct_workspace(events)` — 还原 `source_dir/test_dir/design_dir/project_file`（优先 `user_message` 记录，旧 trace 回退从 context 文本解析）。
- **策略**：`tool_policy=readonly`（默认，安全）/ `full`（write/edit/bash 也真实，写盘风险自负，仅 API 逃生口）。
- **前端**：Segmented 新增 `Live(真工具)`；`steps` vs `recorded_steps` 左右对比在 live 下最有意义（真实读当前项目 vs 原始读当时项目）。

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

### 6.5 回放污染与隔离（踩坑）

rerun 模式真调 LLM，若全局 trace 钩子仍指向某会话，回放自身的 LLM 调用（`span_path="replay"`）会被写进**同一个** trace 文件，污染后续回放的游标对齐（曾出现「14 条步级响应 vs 6 条 tool_result」错位，触发「已耗尽」）。

**双层修复**：

1. **隔离**：`replay_agent_session` 在回放期间 `push_trace_hook(_suppress_trace_hook)` 压入 no-op 钩子，`finally` 中 `pop` 恢复——rerun 的 LLM 调用不再写入任何 trace。
2. **防御过滤**：`_is_step_level` 额外排除 `span_path == "replay"`，兼容已被污染的旧文件。

### 6.6 rerun 原始上下文重建

rerun 真调 LLM 需要与原始运行一致的初始上下文，否则轨迹大幅漂移（原始跑 `glob/bash`，rerun 却去 `read_file`×7）。trace 已把上下文记录在步级 `llm_request` 里：

- `system_prompt`：`_split_system_prompt` 把 system 消息拆成独立字段记录，`_reconstruct_original_context` 直接取首个步级请求。
- `context`（workspace/记忆/日期）：原运行把它拼在首个 user 消息开头（`context + "\n\n" + 输入`），还原时从首个 user 内容剥掉原始输入得到。

二者分别注入 `ReActAgent(system_prompt=...)` 与 `arun_stream(context=...)`。工具 schema 已由 6.3 的 `_step_level_tool_specs` 覆盖，补齐后 rerun 输入基本等价于原始运行。

### 6.7 rerun 工具发散与优雅降级

rerun = 真 LLM + mock 工具，真 LLM 可能偏离原始轨迹（多调工具 / 调不同工具）。`MockToolRegistry(graceful=True)`（仅 rerun）在 tool_result 耗尽时返回占位观察而非抛 `ReplayExhausted`，让回放继续产出最终答案（用于漂移对比）。mock 模式仍抛错，以暴露游标对齐 bug。

### 6.8 单步执行的累积语义

「单步执行第 N 轮」必须是**累积**而非孤立：复用同一 agent 实例逐轮 `arun`，历史（结论级 user/assistant）自然累积到第 N 轮，等价于全量重放的前 N 轮前缀。`until_turn` 截断时 `all_matched` 只校验已执行轮次的 `matches`（游标天然消费不完，不要求全量消费）。

### 6.9 步级明细与左右对比的数据流

回放侧 `steps` 由 `arun_stream` 采集（`ReActProgress.tool_calls_detail` 已含 name/arguments/observation）；原始侧 `recorded_steps` 由 `_extract_recorded_steps` 从 `agent_step`+`tool_call`+`tool_result` 还原。二者同构，前端 rerun 下左（原始）右（回放）双列 `Timeline` 对比，一眼看出漂移；mock 下二者逐字一致，保持单列。

### 6.10 live 混合回放的关键约束

- **解耦「可见」与「执行」**：`get_openai_specs()` 必须返回**完整**记录 schema，而非只暴露真实执行的只读工具。若只暴露只读工具，LLM 轨迹会因「工具可见性变化」而漂移，混淆「真实执行 vs mock」这一变量，A/B 就测不准。
- **按工具名分队列 mock**：全局游标在「只读真实 + 破坏性 mock」交错执行下会错位（真实工具不消费游标）。改为每工具一个队列，mock 工具从自己的队列 pop，真实工具不碰队列。
- **workspace 重建**：真实工具依赖 `source_dir/test_dir/design_dir`（`safe_path` 守卫）。trace 的 `user_message` 事件现携带 `source_dir/test_dir`（向前记录），旧 trace 回退从 context 文本 `## Workspace ...` 行解析。
- **审核 fail-closed**：离线回放无人类可批准。`submit_uml_review` 不注册；`bash`（full 模式）不传 review_manager → 敏感命令「无审核通道」拒绝、高危命令直接拒。真实只读工具永不触发审核。

## 7. 已知边界与后续

- **tool 游标错位边界**：原始运行中 tool_call 参数非法 JSON 时，ReAct 循环跳过工具但仍写 `tool_result`，回放 tool 游标会错位（罕见）。
- **rerun 上下文还原为第 1 轮快照**：`context` 从首个步级 `llm_request` 还原（静态 workspace + 初始记忆/日期），轮间记忆漂移不还原。
- **rerun/live 发散时右列含占位观察**：偏离原始轨迹的额外工具调用无法 mock 真实结果，`steps` 会带占位文本（符合 6.7 设计，用于漂移对比）。
- **左右对比仅非 mock 有意义**：mock 下 `steps` 与 `recorded_steps` 逐字一致，前端保持单列。
- **live 真实工具需新 trace 目录字段**：`user_message` 现带 `source_dir/test_dir`（向前记录）；旧 trace 回退从 context 文本解析，路径含空格/换行时可能解析失败 → 真实工具退化为「无 workspace root」错误。
- **full 模式有副作用**：`write_file`/`edit_file`/`bash` 真实执行会写盘/跑命令，属显式风险；默认 `readonly` 不触发。
- **同步流式路径无 trace**：`think()` / `stream_invoke()`（`llm.py:347`）未打 trace。
- **optimize_v2 独立 trace 分文件**：无 `user_message` 事件，回放按单轮空消息兜底。

## 8. 文件索引

| 文件 | 职责 |
|---|---|
| `backend/app/services/chat_trace.py` | trace 记录（ChatTraceLogger / TraceSession / trace_span / 全局 hook；user_message 带 source_dir/test_dir） |
| `backend/app/agent_base/core/llm.py` | BaseAgentsLLM + `_trace_hook` 转发 |
| `backend/app/services/trace_reader.py` | 读取解析 JSONL（list / read） |
| `backend/app/services/replay.py` | 回放引擎（ReplayLLM / MockToolRegistry / HybridToolRegistry / replay_agent_session / 上下文与 workspace 重建 / 原始侧还原 / 污染隔离） |
| `backend/app/api/trace.py` | `/api/trace/*` 端点 |
| `backend/app/services/agent_chat_ws.py` | agent 对话 WS，记录 tool_call/result/done，补 `start()` |
| `frontend/src/components/TraceViewer/` | 前端查看/回放 UI |
