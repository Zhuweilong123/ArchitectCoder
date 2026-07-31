# Knowledge Graph — 项目知识图谱系统

为 AI 助手提供结构化的项目理解能力，让模型按需查询项目结构而非被动接收全部内容。

## 核心理念

**从"被动注入"到"主动探索"** — AI 助手在推理过程中遇到不确定的依赖关系时，主动调用工具查询图谱，而非预先塞满上下文窗口。

## 架构

```
knowledge_graph/
├── __init__.py                     # 统一导出入口
├── models.py                       # 数据模型 (GraphNode, GraphEdge, GraphConfig, 枚举)
├── database.py                     # SQLite 图数据库 + FTS5 全文索引
├── builder.py                      # GraphBuilder (设计层 + 代码层构建)
└── retriever.py                    # GraphRetriever (query / expand / trace / diff)

backend/app/agent_base/tools/my_tools/
└── knowledge_graph_tools.py        # 4 个 AsyncTool (Agent 可调用)
```

## 数据模型

### 节点类型 (NodeType)

| 类型 | 含义 | 来源 |
|------|------|------|
| `project` | 顶层项目 | design |
| `diagram` | 单张 UML 图 (class/sequence/component) | design |
| `class` | 类 / 接口 / 抽象类 / 枚举 | design / code |
| `component` | 组件图节点 | design |
| `lifeline` | 时序图生命线 | design |
| `source_file` | 源码文件 (.py 等) | code |
| `test_file` | 测试文件 (test_*.py) | test |
| `method` | 方法 / 函数 | design / code |
| `attribute` | 属性 / 字段 | design / code |
| `interface` | 提供/需要的接口 | design |

### 边类型 (EdgeType)

```
结构关系: contains
UML 语义: inherits | composition | aggregation | association | realization | dependency
设计-代码: implements | imports | tests | references
时序消息: messages
```

### 三层 knowledge

```
项目层: PROJECT → DIAGRAM  (项目有哪些图)
实体层: DIAGRAM → CLASS → METHOD / ATTRIBUTE  (图里有什么实体)
关系层: CLASS → [INHERITS / COMPOSITION / ...] → CLASS  (实体间怎么关联)
       SOURCE_FILE → [IMPLEMENTS] → CLASS  (代码实现了哪个设计)
       TEST_FILE → [TESTS] → SOURCE_FILE  (测试覆盖了哪个文件)
```

## 数据库 Schema

### kg_nodes（节点表）

```sql
CREATE TABLE kg_nodes (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT UNIQUE NOT NULL,
    node_type    TEXT NOT NULL,       -- NodeType 枚举值
    name         TEXT NOT NULL,       -- 人可读名称
    project_id   TEXT NOT NULL,       -- 项目隔离
    source       TEXT DEFAULT 'design', -- design | code | test
    properties   TEXT DEFAULT '{}',   -- 结构化属性 JSON
    content_text TEXT DEFAULT '',     -- FTS5 索引用合成文本
    embedding    BLOB,                -- 向量预留
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

**自然键**：`(project_id, node_type, name, source)` — 幂等构建，同名的 UML 类保存两次产生同一节点。

### kg_edges（边表）

```sql
CREATE TABLE kg_edges (
    rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT UNIQUE NOT NULL,
    source_id  TEXT NOT NULL,         -- GraphNode.id
    target_id  TEXT NOT NULL,         -- GraphNode.id
    edge_type  TEXT NOT NULL,         -- EdgeType 枚举值
    properties TEXT DEFAULT '{}',     -- 结构化属性 JSON
    weight     REAL DEFAULT 1.0,      -- 边权重
    created_at TEXT NOT NULL
);
```

**去重键**：`(source_id, target_id, edge_type, properties)` — 相同语义的边不重复创建。

### kg_node_fts（FTS5 全文索引）

```sql
CREATE VIRTUAL TABLE kg_node_fts USING fts5(
    content_text,
    content='kg_nodes',          -- content-sync 模式, 触发器自动同步
    content_rowid='rowid'
);
```

**content-sync 模式**：INSERT/UPDATE/DELETE 触发器自动维护 FTS 索引，无需手动操作。区别于 memory_system 的手动 FTS（memory 需要 jieba 预分词，KG 的 content_text 由 builder 合成）。

### 索引

```sql
-- 节点自然键唯一索引 (幂等 upsert)
CREATE UNIQUE INDEX idx_kg_nodes_natural ON kg_nodes(project_id, node_type, name, source);

-- 常用查询加速
CREATE INDEX idx_kg_nodes_project  ON kg_nodes(project_id);
CREATE INDEX idx_kg_nodes_type     ON kg_nodes(project_id, node_type);
CREATE INDEX idx_kg_nodes_source   ON kg_nodes(project_id, source);

-- 边查询加速
CREATE INDEX idx_kg_edges_source   ON kg_edges(source_id);
CREATE INDEX idx_kg_edges_target   ON kg_edges(target_id);
CREATE INDEX idx_kg_edges_type     ON kg_edges(source_id, edge_type);
```

## 构建策略

### 声明式构建（设计层）

**触发点**：`file_service.save_project()` / `save_diagram()` 成功后，daemon 线程自动调用。

**过程**：
```
Project JSON → GraphBuilder.build_from_project()
  ├── 清除旧 design 节点 → DELETE kg_nodes WHERE source='design'
  ├── PROJECT 节点 (1)
  ├── 遍历 diagrams[]:
  │   ├── class diagram     → CLASS + METHOD + ATTRIBUTE + 关系边
  │   ├── sequence diagram  → LIFELINE + MESSAGES 边
  │   └── component diagram → COMPONENT + INTERFACE + 嵌套边
  └── 跨图关联: lifeline.class_ref → REFERENCES CLASS,
               diagram.component_id → REFERENCES COMPONENT
```

**确定性**：从 `UmlDiagram` JSON 直接解析，无 LLM 参与。`UmlDiagram.classes[]` → CLASS 节点，`UmlDiagram.relations[]` → 语义边（inheritance→INHERITS 等）。

### 探索式构建（代码层）

**触发点**：Agent 调用 `kg_diff` 时自动检测代码层是否已索引，未索引则自动调用 `build_from_source_dir()`。

**过程**：
```
Python 源文件 → AST 解析 (ast.parse)
  ├── ast.ClassDef → CLASS 节点 (source="code")
  │   ├── 类体中的 FunctionDef → METHOD 节点 + CONTAINS 边
  │   └── 类体中的 AnnAssign → ATTRIBUTE 节点 + CONTAINS 边
  ├── 文件级 FunctionDef → METHOD 节点
  ├── ast.Import / ImportFrom → IMPORTS 边 (SourceFile → SourceFile)
  └── 类名匹配: 查设计层 KG 中同名的 CLASS 节点
      → 找到则创建 (SourceFile)--[IMPLEMENTS]-->(CLASS) 边
```

**测试覆盖推断**：扫描 `test_*.py`，从文件名和 import 推断 `(TestFile)--[TESTS]-->(SourceFile)`。

## 检索接口

### query — BM25 全文检索

```
query(project_id, pattern, node_types?=None, source?=None, top_k=20) → list[NodeResult]
```

- 用 `memory_system.tokenizer.tokenize_for_fts()` 对查询分词
- FTS5 MATCH + bm25() 排序
- 支持按节点类型 + 来源过滤
- 多类型查询：分别检索后合并按得分排序

### expand — n-hop 邻域展开

```
expand(node_ids, depth=1, edge_types?=None, direction="outgoing", max_nodes=50) → list[NodeResult]
```

- BFS 迭代：每层批量 SQL 查边，去重
- 支持出边/入边/双向三种模式
- `depth` 限制防止图爆炸

### trace — 依赖路径追踪

```
trace(source_id, target_id, max_depth=10, edge_types?=None) → list[PathResult]
```

- SQLite recursive CTE 纯 SQL 实现
- 防环：`path_nodes NOT LIKE '%|target_id|%'`
- 路径按长度升序返回

### diff — 设计 vs 代码差异

```
diff(project_id, source_dir?=None) → DiffResult
```

检测 3 类差异：

| 类别 | 严重度 | 检测规则 |
|------|--------|----------|
| `missing_implementation` | error | 设计 CLASS 节点无 incoming IMPLEMENTS 边 |
| `extra_code` | warning | 代码 CLASS 节点无 matching 设计 CLASS |
| `mismatch` | warning | IMPLEMENTS 对存在但方法签名不一致 |
| `no_coverage` | info | SourceFile 无对应的 TestFile |

## Agent 工具

4 个 `AsyncTool` 子类，遵循 `conversation_tools.py` 的 `run()→coroutine` 模式：

| 工具 | 功能 | 使用场景 |
|------|------|----------|
| `kg_query` | BM25 全文检索 | "项目里有没有 User 类？" |
| `kg_expand` | 展开节点关系 | "User 类有哪些方法和依赖？" |
| `kg_trace` | 依赖路径追踪 | "User 如何间接依赖 Logger？" |
| `kg_diff` | 设计 vs 代码对比 | "UML 设计都实现完了吗？" |

## 集成点

### file_service.py — 声明式构建 Hook

```python
def save_project(project, filepath=None):
    # ... 保存 JSON 到磁盘 ...
    _rebuild_kg_async(project, filepath)  # daemon 线程重建 KG
    return filepath
```

### agent_chat_ws.py — Agent 工具注册

```python
def _create_dev_agent(llm, source_dir="", test_dir="", project_file=""):
    tools, review_mgr = create_conversation_tools(llm, ...)  # 8 个核心工具
    kg_tools = create_kg_tools(db_path=...)
    tools.extend(kg_tools)                                    # +4 个 KG 工具
    # system prompt 包含 KG 使用指导
```

### 自动代码层索引

Agent 调用 `kg_diff` 时，`GraphRetriever.diff()` 检测 `source='code'` 节点是否存在，不存在则自动在 `source_dir` 上运行 `build_from_source_dir()` — 对 Agent 透明。

## 设计决策

| 决策 | 理由 |
|------|------|
| **FTS5 content-sync** | 触发器自动同步，比手动 FTS 更简洁干净 |
| **Natural key upsert** | `(project_id, node_type, name, source)` 唯一，构建幂等 |
| **独立 DB** | KG 是项目结构数据 (rebuild 时清空)，memory 是交互洞察 (衰减淘汰)，生命周期不同 |
| **Daemon thread 构建** | 不阻塞 HTTP 保存响应，WAL 模式支持并发读写 |
| **Content text 合成** | name + 关键属性拼接，FTS5 default tokenizer 处理中英文混合 |
| **Recursive CTE** | 纯 SQL 路径查找，无需 Python BFS/DFS |
| **4 个工具分拆** | 每个操作语义不同，单工具 "operation" 枚举会混淆 LLM 的 FC |
| **IMPLEMENTS 边** | 设计-代码的 pivot，diff() 的唯一判断依据 |

## 与 memory_system 的关系

| 维度 | knowledge_graph | memory_system |
|------|-----------------|---------------|
| 数据来源 | UML 设计 JSON + Python AST | LLM 对话交互 |
| 存储内容 | 项目结构 (类/方法/关系) | 设计洞察/决策/偏好 |
| 生命周期 | rebuild 时清空重建 | 衰减淘汰 |
| 检索方式 | BM25 + 图遍历 | BM25 (向量预留) |
| 分词 | content_text 合成, FTS5 default | jieba 预分词, 手动 FTS |

两者互补：**图谱回答"系统长什么样"**，**记忆回答"为什么长这样"**。

## 快速开始

```python
from knowledge_graph import GraphBuilder, GraphRetriever, KnowledgeGraphDB

# 1. 从项目构建知识图谱
builder = GraphBuilder(db_path="./data/knowledge_graph.db")
stats = builder.build_from_project(project, "my_project")
# → BuildStats(nodes: +15, edges: +22, source='declarative')

# 2. 从源码文件补充代码层
stats = builder.build_from_source_file("app.py", "my_project")
# → 自动创建 IMPLEMENTS 边连接代码和设计

# 3. 检索
retriever = GraphRetriever(db_path="./data/knowledge_graph.db")
results = await retriever.query("my_project", "User login")
neighbors = await retriever.expand([results[0].node.id], depth=2)
diffs = await retriever.diff("my_project")

builder.close()
retriever.close()
```
