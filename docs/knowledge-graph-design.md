# 知识图谱设计

> 本文归档 ArchitectCoder 的知识图谱系统（`backend/knowledge_graph/`）设计，
> 反映最新代码（5 个内部工具、增量重建、项目作用域 id、jieba 预分词）。
> 为 AI 助手提供结构化的项目理解能力，让模型按需查询项目结构而非被动接收全部内容。

## 1. 核心理念

**从「被动注入」到「主动探索」** —— AI 助手在推理过程中遇到不确定的依赖关系时，
主动调用工具查询图谱，而非预先塞满上下文窗口。

## 2. 架构

```
backend/knowledge_graph/
├── __init__.py                     # 统一导出入口
├── models.py                       # 数据模型 (GraphNode / GraphEdge / NodeType / EdgeType / 枚举)
├── database.py                     # SQLite 图数据库 + FTS5 全文索引
├── builder.py                      # GraphBuilder (设计层 + 代码层构建)
├── retriever.py                    # GraphRetriever (query / expand / trace / diff)
└── README.md                       # 本文件（迁移自旧 README）

backend/app/agent_base/tools/my_tools/
└── knowledge_graph_tools.py        # 5 个 AsyncTool (内部工具，供 explore_project 使用)
```

## 3. 数据模型

### 3.1 节点类型（NodeType）

| 类型 | 含义 | 来源 |
|------|------|------|
| `project` | 顶层项目 | design |
| `diagram` | 单张 UML 图（class/sequence/component） | design |
| `class` | 类 / 接口 / 抽象类 / 枚举 | design / code |
| `component` | 组件图节点 | design |
| `lifeline` | 时序图生命线 | design |
| `source_file` | 源码文件（.py 等） | code |
| `test_file` | 测试文件（test_*.py） | test |
| `method` | 方法 / 函数 | design / code |
| `attribute` | 属性 / 字段 | design / code |
| `interface` | 提供/需要的接口 | design |

### 3.2 边类型（EdgeType）

```
结构关系: contains
UML 语义: inherits | composition | aggregation | association | realization | dependency
设计-代码: implements | imports | tests | references
时序消息: messages
```

### 3.3 三层 knowledge

```
项目层: PROJECT → DIAGRAM                      (项目有哪些图)
实体层: DIAGRAM → CLASS → METHOD / ATTRIBUTE   (图里有什么实体)
关系层: CLASS → [INHERITS / COMPOSITION / ...] → CLASS
       SOURCE_FILE → [IMPLEMENTS] → CLASS
       TEST_FILE → [TESTS] → SOURCE_FILE
```

## 4. 数据库 Schema

### 4.1 kg_nodes（节点表）

```sql
CREATE TABLE kg_nodes (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT UNIQUE NOT NULL,
    node_type    TEXT NOT NULL,        -- NodeType 枚举值
    name         TEXT NOT NULL,        -- 人可读名称
    project_id   TEXT NOT NULL,        -- 项目隔离
    source       TEXT DEFAULT 'design', -- design | code | test
    properties   TEXT DEFAULT '{}',    -- 结构化属性 JSON
    content_text TEXT DEFAULT '',      -- FTS 索引用合成文本（jieba 预分词）
    embedding    BLOB,                 -- 向量预留
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

**自然键**：`(project_id, node_type, name, source)` —— 幂等构建，同名 UML 类保存两次
产生同一节点。

**节点 id**：项目作用域的确定性 hash（`_make_id(prefix, project_id, name, source)`），
而非文件实体 id —— 避免项目文件被复制时跨项目 id 冲突（历史教训：文件本地 id 全局
不唯一曾导致 rebuild 崩溃）。

### 4.2 kg_edges（边表）

```sql
CREATE TABLE kg_edges (
    rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT UNIQUE NOT NULL,
    source_id  TEXT NOT NULL,         -- GraphNode.id
    target_id  TEXT NOT NULL,         -- GraphNode.id
    edge_type  TEXT NOT NULL,         -- EdgeType 枚举值
    properties TEXT DEFAULT '{}',     -- 结构化属性 JSON
    weight     REAL DEFAULT 1.0,
    created_at TEXT NOT NULL
);
```

**去重键**：`(source_id, target_id, edge_type, properties)` —— 相同语义的边不重复创建。

### 4.3 kg_node_fts（FTS5 全文索引）

```sql
CREATE VIRTUAL TABLE kg_node_fts USING fts5(
    content_text,
    content='kg_nodes',           -- content-sync 模式，触发器自动同步
    content_rowid='rowid'
);
```

**content-sync 模式**：INSERT/UPDATE/DELETE 触发器自动维护 FTS 索引。
`content_text` 由 builder 合成并**经 jieba 预分词**（与查询端 `tokenize_for_fts()`
一致），FTS5 default tokenizer 再按空格切分。区别于 memory_system 的**手动 FTS**
（显式 INSERT/DELETE），二者都走 jieba 预分词，差异仅在 FTS 维护方式。

### 4.4 索引

```sql
CREATE UNIQUE INDEX idx_kg_nodes_natural ON kg_nodes(project_id, node_type, name, source);
CREATE INDEX idx_kg_nodes_project  ON kg_nodes(project_id);
CREATE INDEX idx_kg_nodes_type     ON kg_nodes(project_id, node_type);
CREATE INDEX idx_kg_nodes_source   ON kg_nodes(project_id, source);
CREATE INDEX idx_kg_edges_source   ON kg_edges(source_id);
CREATE INDEX idx_kg_edges_target   ON kg_edges(target_id);
CREATE INDEX idx_kg_edges_type     ON kg_edges(source_id, edge_type);
```

## 5. 构建策略

### 5.1 声明式构建（设计层）

**触发点**：`file_service.save_project()` 成功后，daemon 线程调用 `_rebuild_kg_async`。

**过程**（增量，`rebuild_project`）：

```
Project JSON → GraphBuilder.rebuild_project()
  ├── 确保 PROJECT 节点存在
  ├── 移除已删除的 diagram 节点（增量）
  ├── 逐图 rebuild_diagram()（只删该图旧实体再重建）
  │   ├── class diagram     → CLASS + METHOD + ATTRIBUTE + 关系边
  │   ├── sequence diagram  → LIFELINE + MESSAGES 边
  │   └── component diagram → COMPONENT + INTERFACE + 嵌套边
  └── 跨图关联: lifeline.class_ref → REFERENCES CLASS,
               diagram.component_id → REFERENCES COMPONENT
```

全量 `build_from_project()` 仍保留（删除 design 层重建），用于按需兜底重建
（`_ensure_project_indexed` 检测到文件与索引不一致时触发）。

**确定性**：从 `UmlDiagram` JSON 直接解析，无 LLM 参与。

### 5.2 探索式构建（代码层）

**触发点**：Agent 调用 `kg_diff` 时检测代码层是否已索引，未索引则自动
`build_from_source_dir()`。

```
Python 源文件 → AST 解析 (ast.parse)
  ├── ast.ClassDef → CLASS 节点 (source="code")
  │   ├── 类体 FunctionDef → METHOD + CONTAINS 边
  │   └── 类体 AnnAssign → ATTRIBUTE + CONTAINS 边
  ├── 文件级 FunctionDef → METHOD 节点
  ├── ast.Import / ImportFrom → IMPORTS 边 (SourceFile → SourceFile)
  └── 类名匹配设计层同名 CLASS → (SourceFile)--[IMPLEMENTS]-->(CLASS)
```

**测试覆盖推断**：扫描 `test_*.py`，从文件名和 import 推断 `(TestFile)--[TESTS]-->(SourceFile)`。

## 6. 检索接口

| 方法 | 说明 |
|------|------|
| `query` | BM25 全文检索（jieba 预分词 + FTS5 MATCH + bm25() 排序，支持类型/来源过滤） |
| `expand` | n-hop 邻域展开（BFS 批量查边，出边/入边/双向，depth 限制） |
| `trace` | 依赖路径追踪（SQLite recursive CTE，防环） |
| `diff` | 设计 vs 代码差异（missing_implementation / extra_code / mismatch / no_coverage） |

## 7. Agent 工具（5 个，内部）

5 个 `AsyncTool` 子类，遵循 `conversation_tools.py` 的 `run()→coroutine` 模式：

| 工具 | 功能 | 使用场景 |
|------|------|----------|
| `kg_query` | BM25 全文检索 | "项目里有没有 User 类？" |
| `kg_expand` | 展开节点关系 | "User 类有哪些方法和依赖？" |
| `kg_trace` | 依赖路径追踪 | "User 如何间接依赖 Logger？" |
| `kg_diff` | 设计 vs 代码对比 | "UML 设计都实现完了吗？" |
| `kg_project_structure` | 获取项目完整树状结构（图/类/方法/消息） | "总结项目结构" |

> **注意**：这 5 个工具**不直接暴露给主 Agent**。主 Agent 的只读探索统一收敛进
> `explore_project`（内部调用 `kg_project_structure` 等）。这是为了避免主 Agent
> 自己查图谱导致 token 膨胀。

## 8. 集成点

### 8.1 file_service.py — 声明式构建 Hook

```python
def save_project(project, filepath=None):
    # ... 保存 JSON 到磁盘 ...
    _rebuild_kg_async(project, filepath)   # daemon 线程增量重建 KG
    return filepath
```

### 8.2 explore_project_tools.py — 项目探索

`explore_project` 的 `summary` / `locate` 模式内部调用 `kg_project_structure` 获取完整
结构，并**与 `.umlproj` 文件交叉校验**（文件图签名 vs KG 图签名不一致时强制重建）。
兜底 `_ensure_project_indexed` 的图签名比对。

### 8.3 自动代码层索引

`kg_diff` 检测 `source='code'` 节点是否存在，不存在则自动在 `source_dir` 上
`build_from_source_dir()` —— 对 Agent 透明。

## 9. 设计决策

| 决策 | 理由 |
|------|------|
| **FTS5 content-sync** | 触发器自动同步，比手动 FTS 更简洁 |
| **自然键 upsert** | `(project_id, node_type, name, source)` 唯一，构建幂等 |
| **项目作用域 id** | `_make_id` 确定性 hash，跨项目文件复制不冲突 |
| **独立 DB** | KG 是项目结构（rebuild 清空），memory 是交互洞察（衰减淘汰），生命周期不同 |
| **Daemon 线程构建** | 不阻塞 HTTP 保存响应，WAL 模式支持并发读写 |
| **content_text 合成** | name + 关键属性 + 中文 note（桥接中文搜索），jieba 预分词 |
| **Recursive CTE** | 纯 SQL 路径查找，无需 Python BFS/DFS |
| **5 个工具分拆** | 每个操作语义不同，单工具 "operation" 枚举会混淆 LLM 的 FC |
| **IMPLEMENTS 边** | 设计-代码的 pivot，diff() 的唯一判断依据 |

## 10. 与 memory_system 的关系

| 维度 | knowledge_graph | memory_system |
|------|-----------------|---------------|
| 数据来源 | UML 设计 JSON + Python AST | LLM 对话交互 |
| 存储内容 | 项目结构（类/方法/关系） | 设计洞察/决策/偏好 |
| 生命周期 | rebuild 时清空重建 | 衰减淘汰（subject 后写覆盖） |
| 检索方式 | BM25 + 图遍历 | BM25（向量预留） |
| 分词 | jieba 预分词，content-sync FTS | jieba 预分词，手动 FTS |

两者互补：**图谱回答「系统长什么样」，记忆回答「为什么长这样」**。

## 11. 快速开始

```python
from knowledge_graph import GraphBuilder, GraphRetriever, KnowledgeGraphDB

# 1. 从项目构建知识图谱
builder = GraphBuilder(db_path="./data/knowledge_graph.db")
stats = builder.build_from_project(project, "my_project")

# 2. 从源码文件补充代码层
stats = builder.build_from_source_file("app.py", "my_project")

# 3. 检索
retriever = GraphRetriever(db_path="./data/knowledge_graph.db")
results = await retriever.query("my_project", "User login")
neighbors = await retriever.expand([results[0].node.id], depth=2)
diffs = await retriever.diff("my_project")

builder.close()
retriever.close()
```

## 12. 文件索引

| 文件 | 职责 |
|---|---|
| `backend/knowledge_graph/models.py` | `GraphNode` / `GraphEdge` / `NodeType` / `EdgeType` 及枚举 |
| `backend/knowledge_graph/database.py` | SQLite 图数据库 + FTS5 索引 + 幂等迁移 |
| `backend/knowledge_graph/builder.py` | `GraphBuilder`（设计层 + 代码层 + 跨图关联，项目作用域 id） |
| `backend/knowledge_graph/retriever.py` | `GraphRetriever`（query / expand / trace / diff） |
| `backend/app/agent_base/tools/my_tools/knowledge_graph_tools.py` | 5 个内部 AsyncTool（kg_*） |
| `backend/app/agent_base/tools/my_tools/explore_project_tools.py` | 项目探索统一入口（内部调 kg_project_structure + 交叉校验） |
| `backend/app/services/file_service.py` | `save_project` 触发 KG 增量重建 |
