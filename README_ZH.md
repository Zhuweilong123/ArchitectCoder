<div align="center">

# ArchitectCoder

**让想法成为架构，让架构驱动开发。**

[English](README.md) | **中文**

[快速开始](#快速开始) · [产品演示](#产品演示) · [核心能力](#核心能力) · [真实工程案例](#真实工程案例轻量级车辆仿真) · [文档](#文档)

</div>

ArchitectCoder 是一个以 UML 为设计入口的 **AI 协同开发工作台**，将架构设计、代码修改、测试验证、人工审核和执行回放串成可追踪的开发闭环。

内置 **DevAgent 开发助手**、**全局 UML 优化**、**一键式 DevAgent 能力基准中心**、**TestHub 测试中心**、**Trace 追踪与回放**、**知识图谱**、**记忆系统**及 **BaseAgents 框架**，支持从设计到实现、验证与复盘的完整流程。

根据任务选择设计契约模式：

- **开启设计契约：先设计，后开发。** 涉及架构、接口或交互变化时，先更新 UML 并审核设计，再实现代码和验证；提交前执行设计契约检查。
- **关闭设计契约：从代码同步设计。** 可以从现有代码仓库出发，让 DevAgent 分析实现、补充或更新 UML；本次任务跳过设计契约门禁。设计同步由用户发起，不会自动发生。

## 产品演示

<table>
<tr>
<td width="64%" valign="top">

<a href="https://raw.githubusercontent.com/Zhuweilong123/ArchitectCoder/dev-4.0/docs/media/architectcoder-demo.mp4"><img src="docs/media/architectcoder-demo-preview.gif" alt="ArchitectCoder 产品演示预览" width="100%"></a>

<p align="center"><sub>动态浏览器预览。<a href="https://raw.githubusercontent.com/Zhuweilong123/ArchitectCoder/dev-4.0/docs/media/architectcoder-demo.mp4">打开完整 MP4 演示</a>。</sub></p>

<p align="center"><sub>真实浏览器画面剪辑：打开工作目录、加载设计与源码 → 检查并更新 UML → 对比优化前后设计并审核 → 查看 Trace。</sub></p>
</td>
<td width="36%" valign="top">

### 一条可追踪的闭环

1. 用类图、时序图或组件图**建模**系统。
2. 让 DevAgent 在工作区上下文中**分析**设计。
3. **审核** UML 变更和范围明确的修改计划。
4. 在同一处**实现并验证**代码、测试、Trace 和结果。

从设计到交付，每一步都清晰、可审核、可回放。
</td>
</tr>
</table>

## 快速开始

需要先安装 Python 和 Node.js/npm。仓库 CI 使用 Python 3.12。AI 功能需要兼容 OpenAI API 的模型服务；手动编辑 UML 和导出不需要调用模型。

### 1. 安装与配置

在仓库根目录执行：

```powershell
python -m pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
```

macOS/Linux 使用 `cp backend/.env.example backend/.env` 替代 `copy`。

编辑 `backend/.env`，填写模型服务配置：

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=your-model-id
```

### 2. 启动应用

Windows 用户安装后端依赖并完成模型配置后，可在仓库根目录运行 `start.bat`。脚本会安装缺失的前端依赖，并启动前后端。

手动启动时，打开**两个终端**，各自从仓库根目录开始：

```bash
# 终端 1：后端
cd backend
python -X utf8 -m app.main
```

```bash
# 终端 2：前端
cd frontend
npm ci
npm run dev
```

访问 **http://localhost:3000**。后端地址为 `http://localhost:8001`。

### 3. 体验完整案例

1. 在工具栏点击**工作目录**，选择 [`examples/quickstart`](examples/quickstart/) 根目录。不要只选 `design/`；系统会识别设计项目、`src/` 和 `test/`。
2. 查看组件图、类图和时序图。打开 **AI 助手**，尝试：“解释这个项目的架构”或“检查 UML 与源码是否一致，并提交设计修改供我审核”。
3. Agent 提交设计审核时，在**对比**面板查看变化，批准或提出修改意见。
4. 选择**导出设计 → 导出项目 HTML（离线阅读）**，用浏览器打开下载文件，即可切换图、缩放、平移和适应窗口，无需运行 ArchitectCoder。

案例包含 UML、Python 源码、pytest 测试和设计—源码一致性检查。从仓库根目录执行：

```bash
cd examples/quickstart
python -m pytest test -q
```

详见[案例说明](examples/quickstart/README.md)。其中的性能参考是历史摘要，不代表当前环境重新执行后的结果。

## 核心能力

### 架构设计与导航

- **三类设计图**：类图描述结构，组件图描述模块边界和接口，时序图描述交互流程。
- **跨图关联**：将类图、时序图关联到组件，通过组件右键菜单创建或切换详细设计。
- **关系驱动布局**：按依赖和包含关系组织类图、组件图，紧凑排列独立分组，分离组件依赖线路；时序图按时间轴排列生命线与消息。
- **画布编辑**：属性面板、复制粘贴、撤销重做、网格吸附、直角连线折点调整，以及四种画布主题。
- **项目工作区**：从选定根目录识别常规 `design/`、`src/`、`test/`，也可单独指定目录；刷新后重新打开上次保存的设计。

### 可审核的 AI 开发

**DevAgent** 通过流式对话分析工作区、修改设计和文件、执行检查并报告结果。**全局优化**也通过同一 Agent 流程提交跨图检查与优化请求。

- **设计审核**：比较原始与提议版本，按语义高亮元素、成员和关系变化；批准变更或反馈修改意见。
- **设计契约**：启用后采集设计、源码和测试事实，检查一致性，并将失败结果接入执行与审核生命周期；聊天中的契约开关控制下一次运行。
- **执行进度**：查看任务清单和工具步骤，停止运行，并显式恢复支持的执行检查点。
- **项目历史**：切换会话、恢复对话历史，并通过可配置 Provider 使用项目级记忆。
- **受控变更**：工作区边界、限定文件修改范围、冲突检测、命令策略及敏感命令审批。

### 导出与交付

| 格式 | 用途 |
|---|---|
| **HTML** | 整个项目导出为一个离线只读文件，支持图目录、缩放、拖动平移、适应窗口和中英文界面 |
| **PNG / SVG** | 当前图，用于演示、文档插图及矢量展示 |
| **`.umlproj`** | 完整可编辑项目，可在 ArchitectCoder 中重新打开 |
| **Markdown ZIP** | 当前图或全项目设计文档，附带 SVG 图形 |

HTML 内嵌图形、样式和脚本，无外部依赖，保留导出时的图形展示内容。需要继续编辑时使用 `.umlproj`。详见 [HTML 导出说明](docs/project-html-export.md)。

## 真实工程案例：轻量级车辆仿真

我们在一个包含 **93 个 Python 文件、约 1.2 万行 Python 代码**的车辆仿真工程中使用 DevAgent。它对照源码检查现有 UML，发现设计覆盖缺口，并更新组件图与类图。设计从 **8 张图扩展到 11 张图**，新增路线规划、参考线和分析模块的类图。更新通过 UML 结构校验和人工审核后，提交到仿真工程仓库。

[查看设计提交](https://github.com/Zhuweilong123/my_carla_sim/commit/998a6c813683dd76234a85432b29a807d694c9e3)

**整体架构 · Engine Architecture**

组件图展示仿真内核、规划、控制、路由、参考线、分析及 ROS 2 适配模块，以及它们的接口、依赖和子组件。

[![轻量级车辆仿真的组件架构图](docs/media/lightweight-sim/engine-architecture.png)](docs/media/lightweight-sim/engine-architecture.png)

**核心结构 · Simulator Core Classes**

类图展开 `SimulationEngine` 与 `World`、`EgoVehicle`、`ObstacleManager`、`SteeringActuator` 的组合关系，以及反向仿真引擎的继承关系。

[![仿真核心类图：类成员、组合与继承关系](docs/media/lightweight-sim/simulator-core-classes.png)](docs/media/lightweight-sim/simulator-core-classes.png)

**运行流程 · Autonomous Driving Loop**

时序图串起路由与参考线生成、仿真状态发布、路径规划、车辆控制和指令回传，并展示控制指令过期时的处理分支。

[![自动驾驶循环时序图：规划、控制与仿真协作](docs/media/lightweight-sim/autonomous-driving-loop.png)](docs/media/lightweight-sim/autonomous-driving-loop.png)

点击图片查看完整尺寸，或打开 SVG 放大查看：[组件图](docs/media/lightweight-sim/engine-architecture.svg) · [类图](docs/media/lightweight-sim/simulator-core-classes.svg) · [时序图](docs/media/lightweight-sim/autonomous-driving-loop.svg)。

## 验证与高级能力

- **DevAgent 能力基准中心**：在隔离工作区中用生产 Agent 执行版本化用例，查看 Checker 结果、工具与 Token 用量、耗时和 Trace；比较版本并归档结果。
- **Trace 追踪与回放**：查看模型与工具调用记录，对比回放步骤；`mock` 使用记录数据且不调用模型，`rerun`、`live` 按各自策略执行模型与工具。
- **TestHub**：在画布中加载、编辑和保存 Excel 测试用例，保留审核记录；测试代码的编写与更新由 DevAgent 通过工作区流程完成。
- **知识图谱与记忆**：通过可配置 Provider 提供设计、源码、测试索引和项目级记忆检索。
- **可扩展运行时**：BaseAgents 和 Provider 端口支持替换编排、记忆、Trace、评测、知识图谱及设计契约实现。

### 评测中心演示

<a href="https://raw.githubusercontent.com/Zhuweilong123/ArchitectCoder/dev-4.0/docs/media/architectcoder-evaluation-demo.mp4"><img src="docs/media/architectcoder-evaluation-demo-preview.gif" alt="ArchitectCoder 评测中心演示预览" width="100%"></a>

<p align="center"><sub>动态浏览器预览。<a href="https://raw.githubusercontent.com/Zhuweilong123/ArchitectCoder/dev-4.0/docs/media/architectcoder-evaluation-demo.mp4">打开完整 MP4 演示</a>。</sub></p>

<p align="center"><sub>真实浏览器录屏：性能结果 → 三版本趋势对比 → 用例级 Trace 回放 → 归档中心。</sub></p>

## 语言与工具链支持

界面提供 12 种目标语言选项。**任务执行与源码理解的支持范围不同**：

- 项目任务解析覆盖 Python、Node.js、CMake/CTest、Cargo、Maven/Gradle、Go、.NET 和显式 `.architectcoder/tasks.json` 任务。能否执行取决于可用工具链与执行策略。
- 源码事实采集包含 Python AST，以及支持编译数据库的 C++ Clang 适配基础；其他语言选项不代表具有同等的结构解析和设计契约覆盖。
- 内置快启案例展示 Python 设计、源码和测试流程。详见[多语言执行说明](docs/multilanguage-execution-design.md)，了解实现范围与前置条件。

## 配置

运行配置位于 `backend/.env`，定义集中在 `backend/config/`。从 [`.env.example`](backend/.env.example) 开始配置。

- **模型**：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`。
- **工作区**：仓库外项目通过 `WORKSPACE_ROOTS` 加入允许的根目录，并重启后端；将快启案例复制到仓库外时同样适用。
- **执行环境**：默认使用原生命令环境，需要 WSL 时显式设置 `AGENT_COMMAND_ENVIRONMENT=wsl`。
- **Provider**：通过 `AGENT_*_ENABLED` 和 `AGENT_*_PROVIDER` 控制可选能力；示例配置关闭知识图谱和编排，可按需启用。
- **API 鉴权**：设置 `INTERNAL_API_TOKEN` 后，在 `frontend/.env.local` 配置相同的 `VITE_API_TOKEN`。

详细配置与策略见[插件架构](docs/plugin-architecture-design.md)及[命令执行说明](docs/runtime-command-execution.md)。

## 文档

| 主题 | 入口 |
|---|---|
| 全部文档 | [文档导航](docs/README.md) |
| Agent 架构与框架 | [当前架构](docs/current-architecture.md) · [BaseAgents](docs/baseagents-design.md) |
| 设计—源码一致性 | [设计契约](docs/design-source-contract.md) |
| 项目 HTML 交付 | [HTML 导出](docs/project-html-export.md) |
| 任务执行与语言支持 | [命令执行](docs/runtime-command-execution.md) · [多语言执行](docs/multilanguage-execution-design.md) |
| 评测与回放 | [评测体系](docs/evaluation-system.md) · [Trace 回放](docs/trace-replay-design.md) · [Trace Case Factory](docs/trace-to-eval-case-factory-design.md) |
| 上下文与项目知识 | [上下文管理](docs/context-management-design.md) · [记忆](docs/memory-system-design.md) · [知识图谱](docs/knowledge-graph-design.md) |
| 扩展开发 | [插件架构](docs/plugin-architecture-design.md) |

## 开发

**技术栈：** React 18、TypeScript、AntV X6、Zustand、Ant Design 5、FastAPI、WebSocket、SQLite 和 Vite。

```text
frontend/         UML 编辑、Agent 对话、审核、导出和评测界面
backend/app/      API、Agent 核心、执行生命周期与运行时端口
backend/config/   应用与 Agent 配置
extensions/       编排、记忆、Trace、评测、知识图谱、设计契约
examples/         内置案例
skills/           可加载的 Agent 指南
docs/             使用、设计与实现文档
temp/             运行时产物
```

- 后端检查：在 `backend/` 运行 `python -m pytest -q`。
- 前端检查：在 `frontend/` 运行 `npm run build`、`npm run test:layout` 和 `npm run test:html`。
- Agent 评测：在仓库根目录运行 `python -m extensions.evals.cli --suite understanding`，也可选择 `single`、`multiturn`。
- API 文档：**http://localhost:8001/api/docs**；Agent WebSocket：`/api/agent/ws/chat`。

## 快捷键

| 快捷键 | 功能 |
|---|---|
| Ctrl+Z / Ctrl+Y | 撤销 / 重做 |
| Ctrl+C / Ctrl+V | 复制 / 粘贴 |
| Ctrl+S | 保存工程 |
| Delete | 删除选中 |
| Ctrl+滚轮 | 缩放 |
| 空格+拖拽 | 平移 |
