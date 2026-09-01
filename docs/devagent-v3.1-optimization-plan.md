# DevAgent 3.1 优化实施方案

> 文档版本：3.1-draft
> 编写日期：2026-09-01
> 代码基线：`2300ca6b8979a27a23d063cd6629e85e58801fb9`
> Trace 基线：`temp/chat_log/trace_20260901_181159_uq2m.jsonl`
> 适用范围：DevAgent WebSocket 会话、ReAct 执行循环、工具路由、上下文管理、长期记忆和模型路由

## 1. 背景与结论

DevAgent 3.1 的目标不是单纯缩短 system prompt，而是在不降低任务成功率、修改正确性和安全性的前提下，系统性降低无效 LLM 调用、工具失败、重复上下文和端到端延迟。

最新 trace 表明，当前成本的主要来源并不是静态 system prompt，而是执行过程中不断累积的 assistant tool-call 历史与 tool result：

| 指标 | 当前基线 |
|---|---:|
| 主 Agent LLM 调用 | 164 |
| 工具调用 | 214 |
| 主 Agent prompt tokens | 1,543,206 |
| 主 Agent completion tokens | 76,534 |
| Prompt cache 命中率 | 94.9% |
| LLM 累计耗时 | 661.6 秒 |
| 单次最大 prompt | 23,410 tokens |
| 失败或被策略阻止的工具结果 | 62 |
| 用户主动中止任务 | 1 |

按逻辑上下文构成估算：

| 上下文类型 | 占比 |
|---|---:|
| 静态 system prompt | 6.1% |
| 工具 schema | 16.8% |
| 用户输入与动态上下文 | 9.6% |
| assistant tool-call 历史 | 23.7% |
| tool result | 43.8% |

assistant tool-call 历史和 tool result 合计约占 67.5%。因此，即使将静态 system prompt 压缩一半，理论上也只能减少约 3% 的逻辑 prompt token；考虑 94.9% 的缓存命中率，实际成本收益还会更低。反之，如果过早删除行为约束，可能增加误操作、漏验证和任务失败。

3.1 采用以下总体策略：

1. 先修运行时闭环，消除预算失效、工具契约错位和重复失败。
2. 再减少动态注入，按意图加载工具、计划、技能、记忆和状态。
3. 最后精简静态 prompt，通过固定评测集和 trace 回放证明无质量回退后再默认启用。

## 2. 版本目标

### 2.1 核心目标

- 保持或提升任务完成率、修改正确率、测试通过率和工具安全性。
- 显著减少无效 LLM 循环、工具策略失败和重复项目探索。
- 建立真正生效的 token 预算与防循环机制。
- 让工具的模型可见契约与运行时真实能力一致。
- 将 prompt 从“全量常驻说明”改为“稳定核心 + 按需能力”。
- 为后续 prompt 优化提供可量化、可回放、可灰度、可回滚的评测体系。

### 2.2 非目标

- 3.1 不重写整个 Agent 框架。
- 不改变现有前端 WebSocket 协议和主要用户交互方式。
- 不以减少必要的代码读取、验证或安全检查换取 token 降低。
- 不在缺少评测证据时删除关键编辑、测试和安全约束。
- 不要求一次性移除全部旧逻辑；允许通过 feature flag 渐进迁移。

### 2.3 质量优先级

发生目标冲突时，按以下顺序取舍：

1. 文件与命令安全。
2. 任务结果正确性。
3. 验证完整性与状态可解释性。
4. 任务完成率。
5. 延迟和调用次数。
6. token 成本。

## 3. 现状问题分解

### 3.1 Token 预算没有实际闭环

`BaseAgentsLLM.ainvoke_with_tools()` 已将 usage 写入 trace，但没有把 usage 返回给 ReAct 循环。`ReactAgent` 因而始终读取到空 usage，`agent_max_total_tokens` 无法累计和限制真实消耗。

最新 trace 中，源码同步任务单轮总 token 已远超配置的 100k 预算，仍继续执行至用户主动停止。这既是成本风险，也是失控循环无法及时终止的根因之一。

### 3.2 模型路由只发生在会话首条消息

WebSocket 会话只在 `llm is None` 时调用 `choose_model(user_message)`。由于首条消息是“你好”，后续包括跨设计与源码同步在内的复杂任务全部沿用首次选择的模型，复杂度路由没有按用户轮次生效。

### 3.3 工具说明与真实执行约束不一致

当前 Bash 工具 schema 只描述为通用 shell 命令，但运行时实际存在较严格的 Windows、工作区和操作符限制，包括禁止多命令操作符、管道、重定向、嵌套 shell、`python -c` 以及部分删除命令等。

模型无法从 schema 得知这些限制，导致重复尝试：

- `grep`、`head` 等当前环境缺失或不适用的命令；
- `cd /d`、Windows 参数被路径策略误判；
- PowerShell、`cmd`、`python -c` 等嵌套执行；
- 管道、重定向、`&&`、`;`；
- 缺少直接删除能力时不断生成临时辅助文件。

62 个失败或阻止结果中，shell 路径、操作符、命令缺失和规划前置阻止占据主要部分。

### 3.4 Todo 前置规则覆盖面过大

当前逻辑基本将所有非纯聊天消息都视为必须先写 Todo。模型又经常在首个 function-call 响应中并行提交 `todo_write` 与业务工具；运行时在执行前统一检查，结果是 Todo 与业务工具虽然同批生成，业务工具仍被阻止。

这使“列组件名”“查询任务状态”“运行测试”等简单任务也产生额外轮次和失败记录。

### 3.5 中文意图和工具路由不完整

“组件”等中文设计语义未稳定命中 UML/知识图谱工具路由。组件列表任务因此只暴露通用文件工具，Agent 通过多次读取和 shell 搜索完成本可由 `find_nodes` 一次完成的查询。

### 3.6 技能、记忆和动态段落注入过量

- 简单名称修改也加载完整 UML 指南及引用文档。
- 长期记忆中混入 Todo 流程、工具限制、临时测试结果、一次性删除决策等非持久信息。
- 相互矛盾的组件删除/新增决策可能同时被召回。
- enabled tools、Todo 规则等信息在 system、动态上下文和工具 schema 中重复出现。
- workspace、日期、项目上下文等短信息在每次调用中重复注入，即使当前任务并不需要。

### 3.7 缺少“上一任务执行状态”的结构化交接

用户询问“上面的任务执行完了吗”时，Agent 没有可直接读取的 run checkpoint，只能重新探索文件和工具状态。该状态查询最终使用了 22 次 LLM 调用和 30 次工具调用。

### 3.8 ReAct 历史按消息累积，缺少任务级压缩

当前上下文保留大量重复读取、同类失败和过时 Todo。一次工具调用产生的 assistant tool-call 与 tool result 没有作为可压缩的原子步骤管理，导致上下文随循环快速膨胀。

## 4. 3.1 目标架构

3.1 将单轮执行调整为以下流水线：

```text
用户消息
  │
  ├─ 轻量意图识别
  │    ├─ 问候/闲聊 → 直接响应，不初始化任务工具链
  │    ├─ 状态查询 → 读取 run checkpoint
  │    └─ 任务请求 → 复杂度、领域、风险分级
  │
  ├─ 按轮次选择模型
  ├─ 按意图选择工具和 schema
  ├─ 按复杂度决定是否需要 Todo
  ├─ 按任务选择技能、记忆和项目上下文
  │
  └─ ReAct 执行
       ├─ usage 实时累计与预算门禁
       ├─ 工具契约预校验
       ├─ 重复失败检测
       ├─ step 级历史压缩
       ├─ 修改后验证
       └─ 更新 run checkpoint
```

Prompt 由四层组成：

```text
L0 稳定核心：角色、边界、基本执行纪律、安全规则
L1 任务策略：任务类型、复杂度、Todo/验证要求、模型选择结果
L2 能力说明：仅当前可用的工具 schema、必要技能摘要
L3 运行状态：用户请求、精简记忆、checkpoint、压缩后的 ReAct 历史
```

## 5. 实施工作包

## 5.1 P0：运行时正确性与安全闭环

### V31-P0-01：修复 usage 返回和 token 预算

**问题**

LLM usage 只记录、不返回，导致 Agent 预算累计失效。

**改造内容**

1. 在 `BaseAgentsLLM.ainvoke_with_tools()` 返回结果中增加标准化 `usage`：

   ```python
   {
       "prompt_tokens": int,
       "completion_tokens": int,
       "total_tokens": int,
       "cached_tokens": int | None,
   }
   ```

2. 统一兼容不同模型供应商 usage 字段，缺失字段时显式标记，不静默当作 0。
3. `ReactAgent` 每次 LLM 返回后立即累计 usage，再决定是否进入下一次循环。
4. 增加两级预算：
   - 软预算：达到 80% 时禁止非必要探索，要求收敛、验证或总结；
   - 硬预算：达到 100% 时停止新的 LLM/工具循环，输出当前状态和未完成项。
5. token 预算停止原因写入 trace 和 run checkpoint。

**涉及文件**

- `backend/app/agent_base/core/llm.py`
- `backend/app/agent_base/agents/react_agent.py`
- 相关 Agent/LLM 单元测试

**验收标准**

- 模拟三次 usage 返回时，累计值与供应商响应完全一致。
- 达到硬预算后不再发起下一次 LLM 调用。
- usage 缺失时产生可观测告警，不错误宣称预算仍充足。
- trace 中能看到每步 usage、累计 usage、预算剩余量和停止原因。

### V31-P0-02：按用户轮次重新路由模型

**问题**

模型被会话首条消息锁定，后续任务复杂度变化无法生效。

**改造内容**

1. 将 `choose_model(user_message)` 从会话初始化移动到每个用户任务轮次。
2. 问候、致谢、简短状态确认走轻量路径，不污染后续任务的模型选择。
3. 模型对象允许复用连接配置，但模型名称和参数按轮次重新解析。
4. 路由输入加入以下结构化特征：
   - 是否跨设计与源码；
   - 是否需要多文件编辑；
   - 是否需要测试/构建；
   - 是否为只读查询；
   - 是否为状态查询；
   - 风险等级和预估步骤数。
5. trace 记录 `route_reason`、`selected_model` 和 `task_complexity`。

**涉及文件**

- `backend/app/services/agent_chat_ws.py`
- `backend/app/services/model_router.py`
- WebSocket 会话与模型路由测试

**验收标准**

- 同一会话中“你好”后发起复杂同步任务，复杂任务能独立选择对应模型。
- 简单查询不会误用高成本模型。
- 模型切换不丢失会话消息、项目状态和 checkpoint。

### V31-P0-03：让工具契约与运行时能力一致

**问题**

模型看到的 Bash 能力大于真实能力，策略失败后只能反复猜测。

**改造内容**

1. 重写 Bash 工具 schema，明确：
   - 当前为 Windows 工作区命令执行；
   - 一次只允许一条命令；
   - 禁止的操作符和嵌套 shell；
   - 允许的可执行程序范围；
   - 工作目录选择方式；
   - 搜索、删除应使用专用工具。
2. 为 Bash 增加结构化 `cwd` 参数，至少支持：
   - `source`
   - `test`
   - `design`
   - 明确的工作区内相对目录
3. 增加 `search_text` 工具：
   - 基于项目现有搜索能力或复用 `GrepFileTool`；
   - 支持目录、glob、大小写和最大结果数；
   - 返回结构化文件、行号和匹配摘要；
   - 对超长结果先摘要再按需展开。
4. 增加 `delete_path` 工具：
   - 仅允许工作区内已解析的明确路径；
   - 默认不递归；递归删除要求显式参数并通过安全检查；
   - 返回删除目标、类型、结果和可恢复性；
   - 设计目录和受保护路径保持额外限制。
5. 修复 Windows `/S`、`/N` 等参数被误识别为绝对路径的问题。
6. 工具策略失败返回机器可读的 `error_code` 和 `next_action`，避免模型从自然语言错误中猜测。

**涉及文件**

- `backend/app/agent_base/tools/my_tools/file_system_tools.py`
- 项目搜索工具相关模块
- 工具注册与 schema 构建模块
- 工具安全策略和单元测试

**验收标准**

- 模型可见 schema 中不存在运行时不支持的行为暗示。
- “搜索文本”“删除指定无关文件”“在测试目录运行 pytest”无需 shell 绕行。
- 基线任务中的 shell 策略失败降至接近 0。
- 路径逃逸、受保护目录删除、未声明递归删除继续被阻止。

### V31-P0-04：修复 Todo 同批调用误阻止

**问题**

模型在同一响应中同时调用 `todo_write` 和业务工具时，业务工具在 Todo 执行前被阻止。

**改造内容**

采用以下优先方案：

1. function calls 按声明顺序执行。
2. 如果同批第一项是合法 `todo_write`，先执行并更新状态，再校验后续工具。
3. Todo 初始化失败时，不执行同批业务工具，并返回单个明确错误。
4. 同时在任务动态约束中说明：需要计划的任务，首次应只提交 Todo；该提示作为模型引导，不作为修复运行时竞态的唯一手段。

**涉及文件**

- `backend/app/agent_base/agents/react_agent.py`
- `backend/app/agent_base/tools/my_tools/todo_tools.py`

**验收标准**

- 合法的同批 `todo_write` + 业务工具不再产生 planning-first 误阻止。
- Todo 写入失败时，后续修改工具不会越过门禁。

### V31-P0-05：增加重复失败熔断和任务级 step 管理

**问题**

相同能力错误、命令错误和路径错误可被连续重复，且每次都扩大上下文。

**改造内容**

1. 将一次 assistant tool-call 与对应 tool result 封装成 `AgentStep`。
2. 对失败建立归一化指纹：`tool + error_code + normalized_args`。
3. 同一指纹连续出现 2 次后，不再执行第 3 次，向模型注入一次结构化 capability fact。
4. 同类策略错误累计达到阈值后，要求切换工具或结束该分支。
5. 成功产生修改后，将修改前的重复探索步骤标记为可压缩，但保留修改依据和验证结果。
6. 熔断不会跳过必要验证，也不会将权限拒绝自动转化为更高权限操作。

**涉及文件**

- `backend/app/agent_base/agents/react_agent.py`
- `backend/app/services/context_manager.py`
- trace step 序列化逻辑

**验收标准**

- 相同工具错误最多实际执行 2 次。
- 熔断后 trace 清楚记录原错误、阻止的重复尝试和替代动作。
- 正常的一次失败后修正参数重试不受影响。

## 5.2 P1：任务路由与动态上下文精简

### V31-P1-01：完善中文意图和工具路由

**问题**

中文 UML 领域词没有完整进入设计意图识别，导致工具暴露不足或错误。

**改造内容**

1. 建立可测试的意图类别，而不是分散的关键字判断：

   | 意图 | 典型表达 | 默认工具组 |
   |---|---|---|
   | `chat` | 你好、谢谢 | 无业务工具 |
   | `status` | 完成了吗、做到哪了 | checkpoint，只读兜底 |
   | `uml_query` | 组件、节点、关系、图中有哪些 | UML/KG 查询 |
   | `uml_value_edit` | 改名、改属性、加前后缀 | UML 查询与值编辑 |
   | `uml_structure_edit` | 新增/删除组件、调整关系 | UML 全工具 + 技能 |
   | `code_query` | 查源码、定位实现 | 文件读取 + 搜索 |
   | `code_edit` | 修改实现、修复问题 | 文件编辑 + 搜索 + 测试 |
   | `cross_artifact_sync` | 按设计同步源码 | UML + 代码 + 计划 + 验证 |
   | `test` | 跑测试、构建验证 | 测试目录 Bash/专用测试工具 |
   | `cleanup` | 删除无关文件 | 查询 + `delete_path` |

2. 中文词表至少覆盖：组件、节点、元素、关系、连接、图、设计、源码、同步、测试、删除、清理、状态。
3. 优先规则由“关键词命中”升级为“显式意图 + 风险分级”，歧义时选择较安全的只读能力。
4. 保留 fail-open，但不再直接暴露全部 14 个工具；先进入只读诊断工具集，明确需要后再扩展。
5. 工具 schema 顺序保持稳定，使用 set 仅做成员判断，不用于最终输出顺序。

**涉及文件**

- `backend/app/services/agent_chat_ws.py`
- 工具路由测试

**验收标准**

- “列出组件名”默认提供 UML 查询工具，可在 1 次查询工具调用内取得结构化结果。
- “跑测试”不加载 UML 工具和 UML 技能。
- 工具 schema 顺序在相同请求间稳定，提升缓存稳定性。

### V31-P1-02：Todo 按复杂度分级

**问题**

所有非聊天请求强制 Todo，简单任务产生不必要轮次。

**改造内容**

将 Todo 策略分为三级：

| 级别 | 条件 | 策略 |
|---|---|---|
| T0 | 问候、状态查询、单次只读查询、单命令测试 | 不要求 Todo |
| T1 | 单文件或单对象、步骤明确、低风险修改 | 可选短计划，不设强制前置门禁 |
| T2 | 多文件、跨 UML/源码、删除、迁移、需多阶段验证 | 强制 Todo 和完成状态更新 |

Todo 只记录用户可感知的任务阶段，不记录每次工具调用。完成后保留最终摘要，历史中间更新可被压缩。

**涉及文件**

- `backend/app/services/agent_chat_ws.py`
- `backend/app/agent_base/agents/react_agent.py`
- `backend/app/agent_base/tools/my_tools/todo_tools.py`

**验收标准**

- 组件列表、状态查询、单次测试不会因 Todo 产生额外 LLM 轮次。
- 跨设计/源码同步和递归清理仍强制计划。
- 强制计划任务结束时不存在未解释的 `in_progress` 项。

### V31-P1-03：引入 run checkpoint 与状态查询快路径

**问题**

上一任务状态无法直接复用，状态询问触发完整项目探索。

**改造内容**

每个用户任务结束、停止、预算终止或异常时写入结构化 checkpoint：

```json
{
  "run_id": "...",
  "status": "completed|partial|failed|stopped|budget_exceeded",
  "request_summary": "...",
  "completed_items": [],
  "pending_items": [],
  "changed_files": [],
  "design_changes": [],
  "verification": [],
  "last_error": null,
  "stop_reason": null,
  "updated_at": "..."
}
```

状态类消息默认只读取 checkpoint 并生成回答。仅当用户要求重新核验，或 checkpoint 明确缺失/过期时，才进行最小只读检查。

**涉及文件**

- `backend/app/services/agent_chat_ws.py`
- 会话状态存储模块
- trace/checkpoint 序列化测试

**验收标准**

- “上面的任务执行完了吗”在 checkpoint 存在时不重新遍历项目。
- 用户停止和 token 预算停止能准确回答已完成、未完成及验证状态。
- checkpoint 不把“已修改”错误表述为“已验证通过”。

### V31-P1-04：技能按任务风险选择性加载

**问题**

简单 UML 查询和值修改加载完整 UML 设计指南，动态上下文过重。

**改造内容**

1. 技能加载按意图分层：
   - `uml_query`：不加载完整技能，只依赖工具 schema；
   - `uml_value_edit`：注入 3.1 内置的最小编辑/同步约束；
   - `uml_structure_edit`、`cross_artifact_sync`：加载 UML 技能；
   - 恢复、迁移等特殊场景再加载对应引用文档。
2. `SKILL.md` 主文件与大型 reference 分开计量和记录。
3. 技能工具结果进入上下文时生成稳定摘要；仅在具体规则需要时保留原文片段。
4. 已加载技能在同一任务内使用引用标识，不重复注入全文。

**涉及文件**

- `backend/app/agent_base/tools/my_tools/skill_loader.py`
- `skills/uml-design-guide/SKILL.md`
- `backend/app/services/agent_chat_ws.py`
- `backend/app/services/context_manager.py`

**验收标准**

- 组件列表不加载 UML 技能。
- 简单名称修改不加载完整 `component_diagram_guide.md`。
- 结构性 UML 修改仍能遵循迁移、同步和验证要求。

### V31-P1-05：长期记忆写入与召回治理

**问题**

长期记忆混入临时执行信息、工具规则和冲突决策，反过来干扰后续任务。

**改造内容**

1. 提取 prompt 从“必须提取 2–3 条”改为“允许 0–3 条；没有持久价值时必须返回空数组”。
2. 仅允许写入：
   - 用户明确的长期偏好；
   - 用户接受的架构决策；
   - 稳定项目约定；
   - 可跨任务复用且已验证的项目事实。
3. 明确禁止写入：
   - Todo 和 Agent 工作流程；
   - 工具限制、策略错误和重试过程；
   - 临时状态、文件列表、单次测试结果；
   - 一次性清理/删除指令；
   - 尚未确认的推断。
4. 召回阶段按 `subject` 去重，冲突时优先最新且置信度高的已确认记忆。
5. 默认 top-k 调整为 2–3，并设置最低相关性阈值。
6. `status`、`test`、`cleanup` 意图默认不召回长期记忆，除非存在明确项目约定。
7. trace 记录记忆的 id、相关性、选择或拒绝原因，避免只记录全文。

**涉及文件**

- `backend/memory_system/manager.py`
- `backend/memory_system/policy.py`
- 记忆提取与召回测试

**验收标准**

- 无持久信息的对话提取结果为 `[]`。
- 相互冲突的同主题决策不会同时注入。
- “跑测试”“清理文件”不会召回无关组件设计决策。
- 记忆精简后，固定评测集任务成功率不下降。

### V31-P1-06：ReAct 上下文分层压缩

**问题**

简单的全局字符截断无法区分仍用于编辑的证据和已经过时的探索结果。

**改造内容**

按语义而不是只按长度压缩：

1. assistant tool-call 与 tool result 必须成对保留或成对摘要。
2. 文件编辑完成前，保留支撑该编辑的精确读取内容。
3. 文件编辑成功后，将旧读取压缩为文件路径、关键符号、已用范围和内容哈希。
4. 对重复目录扫描只保留最新有效结果。
5. 对重复失败只保留首次详细错误和最终 capability fact。
6. Todo 仅保留当前状态和已完成摘要，不保留每次更新全文。
7. 测试结果保留命令、退出码、失败摘要；完整输出按需读取。
8. 压缩前后都维持 tool-call/result 协议合法性。
9. 达到上下文软阈值时优先压缩，达到硬阈值时停止无关探索，而不是截断关键证据。

**涉及文件**

- `backend/app/services/context_manager.py`
- `backend/app/agent_base/core/hooks.py`
- `backend/app/agent_base/agents/react_agent.py`

**验收标准**

- 长任务最大 prompt 控制在目标范围内。
- 压缩后不存在孤立 tool result 或丢失 tool-call id。
- 修改正确率和测试通过率不低于未压缩基线。

## 5.3 P2：Prompt 本体精简

### V31-P2-01：建立 PromptBuilder 分层与版本化

**问题**

稳定行为规则、任务策略、工具列表和项目上下文混在一起，难以度量每段价值，也不利于缓存稳定。

**改造内容**

1. `DevPromptBuilder` 输出具名区段，并记录每段字符数和估算 token：
   - `core`
   - `task_policy`
   - `project_context`
   - `memory`
   - `checkpoint`
   - `skill`
   - `tool_schema`
   - `react_history`
2. 增加 `prompt_version=devagent-3.1`，写入 trace 环境快照。
3. 稳定核心保持固定顺序和内容，动态段落放在其后，以保留 prompt cache 效果。
4. enabled tools 不再以自然语言重复列出；工具 schema 是唯一能力事实来源。
5. workspace/date/project context 改为条件注入：只有任务需要时才加入。
6. Todo 规则只在 T2 任务注入，T0/T1 不出现无关说明。

**涉及文件**

- `backend/app/services/agent_chat_ws.py`
- trace 元数据与 prompt 统计模块

**验收标准**

- trace 可按区段解释每次 prompt 的构成。
- 相同意图和工具组的稳定前缀一致。
- 删除动态重复段落后，能力边界仍由 schema 完整表达。

### V31-P2-02：压缩静态 system prompt

静态 system prompt 只保留跨任务都成立、且不能由代码门禁或工具 schema 替代的规则。目标约为 220–280 个英文等价 token，具体以 tokenizer 统计为准，不以字符数硬裁剪。

建议候选文本：

```text
You are DevAgent, a coding and UML engineering agent operating only inside the
configured workspace. Complete the user's request end to end: inspect relevant
state, make scoped changes when requested, verify results, and report what was
done and what remains.

Use only the tools exposed for the current task and follow each tool's schema.
Do not invent files, tool results, tests, or completion. Prefer direct domain
tools over shell workarounds. Read enough context before editing, preserve
unrelated user changes, and keep operations within the workspace.

For design/code changes, maintain consistency between UML artifacts and source
when the task requires both. Use a task plan only when the injected task policy
requires it. Treat tool errors as capability facts: correct the approach instead
of repeating the same invalid action.

After changes, run the narrowest relevant verification available. Distinguish
modified, verified, partially completed, blocked, and failed states precisely.
If a safety rule, missing authority, or hard budget prevents completion, stop
safely and provide the completed work, remaining work, and exact reason.
```

以下内容从静态 prompt 移出：

- 全量工具名和 enabled tools 重复说明；
- 具体 Bash 限制，迁移到 Bash schema；
- UML 恢复、迁移的详细步骤，迁移到技能；
- Todo 详细格式，迁移到 T2 task policy；
- 当前日期、路径、项目状态，迁移到条件动态上下文；
- 可由运行时代码强制的安全门禁。

**验收方法**

1. 同一代码版本、同一模型、同一温度和同一评测任务集进行 A/B。
2. A 组使用 3.0 prompt，B 组使用候选 3.1 prompt。
3. 每个非确定性用例至少运行 3 次，比较中位数和最差结果。
4. 只有所有硬质量门禁通过，且成本指标改善时，B 组才可设为默认。

**硬门禁**

- 任务成功率不得下降超过 1 个百分点；样本较小时不得减少成功用例数。
- UML/源码一致性检查不得新增失败。
- 测试通过率不得下降。
- 路径逃逸、越权删除和危险 shell 测试必须 100% 阻止。
- 不得增加“声称完成但实际未完成”的错误。

## 5.4 横向基础：可观测性与评测基建

### V31-OBS-01：扩展 trace 指标

每个用户任务至少记录：

```json
{
  "prompt_version": "devagent-3.1",
  "intent": "cross_artifact_sync",
  "complexity": "T2",
  "selected_model": "...",
  "route_reason": "...",
  "allowed_tools": [],
  "loaded_skills": [],
  "recalled_memory_ids": [],
  "prompt_sections": {},
  "llm_calls": 0,
  "tool_calls": 0,
  "tool_failures_by_code": {},
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "cached_tokens": 0,
  "max_prompt_tokens": 0,
  "compaction_count": 0,
  "budget_stop": false,
  "checkpoint_status": "completed"
}
```

要求：

- 失败按稳定 `error_code` 聚合，避免依赖错误文本分析。
- 区分主 Agent 调用与后台记忆提取调用。
- 区分逻辑 token、缓存 token 和实际计费口径。
- 对 prompt 区段记录大小，不默认记录敏感全文。

### V31-OBS-02：建立 trace 回放与版本对比

1. 从最新 trace 提取用户请求、初始项目快照引用、预期行为和关键断言。
2. 对具有写入行为的任务使用隔离副本或可重建 fixture，不直接在同一工作区重复执行。
3. 输出按任务的质量、调用、token、耗时和失败类型对比。
4. 保存 3.0 基线，不用未来代码重算后覆盖原始指标。
5. 3.1 后续优化必须提交版本对比结果，避免只凭单条 trace 判断。

## 6. 分阶段实施顺序

### Phase A：测量与安全闭环

实施项：

- V31-P0-01 usage 与预算闭环
- V31-P0-02 每轮模型路由
- V31-OBS-01 trace 指标
- 建立 3.0 固定评测基线

完成条件：预算可验证生效；每轮模型选择可解释；后续改动能被可靠度量。

### Phase B：工具契约与防循环

实施项：

- V31-P0-03 Bash/schema、`search_text`、`delete_path`、`cwd`
- V31-P0-04 Todo 同批执行修复
- V31-P0-05 重复失败熔断

完成条件：基线任务不再依靠无效 shell 猜测；策略失败显著下降；安全用例全部通过。

### Phase C：路由与动态上下文

实施项：

- V31-P1-01 中文意图和工具路由
- V31-P1-02 Todo 分级
- V31-P1-03 run checkpoint
- V31-P1-04 技能选择性加载
- V31-P1-05 记忆治理
- V31-P1-06 ReAct 上下文压缩

完成条件：简单查询和状态任务进入快路径；复杂任务保留完整规划、修改和验证能力。

### Phase D：静态 Prompt 精简

实施项：

- V31-P2-01 PromptBuilder 分层与版本化
- V31-P2-02 静态 system prompt A/B

完成条件：所有硬质量门禁通过，且至少一个核心效率指标达到发布阈值。

### Phase E：灰度和默认启用

1. 开发环境全量开启。
2. 内部评测流量 10% 灰度。
3. 观察至少一个完整评测周期，扩大到 50%。
4. 无质量回退后默认启用 3.1。
5. 保留 3.0 prompt 和旧上下文策略一个发布周期作为快速回滚路径。

## 7. 评测矩阵与目标值

### 7.1 最新 trace 同类任务目标

以下数值用于 3.1 实施验收，必须同时满足质量门禁，不能仅追求调用数：

| 任务 | 3.0 基线：LLM/工具 | 3.1 目标：LLM/工具 | 关键质量断言 |
|---|---:|---:|---|
| 列出组件名 | 16 / 16 | ≤ 3 / ≤ 2 | 结果完整、名称准确 |
| UML 名称加 `Element` | 19 / 25 | ≤ 10 / ≤ 12 | 仅目标值变化，设计可加载 |
| 按设计同步源码 | 50 / 84，用户中止 | ≤ 20 / ≤ 30 | 修改完整、测试真实通过 |
| 查询上一任务状态 | 22 / 30 | ≤ 2 / ≤ 1 | 状态、剩余项、验证准确 |
| 删除源码无关文件 | 49 / 52 | ≤ 8 / ≤ 10 | 只删目标，受保护内容保留 |
| 运行测试 | 7 / 7 | ≤ 3 / ≤ 3 | 命令真实执行，退出码准确 |

### 7.2 聚合发布目标

在同一 fixture、相同模型配置和相同任务集下：

| 指标 | 3.0 基线 | 3.1 发布目标 |
|---|---:|---:|
| 主 Agent LLM 调用 | 164 | ≤ 60 |
| 工具调用 | 214 | ≤ 90 |
| 主 Agent prompt tokens | 1,543,206 | ≤ 500,000 |
| 失败/阻止工具结果 | 62 | ≤ 5，策略类 ≤ 2 |
| 单次最大 prompt | 23,410 | ≤ 12,000 |
| LLM 累计耗时 | 661.6 秒 | ≤ 300 秒 |
| Prompt cache 命中率 | 94.9% | ≥ 90% |

这些是整套优化完成后的目标，不要求每个中间 Phase 单独达到。若模型切换导致 token 或耗时口径不可直接比较，应同时报告按任务、按模型的标准化结果。

### 7.3 单元测试

- usage 字段标准化与缺失处理。
- 软/硬预算门禁。
- 每轮模型路由与问候快路径。
- 中文意图分类和工具集合。
- Todo T0/T1/T2 判定。
- 同批 Todo 与业务工具执行顺序。
- Bash 参数、cwd、操作符与 Windows 路径策略。
- `search_text` 结果截断和结构化输出。
- `delete_path` 路径边界、递归与保护策略。
- 重复失败指纹和熔断。
- checkpoint 各终止状态。
- 记忆 0 条提取、冲突去重和阈值召回。
- step 配对压缩和协议合法性。

### 7.4 集成测试

- 问候后执行复杂任务，验证模型重新路由。
- UML 查询、值修改、结构修改三类技能加载差异。
- 多文件修改达到软预算后的收敛行为。
- 用户停止后查询任务状态。
- 删除目标中混有受保护路径。
- 测试失败时准确报告，不生成“通过”结论。
- 长任务压缩后继续编辑和验证。

### 7.5 安全回归测试

- 工作区路径逃逸。
- 绝对路径和 `..` 绕过。
- 符号链接/链接点越界。
- 递归删除未显式授权。
- 删除工作区根、源码根、设计根或受保护目录。
- shell 多命令、管道、重定向、嵌套 shell 绕过。
- 工具失败后连续重复或换壳重试。

## 8. Feature Flag 与配置

建议为 3.1 改造提供独立开关：

| 配置 | 默认灰度值 | 作用 |
|---|---:|---|
| `DEVAGENT_PROMPT_VERSION` | `3.0` | 选择静态 prompt 版本 |
| `DEVAGENT_PROMPT_AB` | `false` | 显式记录 3.0/3.1 Prompt 候选大小对比 |
| `DEVAGENT_V31_PER_TURN_ROUTING` | `false` | 每用户轮次重新选模型 |
| `DEVAGENT_V31_TOKEN_BUDGET` | `false` | 新 usage 与预算闭环 |
| `DEVAGENT_V31_TYPED_INTENT` | `false` | 新意图/工具路由 |
| `DEVAGENT_V31_TODO_TIERS` | `false` | T0/T1/T2 Todo 策略 |
| `DEVAGENT_V31_TOOL_CONTRACTS` | `false` | 新 Bash 与专用工具 |
| `DEVAGENT_V31_CHECKPOINT` | `false` | 状态快路径 |
| `DEVAGENT_V31_MEMORY_POLICY` | `false` | 新记忆治理 |
| `DEVAGENT_V31_STEP_COMPACTION` | `false` | 任务级 step 压缩 |

配置要求：

- 开关状态写入 trace，确保结果可复现。
- 新 prompt 不应隐式依赖尚未启用的新运行时能力。
- 默认值按 Phase 推进更新，不在一个提交中一次性翻转全部开关。
- 发布稳定后清理过期兼容分支，避免长期双实现。

## 9. 灰度、回滚与故障处理

### 9.1 灰度观察项

- 任务成功率和人工判定正确率。
- 工具失败类型是否从“策略错误”转为真实业务错误。
- token 预算停止率是否异常升高。
- checkpoint 是否准确，是否出现提前宣称完成。
- 复杂任务是否因上下文压缩丢失关键证据。
- 记忆召回减少后是否出现项目约定遗忘。
- 不同模型路由下的延迟、成本和质量分布。

### 9.2 回滚优先级

出现问题时按模块回滚，而不是整体退回：

1. 静态 prompt 质量回退：切回 `DEVAGENT_PROMPT_VERSION=3.0`。
2. 压缩导致上下文丢失：关闭 `STEP_COMPACTION`。
3. 意图误路由：关闭 `TYPED_INTENT`，保留专用工具。
4. 模型路由异常：关闭 `PER_TURN_ROUTING`。
5. 新工具安全异常：立即关闭对应工具注册，保留旧只读能力。

usage 预算、安全路径检查和危险操作门禁属于正确性修复；除非实现自身存在缺陷，不应为了恢复旧行为而长期关闭。

## 10. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Prompt 过度精简 | 漏规划、漏验证或错误完成 | 静态 prompt 最后改；固定评测 A/B；硬质量门禁 |
| 意图误分类 | 工具不足或暴露过多 | 安全只读 fail-open；可按任务扩展工具；记录路由原因 |
| 压缩丢失编辑依据 | 错误修改或重复读取 | 编辑前保留精确内容；编辑后才摘要；协议测试 |
| Token 预算过紧 | 合法复杂任务提前停止 | 软/硬两级；按复杂度和模型配置预算；checkpoint 可继续 |
| 专用删除工具引入风险 | 误删或越界 | 明确路径、默认非递归、保护目录、安全回归测试 |
| 记忆召回过少 | 忘记稳定约定 | 只降低噪声；保留高置信项目约定；可解释召回日志 |
| 每轮模型切换破坏上下文 | 响应风格或能力不一致 | 模型无关消息格式；集成测试；checkpoint 结构化 |
| Feature flag 组合过多 | 测试矩阵膨胀 | 按 Phase 支持有限组合；稳定后删除旧分支 |

## 11. 代码改造清单

| 模块 | 主要改造 |
|---|---|
| `backend/app/agent_base/core/llm.py` | 返回标准化 usage，供应商字段适配 |
| `backend/app/agent_base/agents/react_agent.py` | 预算、Todo 顺序、失败熔断、step 管理、稳定工具顺序 |
| `backend/app/services/agent_chat_ws.py` | 每轮模型路由、意图分类、Todo 分级、PromptBuilder、checkpoint |
| `backend/app/services/model_router.py` | 结构化复杂度与风险路由 |
| `backend/app/services/context_manager.py` | step 级上下文压缩与区段计量 |
| `backend/app/agent_base/core/hooks.py` | 从固定截断升级为语义压缩接入 |
| `backend/app/agent_base/tools/my_tools/file_system_tools.py` | Bash 契约、cwd、搜索/删除专用能力 |
| `backend/app/agent_base/tools/my_tools/todo_tools.py` | Todo 分级和同批执行兼容 |
| `backend/app/agent_base/tools/my_tools/skill_loader.py` | 技能按需加载与引用摘要 |
| `backend/memory_system/manager.py` | 0–3 提取、持久性白名单、可解释召回 |
| `backend/memory_system/policy.py` | top-k、阈值、去重、冲突规则 |
| `skills/uml-design-guide/` | 主规则与场景引用拆分 |
| trace/eval 相关模块 | prompt 版本、区段、路由、预算、错误码和对比报告 |

## 12. 交付物

3.1 完成时应包含：

- 分层且版本化的 DevAgent prompt。
- 生效的 token usage 与软/硬预算。
- 每用户轮次模型路由。
- 明确的 Bash schema、`search_text`、`delete_path` 和 cwd 能力。
- T0/T1/T2 Todo 策略。
- 结构化 run checkpoint 和状态快路径。
- 选择性技能加载。
- 长期记忆写入/召回治理。
- ReAct step 压缩和重复失败熔断。
- trace 3.1 指标及 3.0/3.1 对比报告。
- 单元、集成、安全和固定任务回放测试。
- 灰度开关、回滚说明和更新后的架构文档。

## 13. Definition of Done

DevAgent 3.1 只有在以下条件全部满足时才视为完成：

1. P0 正确性和安全项全部合入并有自动化测试。
2. 固定评测集的任务成功率、测试通过率、UML/源码一致性均不低于 3.0。
3. 安全回归用例 100% 通过。
4. token 预算在真实模型响应上可观测且严格生效。
5. 最新 trace 同类任务不再出现系统性 shell 契约猜测和 Todo 误阻止。
6. 状态查询能从 checkpoint 准确回答，不重新执行上一任务。
7. 聚合 LLM 调用、工具调用、prompt token 和耗时达到发布目标，或对未达目标项给出经评审接受的模型/质量原因。
8. 3.1 静态 prompt 通过 A/B 硬门禁后才设为默认。
9. 所有 3.1 开关、prompt 版本、路由选择和上下文构成可在 trace 中追溯。
10. 相关设计文档、评测基线和运维回滚说明同步更新。

## 14. 推荐实施拆分

为降低评审与回归风险，建议按以下变更集提交：

1. `v3.1-observability-budget`：usage、预算、trace 指标。
2. `v3.1-model-routing`：问候快路径和每轮模型选择。
3. `v3.1-tool-contracts`：Bash schema、cwd、search、delete、安全测试。
4. `v3.1-react-guardrails`：Todo 顺序、重复失败熔断、step 结构。
5. `v3.1-intent-context`：中文意图、工具路由、Todo 分级、checkpoint。
6. `v3.1-skill-memory`：技能选择性加载和记忆治理。
7. `v3.1-compaction`：语义压缩和长任务回归。
8. `v3.1-prompt`：静态 prompt 候选、A/B 报告、默认版本切换。

该顺序确保 prompt 精简建立在运行时能力已经明确、动态噪声已经降低、质量评测已经可用的基础上，从而最大限度降低性能回退风险。

## 15. 实现进度同步（2026-09-01）

当前状态：**3.1 运行时基础能力已实现，完整发布验收尚未完成**。

### 15.1 已完成

- LLM usage 回传到 ReAct 循环，token 硬预算在真实响应字段上累计。
- 工具 schema 筛选保持路由顺序，避免 set 顺序造成缓存抖动。
- Todo 计划门禁改为执行前检查，修复同一响应中 `todo_write` 与业务工具的误阻止。
- Bash 增加 `cwd`（source/test/design/workspace）参数和与实际限制一致的说明。
- 增加结构化 `search_text`、`delete_path` 工具，并接入工作区安全策略。
- 中文 UML 组件/元素/节点/关系意图进入工具路由。
- 模型按用户轮次重新选择，问候不会锁定后续复杂任务的模型。
- 增加基于 Agent 实例及 RunStore 元数据的 run checkpoint 和状态追问快路径，支持重连后的持久化回退读取。
- 记忆提取允许返回空数组；测试/清理/状态类上下文不召回无关长期记忆。
- ReAct 历史支持 tool-call/result 成对压缩，并保留旧步骤的 extractive checkpoint。
- PromptBuilder 增加动态区段 token 统计，并提供 `DEVAGENT_PROMPT_VERSION=3.1` 紧凑 Prompt opt-in。
- 记忆召回按稳定 `subject` 去重，避免同主题候选同时注入。
- 记忆主题冲突按“已确认优先、再按更新时间、最后按相关性”选择唯一候选，并写入治理确认标记。
- Agent metrics 聚合 Prompt 构建次数、估算 token、压缩 token 和 Prompt 版本，支持后续 A/B 门禁且不保存 Prompt 内容。
- Trace 新增只读聚合摘要接口，按版本汇总 Prompt token、LLM usage、工具调用/错误和压缩次数，不返回原始内容。

### 15.2 部分完成

- Todo 已具备基础 T2 判断，但完整 T0/T1/T2 策略、前端状态呈现和 feature flag 仍未完成。
- 已有 step 级成对/抽取式压缩，但尚未完成按编辑证据、验证结果和重复失败分类的完整语义压缩。
- checkpoint 已写入 RunStore 元数据；跨服务重启的恢复/续跑语义仍未实现，目前仅支持状态读取。
- `DEVAGENT_PROMPT_VERSION` 已可切换 Prompt 候选，但尚未完成固定任务集 A/B 和默认版本切换。

### 15.3 未开始或未完成发布闭环

- 固定 trace 回放、3.0/3.1 聚合指标报告和质量门禁。
- 记忆治理的跨服务确认同步、撤销语义及完整端到端回放仍未完成。
- 文档第 8 节列出的完整 Feature Flag、10%/50% 灰度和按模块回滚链路。
- 3.1 发布目标（调用数、prompt token、工具失败、延迟）在真实任务集上的达标验证。

### 15.4 验证与提交记录

- backend 全量测试：**163 passed**（仓库既有 `.pytest_cache` 权限 warning 不影响结果）。
- 首批提交：`f285147 feat: implement DevAgent 3.1 optimization foundation`。
- 上下文与 Prompt telemetry：`774e58a feat: add DevAgent context compaction and prompt telemetry`。
- 记忆主题去重：`34dd250 fix: deduplicate DevAgent memory subjects`。
- checkpoint 持久化：`499b802 feat: persist DevAgent run checkpoints`。
- 记忆冲突治理：`dde7b9e fix: prioritize confirmed memory conflicts`。
- Prompt metrics 聚合：`dfd485a feat: aggregate DevAgent prompt metrics`。
- Trace 聚合摘要：`e41717f feat: add privacy-safe trace summaries`。

后续工作必须先完成固定评测集的质量门禁，再将 3.1 紧凑 Prompt 和相关运行时开关设为默认。
