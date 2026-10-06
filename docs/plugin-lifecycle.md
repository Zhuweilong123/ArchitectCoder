# 插件公共阶段、操作与执行回放

初始化发现插件及其显式接口贡献，校验后生成只读执行计划，再注册到 HookRegistry。公共阶段描述主流程，操作记录描述具体调用及父子关系，通知描述异常、取消、审核和后台工作。

## 13 个公共阶段

| 阶段 | 语义 | 阶段处理器模式 |
|---|---|---|
| initialize | Agent 与能力装配，可早于任务建立 | observer |
| prepare | 上下文、记忆及编排准备，可重复进入 | observer |
| run_start | 主循环执行区间开始 | observer |
| round_before | 一轮推理开始 | observer |
| model_before | 模型调用前，可修改消息或阻止请求 | observer / transform / control |
| model_after | 模型调用尝试结束，包含失败、取消及阻断 | observer / transform |
| tool_batch_before | 工具批次开始 | observer |
| tool_before | 单个工具执行前，可阻断 | observer / control |
| tool_after | 单个工具尝试结束，包含失败、取消及阻断 | observer / transform |
| tool_batch_after | 批次结果汇总及收敛决策 | observer / transform / control |
| round_after | 当前轮次结束 | observer |
| finalize | 整理执行结果或终止信息 | observer |
| run_end | 执行区间清理结束 | observer |

所有公共阶段也允许声明 service 的默认绑定，但发布阶段不会自动执行服务。模型和工具执行仍由核心执行器负责，调度图保留独立执行节点。

阶段可以重复、嵌套或按分支跳过，不要求每次调用从头走完 13 步。主 Agent 与子 Agent 都发布模型、工具、轮次与收尾边界。失败后的 model_after / tool_after 只运行观察器，避免对缺失的正常响应执行变换。

**run_end 只表示执行区间结束**，不表示审批已接受、业务任务已完成或后台归档已完成。业务 checkpoint 与操作状态分别管理。

## 操作、状态与通知

`core/operations.py` 使用协程上下文记录操作区间，包含：

- operation_id / parent_operation_id：嵌套关系，支持并发调用隔离。
- operation_kind：run、model、tool、plugin、subagent、prepare、initialize、review、background 等。
- scope：实际执行范围，例如 run、agent、request、background。
- run_id / stage：任务关联与当前公共阶段。
- interface_id / contribution_id：插件接口及计划贡献。
- status、耗时及失败信息：开始为 running，结束反映实际结果。

例如技能读取 plugin 操作的父操作是技能 tool；子 Agent 的父操作是调用它的工具，子任务仍使用自己的 run_id。后台任务继承创建时的父操作关联，拥有独立区间和最终状态。

`error`、`cancel`、`review_after`、`background_before`、`background_after` 是独立通知。计划将它们放在 notifications 中，Trace 写为 runtime_notification，不增加主流程阶段或拓扑节点。审核与后台完成可发生在 run_end 之后。

Trace 使用 operation、lifecycle_stage、plugin_contribution、runtime_notification 记录不同维度。初始化早于 Trace sink 建立、或 API 查询没有活动 Trace 时，不补造历史记录。服务调度记录不包含原始接口参数及返回正文；模型和工具原有 Trace 内容策略保持不变。

plugin_contribution 还保存执行绑定的 plugin_version 与 plugin_revision；版本及目录 Python 内容指纹随计划归档至 `PLUGIN_PLAN_DIR/history/<plan_id>.json`。声明编译和运行期实例加载诊断分别管理，不把运行期加载结果加入计划摘要。校验边界、诊断 API 与归档读取见[插件目录发现](plugin-discovery.md)。

## 领域接口统一调度

七个内置槽位的协议接口及已声明可选能力均编译为 service 贡献。Provider 加载后由调度适配器包装，调用点保留按需触发及领域降级规则。已安装计划缺少所需贡献时明确报错；无应用启动过程的 CLI / 库调用使用同一声明生成局部调度器。

| 插件 | 接口默认公共阶段 |
|---|---|
| skills | list_skills → initialize；read_skill → tool_before |
| memory | recall / reinforce → prepare；archive → finalize |
| orchestration | create_tools → initialize；prepare → prepare；explore → tool_before |
| trace | create → initialize；查询适配器及读取接口、replay → run_start |
| evals | 查询、运行、写入及归档等声明接口 → run_start |
| knowledge_graph | create_tools → initialize；查询、索引、重建、同步等 → tool_before |
| design_contract | collect / collect_facts / snapshot_from_facts → finalize |

默认阶段用于无活动操作的独立调用。有活动操作时，服务继承父操作当前阶段和范围；贡献记录同时保留 binding_stage（声明默认阶段）和 stage（实际阶段）。同一接口可以在不同公共阶段使用，无需新增领域阶段。

一次请求只执行指定接口；不运行其他同阶段服务，也不重复广播阶段观察器。图谱和编排工具工厂返回的工具绑定调度 Provider，避免绕过计划。未声明公共方法被拒绝；资源关闭、内部算法、存储实现及 Trace sink 写入原语保持原边界，避免递归追踪。

## 配置、发现与贡献

七个内置扩展与新增扩展都通过自己目录中的 plugin.json 声明。启动时扫描仓库 extensions/，以及 PLUGIN_ROOTS 指定的额外根目录的直接子目录；元数据扫描不导入插件代码。默认 Provider、必需和可选接口、异步标志及默认阶段均由插件自身维护。独立配置、扫描规则与完整字段见[插件目录发现](plugin-discovery.md)。

已有 AGENT_*_ENABLED / AGENT_*_PROVIDER 继续生效；PLUGIN_CONFIG_FILE 指定部署覆盖文件，优先级为显式 Settings / 环境变量 > 部署覆盖 > 插件默认值。PLUGIN_MANIFEST_FILE 兼容旧的额外插件集中清单，相对路径仍以 backend/ 解析：

```json
{"schema_version":1,"plugins":[{"name":"team_checks","provider":"my_team.checks:create","enabled":true,"interfaces":["query"],"interface_stages":{"query":"prepare"}}]}
```

接口未指定阶段时默认 run_start。声明 ID 和槽位不得冲突；必需依赖缺失、禁用、声明失败或形成循环时，插件标记 unavailable。元数据解析后，计划编译才导入指定模块并校验工厂，读取 JSON contributions 或显式 contribution_loader；不创建需要 LLM、项目或数据库的 Provider。discovered 表示声明可用，不代表已实例化或通过健康检查。代码声明函数示例，需在 plugin.json 的 contribution_loader 中配置其入口：

```python
from app.agent_base.core.hooks import HookEvent
from app.agent_base.core.lifecycle import Contribution

def list_contributions(*, settings=None):
    return (Contribution(id="team_checks.inspect", stage=HookEvent.TOOL_BEFORE,
        handler="my_team.checks:inspect_tool", mode="control",
        priority=60, fail_closed=True),)

def inspect_tool(context):
    return None  # 或返回该阶段支持的 HookDecision
```

贡献 ID 全局唯一，handler 使用 module:callable。同阶段 before / after 排序依赖优先于数值优先级，其余按优先级降序、ID 排序。重复 ID、缺失或跨阶段依赖及循环依赖均报错。单插件导入或声明失败标记 unavailable，不安装其贡献并保留原因；清单格式错误使初始化失败。

- observer 接收独立快照，不能修改消息或访问运行时活对象；失败不阻断执行，返回值不参与控制。
- transform 修改阶段数据；工具反馈可替换，原始结果及证据保持独立。
- control 返回阶段支持的 HookDecision；前置短路后观察器仍执行，批次收尾优先于恢复。
- service 使用独立 Invocation 保存本次目标、参数及结果。贡献 scope 为 invocation，不表示创建永久实例；操作 scope 描述实际所在执行范围。

同步和异步阶段处理器分别使用 trigger / emit 与 atrigger / aemit；服务分别使用 invoke / ainvoke。异步调度按计划顺序等待贡献；同步路径遇到异步处理器记录明确错误，不阻塞活动事件循环。任务状态保存在 context.runtime，不放入模块级可变变量。

## 兼容与生成文件

旧标识作为单一别名解析，例如 llm_before → model_before、llm_after → model_after、run_finalize → finalize、context_prepare / memory_reinforce → prepare、skill_read / graph_query → tool_before。新计划和记录只写规范阶段，不同时派发新旧标识。手工 Hook 注册仍兼容，运行期间新增注册不属于初始化计划快照。

后端默认生成 backend/.architectcoder/plugins/，可配置 PLUGIN_PLAN_DIR：

| 文件 | 内容 |
|---|---|
| plugin-plan.json | 插件声明来源、版本、槽位、依赖、参数名称、状态、接口绑定、13 个阶段及独立通知 |
| plugin-schedule.mmd | 公共主循环、模型与工具节点、阶段贡献 |
| plugin-organization.mmd | 插件、接口与默认公共阶段的组织关系 |

三份文件来自同一计划并携带 plan_id。服务之间没有自动执行顺序；静态连线表示默认绑定，实际父子关系以 Trace 为准。生成文件只读，修改配置需重启后端。

仓库根目录可离线导出：

```bash
python backend/export_plugin_plan.py --output ./temp/plugin-plan
```

只读 API 沿用现有认证：GET /api/plugins/plan、GET /api/plugins/graph?view=schedule、GET /api/plugins/graph?view=organization。

## 前端与回放

Trace 旁的“插件架构 / Plugin architecture”打开组织图和调度图，支持中英文、筛选、缩放、定位、刷新及 JSON 导出。调度图保留 13 个公共阶段及模型、工具执行节点；领域接口详情标明默认阶段和按需调用语义。

回放按 session 和 run_id 选择任务，上一步、下一步和列表保留 JSONL 顺序及重复阶段。操作树合并开始和结束记录，展示最终状态及父子关系，包含跨 run_id 子任务；点击操作定位对应任务记录。详情显示操作及父操作 ID、类型、范围、实际和默认阶段、耗时、控制原因与原始事件。通知可查看详情，无独立主流程节点。

回放只读取历史，不执行模型或工具。plan_id 缺失、混合或不同于当前计划时保留详情，关闭高亮；当前不加载历史计划快照。旧 Trace 无操作 ID 时仍可查看阶段记录，但不能恢复操作树。贡献累计耗时是接口耗时之和，不代表任务总耗时。后端更新后需运行新任务才能看到新记录。
