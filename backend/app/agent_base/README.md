# BaseAgents 框架

基于分层解耦、职责单一、接口统一原则构建的轻量级 Agent 框架。

## 核心原则

- **分层解耦**：core / agents / tools 三层独立
- **职责单一**：每个模块只做一件事
- **接口统一**：Agent 和 Tool 通过 ABC 抽象基类约束，子类必须实现标准接口

## 架构

```
agent_base/
├── __init__.py                     # 统一导出入口
│
├── core/                           # 核心层
│   ├── exceptions.py               # 异常体系 (BaseAgentsException + 4 子类)
│   ├── config.py                   # 配置管理 (Pydantic, 支持 from_env())
│   ├── message.py                  # 消息系统 (Message + MessageRole)
│   ├── llm.py                      # 统一 LLM 接口 (多 provider + 同步/异步)
│   └── agent.py                    # Agent 抽象基类 (ABC, run() + 历史管理)
│
├── agents/                         # Agent 实现层
│   ├── simple_agent.py             # 基础对话 + 可选工具调用
│   ├── react_agent.py              # ReAct (Thought→Action→Observe 循环)
│   ├── reflection_agent.py         # Reflection (initial→reflect→refine) + Hook 机制
│   └── plan_solve_agent.py          # Plan-and-Solve (规划→逐步执行)
│
└── tools/                          # 工具系统层
    ├── base.py                     # Tool 基类 + ToolParameter
    ├── registry.py                 # ToolRegistry (注册/发现/执行)
    ├── chain.py                    # ToolChain + ToolChainManager
    ├── async_executor.py           # AsyncToolExecutor (并行执行)
    │
    └── my_tools/                   # 项目特有工具
        ├── uml_tools.py            # UmlValidationTool (跨图引用验证)
        └── uml_optimizer.py        # UmlOptimizer (基于 ReflectionAgent 的 UML 全局优化)
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

### 2. 四种 Agent 范式

```python
from app.agent_base import SimpleAgent, ReActAgent, ReflectionAgent, PlanAndSolveAgent

# 基础对话
agent = SimpleAgent(name="助手", llm=llm, system_prompt="你是有用的助手")
answer = agent.run("Python的with语句有什么作用？")

# 推理+行动
agent = ReActAgent(name="研究员", llm=llm, tool_registry=registry, max_steps=5)
answer = agent.run("搜索2024年Java最新特性")

# 自我反思（三阶段：生成→审查→精炼）
agent = ReflectionAgent(name="写手", llm=llm, max_iterations=3)
answer = agent.run("写一篇关于AI伦理的短文")

# 先规划后执行
agent = PlanAndSolveAgent(name="规划者", llm=llm)
answer = agent.run("设计一个用户注册系统的数据库schema")
```

### 3. ReflectionAgent Hook 机制

反射阶段可注入外部验证工具，实现客观化反馈（非 LLM 自省）：

```python
def my_validate(task, content, context):
    """外部验证 Hook，返回空字符串表示通过"""
    issues = validate_logic(content)
    if not issues:
        return ""          # 通过 → 停止迭代
    return "问题:\n" + issues  # 有问题 → 注入 feedback → 触发 refine

answer = agent.run("任务描述", reflect_hook=my_validate)
```

### 4. 自定义工具

继承 `Tool` 基类，放入 `my_tools/` 目录：

```python
from app.agent_base.tools import Tool, ToolParameter

class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool", description="...")

    def get_parameters(self):
        return [ToolParameter(name="input", type="string", description="输入")]

    def run(self, parameters):
        return f"处理结果: {parameters['input']}"

# 注册使用
registry = ToolRegistry()
registry.register_tool(MyTool())
```

### 5. UML 全局优化（替换 optimize_project）

```python
from app.agent_base.tools.my_tools import optimize_project_v2

# 三阶段反射循环替代原有的单次 chat() 调用
result = await optimize_project_v2(
    diagrams=existing_diagrams,
    instructions="增加支付模块，完善异常处理",
)
```

## 设计参考

- 架构模式：Simple / ReAct / Reflection / Plan-and-Solve 四种经典 Agent 范式
- 工具系统：万物皆为工具（Tool ABC + Registry + Chain + AsyncExecutor）
