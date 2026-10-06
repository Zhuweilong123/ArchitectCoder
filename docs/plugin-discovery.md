# 插件目录发现与独立配置

所有扩展通过自己目录中的 `plugin.json` 声明身份、Provider、接口、默认阶段、配置、依赖和路由。七个内置扩展与额外扩展使用同一解析及加载流程。核心保留领域端口和业务装配点，不再维护内置插件接口与阶段的硬编码表。

## 扫描范围与启动流程

系统始终扫描仓库 `extensions/`。`PLUGIN_ROOTS` 可添加其他根目录，格式为 JSON 数组；相对路径以 `backend/` 解析：

```dotenv
PLUGIN_ROOTS=["../examples/plugins"]
PLUGIN_CONFIG_FILE=config/plugin-overrides.example.json
```

每个根目录只扫描直接子目录中的 `plugin.json`。普通代码目录和插件内部子模块不被识别为新插件；不递归扫描，也不安装 Python 包。Provider 与 handler 模块必须可导入，扫描根目录会加入插件加载所需的 Python 搜索路径。

```text
扫描声明文件（不导入插件代码）
  → 合并部署配置
  → 校验 ID、槽位、参数与依赖
  → 导入工厂和阶段处理器声明（不创建 Provider）
  → 编译执行计划并注册贡献
  → 业务调用时创建 Provider，接口按需执行
```

缺失的扫描根目录、损坏的 JSON、重复 ID 或槽位、无效部署参数会明确阻止配置生效；目录扫描与配置更新保持原子性。插件导入、阶段贡献或必需依赖失败则在计划中标记 unavailable，并排除对应贡献。disabled 插件仍可在计划中查看，阶段编译不导入其 Provider；路由保持独立挂载，沿用现有禁用响应。

第一版在启动时扫描；修改声明、扫描目录或部署配置后重启后端。前端“刷新”重新读取当前计划，不触发热加载。可选领域插件目录没有声明文件时，其 loader 使用现有 NoOp 降级，不要求该插件必须注册。

## 插件声明

文件位置示例：`extensions/memory/plugin.json`。额外插件无需添加到核心注册表：

```json
{
  "schema_version": 1,
  "id": "team_checks",
  "version": "1.0.0",
  "provider": "team_checks:create",
  "enabled_by_default": true,
  "interfaces": {
    "query": {"stage": "prepare", "required": true, "async": false}
  },
  "defaults": {"limit": 3},
  "dependencies": [],
  "contributions": [{
    "id": "team_checks.run_start",
    "stage": "run_start",
    "handler": "team_checks:observe_start",
    "mode": "observer"
  }]
}
```

| 字段 | 含义 |
|---|---|
| id / version | 插件唯一标识及声明版本；目前依赖按 ID 校验，不解析版本范围 |
| slot | 可选能力槽位，默认使用 id；ID 与槽位均不能冲突 |
| provider | `module:factory`，工厂接收 settings 与调用点提供的参数 |
| enabled_by_default | 默认启停，默认 true |
| interfaces | 方法名到阶段及标志的映射；默认 required=true、async=false、stage=run_start |
| interfaces.*.wrap_result | 将方法返回的公开接口适配器继续纳入同一调度，如 Trace query |
| defaults | 插件参数默认值，部署 config 只允许覆盖这里已声明的参数 |
| settings | 可选 enabled / provider 环境字段别名与参数 prefix，用于兼容已有配置 |
| dependencies | 必需插件 ID；缺失、禁用、循环依赖或上游声明失败使插件不可用 |
| optional_dependencies | 信息性可选依赖，不影响加载资格，具体降级由插件负责 |
| contributions | 阶段贡献，支持现有模式、优先级、before / after 与失败策略 |
| contribution_loader | 可选 `module:callable`，调用 list_contributions(settings=...) 生成贡献 |
| router | 可选 `module:router`；主应用统一挂载并附加现有 API 认证 |

公共阶段与通知、贡献排序规则见[插件生命周期](plugin-lifecycle.md)。required=false 的接口仅声明可选能力，不要求 Provider 必须实现。服务默认阶段来自插件声明，存在活动操作时仍继承当前阶段；扫描和阶段发布不会自动执行所有服务。

## 配置覆盖与工厂读取

生效顺序为：**显式 Settings / 已有环境变量 > 部署覆盖文件 > 插件默认值**。已有 AGENT_* 开关和 Provider 配置继续生效。希望通过部署文件管理某项设置时，移除对应的显式环境覆盖。

部署文件格式见 [plugin-overrides.example.json](../backend/config/plugin-overrides.example.json)：

```json
{
  "schema_version": 1,
  "plugins": {
    "memory": {"enabled": true, "config": {"recall_top_k": 5}},
    "team_checks": {"enabled": false, "config": {"limit": 8}}
  }
}
```

通用工厂通过 `settings.plugin_configs["team_checks"]["limit"]` 读取已合并的本插件配置，也可以访问默认参数别名 `settings.plugin_team_checks_limit`。内置插件在声明中配置原有字段别名，例如 memory 的参数前缀 agent_memory_，原来的 typed Settings 与调用点继续兼容。未知参数、未知插件或与默认值不符的参数类型会报错，不静默忽略。

`PLUGIN_MANIFEST_FILE` 仍支持旧的额外插件集中清单。它与目录扫描结果合并，不能重复声明已有插件；新扩展优先使用自己的 plugin.json。`plugin_defaults.py` 与 `DEFAULT_PLUGIN_SPECS` 只保留从插件文件生成的兼容入口，不再是独立声明来源。

## 示例与观察

[task_notes 示例](../examples/plugins/task_notes/plugin.json) 包含一个按需服务接口与 run_start 观察贡献。设置 `PLUGIN_ROOTS=["../examples/plugins"]` 并重启后端，即可在插件架构中看到它；开启 Trace 后运行一次任务，可以查看 task_notes.run_start 的执行记录。

插件计划包含声明来源、版本、槽位、依赖、配置参数名称和接口绑定；不导出部署参数值。插件架构面板展示这些信息。组织图、调度图与回放沿用同一个 plan_id。

插件架构面板的“导出 HTML”生成单个离线文件 `plugin-architecture.html`，无需后端、CDN 或其他资源即可在浏览器打开。文件包含完整的组织图和调度图，支持切换视图、筛选插件、缩放、拖动空白区域平移和点击节点查看详情，并以导出时的视图与插件筛选作为初始状态。节点详情保留插件版本、内容指纹、接口声明、排序关系及同一计划下已加载的诊断快照；独立通知也可展开查看。导出的是只读架构快照，任务 Trace 回放记录仍在系统中查看。“导出计划”继续提供原始 JSON。

## 校验与加载诊断

启动时校验清单字段、配置别名冲突、依赖、同步工厂签名与贡献排序。工厂必须接受 `settings` 关键字参数；业务调用点额外传入的 LLM、项目等参数在实际调用时验证。阶段编译只导入声明，不为检查而创建数据库或模型客户端。

首次实例化时检查所有必需接口、已实现的可选接口是否可调用，以及清单同步接口是否误实现为 async 方法。async 方法必须声明 `async=true`；声明为异步的接口也允许同步包装函数返回 awaitable。旧的程序化 PluginSpec 保留自动识别协程的兼容行为。`close` / `aclose` 是资源管理入口，不能声明为阶段接口。

前端插件节点详情分别显示声明状态、最近一次实例加载状态和失败诊断。诊断包含 component、phase、code、message、声明来源及版本；路由加载失败单独记录，不被 Provider 成功加载覆盖。重新加载成功会清除该组件的旧失败信息。最近一次实例加载成功不代表全部实例或外部服务持续健康。

`GET /api/plugins/diagnostics` 提供运行时加载诊断；计划内 diagnostics 保存声明编译失败原因。运行时结果不修改执行计划或 plan_id。前端刷新重新读取这两种信息，仍不重新扫描或替换插件。

启动前可运行：

```shell
python backend/export_plugin_plan.py --check --output temp/plugin-check
```

命令导出计划并逐插件报告声明状态。存在 unavailable 插件时退出码为 1；disabled 不算失败。此检查不实例化 Provider、不验证运行期数据库连接，也不挂载 HTTP 路由；实际实例与路由结果可在运行时诊断中查看。

## 版本与历史计划

每个目录插件记录声明 version，以及 manifest_digest、implementation_digest、revision。声明指纹使用规范化 JSON，忽略 JSON 缩进变化；实现指纹覆盖插件目录内 Python 源文件内容与相对文件名。因此即使未修改 version，Python 代码或声明变化也会改变下一次启动生成的 plan_id。

revision 描述声明与本插件目录内 Python 内容，不是整个 Python 依赖环境的版本；资源文件、外部依赖和部署覆盖到目录外的 Provider 代码不在该指纹范围内。部署参数值不会写入版本记录。建议发布插件时仍主动更新声明 version。

计划导出时同时保留 `PLUGIN_PLAN_DIR/history/<plan_id>.json`。后续启动覆盖当前 plugin-plan.json，但保留旧计划；`GET /api/plugins/plans/{plan_id}` 读取指定快照并验证内容摘要。它只读取记录，不重新执行历史插件。

插件贡献 Trace 事件保存执行时绑定的 plugin_version、plugin_revision 与 plan_id，回放步骤详情展示这些字段。旧记录仍可查看；历史计划不一致时，现有图高亮保护继续生效。归档与诊断为后续受控刷新提供基础，当前修改仍需要重启生效。

目录扫描负责发现实现及注册阶段贡献。新的 Agent 工具或新的业务调用入口仍需按现有契约装配；声明一个 service 方法不会自动把它暴露给模型。新增领域端口时继续在核心定义协议、请求结果模型和降级行为；同一端口的不同实现通过 Provider 配置选择。
