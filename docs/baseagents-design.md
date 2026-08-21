# BaseAgents 框架设计

> 本文归档 UML Designer 的 Agent 框架（`backend/app/agent_base/`）设计，
> 反映最新代码（UML 优化已迁移 V2 引擎、9 工具集、记忆系统改造）。
> 作为后续扩展工具、接入新 Agent 范式、调整运行时的参考基线。

## 1. 定位与核心原则

基于分层解耦、职责单一、接口统一构建的轻量级 Agent 框架。

- **分层解耦**：core / agents / tools 三层独立，互不交叉依赖。
- **职责单一**：Agent 只管推理循环，Tool 只管执行逻辑。
- **接口统一**：Agent 与 Tool 通过 ABC 抽象基类约束，子类实现标准接口。

公开入口 `__init__.py` 导出 **20 个符号**（core 10 + agents 4 + tools 6）。

## 2. 架构

```
agent_base/
├── __init__.py                          # 统一导出入口（20 个公开符号）
│
├── agentTest/                           # 测试和演示代码（非生产代码）
│   ├── demo_reflection.py / demo_simple.py / demo_uml_tool.py / demo_dev_system.py
│
├── core/                                # 核心基础设施层
│   ├── exceptions.py                    # 异常体系（BaseAgentsException + 4 子类）
│   ├── config.py                        # 配置管理（Pydantic, from_env()）
│   ├── message.py                       # 消息系统（Message + MessageRole）
│   ├── llm.py                           # 统一 LLM 接口（6 种 provider + 同步/异步/流式/FC）
│   └── agent.py                         # Agent 抽象基类（ABC, run() + 历史管理）
│
├── agents/                              # Agent 实现层（4 种范式 + 可中断包装器）
│   ├── simple_agent.py                  # 基础对话 + 文本格式工具调用
│   ├── react_agent.py                   # ReAct 循环（原生 FC + 文本解析降级）
│   ├── reflection_agent.py              # 反思优化（initial→reflect→refine）+ Hook 机制
│   ├── plan_solve_agent.py              # 先规划后执行（Planner→Executor）
│   └── interruptible.py                 # 可中断包装器（前端 stop 按钮）
│
└── tools/                               # 工具系统层
    ├── base.py                          # Tool 基类 + ToolParameter + to_openai_schema()
    ├── registry.py                      # ToolRegistry（注册/发现/执行 + FC schema 生成）
    ├── chain.py                         # ToolChain + ToolChainManager（顺序流 + 变量模板）
    ├── async_executor.py                # AsyncToolExecutor（并行执行 I/O 密集任务）
    ├── review.py                        # ReviewManager（人工审核机制）+ SubmitUmlReviewTool
    │
    └── my_tools/                        # 项目特有工具
        ├── conversation_tools.py        # 7 个开发工具 + AsyncTool 基类 + ProgressRelay
        ├── explore_project_tools.py     # 项目探索统一入口（summary/locate 模式）
        ├── knowledge_graph_tools.py     # 5 个知识图谱工具（内部，供 explore 使用）
        ├── project_info_tools.py        # project_info / read_file / grep（内部）
        ├── uml_tools.py                 # UmlValidationTool（5 项跨图一致性校验）
        ├── uml_optimizer.py             # V2 引擎兼容委托（V1 已下线）
        ├── code_validator.py            # CodeValidator（ReActAgent FC 驱动）
        ├── code_fixer.py                # CodeFixer（ReflectionAgent + pytest 驱动）
        └── dev_system.py                # DevSystem（编排层：验证→修复流水线）
```

## 3. 快速开始

### 3.1 LLM 初始化

```python
from app.agent_base import BaseAgentsLLM

llm = BaseAgentsLLM.from_settings()   # 方式 A：对接项目 Settings
llm = BaseAgentsLLM()                 # 方式 B：自动检测环境变量
llm = BaseAgentsLLM(provider="deepseek", model="deepseek-v4-pro", api_key="...")  # 方式 C
```

支持的 Provider：

| Provider | 默认模型 | 环境变量 |
|----------|---------|---------|
| openai | gpt-3.5-turbo | OPENAI_API_KEY |
| deepseek | deepseek-chat | DEEPSEEK_API_KEY |
| modelscope | Qwen/Qwen2.5-72B-Instruct | MODELSCOPE_API_KEY |
| zhipu | glm-4 | ZHIPU_API_KEY |
| ollama | llama3（本地） | — |
| vllm | （本地） | — |

### 3.2 四种 Agent 范式

```python
from app.agent_base import SimpleAgent, ReActAgent, ReflectionAgent, PlanAndSolveAgent
from app.agent_base.tools import ToolRegistry

registry = ToolRegistry()
# ... 注册工具 ...

agent = SimpleAgent(name="助手", llm=llm, system_prompt="你是有用的助手")
answer = agent.run("Python 的 with 语句有什么作用？")

agent = ReActAgent(name="研究员", llm=llm, tool_registry=registry, max_steps=5, use_native_fc=True)
answer = await agent.arun("搜索 2024 年 Java 最新特性")          # 异步 FC
async for progress in agent.arun_stream("帮我优化这段代码"):      # 流式进度
    print(f"Step {progress.step}: {progress.actions}")

agent = ReflectionAgent(name="写手", llm=llm, max_iterations=3)
answer = agent.run("写一篇关于 AI 伦理的短文")

agent = PlanAndSolveAgent(name="规划者", llm=llm)
answer = agent.run("设计一个用户注册系统的数据库 schema")
```

## 4. Agent 范式详解

### 4.1 SimpleAgent — 基础对话

封装一次 LLM 调用，支持可选的文本格式工具调用（`[TOOL_CALL:tool_name:parameters]`，
最多 3 轮工具循环）。适合简单问答、无需复杂推理的场景。

### 4.2 ReActAgent — 推理+行动（主力）

完整 Reasoning + Acting 循环，**项目对话 Agent 的主力范式**。

| 模式 | 机制 | 入口 | 适用 |
|------|------|------|------|
| 原生 Function Calling | LLM 内置工具调用，结构化 JSON 参数，支持多工具并行 | `arun()` / `arun_stream()` | 推荐 |
| 文本解析降级 | 正则匹配 `Thought:/Action:` 格式 | `run()` | 兼容无 FC 模型 |

**FC 核心循环**：构建 messages → `ainvoke_with_tools(tool_specs)` → 遍历 tool_calls
并行执行 → 追加 assistant/tool 消息 → 循环直至纯文本或 `max_steps`。

**流式进度** `ReActProgress`：`step / actions / tool_calls_detail / thought / is_final /
final_answer`。

### 4.3 ReflectionAgent — 反思优化

三阶段循环（initial → reflect → refine），用于需反复打磨的任务（代码修复）。

**Hook 机制**：`validate(content) → feedback_str`，返回空串表示通过（停止迭代），
返回问题描述则作为补充消息追加，触发 LLM 修正。原始需求始终在 `messages[0]`。

### 4.4 PlanAndSolveAgent — 先规划后执行

Planner 生成步骤列表 → Executor 逐步执行，历史结果传递给后续步骤。

### 4.5 InterruptibleAgent — 可中断包装器

每轮 ReAct 步骤前检查 `should_stop()` 回调。前端 WebSocket 发送 `{"type": "stop"}`
→ 下一轮循环检测并终止。

## 5. 工具系统

- **Tool 基类**：`name / description / get_parameters() / run()`，`to_openai_schema()`
  自动生成 FC JSON Schema。
- **ToolRegistry**：`register_tool()`（推荐，完整 schema）或 `register_function()`
  （便捷函数）；`execute_tool_with_params()` / `aexecute_tool_with_params()`；
  `get_openai_specs()` 直接喂给 LLM 的 `tools` 参数。
- **AsyncTool**：异步工具基类。`run()` 返回 `self._execute(parameters)` 的 coroutine，
  由 `aexecute_tool_with_params()` await；`get_parameters()` 返回空，子类直接覆写
  `to_openai_schema()`。所有对话工具均继承 `AsyncTool`。
- **ToolChain / ToolChainManager**：顺序编排 + 变量模板。
- **AsyncToolExecutor**：并行执行 I/O 密集任务。
- **ReviewManager**：人工审核机制（asyncio.Future 阻塞等待人工响应）。两个使用方：
  `SubmitUmlReviewTool`（UML diff 审核）与 `BashTool`（敏感命令批准，见 file_system_tools）。

## 6. 对话 Agent 工具集（9 个）

主 Agent 注册 9 个工具，形成 **设计 → 代码 → 验证 → 测试 → 修复 → 落盘** 闭环。

### 6.1 核心开发工具（7 个）

| 工具 | 内部实现 | 功能 |
|------|---------|------|
| `optimize_uml` | **V2 直连优化引擎**（`run_optimize_v2`） | UML 设计优化：scope 分析 + 单次 LLM + 程序化跨图验证 + 可选落盘 |
| `generate_code` | 直接 LLM 调用 | 从 UML 类图 JSON 生成 Python 源码 |
| `validate_code` | CodeValidator（ReActAgent FC, max_rounds=5） | 语法/导入/运行时验证，自动修复；内置 6 个验证子工具 |
| `generate_tests` | 直接 LLM 调用 | 从源码生成 pytest 测试文件 |
| `run_tests` | 直接 pytest 执行 | 执行测试并返回 pass/fail 统计 |
| `fix_code` | CodeFixer（ReflectionAgent, max_iterations=5） | pytest 驱动源码修复（run→reflect→refine） |
| `write_files` | 直接文件 I/O | 将最终源码/测试写入磁盘 |

### 6.2 探索与审核（2 个）

| 工具 | 功能 |
|------|------|
| `explore_project` | **项目探索统一入口**。`summary` 按资源类型（uml/source/test）总结；`locate` 定位元素精确位置。内部走确定性流程（不走 ReAct），KG 结构会与 `.umlproj` 文件交叉校验。`what='all'` 并行探索 |
| `request_review` | **人工审核**。关键节点暂停执行，推送审核事件到前端，等待人工响应后继续 |

### 6.3 内部工具（不暴露给主 Agent）

主 Agent 的只读能力收敛进 `explore_project`，以下工具仅供 `explore_project` 等内部调用：

- **知识图谱 5 个**（`knowledge_graph_tools.py`）：`kg_query` / `kg_expand` / `kg_trace` /
  `kg_diff` / `kg_project_structure`。
- **项目信息**（`project_info_tools.py`）：`project_info` / `read_file` / `grep`。
- **UML 校验**（`uml_tools.py`）：`UmlValidationTool`（5 项跨图一致性校验）。

> **设计要点**：收敛所有只读操作，主 Agent 只需 `explore_project` 一个入口即可理解
> 项目，避免主 Agent 自己读文件导致 token 累积膨胀。

## 7. 运行时架构

### 7.1 WebSocket 驱动的对话 Agent

`backend/app/services/agent_chat_ws.py` 是运行时入口：

```
前端（React 对话面板）
  ↕ WebSocket (/api/agent/ws/chat)
后端 FastAPI
  ├── 单 ReActAgent（跨轮复用，懒创建，max_steps=12, use_native_fc=True）
  │   ├── system_prompt：行为准则 + 项目上下文 + 记忆注入
  │   └── ToolRegistry：9 个工具
  ├── 流式进度 → ReActProgress → 前端实时渲染
  ├── 审核拦截 → submit_uml_review / bash 敏感命令 → 暂停 → 人工响应 → 继续
  └── 中断控制 → stop 消息 → 优雅终止
```

### 7.2 WebSocket 协议

```
客户端 → 服务端:
  {"type": "chat", "message": "...", "source_dir": "...", "test_dir": "...", "project_file": "..."}
  {"type": "stop"}
  {"type": "review_response", "review_id": 0, "response": "批准"}

服务端 → 客户端 (流式):
  {"event": "progress", "step": 1, "actions": [...], "thought": "...", "tool_calls_detail": [...]}
  {"event": "request_review", "review_id": 0, "review_type": "bash_command", "title": "...", "question": "..."}
  {"event": "done", "result": "..."}
  {"event": "stopped", "reason": "User requested stop"}
  {"event": "error", "message": "..."}
  {"event": "design_updated", "diagrams": [...], "saved_to": "..."}   # optimize_uml 落盘后触发
  {"event": "design_element", ...}                                     # 设计元素级更新
```

### 7.3 子 Agent 分层架构

```
主 Agent: ReActAgent (FC 模式, max_steps=12)
│
├── optimize_uml  → OptimizeUmlTool → V2 直连引擎 (run_optimize_v2)
│                    ├── scope 分析（识别影响范围）
│                    ├── 单次 LLM 生成优化设计
│                    └── 程序化跨图一致性验证（可选 save_to_project 落盘）
│
├── validate_code → ValidateCodeTool → CodeValidator (ReActAgent FC, max_rounds=5)
│                    ├── check_imports / run_module / run_bash
│                    ├── analyze_error / diff_code
│                    └── finish_validation（退出信号）
│
├── fix_code      → FixCodeTool → CodeFixer (ReflectionAgent, max_iterations=5)
│                    ├── initial：根据错误生成修复
│                    ├── reflect：pytest 运行结果（Hook 验证）
│                    └── refine：修正代码
│
├── explore_project → 确定性探索（非 ReAct）
├── generate_code / generate_tests → 直接 LLM 调用
├── run_tests → 直接 pytest；write_files → 文件 I/O
├── submit_uml_review → UML diff 人工审核（暂停等待 accept/reject）
└── bash → 两级防护：高危命令直接拒绝；敏感命令暂停等待人工批准
```

## 8. 记忆系统集成

每次 Agent 任务完成后自动归档，下次任务开始时检索注入：

```
任务开始 → recall(project_id, user_message) → 相关记忆注入 system_prompt
任务结束 → 工具过程摘要 + 最终结论 → MemoryManager.remember() → 异步归档
```

记忆存储于 `data/memories.db`（SQLite + FTS5 + jieba）。记忆系统已引入
**subject 后写覆盖 + recency 检索 + 类型化衰退 + 检索别名**，详见
`docs/memory-system-design.md`。

## 9. 会话日志（trace）

每次 WebSocket 连接生成结构化 trace 日志，落盘 `temp/chat_log/`：

| 日志 | 格式 | 内容 |
|------|------|------|
| `trace_*.jsonl` | JSONL | 机读可回放，含 LLM 原始往返、工具调用参数/返回、审核记录 |

支持 TraceViewer 可视化与确定性回放，详见 `docs/trace-replay-design.md`。

## 10. 创建对话 Agent 工具

### 10.1 继承 AsyncTool

```python
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool

class MyNewTool(AsyncTool):
    def __init__(self, llm, **deps):
        super().__init__(name="my_tool", description="工具描述")
        self.llm = llm

    async def _execute(self, params: dict) -> str:
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
                    "properties": {"param1": {"type": "string", "description": "..."}},
                    "required": ["param1"],
                },
            },
        }
```

### 10.2 注册

在 `_create_dev_agent()` 中 `registry.register_tool(MyNewTool(llm=llm))`。

### 10.3 子 Agent 进度转发

工具内部运行子 Agent 时，通过 `ProgressRelay` 推送 `sub_agent` 事件到前端。

## 11. UML 全局优化（V2）

V1 的 `UmlOptimizer`（ReflectionAgent 三阶段反射）已下线，现由 **V2 直连优化引擎**
（`backend/app/services/uml_optimizer_v2.py` 的 `run_optimize_v2`）取代：

```python
from app.services.uml_optimizer_v2 import run_optimize_v2

result = await run_optimize_v2(
    project_file="project.umlproj",
    instructions="增加支付模块，完善异常处理",
)
# result 包含: diagrams / design_constraints / changes_summary / consistency_report
```

流程：**scope 分析（识别影响范围）→ 单次 LLM 生成 → 程序化跨图一致性验证**，
替代 V1 的「initial 生成 → 程序化验证 → 反馈注入 messages → LLM 修正」多轮反射。

`uml_optimizer.py` 保留 `optimize_project_v2()` 作为 V1 `optimize_project()` 的兼容委托。

## 12. 设计参考

- **架构模式**：Simple / ReAct / Reflection / Plan-and-Solve 四种经典 Agent 范式。
- **工具系统**：万物皆为工具（Tool ABC → Registry → Chain → AsyncExecutor）。
- **分层代理**：工具封装子 Agent（对话 Agent → optimize_uml/validate_code/fix_code →
  V2 引擎 / ReActAgent / ReflectionAgent 子实例）。
- **流式进度**：ReActProgress 逐轮推送 → 前端实时渲染。
- **人工介入**：ReviewManager → asyncio.Future 阻塞 → 人工响应（UML diff 审核 + bash 敏感命令批准）。
- **可中断**：InterruptibleAgent 包装器 + should_stop 回调。
- **记忆系统**：跨任务知识归档与检索（subject 后写覆盖 + recency + 别名 + 类型化衰退）。
- **结构化 trace**：机读 JSONL trace，支持 TraceViewer 与确定性回放。

## 13. 文件索引

| 文件 | 职责 |
|---|---|
| `backend/app/agent_base/__init__.py` | 统一导出（20 符号） |
| `backend/app/agent_base/core/llm.py` | `BaseAgentsLLM`（6 provider + 同步/异步/流式/FC） |
| `backend/app/agent_base/core/agent.py` | Agent ABC + 历史管理 |
| `backend/app/agent_base/agents/react_agent.py` | ReAct 循环（FC + 文本降级）+ ReActProgress |
| `backend/app/agent_base/agents/reflection_agent.py` | 反思优化 + Hook 机制 |
| `backend/app/agent_base/tools/registry.py` | ToolRegistry（注册/发现/执行 + FC schema） |
| `backend/app/agent_base/tools/review.py` | ReviewManager + SubmitUmlReviewTool |
| `backend/app/agent_base/tools/my_tools/conversation_tools.py` | 7 开发工具 + AsyncTool + create_conversation_tools |
| `backend/app/agent_base/tools/my_tools/explore_project_tools.py` | 项目探索统一入口 |
| `backend/app/services/uml_optimizer_v2.py` | V2 直连优化引擎 |
| `backend/app/services/agent_chat_ws.py` | 运行时入口 + 记忆归档/注入 + 9 工具注册 |
