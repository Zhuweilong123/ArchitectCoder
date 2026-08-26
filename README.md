<div align="center">

# ArchitectCoder

**让想法成为架构，让架构驱动开发**

[English](README_EN.md) | **中文**

</div>

支持类图、时序图、组件图，从设计到代码生成、测试、修复的全流程闭环；内置 **AI 协同开发助手（对话 Agent）**、**TestHub 测试中心**、**Trace 追踪回放**、**知识图谱** 与 **BaseAgents 框架**。

## 为什么选择 ArchitectCoder？

- **设计即源码**：架构图是驱动代码生成、验证、测试的唯一真相源。
- **AI 自动验证**：跨图一致性检查 + 模糊匹配自动修复，引用关系不再靠人眼核对。
- **自然语言到可运行代码**：描述需求 → 自动生成全套 UML → Agent 自主生成代码并真跑 pytest → 失败自动回修。

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

右下角机器人按钮打开浮动对话面板，一个 ReActAgent（**设计 + 代码协同演进**：在现有代码上演进，不推倒重设计）承接全部消息。v2.0 起移除固定流水线编排，Agent 直接持有原生文件系统工具，自主规划开发步骤：

- **文件系统原语**：`read_file` / `write_file` / `edit_file` / `glob` / `bash` —— 读写代码、跑 pytest、修复失败，全由 Agent 自主编排
- **任务规划**：`todo_write` 维护会话任务清单，多步任务先规划、边做边更新状态
- **通用子代理** `spawn_subagent`：把探索、摘要、独立改动等自包含子任务委托给 flash 模型子代理（仅持受限文件系统工具集），只回传结论，避免主上下文膨胀；审核通道一并透传，委托子代理也无法绕过人工审核
- **持久化任务系统**：任务 DAG（创建 / 依赖 / 认领 / 完成）落盘保存 + git worktree 隔离工作区，支持长任务拆解与跨会话续做
- **人工审核**：UML 设计修改经 `submit_uml_review` 推送 diff 对比审批；`bash` 敏感命令（强制删除、进程终止、`git reset --hard` 等）执行前弹出批准/拒绝审核卡，高危命令（格式化、分区、写引导等）直接拒绝 —— 把 AI 开发关进护栏
- **流式进度**：每步工具调用、参数与返回实时推送，开发过程全程可见
- **中断控制**：随时停止 Agent 执行
- **多会话持久化**：新建 / 切换会话，对话历史刷新不丢失；会话日志落盘（Markdown + JSONL trace）
- **记忆系统**：跨会话记忆，任务结束自动归档、新任务按相关性召回注入，BM25 全文检索 + jieba 分词

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
- **确定性回放**：`mock` 模式零网络按记录重放（含游标一致性校验）；`rerun` 模式真实 LLM 重跑、工具仍按记录 mock —— 用于调试 prompt 与回归对比

### 知识图谱

SQLite 图数据库 + FTS5 全文索引，工程保存时自动重建，为 AI 助手提供结构化项目理解：

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
| LLM | DeepSeek API（v4-pro + v4-flash） |
| 知识图谱 | SQLite + FTS5 |
| 记忆系统 | SQLite + FTS5 + jieba |
| 测试 | pytest（真实子进程执行）+ openpyxl（Excel 用例） |
| 构建 | Vite |

## 项目结构

```
uml_designer/
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
│   ├── app/
│   │   ├── api/                    # REST + WebSocket 路由 (files/llm/optimize_v2/testhub/trace)
│   │   ├── core/                   # 配置 / 鉴权 / 安全
│   │   ├── models/                 # Pydantic 数据模型
│   │   ├── services/               # LLM / 优化引擎 V2 / 布局 / trace 回放 / 会话管理
│   │   ├── agent_base/             # BaseAgents 框架 (core/agents/tools)
│   │   │   └── tools/my_tools/     # 文件系统原语 / todo / 子代理 / 审核
│   │   └── main.py
│   ├── knowledge_graph/            # 知识图谱系统 (SQLite + FTS5)
│   ├── memory_system/              # 跨会话记忆系统 (SQLite + FTS5 + jieba)
│   ├── requirements.txt
│   └── .env
├── docs/                           # 设计文档 (BaseAgents / KG / 记忆 / trace)
├── uml_guide/                      # UML 设计指南
├── generated/                      # 生成的代码输出 (src/ + test/)
├── temp/                           # 运行时临时文件（不上库）
├── .claude/                        # Claude Code 配置
└── README.md
```

## 快速开始

```bash
# 后端
cd backend && pip install -r requirements.txt
# 编辑 .env：设置 DEEPSEEK_API_KEY
python -m app.main          # http://localhost:8001

# 前端
cd frontend && npm install && npm run dev   # http://localhost:3000
```

Windows 下也可直接运行 `start.bat` 一键启动前后端。

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
