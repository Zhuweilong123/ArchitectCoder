# 跨模块接口约定

关键接口以结构化类型为契约；新增字段或参数应先修改契约，再修改提供方和调用方。

- 模型消息、工具定义、调用结果及 token 用量定义在 shared/llm_types.py。
- ToolResult 的数据限制为 JSON，文件效果、命令执行、验证结果、调用尝试和证据快照分别有结构化定义；结果边界拒绝无法序列化的数据和错误证据字段。证据快照与原结果分离，修改结果不会污染已记录的事实。
- 原生工具循环与任务摘要共用规范化调用签名，区分布尔、整数、浮点和非法参数字符串；摘要先按签名建索引，再查找后续重试。
- 执行检查点、工具效果、验证证据和流式步骤定义在 backend/app/agent_base/host_api/execution_types.py。恢复数据通过公共 payloads 模块校验已声明字段；JSON 扩展字段会保留，非 JSON 值和错误字段类型会被拒绝。
- 会话迁移通过 export_conversation/restore_conversation 获取独立快照，保留消息角色、内容、时间和元数据，避免共享可变历史。
- Agent、Review、Prompt、Progress 及工具注册表端口定义在 backend/app/agent_base/host_api/agents.py；宿主服务通过明确的参数和返回类型提供能力。
- AgentAssembly 使用具名字段 agent、review_manager、prompt_builder，兼容原来的三项解包。评测配置通过 EvaluationAgentBinding 返回，不再往 Agent 上动态写入私有配置属性。
- 可序列化的扩展元数据使用 JsonValue/JsonObject。具有确定含义的数据应优先定义 TypedDict、dataclass 或 Protocol；不能用 JSON 字典代替已知的业务结构。
- 插件私有内存状态允许使用 object，读取时必须收窄类型。网络输入允许 unknown，经过校验和归一化后才能进入业务逻辑。
- TraceHookPayload 明确列出模型和工具事件参数。聊天、评测和后台任务通过共享 TraceHookBridge 转发事件，各自管理记录器生命周期；请求统计通过独立 observer 保留。
- TraceViewer 的事件、消息、工具 schema 和回放结果定义在 frontend/src/types/trace.ts。网络响应和本地回放缓存经过 traceNormalization 校验，兼容旧记录中的缺省字段；未知事件和供应方 JSON 元数据保留。
- 前端 API 显式指定响应类型；设计流事件使用可辨识联合类型；评测 checker、结果、manifest 和 trace 摘要有独立定义。

已删除无人调用的独立 /api/llm 路由、LLMService、CodeGenerator 及前端 llmChat/getSupportedLanguages 包装。当前 Agent 模型调用统一通过 LLM gateway；需要模型能力时应使用宿主模型端口。

## 本地验证

在 backend 目录执行：

~~~shell
python -m pip install -r requirements-dev.txt
python -m mypy --config-file mypy.ini
python -m pytest -q
~~~

在 frontend 目录执行 npm run check:interfaces、npm run build 和对应行为测试。CI 同样执行接口类型检查和前端回归。

mypy 当前严格检查 host_api、宿主适配器、共享 Trace 桥接器、执行协调器、工具结果、证据账本、任务摘要、消息模型、流式步骤和评测轮次状态，以及共享模型类型；尚未对整个后端启用 strict。其余实现代码中的宽泛类型需要按模块逐步收紧，不能把当前检查范围理解为全仓库已经没有 Any。
