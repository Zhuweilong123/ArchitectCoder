# BaseAgents 框架

基于分层解耦、职责单一、接口统一原则构建的轻量级 Agent 框架。

## 核心原则

- **分层解耦**：core / agents / tools 三层独立，互不交叉依赖
- **职责单一**：每个模块只做一件事，Agent 只管推理循环，Tool 只管执行逻辑
- **接口统一**：Agent 和 Tool 通过 ABC 抽象基类约束，子类必须实现标准接口

## 架构

```
agent_base/
├── __init__.py                          # 统一导出入口（28 个公开符号）
│
├── agentTest/                           # 测试和演示代码（非生产代码）
│   ├── demo_reflection.py               # ReflectionAgent 使用示例
│   ├── demo_simple.py                   # SimpleAgent + ReActAgent 使用示例
│   ├── demo_uml_tool.py                 # UmlValidationTool 使用示例
│   └── demo_dev_system.py               # 对话 Agent 完整开发流程演示
│
├── core/                                # 核心基础设施层
│   ├── exceptions.py                    # 异常体系 (BaseAgentsException + 4 子类)
│   ├── config.py                        # 配置管理 (Pydantic, from_env())
│   ├── message.py                       # 消息系统 (Message + MessageRole)
│   ├── llm.py                           # 统一 LLM 接口 (6 种 provider + 同步/异步/流式/FC)
│   └── agent.py                         # Agent 抽象基类 (ABC, run() + 历史管理)
│
├── agents/                              # Agent 实现层（4 种范式 + 可中断包装器）
│   ├── __init__.py                      # 导出 Simple/ReAct/Reflection/PlanAndSolve
│   ├── simple_agent.py                  # 基础对话 + 文本格式工具调用
│   ├── react_agent.py                   # ReAct 循环 (原生 FC + 文本解析降级)
│   ├── reflection_agent.py              # 反思优化 (initial→reflect→refine) + Hook 机制
│   ├── plan_solve_agent.py              # 先规划后执行 (Planner→Executor)
│   └── interruptible.py                 # 可中断包装器 (前端 stop 按钮)
│
└── tools/                               # 工具系统层
    ├── __init__.py                      # 导出 Tool/ToolParameter/Registry/Chain/AsyncExecutor
    ├── base.py                          # Tool 基类 + ToolParameter + to_openai_schema()
    ├── registry.py                      # ToolRegistry (注册/发现/执行 + FC schema 生成)
    ├── chain.py                         # ToolChain + ToolChainManager (顺序流 + 变量模板)
    ├── async_executor.py                # AsyncToolExecutor (并行执行 I/O 密集任务)
    ├── review.py                        # RequestReviewTool + ReviewManager (人工审核机制)
    │
    └── my_tools/                        # 项目特有工具（13 个）
        ├── __init__.py
        ├── conversation_tools.py        # 7 个开发工具 + AsyncTool 基类 + ProgressRelay
        ├── knowledge_graph_tools.py     # 5 个知识图谱工具 (kg_query/expand/trace/diff/structure)
        ├── explore_project_tools.py     # 项目探索统一入口 (summary/locate 模式)
        ├── project_info_tools.py        # project_info / read_file / grep
        ├── uml_tools.py                 # UmlValidationTool (5 项跨图一致性校验)
        ├── uml_optimizer.py             # UmlOptimizer (ReflectionAgent 驱动的 UML 全局优化)
        ├── code_validator.py            # CodeValidator (ReActAgent FC 驱动的代码验证)
        ├── code_fixer.py                # CodeFixer (ReflectionAgent + pytest 驱动的修复)
        └── dev_system.py                # DevSystem (编排层: 验证→修复流水线)
```

## 快速开始

### 1. LLM 初始化

```python
from app.agent_base import BaseAgentsLLM

# 方式 A: 一行对接项目现有 Settings 配置
llm = BaseAgentsLLM.from_settings()

# 方式 B: 自动检测环境变量
llm = BaseAgentsLLM()  # 自动读取 DEEPSEEK_API_KEY 等

# 方式 C: 手动指定
llm = BaseAgentsLLM(provider="deepseek", model="deepseek-v4-pro", api_key="...")
```

支持的 Provider（自动检测或手动指定）：

| Provider | 默认模型 | 环境变量 |
|----------|---------|---------|
| openai | gpt-3.5-turbo | OPENAI_API_KEY |
| deepseek | deepseek-chat | DEEPSEEK_API_KEY |
| modelscope | Qwen2.5-72B-Instruct | MODELSCOPE_API_KEY |
| zhipu | glm-4 | ZHIPU_API_KEY |
| ollama | llama3 (本地) | — |
| vllm | (本地) | — |

### 2. 四种 Agent 范式

```python
from app.agent_base import SimpleAgent, ReActAgent, ReflectionAgent, PlanAndSolveAgent
from app.agent_base.tools import ToolRegistry

registry = ToolRegistry()
# ... 注册工具 ...

# 基础对话（一次 LLM 调用，可选文本格式工具调用）
agent = SimpleAgent(name="助手", llm=llm, system_prompt="你是有用的助手")
answer = agent.run("Python的with语句有什么作用？")

# 推理+行动（ReAct 循环，支持原生 Function Calling）
agent = ReActAgent(
    name="研究员", llm=llm, tool_registry=registry,
    max_steps=5, use_native_fc=True,
)
# 推荐：异步 FC 模式
answer = await agent.arun("搜索2024年Java最新特性")
# 流式：每轮 yield ReActProgress
async for progress in agent.arun_stream("帮我优化这段代码"):
    print(f"Step {progress.step}: {progress.actions}")

# 自我反思（三阶段：生成→审查→精炼）
agent = ReflectionAgent(name="写手", llm=llm, max_iterations=3)
answer = agent.run("写一篇关于AI伦理的短文")

# 先规划后执行（Planner 分解步骤 → Executor 逐步执行）
agent = PlanAndSolveAgent(name="规划者", llm=llm)
answer = agent.run("设计一个用户注册系统的数据库schema")
```

## Agent 范式详解

### 1. SimpleAgent — 基础对话

最简实现，封装一次 LLM 调用。支持可选的文本格式工具调用：

```python
agent = SimpleAgent(name="助手", llm=llm, tool_registry=registry, enable_tool_calling=True)
answer = agent.run("查一下天气")  # LLM 输出 [TOOL_CALL:search:北京天气] → 自动执行 → 返回结果
```

工具调用的文本格式：`[TOOL_CALL:tool_name:parameters]`，最多 3 轮工具循环。适合简单问答、无需复杂推理的场景。

### 2. ReActAgent — 推理+行动（主力 Agent）

实现了完整的 Reasoning + Acting 循环，**是项目对话 Agent 的主力范式**：

**两种运行模式：**

| 模式 | 机制 | 入口 | 适用场景 |
|------|------|------|---------|
| 原生 Function Calling | LLM 内置工具调用，结构化 JSON 参数，支持多工具并行 | `arun()` / `arun_stream()` | 推荐，所有支持 FC 的模型 |
| 文本解析降级 | 正则匹配 `Thought:/Action:` 格式 | `run()` | 兼容不支持 FC 的模型 |

**FC 模式核心循环：**

```
1. 构建 messages（system + user + history）
2. llm.ainvoke_with_tools(tool_specs) → 模型返回 tool_calls 或纯文本
3. 遍历 tool_calls → 全部并行执行（aexecute_tool_with_params）
4. 追加 assistant + tool 消息到 messages
5. 重复直到模型返回纯文本或达到 max_steps
```

**流式进度：** `arun_stream()` 每轮 yield `ReActProgress` 对象，包含：

```python
class ReActProgress:
    step: int                    # 当前轮次（1-based）
    actions: list[str]           # 本轮调用的工具名列表
    tool_calls_detail: list[dict] # [{name, arguments, observation}] 详情
    thought: str                 # LLM 文本内容（工具调用之外的思考）
    is_final: bool               # 是否为本轮后终止
    final_answer: str            # 若 is_final=True，则为最终答案
```

**对话场景配置：** `max_steps=12`，防止模型在工具循环中无限徘徊。

### 3. ReflectionAgent — 反思优化

三阶段循环，用于需要反复打磨的任务（UML 优化、代码修复）：

```
Phase 1: initial  → 首轮生成
Phase 2: reflect → 外部 Hook 验证 + LLM 语义审查 → 生成反馈
Phase 3: refine  → 根据反馈重新生成
        重复 2-3 直到验证通过或达到 max_iterations
```

**Hook 机制（关键特性）：**

- `validate(content) → feedback_str`：注入外部验证工具实现客观化反馈。返回空字符串表示通过，停止迭代；返回问题描述则作为补充消息追加到对话中，触发 LLM 修正。
- 使用 `messages` 列表维护全对话上下文，原始需求永远在 `messages[0]` 中，LLM 每轮都能看到完整历史。

```python
def pytest_validate(content):
    """用 pytest 验证代码，而非 LLM 自省"""
    result = run_pytest(content)
    if result.passed:
        return ""                     # 通过 → 停止迭代
    return f"测试失败:\n{result.output}"  # 失败 → 触发 refine

answer = agent.run("修复 bug", validate=pytest_validate)
```

# 旧的 3 参数 API（见下方）。
```

**UML 优化场景：** `UmlOptimizer` 用此模式替代了旧版单次 `chat()` 调用——initial 生成设计 → 程序化验证跨图一致性 → 反馈注入 messages → LLM 根据对话历史修正。

### 4. PlanAndSolveAgent — 先规划后执行

将复杂问题分解为步骤序列，逐步执行：

```
Phase 1: Planner   → 生成 Python list 格式的步骤 ["step1", "step2", ...]
Phase 2: Executor  → 逐步执行每个步骤，历史结果传递给后续步骤
```

适合复杂多步骤任务、需要结构化分解的问题。LLM 只关注当前步骤，上下文更聚焦。

### 5. InterruptibleAgent — 可中断包装器

薄包装层，在每轮 ReAct 步骤前检查 `should_stop()` 回调：

```python
agent = ReActAgent(name="dev", llm=llm, tool_registry=registry)
interruptible = InterruptibleAgent(agent=agent, should_stop=lambda: stop_flag)

async for progress in interruptible.arun_stream("生成代码"):
    if progress.get("event") == "stopped":
        print("被用户中断")
        break
```

前端通过 WebSocket 发送 `{"type": "stop"}` → 设置 `stop_requested=True` → 下一轮循环检测到并终止。

## 工具系统

### 工具基类

```python
from app.agent_base.tools import Tool, ToolParameter

class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool", description="工具描述")

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="input", type="string", description="输入")]

    def run(self, parameters: dict) -> str:
        return f"处理结果: {parameters['input']}"

    # to_openai_schema() 自动生成 OpenAI Function Calling JSON Schema
```

### 工具注册表（ToolRegistry）

两种注册方式：

```python
registry = ToolRegistry()

# 方式 A: Tool 对象（推荐 — 完整的参数 schema + FC 支持）
registry.register_tool(MyTool())

# 方式 B: 便捷函数（自动生成最简 schema）
registry.register_function("echo", "回显输入", lambda s: s)

# 执行
result = registry.execute_tool_with_params("my_tool", {"input": "hello"})
result = await registry.aexecute_tool_with_params("my_tool", {"input": "hello"})  # 异步

# FC schema
specs = registry.get_openai_specs()  # → 直接传给 LLM 的 tools 参数
```

### AsyncTool — 异步工具基类

项目特有的异步工具模式。`run()` 返回 coroutine，由 `aexecute_tool_with_params()` 正确 await：

```python
class MyAsyncTool(AsyncTool):
    def get_parameters(self): return []  # 子类直接覆写 to_openai_schema()

    def run(self, parameters):
        return self._execute(parameters)  # 返回 coroutine

    async def _execute(self, parameters):
        # 异步逻辑：调用 LLM、读文件、执行子 Agent 等
        result = await llm.ainvoke([...])
        return json.dumps(result)
```

所有对话 Agent 工具均继承 `AsyncTool`，确保在 ReActAgent FC 循环中正确执行。

## 对话 Agent 工具集（13 个工具）

对话 Agent 注册了完整的开发工具链，形成 **设计→代码→验证→测试→修复→落盘** 全流程闭环：

### 核心开发工具链（7 个）

| 工具 | 内部实现 | 功能 |
|------|---------|------|
| `optimize_uml` | UmlOptimizer (ReflectionAgent, max_iterations=3) | UML 设计优化，支持从 `.umlproj` 加载或手传 JSON，含 5 项跨图一致性验证和自动修复 |
| `generate_code` | 直接 LLM 调用 | 从 UML 类图 JSON 生成 Python 源码 |
| `validate_code` | CodeValidator (ReActAgent FC, max_rounds=5) | 语法/导入/运行时验证，自动修复错误；内置 6 个验证子工具（check_imports/run_module/run_bash/analyze_error/diff_code/finish_validation） |
| `generate_tests` | 直接 LLM 调用 | 从源码生成 pytest 测试文件 |
| `run_tests` | 直接 pytest 执行 | 执行测试并返回 pass/fail 统计 |
| `fix_code` | CodeFixer (ReflectionAgent, max_iterations=5) | pytest 驱动的源码修复循环（run→reflect→refine 直到全部通过） |
| `write_files` | 直接文件 I/O | 将最终源码/测试写入磁盘 |

### 知识图谱工具（5 个）

| 工具 | 功能 |
|------|------|
| `kg_query` | BM25 语义查询节点（支持按需构建、空 pattern 枚举全部、驼峰模糊匹配） |
| `kg_expand` | 展开节点邻域关系 |
| `kg_trace` | 追踪设计-代码依赖路径 |
| `kg_diff` | 对比设计与代码差异 |
| `kg_project_structure` | 获取项目完整树状结构（含图、类、方法、消息） |

### 项目信息与探索（2 个）

| 工具 | 功能 |
|------|------|
| `explore_project` | **项目探索统一入口**。`mode='summary'` 按资源类型（uml/source/test）总结项目结构；`mode='locate'` 定位指定元素的精确位置（id/行号/偏移量）。内部走确定性流程（不走 ReAct），避免子代理重复验证。`what='all'` 时并行探索 |
| `request_review` | **人工审核**。Agent 在关键节点（代码/测试/设计决策）暂停执行，推送审核事件到前端，等待人工响应后继续 |

> **设计要点**：`explore_project` 收敛了所有只读操作（kg_*/read_file/grep/project_info），主 Agent 只需调用这一个工具即可理解项目，避免主 Agent 自己读文件导致 token 累积膨胀。

## 运行时架构

### WebSocket 驱动的对话 Agent

`backend/app/services/agent_chat_ws.py` 是对话 Agent 的运行时入口：

```
前端（React 对话面板）
  ↕ WebSocket (/api/agent/ws/chat)
后端 FastAPI
  ├── 单 ReActAgent（跨轮复用，懒创建）
  │   ├── system_prompt：行为准则 + 记忆注入
  │   ├── ToolRegistry：13 个工具
  │   └── max_steps=12, use_native_fc=True
  ├── 流式进度 → ReActProgress → 前端实时渲染
  ├── 审核拦截 → request_review → 暂停 → 人工响应 → 继续
  └── 中断控制 → stop 消息 → 优雅终止
```

### WebSocket 协议

```
客户端 → 服务端:
  {"type": "chat", "message": "...", "source_dir": "...", "test_dir": "...", "project_file": "..."}
  {"type": "stop"}
  {"type": "review_response", "review_id": 0, "response": "批准"}

服务端 → 客户端 (流式):
  {"event": "progress", "step": 1, "actions": [...], "thought": "...", "tool_calls_detail": [...]}
  {"event": "request_review", "review_id": 0, "review_type": "code", "title": "...", "question": "..."}
  {"event": "done", "result": "..."}
  {"event": "stopped", "reason": "User requested stop"}
  {"event": "error", "message": "..."}
  {"event": "design_updated", "diagrams": [...], "saved_to": "..."}  # optimize_uml 写盘后触发
```

### 子 Agent 分层架构

对话 Agent 内部的工具封装了多个子 Agent，形成分层代理：

```
主 Agent: ReActAgent (FC 模式, max_steps=12)
│
├── optimize_uml  → UmlOptimizer → ReflectionAgent (max_iterations=3)
│                    ├── initial: LLM 生成优化后的设计
│                    ├── reflect: 跨图引用校验 Hook (_validate_cross_references)
│                    └── refine: 根据反馈修正
│
├── validate_code → CodeValidator → ReActAgent FC (max_rounds=5)
│                    ├── check_imports: 语法+导入检测
│                    ├── run_module: 运行时验证
│                    ├── run_bash: 白名单安全命令
│                    ├── analyze_error: 错误定位分析
│                    ├── diff_code: 代码 diff
│                    └── finish_validation: 退出信号
│
├── fix_code      → CodeFixer → ReflectionAgent (max_iterations=5)
│                    ├── initial: 根据错误生成修复
│                    ├── reflect: pytest 运行结果（Hook 验证）
│                    └── refine: 修正代码
│
├── generate_code  → 直接 LLM 调用（非 Agent）
├── generate_tests → 直接 LLM 调用（非 Agent）
├── run_tests      → 直接 pytest 执行（非 Agent）
└── write_files    → 直接文件 I/O（非 Agent）
```

### 记忆系统集成

每次 Agent 任务完成后自动归档，下次任务开始时检索注入：

```
任务开始 → recall(project_id, user_message) → 相关记忆注入 system_prompt
任务结束 → 工具过程摘要 + 最终结论 → MemoryManager.remember() → 异步归档
```

记忆存储于 `data/memories.db`（SQLite + FTS5 + jieba 分词），BM25 检索。

### 会话日志

每次 WebSocket 连接生成双份日志，落盘到 `temp/chat_log/`：

| 日志 | 格式 | 内容 |
|------|------|------|
| `chat_*.md` | Markdown | 人类可读，记录用户消息、AI 回复、工具调用详情、审核请求/响应 |
| `trace_*.jsonl` | JSONL | 机器可回放，含 LLM 原始往返（prompt/completion）、工具调用参数/返回、审核记录 |

## 创建对话 Agent 工具

### 继承 AsyncTool

所有对话 Agent 工具应继承 `AsyncTool`，确保在 ReActAgent FC 循环中正确异步执行：

```python
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

class MyNewTool(AsyncTool):
    def __init__(self, llm, **deps):
        super().__init__(name="my_tool", description="工具描述")
        self.llm = llm

    async def _execute(self, params: dict) -> str:
        # 异步逻辑：LLM 调用、文件读写、子 Agent 执行等
        result = await self.llm.ainvoke([...])
        return json.dumps({"result": result})

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string", "description": "..."},
                    },
                    "required": ["param1"],
                },
            },
        }
```

### 注册到对话 Agent

在 `_create_dev_agent()` 中注册新工具：

```python
from app.agent_base.tools.registry import ToolRegistry
registry = ToolRegistry()
registry.register_tool(MyNewTool(llm=llm))
```

### 子 Agent 进度转发

工具内部如果运行子 Agent，通过 `ProgressRelay` 推送进度到前端：

```python
class MyTool(AsyncTool):
    def __init__(self, progress: ProgressRelay | None = None):
        self.progress = progress

    async def _execute(self, params):
        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "MySubAgent", "status": "started",
        })
        # ... 执行子 Agent ...
        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "MySubAgent", "status": "done",
        })
```

## UML 全局优化

`UmlOptimizer` 基于 ReflectionAgent 实现三阶段反射循环，替代旧版单次 `chat()` 调用：

```python
from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer

optimizer = UmlOptimizer(llm, max_iterations=3)
result = await optimizer.optimize(
    diagrams=existing_diagrams,
    instructions="增加支付模块，完善异常处理",
)
# result 包含: diagrams / design_constraints / changes_summary / consistency_report
```

reflection 阶段的 Hook 自动执行 5 项跨图一致性校验：
1. 生命线 `class_ref` → 类 ID 有效性（模糊匹配自动修复）
2. 时序图消息方法名 → 类方法签名匹配
3. 图 `component_id` → 组件 ID 有效性
4. 组件接口 ↔ 类接口一致性
5. 组件覆盖率检查（每个组件是否有关联的类图和时序图）

## 设计参考

- **架构模式**：Simple / ReAct / Reflection / Plan-and-Solve 四种经典 Agent 范式
- **工具系统**：万物皆为工具（Tool ABC → Registry（注册/发现/FC schema）→ Chain（顺序编排）→ AsyncExecutor（并行执行））
- **分层代理**：工具封装子 Agent（对话 Agent → optimize_uml/validate_code/fix_code → ReflectionAgent/ReActAgent 子实例）
- **流式进度**：ReActProgress 逐轮推送 → 前端实时渲染工具调用过程
- **人工介入**：RequestReviewTool + ReviewManager → asyncio.Future 阻塞 → 人工 WebSocket 响应 → 继续执行
- **可中断**：InterruptibleAgent 包装器 + should_stop 回调 → 优雅终止循环
- **记忆系统**：跨任务知识归档与检索（SQLite + FTS5 + jieba 分词 + BM25 检索 + LLM 事实提取）
- **双日志**：人读 Markdown（chat_*.md）+ 机读 JSONL trace（trace_*.jsonl）
