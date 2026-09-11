# DevAgent 当前架构基线

> 状态：当前实现说明（非历史方案）
>
> 代码基线：`78af79b`（`dev-4.0`）
>
> 本文是当前代码的单一入口。旧版本基线、优化过程和评测数字请分别参阅文末的历史文档。

## 1. 总体流程

```text
WebSocket / Evaluation / future HTTP or CLI
              |
              v
        app/agent_base/assembly.py
              |
              +-- runtime environment + command executor
              +-- memory / plugin providers
              +-- foundation tools + review tools
              +-- prompt and context budget
              v
          ReActAgent
              |
              v
        AgentExecution
              |
              +-- run lifecycle / checkpoint / approval
              +-- structured ToolResult and evidence
              +-- trace and memory archive
              v
        transport-neutral outcome
```

`agent_chat_ws.py` 只负责 WebSocket 鉴权和协议适配；会话协调由
`app/services/chat_session.py` 负责，单次 Agent 执行由
`app/services/agent_execution.py` 负责。评测入口复用同一套 assembly，不应再维护
一套独立的工具或提示词装配逻辑。

## 2. 稳定边界

| 边界 | 当前所有者 | 说明 |
|---|---|---|
| Agent 组合 | `app/agent_base/assembly.py` | 统一创建 LLM、工具、运行时、记忆和提示词 |
| Agent 循环 | `app/agent_base/agents/react_agent.py` | 推理、工具调用、预算和收敛控制 |
| 单次执行 | `app/services/agent_execution.py` | 生命周期、checkpoint、审批、证据和结果 |
| 会话/传输 | `app/services/chat_session.py`、`agent_chat_ws.py` | 会话状态与 WebSocket 适配分离 |
| 文件与变更 | `app/agent_base/tools/my_tools/foundation_tools.py`、`app/agent_base/tools/my_tools/foundation_runtime.py`、`app/services/change_set.py` | Foundation 能力契约、工作区边界、原子变更和 SHA 校验 |
| 扩展能力 | `extensions/*` + `app/agent_base/core/plugins.py` | 具体 memory、trace、evals、KG、orchestration 实现 |
| Trace 端口 | `app/trace/tracing.py` | 生命周期和 hook；存储/回放实现在 `extensions/trace` |
| 图表历史 | `frontend/src/stores/diagramHistory.ts` | 撤销、重做、批处理和快照；`diagramStore` 只负责状态组合 |
| 图表布局 | `frontend/src/utils/componentLayout.ts` | 组件自动布局的纯计算；画布状态写入仍由 `diagramStore` 完成 |
| AgentChat 数据 | `frontend/src/components/AgentChat/agentChatUtils.ts` | 消息持久化裁剪、会话格式化和审核图归一化 |
| AgentChat 事件 | `frontend/src/components/AgentChat/agentChatEventHandler.ts` | WebSocket 事件到消息、进度、审核和终态状态的适配 |
| Toolbar 路径 | `frontend/src/components/Toolbar/toolbarUtils.ts` | 路径规范化、文件名派生、相对路径和项目图表摘要 |
| Toolbar 图表控件 | `frontend/src/components/Toolbar/DiagramTypeControls.tsx` | 图表类型切换、新增、删除及撤销/重做控件 |
| EvaluationCenter 工具 | `frontend/src/components/EvaluationCenter/evaluationUtils.ts` | 评测展示格式化、Trace 会话解析和 Checker 定义/校验 |
| UML 类布局 | `frontend/src/components/Canvas/umlClassLayout.ts` | 类节点尺寸估算与重叠消解的纯计算 |
| 时序图渲染规则 | `frontend/src/components/Canvas/seqRenderUtils.ts` | 生命线 HTML、消息视觉样式和布局常量 |
| 组件图渲染规则 | `frontend/src/components/Canvas/compRenderUtils.ts` | 组件主题、HTML 渲染和节点尺寸计算 |
| Canvas 生命周期 | `frontend/src/components/Canvas/core/canvasLifecycle.ts` | Graph 注册、注销、事件清理和销毁顺序 |
| Canvas 公共运行时 | `frontend/src/components/Canvas/core/createCanvasGraph.ts`、`canvasCommon.ts`、`canvasEventAdapter.ts`、`useCanvasGraphViewport.ts` | Graph 创建、网格/视口同步和通用事件适配 |
| 前端请求边界 | `frontend/src/services/toolbarProjectApi.ts`、`evaluationCenterApi.ts` | Toolbar 工程文件请求和评测资源批量加载 |

## 3. 当前基础工具契约

生产 Agent 的基础工具是：

| 工具 | 使用场景 |
|---|---|
| `list_files` / `read_file` / `search_text` | 受工作区约束的只读探索 |
| `apply_changes` | 文件创建、修改、删除、移动和复制 |
| `run_task` | `test`、`build`、`lint`、`format`、`typecheck`、`validate` 等标准任务 |
| `run_program` | 允许列表中的可执行文件 + 字面量参数 |
| `shell` | 最后的单条原生命令；禁止管道、串联、重定向、替换和嵌套 Shell |

执行路由固定为：`run_task` → `run_program` → `shell`。Windows 默认使用
原生 PowerShell；WSL Bash 只有在显式配置时启用。命令适配器和宿主策略位于
`app/runtime/command.py`，工具描述和路由规则位于 `assembly.py`。

## 4. 可选扩展

`extensions/` 是具体实现的唯一归属，当前内置入口为：

```text
extensions.orchestration:create
extensions.memory:create
extensions.trace:create
extensions.evals:create
extensions.knowledge_graph:create
```

应用层只依赖稳定端口：`MemoryPort`、`KnowledgeGraphProvider`、Trace
端口、评测端口和 orchestration 端口。扩展失效时应回退到对应的 no-op 或安全
降级实现，不得破坏主 Agent 执行链。

## 5. 上下文、记忆和 Trace

- 上下文预算由 `app/services/context_manager.py` 管理；它只负责本次请求的
  prompt 预算、历史压缩和恢复，不直接实现长期记忆。
- 长期记忆通过 `MemoryPort` 访问，具体 SQLite、BM25、生命周期和策略位于
  `extensions/memory`。
- Trace 的运行时 hook 位于 `app/trace/tracing.py`，JSONL 写入、读取和回放位于
  `extensions/trace`。
- 记忆、Trace 和上下文都是参考数据，当前用户指令优先级最高。

## 6. 文档使用规则

- 当前架构、工具边界和代码路径：本文。
- 插件加载和扩展所有权：`plugin-architecture-design.md`。
- 评测运行链路和指标：`evaluation-system.md`。
- memory、knowledge graph、trace 的领域细节：对应子系统设计文档。
- 旧版本基线和实施过程已从工作树移除；如需复盘，请通过 Git 历史查看对应提交。

## 7. 模块边界维护规则

以下规则用于避免新的功能把已建立的边界重新耦合：

- `extensions/trace/format.py` 只保存 JSONL 格式常量和默认存储路径；读取器和
  写入器都依赖它，但不能互相导入。
- `extensions/evals/summary.py` 是批次和性能结果的唯一汇总实现；批次管理和性能
  浏览不得相互导入。
- 知识图谱工具工厂只接受 `KnowledgeGraphProvider`。本地 SQLite Provider 必须由
  组合层创建后注入，工具层不得回退导入具体 Provider。
- 内置插件 Provider 默认值只在 `config/plugin_defaults.py` 定义，`Settings` 和
  `PluginManager` 只引用该定义。
- `agent_execution.py` 保持传输无关：新增进度事件先扩展独立的事件适配器，再接入
  执行生命周期，避免把 WebSocket 协议分支重新塞回主协调器。
- `chat_session.py` 只协调会话、WebSocket 命令和连接生命周期；持久 Run 创建、
  恢复 checkpoint、提示词上下文构建和后台执行启动由独立启动边界负责。
- `agent_execution.py` 只编排一次 Agent 任务；编排器准备、fallback UML 审核、
  终态 checkpoint 塑形和终态发布均通过独立职责边界完成。
- `react_runtime/fc_loop.py` 只管理回合状态；LLM hook/trace/超时调用和工具失败恢复
  分别由独立函数负责，不能把传输或会话状态引入 FC 循环。
- `diagramStore.ts` 不重新实现历史或布局算法；历史操作依赖 `diagramHistory.ts`，
  组件自动布局依赖 `componentLayout.ts`。
- `AgentChat.tsx` 只组合 UI、连接生命周期和用户操作；消息数据转换与 WebSocket
  事件分发分别位于 `agentChatUtils.ts`、`agentChatEventHandler.ts`，避免在组件中
  直接堆叠协议分支。
- `Toolbar.tsx` 只组合文件、目录和视图操作；路径/摘要计算位于 `toolbarUtils.ts`，
  图表切换和历史操作控件位于 `DiagramTypeControls.tsx`。
- `EvaluationCenter.tsx` 负责评测页面状态和交互；格式化、Trace 标识解析以及 Checker
  配置校验位于 `evaluationUtils.ts`，避免把领域规则重新散落到渲染逻辑中。
- `UMLEditor.tsx` 负责 X6 图形生命周期和交互；类节点尺寸与布局冲突消解位于
  `umlClassLayout.ts`，避免将纯布局算法与图形事件处理混合。
- `SeqEditor.tsx` 负责时序图 X6 生命周期和交互；生命线展示、消息颜色/箭头和固定
  布局参数位于 `seqRenderUtils.ts`。
- `CompEditor.tsx` 负责组件图 X6 生命周期和交互；主题、HTML 和节点尺寸计算位于
  `compRenderUtils.ts`。
- Canvas 编辑器统一通过 `canvasLifecycle.ts` 完成 Graph 注册和销毁；Toolbar 与
  EvaluationCenter 的批量请求分别通过服务层边界实现。
- `frontend/vite.config.ts` 显式拆分 React、Ant Design、X6、X6 插件和 Monaco vendor
-  chunk；主入口只保留应用代码，避免将共享运行时再次打入业务入口。Ant Design
  作为共享 UI 运行时保留独立 vendor chunk，告警阈值与拆包策略在同一配置中维护。

当前前端分层优化基线已完成。后续维护新功能时，应优先复用上述公共运行时、请求边界
和纯计算模块，避免把协议分支、API 批量请求或布局算法重新放回页面组件。所有迁移
必须保持公开工具/Provider 契约不变；当前按用户要求不新增自动化测试，使用既有构建
和检查命令验证变更。
