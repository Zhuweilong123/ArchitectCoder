# my_tools — AI 开发助手的工具集

基于 BaseAgents 框架构建的「AI 开发助手」（对话 Agent，WebSocket `/api/agent/ws/chat`）
可调用的工具集。所有会话工具由 `create_conversation_tools()` 工厂装配。

> **历史说明**：V1 的「代码生成闭环」工具（`generate_code` / `validate_code` /
> `generate_tests` / `fix_code` / `run_tests` / `write_files` 及 `uml_optimizer.py` /
> `code_validator.py` / `code_fixer.py` / `dev_system.py`）已下线移除（2026-08）。
> 当前助手不再内置专用代码生成工具，改为通过**文件系统原语**自主读写代码。

## 架构概览

```
用户消息
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│             对话 Agent (ReActAgent, native FC)           │
│  system_prompt: DevPromptBuilder（行为准则 + 项目上下文）   │
│  tools: create_conversation_tools() 装配的工具集           │
│  max_steps: agent_max_steps                               │
│                                                          │
│  read_file / write_file / edit_file / glob / bash         │  ← 文件系统原语
│  find_nodes / expand_neighbors / analyze_impact /          │  ← KG 结构化理解
│  get_project_map / compare_design_code                     │
│  todo_write                        ← 会话任务列表         │
│  skill                             ← 领域知识包（L1/L2/L3）│
│  spawn_subagent                    ← 通用子代理（toolkit） │
│  create_task / update_task / ...   ← 持久化任务 DAG       │
│  submit_uml_review                 ← UML diff 人工审核    │
│  bash 敏感命令                      ← 人工批准后才执行      │
└─────────────────────────────────────────────────────────┘
```

## 文件说明

| 文件 | 组件 | 职责 |
|---|---|---|
| `conversation_tools.py` | `AsyncTool` 基类 + `ProgressRelay` + `create_conversation_tools()` | 工具基类、子 Agent 进度转发、会话工具工厂 |
| `file_system_tools.py` | `ReadFileTool` / `WriteFileTool` / `EditFileTool` / `GlobTool` / `BashTool` | A 层文件系统原语（读/写/改/查/跑命令），bash 两级防护 |
| `todo_tools.py` | `TodoWriteTool` | 会话级任务列表（`todo_write`） |
| `skill_loader.py` | `SkillTool` + L1/L2/L3 渐进式披露 | 按需加载 `skills/` 下的领域知识包（`skill`） |
| `subagent_tool.py` | `SpawnSubagentTool` | 通用子代理（受限工具集 + `sub_agent_model`） |
| `uml_tools.py` | `UmlValidationTool` | UML 跨图引用验证（可复用，未自动注册） |
| `explore_project_tools.py` | 项目探索（summary/locate） | ⚠️ 当前未接线（`conversation_tools.py` 中注释） |
| `knowledge_graph_tools.py` | 旧版 5 个知识图谱工具 | ⚠️ 未接线（供 explore 内部使用，仍有使用方故保留） |
| `knowledge_graph_v2_tools.py` | 新版 5 个知识图谱工具（动词命名，分层架构） | ✅ 已接线（`create_conversation_tools()`） |
| `project_info_tools.py` | `project_info` / `read_file` / `grep` | ⚠️ 当前未接线 |

## 会话工具清单（`create_conversation_tools()` 装配）

| 工具 | 内部实现 | 功能 |
|---|---|---|
| `read_file` | `ReadFileTool` | 按行读文件，支持 `offset`/`limit` 切片 |
| `write_file` | `WriteFileTool` | 写文件（覆盖/新建，自动建父目录） |
| `edit_file` | `EditFileTool` | 精确文本替换（只替换首次出现） |
| `glob` | `GlobTool` | 按 glob 模式查找文件 |
| `bash` | `BashTool` | 跑 shell 命令（超时守卫 + 高危拒绝 + 敏感人工审核） |
| `find_nodes` | `KgLocateTool` | 图谱全文检索：类/方法/组件在哪、某功能在哪个文件（code 节点带 read_file 坐标） |
| `expand_neighbors` | `KgExpandTool` | 邻域展开：某节点的依赖/方法/关系，带距离与边类型 |
| `analyze_impact` | `KgImpactTool` | 反依赖影响分析：改 X 会碰谁（直接/传递，含测试） |
| `get_project_map` | `KgMapTool` | 项目结构地图：图清单、承重墙类、源码/测试统计 |
| `compare_design_code` | `KgDiffTool` | 设计 vs 代码漂移：缺失实现/多余代码/签名不匹配/未测 |
| `todo_write` | `TodoWriteTool` | 维护会话任务列表，跟踪长任务子步骤 |
| `skill` | `SkillTool` | 加载 `## Skills` 目录中的知识包正文或引用文件 |
| `spawn_subagent` | `SpawnSubagentTool` | 委托子任务，返回 summary；`toolkit` 决定受限子工具集，防递归 |
| `create_task` / `update_task` / `list_tasks` / `get_task` / `claim_task` / `complete_task` / `create_worktree` | `task_system.py` | 持久化任务 DAG + 认领/完成 + git worktree |
| `submit_uml_review` | `SubmitUmlReviewTool` | UML diff 人工审核（暂停等待 accept/reject） |

> `create_conversation_tools()` 返回 `(tools, review_manager)`。`review_manager`
> 供 bash 敏感命令与 `submit_uml_review` 共用同一审核通道。

## 工具详解

### 文件系统原语（`file_system_tools.py`）

借鉴 Claude Code 范式的 A 层工具，为助手补齐底层动手能力。所有文件操作经
`safe_path()` 守卫在 workspace 内（source_dir / test_dir / design_dir 三个 root）。

**bash 两级防护**：
- **高危命令**（磁盘格式化、分区、引导破坏等）→ 直接拒绝，无申诉（`DENY_LIST`）。
- **敏感命令**（强制删除、提权、注册表、进程强杀、`git reset --hard` 等）→ 经
  `ReviewManager` 请求人工批准，批准才执行；拒绝/超时/无审核通道均不执行
  （fail closed）。超时上限 `BASH_REVIEW_TIMEOUT`。
- 其余命令带 120s 超时直接放行；输出经 `TruncateHook` 截断。

### 知识图谱理解（`knowledge_graph_v2_tools.py`）

KG 提供**文件原语给不了**的结构化答案：类型化关系、设计-代码一致性、大项目的
有界地图。分工：KG 回答「有没有/谁依赖谁/设计实现没」，`read_file`/`bash` 回答
具体内容与符号——KG 是索引/地图，文件原语是内容。

- `find_nodes`：图谱全文检索（BM25 + 名称匹配）。code 层节点返回 `file` +
  `offset`/`limit`（0 基，read_file 就绪）——构成「kg 定位 → read_file 精读」链路。
- `expand_neighbors`：n-hop 邻域展开，`direction='incoming'` 即「谁依赖 X」。
- `analyze_impact`：反依赖影响分析，直接/传递分级，按节点类型分组，含测试文件。
- `get_project_map`：有界结构地图（图/类/文件统计 + in-degree 承重墙），非全量 dump。
- `compare_design_code`：设计 vs 代码漂移（missing/extra/mismatch/no_coverage），
  首次调用可能惰性索引代码层。

设计要点：紧凑序列化（丢弃 `methods[]`/`attributes[]` 全量）+ 显式截断标记；
`project_id` 由工厂绑定，agent 无需手填。重分析型工具（`analyze_impact` /
`get_project_map` / `compare_design_code`）可通过
`spawn_subagent(toolkit="kg_analysis")` 委派，避免重输出污染主上下文；
`find_nodes` / `expand_neighbors` 保留直连以支撑 locate→read 链路。

### Skill（`skill_loader.py`）

渐进式披露三级：
- **L1** `build_skills_section()`：name + description 目录，进静态 system prompt。
- **L2** `skill(name)`：SKILL.md 正文 + 同目录引用文件清单。
- **L3** `skill(name, file=...)`：单个引用文件正文。

L3 必须由本工具投递而非走 `read_file` —— `safe_path()` 把路径限制在用户项目的
三个 workspace root 内，而 `skills/` 在仓库根目录，`read_file` 必然抛
`Path escapes workspace`。

### 子代理（`subagent_tool.py`）

`spawn_subagent` 按 `toolkit` 参数选择受限子工具集 + `sub_agent_model` 独立跑
简化 FC 循环，避免主上下文膨胀。三个工具包：

- `standard`：文件系统原语 + skill（可读写，默认）
- `read_only`：`find_nodes` / `expand_neighbors` / `read_file`（只读理解，无写入）
- `kg_analysis`：`find_nodes` / `analyze_impact` / `get_project_map` /
  `compare_design_code` / `read_file`（KG 分析三件套，无编辑权）

任何工具包**不含** `submit_uml_review` / `spawn_subagent`，防止递归子代理与
UML 审核嵌套；bash 敏感命令仍走人工审核（与主代理共用同一通道），防止委托绕过。

### 任务系统（`task_system.py`）

持久化任务 DAG（`create_task`/`claim_task`/`complete_task` 等）+ git worktree 隔离，
支持跨会话任务跟踪。

### UML 验证（`uml_tools.py`）

`UmlValidationTool`（`validate_uml_design`）做 UML 跨图引用验证：生命线 `class_ref`
→ 类 ID、消息方法名 → 类方法签名、组件接口一致性、组件图覆盖度，支持模糊匹配
自动修复。**未自动注册**进会话工具集，供 demo / 测试 / 未来按需接入使用。

## 人工审核机制

`ReviewManager`（`base/tools/review.py`）提供「提交审核 → Future 阻塞 → 人工响应
回灌」的通用机制，当前两个使用方：

1. **`submit_uml_review`**：Agent 修改 UML 后调用 → 编排层推送 DiffViewer 审核
   → 前端 accept/reject → 结论回灌 → Agent 被拒则修订后重新提交。
2. **`BashTool` 敏感命令**：敏感命令请求人工批准，批准才执行。

## 用法示例

```python
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools,
)

llm = BaseAgentsLLM.from_settings()
tools, review_mgr = create_conversation_tools(
    llm, source_dir="src/", test_dir="tests/", project_file="project.umlproj",
)
registry = ToolRegistry()
for t in tools:
    registry.register_tool(t)

agent = ReActAgent(
    name="DevAgent",
    llm=llm,
    tool_registry=registry,
    max_steps=12,
    use_native_fc=True,
)

async for progress in agent.arun_stream("帮我实现登录模块"):
    print(progress.to_dict())
```

## 与 API 的关系

| API | 说明 |
|---|---|
| `/api/agent/ws/chat`（`agent_chat_ws.py`） | 对话 Agent 运行时入口，装配 `create_conversation_tools()` |
| `/api/llm/optimize-uml` | 单图优化（独立于 Agent 工具集） |
| `/api/optimize_v2/*` | 全局优化 V2 引擎（独立于 Agent 工具集） |
