# 插件阶段贡献与执行计划

系统初始化时读取内置插件配置与可选的额外插件清单，发现各插件显式声明的生命周期接口，校验后生成执行计划，再按计划注册到 HookRegistry。Agent 主循环发布阶段事件，调度器执行对应接口。

执行计划是系统生成的只读结果。启停与 provider 选择仍由配置决定，修改后重启后端。编辑生成文件不会改变运行行为。

## 配置与发现

内置插件继续使用各自的 `AGENT_*_ENABLED` / `AGENT_*_PROVIDER` 配置。额外插件通过 JSON 清单接入，无需修改内置槽位表：

```dotenv
PLUGIN_MANIFEST_FILE=config/plugins.json
```

相对清单路径从 `backend/` 解析。清单格式见 `backend/config/plugins.example.json`：

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "name": "team_checks",
      "provider": "my_team.checks:create",
      "enabled": true,
      "interfaces": ["query"]
    }
  ]
}
```

清单不允许覆盖已有插件名称。格式错误使初始化明确失败；单个插件的导入、贡献声明或依赖错误会标为 `unavailable`，不安装其贡献，并在执行计划中保留原因。

发现过程只导入配置指定的模块，校验工厂入口并调用模块级 `list_contributions(settings=...)`，不会创建需要 LLM、项目路径或数据库的领域 provider。`interfaces` 列出按需领域接口，与生命周期贡献分开展示。`discovered` 表示声明可用，不表示领域 provider 已实例化或通过健康检查。

## 接口声明

插件工厂所在模块可以提供：

```python
from app.agent_base.core.hooks import HookEvent
from app.agent_base.core.lifecycle import Contribution

def list_contributions(*, settings=None):
    return (Contribution(
        id="team_checks.inspect",
        stage=HookEvent.TOOL_BEFORE,
        handler="my_team.checks:inspect_tool",
        mode="control",
        priority=60,
        fail_closed=True,
    ),)

def inspect_tool(context):
    # Return None to continue, or a supported HookDecision to block.
    return None
```

`id` 全局唯一，`handler` 使用 `module:callable`。同阶段内可用 `before` / `after` 元组声明贡献 ID 依赖；依赖优先于数值优先级，其他接口按优先级降序、ID 排序。重复 ID、缺失依赖、跨阶段排序及循环依赖均有明确错误原因。

当前阶段接口是同步、无状态函数，`scope="run"` 表示通过任务上下文访问任务状态，不代表自动创建每任务的插件对象。任务状态放入 `context.runtime`，不能保存在模块级可变变量中。异步 I/O 能力继续通过已有异步 Provider / 工具接口调用，不应放入同步阶段处理器。

## 阶段与执行方式

| 阶段 | 外部插件允许的方式 | 语义 |
|---|---|---|
| `RUN_START` | observer | 主循环任务开始；预算和运行状态已建立 |
| `ROUND_BEFORE` | observer | 每轮准备模型请求之前 |
| `LLM_BEFORE` | observer / transform / control | 请求前；可修改消息或返回 STOP |
| `LLM_AFTER` | observer / transform | 模型返回后；可就地处理消息与响应 |
| `TOOL_BATCH_BEFORE` | observer | 调用工具批次前 |
| `TOOL_BEFORE` | observer / control | 可执行调用的前置检查；可返回 VETO / STOP |
| `TOOL_AFTER` | observer / transform | 结果产生后，包括被阻断的调用；可替换反馈文本 |
| `TOOL_BATCH_AFTER` | observer / transform / control | 批次汇总；可更新状态或返回 RECOVER / FINALIZE |
| `ROUND_AFTER` | observer | 下一轮开始前或当前轮终止时 |
| `RUN_FINALIZE` | observer | 最终输出及 RunOutcome 已整理，返回给消费方之前 |
| `RUN_END` | observer | 正常结束、错误、取消后的清理通知，携带结束状态 |
| `ERROR` / `CANCEL` | observer | 模型超时、未处理异常或任务取消通知 |

模型调用和工具执行仍由核心执行器负责，替换实现使用对应领域端口。工具批次可由现有执行器串行或并行执行，阶段贡献本身按计划串行调度。

- **observer** 接收独立的数据快照，不能修改实际消息或访问运行时活对象；返回值不参与控制决定，观察失败不阻断执行。
- **transform** 修改其阶段数据并返回 `None`；`TOOL_AFTER` 也可返回文本或 REPLACE 决定。替换反馈不改变原始工具结果与执行证据。
- **control** 返回阶段支持的 `HookDecision`。前置阶段的第一个有效决定使后续非观察接口跳过，观察接口仍执行；批次结束收集控制决定，FINALIZE 优先于 RECOVER。

核心的中断、预算和收敛接口也出现在计划中。它们承担核心运行职责，保留既有实现。Trace 生命周期观察与架构路由 checkpoint 已通过显式贡献接入；后者保留独立使用 provider 时的兼容注册，并避免重复挂接。

## 生成文件与查询

后端启动时默认写入 `backend/.architectcoder/plugins/`，可通过 `PLUGIN_PLAN_DIR` 修改：

| 文件 | 内容 |
|---|---|
| `plugin-plan.json` | 插件来源、状态、按需接口、各阶段贡献、顺序、模式和依赖 |
| `plugin-schedule.mmd` | 主循环阶段与阶段内接口顺序的 Mermaid 图 |
| `plugin-organization.mmd` | 插件、贡献接口、阶段及按需接口的 Mermaid 图 |

JSON 与两种图从同一执行计划生成，并携带同一 `plan_id`。图展示生命周期组织关系，具体某次任务经过的路径以 Trace 为准。

无需启动服务也可在仓库根目录导出：

```bash
python backend/export_plugin_plan.py
python backend/export_plugin_plan.py --output ./temp/plugin-plan
```

后端提供只读接口，沿用现有 API 认证：

- `GET /api/plugins/plan`
- `GET /api/plugins/graph?view=schedule`
- `GET /api/plugins/graph?view=organization`

## 前端可视化

工具栏中点击 Trace 旁的 **插件架构 / Plugin architecture**，可打开交互式面板：

- **组织图** 展示插件、阶段贡献、按需领域接口和生命周期阶段；禁用与不可用插件仍保留。
- **调度图** 展示主循环阶段、模型及工具执行节点、继续或结束分支，以及阶段内接口顺序。
- 支持筛选插件、缩放、适应宽度、滚动浏览、刷新和导出 JSON 计划。
- 点击节点或用键盘选择，可查看接口类型、顺序、优先级、依赖、provider 来源及失败原因。点击阶段详情中的接口可定位对应节点。

前端图形直接从 `/api/plugins/plan` 返回的同一快照绘制，保留其 `plan_id` 与编译顺序。刷新失败时明确标示旧计划可能过期，不把旧数据视作最新结果。界面支持中英文，并沿用现有 API 认证配置。

Trace 会记录 `lifecycle_stage` 和 `plugin_contribution`，包含任务 ID、计划 ID、阶段、接口、执行或跳过状态、耗时及控制决定。可据此关联配置组织与实际执行；当前未提供 Trace 覆盖到图上的前端交互视图。

现有领域 provider 的按需接入保留，未整体迁移为阶段接口。已有手动 Hook 注册兼容保留；计划描述显式管理的贡献，运行中临时手动注册不属于初始化快照。后续新增跨切面能力应使用贡献声明，避免形成第二套隐式组织关系。
