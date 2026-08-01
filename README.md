<div align="center">

# UML Designer

**让想法成为架构，让架构驱动开发**

[English](README_EN.md) | **中文**

</div>

支持类图、时序图、组件图，从设计到代码生成、测试构建、代码优化的全流程闭环；并内置 **对话 Agent**、**知识图谱** 与 **BaseAgents Agent 框架**，让 AI 真正参与架构设计与开发。

https://github.com/user-attachments/assets/6e78effa-e00b-4e69-bdfb-2c3edbb011b9

## 为什么需要 UML Designer？

架构图不该是画完就过期的文档——它们应该是驱动整个开发流程的**唯一真相源**。

- **大规模一致性检验，机器比人可靠。** 50 个类、30 个组件、成千上万个引用关系，没人能手工逐一核对。我们的自动校验引擎可以——5 项跨图一致性检查，模糊匹配自动修复，在你发现之前就帮你修正。
- **设计意图贯穿始终，不随开发偏移。** AI 自动提取关键设计约束——哪些实体不可变、哪些关系必须保留、哪些架构决策是基石——注入到代码生成和测试阶段，相当于架构师盯着每一行代码落地。
- **从自然语言到可运行的代码，一条龙打通。** 产品经理用自然语言描述需求 → 自动生成全套 UML 架构 → 代码生成 + ReAct 验证 → 测试生成 + pytest 真跑 → 失败反馈回修源码。每一步都有 AI 看护。

## 功能特性

### 多图类型编辑器

| 图类型 | 核心元素 | 交互方式 |
|--------|---------|---------|
| **类图** | 类节点 + 关系连线（6 种） | 双击添加类 / 拖拽端口创建关系 |
| **时序图** | 生命线 + 消息箭头（5 种） | 双击添加生命线 / 点击 A→B 创建消息 |
| **组件图** | 组件节点 + 依赖箭头 | 双击添加组件 / 双击组件内部创建子组件 |

### 核心编辑
- 撤销/重做（50 步）：拖拽、属性面板连续编辑自动合并为单步；类/关系/生命线/消息/组件/组合片段的全部编辑均可撤销
- 缩放：工具栏 +/−/重置 与 Ctrl+滚轮双向同步，缩放级别随工程持久化；空格/中键平移
- 网格吸附与自定义（大小/颜色/粗细）
- Ctrl+C/V 复制粘贴元素，Ctrl+S 保存工程
- 属性编辑：选中元素后在右侧面板编辑

### 项目管理
- `.umlproj` 工程文件：一个工程包含多张不同类型的设计图
- **分组下拉切换**：工具栏按组件图/类图/时序图分组为三个下拉按钮，同类型图归组管理，颜色编码（橙/蓝/绿），一键切换
- 下拉菜单项内直接删除图（🗑 图标），无需右键菜单
- **组件-图层级关系**：类图和时序图可关联到组件图中的组件节点，右键组件查看/新建关联图，形成 `项目 → 组件 → 类图/时序图` 的三层组织架构
- 工具栏显示组件归属（如 `AuthService › 类图`）
- 向下兼容：打开旧 `.uml` 文件自动包装为工程
- 目录选择自动持久化（localStorage）

### LLM 集成

#### 两个入口，灵活选择

| 功能 | 按钮 | 说明 |
|------|------|------|
| **单图设计** | 工具栏蓝色按钮 | 仅优化/生成当前打开的这张图（类图/时序图/组件图），弹窗收集需求 |
| **全局优化** | 工具栏紫色按钮 | 需求驱动的综合设计——交叉校验所有图的一致性，**无需预设空白图**，从自然语言描述直接生成全部所需 UML 设计 |

#### 对话 Agent（AI 开发助手 · 浮动对话面板）

右下角机器人按钮打开可拖拽/缩放的对话面板，**一个 ReActAgent 承接全部消息**——闲聊直接文本回复，开发需求则自动编排工具完成完整开发流程。

- **单 Agent 设计**：同一 Agent 依据精简的 system prompt（纯英文行为准则）自行决定是否调用工具；跨轮复用对话历史
- **行为准则**：直接给答案不重复问题、涉及代码先看已有实现、用户未提明确任务（仅问候/感谢/评论/聊天）时简短回复不调工具——避免闲聊被过度工具化
- **13 个工具 + 人工审核**：开发工具 `optimize_uml`→`generate_code`→`validate_code`→`generate_tests`→`run_tests`→`fix_code`→`write_files`，知识图谱 `kg_query`/`kg_expand`/`kg_trace`/`kg_diff`，项目信息 `project_info`、文件读取 `read_file`，外加 `request_review` 关键节点人工审批
- **知识图谱主动探索**：`kg_query` 支持按需构建（空库自动从设计文件重建）、空 pattern 枚举全部节点、驼峰类名模糊匹配；`kg_expand`/`kg_query` 序列化结果带 `source_file` 绝对路径，实现"**KG 定位元素 → 找到文件 → read_file 读整文件**"的闭环
- **项目信息按需获取**：`project_info` 返回设计文件/源码/测试目录文件清单（递归含子包），`read_file` 读取文件完整内容（限定项目目录内）——不再每轮注入 prompt，首轮 token 更省、信息永远新鲜
- **流式进度**：每一步的工具调用、参数与返回实时推送到面板，开发过程全程可见
- **中断控制**：随时停止 Agent 执行，优雅终止工具循环
- **会话日志**：每次会话落盘 `temp/chat_log/` —— 人读 Markdown（`chat_*.md`）+ 机器可回放 JSONL trace（`trace_*.jsonl`，含 LLM 原始往返、工具调用、审核记录；system prompt 置顶、tools 沉底、`system_prompt` 字段与 `messages` 去重）
- **消息持久化**：刷新页面不丢失对话历史

#### 全局优化（需求驱动 · 无需预设空白图）
- **从零生成**：用户只需输入需求描述（如"设计一个车机 OTA 升级系统，包含云端下发、TBox 转发、MDC 执行三层架构"），LLM 自动生成组件图、类图、时序图（支持同类型多张图）
- **自动创建图标签页**：前端根据 LLM 返回的 `diagrams` 数组自动创建对应的图标签页，无需手动添加空白图
- **多图交叉验证**：已有图时，LLM 同时分析所有图进行跨图一致性校验和协同优化
- **`component_id` 语义自动识别**：LLM 自动建立组件-图的层级关联
- **两种模式**：完整模式（一次性返回）+ 流式模式（动态绘图，边生成边显示，支持中途取消）
- **多图 Diff 面板**：结果支持按图切换查看对比，可逐图接受或继续优化

#### 跨图引用索引与自动校验（全局优化增强）
- **预计算引用索引**：LLM 调用前，后端自动扫描所有图的类、生命线、组件、接口，生成结构化的跨图引用索引注入 prompt，让 LLM 有完整的上下文而非自行扫描 JSON
- **后校验引擎**：LLM 返回后自动校验 **5 项**跨图一致性：
  - 生命线 `class_ref` → 类 ID 有效性（自动模糊匹配修复）
  - 时序图消息方法名 → 类方法签名匹配
  - 图 `component_id` → 组件 ID 有效性
  - 组件接口 ↔ 类接口一致性
  - **组件覆盖率**：检查每个组件是否有关联的类图和时序图（❌缺失 → warning / ⚠️缺少某一类 → info）
- **自动修复**：明确的引用错误（如 `class_ref` 指向不存在的 ID）通过名称模糊匹配自动修正，标记为"已自动修复"
- **Diff 面板分类展示**：一致性报告区分 ❌错误 / ⚠️警告 / 🟢已自动修复，一目了然
- **跨图设计指南**：`uml_guide/cross_diagram_guide.md`，包含 `component_id`/`class_ref` 使用规范、三图一致性检查清单、典型设计模式、常见错误与修正示例

#### 组件清单与多图关联（全局优化增强）
- **组件清单（Component Manifest）**：prompt 中注入组件覆盖率状态表（❌ 缺失 / ⚠️ 部分 / ✅ 完整），LLM 明确知道每个组件缺哪些图、应补哪些图
- **多图关联规则**：prompt 中明确定义组件-图 `1:N` 关系——一个组件可以关联多张类图（按层拆分）和多张时序图（按场景拆分），所有图共享同一个 `component_id`
- **从零生成工作流**：空项目时 prompt 引导 LLM 按 Step 1（组件图，建立 ID 锚点）→ Step 2（逐组件类图）→ Step 3（逐组件时序图）顺序生成，确保关联完整
- **关联完整性检查**：post-validation 新增 Check 5，检查 LLM 输出中每个组件是否都有至少一张类图和一张时序图
- **设计指南同步**：`cross_diagram_guide.md` 新增 §1.4 组件-图多对多关联规范，包括关联模型、何时需要多张图、检查清单；§4.7 新增组件覆盖率不足的错误示例

#### 自动布局引擎
- **极简集成**：`backend/app/services/layout_engine.py` — 独立模块，一个 `auto_layout(result)` 调用
- **三种布局算法**：
  - **类图**：继承链分层（父类在上、子类在下）+ 非继承类网格布局
  - **时序图**：生命线等距水平排列（间距 200px），消息按 `order` 垂直递增（间隔 45px）
  - **组件图**：三阶段流式布局 — 子组件先行计算空间 → 父组件按需扩展 → 顶层组件流式排列自动换行
- **仅影响新生成元素**：LLM 生成时坐标全为 (0,0) 的元素自动重排，用户手动拖拽的位置完全保留
- **前端高度自适应**：时序图生命线高度不再使用固定公式估算，改为基于消息实际 Y 坐标动态计算，消息再多也不会出现连接线悬空
- **插入管道**：在 LLM 返回 → 字段归一化 → 跨图校验 → `auto_layout()` → 返回前端，零额外延迟

#### 单图设计
- **单图优化**：LLM 分析并优化单个图设计，生成 diff 对比
- **单图生成**：当前图为空时，LLM 根据需求描述生成全新设计
- 优化结果自动推送至 Diff 面板，可接受/拒绝/继续优化
- **双模型策略**: 主模型 `deepseek-v4-pro`（复杂任务）+ 轻量模型 `deepseek-v4-flash`（简单任务），按场景选配节省成本；Sub-agent 默认使用 flash 模型
- **代码生成**: 调用 LLM 生成 12 种编程语言代码（类图）
- **已有代码适配**: 加载已有源码，LLM 根据 UML 设计适配/优化
- **增量测试更新**: 加载已有测试，LLM 根据用例变更增量修改，ReAct 校验测试代码正确性
- **ReAct 代码看护**: 源码生成 + 测试生成后均有 ReAct 引擎自动验证（原生 Function Calling）。验证工具链：`check_imports`（从磁盘读取 → 语法+导入检测）、`run_module`（从磁盘读取 → 运行时验证）、`run_bash`（白名单安全沙箱 → 环境查询/测试执行）、`analyze_error`（错误定位分析）、`finish_optimization`（退出信号）。配置变更上限（%）后自动拦截超大改动，要求 LLM 精简
- **LLM 调用可靠性**:
  - 空响应自愈：API 返回空 / 被截断时自动重试（降级 json_mode + 翻倍 max_tokens）
  - 响应 fallback 解析：JSON 解析失败时自动降级提取 markdown 代码块或包装原始输出
  - 工具参数 JSON 截断防护：Function Calling 参数异常时拒绝并反馈，避免静默崩溃
  - 代码文件先落盘再校验：生成代码写入 `generated/src/` 后 ReAct 验证，工具从磁盘读取无需搬运源码
- **设计约束跨阶段传递**: 设计优化完成后 LLM 自动提取关键设计约束（不可变实体、保留关系、设计理由），注入后续代码生成与测试阶段的 system prompt，防止偏离原始设计意图
- **逐文件测试生成**: 有大量用例时按模块拆分调用 LLM，每次只生成单个源文件对应的测试（含 UML 全局架构 + 依赖类 API 签名），避免单次调用 token 上限截断

### 知识图谱系统

**从"被动注入"到"主动探索"** — 为 AI 助手提供结构化的项目理解能力，让模型按需查询项目结构，而非预先塞满上下文窗口。

- **三层 knowledge**：项目层（项目有哪些图）→ 实体层（图里有哪些类/方法/属性）→ 关系层（继承/组合/依赖等关联 + 设计-代码映射 + 测试覆盖）
- **SQLite 图数据库 + FTS5 全文索引**：节点/边/全文三表，content-sync 模式触发器自动维护索引，无需额外数据库服务
- **双源构建**：
  - **设计层（声明式）**：项目保存时 daemon 线程自动从 UML JSON 幂等重建
  - **代码层（探索式）**：Agent 首次调用 `kg_diff` 时自动递归 AST 解析源码目录（支持子包结构），`IMPLEMENTS` 边打通设计与代码；code 层 class/method/attribute 节点携带 `filename` 定位属性
- **4 个 Agent 工具**：`kg_query`（BM25 全文检索 + 名称模糊匹配，空 pattern 枚举全部节点，项目未索引时自动按需构建）、`kg_expand`（n-hop 邻域展开）、`kg_trace`（依赖路径追踪）、`kg_diff`（设计 vs 代码差异检测：缺失实现/多余代码/签名不匹配/无测试覆盖）
- **代码定位能力**：`kg_query`/`kg_expand` 序列化 code 层节点时附加 `source_file` 绝对路径，配合 `read_file` 工具实现"KG 定位元素 → 读取源码文件"——知识图谱作为加载哪些原始文件的依据
- **集成点**：`file_service` 保存 Hook 自动重建 + 对话 Agent 工具注册，全程对用户透明

### 跨会话记忆系统

基于 **SQLite + FTS5 + jieba 分词** 的记忆模块（`memory_system/`），为 Agent 提供跨会话上下文感知能力：

- **BM25 全文检索**：中文语义分词，召回精度显著优于字符级 bigram；jieba 缺失时自动回退
- **自动记忆提取**：LLM 交互后自动提取 summary + original_text 双字段记忆
- **生命周期管理**：强化（reinforce）/ 衰减（decay）/ 淘汰（prune）三阶段机制，LFU 淘汰保护 pinned + 热记忆
- **WAL 模式**：支持并发读写，原子操作；内置 `migrate.py` 从旧版 JSON 存储迁移

### BaseAgents — 轻量 Agent 框架

项目内置一套**分层解耦、职责单一、接口统一**的 Agent 框架（`backend/app/agent_base/`）：

| 层 | 模块 | 职责 |
|----|------|------|
| **core** | `agent` / `llm` / `message` / `config` / `exceptions` | 抽象基类 + 统一 LLM 接口 + 消息与配置 |
| **agents** | `SimpleAgent` / `ReActAgent` / `ReflectionAgent` / `PlanAndSolveAgent` | 四种经典 Agent 范式 |
| **tools** | `base` / `registry` / `chain` / `async_executor` | 万物皆为工具，注册/发现/执行/并行 |

- **四种范式**：Simple（基础对话）、ReAct（思考→行动→观察循环）、Reflection（生成→审查→精炼，支持外部验证 Hook）、Plan-and-Solve（先规划后执行）
- **工具系统**：Tool ABC + ToolRegistry + ToolChain + AsyncToolExecutor，支持原生 Function Calling
- **开箱即用**：`BaseAgentsLLM.from_settings()` 一行对接项目现有配置；`optimize_project_v2` 以三阶段反射循环替代原有单次调用

### 安全
- Bearer Token API 鉴权（可配置，本地开发自动跳过）
- 路径安全校验，防目录遍历
- API Key 环境变量隔离

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端框架 | React 18 + TypeScript |
| 图形引擎 | AntV X6 |
| 状态管理 | Zustand |
| UI 组件库 | Ant Design 5 |
| 代码编辑器 | Monaco Editor |
| 后端框架 | FastAPI (Python) |
| Agent 框架 | BaseAgents（Simple / ReAct / Reflection / Plan-and-Solve） |
| 知识图谱 | SQLite 图数据库 + FTS5 全文索引 |
| 记忆系统 | SQLite + FTS5 + jieba 分词 |
| LLM | DeepSeek API（v4-pro + v4-flash 双模型） |
| 测试框架 | pytest (真实子进程执行) |
| 构建工具 | Vite |

## 项目结构

```
uml_designer/
├── frontend/                       # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── Canvas/
│   │   │   │   ├── UMLEditor.tsx   # 类图编辑器
│   │   │   │   ├── SeqEditor.tsx   # 时序图编辑器
│   │   │   │   └── CompEditor.tsx  # 组件图编辑器
│   │   │   ├── AgentChat/          # 对话 Agent 浮动面板 (WebSocket)
│   │   │   ├── PropertyPanel/      # 属性编辑面板
│   │   │   ├── Toolbar/            # 工具栏
│   │   │   ├── CodeViewer/         # 代码查看器 (Monaco)
│   │   │   ├── TestCodeViewer/     # 测试代码查看器
│   │   │   ├── TestCaseViewer/     # 用例检视表格
│   │   │   ├── PipelineConsole/    # 流水线控制台
│   │   │   └── DiffViewer/         # Diff 对比视图
│   │   ├── stores/                 # Zustand 状态管理
│   │   ├── services/               # API 服务层 (含 agentChat WebSocket)
│   │   ├── types/                  # TypeScript 类型 (uml, sequence, component, pipeline)
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── api/                    # REST + WebSocket 路由 (含 /api/agent/ws/chat)
│   │   ├── core/                   # 配置 / 鉴权 / 安全
│   │   ├── models/                 # Pydantic 数据模型
│   │   ├── services/               # LLM / 代码生成 / ReAct / 流水线 / 对话 Agent / trace
│   │   ├── agent_base/             # BaseAgents 框架 (core/agents/tools)
│   │   └── main.py
│   ├── requirements.txt
│   └── .env
├── knowledge_graph/                # 知识图谱系统 (SQLite + FTS5)
├── memory_system/                  # 跨会话记忆系统 (SQLite + FTS5 + jieba)
├── uml_guide/                      # UML 设计指南 (含跨图一致性规范)
├── generated/                      # 生成的代码输出 (src/ + test/)
├── temp/                           # 后端临时文件（不上库）
│   ├── uml_files/                  # UML / umlproj 保存目录
│   ├── chat_log/                   # 对话 Agent 会话日志 (chat_*.md + trace_*.jsonl)
│   ├── pipeline_log/               # 流水线运行报告 + LLM 交互日志
│   ├── testHub/                    # Excel 用例库默认目录
│   └── dev_review.txt              # 统一评审记录
├── .claude/                        # Claude Code 配置
└── README.md
```

## 快速开始

### 1. 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `backend/.env`：

```env
DEEPSEEK_API_KEY=你的DeepSeek密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro        # 默认主模型（复杂任务）
SUB_AGENT_MODEL=deepseek-v4-flash     # Sub-agent 模型（轻量省钱）
INTERNAL_API_TOKEN=                    # 可选，设置后启用 API 鉴权
```

### 3. 启动后端

```bash
cd backend
python -m app.main          # http://localhost:8000 | API 文档: /api/docs
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

### 生产部署鉴权

```env
# backend/.env
INTERNAL_API_TOKEN=你的随机密钥

# frontend/.env.local
VITE_API_TOKEN=你的随机密钥
```

生成随机密钥：`python -c "import secrets; print(secrets.token_urlsafe(32))"`

## 使用指南

### 项目管理

1. 工具栏"+"下拉 → 添加类图/时序图/组件图
2. 图标签页切换当前编辑的图，**右键删除**不需要的图
3. Ctrl+S 保存为 `.umlproj` 工程文件
4. 工具栏"打开" → 浏览任意目录，手动输入路径或点击快捷入口（桌面/文档/磁盘），选择 `.umlproj` 或 `.uml` 文件打开

### 组件-图关联

1. 在**组件图**中创建组件（如 `AuthService`、`PaymentService`）
2. **右键**组件节点 → 点击「新建类图」或「新建时序图」→ 自动关联并切换到新图
3. 工具栏图标签页显示组件归属（`组件名 › 图类型`）
4. 再次右键组件 → 菜单中列出所有关联的类图和时序图 → 点击切换
5. 选中组件 → 右侧属性面板底部也显示关联图列表
6. **全局优化**时 LLM 自动识别组件层级关系，为各组件生成/优化关联的类图和时序图，支持从需求描述直接生成完整的三层架构设计

### 类图
- **添加类**: 双击画布空白区域
- **创建关系**: 从节点端口拖拽到目标节点
- **编辑属性**: 单击类或关系，右侧面板编辑

### 时序图
- **添加生命线**: 双击画布空白区域
- **创建消息**: 点击生命线 A → 再点生命线 B
- **自反消息**: 点击同一生命线两次
- **编辑消息**: 点击消息线，右侧面板修改类型和备注

### 组件图
- **添加顶层组件**: 双击画布空白区域
- **添加子组件**: 双击父组件内部
- **创建依赖**: 从节点端口拖拽到目标节点
- **调整大小**: 拖拽组件边角
- **组件右键菜单**: 右键组件节点 → 查看该组件关联的类图和时序图 → 一键新建或切换
- **属性面板**: 选中组件后在右侧面板查看关联图列表，支持快捷新建

### 对话 Agent（AI 开发助手）

右下角机器人按钮打开对话面板——把它当作一位能动手写代码的 AI 同事。

1. 点击右下角 **🤖 AI 开发助手** 按钮打开浮动对话面板（可拖拽、可放大）
2. 直接输入需求，例如：*"设计一个用户认证系统，包含注册、登录、密码重置"* 或 *"创建一个计算器系统，支持加减乘除"*
3. 闲聊会直接回复；开发需求会自动编排工具完成 UML 设计 → 代码生成 → 验证 → 测试 → 修复 → 落盘
4. 面板实时展示每一步的工具调用、参数与返回，开发过程全程可见
5. 关键节点可弹出**人工审核**（批准/拒绝/稍后），把 AI 开发关进护栏
6. 随时点击 **停止** 中断 Agent 执行；对话历史刷新不丢失
7. 已打开 `.umlproj` 工程并设置了源码/测试目录时，Agent 自动感知项目上下文，并可调用知识图谱查询项目结构

### 全局优化（需求驱动）

**无需预设空白图**——只需描述需求，LLM 自动生成全部所需 UML 设计。

1. 工具栏点击"全局优化"按钮（紫色图标）
2. 输入需求描述，例如：*"设计一个车机 OTA 升级系统，包含云端、TBox、MDC 三层组件架构，支持 OTA 任务调度和鸡叫任务管理"*
3. 勾选"动态绘图"可启用流式实时渲染（边生成边显示）
4. 完整模式：LLM 返回后自动创建图标签页并填充内容，Diff 面板支持按图切换对比
5. 流式模式：LLM 实时逐元素输出到画布，自动创建所需图类型
6. LLM 可生成同类型多张图（如两个时序图分别描述不同业务场景）

### 代码生成与测试

1. 在类图上点击 **生成代码** → 选择语言（支持 12 种）
2. 加载已有源码目录 → **已有代码适配**，LLM 根据 UML 设计优化代码
3. 加载用例库（Excel）→ 生成测试 → 真实 pytest 执行 → 失败自动回修源码
4. ReAct 引擎逐轮验证过程可在推理详情中展开查看

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+Z | 撤销 |
| Ctrl+Y | 重做 |
| Ctrl+C | 复制选中元素 |
| Ctrl+V | 粘贴 |
| Ctrl+S | 保存工程 |
| Delete | 删除选中元素 |
| Ctrl+滚轮 | 缩放画布 |
| 空格+拖拽 | 平移画布 |

## 支持的编程语言

Python, Java, TypeScript, JavaScript, C#, C++, Go, Rust, Ruby, Swift, Kotlin, PHP
