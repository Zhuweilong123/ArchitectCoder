# 插件架构与扩展契约

> 状态：当前实现说明
> 更新日期：2026-09-11
> 适用范围：当前仓库 HEAD。本文描述运行时代码的实际边界；代码提交继续演进时，以源码和配置为最终依据。

## 1. 当前边界

插件机制把稳定的 Agent 端口与可替换的领域实现分开：

- `backend/app/agent_base/core/` 定义端口、协议、生命周期管理器和降级实现。
- `backend/app/trace/tracing.py` 保留 Trace 的运行时端口、会话生命周期和协程上下文桥接。
- `extensions/` 保存具体 provider、存储、序列化和领域算法。
- `backend/app/main.py` 只负责加载插件拥有的 HTTP router，并统一附加认证依赖。
- `backend/config/` 是插件开关和 provider 入口的配置来源。

当前由统一管理器维护五个插件槽位：`orchestration`、`memory`、`trace`、`evals`、`knowledge_graph`。

插件不是动态扫描出来的。槽位由 `DEFAULT_PLUGIN_SPECS` 静态声明，provider 通过配置指定的 `module:factory` 入口加载。

## 2. 代码布局

```text
backend/
├── config/
│   ├── settings.py              # Settings、环境变量和缓存入口
│   ├── plugin_defaults.py       # 五个内置 provider 的唯一默认值来源
│   └── agent_config.py          # 单个 Agent 的运行参数模型
└── app/
    ├── agent_base/
    │   └── core/
    │       ├── plugins.py       # PluginSpec、PluginManager、PluginState
    │       ├── orchestration.py # OrchestrationPort 和 NoOpOrchestrator
    │       ├── memory.py        # MemoryPort 和 NoOpMemory
    │       ├── evals.py         # EvalProvider 和 NoOpEvalProvider
    │       └── knowledge_graph.py # KnowledgeGraphProvider 和 NoOp 实现
    ├── trace/
    │   └── tracing.py           # TraceProvider、TraceSession 和 NoOpTraceProvider
    └── main.py                  # 扩展 router 的应用挂载点

extensions/
├── orchestration/               # 规划/探索 provider
├── memory/                      # SQLite memory provider
├── trace/                       # JSONL 写入、查询、回放和 Trace API
├── evals/                       # 评测 provider、目录、运行器和 API
└── knowledge_graph/             # SQLite 图索引、检索和图工具
```

核心层不得导入具体扩展实现。扩展可以依赖自己的存储和第三方库，但只能通过对应端口与 Agent 主流程交互。

## 3. 管理器生命周期

实现位置：`backend/app/agent_base/core/plugins.py`。

```text
领域 loader
    │
    ▼
PluginManager.load(name, settings, kwargs)
    │
    ├─ 读取 enabled/provider 配置
    ├─ 未启用或 provider=none/noop/disabled ──► 返回 None
    ├─ 按 module:factory 导入并创建实例
    ├─ 校验 required_methods
    └─ 记录 loaded 或 unavailable ──► 领域层选择 NoOp
```

`PluginManager` 进程内单例由 `get_plugin_manager()` 提供。`load()` 具有以下边界：

- 未知槽位抛出 `KeyError`。
- provider 为空、`none`、`noop` 或 `disabled` 时标记为 `disabled`，不导入扩展。
- 导入、工厂创建或契约校验失败时标记为 `unavailable`，记录日志并返回 `None`。
- `settings=None` 时读取缓存的 `backend.config.get_settings()`。
- 每个 provider 工厂收到 `settings=settings` 以及领域 loader 传入的 `kwargs`。

可选贡献通过 `load_contribution(name, method, ...)` 调用。provider 缺失、贡献方法不存在或调用失败时返回调用方指定的默认值（未指定则为空列表）。

`status()` 为全部槽位返回最近状态：

```text
not_loaded | loaded | disabled | unavailable
```

状态目前只供 Python 内部调用，没有插件管理后台或前端配置页。

## 4. 五个内置槽位

| 槽位 | 配置开关 | provider 配置 | 默认入口 | 必需方法 | 插件 router |
|---|---|---|---|---|---|
| `orchestration` | `agent_orchestration_enabled` | `agent_orchestrator_provider` | `extensions.orchestration:create` | `prepare` | 无 |
| `memory` | `agent_memory_enabled` | `agent_memory_provider` | `extensions.memory:create` | `recall`, `archive`, `reinforce` | 无 |
| `trace` | `agent_trace_enabled` | `agent_trace_provider` | `extensions.trace:create` | `create` | `extensions.trace.api:router` |
| `evals` | `agent_evals_enabled` | `agent_evals_provider` | `extensions.evals:create` | `list_cases`, `get_case`, `run_case`, `list_results` | `extensions.evals.full_api:router` |
| `knowledge_graph` | `agent_knowledge_graph_enabled` | `agent_knowledge_graph_provider` | `extensions.knowledge_graph:create` | `rebuild_project`, `search_diagrams`, `map_project`, `locate`, `expand`, `impact`, `diff` | 无 |

领域 loader 与端口对应关系如下：

- `load_orchestrator()` 返回 `OrchestrationPort`；失败时使用 `NoOpOrchestrator`。
- `load_memory()` 返回 `MemoryPort`；失败时使用 `NoOpMemory`。
- `load_trace()` 返回 Trace provider；失败时使用 `NoOpTraceProvider`。
- `load_evals()` 返回 `EvalProvider`；失败时使用 `NoOpEvalProvider`。
- `load_knowledge_graph()` 返回 `KnowledgeGraphProvider`；失败时使用 `NoOpKnowledgeGraphProvider`。

Orchestration、Memory、Trace 和 Evals loader 会在 provider 外包一层运行时保护，避免可选 provider 的执行异常破坏主 Agent 流程。Knowledge Graph 的 NoOp 实现对禁用能力返回空结果或明确的 disabled 错误。

`AGENT_MAIN_SUBAGENT_ENABLED` 是主 Agent 子代理能力的独立开关，不等同于 `orchestration` provider 开关。

## 5. Provider 契约

provider 通过 `module:factory` 入口暴露可调用工厂，例如：

```text
extensions.memory:create
```

最小形态：

```python
class CustomMemoryProvider:
    async def recall(self, request): ...
    async def archive(self, request): ...
    async def reinforce(self, memory_ids, project_id=""): ...


def create(*, settings, **kwargs):
    return CustomMemoryProvider()
```

工厂必须返回包含该槽位全部 `required_methods` 的对象。额外方法可以由扩展自己使用，也可以通过 `load_contribution()` 作为可选能力暴露；核心不会因为额外方法缺失而拒绝 provider。

各端口的请求/结果模型仍归核心所有。例如 Memory 的 `MemoryRecallRequest`、Trace 的 `TraceSession` 和 Evals 的批次请求模型，用于保证扩展替换时主流程不变。

## 6. 配置来源与运行 profile

默认 provider 只在 `backend/config/plugin_defaults.py` 定义一次：

```python
DEFAULT_ORCHESTRATION_PROVIDER = "extensions.orchestration:create"
DEFAULT_MEMORY_PROVIDER = "extensions.memory:create"
DEFAULT_TRACE_PROVIDER = "extensions.trace:create"
DEFAULT_EVALS_PROVIDER = "extensions.evals:create"
DEFAULT_KNOWLEDGE_GRAPH_PROVIDER = "extensions.knowledge_graph:create"
```

`backend/config/settings.py` 将这些默认值映射为 Settings 字段。环境变量覆盖 Settings 默认值，进程内由 `get_settings()` 缓存；修改配置后需要重启 backend。

仓库中的 `backend/.env.example` 是保守部署 profile：

- `AGENT_MEMORY_ENABLED=true`
- `AGENT_TRACE_ENABLED=true`
- `AGENT_EVALS_ENABLED=true`
- `AGENT_ORCHESTRATION_ENABLED=false`
- `AGENT_KNOWLEDGE_GRAPH_ENABLED=false`

这只是示例部署配置，不改变 `Settings` 中 orchestration 和 knowledge graph 的代码默认值（当前均为 `true`）。

示例：

```env
AGENT_MEMORY_ENABLED=false
AGENT_TRACE_PROVIDER=extensions.trace:create
```

## 7. Router 挂载边界

插件 HTTP router 由扩展包自己定义，当前仅有两个：

- `extensions.trace.api:router`
- `extensions.evals.full_api:router`

`PluginManager.load_router()` 只导入 router，不加载 provider。`backend/app/main.py` 启动时分别调用 `load_router("trace")` 和 `load_router("evals")`，并通过 `Depends(require_auth)` 统一挂载认证依赖。

即使对应 provider 被禁用，router 仍会尝试挂载，使客户端进入扩展自己的禁用处理（按 endpoint 返回空结果或 503），而不是因为路由未注册而得到 404。router 导入失败只记录日志并跳过挂载。

因此 API 所有权如下：扩展拥有领域路由和 provider 适配；主应用只负责生命周期启动、认证依赖和路由注册，不承载评测或 Trace 的领域实现。

## 8. 故障与降级

插件故障分为两层：

1. **加载阶段**：导入、工厂创建或方法校验失败，管理器记录 `unavailable`，领域 loader 返回 NoOp。
2. **运行阶段**：provider 方法抛错，由领域层 resilience wrapper 记录日志并返回空上下文、空结果或保持端口约定的降级值。

评测 provider 的部分操作在禁用时会明确抛出“provider disabled”错误，这是为了让 API 层返回可识别的不可用状态；Trace 和 Memory 的存储异常不会阻断当前 Agent 回合。

降级是可靠性边界，不代表 provider 健康检查。当前没有独立的插件健康检查、热重载或运行时切换机制。

## 9. 所有权规则

### `backend/config`

- 环境变量和部署 Settings
- 五个插件的 enable/provider 选择
- 路径、超时和共享基础设施默认值
- Agent 实例配置模型

### `backend/app`

- 稳定端口、协议和请求/结果模型
- 通用插件生命周期和运行时编排
- 认证、权限和传输适配
- Trace 的运行时会话/事件桥接

### `extensions`

- provider 工厂及具体算法
- SQLite/JSONL 等存储实现
- 评测目录、运行器、批次和检查器
- Trace 格式、查询和回放
- 知识图谱索引、检索和图工具

核心代码不得通过具体扩展模块导入绕过管理器；扩展也不得反向依赖 FastAPI 传输对象来实现核心端口。

## 10. 当前限制与新增插件流程

- `DEFAULT_PLUGIN_SPECS` 是静态注册表，不支持目录扫描或第三方包自动发现。
- 没有公开的插件状态/管理 API，也没有前端插件配置界面。
- provider 切换需要修改环境配置并重启 backend。
- router 挂载与 provider 加载是两个独立步骤；新增带 API 的插件必须同时在 `PluginSpec.router_provider` 和 `backend/app/main.py` 的挂载流程中接入。
- 本文不固化旧版本测试通过数量；验证结果应以当前代码对应的 CI/本地命令为准。

新增插件时，按以下顺序完成边界接入：

1. 在 `backend/app/agent_base/core/` 定义端口与 NoOp 实现。
2. 在 `extensions/<name>/` 提供 `create(**kwargs)` 和具体实现。
3. 在 `plugin_defaults.py` 增加默认入口，并在 `Settings` 增加开关/入口字段。
4. 在 `DEFAULT_PLUGIN_SPECS` 增加 required methods 和可选 router。
5. 在领域组装点调用对应 loader；若有 HTTP API，再由 `main.py` 挂载 router。
6. 同步本文件和 `docs/current-architecture.md` 的边界说明。
