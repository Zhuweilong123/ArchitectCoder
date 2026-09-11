# 记忆机制与实现说明

> 状态：当前实现说明
> 更新日期：2026-09-11
> 适用范围：当前仓库 HEAD。核心契约以 `backend/app/agent_base/core/memory.py` 为准，默认实现以 `extensions/memory/` 为准。

## 1. 当前边界

记忆能力分为两层：

- **核心层**：定义 `MemoryPort`、请求/结果模型、NoOp 降级和 resilience wrapper。核心不依赖 SQLite、FTS 或具体提取算法。
- **扩展层**：`extensions/memory` 提供 SQLite 存储、FTS5/BM25 检索、LLM 提取、写入治理和生命周期管理。

`backend/app/agent_base/core/plugins.py` 通过 `extensions.memory:create` 加载默认 provider。关闭或加载失败时，Agent 继续使用 `NoOpMemory`，不阻断主流程。

## 2. 运行时调用链

```text
create_dev_agent()
    │
    ├─ load_memory(llm, settings)
    │      └─ PluginManager.load("memory")
    │             └─ extensions.memory:create
    │                    └─ SQLiteMemoryProvider
    │
    ├─ 每轮构建上下文
    │      └─ DevPromptBuilder._recall_memory_block()
    │             ├─ MemoryPort.recall(MemoryRecallRequest)
    │             ├─ 将 context_block 加入本轮上下文
    │             └─ 有 memory_ids 时调用 reinforce()
    │
    └─ 任务结束后台归档
           └─ agent_execution._archive_task_to_memory()
                  └─ MemoryPort.archive(MemoryArchiveRequest)
                         └─ SQLiteMemoryProvider.archive()
                                └─ MemoryManager.remember()
```

Recall 位于主 Agent 的 prompt 组装阶段，受 `top_k` 和 token 预算限制。Archive
在任务完成后通过 `asyncio.create_task` 异步执行，不属于响应关键路径；归档失败只
记录日志。provider 每次 recall/archive/reinforce 操作创建一个短生命周期的
`MemoryManager`，操作结束关闭 SQLite 连接。

## 3. 核心端口（`backend/app/agent_base/core/memory.py`）

### 3.1 请求和结果模型

| 模型 | 字段 | 用途 |
|---|---|---|
| `MemoryRecallRequest` | `project_id`, `query`, `top_k=3`, `max_tokens=500` | 对话前检索项目记忆 |
| `MemoryRecallResult` | `context_block`, `memory_ids`, `token_count`, `metadata` | 返回可注入上下文和强化 ID |
| `MemoryArchiveRequest` | `project_id`, `user_message`, `final_answer`, `tool_steps`, `run_id`, `trace_id` | 任务完成后归档 |
| `MemoryArchiveResult` | `stored_count`, `metadata` | 返回新增记忆数量和 provider 信息 |

### 3.2 `MemoryPort`

```python
class MemoryPort(Protocol):
    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult: ...
    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult: ...
    async def reinforce(
        self, memory_ids: tuple[str, ...], project_id: str = ""
    ) -> None: ...
```

`NoOpMemory` 的 recall/archive 返回空结果，reinforce 和 close 不执行操作。
`_ResilientMemory` 捕获 provider 运行时异常：recall 返回空上下文并标记
`metadata={"degraded": True}`，archive 返回 `stored_count=0` 的降级结果，
reinforce/close 只记录日志。因此调用方不需要为可选记忆增加分支式容错。

## 4. 默认 provider（`extensions/memory/provider.py`）

`SQLiteMemoryProvider.create(*, llm, settings, **kwargs)` 实现核心端口：

- **数据库路径**：优先使用 `AGENT_MEMORY_DB_PATH`；为空时取
  `dirname(settings.uml_dir)/data/memories.db`，并规范化为绝对路径。
- **召回参数**：`AGENT_MEMORY_RECALL_TOP_K`（默认 3）和
  `AGENT_MEMORY_RECALL_MAX_TOKENS`（默认 500）。请求中的正值优先，请求值为空/零时使用配置值。
- **归档参数**：`AGENT_MEMORY_ARCHIVE_MAX_TOKENS`（默认 3000）限制后台提取调用。
- **recall**：调用 `MemoryManager.recall()`，再用 `inject_memories("", results)`
  生成 `context_block`，返回命中的 ID、provider 标识和数量。
- **archive**：最多格式化前 8 个工具步骤，每个 observation 截断到 300 字符，
  将工具过程与最终答案组合后交给 LLM 提取；提取调用包在 `trace_span("MemoryArchive")`
  中，并将 run/trace 来源写入记忆 metadata。
- **无 LLM 归档**：返回 `metadata={"provider": "sqlite", "skipped": "no_llm"}`，不写入数据。
- **reinforce**：没有 memory ID 或 project ID 时直接返回；否则调用 `MemoryManager.reinforce()`。

provider 的 `close()` 当前是兼容性空操作，因为每个操作自身管理 manager/数据库
生命周期。未来 provider 可实现异步 close，核心 wrapper 已支持 `aclose()` 或 `close()`。

## 5. 领域模型（`extensions/memory/models.py`）

### 5.1 记忆类型

`MemoryType` 当前包含五类：

- `preference`：用户偏好
- `decision`：设计或实现决策
- `rejection`：被拒绝的方案或约束
- `convention`：项目规范和约定
- `insight`：对项目状态的观察性总结

前四类属于 durable memory；`insight` 属于会随时间变化的状态观察。

### 5.2 `MemoryEntry`

持久化字段包括 `id`、`project_id`、`memory_type`、`summary`、`original_text`、
`subject`、`metadata`、`embedding`/`embedding_model`、`importance_score`、
`access_count`、`last_accessed_at`、`created_at`、`updated_at`、`tags`、`source`、
`user_feedback` 和 `is_pinned`。

运行时属性：

- `age_days` 基于 `created_at`。
- `age_hours` 基于 `updated_at`，没有更新时间时回退到 `created_at`，供 insight recency 使用。
- `days_since_access` 基于 `last_accessed_at`，从未访问时回退到记忆年龄。

`RecallResult` 将 `MemoryEntry` 与相关性分数、检索来源（当前为 `bm25`）组合。
`RetrieveMode` 保留 `bm25`、`vector`、`hybrid` 三种值，但后两者目前回退到 BM25。

## 6. 写入流程（`MemoryManager.remember`）

```text
任务上下文 + 用户输入 + LLM 输出
        │
        ▼
EXTRACT_PROMPT → extract_fn(LLM)
        │
        ▼
解析 JSON（原始数组 / fenced JSON / 文本包裹）
        │
        ▼
MemoryWritePolicy
        │
        ├─ insight + subject：同项目同类型同 subject 后写覆盖
        └─ durable：FTS 候选 + Jaccard 相似去重/合并
        │
        ▼
MemoryDatabase.add/update
```

提取器最多由 prompt 要求返回 0-3 条 JSON 记忆。每条候选可以包含
`memory_type`、`summary`、`subject`、`original_text`、`tags`、`aliases`、
`importance`、`confidence` 和 `metadata`。

`MemoryWritePolicy` 是确定性的持久化门禁：

- 默认最低置信度 `0.55`，摘要不能为空且最长 500 字符。
- 非法类型、非法置信度、临时/被拒绝状态直接丢弃。
- durable 类型在用户反馈为 `accepted` 或 `modified` 时，最低置信度可降到 `0.35`。
- 写入 metadata 包含 context、call type、提取时间、run/trace/message provenance、scope，
  以及 `governance={confidence,status,policy,confirmed}`。
- `aliases` 与 `tags` 合并去重后一起进入检索索引。

### 6.1 Insight 后写覆盖

`insight` 且存在 `subject` 时，subject 会 strip、压缩空白并转小写。相同
`(project_id, memory_type, subject)` 已存在时复用原 ID 和创建时间，更新摘要、
原文、标签、重要性和 metadata。这是 last-write-wins，不会继承旧 insight 的
重要性或访问次数，避免错误观察持续自我强化。

### 6.2 Durable 去重合并

其他类型通过 FTS5 找最多 3 个候选，再对摘要 token 计算 Jaccard 相似度。默认
阈值为 `0.55`：

- 达到阈值：更新已有摘要、原文、标签和反馈，重要性增加 `0.05`（上限 1.0），并记录一次访问。
- 未达到阈值：新增 `MemoryEntry`。

## 7. 检索与注入

`MemoryManager.recall(project_id, query, top_k=5, max_tokens=800, ...)` 当前实际
执行 BM25 路径：

1. 以 `max(top_k * 3, 10)` 扩大候选集；可按一个或多个 `MemoryType` 过滤。
2. `MemoryDatabase.search_bm25()` 使用 SQLite FTS5 和 BM25，内部将 BM25 负分转为“越高越相关”。
3. 仅对 `insight` 应用 `exp(-age_hours / recency_half_life_hours)` recency 衰减；durable 类型不因年龄降分。
4. `MemoryRecallPolicy.select()` 依次执行最低分、同 subject 冲突解决、类型上限、摘要去重和 token 预算筛选。
5. 结果按相关性和类型优先级返回，provider 再调用 `inject_memories()` 生成 prompt 文本。

召回策略的当前规则：

- `decision`、`rejection`、`convention`、`preference`、`insight` 依次具有 5 到 1 的类型优先级。
- 同 subject 优先已确认（`accepted`/`modified` 或 governance confirmed），再优先显式更新时间较新的条目。
- 默认每种类型最多 2 条，摘要相似度达到 `0.8` 时去重。
- 注入成本按摘要和标签字符数估算；超出 `max_tokens` 的条目跳过。
- 注入内容使用 `<project_memory>` 边界，只放 `summary`、类型、scope、tags 和相关性分数，
  并明确声明其仅为历史参考，当前用户指令优先。

向量和混合检索接口仍保留，但 `VECTOR`/`HYBRID` 当前记录 warning 后回退到 BM25，
不会执行 embedding 或 RRF 融合。

## 8. SQLite 存储（`extensions/memory/database.py`）

数据库首次连接时创建：

- `memories` 主表：记忆内容、治理 metadata、生命周期字段和来源字段。
- `memories_fts` FTS5 虚拟表：独立维护，与主表 rowid 对齐；写入 `summary + tags` 的预分词文本。
- `memory_maintenance`：每个 project 最近一次成功维护时间。

连接使用 WAL、`synchronous=NORMAL`、foreign keys 和 `check_same_thread=False`。
主表索引覆盖 project、类型、subject、重要性、访问时间和 pinned 状态。初始化
会幂等补齐旧库缺少的 `subject`、`updated_at` 列，并同步 FTS 数据。

检索分词由 `extensions/memory/tokenizer.py` 提供：优先使用 jieba 的搜索模式；
jieba 不可用时，中文使用 bigram + unigram 回退，英文按字母数字分词。FTS 查询
对 token 做安全引用并以 OR 连接。

数据库层还提供 `add`、`update`、`get`、`get_by_subject`、`list_by_project`、
`search_bm25`、`find_similar`、`reinforce`、`apply_decay`、`get_prune_candidates`、
`delete_by_rowids`、`stats` 和 `clear_project` 等内部 API。

## 9. 生命周期管理（`extensions/memory/lifecycle.py`）

| 操作 | 当前行为 |
|---|---|
| reinforce | 重要性增加 `reinforce_delta`（默认 0.1，上限 1.0），并可增加访问次数 |
| decay | 非 pinned 记忆重要性乘因子，最低不低于 `importance_min`；insight 使用更快因子 |
| prune | 仅在超过 `max_entries_per_project` 时执行，排除 pinned 和高访问记忆，按低重要性/久未访问优先批量删除 |
| maintenance | 顺序执行 decay 和 prune，返回 `{"decayed": N, "pruned": M}` |
| pin/unpin | 设置或清除 pinned，pinned 不参与衰减和淘汰 |

`remember()` 开始时执行机会式维护：读取持久化的 `memory_maintenance` 时间戳，
只有距离上次成功维护达到 `maintenance_interval_hours`（默认 24 小时）才执行。
显式 `maintenance(project_id)` 成功后也会更新该时间戳，因此重启进程不会丢失
维护节流状态。

## 10. 当前配置

### 10.1 应用层 Settings

| 环境变量 | 默认值 | 用途 |
|---|---:|---|
| `AGENT_MEMORY_ENABLED` | `true` | 通过插件管理器启用/禁用 memory provider |
| `AGENT_MEMORY_PROVIDER` | `extensions.memory:create` | provider 入口 |
| `AGENT_MEMORY_DB_PATH` | 空 | 覆盖 SQLite 路径 |
| `AGENT_MEMORY_RECALL_TOP_K` | `3` | Agent 端口默认召回条数 |
| `AGENT_MEMORY_RECALL_MAX_TOKENS` | `500` | Agent 端口默认召回预算 |
| `AGENT_MEMORY_ARCHIVE_MAX_TOKENS` | `3000` | 后台 LLM 提取预算 |

`AGENT_MEMORY_ENABLED=false` 或 provider 值为 `none`、`noop`、`disabled` 时，
核心返回 `NoOpMemory`。配置由 `get_settings()` 缓存，修改后需要重启 backend。

### 10.2 `MemoryConfig`（扩展内部）

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `max_entries_per_project` | 100 | 项目记忆上限 |
| `min_write_confidence` | 0.55 | 写入最低置信度 |
| `recall_min_score` | 0.0 | 召回最低分 |
| `recall_max_per_type` | 2 | 每类最多召回数 |
| `recall_duplicate_threshold` | 0.8 | 召回摘要去重阈值 |
| `dedup_threshold` | 0.55 | durable 写入 Jaccard 合并阈值 |
| `reinforce_delta` | 0.1 | 强化增量 |
| `decay_factor` / `insight_decay_factor` | 0.98 / 0.93 | durable / insight 衰减因子 |
| `maintenance_interval_hours` | 24 | 机会式维护间隔 |
| `importance_min` | 0.1 | 衰减下限 |
| `prune_batch_ratio` | 0.1 | 单次最大淘汰比例 |
| `pin_access_threshold` | 5 | 高访问记忆保护阈值 |
| `recency_half_life_hours` | 24.0 | insight 检索衰减半衰期 |
| `enable_bm25` / `enable_vector` / `enable_hybrid` | true / false / false | 当前仅 BM25 生效 |

`MemoryConfig` 还保留 `decay_interval_hours`、BM25 参数和向量/RRF 参数，作为
扩展内部兼容和后续演进配置；当前实际维护节流使用 `maintenance_interval_hours`，
向量/混合检索尚未启用。

## 11. 直接使用扩展 API

应用主流程不应绕过 `MemoryPort` 直接实例化 `MemoryManager`。只有扩展内部工具、
迁移脚本和离线维护任务可以直接使用领域 API：

```python
from extensions.memory import MemoryManager

manager = MemoryManager(db_path="./data/memories.db")
try:
    results = await manager.recall("project-id", "查询内容", top_k=5, max_tokens=800)
    prompt = manager.inject_memories("系统提示", results)
    manager.reinforce(results, project_id="project-id")
finally:
    manager.close()
```

主要领域方法：`remember`、`recall`、`inject_memories`、`reinforce`、`forget`、
`list_memories`、`stats`、`maintenance`、`pin`、`unpin`、`clear_project` 和 `close`。

## 12. 文件职责与维护规则

| 文件 | 职责 |
|---|---|
| `backend/app/agent_base/core/memory.py` | 核心端口、请求/结果模型、NoOp 和 resilience |
| `backend/app/agent_base/assembly.py` | 组装 provider、每轮 recall 和强化 |
| `backend/app/services/agent_execution.py` | 任务结束后的异步 archive 调度 |
| `extensions/memory/provider.py` | SQLite provider 到核心端口的适配 |
| `extensions/memory/manager.py` | 提取、写入、召回、注入和领域编排 |
| `extensions/memory/policy.py` | 写入门禁和召回筛选 |
| `extensions/memory/database.py` | SQLite、FTS5、迁移、索引和 CRUD |
| `extensions/memory/lifecycle.py` | reinforce、decay、prune、maintenance、pin |
| `extensions/memory/models.py` | `MemoryEntry`、类型、结果和 `MemoryConfig` |
| `extensions/memory/tokenizer.py` | jieba/bigram 分词和 FTS token 构造 |
| `extensions/memory/embedding.py` | embedding 协议和向量工具（当前未接入检索） |
| `extensions/memory/migrate.py` | 旧 JSON 记忆迁移到 SQLite 的一次性脚本 |

新增 provider 必须实现 `MemoryPort` 的三个方法，并通过插件管理器注册；新增
存储或检索策略应留在 `extensions/memory`，不能把 SQLite、FTS、LLM 提取细节
泄漏到 Agent 核心。更新行为时同时核对本文件、插件架构文档和当前架构总览。

## 13. 当前限制

- 默认检索仍是 SQLite FTS5/BM25；向量和混合检索只有协议/字段预留。
- 记忆没有独立的 HTTP 管理 API 或前端管理页，管理方法是扩展内部 API。
- provider 每次操作创建短生命周期 manager，暂未提供跨请求连接池或后台维护 worker。
- 写入依赖归档阶段的 LLM 提取；没有 `llm` 时不会生成记忆。
- `MemoryManager` 的机会式维护由后续 remember 触发，不是独立定时任务。
