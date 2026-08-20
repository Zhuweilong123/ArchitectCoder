# my_tools — Agent 驱动的代码开发工具集

基于 BaseAgents 框架构建的 UML 设计 → 代码生成 → 验证 → 测试 → 修复全流程工具集。

## 架构概览

```
用户需求
  │
  ▼
┌─────────────────────────────────────────────────────┐
│              对话 Agent (ReActAgent FC)              │
│  system_prompt: "你是全栈 Python 开发专家..."         │
│  tools: 7 个子系统包装为 Tool                         │
│  max_steps: 12                                       │
│                                                      │
│  Step 1: optimize_uml     ─→ UmlOptimizer            │
│  Step 2: generate_code    ─→ LLM 代码生成             │
│  Step 3: validate_code    ─→ CodeValidator           │
│  Step 4: generate_tests   ─→ LLM 测试生成             │
│  Step 5: run_tests        ─→ pytest 子进程            │
│  Step 6: fix_code         ─→ CodeFixer               │
│  Step 7: write_files      ─→ 文件写入磁盘             │
│  Step *: request_review   ─→ 人工审核                 │
└─────────────────────────────────────────────────────┘
```

## 文件说明

### 核心工具

| 文件 | 组件 | 基础 Agent | 职责 |
|---|---|---|---|
| `uml_optimizer.py` | `UmlOptimizer` | `ReflectionAgent` | UML 设计优化：生成 → 交叉验证 → 修复 |
| `uml_tools.py` | `UmlValidationTool` | `Tool` | UML 跨图引用验证（类图↔时序图↔组件图） |
| `code_validator.py` | `CodeValidator` | `ReActAgent` (FC) | 代码验证：语法检查 → 导入检查 → 模块运行 → 修复 |
| `code_fixer.py` | `CodeFixer` | `ReflectionAgent` | 测试驱动修复：pytest → 分析失败 → 修复源码 → 重试 |
| `dev_system.py` | `DevSystem` | 编排层 | ⚠️ 已被对话 Agent 模式替代，保留供参考 |

### 对话 Agent 层

| 文件 | 说明 |
|---|---|
| `conversation_tools.py` | 将子 Agent 封装为对话 Agent 可调用的 Tool。包含 7 个工具 + `create_conversation_tools()` 工厂函数 |
| `demo_dev_system.py` | 完整 Demo，5 个演示覆盖中断控制、代码验证、工具检查、真实 LLM 完整开发（已移至 `agentTest/`） |

## 子 Agent 详解

### 1. UmlOptimizer (`uml_optimizer.py`)

```
ReflectionAgent(
  initial: 根据需求 + 现有图 → 生成完整 UML 设计
  reflect: UmlValidationTool 程序化验证 + LLM 语义审查
  refine:  根据反馈修正设计
)
→ 输出: diagrams JSON + design_constraints
```

**使用**:
```python
from app.agent_base.tools.my_tools import UmlOptimizer

llm = BaseAgentsLLM.from_settings()
optimizer = UmlOptimizer(llm, max_iterations=3)
result = await optimizer.optimize(
    diagrams=existing_diagrams,   # 现有图列表，None 表示从零生成
    instructions="增加支付模块，完善异常处理",
)
# result = {diagrams: [...], design_constraints: {...}, ...}
```

### 2. CodeValidator (`code_validator.py`)

```
ReActAgent(FC) + 验证工具链
  ├─ check_imports  → ast.parse + subprocess import
  ├─ run_module     → python -c "import module"
  ├─ run_bash       → 安全沙箱命令
  ├─ analyze_error  → 错误结构分析
  ├─ diff_code      → 修改差异对比
  └─ finish_validation → 完成信号
→ 输出: {success, final_code, summary, steps}
```

**使用**:
```python
from app.agent_base.tools.my_tools import CodeValidator

validator = CodeValidator(llm, max_rounds=5, change_ratio=30)
async for progress in validator.validate_stream(
    code_files={"app.py": "def main(): pass"},
    task_description="Validate generated code",
):
    if "result" in progress:
        print(progress["result"]["success"])
```

### 3. CodeFixer (`code_fixer.py`)

```
ReflectionAgent(
  initial: 分析源码和测试
  reflect: pytest 子进程验证
  refine:  根据失败信息修复源码
)
→ 输出: {success, final_source, test_output, pass_rate}
```

**使用**:
```python
from app.agent_base.tools.my_tools import CodeFixer

fixer = CodeFixer(llm, max_iterations=5)
result = await fixer.fix(
    source_code={"app.py": "def add(a,b): return a-b"},
    test_code={"test_app.py": "from app import add\ndef test():\n assert add(1,2)==3"},
)
# 自动修复 add 函数：a-b → a+b，直到测试通过
```

### 4. 对话 Agent (`conversation_tools.py`)

将所有子 Agent 封装为对话 Agent 的工具：

```python
from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
from app.agent_base.agents.react_agent import ReActAgent

# 1. 创建工具集
llm = BaseAgentsLLM.from_settings()
tools, review_mgr = create_conversation_tools(llm, source_dir="src/", test_dir="tests/")
registry = ToolRegistry()
for t in tools:
    registry.register_tool(t)

# 2. 创建对话 Agent
agent = ReActAgent(
    name="FullStackDev",
    llm=llm,
    tool_registry=registry,
    system_prompt="你是全栈 Python 开发专家...",
    max_steps=12,
    use_native_fc=True,
)

# 3. 运行
async for progress in agent.arun_stream("创建一个计算器系统"):
    print(progress.to_dict())
```

#### 可用工具列表

| 工具名 | 子 Agent | 输入 | 输出 |
|---|---|---|---|
| `optimize_uml` | UmlOptimizer | `diagrams_json`, `instructions` | `{diagrams, design_constraints}` |
| `generate_code` | LLM 直接调用 | `diagram_json`, `language` | `{files: {...}, count}` |
| `validate_code` | CodeValidator | `code_files_json`, `task` | `{success, final_code, summary}` |
| `generate_tests` | LLM 直接调用 | `source_files_json`, `test_cases` | `{files: {...}, count}` |
| `fix_code` | CodeFixer | `source_files_json`, `test_files_json`, `task` | `{success, final_source, pass_rate}` |
| `run_tests` | pytest 子进程 | `source_files_json`, `test_files_json` | `{output, passed, total, all_passing}` |
| `write_files` | 文件 I/O | `files_json`, `file_type` | `{written, count, directory}` |
| `request_review` | ReviewManager | `review_type`, `title`, `content`, `question` | 人工审核结果 |

## 工具桥接

`services/tools.py` 中的验证函数（`_check_imports`, `_run_module`, `_run_bash` 等）通过 `conversation_tools.py` 的 `_AsyncTool` 适配器封装为 BaseAgents `Tool` 对象。关键点：

- `_AsyncTool.run()` 返回 coroutine，由 `ToolRegistry.aexecute_tool_with_params()` await
- `_AsyncTool.to_openai_schema()` 直接返回 JSON Schema，绕过 `ToolParameter` 限制
- ReActAgent FC 循环中用 `aexecute_tool_with_params` 而非 `execute_tool_with_params`

## 中断与审核

### 中断 (Hook 机制, `base/core/hooks.py`)

框架内置 `InterruptHook`，通过运行时上下文注入 stop 标志；命中后抛 `AgentInterrupted`，编排层捕获即可。

```python
from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
from app.agent_base.core.exceptions import AgentInterrupted

token = set_runtime(AgentRuntime(stop_check=lambda: check_user_stop_flag()))
try:
    async for progress in agent.arun_stream(task):
        ...
except AgentInterrupted:
    # 用户中断
finally:
    reset_runtime(token)
```

### RequestReviewTool (`base/tools/review.py`)

```python
# 对话 Agent 自主决定何时请求审核
# Agent 调用 request_review(type="code", title="SQL注入修复", content="...", question="这个修复对吗?")
# → ReviewManager 创建 Future → 编排层推送到前端
# → 前端 resolve(review_id, "批准，但还要加参数化查询")
# → Agent 收到 feedback 继续
```

## 运行 Demo

```bash
cd backend
python app/agent_base/agentTest/demo_dev_system.py
```

5 个演示：
1. **中断控制** — InterruptHook 在指定步数后停止
2. **代码验证** — CodeValidator Mock 修复语法错误
3. **工具检查** — 7 个工具注册 + 快速验证
4. **完整开发** — 真实 LLM 驱动 UML→代码→验证→测试→保存 全流程
5. **可中断开发** — 对话 Agent + 中断信号

## 与 Pipeline 的关系

| Pipeline 阶段 | Agent 替代 |
|---|---|
| Stage 1: UML 优化 | `UmlOptimizer` (ReflectionAgent) |
| Stage 3: 代码生成 | 对话 Agent `generate_code` tool |
| Stage 3b: ReAct 验证 | `CodeValidator` (ReActAgent FC) |
| Stage 5: 测试生成 | 对话 Agent `generate_tests` tool |
| Stage 5b: 测试验证 | `CodeValidator` 同上 |
| Stage 6: 测试修复 | `CodeFixer` (ReflectionAgent) |

Pipeline 的固定 6 阶段编排被对话 Agent 的自主推理替代。
Agent 不再被写死的 `Phase 1→2→3` 限制，而是根据每一步的结果自主决定下一步。
