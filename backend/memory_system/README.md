# Memory System — Agent 跨会话记忆系统

基于 **SQLite + FTS5 + jieba 分词** 的记忆系统，为 UML Designer 提供 LLM 跨会话上下文感知能力。

## 核心特性

- **SQLite + FTS5** — 全文检索持久化，无需额外数据库服务
- **jieba 分词** — 中文语义分词，BM25 召回精度显著优于字符级 bigram
- **BM25 检索** — FTS5 内置 BM25 算法，轻量高效
- **混合检索预留** — Embedding 向量接口已定义，后续接入即可开启语义检索 + RRF 融合
- **自动记忆提取** — LLM 交互后自动提取 summary + original_text 双字段记忆
- **生命周期管理** — 强化（reinforce）/ 衰减（decay）/ 淘汰（prune）三阶段机制
- **LFU 淘汰** — 低重要性 + 长期未访问优先淘汰，pinned + 热记忆保护
- **配置化参数** — MemoryConfig 统一管理所有可调参数
- **WAL 模式** — 支持并发读写，原子操作
- **JSON 迁移** — 内置 `migrate.py` 从旧版 JSON 文件迁移

## 目录结构

```
memory_system/
├── __init__.py      # 公开 API
├── models.py        # 数据模型 (MemoryEntry, MemoryType, MemoryConfig)
├── database.py      # SQLite + FTS5 存储层
├── tokenizer.py     # jieba 分词 (bigram 兜底)
├── embedding.py     # 嵌入服务接口 (预留)
├── lifecycle.py     # 强化 / 衰减 / 淘汰
├── manager.py       # MemoryManager 顶层接口
├── demo.py          # 独立演示脚本
├── migrate.py       # JSON → SQLite 迁移
└── README.md
```

## 快速开始

### 安装依赖

```bash
pip install jieba
```

> jieba 未安装时自动回退到 bigram 分词，功能不受影响但检索精度下降。

### 运行演示

```bash
cd memory_system
python demo.py
```

演示模拟 3 轮 LLM 交互 → 记忆提取 → BM25 检索 → 注入 → 强化 → 维护的完整流程。

### 基础用法

```python
from memory_system import MemoryManager, MemoryConfig

# 初始化
manager = MemoryManager(db_path="./memories.db")

# ── 1. LLM 调用后: 提取并存储记忆 ──
entries = await manager.remember(
    project_id="blog_system",
    context="用户请求优化类图，提高可扩展性",
    llm_call_type="optimize",
    user_input="请优化 Blog 系统的类图设计",
    llm_output="...",       # LLM 返回内容
    user_feedback="accepted",
    extract_fn=my_llm_chat, # 你的 LLM 调用函数
)

# ── 2. LLM 调用前: 检索相关记忆 ──
results = await manager.recall(
    project_id="blog_system",
    query="如何优化时序图中的认证流程",
    top_k=5,
    max_tokens=800,
)

# ── 3. 注入到 system prompt ──
enriched_prompt = manager.inject_memories(
    system_prompt="你是 UML 设计专家...",
    recall_results=results,
)
response = await chat(system_prompt=enriched_prompt, ...)

# ── 4. 强化被使用的记忆 ──
manager.reinforce(results)

# ── 5. 定期维护 (衰减 + 淘汰) ──
manager.maintenance("blog_system")
```

## 数据模型

### MemoryEntry

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | UUID 唯一标识 |
| `project_id` | str | 所属项目 |
| `memory_type` | MemoryType | 记忆分类 (强类型枚举) |
| `summary` | str | 摘要化总结，用于 BM25 检索和上下文注入 |
| `original_text` | str | 原始文本，可回溯完整细节 |
| `metadata` | dict | JSON 元数据（上下文、来源、自定义字段） |
| `tags` | list[str] | 扁平标签 |
| `importance_score` | float | 重要性 0.0~1.0（支持强化与衰减） |
| `access_count` | int | 被检索使用的次数 |
| `last_accessed_at` | str | 最后访问时间 |
| `created_at` | str | 创建时间 |
| `is_pinned` | bool | 是否固定（不参与淘汰） |
| `embedding` | bytes | 向量 BLOB（预留） |

### 记忆类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `preference` | 用户偏好 | "用户喜欢组合优于继承" |
| `decision` | 设计决策 | "BlogService 使用 CQRS 分离读写" |
| `rejection` | 被拒绝的方案 | "不要用 Session 认证" |
| `convention` | 代码/设计规范 | "项目统一使用 MVC 分层" |
| `insight` | 通用洞察 | "领域模型倾向贫血模型" |

## API 参考

### MemoryManager

| 方法 | 说明 |
|------|------|
| `remember(project_id, ...)` | LLM 调用后提取并存储记忆 |
| `recall(project_id, query, top_k, mode)` | 根据查询检索相关记忆 |
| `inject_memories(prompt, results)` | 将记忆注入 system prompt |
| `reinforce(results_or_id)` | 强化记忆（被使用后调用） |
| `forget(project_id, memory_id)` | 删除指定记忆 |
| `list_memories(project_id, type)` | 列出项目记忆 |
| `stats(project_id)` | 获取统计信息 |
| `maintenance(project_id)` | 执行衰减 + 淘汰 |
| `pin(memory_id, project_id)` | 固定记忆（保护） |
| `unpin(memory_id, project_id)` | 取消固定 |
| `clear_project(project_id)` | 清除项目所有记忆 |
| `close()` | 关闭数据库连接 |

### 检索模式

| 模式 | 状态 | 说明 |
|------|------|------|
| `BM25` | ✅ 已实现 | FTS5 + jieba 分词全文检索 |
| `VECTOR` | 🔌 预留 | Embedding 语义检索，接口已定义 |
| `HYBRID` | 🔌 预留 | BM25 + Vector RRF 融合 |

### 生命周期参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `reinforce_delta` | 0.1 | 每次强化 importance 增量 |
| `decay_factor` | 0.98 | 每次衰减 multiplier |
| `importance_min` | 0.1 | 重要性下限 |
| `max_entries_per_project` | 100 | 项目记忆上限 |
| `prune_batch_ratio` | 0.1 | 每次最多淘汰比例 |
| `pin_access_threshold` | 5 | 访问量达标自动保护 |

## 集成到 UML Designer

### Step 1: 初始化

```python
from memory_system import MemoryManager, MemoryConfig
from app.services.llm_service import chat

config = MemoryConfig(
    db_path="./project_memories.db",
    max_entries_per_project=100,
    enable_bm25=True,
)
manager = MemoryManager(config=config)

async def extract_fn(prompt: str) -> str:
    return await chat(prompt, temperature=0.3, max_tokens=500)
```

### Step 2: LLM 调用后记录

```python
await manager.remember(
    project_id=diagram.name,
    context=f"用户请求{'优化' if is_optimize else '生成代码'}",
    llm_call_type="optimize" if is_optimize else "generate",
    user_input=user_prompt,
    llm_output=llm_response,
    user_feedback=user_feedback,
    extract_fn=extract_fn,
)
```

### Step 3: LLM 调用前检索并注入

```python
results = await manager.recall(
    project_id=diagram.name,
    query=user_instructions,
    top_k=5,
)
enriched_system = manager.inject_memories(
    build_system_prompt(diagram),
    results,
)
response = await chat(prompt=user_prompt, system_prompt=enriched_system)

# 记忆被使用后强化
manager.reinforce(results)
```

### Step 4: 定期维护

```python
# 建议通过定时任务每天执行一次
manager.maintenance(project_id)
```

## 从旧版 (v1 JSON) 迁移

```bash
python migrate.py --json-dir ./old_memories --db ./memories.db

# 只迁移特定项目
python migrate.py --json-dir ./old_memories --db ./memories.db --project blog_system
```

## 设计决策

1. **为什么是 SQLite + FTS5？** — 零运维的全文检索方案，FTS5 内置 BM25 评分，比手写倒排索引更成熟。WAL 模式支持并发读写。

2. **为什么用 jieba？** — 中文语义分词，BM25 term 质量远优于字符级 bigram。"组合模式" 被正确切为一个 term，而非 "组合"+"合模"+"模式"。

3. **为什么 summary + original_text 分离？** — summary 用于 BM25 检索和 prompt 注入（简洁），original_text 保留完整上下文（可回溯）。兼顾检索效率和信息密度。

4. **为什么需要生命周期管理？** — 记忆不能只进不出。强化让高频使用的记忆更靠前，衰减让过期信息自然淡化，淘汰防止记忆爆炸。

5. **为什么 Embedding 只预留接口？** — 当前 BM25 + jieba 已覆盖核心检索需求。向量检索引入外部依赖（embedding API 或本地模型），接口化后可无缝接入，不阻塞当前功能。
