# 插件架构与扩展契约

> 状态：当前实现说明
> 更新日期：2026-10-07
> 适用范围：当前仓库 HEAD。本文描述运行时代码的实际边界；代码提交继续演进时，以源码和配置为最终依据。

## 1. 当前边界

插件机制把稳定的 Agent 端口与可替换的领域实现分开：

- `backend/app/agent_base/host_api/` 定义宿主协议；`adapters/` 负责加载和降级；`core/` 管理通用生命周期、事件路由和操作上下文。
- `backend/app/runtime/trace_session.py` 管理 Trace 会话与后台资源生命周期；`runtime/tool_outputs.py` 独立管理工具结果续读。
- `extensions/` 保存具体 provider、存储、序列化和领域算法。
- `backend/app/main.py` 只负责加载插件拥有的 HTTP router，并统一附加认证依赖。
- `extensions/<plugin>/plugin.json` 是插件声明及默认配置的来源；`backend/config/` 负责解析和部署覆盖。

当前由统一管理器维护七个插件槽位：`orchestration`、`memory`、`trace`、`evals`、`knowledge_graph`、`design_contract`、`skills`。Skill 的协议与版本快照详见 [Skill 插件](skills-plugin.md)。

演化目标是保持主流程稳定，通过 slot 接入新增能力。新扩展在自己的 `plugin.json` 中声明 slot、接口、阶段贡献和配置，由统一发现与调度机制接入；领域请求、结果和策略放在扩展自己的 `plugin_api.py` 与实现中。只有新增通用执行语义时才调整主流程，新增领域能力不应增加核心分支或插件专用字段。

扩展不得直接导入 `app.agent_base.core`。公共阶段、Hook 数据和 Contribution 声明位于 `host_api/lifecycle.py`，公共异常位于 `host_api/errors.py`；事件、运行上下文、后台任务、操作范围、模型创建与工具 Provider 调度通过 `host_api/services.py` 的 `HostServices` 接口访问。实现由 `adapters/host_services.py` 绑定到核心，并在应用装配入口安装；请求可通过 `host_services_scope` 或 `ExtensionContext.host_services` 注入替代实现，子任务继承当前请求绑定。该接口只承载通用宿主能力，不吸收各插件的领域接口。

Contribution 是纯数据声明；处理器解析、校验与注册由宿主执行，插件不操作核心 HookRegistry 或 ContextVar token。稳定的工具基类与执行入口仍属于宿主提供的公共工具能力。

七个内置扩展与新增扩展均通过自己的 `plugin.json` 声明。系统扫描仓库 `extensions/` 和 `PLUGIN_ROOTS` 指定根目录的直接子目录，发现元数据后合并部署配置，再导入工厂与贡献声明并编译计划。Provider 保持按需创建。阶段贡献可以写入 JSON，或通过显式 contribution_loader 提供；路由入口也由插件声明。`DEFAULT_PLUGIN_SPECS` 仅是从插件文件生成的兼容快照。详见[目录发现与独立配置](plugin-discovery.md)和[阶段贡献说明](plugin-lifecycle.md)。

全部内置领域 provider 的协议接口及已声明可选能力均编译为 `service` 贡献。加载后的 provider 调度适配器按请求选择接口，保留原有触发条件和降级边界；同步和异步调用分别通过 `invoke` / `ainvoke`。公共主流程固定为 13 个阶段，领域接口继承当前操作阶段，无活动操作时采用声明的默认阶段。操作通过 ID、父操作 ID、类型、范围和状态表达嵌套关系；审核、异常、取消与后台完成属于独立通知，不能把执行结束解释为业务审批完成。阶段发布不会自动执行服务，也不会因嵌套服务调用再次广播阶段。

自动参与阶段需要另外声明 `contributions`。记忆插件通过这些贡献完成召回、证据观察、上下文刷新及后台归档；宿主只提供通用 sections、结构化工具结果、当前消息位置和最终任务通知 `task_after`。`core/extension_context.py` 绑定请求能力并隔离插件状态，不包含领域策略。后续修改记忆行为应留在 `extensions/memory`，而不是向主循环增加记忆条件分支。

## 2. 代码布局

```text
backend/
├── config/
│   ├── settings.py              # Settings、环境变量和缓存入口
│   ├── plugin_catalog.py        # 无代码导入的元数据扫描与配置合并
│   ├── plugin_defaults.py       # 从插件声明生成的默认入口兼容名称
│   └── agent_config.py          # 单个 Agent 的运行参数模型
└── app/
    ├── agent_base/
    │   ├── host_api/            # 宿主上下文、通用请求与 Trace 协议
    │   ├── adapters/            # Provider 加载、校验、降级与宿主能力适配
    │   └── core/
    │       ├── plugins.py       # PluginSpec、PluginManager、PluginState
    │       ├── observability.py # 协程上下文、事件路由和 span
    │       └── background_tasks.py # 通用后台任务调度与结算
    ├── runtime/
    │   ├── trace_session.py     # Provider 无关的会话生命周期
    │   └── tool_outputs.py      # 有界工具结果存储与会话隔离
    └── main.py                  # 扩展 router 的应用挂载点

extensions/
├── skills/                      # plugin.json、文件技能与资源快照 provider
├── orchestration/               # plugin.json、规划/探索 provider
├── memory/                      # plugin.json、SQLite memory provider
├── trace/                       # plugin.json、JSONL 读写、回放与 API
├── evals/                       # plugin.json、评测 provider、运行器与 API
├── knowledge_graph/             # plugin.json、SQLite 图索引和图工具
└── design_contract/             # plugin.json、契约收集与分析
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
    ├─ 扫描声明并合并部署配置（启动及新增插件受控刷新）
    ├─ 读取 enabled/provider 配置
    ├─ 未启用或 provider=none/noop/disabled ──► 返回 None
    ├─ 必需依赖或已编译声明不可用 ──► 返回 None
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

`status()` 保留领域 provider 的最近加载状态。初始化声明与执行计划通过 `/api/plugins/plan`、`/api/plugins/graph` 提供只读查询；目前没有可编辑的插件管理后台。

## 4. 七个内置槽位

| 槽位 | 配置开关 | provider 配置 | 默认入口 | 必需方法 | 插件 router |
|---|---|---|---|---|---|
| `orchestration` | `agent_orchestration_enabled` | `agent_orchestrator_provider` | `extensions.orchestration:create` | `prepare` | 无 |
| `skills` | `agent_skills_enabled` | `agent_skills_provider` | `extensions.skills:create` | `list_skills`, `read_skill` | 无 |
| `design_contract` | `agent_design_contract_enabled` | `agent_design_contract_provider` | `extensions.design_contract:create` | `collect` | 无 |
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
- `load_skills()` 返回 `SkillProvider`；失败时使用 `NoOpSkillProvider`，任务通过 `SkillCatalog` 校验读取版本。

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

默认 Provider、默认启停、接口与参数由 `extensions/<plugin>/plugin.json` 定义。配置顺序是显式 Settings / 环境变量 > PLUGIN_CONFIG_FILE 部署覆盖 > 插件默认值。新插件无需修改核心注册表或 Settings：工厂可读取 settings.plugin_configs 中自己的参数，系统也提供默认 plugin_<id>_<parameter> 属性别名。

已有内置插件的 AGENT_* 配置保持兼容，声明文件中的 settings 字段指定原有字段名与参数前缀。plugin_defaults.py 的 DEFAULT_* 名称从插件文件生成；Typed Settings 保留公开字段，默认值读取插件声明。get_settings() 继续缓存，配置修改后重启 backend。

```dotenv
PLUGIN_ROOTS=["../examples/plugins"]
PLUGIN_CONFIG_FILE=config/plugin-overrides.example.json
```

扫描仓库扩展目录与指定额外根目录时，只读取直接子目录的 plugin.json；元数据阶段不导入插件代码。PLUGIN_MANIFEST_FILE 仍兼容旧的集中清单，但不能重复声明扫描到的插件。详情和配置示例见[插件目录发现](plugin-discovery.md)。

仓库中的 `backend/.env.example` 是保守部署 profile：

- `AGENT_MEMORY_ENABLED=true`
- `AGENT_TRACE_ENABLED=true`
- `AGENT_EVALS_ENABLED=true`
- `AGENT_SKILLS_ENABLED=true`
- `AGENT_ORCHESTRATION_ENABLED=false`
- `AGENT_KNOWLEDGE_GRAPH_ENABLED=false`

这些显式环境设置优先于插件默认配置和部署覆盖文件。若希望某项设置由部署文件管理，应移除相应的环境覆盖。

示例：

```env
AGENT_MEMORY_ENABLED=false
AGENT_TRACE_PROVIDER=extensions.trace:create
```

## 7. Router 挂载边界

插件 HTTP router 由扩展包自己定义，当前仅有两个：

- `extensions.trace.api:router`
- `extensions.evals.full_api:router`

`PluginManager.load_router()` 只导入 router，不创建 provider。`backend/app/main.py` 遍历扫描到的全部插件，加载其声明的 router，并通过 `Depends(require_auth)` 统一挂载认证依赖。新增带路由的插件无需再修改 main.py。

即使对应 provider 被禁用，router 仍会尝试挂载，使客户端进入扩展自己的禁用处理（按 endpoint 返回空结果或 503），而不是因为路由未注册而得到 404。router 导入失败只记录日志并跳过挂载。

因此 API 所有权如下：扩展拥有领域路由和 provider 适配；主应用只负责生命周期启动、认证依赖和路由注册，不承载评测或 Trace 的领域实现。

## 8. 故障与降级

插件故障分为两层：

1. **加载阶段**：导入、工厂创建或方法校验失败，管理器记录 `unavailable`，领域 loader 返回 NoOp。
2. **运行阶段**：provider 方法抛错，由领域层 resilience wrapper 记录日志并返回空上下文、空结果或保持端口约定的降级值。

评测 provider 的部分操作在禁用时会明确抛出“provider disabled”错误，这是为了让 API 层返回可识别的不可用状态；Trace 和 Memory 的存储异常不会阻断当前 Agent 回合。

降级是可靠性边界，不代表 provider 健康检查。当前支持新增插件的受控刷新，候选校验与归档成功后发布，新任务使用新快照，运行中的任务保持原快照。已有插件代码、配置、路由和编译贡献的更新需要重启；没有运行中替换已有实现或独立健康检查。详见[受控刷新](plugin-discovery.md#受控刷新与任务隔离)。

## 9. 所有权规则

### `backend/config`

- 环境变量和部署 Settings
- 扫描目录、部署覆盖文件和显式 enable/provider 选择
- 路径、超时和共享基础设施默认值
- Agent 实例配置模型

### `backend/app`

- 稳定端口、协议和请求/结果模型
- 通用插件生命周期和运行时编排
- 认证、权限和传输适配
- Trace 的运行时会话/事件桥接

### `extensions`

- plugin.json：插件身份、入口、接口、默认阶段、默认配置与依赖
- provider 工厂及具体算法
- SQLite/JSONL 等存储实现
- 评测目录、运行器、批次和检查器
- Trace 格式、查询和回放
- 知识图谱索引、检索和图工具

核心代码不得通过具体扩展模块导入绕过管理器；扩展也不得反向依赖 FastAPI 传输对象来实现核心端口。

## 10. 当前限制与新增插件流程

- 启动时扫描明确的根目录和直接子目录；内部子模块不被递归识别，也不自动安装第三方包。
- 已配置根目录中的新增插件可受控刷新生效；已有插件更新、配置修改、Provider 切换及路由变化需要重启，没有运行中替换或卸载已有实现。
- 提供只读计划、组织图与回放，前端尚不能编辑插件配置。
- 必需依赖按插件 ID 检查；声明版本用于展示，暂未解析版本范围。
- 扫描注册阶段贡献和接口声明，Agent 工具暴露及新的业务调用入口仍按现有契约装配。
- 验证结果以当前代码对应的 CI/本地检查为准。

新增普通扩展：

1. 在扫描根目录下创建插件目录，编写 plugin.json 和可导入的实现入口。
2. 声明 Provider、接口、阶段贡献、默认配置、依赖及可选路由。
3. 若位于外部根目录，在 PLUGIN_ROOTS 添加该根目录；启停与参数可放入部署覆盖文件。
4. 若位于已有扫描根目录且不增加 HTTP router，点击“扫描新插件”；其余情况重启后端。在插件架构中检查声明、状态及绑定，运行任务验证 Trace。
5. 可参考 [task_notes](../examples/plugins/task_notes/plugin.json)，无需修改 DEFAULT_PLUGIN_SPECS 或默认阶段映射表。

插件骨架生成、接口契约检查和独立试运行使用[插件开发工具包](plugin-development.md)；应用全量部署校验继续使用 `export_plugin_plan.py --check`。

新增核心领域端口时，仍先定义端口、请求结果模型与 NoOp 实现，再在业务装配点调用对应 loader。将接口绑定到公共阶段不会自动创建新的业务调用入口。
