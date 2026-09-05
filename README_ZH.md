<div align="center">

# ArchitectCoder

**让想法成为架构，让架构驱动开发**

[English](README.md) | **中文**

</div>

ArchitectCoder 是一个以 UML 为设计入口的 AI 协同开发工作台：支持类图、时序图、组件图，并将设计、代码生成、测试、修复和回放串成可追踪的闭环。内置 **DevAgent 开发助手**、**能力基准中心**、**TestHub 测试中心**、**Trace 追踪回放**、**知识图谱**、**记忆系统** 与 **BaseAgents 框架**。

## 为什么选择 ArchitectCoder？

- **设计即源码**：架构图是驱动代码生成、验证、测试的唯一真相源。
- **AI 自动验证**：跨图一致性检查 + 模糊匹配自动修复，引用关系不再靠人眼核对。
- **自然语言到可运行代码**：描述需求 → 自动生成全套 UML → 人工 Diff 审核 → Agent 开发代码并真跑 pytest → 失败自动回修。

## 核心能力

### 多图编辑器

| 图类型 | 核心元素 | 交互 |
|--------|---------|------|
| **类图** | 类 + 6 种关系 | 双击添加类 / 拖拽端口创建关系 |
| **时序图** | 生命线 + 5 种消息 | 双击添加生命线 / 点击 A→B 创建消息 |
| **组件图** | 组件 + 依赖 + 子组件 | 双击添加 / 双击内部创建子组件 |

- 撤销/重做（50 步）、缩放（工具栏 + Ctrl+滚轮）、网格吸附
- Ctrl+C/V 复制粘贴、Ctrl+S 保存
- 属性面板右侧编辑、空格+拖拽平移

### 项目管理

- `.umlproj` 工程文件：一个工程包含多张不同类型的设计图
- **组件-图层级**：`项目 → 组件 → 类图/时序图` 三层组织，右键组件新建/切换关联图
- 工具栏按类型分组下拉切换（颜色编码：橙/蓝/绿）
- 打开旧 `.uml` 文件自动包装为工程

### AI 开发助手

右下角机器人按钮打开浮动对话面板，生产 **DevAgent**（底层使用 ReActAgent runtime）承接全部消息，支持从 UML 设计到代码实现的协同开发，并可根据任务分析、生成、修改和验证代码。v3.2 默认使用直接 ReAct 主流程，并通过可插拔 provider 提供可选的任务编排和只读策略子代理：

- **文件系统原语**：`read_file` / `write_file` / `edit_file` / `glob` / `bash` —— 读写代码、跑 pytest、修复失败，全由 Agent 自主编排
- **任务规划**：`todo_write` 维护会话任务清单，多步骤任务创建 3–5 个精简 TODO，必须包含验证项，并在执行过程中更新状态
- **设计优先工作流**：影响设计的需求遵循“检查 → 修改 UML → 校验 → 人工审核 → 开发 → 验证”；相关设计审核通过前不修改业务代码。纯实现型任务可以直接开发。
- **可选任务编排**：规划器可生成有界任务计划，委托只读的跨设计/源码/测试探索，并将结构化证据交回主 Agent；禁用 provider 后自动回到直接 ReAct 流程
- **受控子代理** `spawn_subagent`：仅在显式启用策略探索时使用独立上下文和只读工具，文件修改与验证仍由主 Agent 负责
- **人工审核**：UML 设计修改在进入开发阶段前经 `submit_uml_review` 推送 diff 对比审批；`bash` 敏感命令（强制删除、进程终止、`git reset --hard` 等）执行前弹出批准/拒绝审核卡，高危命令（格式化、分区、写引导等）直接拒绝 —— 把 AI 开发关进护栏
- **UML 2.5.1 skill 知识包**：按需加载类图、时序图、组件图和跨图一致性指南，并提供可直接加载的小型 `.umlproj` 跨图案例；高级项目案例不放入 skill
- **流式进度**：每步工具调用、参数与返回实时推送，开发过程全程可见
- **中断控制**：随时停止 Agent 执行
- **多会话持久化**：新建 / 切换会话，对话历史刷新不丢失；会话日志落盘（Markdown + JSONL trace）
- **可插拔记忆系统**：任务结束自动归档、新任务按相关性召回注入；默认 SQLite/BM25 provider 可关闭或替换，不改变 Agent 主流程

### DevAgent 能力基准体系

评测体系只保留生产链路 **DevAgent**，不再维护 Legacy / ReAct 独立评测方案，避免不同 Agent 路径干扰结果。每个评测用例由受控 JSON 描述，绑定固定项目 fixture 和项目 manifest，在隔离工作区中执行：

- **用例目录**：`backend/evals/cases/`，当前 18 个用例，按 `baseline`、`p0`、`p1`、`p2`、`diagnostic` 和 `trace-3.1` 套件组织。
- **执行链路**：`case → fixture/project manifest → DevAgent → hard checkers/checkers → Trace + JSONL result`。
- **确定性检查**：支持 pytest、UML 有效性/结构/方法/时序、文件存在/内容、受保护路径未变更等检查器。
- **运行边界**：每个用例可配置最大执行时间、Tool Calls 和 Total Tokens；结果保留状态、得分、耗时、模型、Token、工具调用、Trace ID 和检查器明细。
- **基线快照**：当前基线版本为 `a1122e8`，16 个用例通过 10 个、失败 1 个、超时 5 个，通过率 62.5%，平均得分 66.67%，累计 6,639,458 Tokens、602 次工具调用。完整指标见 `docs/devagent-evaluation-baseline-2026-09-01.md`。
- **版本标识**：评测中心自动读取当前 Git 分支和 HEAD commit，使用 `branch@commit` 作为版本；工作区有未提交修改时标记为 `dirty`。
- **运行与归档**：评测中心支持按套件一键运行、实时查看批次和结果，并将已完成批次或基线快照一键归档到 `temp/evals/archives/`。CLI 可运行全部用例或指定套件：

  ```bash
  python -m extensions.evals.cli
  python -m extensions.evals.cli --suite p0
  ```

  CLI 在存在失败或超时时返回非零退出码；这表示评测结果未全通过，不代表评测框架启动失败。

### 全局优化

点击工具栏"全局优化"按钮，输入需求描述即可：

- **从零生成**：无需预设空白图，LLM 自动生成全部所需图及标签页
- **V2 直连引擎**：scope 分析（轻量模型）→ 单次 LLM 生成（pro 模型）→ 程序化跨图验证 + 自动修复 + 自动布局
- **流式绘图**：边生成边显示到画布，支持中途取消
- **多图 Diff**：结果按图切换对比，可逐图接受或继续优化

### TestHub 测试中心

Excel 用例驱动的测试代码生成：

- **用例管理**：加载 testHub 目录下的 Excel 用例表，在线编辑并回写保存
- **测试代码生成**：全量 / 增量（仅变更用例）两种模式生成测试代码，右侧"用例代码"页签查看、复制、下载
- **评审留痕**：查看、编辑、生成、接受 / 拒绝等操作统一记录到 `dev_review.txt`

### Trace 追踪与回放

- **全程记录**：Agent 会话自动落盘 JSONL trace（LLM 请求/响应 + 工具调用逐步事件）
- **TraceViewer**：前端抽屉式浏览历史会话，步骤级展开每次工具调用的参数与返回
- **长 Prompt 查看**：超长 LLM Prompt 和工具 schema 默认保持紧凑，用户可以展开，在可滚动面板中查看完整内容
- **确定性回放**：`mock` 模式零网络按记录重放（含游标一致性校验）；`rerun` 模式真实 LLM 重跑、工具仍按记录 mock —— 用于调试 prompt 与回归对比

### 知识图谱

SQLite 图数据库 + FTS5 全文索引，知识图谱插件开启后在工程保存时自动重建，
为 AI 助手提供结构化项目理解：

- **三层知识**：项目层 → 实体层 → 关系层（继承/组合/依赖 + 设计-代码映射 + 测试覆盖）
- **双源构建**：设计层（UML JSON 自动同步）+ 代码层（AST 解析源码目录）

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
│   │   ├── api/                    # REST + WebSocket 路由 (files/llm/optimize_v2/testhub/trace/metrics/evals)
│   │   ├── core/                   # 鉴权与安全
│   │   ├── models/                 # Pydantic 数据模型
│   │   ├── services/               # LLM / 优化引擎 V2 / 布局 / trace 回放 / 会话管理
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
├── skills/uml-design-guide/        # UML 设计指南 (SkillTool 知识包 + 优化流水线共用)
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

可选配置包括 `DEEPSEEK_MODEL`、五类插件的 `AGENT_*_ENABLED` 开关和 `AGENT_*_PROVIDER` 入口。配置定义集中在 `backend/config/settings.py` 与 `backend/config/agent_config.py`，插件实现统一位于 `extensions/`，由 `backend/app/agent_base/core/plugins.py` 加载。将开关设为 `false`，或将 Provider 设为 `none`、`noop`、`disabled`，即可关闭插件；当前不使用模型路由或 `SUB_AGENT_MODEL`。设置 `INTERNAL_API_TOKEN` 后，需在 `frontend/.env.local` 设置相同的 `VITE_API_TOKEN`。Windows 下命令执行优先使用配置的 WSL 环境；依赖安装完成后，也可直接运行 `start.bat` 一键启动前后端。

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

- 后端 REST API 默认前缀为 `/api`，包含文件操作、LLM、全局优化、TestHub、Trace、Agent 指标和 DevAgent 评测接口。
- 对话 Agent 使用 WebSocket：`/api/ws/chat`。
- API 文档：启动后访问 `http://localhost:8001/api/docs`。
- 单元测试：`cd backend && python -m pytest -q`。
- 评测全部 DevAgent 用例：在仓库根目录运行 `python -m extensions.evals.cli`。
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
