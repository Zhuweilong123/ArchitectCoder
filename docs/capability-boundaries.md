# Agent 能力边界

`agent_base/core/` 保留通用执行设施。插件领域接口可以存在于宿主侧，但具体规则、提示词、算法和存储实现由插件拥有。

| 归属 | 内容 | 依赖约束 |
|---|---|---|
| `backend/app/agent_base/ports/` | 请求、结果、Protocol | 不导入插件实现、加载器或宿主适配器 |
| `backend/app/agent_base/adapters/` | 配置加载、结果验证、降级、审核与只读模型调用适配 | 依赖协议和通用宿主能力，通过 PluginManager 加载插件 |
| `extensions/<plugin>/` | 领域规则、算法、提示词、解析、存储 | 通过协议及注入的回调使用宿主能力 |
| `backend/app/agent_base/assembly.py` | 生产组合入口 | 选中 provider 并注入能力；不实现插件策略 |

原 `core/memory.py`、`skills.py`、`orchestration.py`、`knowledge_graph.py`、`evals.py`、`contracts.py` 的数据模型和协议迁入 `ports/`，NoOp、加载器、目录捕获和验证迁入 `adapters/`。仓库内调用已迁移；自定义插件的旧 Python 导入也需要按这个区分更新。旧路径不保留领域文件作为转发层。

契约检查结果、审核上下文和失败分析上下文在 `ports/`。一致性规则、审核策略、失败分析报告和提示词、事实编排及 Python/Clang AST 实现归入 `extensions/design_contract/`。执行 broker 的命令适配仍在 `backend/app/runtime/language_runner.py`，由组合入口注入解析器。

契约插件清单新增三个可选异步接口，版本为 1.1.0：

- `evaluate(context)`：检查候选变更，返回 `ContractGateDecision`；检查阶段不写入图谱。
- `finalize(context, prior_result=None)`：宿主提交完成后触发，默认插件此时更新图谱。
- `analyze(context)`：组织失败证据和分析提示词，通过 `context.invoke(AnalysisRequest(...))` 调用只读模型能力。

审核上下文只包含工作区和变更快照、事件发送与审核回调。分析上下文只有结构化结果和模型回调。插件不再接收主 Agent、ChangeSet、审核管理器，也不读取 Agent 私有历史或模型状态。

只实现 `collect` 的旧 provider 仍可独立收集事实。作为生产审核 provider 使用时必须同时实现 `evaluate`；能力缺失返回 `inconclusive` 并阻止有变更的候选提交，不静默回退到另一套契约策略。缺失 `analyze` 时返回事实说明，不调用模型。服务端或本次任务显式禁用契约时仍按原禁用逻辑处理。

修改记忆召回、提取、生命周期、归档和注入策略，只需要修改 `extensions/memory/`。只有新增宿主能力或修改稳定协议时，才需要修改 `ports/`、`adapters/` 或组合入口。新接口必须在插件清单声明；阶段名称保持通用，不向核心枚举添加领域别名。
