# 跨模块接口约定

关键接口以结构化类型为契约；新增字段或参数应先修改契约，再修改提供方和调用方。

- 模型消息、工具定义、调用结果及 token 用量定义在 shared/llm_types.py。
- Agent、Review、Prompt、Progress 及工具注册表端口定义在 backend/app/agent_base/host_api/agents.py；宿主服务通过明确的参数和返回类型提供能力。
- AgentAssembly 使用具名字段 agent、review_manager、prompt_builder，兼容原来的三项解包。评测配置通过 EvaluationAgentBinding 返回，不再往 Agent 上动态写入私有配置属性。
- 可序列化的扩展元数据使用 JsonValue/JsonObject。具有确定含义的数据应优先定义 TypedDict、dataclass 或 Protocol；不能用 JSON 字典代替已知的业务结构。
- 插件私有内存状态允许使用 object，读取时必须收窄类型。网络输入允许 unknown，经过校验和归一化后才能进入业务逻辑。
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

mypy 当前严格检查 host_api、宿主适配器和共享模型类型；尚未对整个后端启用 strict。其余实现代码中的宽泛类型需要按模块逐步收紧，不能把当前检查范围理解为全仓库已经没有 Any。
