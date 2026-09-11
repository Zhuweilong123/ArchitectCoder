# Trace 转评测用例子能力设计

> 状态：已实现基线（设计与实现说明，2026-09-11 更新）
>
> 所属：`extensions/evals` 评测插件
>
> 目标：把一次真实 DevAgent Trace 转换为可审阅、可复现、可验证的评测用例草稿；在人工确认后发布为版本化评测资产。

## 1. 背景与目标

Trace 记录了 DevAgent 在真实工程任务中的用户请求、工具调用、工具结果、最终回答和运行指标。评测体系则提供了版本化 Case、受控 fixture、确定性 Checker、隔离运行和版本对比能力。

“Trace 转评测用例”将二者衔接，使真实任务中的成功经验和问题复现可以持续沉淀为回归资产。

该能力的产品定位是 **Trace Case Factory**：

```text
真实 Trace
  → 评测草稿
  → fixture / manifest 绑定
  → 检查器审核
  → 隔离试运行
  → 发布为版本化 Case
```

### 1.1 设计目标

- 将 Trace 中的任务输入、对话轮次和执行证据转换为评测草稿。
- 复用生产 DevAgent 和标准 `EvalRunner` 进行最终验证。
- 自动提供可解释的候选检查器，但不把 Agent 的偶然执行路径固化为硬规则。
- 支持单轮与多轮任务，支持成功任务沉淀和后续的失败复现。
- 默认不修改仓库评测资产；发布必须由用户显式确认。

### 1.2 非目标

- 不把 Trace 回放结果视为评测通过结果。
- 不要求被评测 Agent 按原 Trace 的工具顺序、Thought 或原始回答逐字执行。
- 不修改 AgentChat、Trace 写入、Trace 回放或现有 `EvalRunner` 的主流程。
- 不自动把新用例提升为正式基线或发布门禁。

## 2. 架构边界

该能力是 Evals Provider 的可选子能力，Trace 仅作为按需读取的数据源。它不订阅 Trace 事件、不改变 Trace 文件格式，也不向聊天执行链路注入逻辑。

```text
┌────────────────────────────────────────────┐
│ AgentChat / Trace 写入 / Trace 回放           │
│                 保持不变                     │
└──────────────────────┬─────────────────────┘
                       │ 用户主动发起时只读查询
                       ▼
┌────────────────────────────────────────────┐
│ Trace Query Port                            │
│ read_trace(session_id)                      │
└──────────────────────┬─────────────────────┘
                       ▼
┌────────────────────────────────────────────┐
│ Evals Provider: Trace Case Factory          │
│ extract → draft → validate → publish        │
└──────────────────────┬─────────────────────┘
                       ▼
┌────────────────────────────────────────────┐
│ versioned cases / fixtures / manifests      │
│ standard EvalRunner / batches / archives    │
└────────────────────────────────────────────┘
```

### 2.1 所有权

| 领域 | 所有者 | 责任 |
|---|---|---|
| Trace 持久化、查询和回放 | Trace 插件 | 提供只读 Trace 事件 |
| 草稿提取、推断、校验、发布 | Evals 插件 | 完整拥有 Trace Case Factory |
| API 鉴权与传输 | `extensions/evals/full_api.py`、`extensions/evals/api.py`、`backend/app/main.py` | 插件路由挂载、认证和请求转发 |
| 评测用例编辑与发布体验 | Evaluation Center | 评测能力入口与状态展示 |

Evals 插件只能依赖核心层暴露的 Trace 查询契约，不得直接导入 `extensions.trace` 的具体存储实现。

## 3. 现有能力与缺口

当前系统已经具备：

- Trace 事件包含 `user_message`、`tool_call`、`tool_result`、`done`、`task_summary`、LLM 用量和运行时信息。
- `EvalCase` 支持单轮 `prompt`、多轮 `turns`、fixture、ProjectManifest、预算和 Hard/Diagnostic Checkers。
- `EvalRunner` 已在隔离工作区物化 fixture，并使用生产 DevAgent 运行、校验和记录 Trace。
- Evaluation Center 已支持批次、性能结果、多版本比较和归档。

关键缺口是：普通聊天 Trace 通常记录工作区路径和行为证据，但不保证保存执行前的完整项目快照。因此，历史 Trace 不能天然还原原始输入状态。

这决定了正式 Case 的发布必须经过 fixture 绑定或捕获，以及一次真实隔离试运行。

## 4. 生命周期与状态机

```text
draft_created
  → fixture_bound
  → review_ready
  → validation_running
  → validated | validation_failed
  → published
```

- `draft_created`：已提取 Prompt、轮次、工具摘要和候选检查器。
- `fixture_bound`：已选择已有 fixture，或从用户确认的工作区捕获新 fixture。
- `review_ready`：用户已审核 Case 元数据、检查器和安全提示。
- `validated`：标准 `EvalRunner` 在新隔离工作区中完成试运行。
- `published`：显式写入受控 Case、manifest 和 fixture 目录。

任何 `validation_failed` 草稿都保留证据和修改入口，但不能发布为正式用例。

## 5. 数据提取与映射

| Trace 证据 | 评测草稿字段 | 默认策略 |
|---|---|---|
| `user_message` 序列 | `prompt` / `turns` | 高置信度直接提取 |
| `project_file`、`source_dir`、`test_dir` | fixture / manifest 候选项 | 需要用户确认与路径校验 |
| `user_message` 序列 | `prompt` 或 `turns` | 仅保留非空用户消息 |
| 支持的工具调用名称 | `trace_policy` 候选 Checker | 仅作为观察证据，默认不升级为 hard checker |
| `tool_result.error` | 草稿 `warnings` | 记录源 Trace 的工具错误数量 |
| 事件类型和数量、Trace 内容 | `trace_summary`、`trace_sha256` | 用于审阅和来源追踪 |

当前实现只自动生成 `trace_policy` 候选，不会从自然语言答案、设计差异或命令成功状态推断 UML、pytest 或 Answer Checker；这些检查器必须由用户在审阅阶段显式填写。

### 5.1 检查器原则

优先验证最终交付物和安全边界，而不是验证实现路径。

- 高置信度：`file_exists`、`file_contains`、`uml_valid`、`paths_unchanged`。
- 中置信度：`pytest`、`uml_relation`、`uml_method`、`trace_policy`。
- 低置信度：`answer_contains_all`、工具顺序、原始回答全文。

自动生成的检查器默认放入 `checkers`。只有在用户确认且证据充分时，才允许放入 `hard_checkers`。

## 6. Fixture 与可复现性

### 6.1 两种转换模式

| 模式 | 输入状态 | 用途 | 发布资格 |
|---|---|---|---|
| 历史 Trace 转草稿 | 当前用户选择的项目状态 | 从已有 Trace 提取任务 | 必须重新验证 |
| 可复现实例转用例 | 转换时捕获并确认的工作区快照 | 新增高质量回归资产 | 验证通过后可发布 |

系统必须显式标注 fixture 的来源时间和状态哈希。不能声称当前快照就是 Trace 执行时的原始快照，除非该快照有受控引用。

### 6.2 捕获约束

- 只允许从已配置的 Agent 工作区根目录捕获。
- 仅复制项目所需的 `design/`、`src/`、`test/` 等已确认目录。
- 排除 `.git`、`.env`、依赖目录、缓存、运行时产物和符号链接。
- 所有发布后的路径必须相对于 fixture 根目录。
- 使用 staging 目录生成资产，完成校验后再原子发布。

## 7. Provider 与 API 契约

Trace Case Factory 是 EvalProvider 的可选扩展契约，避免强制所有第三方 Evals Provider 实现该能力。

```python
class TraceCaseFactory(Protocol):
    async def create_trace_case_draft(self, request): ...
    def get_trace_case_draft(self, draft_id: str): ...
    async def validate_trace_case_draft(self, draft_id: str): ...
    def publish_trace_case_draft(self, draft_id: str, request): ...
```

若当前 Provider 未实现该协议，接口返回“当前评测 Provider 不支持 Trace 转评测用例”，不影响既有评测功能。

API 统一归属于 Evals，当前实现的完整路由为：

```text
POST /api/evals/trace-cases/drafts
GET  /api/evals/trace-cases/drafts/{draft_id}
POST /api/evals/trace-cases/drafts/{draft_id}/validate
POST /api/evals/trace-cases/drafts/{draft_id}/publish
GET  /api/evals/trace-cases/projects
GET  /api/evals/trace-cases/drafts
GET  /api/evals/trace-cases/drafts/{draft_id}
PUT  /api/evals/trace-cases/drafts/{draft_id}
DELETE /api/evals/trace-cases/drafts/{draft_id}
POST /api/evals/trace-cases/drafts/{draft_id}/fixture-preview
POST /api/evals/trace-cases/drafts/{draft_id}/capture-fixture
```

`/api/trace` 保持只读的浏览与回放职责，不承载草稿、fixture 或发布逻辑。

## 8. Evals 插件内部模块

```text
extensions/evals/
├─ trace_cases.py         # Trace 提取、草稿、fixture、校验与发布
├─ api.py                 # /trace-cases/* HTTP 适配器
├─ provider.py            # 暴露可选子能力
└─ runner.py              # 复用现有标准试运行能力
```

草稿运行时数据存放在：

```text
temp/evals/trace-case-drafts/<draft_id>/
```

正式发布资产仍使用既有受控目录：

```text
backend/evals/cases/
backend/evals/projects/
backend/evals/fixtures/
```

## 9. 前端交互

主入口放在 Evaluation Center，而非 TraceViewer：

```text
从 Trace 创建评测用例
  1. 选择 Trace
  2. 选择保留轮次与评测集
  3. 绑定或捕获 fixture
  4. 审核元数据、预算与 Checker
  5. 隔离试运行
  6. 发布为正式用例
```

TraceViewer 后续可以提供“生成评测草稿”快捷按钮，但仅负责携带 `session_id` 打开 Evaluation Center；不持有草稿状态，不承担发布逻辑。

## 10. 安全与隐私

- 不将完整系统提示词、环境快照、密钥、令牌或完整工具观察结果写入正式 Case。
- 对 Prompt、路径、工具参数和最终答案执行敏感信息扫描与人工确认。
- 草稿元数据只保存 Trace `session_id`、Trace 内容哈希、生成时间、来源版本和确认记录。
- 禁止自动提升基线；发布的 Case 先作为普通用例进入目录。
- 对失败 Trace，仅生成“问题复现草稿”；期望结果和 Hard Checkers 必须由用户明确填写。

## 11. 验证规则

Trace 回放的 `mock` 模式只验证记录一致性，不能用于 Case 发布。

发布校验必须：

1. 物化 fixture 到新隔离工作区。
2. 按标准评测路径装配生产 DevAgent。
3. 执行生成后的 `EvalCase`。
4. 运行 Hard/Diagnostic Checkers。
5. 保存结果、Trace、工作区快照和 Checker 证据。

只有试运行通过的草稿才能发布；发布后仍应作为普通用例运行至少一次批次评测，再由人工决定是否纳入正式基线。

## 12. 分期交付

### Phase 1：可用 MVP（当前已实现）

- 单轮、成功工程 Trace 转草稿。
- Prompt、路径、变更文件和工具摘要提取。
- 手动选择现有 fixture 或捕获当前工作区。
- 文件、UML 有效性、路径保护类 Checker。
- 标准试运行与显式发布。

当前实现额外支持：多轮 Trace、草稿编辑/删除、fixture 预览、受控路径捕获、Trace SHA-256、捕获快照 SHA-256 和原子发布。

### Phase 2：覆盖扩展（后续规划）

- 多轮 Trace 转 `turns`。
- `pytest`、`trace_policy` 和 UML 结构 Checker 推荐。
- 草稿质量评分、缺失项提示和敏感信息扫描。
- TraceViewer 快捷跳转。

### Phase 3：规模化沉淀（后续规划）

- 失败 Trace 的缺陷复现工作流。
- 相似 Trace 聚类、候选用例去重和资产 Diff。
- 用例审批、基线候选池与 CI 自动验证。

## 13. 验收标准

- 关闭 Evals 插件时，该功能不可用但聊天、Trace 和回放不受影响。
- Trace 插件不可用或 Trace 不存在时，Evals 子能力返回明确错误，不影响其他评测功能。
- 从草稿生成到发布期间，未经确认不得写入 `backend/evals/`。
- 发布后的 Case 能被既有 Registry 加载，并由既有 EvalRunner 执行。
- 标准试运行不依赖 mock 回放，也不复用原 Trace 的工具结果。
- 所有发布资产都包含来源 Trace、Trace 哈希、fixture（如有）哈希和审阅时间；人工确认以显式 review、validate、publish 请求为边界，不由系统伪造额外审批记录。
## Implementation boundary update

The Trace-to-case capability is owned by the Evals extension:

- `extensions/evals/trace_cases.py` owns request models, Factory lifecycle, fixture preview/capture, validation, publishing and Trace evidence recommendations.
- `extensions/evals/api.py` owns the `/api/evals/trace-cases/*` HTTP routes.
- `extensions/evals/provider.py` adapts the capability to the local Evals provider.
- `backend/app/agent_base/core/plugins.py` only provides the generic plugin router mounting mechanism.
- `extensions/evals/full_api.py` keeps generic evaluation catalog and batch APIs and mounts the Trace-case router; it no longer relies on a host `backend/app/api/evals.py` module.

This keeps the Trace-to-case feature optional and removable without adding Trace-specific business logic to the host application.
