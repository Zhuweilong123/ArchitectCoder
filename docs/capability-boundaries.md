# Agent 能力边界

`agent_base/core/` 保留通用执行设施。插件领域接口由插件公开，宿主只保留真正跨插件的生命周期和宿主能力协议。

包入口只按需导出框架通用类型，不导出领域加载器；导入宿主协议和基础 Agent 不加载任何扩展模块。memory、orchestration 和契约的旧领域加载/降级链已移除，生产实现统一走插件装配和执行贡献；仍被传输或工具适配使用的加载器保留在 adapters。

`execution.check` 的拒绝结果只能收紧。宿主保留最早拒绝的消息、停止原因和恢复提示，并在 `ExecutionRequest.rejections` 记录后续拒绝原因；贡献不能通过重新赋值 allowed=True 解除已有拒绝。这个规则由请求的 contribution_scope 与通用调度器共同保证，核心调度器不识别具体插件或领域 slot。

| 归属 | 内容 | 依赖约束 |
|---|---|---|
| `backend/app/agent_base/host_api/` | 主流程需要的宿主上下文、回调和编排请求 | 不导入插件实现、加载器或宿主适配器 |
| `backend/app/agent_base/adapters/` | 配置加载、结果验证、降级、审核与只读模型调用适配 | 依赖宿主协议，并通过插件公开 API 校验具体 provider |
| `extensions/<plugin>/plugin_api.py` | 插件自己的请求、结果和 Provider Protocol | 插件实现与对应适配器共同依赖；不放入核心流程 |
| `extensions/<plugin>/` | 领域规则、算法、提示词、解析、存储 | 通过宿主协议及注入的回调使用宿主能力 |
| `backend/app/agent_base/assembly.py` | 生产组合入口 | 选中 provider 并注入能力；不实现插件策略 |

记忆、技能、评测、知识图谱和设计契约的领域模型与 Provider Protocol 已分别归入各自插件的 `plugin_api.py`；宿主 `host_api/` 只保留跨边界的审核、分析、编排和生命周期上下文。NoOp、加载器和宿主适配仍在 `adapters/`。自定义插件应依赖对应插件的公开 API，不再从旧 `core` 或宿主 host_api 导入领域模型。

契约检查结果、审核上下文和失败分析上下文在 `host_api/`。一致性规则、审核策略、失败分析报告和提示词、事实编排及 Python/Clang AST 实现归入 `extensions/design_contract/`。执行 broker 的命令适配仍在 `backend/app/runtime/language_runner.py`，由组合入口注入解析器。

契约插件清单新增三个可选异步接口，版本为 1.1.0：

- `evaluate(context)`：检查候选变更，返回 `ContractGateDecision`；检查阶段不写入图谱。
- `finalize(context, prior_result=None)`：宿主提交完成后触发，默认插件此时更新图谱。
- `analyze(context)`：组织失败证据和分析提示词，通过 `context.invoke(AnalysisRequest(...))` 调用只读模型能力。

审核上下文只包含工作区和变更快照、事件发送与审核回调。分析上下文只有结构化结果和模型回调。插件不再接收主 Agent、ChangeSet、审核管理器，也不读取 Agent 私有历史或模型状态。

只实现 `collect` 的旧 provider 仍可独立收集事实。作为生产审核 provider 使用时必须同时实现 `evaluate`；能力缺失返回 `inconclusive` 并阻止有变更的候选提交，不静默回退到另一套契约策略。缺失 `analyze` 时返回事实说明，不调用模型。服务端或本次任务显式禁用契约时仍按原禁用逻辑处理。

修改记忆召回、提取、生命周期、归档和注入策略，只需要修改 `extensions/memory/`。只有新增宿主能力或修改跨插件上下文时，才需要修改 `host_api/`、`adapters/` 或组合入口。新领域请求和结果应先放入对应插件的 `plugin_api.py`，并在插件清单声明接口；阶段名称保持通用，不向核心枚举添加领域别名。
