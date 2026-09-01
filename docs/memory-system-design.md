# 记忆系统设计

> 本文完整归档 ArchitectCoder 的 Agent 记忆系统（`backend/memory_system/`）设计，
> 反映最新实现（含 subject 后写覆盖、recency 检索、类型化衰退）。
> 作为后续接入向量/混合检索、调参、生命周期策略迭代的参考基线。

## 1. 定位与目标

Agent 在对话式开发中需要「跨会话记住」两类信息：用户偏好/设计决策（耐久），以及对
项目当前状态的观察（会过时）。记忆系统提供：

- **写入**：从 LLM 交互中自动提取并去重存储记忆（`remember`）。
- **检索**：对话前按查询检索相关记忆（`recall`），注入 system prompt（`inject_memories`）。
- **生命周期**：强化、衰减、淘汰（`LifecycleManager`）。

技术栈：**SQLite + FTS5 全文索引 + jieba 分词**，向量检索接口预留。

## 2. 架构总览

```
调用方（agent_chat_ws / explore_project_tools）
   │  写入：remember(llm_call_type, user_input, llm_output, extract_fn)
   │  检索：recall(query) → inject_memories(system_prompt, results)
   ▼
MemoryManager ── 顶层 API / 编排
   ├─ EXTRACT_PROMPT ── 让 LLM 提取记忆 JSON
   ├─ _parse_extract_result ── 解析 LLM 返回
   ├─ MemoryDatabase ── SQLite + FTS5 存储
   ├─ LifecycleManager ── 强化/衰减/淘汰
   └─ EmbeddingService ── 嵌入协议（预留）
```

数据流：

- **写入**：`remember` → LLM 提取 → 按类型分叉（insight 后写覆盖 / 耐久类相似合并）→
  落库（同步 FTS5）。
- **检索**：`recall` → FTS5 BM25 → recency 重排 → 截断 top_k → 注入。
- **维护**：机会式触发 `decay + prune`。

## 3. 数据模型（`backend/memory_system/models.py`）

### 3.1 MemoryEntry

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_id` | str | 项目标识（`umlproj` basename 去后缀） |
| `memory_type` | `MemoryType` | 记忆分类（强类型枚举） |
| `summary` | str | 摘要（检索 + 注入用） |
| `original_text` | str | 原始上下文详情（回溯用） |
| `subject` | str | 主题键（insight 后写覆盖主键，LLM 输出） |
| `id` | str | UUID（覆盖时保留原 id） |
| `metadata` | dict | 元数据（context / call_type / extracted_at） |
| `embedding` | BLOB | 向量（预留） |
| `importance_score` | float | 重要度 0~1（强化/衰减作用域） |
| `access_count` | int | 被检索使用次数 |
| `last_accessed_at` | str | 最后访问时间 |
| `created_at` / `updated_at` | str | 创建 / 最近写入时间（`age_hours` 基准） |
| `tags` | list[str] | 关键词标签 |
| `source` | str | 来源（llm_call_type） |
| `user_feedback` | str | 用户反馈（accepted/rejected/modified） |
| `is_pinned` | bool | 固定（不参与淘汰） |

计算属性：`age_days`（基于 created_at）、`days_since_access`、`age_hours`（基于
`updated_at or created_at`，供 recency 衰减）。

### 3.2 MemoryType

`PREFERENCE`（用户偏好）、`DECISION`（设计决策）、`REJECTION`（被拒绝的建议）、
`CONVENTION`（规范）、`INSIGHT`（LLM 总结的通用洞察）。

### 3.3 MemoryConfig

见 §9 配置项。

## 4. 存储层（`backend/memory_system/database.py`）

- **SQLite**：WAL + `synchronous=NORMAL` + `check_same_thread=False`，`memories` 主表。
- **FTS5**：独立虚拟表 `memories_fts(summary)`，非 content-synced——写入时把
  `summary + tags`（tags 含检索别名）拼成 `_search_text`，用 `tokenize_for_fts`
  预分词后以空格连接插入，查询时同样预分词构造 `MATCH` 表达式。别名/标签因此参与召回。
- **索引**：`(project_id)`、`(project_id, memory_type)`、
  `(project_id, memory_type, subject)`、importance / last_accessed / is_pinned。
- **分词**（`tokenizer.py`）：jieba `cut_for_search` 优先，不可用时回退字符级 bigram +
  英文空格分词。

### 4.1 幂等迁移

`_migrate_columns` 在 `_init_schema`（首次建连）时用 `PRAGMA table_info` 判断缺列并
`ALTER TABLE ADD COLUMN`（`subject` / `updated_at`），旧库自动补齐、幂等可重入。

### 4.2 关键方法

`add` / `update` / `delete` / `get` / `get_by_subject` / `list_by_project` /
`search_bm25` / `find_similar` / `reinforce` / `apply_decay` /
`get_prune_candidates` / `delete_by_rowids` / `clear_project` / `stats`。

## 5. 记忆类型与生命周期策略（本次核心优化）

记忆按类型分成两类生命周期，避免把「会变的状态」和「不变的承诺」混为一谈：

| 类型 | 生命周期模型 | 理由 |
|---|---|---|
| `preference / decision / rejection / convention` | **累积 + 相似合并 + 强化 + 慢衰减** | 偏好/决策是承诺，不该被覆盖 |
| `insight` | **同 subject 后写覆盖 + recency 检索 + 快衰减** | insight 是观察，会过时、会被纠正 |

这一设计的直接动因：一次「项目没有类图」的错误结论被归档成多条高重要度 insight，
持续注入并自我强化、无法纠正。详见 §8.1。

## 6. 写入路径（remember）

### 6.1 提取

`EXTRACT_PROMPT` 让 LLM 输出 JSON 数组，每条含 `memory_type / summary / subject /
original_text / tags / aliases / importance`。其中 `subject` 仅 `insight` 必填，格式
「实体:方面」且稳定可复现（例：`uml:class_diagram:existence`、`class:ModeController:methods`）。
`aliases` 是中英对照 / 近义说法 / 常见简称，写入时并入 `tags`（见 §6.2），用于拓宽 BM25
召回（让英文/别名查询也能命中中文摘要）。

`_parse_extract_result` 兼容纯 JSON 数组 / ```json 代码块 / 额外文字包裹三种形态。

### 6.2 分叉写库

```
if memory_type == INSIGHT and subject:
    existing = db.get_by_subject(project_id, INSIGHT, subject)
    if existing:
        entry.id = existing.id; entry.created_at = existing.created_at
        db.update(entry)                    # 后写覆盖，summary/importance/tags 全量刷新
    else:
        db.add(entry)
else:
    # 耐久类：find_similar + Jaccard 相似合并
    if best_sim >= dedup_threshold:
        merge(best_match, entry)            # importance +0.05，更新 summary/original_text
    else:
        db.add(entry)
```

- **覆盖语义**：importance 取新值、access_count 归零，不继承旧值——旧错误结论的高
  重要度不会污染新正确结论。
- **`_normalize_subject`**：存储前 strip + lowercase + 折叠空白，减少 LLM 主题漂移。
- **别名并入 tags**：`aliases` 与 `tags` 去重合并后存进 `tags`（去重保持顺序），
  并随 `_search_text` 一起进 FTS 索引，拓宽召回。
- **耐久类合并**：同时更新 `summary` 与 `original_text`（原实现只改 original_text，
  导致注入端永远吐旧摘要）。

## 7. 检索路径（recall / inject）

```
search_bm25(project_id, query, top_k = top_k * 3)   # 多取候选
_apply_recency(results)                              # insight 按 age 指数衰减
sort(score desc); truncate(top_k)
inject_memories(system_prompt, results)              # 注入 summary + 相关性分
```

- **BM25**：FTS5 `bm25()` 取负转正（高分=高相关）。
- **`_apply_recency`**：仅对 `insight` 施加 `score *= exp(-age_hours / recency_half_life_hours)`，
  耐久类不受影响；多取候选避免新鲜低分记忆被 LIMIT 截掉。
- **注入**：`inject_memories` 拼「## 项目历史记忆」章节，含类型标签、tags、相关性分。

## 8. 生命周期（LifecycleManager）

| 操作 | 语义 | 触发 |
|---|---|---|
| `reinforce` | `importance += delta`（默认 0.1），`access_count++` | 显式调用（当前检索路径未接线） |
| `decay` | 类型化乘性衰减，`MAX(importance_min, …)` | `maintenance` 内 |
| `prune` | 超 `max_entries` 时淘汰低重要度 + 低访问 + 非 pinned，分批 | `maintenance` 内 |

`decay` 类型化因子：

```
insight    → importance * insight_decay_factor   # 0.93，快
durable    → importance * decay_factor           # 0.98，慢
```

**机会式触发**：`remember` 开头调 `_maybe_maintenance`，用模块级
`_LAST_MAINTENANCE[project_id]` 时间戳节流，距上次超过 `maintenance_interval_hours`
才跑一次完整 `decay + prune`。因调用方每次新建 `MemoryManager`，节流状态放模块级
（进程重启重置无害）。

## 9. 检索模式（BM25 / 向量 / 混合）

| 模式 | 状态 |
|---|---|
| BM25（FTS5 + jieba） | ✅ 可用 |
| Vector（EmbeddingService 协议） | ⬜ 接口预留 |
| Hybrid（RRF 融合） | ⬜ 接口预留 |

`embedding.py` 定义 `EmbeddingService` 协议（`encode` + `dimension`）及
`cosine_similarity` / `normalize_vector` 工具，`MemoryManager(embedding_service=…)`
预留注入点，`MemoryEntry.embedding` 字段与 `embedding_model` 已预留。

## 10. 集成点

| 调用方 | 写入 | 检索 |
|---|---|---|
| `backend/app/services/agent_chat_ws.py` | `_archive_task_to_memory`（done 后异步归档） | `_build_memory_system_prompt`（每轮对话前 recall + inject） |
| `backend/app/agent_base/tools/my_tools/explore_project_tools.py` | `_archive_result`（explore 后归档） | `_recall_inject`（当前计算但未使用，死代码） |

均通过 `MemoryManager(db_path=…)` 每次新建实例使用。

## 11. 关键设计点（踩坑）

### 11.1 状态类记忆的自我强化（本系统的核心教训）

旧机制把「没有类图」这类状态观察当持久事实累积，且 `remember` 去重是「相似即合并」、
合并时只更新 `original_text` 不更新 `summary`、还 `importance +0.05`，导致错误摘要被
越刷越重；`recall` 纯 BM25 不认时间；`decay/prune` 是死代码且只护高重要度。

修复的组合拳：**insight 同 subject 后写覆盖（新顶旧）+ recency 检索（新鲜优先）+
类型化衰退（insight 快衰减）**，使错误结论能被自动纠正、旧结论自然淡出，无需人工清理。

### 11.2 subject 的稳定性与粒度

最易翻车处：太粗会把不同事实互相覆盖丢信息；太细则退化成不覆盖。控制手段：Prompt 强制
「实体:方面」+ 示例；存储侧 `_normalize_subject`；即便漂移未撞上，recency 仍能让新鲜
记忆排前（退化为「软纠正」而非「硬覆盖」）。

### 11.3 覆盖时 reset importance

insight 覆盖若继承旧重要度，等于把「错误的高重要度」转嫁给正确结论，因此覆盖语义是
「新观察从自己的值重新开始」。

### 11.4 迁移与存量自愈

存量记忆迁移后 `subject=''`、`updated_at=''`，`age_hours` 回退用 `created_at` 计算年龄，
旧记忆因年龄被 recency 立即压到新鲜记忆之下，再随快衰减逐步降到 prune 线以下被淘汰。

## 12. 配置项（MemoryConfig）

| 参数 | 默认 | 说明 |
|---|---|---|
| `db_path` | `./data/memories.db` | 数据库路径 |
| `max_entries_per_project` | 100 | 触发 prune 的条目阈值 |
| `dedup_threshold` | 0.55 | 耐久类相似合并的 Jaccard 阈值 |
| `recency_half_life_hours` | 24.0 | insight recency 半衰期（小时） |
| `insight_decay_factor` | 0.93 | insight 每次 maintenance 衰减乘数 |
| `decay_factor` | 0.98 | 耐久类衰减乘数 |
| `maintenance_interval_hours` | 24 | 机会式 maintenance 节流间隔 |
| `reinforce_delta` | 0.1 | 强化增量 |
| `importance_min` | 0.1 | 衰减下限（可被 prune） |
| `prune_batch_ratio` | 0.1 | 每次最多淘汰比例 |
| `pin_access_threshold` | 5 | access_count 达到则自动 pin |

## 13. 快速开始

```python
from memory_system import MemoryManager, MemoryConfig

manager = MemoryManager(db_path="./memories.db")

# 1. LLM 调用后：提取并存储记忆
await manager.remember(
    project_id="blog_system",
    context="用户请求优化类图",
    llm_call_type="optimize",
    user_input="请优化 Blog 系统的类图设计",
    llm_output="...",
    extract_fn=my_llm_chat,   # 你的 LLM 调用函数
)

# 2. LLM 调用前：检索相关记忆
results = await manager.recall(project_id="blog_system", query="如何优化时序图认证流程")

# 3. 注入 system prompt
enriched = manager.inject_memories(system_prompt="你是 UML 设计专家...", recall_results=results)

# 4. 强化被使用的记忆
manager.reinforce(results)

# 5. 定期维护（衰减 + 淘汰）
manager.maintenance("blog_system")
```

## 14. API 参考（MemoryManager）

| 方法 | 说明 |
|---|---|
| `remember(project_id, ...)` | LLM 调用后提取并存储记忆（insight 后写覆盖 / 耐久类合并） |
| `recall(project_id, query, top_k, mode)` | 检索相关记忆（BM25 + recency 重排） |
| `inject_memories(prompt, results)` | 将记忆注入 system prompt |
| `reinforce(results_or_id)` | 强化记忆（被使用后调用） |
| `forget(project_id, memory_id)` | 删除指定记忆 |
| `list_memories(project_id, type)` | 列出项目记忆 |
| `stats(project_id)` | 获取统计信息 |
| `maintenance(project_id)` | 执行衰减 + 淘汰 |
| `pin / unpin(memory_id, project_id)` | 固定 / 取消固定（保护） |
| `clear_project(project_id)` | 清除项目所有记忆 |
| `close()` | 关闭数据库连接 |

## 15. 设计决策

1. **为什么 SQLite + FTS5？** 零运维全文检索，FTS5 内置 BM25 评分，WAL 支持并发读写。
2. **为什么 jieba？** 中文语义分词，「组合模式」被切为一个 term，而非字符级 bigram。
3. **为什么 summary + original_text 分离？** summary 供检索/注入（简洁），original_text 保留完整上下文（回溯）。
4. **为什么 insight 后写覆盖？** 状态类观察会过时/被纠正，同 subject 新顶旧，避免矛盾累积。
5. **为什么 Embedding 只预留接口？** 当前 BM25 + jieba 已覆盖核心需求，向量检索引入外部依赖，接口化后可无缝接入。

## 16. 文件索引

| 文件 | 职责 |
|---|---|
| `backend/memory_system/manager.py` | `MemoryManager`：remember/recall/inject/forget/reinforce/maintenance + 提取 prompt + subject 分叉 |
| `backend/memory_system/database.py` | `MemoryDatabase`：SQLite + FTS5 存储、幂等迁移、BM25、`get_by_subject`、类型化 decay |
| `backend/memory_system/models.py` | `MemoryEntry` / `MemoryType` / `MemoryConfig` / `RecallResult` / `RetrieveMode` |
| `backend/memory_system/lifecycle.py` | `LifecycleManager`：reinforce / decay / prune / maintenance / pin |
| `backend/memory_system/tokenizer.py` | jieba / bigram 分词（`tokenize` / `tokenize_for_fts`） |
| `backend/memory_system/embedding.py` | `EmbeddingService` 协议 + 向量工具（预留） |
| `backend/memory_system/migrate.py` | 旧 JSON → SQLite 迁移脚本（一次性） |
| `backend/app/services/agent_chat_ws.py` | 归档 + 注入集成 |
| `backend/app/agent_base/tools/my_tools/explore_project_tools.py` | explore 归档集成 |
