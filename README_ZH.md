<div align="center">

# ArchitectCoder

**让想法成为架构，让架构驱动开发**

[English](README.md) | **中文**

</div>

ArchitectCoder 是一个以 UML 为设计入口的 AI 协同开发工作台：将架构设计、代码修改、测试、审核和回放串成可追踪的闭环。当前 `dev-4.0` 版本线内置 **DevAgent 开发助手**、**全局 UML 优化**、**一键式 DevAgent 能力基准中心**、**TestHub 测试中心**、**Trace 追踪回放**、**知识图谱**、**记忆系统** 与 **BaseAgents 框架**。

## 为什么选择 ArchitectCoder？

- **设计即真相源**：类图、时序图和组件图始终位于开发流程中心。
- **受控 AI 开发**：Agent 只读取必要上下文，进行范围明确的修改，主动验证，并在需要时暂停等待人工审核。
- **全程可追踪**：工具调用、审核、checkpoint、测试和最终结果均可查看与回放。

## 核心能力

### 多图编辑器

| 图类型 | 核心元素 | 交互 |
|--------|---------|------|
| **类图** | 类 + 6 种关系 | 双击添加类 / 拖拽端口创建关系 |
| **时序图** | 生命线 + 5 种消息 | 双击添加生命线 / 点击 A→B 创建消息 |
| **组件图** | 组件 + 依赖 + 子组件 | 双击添加 / 双击内部创建子组件 |

- 撤销/重做（50 步）、缩放（工具栏 + Ctrl+滚轮）、网格吸附
- Ctrl+C/V 复制粘贴、Ctrl+S 保存
- 右侧属性面板、可配置网格、空格+拖拽平移
- 支持直接拖动正交连线线段调整折点；`Alt`+点击可轮换选择重叠连线
- 支持浅色、深色、蓝图和护眼画布主题
- 支持将当前图导出为 PNG/SVG，将完整工程导出为 `.umlproj`，或将设计图导出为 Markdown

### 项目管理

- `.umlproj` 工程文件：一个工程包含多张不同类型的设计图
- **组件-图层级**：`项目 → 组件 → 类图/时序图` 三层组织，右键组件新建/切换关联图
- 工具栏按类型分组下拉切换（颜色编码：橙/蓝/绿）
- 打开旧 `.uml` 文件自动包装为工程
- 可分别设置项目、源码和测试目录，作为 Agent 工作范围
- 工作区与安全路径策略确保 Agent 只访问配置的根目录
- **快启案例**：打开 [`examples/quickstart`](examples/quickstart/)，即可体验完整的雷达信号处理 UML、Python 源码、pytest 测试和脱敏性能参考。

### AI 开发助手

右下角机器人按钮打开浮动对话面板。生产 **DevAgent** 是通过统一运行时装配的 native function calling `ReActAgent`，交互式对话和评测复用同一条生产链路，既能处理闲聊，也能完成端到端工程任务：

- **基础工具**：`list_files`、`read_file`、`search_text`、`apply_changes`、`run_task`、`run_program`、`shell`。所有文件创建、修改、删除、移动和复制都通过 `apply_changes`，测试、构建、Lint、类型检查等标准任务优先使用 `run_task`。
- **设计优先工作流**：影响设计的需求遵循“检查 → 修改 UML → 校验并提交审核 → 开发 → 验证”；纯实现型任务可以直接开发。涉及架构变化时，业务代码要等 UML 审核通过后再修改。
- **人工审核与安全**：`submit_uml_review` 展示 UML Diff 并等待批准；敏感 Shell 命令需要人工批准，高危操作按策略直接拒绝。
- **任务规划与委派**：`todo_write` 跟踪多阶段任务并明确验证项。可选编排器可以生成有界计划并委托只读策略探索；修改和验证仍由主 Agent 负责，`spawn_subagent` 受独立开关和单次任务约束。
- **流式进度与中断**：工具调用、参数、观察结果、思考和 TODO 进度实时推送到界面。停止任务后会保存已完成、待完成、已修改和已验证项目，并支持显式继续执行。
- **上下文与证据**：上下文预算、历史压缩、结构化工具证据、收敛保护和最终摘要让长任务可恢复、可审计。
- **会话与记忆**：支持新建/切换会话，刷新后恢复历史；任务完成后归档记忆，新任务按相关性召回项目历史。SQLite/BM25 记忆可通过 Provider 关闭或替换。
- **UML skill 知识包**：按需加载 UML 2.5.1 类图、时序图、组件图和跨图一致性指南，并提供可直接加载的小型 `.umlproj` 案例。

所有工程能力统一使用工作区工具、审核门禁、Trace 记录和验证生命周期，确保对话、设计变更、代码实现与评测过程保持一致。

### DevAgent 能力基准中心

能力基准中心是生产 **DevAgent** 的质量闸门：把真实工程任务转化为可重复、可版本化、可提供证据的能力评估，而不是停留在主观演示层面。

#### 核心价值

- **生产链路一致**：评测使用与产品对话相同的 DevAgent 运行时、工作区工具、安全策略、上下文管理和验证流程。
- **面向任务覆盖**：通过版本化用例覆盖项目理解、单轮工程任务、多轮上下文连续性，以及设计、代码和测试协同任务。
- **确定性结果校验**：每个用例绑定受控项目 fixture 和 manifest，通过硬门禁与诊断检查器校验文件、UML、测试、受保护路径和其他交付物。
- **安全且可复现**：每次评测都在隔离工作区执行，并明确限制时间、步数、工具调用次数和 Token；当前 Git 分支与 commit 自动记录为评测版本，未提交修改会显式标记。
- **结果可解释**：结果包含通过/失败状态、得分、检查器明细、工具调用、Token、耗时，以及完整 Agent Trace 的关联入口。
- **支持回归与发布对比**：评测中心支持一键运行、历史批次、基线快照、性能 JSONL、结果归档和选中版本对比。

#### 评测链路

~~~text
版本化用例
    → 项目 fixture + manifest
    → 生产 DevAgent
    → 硬门禁 + 诊断检查器
    → Trace + 结构化 JSONL 结果
    → 批次汇总
    → 多版本对比与归档
~~~

#### 评测中心

前端将完整流程收敛为一条可操作的闭环：

1. 选择一个或多个评测集或用例，一键启动评测。
2. 查看用例级结果、检查器证据、失败、超时、工具调用和 Trace 会话。
3. 对比通过率、得分、平均耗时、Token 使用量和工具调用量。
4. 按构建时间从左到右比较选中版本，并用带正负号的变化率和方向折线展示相邻版本变化。
5. 将完整结果归档为可审计快照，用于发布验收和回归跟踪。

同时提供 CLI，便于自动化和 CI 风格检查：

~~~bash
python -m extensions.evals.cli --suite understanding
python -m extensions.evals.cli --suite single
python -m extensions.evals.cli --suite multiturn
~~~

详见[评测体系设计文档](docs/evaluation-system.md)，其中包含用例模型、检查器契约、隔离规则、结果生命周期和 API 说明。
### 全局 UML 优化

工具栏的“全局优化”现在会把自然语言需求提交给同一个 DevAgent 对话运行时。若当前工程尚未保存，工具栏会先保存；随后打开 AI 助手，并将项目、源码和测试目录作为上下文传入。Agent 通过标准工具检查和修改相关 `.umlproj` 设计文件，再按正常审核、Trace 记录和验证流程完成任务。

全局优化与其他 Agent 任务共用安全策略、checkpoint、记忆、Trace 和审计能力。

### TestHub 测试中心

Excel 用例驱动的测试代码生成：

- **用例管理**：加载 testHub 目录下的 Excel 用例表，在线编辑并回写保存
- **测试代码生成**：全量 / 增量（仅变更用例）两种模式生成测试代码，右侧"用例代码"页签查看、复制、下载
- **评审留痕**：查看、编辑、生成、接受 / 拒绝等操作统一记录到 `dev_review.txt`

### Trace 追踪与回放

- **全程记录**：Agent 会话自动落盘 JSONL trace（LLM 请求/响应 + 工具调用逐步事件）
- **TraceViewer**：前端抽屉式浏览历史会话，步骤级展开每次工具调用的参数与返回
- **长 Prompt 查看**：超长 LLM Prompt 和工具 schema 默认保持紧凑，用户可以展开，在可滚动面板中查看完整内容
- **三种回放模式**：`mock` 按记录重放且零网络；`rerun` 真实调用 LLM、工具仍 mock；`live` 真实调用 LLM，并按策略执行真实工具（默认 `readonly`，`full` 为显式有副作用模式）
- 支持整段会话回放、按轮次累积单步执行、匹配状态展示，以及原始步骤与回放步骤对比

### 知识图谱

SQLite 图数据库 + FTS5 全文索引，知识图谱插件开启后在工程保存时自动重建，
为 AI 助手提供结构化项目理解：

- **三层知识**：项目层 → 实体层 → 关系层（继承/组合/依赖 + 设计-代码映射 + 测试覆盖）
- **双源构建**：设计层（UML JSON 自动同步）+ 代码层（AST 解析源码目录）
- **Agent 查询**：项目地图、节点搜索和邻居展开，与文件级读取和搜索互补

### 自动布局引擎

LLM 返回的设计元素坐标自动计算，仅影响新生成元素，手动拖拽位置完全保留：
- 类图：继承链分层 + 网格布局
- 时序图：生命线等距水平排列 + 消息垂直递增
- 组件图：流式排列自动换行

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 18 + TypeScript + AntV X6 + Zustand + Ant Design 5 |
| 后端 | FastAPI (Python) + WebSocket |
| Agent | BaseAgents（ReActAgent，native function calling） |
| LLM | DeepSeek API（每个会话使用一个固定模型，由 `DEEPSEEK_MODEL` 配置） |
| 知识图谱 | SQLite + FTS5 |
| 记忆系统 | SQLite + FTS5 + jieba |
| 测试 | pytest（真实子进程执行）+ openpyxl（Excel 用例） |
| 构建 | Vite |

## 项目结构

```
ArchitectCoder/
├── frontend/                       # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── Canvas/             # 三类图编辑器（类图/时序图/组件图）
│   │   │   ├── AgentChat/          # AI 开发助手浮动面板 (WebSocket)
│   │   │   ├── PropertyPanel/      # 属性编辑面板
│   │   │   ├── Toolbar/            # 工具栏
│   │   │   ├── CodeViewer/         # 代码查看器 (Monaco)
│   │   │   ├── DiffViewer/         # UML Diff 对比视图
│   │   │   ├── TestCaseViewer/     # TestHub Excel 用例管理
│   │   │   ├── TestCodeViewer/     # 生成的测试代码查看器
│   │   │   └── TraceViewer/        # 会话 trace 可视化 + 回放
│   │   ├── stores/                 # Zustand 状态管理
│   │   ├── services/               # API 服务层 (含 agentChat WebSocket)
│   │   ├── types/                  # TypeScript 类型定义
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/                        # FastAPI 后端
│   ├── config/                     # 应用配置与 AgentConfig
│   ├── app/
│   │   ├── api/                    # REST + WebSocket 路由 (files/llm/testhub/trace/metrics/evals)
│   │   ├── core/                   # 鉴权与安全
│   │   ├── models/                 # Pydantic 数据模型
│   │   ├── services/               # 会话、执行、审核、变更集、Trace 回放
│   │   ├── agent_base/             # BaseAgents 框架 (core/agents/tools)
│   │   │   └── tools/my_tools/     # 文件系统原语 / todo / 子代理 / 审核
│   │   └── main.py
│   ├── evals/                      # 版本化评测用例和 fixtures
│   ├── tests/                      # 单元测试和集成测试
│   ├── requirements.txt
│   └── .env
├── docs/                           # 设计文档、评测基线和系统归档
├── extensions/                     # 统一的插件实现与 Provider 入口
│   ├── orchestration/              # 编排插件
│   ├── memory/                     # 记忆插件
│   ├── trace/                      # Trace 插件
│   ├── evals/                      # 评测插件与 CLI
│   └── knowledge_graph/            # 知识图谱插件
├── skills/                         # UML 设计与 Trace 分析 skill 包
├── project/                        # 项目代码输出 (src/ + test/)
├── temp/                           # 运行时临时文件（不上库）
├── .claude/                        # Claude Code 配置
├── README.md                       # 英文项目文档（默认）
└── README_ZH.md                    # 中文项目文档
```

## 快速开始

```bash
# 后端
cd backend
python -m pip install -r requirements.txt
# 创建 backend/.env，并至少设置：DEEPSEEK_API_KEY=你的密钥
python -X utf8 -m app.main          # http://localhost:8001

# 前端
cd frontend
npm install
npm run dev                           # http://localhost:3000
```

可选配置包括 `DEEPSEEK_MODEL`、五类插件的 `AGENT_*_ENABLED` 开关和 `AGENT_*_PROVIDER` 入口。配置定义集中在 `backend/config/settings.py` 与 `backend/config/agent_config.py`，插件实现统一位于 `extensions/`，由 `backend/app/agent_base/core/plugins.py` 加载。将开关设为 `false`，或将 Provider 设为 `none`、`noop`、`disabled`，即可关闭插件。`WORKSPACE_ROOTS` 用于配置额外的 Agent 工作区根目录，命令环境默认使用原生环境；需要 WSL 时显式设置 `AGENT_COMMAND_ENVIRONMENT=wsl`。`SUB_AGENT_MODEL` 仅作为已弃用兼容配置读取且不会生效。设置 `INTERNAL_API_TOKEN` 后，需在 `frontend/.env.local` 设置相同的 `VITE_API_TOKEN`。依赖安装完成后，可运行 `start.bat` 一键启动前后端；只有需要开发热重载时才设置 `UVICORN_RELOAD=1`。

## 插件架构

当前五类可插拔能力统一由 `PluginManager` 管理：编排、记忆、Trace、Evals
和知识图谱。每个插件通过 `module:factory` 形式的 Provider 入口加载，具体
实现代码统一放在 `extensions/` 下，主流程只依赖稳定接口和降级实现。

配置定义集中在 `backend/config/`：

- `settings.py`：应用级配置、插件开关和 Provider 配置
- `agent_config.py`：Agent 实例级配置
- `__init__.py`：统一导出配置类型

运行时可以在 `backend/.env` 中覆盖 `backend/config/` 中配置类的默认值。例如：

```env
AGENT_MEMORY_ENABLED=false
AGENT_TRACE_PROVIDER=extensions.trace:create
```

插件机制的完整设计、生命周期、目录边界和已知缺口见：
[插件机制设计归档](docs/plugin-architecture-design.md)。

知识图谱工具与知识图谱 Provider 共用 `AGENT_KNOWLEDGE_GRAPH_ENABLED` 开关。
开启后，主 Agent 默认获得 `get_project_map`、`find_nodes` 和
`expand_neighbors` 工具；关闭后不会注册任何知识图谱工具。

## API 与开发验证

- 后端 REST API 默认前缀为 `/api`，包含文件操作、LLM、TestHub、Trace、Agent 指标和 DevAgent 评测接口；全局优化通过 Agent 对话链路提交。
- 对话 Agent 使用 WebSocket：`/api/agent/ws/chat`。
- API 文档：启动后访问 `http://localhost:8001/api/docs`。
- 单元测试：`cd backend && python -m pytest -q`。
- 评测全部 18 个 DevAgent 用例（包含保留的 Trace 专项回归）：在仓库根目录运行 `python -m extensions.evals.cli`；正式 16 用例基线使用上面的三个 suite 命令分别运行。
- 前端生产构建：`cd frontend && npm run build`。

评测和运行日志写入 `temp/`，该目录及生成代码、数据库均为运行时产物，不提交到仓库。

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+Z / Ctrl+Y | 撤销 / 重做 |
| Ctrl+C / Ctrl+V | 复制 / 粘贴 |
| Ctrl+S | 保存工程 |
| Delete | 删除选中 |
| Ctrl+滚轮 | 缩放 |
| 空格+拖拽 | 平移 |

## 支持的编程语言

Python, Java, TypeScript, JavaScript, C#, C++, Go, Rust, Ruby, Swift, Kotlin, PHP
