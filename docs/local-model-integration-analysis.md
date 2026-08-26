# 本地小模型（Qwen3.5-4B）接入分析

> 本文归档 2026-08-26 对后端 Agent LLM 能力的全面盘点，以及引入自部署 Qwen3.5-4B 的可行性分析。
> 结论基于 `temp/chat_log/` 下 45 个真实会话、1397 轮 LLM 调用的**实测数据**，而非估算。
> 作为后续「LLM 路由层 + 上下文压缩」实施的决策基线。
>
> 所有 file:line 引用以 2026-08-26 的代码状态为准。

---

## 1. 背景与目标

已有一个自部署的 Qwen3.5-4B。需要回答：**后端哪些流程可以用它，哪些不能，为什么。**

明确的动机：**增强能力 + 降低 API 成本**。不涉及数据合规/内网隔离诉求——这一点很重要，因为「数据不出内网」会导向完全不同的结论（需要评估全本地的可行边界，可能要更大的模型）。

---

## 2. 后端 LLM 调用全景

### 2.1 两套并行的 LLM 通道

| 通道 | 实现 | 覆盖范围 | provider 能力 |
|---|---|---|---|
| **A. BaseAgentsLLM** | `agent_base/core/llm.py` | Agent 主循环、子代理、全局优化 v2、trace 回放 | 6 provider，**含 ollama / vllm** |
| **B. llm_service** | `services/llm_service.py` | REST 聊天、代码生成、测试生成、单图优化 | 裸 `AsyncOpenAI` 直连 DeepSeek |

通道 A 的 `PROVIDER_CONFIGS`（`core/llm.py:149-182`）已内置本地端点：

```python
"ollama": {"env_key": None, "default_base_url": "http://localhost:11434/v1", ...}
"vllm":   {"env_key": None, "default_base_url": "http://localhost:8000/v1",  ...}
```

异步方法支持 per-call 覆盖模型（`ainvoke:399`、`ainvoke_with_tools:485`、`athink:439`）；**同步 `invoke`/`think` 不支持**，传了会被静默忽略。

### 2.2 现役 LLM 调用点

| # | 位置 | 用途 | 模型档 | 输出 | 失败兜底 |
|---|---|---|---|---|---|
| 1 | `react_agent.py:341` | Agent 主循环 FC | pro | tool_calls | 无 |
| 2 | `subagent_tool.py:75` | 子代理 FC | flash（独立配置） | 文本 summary | 返回最后一条 |
| 3 | `uml_common.py:453` | 优化范围分析 | flash（**硬编码**） | 小 JSON | **返回 None → 回退完整 prompt** |
| 4 | `uml_optimizer_v2.py:200/274` | 全局优化主生成 | pro（**硬编码**） | 32k JSON | error JSON |
| 5 | `agent_chat_ws.py:452` | 记忆抽取（后台） | pro | JSON 数组 | **空列表，非致命** |
| 6 | `code_generator.py` ×7 | 代码/测试/修复生成 | pro | 文件 JSON | 正则抽代码块 |
| 7 | `testhub.py:211` | Excel 用例→测试代码 | pro | 文件 JSON | 包成单文件 |
| 8 | `api/llm.py:26` | 裸聊天 | pro | 文本 | 无 |

**定义了但未接线**的重型工具（`conversation_tools.py:870-878, 917-921` 被注释）：`optimize_uml` / `generate_code` / `validate_code` / `generate_tests` / `fix_code` / `explore_project`。评估时要知道它们存在，但别把力气花在死路径上。

### 2.3 确定性能力占比很高（利好）

这是引入小模型的最大结构性优势——**质量底线由代码保证，模型只负责决策与生成**：

- **知识图谱**：AST + JSON 直读 + `difflib` 模糊匹配，**0 处 LLM**
- **记忆系统**：SQLite + FTS5 + jieba BM25，**0 处 LLM、0 处 embedding**（`memory_system/embedding.py` 只有 Protocol 空壳，DB 里 `embedding` BLOB 列是预留）
- **跨图一致性**：`uml_common` 的 6 项校验 + `_apply_auto_fixes` + `auto_layout`，纯程序化
- **代码校验原语**：`code_validator` 的 6 个工具（`ast.parse`、子进程 import、diff、变更率守卫）全确定性，LLM 只决定"先查哪个、怎么改、何时收工"

---

## 3. 实测：token 成本结构

样本：`temp/chat_log/` 下 45 个会话、1397 轮 LLM 调用。复现脚本见附录 A。

| 指标 | 实测值 |
|---|---|
| prompt tokens | **10,518,040** |
| completion tokens | 752,810 |
| **输入 : 输出** | **14 : 1** |
| 每轮真正新增的 prompt | 982,145 tok（约占总 prompt 的 **9%**，其余 91% 是前缀重发） |
| 其中工具输出占比 | **25%** |
| 工具输出来源 | `read_file` 58.8% + `bash` 36.2% = **95%** |
| 单轮 prompt 峰值 | 55,551 tok |
| 缓存命中率 | 2026-08-21 之前 **0%**；之后 **~90%** |

### 3.1 三个关键结论

**① 成本 93% 在输入侧，不在生成侧。**

14:1 的比例意味着——**让 4B 去"生成"什么，省不下钱**。它的正确用法是决定*什么该进上下文*，而不是产出内容。

**② 最大的一笔成本优化已经拿到了。**

2026-08-21 之前所有 trace 的 `prompt_cache_hit_tokens` 都是 0，之后稳定在 ~90%（`trace_20260822_090646` 为 2,040,704 / 2,228,518 = 91.6%）。这是 `agent_chat_ws.py:290-304`「静态 system prompt 为 KV 缓存优化」生效的结果。按 DeepSeek 缓存约 1/10 的价差算，输入成本已降到约 1/5。

**③ 缓存改变了压缩方案的经济性——两种压缩，结论相反。**

| 方案 | 对缓存前缀的影响 | 经济性 |
|---|---|---|
| **工具输出压缩**（append 时压缩） | 只改变"往后追加什么"，**不动已有前缀** | ✅ 省一次全价 token，且之后所有轮次的缓存前缀也变短 |
| **会话历史压缩**（滚动摘要） | **重写前缀**，击穿整条缓存链 | ❌ 之后每轮变全价 miss，很可能净亏 |

且单轮峰值才 55k，离上下文上限还远，**没有历史压缩的刚需**。

> **决策：不做会话历史压缩。**（`Config.max_history_length=100` 定义了但全项目无引用，维持现状即可。）

---

## 4. 结构性障碍：当前无法混用云端与本地

这是必须先解决的前置条件。

`sub_agent_model` 看起来像分层旋钮，但它只换**模型名**——底层复用同一个 `BaseAgentsLLM` 客户端实例，`api_key` 和 `base_url` 是全局单例（`config.py:23`）。所以「pro 走 DeepSeek、flash 走本地 Qwen」这个最自然的方案，**当前配置结构表达不出来**。

需要把「模型档位」从**字符串**升级成**端点 + 模型 + 参数的三元组**，按角色路由：

```
role: tool_compress   → {endpoint: local_vllm, model: qwen3.5-4b, temp: 0.1}
role: scope_analysis  → {endpoint: local_vllm, model: qwen3.5-4b, temp: 0.1}
role: main_agent      → {endpoint: deepseek,   model: v4-pro,     temp: 0.3}
```

顺带能清掉三个历史包袱：

- `uml_optimizer_v2.py:201/275` 硬编码 `"deepseek-v4-pro"`（改 `.env` 打不到）
- `uml_common.py:456` 硬编码 `"deepseek-v4-flash"`（同上）
- `llm_service.py:22-31` 的 `ModelTier` 枚举是死代码——**所有调用方都不传 `model=`**

**这是唯一需要动架构的地方，其余都是配置和 prompt 工作。**

---

## 5. 小模型适用性分档

判据：本地 4B 适合的画像是 **输入短、输出短、任务是分类/抽取/检索而非生成、失败有确定性兜底、不阻塞用户**。

### A 档：可换，失败无害（但收益有限）

| 流程 | 位置 | 为什么安全 | 收益评价 |
|---|---|---|---|
| 优化范围分析 | `uml_common.py:397-483` | `temp=0.1`、`max_tokens=1000`、输入 <800 字符、输出小 JSON；**`:460-483` 已写好失败回退**，最坏只是退化成没有 scope 优化 | 低频，省不下什么 |
| 记忆抽取 | `agent_chat_ws.py:443-455` | 后台异步，用户不等；`manager.py:239-241` 解析失败返回空列表，非致命 | 高频但输出小 |
| 探索类摘要 | `explore_project_tools.py:231/275/304` | 纯摘要任务 | 当前未注册 |

> 这三个是**零风险试验田**，适合作为接入验证，但按第 3 节的数据，**它们省下的成本可以忽略**。优先级应排在压缩类方案之后。

### B 档：4B 的真正价值区

这一档比 A 档重要得多——它们是**因为调用云端太贵/太慢所以干脆没做**的能力，本地 4B 边际成本近乎为零，正好解锁。

**① `bash` 输出摘要**（占工具输出 36.2%）

pytest 日志、git 输出等噪音大、信息密度低。保留错误行和结论，压掉噪音。典型 4B 任务。

**② `read_file` 相关性选段**（占工具输出 58.8%）

⚠️ **绝对不能摘要**。`edit_file` 需要逐字精确匹配，摘要过的文件内容会让后续编辑必然失败。

正确做法是**选段**：给 4B 文件的行号索引 + 当前任务，让它返回相关的行号区间，然后**逐字**读那几段。这是检索任务不是摘要任务，输出只是几个数字，对 4B 极其友好。

**③ 意图路由 / 工具集裁剪**

主 Agent 每轮把**全部 15 个工具 schema** 发出去（`react_agent.py:306`，无裁剪）。`registry.py:107-110` 其实已有 `get_openai_specs_for(names)` 能按名裁剪，只是没人用。用 4B 先判「闲聊 / 查询 / 改设计 / 改代码」，再决定加载哪个工具子集——省 token，也降低小模型选错工具的概率。

**④ 记忆检索查询扩展**

BM25 召回强依赖查询词命中。用 4B 把任务描述扩成关键词集合再喂 FTS5，失败就用原查询。低风险，召回率提升明显。

**⑤ 拆分 `spawn_subagent` 为读写两档**

子代理是唯一已有独立模型配置的位置（`SUB_AGENT_MODEL`），但它现在**同时承担探索和改文件**（带 bash 写权限）。建议拆成：只读探索子代理（`read_file`/`glob`/`grep`）走本地 4B，写操作子代理保持云端。README 里说子代理的目的就是"探索、摘要、只回传结论"——那部分正是 4B 的活。

### C 档：不建议

| 流程 | 位置 | 为什么不行 |
|---|---|---|
| 全局优化 Phase 2 | `uml_optimizer_v2.py:200` | `max_tokens=32768`，一次性吐出全部 diagrams + 跨图约束 + 一致性报告。长结构化生成 + 多约束推理是 4B 的能力断崖；本地生成 32k token 的耗时也不现实 |
| Agent 主循环 FC | `react_agent.py:341` | 15 工具 × 最多 50 步 × 无历史压缩，直接写文件、跑 bash。FC 一旦掉链子，`:414-435` 只能把错误喂回去重试，**没有文本解析兜底**（降级路径存在但是同步的、WS 里禁用了） |
| 代码/测试生成 | `code_generator.py` | 要求输出可编译代码 |
| `fix_code` | `code_fixer.py` | 需精确定位并修改代码，且走同步 `invoke`，连重试都没有 |
| 会话历史压缩 | — | 见 §3.1③，会击穿 90% 缓存 |

---

## 6. 已知坑清单

| # | 坑 | 位置 | 说明 |
|---|---|---|---|
| 1 | **本地 provider 路径是断的** | `core/llm.py:227` | ollama/vllm 的 `env_key` 为 `None`，不显式传 `api_key` 则 `_client` 根本不构造，调用直接 `RuntimeError`（`:335-339`）。必须传 `api_key="not-needed"` 或设非空 `LLM_API_KEY`。provider 声明了但**没人跑通过** |
| 2 | `from_settings()` 硬绑 DeepSeek | `core/llm.py:246-273` | 绕过 auto-detect，所有走它的地方（**包括 replay rerun**）都拿不到本地端点 |
| 3 | `deepseek_api_key` 是必填 | `config.py:19` | 即使全换本地也得留占位值才能启动 |
| 4 | **思考链吃 max_tokens** | — | 已在 DeepSeek 上踩过。Qwen3.5 若默认开思考模式，`_analyze_scope` 的 `max_tokens=1000` 会被推理过程吃光、正文为空。这恰好是 A 档首选，接入时第一个要验证 |
| 5 | 流式 `athink` 无重试 | `core/llm.py:430-465` | `ainvoke` 有 2 次退避重试，`athink` 没有。本地小模型冷启动慢的话，流式路径更脆 |
| 6 | 别把 4B 当 embedding 模型 | — | 记忆/KG 的 `embedding` BLOB 列、`GraphConfig.embedding_dim=384` 都预留好了，但生成模型不能干这活——需要单独的 `bge-small-zh` 之类。这是独立话题，别混在一起 |

### 6.1 顺带发现一个真 bug（与模型无关，建议先修）

`TruncateHook`（`core/hooks.py:179-183`）对**所有工具**无差别硬切 2000 字符：

```python
def __call__(self, ctx: HookContext) -> Optional[str]:
    output = ctx.tool_output
    if output is not None and len(output) > self.max_chars:
        return output[: self.max_chars]      # ← 不加任何截断标记
    return None
```

而 `ReadFileTool._execute`（`file_system_tools.py:165-166`）只有在调用方**显式传了 `limit`** 时才追加 `... (N more lines)`；不传 `limit`（常见情况）就返回全文，随后被 TruncateHook 静默腰斩。

**实测：`read_file` 有 200/333 = 60% 的调用被截断**，全部工具 301/1266 = 23.8%。模型以为自己读完了整个文件，然后用超出前 2000 字符的内容构造 `edit_file` 的 `old_string`，匹配必然失败，且**无法归因**。这是 agent 返工的一个已知来源。

修复只需在截断时追加 `\n... [truncated N chars]`，**一行代码，不需要任何模型**。

---

## 7. 验证方式：replay L2 就是现成的 A/B 台

`services/replay.py` 的 **L2 rerun** 模式天生适合模型对比——按录制的 trace 重放，**真实调 LLM、工具全 mock**，`replay.py:563-571` 把回放的 `steps` 和原始 `recorded_steps` 同构逐轮对比。`temp/chat_log/` 里已经躺着 45 个真实会话 trace。

唯一缺的是一个旋钮：`_build_rerun_llm()`（`replay.py:417-420`）把模型硬编码成 `BaseAgentsLLM.from_settings(temperature=0.3)`，`api/trace.py:44-64` 的 endpoint 也没有 `model` 参数。

加上之后即可：**同一条历史 trace，分别用 DeepSeek 和本地 Qwen 重跑 → 逐轮对比工具选择是否一致、JSON 是否可解析、最终答案是否等价。**

零成本、可重复、用真实历史数据。**这应该是第一件做的事——先有尺子，再谈换不换。**

建议指标：

- 工具选择一致率（同一轮选中同一个工具的比例）
- 工具参数 JSON 可解析率
- 最终答案等价性（`replay.py:567` 目前是字符串相等，可能需要放宽成语义等价）
- 完成同一任务所需轮数

---

## 8. 建议推进顺序

```
0. TruncateHook 加截断标记                      ← 1 行，修静默截断 bug，与模型无关
1. replay rerun 加 model 参数                    ← 造尺子，用 45 个现成 trace
2. LLM 路由层：角色 → {endpoint, model}          ← 唯一的架构改动
3. bash 输出压缩（4B）                            ← 占工具输出 36%，压缩安全
4. read_file 相关性选段（4B）                     ← 占 58.8%，做法不同（选段 ≠ 摘要）
5. 意图路由 / 工具集裁剪（4B）                     ← 削 tool schema + 降低选错工具率
6. scope 分析 / 记忆抽取换本地                     ← 零风险但收益小，排最后
✗  会话历史压缩                                   ← 不做，会击穿 90% 缓存
```

### 8.1 收益预期（要说实话）

工具输出占全价 token 的 25%，压到 1/4 大概能省 **15~20% 的输入成本**。**不是数量级的节省**——大头已经被前缀缓存拿走了。

真正的收益在**质量侧**：不再静默截断、不再返工、上下文更干净。

### 8.2 延迟账

本地 4B 压缩每次工具调用要多花 2~3 秒。1266 次调用里只有 **24% 超过 2000 字符**——**只对超阈值的输出启用压缩**，否则直接透传。

另外本地单卡 vLLM 是串行的，而 agent 循环是 async 的；并发场景下要考虑排队。

---

## 9. 一句话总结

> 后端确定性程度很高，LLM 只负责决策和生成——这个架构本来就适合降档。但实测显示成本 93% 在输入侧、且最大的一笔优化（前缀缓存）已经拿到，所以 **4B 不该被规划成"替换云端调用"，而该被规划成"上下文守门员"**：决定什么进上下文、以什么密度进。

---

## 附录 A：实测数据复现脚本

在 `backend/` 目录下运行。

### A.1 总量与缓存命中

```python
import json, glob
files = sorted(glob.glob('../temp/chat_log/*.jsonl'))
P = C = H = N = 0
for f in files:
    for line in open(f, encoding='utf-8'):
        try: o = json.loads(line)
        except: continue
        if o.get('event_type') != 'llm_response': continue
        u = o.get('usage') or {}
        P += u.get('prompt_tokens') or 0
        C += u.get('completion_tokens') or 0
        H += u.get('prompt_cache_hit_tokens') or 0
        N += 1
print(f"{N} 轮 | prompt {P:,} | completion {C:,} | cache {H:,} ({H/P*100:.1f}%)")
print(f"prompt : completion = {P/C:.1f} : 1")
```

### A.2 每轮新增 prompt 中工具输出的占比

关键在于用**实测的相邻两轮 prompt 增量**，而非"每条工具输出在后续每轮重发"的建模估算——后者会显著高估（实测部分会话算出 >100%，说明模型不成立：子代理调用有独立的 message list，不继承主历史）。

```python
import json, glob, collections
files = sorted(glob.glob('../temp/chat_log/*.jsonl'))
sum_delta = sum_toolchars = n_pairs = 0
share = collections.Counter()
for f in files:
    prev, pending = None, []
    for line in open(f, encoding='utf-8'):
        try: o = json.loads(line)
        except: continue
        et = o.get('event_type')
        if et == 'tool_result':
            pending.append((o.get('tool_name'), o.get('fed_length') or 0))
        elif et == 'llm_response':
            p = (o.get('usage') or {}).get('prompt_tokens') or 0
            if prev is not None and p > prev and p - prev < 60000:
                sum_delta += p - prev
                sum_toolchars += sum(x[1] for x in pending)
                n_pairs += 1
                for nm, L in pending: share[nm] += L
            prev, pending = p, []
print(f"{n_pairs} 对样本 | 新增 prompt {sum_delta:,} tok")
print(f"工具输出 {sum_toolchars:,} 字符 ≈ {sum_toolchars/3.5:,.0f} tok "
      f"→ 占 {sum_toolchars/3.5/sum_delta*100:.0f}%")
for k, v in share.most_common(6):
    print(f"  {k:18s}{v:9,d} 字符  {v/sum(share.values())*100:5.1f}%")
```

### A.3 截断率统计

trace 的 `tool_result` 事件带 `fed_length` / `fed_truncated` 字段，可直接统计：

```python
import json, glob, collections
by = collections.defaultdict(lambda: [0, 0, 0])   # count, total_chars, truncated
for f in glob.glob('../temp/chat_log/*.jsonl'):
    for line in open(f, encoding='utf-8'):
        try: o = json.loads(line)
        except: continue
        if o.get('event_type') != 'tool_result': continue
        r = by[o.get('tool_name', '?')]
        r[0] += 1; r[1] += o.get('fed_length') or 0
        if o.get('fed_truncated'): r[2] += 1
for t, (n, s, tr) in sorted(by.items(), key=lambda x: -x[1][1]):
    print(f"{t:20s}{n:6d}次{s:10,d}字符  均值{s//max(n,1):6,d}  截断{tr:4d} ({tr/max(n,1)*100:.0f}%)")
```

---

## 附录 B：相关文档

- [BaseAgents 框架设计](baseagents-design.md)
- [Trace 回放机制设计](trace-replay-design.md) — L1/L2/L3 语义，A/B 台的基础
- [记忆系统设计](memory-system-design.md) — BM25 检索现状、embedding 预留
- [知识图谱设计](knowledge-graph-design.md) — 确定性构建，无 LLM
