# DevAgent 当前架构基线

> 状态：当前实现说明（非历史方案）
>
> 代码基线：`65f8fab`（`dev-3.0`）
>
> 本文是当前代码的单一入口。旧版本基线、优化过程和评测数字请分别参阅文末的历史文档。

## 1. 总体流程

```text
WebSocket / Evaluation / future HTTP or CLI
              |
              v
        app/agent_base/assembly.py
              |
              +-- runtime environment + command executor
              +-- memory / plugin providers
              +-- foundation tools + review tools
              +-- prompt and context budget
              v
          ReActAgent
              |
              v
        AgentExecution
              |
              +-- run lifecycle / checkpoint / approval
              +-- structured ToolResult and evidence
              +-- trace and memory archive
              v
        transport-neutral outcome
```

`agent_chat_ws.py` 只负责 WebSocket 鉴权和协议适配；会话协调由
`app/services/chat_session.py` 负责，单次 Agent 执行由
`app/services/agent_execution.py` 负责。评测入口复用同一套 assembly，不应再维护
一套独立的工具或提示词装配逻辑。

## 2. 稳定边界

| 边界 | 当前所有者 | 说明 |
|---|---|---|
| Agent 组合 | `app/agent_base/assembly.py` | 统一创建 LLM、工具、运行时、记忆和提示词 |
| Agent 循环 | `app/agent_base/agents/react_agent.py` | 推理、工具调用、预算和收敛控制 |
| 单次执行 | `app/services/agent_execution.py` | 生命周期、checkpoint、审批、证据和结果 |
| 会话/传输 | `app/services/chat_session.py`、`agent_chat_ws.py` | 会话状态与 WebSocket 适配分离 |
| 文件与变更 | `app/agent_base/tools/my_tools/file_system_tools.py`、`app/services/change_set.py` | 工作区边界、原子变更和 SHA 校验 |
| 扩展能力 | `extensions/*` + `app/agent_base/core/plugins.py` | 具体 memory、trace、evals、KG、orchestration 实现 |
| Trace 端口 | `app/trace/tracing.py` | 生命周期和 hook；存储/回放实现在 `extensions/trace` |

## 3. 当前基础工具契约

生产 Agent 的基础工具是：

| 工具 | 使用场景 |
|---|---|
| `list_files` / `read_file` / `search_text` | 受工作区约束的只读探索 |
| `apply_changes` | 文件创建、修改、删除、移动和复制 |
| `run_task` | `test`、`build`、`lint`、`format`、`typecheck`、`validate` 等标准任务 |
| `run_program` | 允许列表中的可执行文件 + 字面量参数 |
| `shell` | 最后的单条原生命令；禁止管道、串联、重定向、替换和嵌套 Shell |

执行路由固定为：`run_task` → `run_program` → `shell`。Windows 默认使用
原生 PowerShell；WSL Bash 只有在显式配置时启用。命令适配器和宿主策略位于
`app/runtime/command.py`，工具描述和路由规则位于 `assembly.py`。

## 4. 可选扩展

`extensions/` 是具体实现的唯一归属，当前内置入口为：

```text
extensions.orchestration:create
extensions.memory:create
extensions.trace:create
extensions.evals:create
extensions.knowledge_graph:create
```

应用层只依赖稳定端口：`MemoryPort`、`KnowledgeGraphProvider`、Trace
端口、评测端口和 orchestration 端口。扩展失效时应回退到对应的 no-op 或安全
降级实现，不得破坏主 Agent 执行链。

## 5. 上下文、记忆和 Trace

- 上下文预算由 `app/services/context_manager.py` 管理；它只负责本次请求的
  prompt 预算、历史压缩和恢复，不直接实现长期记忆。
- 长期记忆通过 `MemoryPort` 访问，具体 SQLite、BM25、生命周期和策略位于
  `extensions/memory`。
- Trace 的运行时 hook 位于 `app/trace/tracing.py`，JSONL 写入、读取和回放位于
  `extensions/trace`。
- 记忆、Trace 和上下文都是参考数据，当前用户指令优先级最高。

## 6. 文档使用规则

- 当前架构、工具边界和代码路径：本文。
- 插件加载和扩展所有权：`plugin-architecture-design.md`。
- 评测运行链路和指标：`evaluation-system.md`。
- memory、knowledge graph、trace 的领域细节：对应子系统设计文档。
- 旧版本基线和实施过程已从工作树移除；如需复盘，请通过 Git 历史查看对应提交。
