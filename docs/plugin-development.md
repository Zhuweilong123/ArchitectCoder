# 插件开发工具包

工具入口：[backend/plugin_dev.py](../backend/plugin_dev.py)。使用后端 Python 环境运行，无需启动服务、配置模型密钥或创建 DevAgent 会话。CLI 不读取应用 `.env`，只使用插件默认值及明确传入的开发输入。命令路径相对当前工作目录解析；应用中的 `PLUGIN_ROOTS` 相对 `backend/` 解析。

## 1. 创建插件骨架

在仓库根目录运行：

```shell
python backend/plugin_dev.py new team_checks --root temp/dev-plugins
```

生成 `temp/dev-plugins/team_checks/`：

| 文件 | 用途 |
|---|---|
| `plugin.json` | 插件身份、版本、默认参数、同步/异步服务接口和 prepare 观察贡献 |
| `__init__.py` | Provider、create 工厂及观察者的最小实现 |
| `smoke.json` | echo 服务试运行输入 |
| `README.md` | 针对当前插件路径生成的开发命令和接入说明 |

名称使用小写 Python 包名，不允许路径穿越、关键字、core 或标准库模块名。目标目录已存在时直接失败，不覆盖现有文件。省略 `--root` 时写入仓库 `examples/plugins/`。修改插件代码时同步维护接口声明；声明 service 不会自动向模型暴露工具。

## 2. 检查清单与接口契约

```shell
python backend/plugin_dev.py check temp/dev-plugins/team_checks
python backend/plugin_dev.py check temp/dev-plugins/team_checks --instantiate
```

`check` 复用生产环境的清单解析、配置类型检查、依赖检查、工厂签名、接口阶段、handler 签名和贡献排序校验。会导入工厂与贡献声明，但不构造 Provider，不执行阶段处理器，也不检查 HTTP 路由是否能够挂载。

`--instantiate` 进一步创建目标 Provider，复用实际加载器检查必需方法、已实现的可选方法是否可调用，以及同步/异步声明是否匹配；报告每个方法的 implemented 与 signature。未实现的可选接口不导致检查失败。检查不自动调用全部方法，返回 awaitable 的同步包装错误及具体调用参数由试运行发现。

工厂需要额外参数时，通过 `--input` 的 factory_kwargs 提供；本地开发会显式启用目标插件，因此即使 enabled_by_default=false，也会检查它的实现。依赖仍遵循各自默认启停。内置依赖自动识别；外部依赖添加可重复的 `--root`，这里只收集目标及其依赖，不导入无关插件：

```shell
python backend/plugin_dev.py check temp/dev-plugins/team_checks --root examples/plugins --instantiate
```

工具不会读取部署覆盖文件或环境覆盖；应用整体部署仍通过 `export_plugin_plan.py --check` 检查。

## 3. 独立试运行

运行一个声明的接口：

```shell
python backend/plugin_dev.py run temp/dev-plugins/team_checks --method describe
python backend/plugin_dev.py run temp/dev-plugins/team_checks --method echo --input temp/dev-plugins/team_checks/smoke.json --output temp/team-checks-echo.json
```

运行目标插件在某个公共阶段或通知上的贡献：

```shell
python backend/plugin_dev.py run temp/dev-plugins/team_checks --stage prepare --output temp/team-checks-prepare.json
```

接口调用经过与应用相同的 ScheduledProvider 和服务调度器，支持同步与异步接口，并在调用前绑定参数签名。阶段试运行使用 trigger 的顺序、控制短路和观察者语义；缺少对应阶段贡献时明确失败，不假装完成一次执行。阶段试运行不创建 Provider。

执行使用独立 PluginSnapshot、HookRegistry、AgentRuntime 和内存 Trace sink，不发布活动计划，不改写应用归档，不运行核心或依赖插件的阶段 hook。依赖服务保留按需绑定，只有被测试插件显式调用时才执行。所有命令结束后恢复上下文；构造的目标 Provider（包括接口校验失败的对象）优先调用 aclose，或调用 close，关闭失败也使命令失败。插件自行创建的其他资源仍由插件负责释放。

JSON 报告包含 status、诊断、声明状态、接口检查结果、实际返回值以及插件贡献/操作记录；记录保留 plan_id、版本、耗时和异常。阶段控制决定也保留在结果与事件中。即使生产调度器允许观察者异常后继续，开发试运行仍返回失败，避免漏掉问题。

## 开发输入格式

`--input` 接受一个 JSON 文件，以下字段均可省略：

```json
{
  "config": {"label": "Team checks"},
  "factory_kwargs": {},
  "args": [],
  "kwargs": {"text": "hello"},
  "context": {
    "agent_name": "PluginDev",
    "payload": {"sample": true}
  }
}
```

| 字段 | 用途 |
|---|---|
| config | 目标插件参数；复用 defaults 的未知参数与类型检查 |
| factory_kwargs | 目标工厂额外关键字参数；settings 由工具提供 |
| args / kwargs | `--method` 的位置参数与关键字参数 |
| context | `--stage` 的 HookContext 数据；不用于服务调用 |

context 可传 agent_name、payload、messages、llm_response、tool_name、tool_input、tool_status、error_code、tool_output；runtime、invocation 和关联 ID 由工具管理。观察者上下文会由生产调度器补充 plugin_plan_id。

输入只支持 JSON 数据，不自动把对象转换为领域 dataclass，也不提供真实 LLM、数据库客户端或项目宿主。需要 Python 对象的工厂/方法应编写 Python 集成测试，或提供接收 JSON 数据的开发适配接口。

## 报告和接入

stdout 为单个 JSON 报告，插件 print 输出转到 stderr；`--output` 同时将报告写入指定路径。status=passed 时退出码 0；校验、调用、处理器或清理失败时退出码 1。参数用法错误由 argparse 返回 2，便于 CI 集成。

这是一套真实执行的开发工具，隔离的是调度和上下文；插件自身的文件、数据库及网络操作仍会执行。使用开发数据验证实现。每次 CLI 调用独立启动进程，编辑代码后重新运行即可，避免应用进程内模块缓存影响验证。

验证完成后，将插件放入已配置的扫描根目录，点击“扫描新插件”。新增扫描根目录或 HTTP router，以及已有插件实现/配置更新仍需重启后端。之后运行实际任务并检查架构图与 Trace，确认宿主装配和业务行为。详细配置见[目录发现](plugin-discovery.md)。
