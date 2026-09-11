# 产品差异化与联合演进路线

> 文档定位：ArchitectCoder 当前产品战略、差异化边界与实施优先级
>
> 状态：vNext 规划基线
>
> 更新日期：2026-09-07
>
> 当前架构以 [`current-architecture.md`](current-architecture.md) 为准；本文不替代具体子系统设计。

## 1. 核心结论

ArchitectCoder 不应继续被定义为“带 UML 的 Coding Agent”，也不应与通用编程
Agent 比拼模型、ReAct、子代理或聊天体验。更有竞争力的定位是：

> **面向复杂存量系统的可执行架构治理与联合演进平台：让每次变更都能证明需求、
> 设计、代码和测试仍然一致。**

“设计即真相源”仍是核心理念，但这里的设计不能只是一组图，也不能只依赖 Agent
遵守提示词。它必须升级为：

- **可持久化**：需求、架构决策、约束和验收标准是工程资产；
- **可计算**：设计元素能稳定映射到代码符号和测试；
- **可执行**：违规变更能够在写入、完成任务和合并代码前被阻断；
- **可演进**：设计与代码可以双向同步，并明确区分实现错误、设计过期和有意偏离；
- **可证明**：每次变更都有审核、验证、Trace 和运行证据。

最终目标不是让用户“画更多图”，而是降低复杂系统变更中的理解成本、架构漂移和
评审风险。

## 2. 竞争边界

### 2.1 “规格驱动”已经成为行业能力

GitHub Spec Kit、Kiro 等产品已经把 requirements → design → tasks → implementation
做成标准工作流，并开始支持 Design-First、Brownfield、Hooks、Skills、MCP 和 PR
交付。因此，以下能力只能算 table stakes：

- 先写规格再生成代码；
- Markdown 形式的需求、设计和任务；
- ReAct、native function calling、子代理；
- 记忆、沙箱、工具审批；
- 通用代码生成和测试执行。

参考：

- [GitHub Spec Kit — Spec-Driven Development](https://github.com/github/spec-kit/blob/main/docs/concepts/sdd.md)
- [Kiro Feature Specs](https://kiro.dev/docs/specs/feature-specs/)
- [Kiro Hooks](https://kiro.dev/docs/hooks/)
- [GitHub Copilot Hooks](https://docs.github.com/en/copilot/concepts/agents/hooks)

### 2.2 ArchitectCoder 应建立的真差异

| 维度 | 通用 Spec/Coding Agent | ArchitectCoder 目标 |
|---|---|---|
| 设计载体 | Markdown 规格或临时计划 | 结构化、可交互、可计算的架构契约 |
| 存量系统 | 读取代码后直接修改 | 代码逆向成设计基线，再受控演进 |
| 一致性 | 主要依赖模型理解和测试 | 确定性的设计—代码—测试图谱与 Diff |
| 约束执行 | Prompt、规则文件或通用 Hook | 与设计元素绑定的架构策略和硬门禁 |
| 评审对象 | 代码 Diff | 需求、设计、代码、测试和风险的联合 Diff |
| 证据 | 最终回答、测试日志、PR | 可回放 Run、审批记录、架构检查和验证证据包 |
| 治理 | 项目级指令 | owner、waiver、有效期、设计债务和趋势 |

核心护城河应当是：

1. **可执行设计契约**；
2. **存量代码与设计的双向演进**；
3. **不可绕过的架构门禁**；
4. **从变更意图到验证证据的完整追溯链**；
5. **历史 Run/Trace 转化为持续评测资产的数据飞轮**。

## 3. 当前产品基线

### 3.1 已经形成的优势

当前项目已经具备较完整的平台底座：

- 类图、时序图、组件图及跨图验证；
- V2 全局设计生成、流式绘图和多图 Diff；
- 统一生产 DevAgent 装配与受控工具边界；
- ChangeSet、SHA 冲突检测、原子变更和回滚基础；
- UML Diff 人工审核与敏感命令审核；
- SQLite 持久化 Run、checkpoint、暂停/恢复和审计事件；
- Trace 记录及 mock/rerun/live 回放；
- 生产链路复用的 Evals、Fixture、Checker 和归档；
- KG、长期记忆和插件 Provider 边界。

主 DevAgent 已经接入 `get_project_map`、`find_nodes`、`expand_neighbors` 三个知识图谱
工具。旧规划中“主 Agent 完全碰不到 KG”的描述已经失效。

### 3.2 尚未闭合的关键断点

#### A. 设计优先仍是软约束

系统 Prompt 要求设计影响型任务遵循“修改 UML → 审核 → 接受后实现”，但文件变更工具
没有根据设计审核状态建立写入屏障。当前兜底逻辑是在 Agent 结束时发现未审核的 UML
变更并补推审核；它能够发现遗漏，但不能阻止模型提前修改业务代码。

#### B. 设计—代码 Diff 没有进入默认执行链

KG 已实现 `compare_design_code`，可以发现：

- `missing_implementation`；
- `extra_code`；
- `mismatch`；
- `no_coverage`。

但该工具仍是显式 opt-in，没有作为设计影响型任务的自动前置/后置检查，也没有形成
发布门禁。

#### C. 设计约束不是持久化真相源

V2 优化结果包含 `design_constraints`，但 `.umlproj` 的 Project 模型当前只持久化
名称、版本、revision 和 diagrams。需求、决策、约束、验收标准和豁免尚未成为工程内
的正式资产。

#### D. 追溯链缺少需求和决策层

当前 KG 覆盖设计、源码和测试，但没有 Requirement、Acceptance Criterion、ADR、
Constraint、Run、Evidence、Waiver 等一等节点。因此系统能回答“代码长什么样”，还
不能确定性回答“为什么这样设计、满足哪条需求、由什么证据证明”。

#### E. Harness 尚未完全产品化

后端已有 `/api/runs`、`/api/audit` 和 `/api/metrics`，但前端仍以画布、工具栏、聊天、
Trace 和评测中心为主要入口。用户看到的是多个功能模块，而不是一个连续的变更生命周期。

#### F. 质量表现是“理解强、执行弱”

4.0 受控基线（16 Case）为 8 通过、5 门禁失败、3 执行错误，通过率 50.0%，平均得分
72.21%；其中 understanding 4/4，multiturn 2/4，single 2/8。当前优先级仍是提升跨
设计、源码、测试任务的稳定执行，而不是继续增加 Agent 范式。

评测口径已于 2026-09-07 完成第一轮清理：当前 Case 统一使用 `apply_changes`，并固定
Case Schema、Tool Protocol、Checker Protocol 与 Fixture Layout 版本；损坏或契约漂移的
Case 将使目录加载失败，不再静默改变分母。`hard_checkers` 现在只负责通过门禁，普通
`checkers` 只影响得分；结果及批次可区分 Agent、工具、环境、Checker、超时和预算失败。
旧的 37.5% 结果仅作为历史对照，不与 4.0 口径混合；后续版本必须沿用同一套 Case、
工具协议和失败归因，才能进行趋势比较。

#### G. 多语言广度与深度不一致

界面保留 12 种语言名称，但 KG 的代码层和测试覆盖目前只解析 Python。短期应明确
“Python 深度闭环”，随后通过语言适配器逐个认证 Java、TypeScript 等能力，不应把
通用 LLM 能生成某种语言等同于该语言已支持完整联合演进。

## 4. 目标用户和首要场景

### 4.1 优先用户

- 有复杂存量 Python 后端的研发团队；
- 工业软件、仿真、通信、金融等强契约场景；
- 架构负责人、技术负责人和需要跨模块评审的高级工程师；
- 有接口稳定性、测试证据、审计或合规要求的组织。

雷达仿真项目适合作为第一套标杆工程：它包含明确的组件边界、领域类、调用链和
设计—源码一致性测试，可以完整展示产品价值。

### 4.2 三个黄金场景

#### 场景一：存量代码 → 可信设计基线

导入仓库后自动生成组件图、核心类图和关键时序图，标记映射置信度和未识别部分，
由用户审核后成为受控设计基线。

#### 场景二：需求 → 设计 → 实现 → 证据

用户提出需求，系统给出影响分析和设计 Diff；设计被接受后才能实施源码变更，随后
自动执行设计—代码检查和测试，生成可交付证据包。

#### 场景三：代码偏离 → 修复、同步或豁免

当人工提交或外部 Agent 修改代码导致设计漂移时，CI/PR Check 自动识别，并提供：

1. 按现有设计修复代码；
2. 将实现变化反向同步为设计 Diff；
3. 创建带责任人和到期时间的临时豁免。

第三个场景最能证明 ArchitectCoder 是持续演进平台，而不是一次性生成器。

## 5. 目标产品闭环

```text
需求 / 缺陷 / 变更意图
          ↓
需求澄清 + 验收标准
          ↓
影响分析（设计 + 代码 + 测试）
          ↓
设计契约 Diff
          ↓
人工审核 ──拒绝──> 修订设计
          ↓ 接受
受控代码与测试变更
          ↓
架构一致性门禁 + 测试门禁
          ↓
证据包 / PR Check / 可审计 Run
          ↓
更新设计基线、KG、记忆和回归集
```

每个阶段必须有明确输入、产物、状态和证据，不能只存在于对话文本中。

## 6. Design Contract v2

`.umlproj` 应从“图集合”升级为“设计契约”。建议增加以下一等实体：

| 实体 | 作用 |
|---|---|
| Requirement | 描述必须实现的用户或业务结果 |
| AcceptanceCriterion | 给需求提供可验证的完成条件 |
| Decision | 记录架构决策、原因和替代方案 |
| Constraint | 表达依赖、分层、接口、兼容性和 NFR 规则 |
| DesignElement | 现有组件、类、接口、生命线和消息 |
| CodeSymbol | 文件、类、方法、字段和调用点 |
| TestCase | 自动测试、TestHub 用例或人工验证 |
| Run | 一次联合演进任务及其状态 |
| Evidence | 测试、构建、静态检查、Diff 和审批结果 |
| Waiver | 有理由、有责任人、有期限的临时偏离 |

核心关系至少包括：

```text
Requirement ──satisfied_by──> DesignElement
DesignElement ──implemented_by──> CodeSymbol
Requirement / DesignElement ──verified_by──> TestCase
Run ──produces──> Evidence
Constraint ──governs──> DesignElement / CodeSymbol
Waiver ──temporarily_allows──> ConstraintViolation
Decision ──explains──> DesignElement / Constraint
```

所有实体都应有稳定 ID、版本、来源和 provenance；设计—代码映射必须区分
`confirmed`、`inferred` 和 `unresolved`，避免把启发式结果伪装成事实。

## 7. 架构门禁设计

应新增独立的 `ArchitecturePolicyEngine`，由执行层调用，而不是依赖模型自行遵守。

### 7.1 写入前门禁

- 根据目标路径、任务分类和 ChangeSet 判断是否影响设计；
- 设计影响型任务在写入 `src/` 前必须绑定已接受的设计 revision；
- 若设计为空、审核过期或 revision 冲突，则拒绝写入并返回结构化原因；
- 纯实现修复可以按策略直接执行，但仍需后置一致性检查。

### 7.2 写入后门禁

- 对 ChangeSet 涉及的节点做增量 KG 重建和影响分析；
- 自动运行 `compare_design_code` 和项目自定义架构规则；
- 自动选择受影响测试，而不是默认全量扫描；
- 任何 release-gate 失败都不允许 Run 报告 `completed`。

### 7.3 交付门禁

- 设计 revision、代码 SHA、测试结果和审批记录必须一致；
- 支持严格阻断、警告和带期限豁免三种策略；
- 输出稳定 JSON/SARIF 和退出码，供 CLI、CI 和 PR Check 使用；
- 所有绕过行为必须写入 audit 和最终证据包。

## 8. 产品体验：以 Evolution Run 为中心

现有画布、聊天、DiffViewer、TraceViewer、TestHub 和 Evaluation Center 应保留，但
导航和信息架构要围绕一次变更重组。

建议新增统一的 **Evolution Run 工作台**：

- 顶部：需求、状态、设计 revision、责任人和风险等级；
- 左侧：阶段时间线与 TODO/验收条件；
- 中部：设计 Diff、代码 Diff、测试结果按阶段切换；
- 右侧：影响范围、约束违规、审批和证据；
- 底部：Agent 实时进度、失败原因和恢复入口。

Run 的标准状态建议统一为：

```text
draft → analyzing → design_pending → waiting_approval
      → implementing → verifying → succeeded / partial / failed / waived
```

同时增加 **设计债务面板**，展示：

- 过期设计和未映射节点；
- 缺失实现、多余代码和签名漂移；
- 未覆盖设计元素；
- 被破坏的依赖/分层规则；
- waiver 数量、责任人、到期时间和债务趋势。

## 9. 实施路线

当前 Agent 执行能力优化阶段采用三条边界：不把知识图谱作为执行依赖，不新增或扩展
工具集合，不引入任务分类器。先在同一套 DevAgent、固定工具协议和统一执行闭环下，
通过评测口径、状态管理、失败恢复和证据约束提升稳定性；知识图谱与任务分类相关设想
保留为远期选项，不进入当前里程碑。

### P0：可信基线（第 1–3 周）

目标：先让当前能力可信、可测、口径一致。

- **已完成（2026-09-07）**：修复 Case 工具名与生产工具契约漂移；
- **部分完成**：Case、Tool Protocol、Fixture、Checker 和结果已带版本口径；Prompt 与
  具体 Tool Schema 指纹仍待补齐；
- 统一从仓库根目录和 `backend/` 执行测试的入口；
- 处理 Windows 临时目录权限与 `tests` 包名冲突；
- 将 `design_constraints` 正式持久化；
- 为设计影响型任务自动启用 `compare_design_code`；
- **已完成（2026-09-07）**：结果与批次已区分 Agent、工具、环境、Checker、超时和
  预算失败；评测记录中的正式基线提交为 `4.0@4076efc`（通过率 50.0%，平均得分
  72.21%）。
- 对外只承诺 Python 深度闭环。

验收：

- 评测无基础设施假失败；
- 所有 `completed` Run 都有 mutation evidence 和 verification evidence；
- single/multiturn release-gate 通过率达到 80% 以上；
- 审核前越权写入能够被测试稳定复现并阻断。

### P1：联合演进 MVP（第 4–8 周）

目标：把理念转化为可演示、可交付的闭环。

- 实现 Design Contract v2 与稳定 ID；
- 从 Python 项目逆向生成可审核的设计基线；
- 实现代码变更的增量影响分析和漂移检测；
- 将设计审核状态接入源码写入硬门禁；
- 将 TestHub 用例纳入 Requirement/DesignElement 的追溯关系；
- 上线 Evolution Run 工作台；
- 上线设计债务面板；
- 以雷达项目制作完整黄金路径和回归集。

验收：

- 三个黄金场景端到端跑通；
- 需求→设计→代码→测试追溯覆盖率达到 90%；
- 架构漂移检查支持修代码、同步设计和申请豁免；
- 失败 Run 可以从 checkpoint 恢复且不重复已完成变更。

### P2：进入真实研发流程（第 9–12 周）

目标：让 ArchitectCoder 成为团队交付门禁，而不只是独立工作台。

- 提供 `architectcoder import/impact/check/evidence` CLI；
- 接入 Git diff、GitHub/GitLab PR Check；
- 输出结构化评论、SARIF、退出码和证据附件；
- 增加规则配置、owner、waiver、有效期和审计查询；
- 支持根据变更影响自动选择测试；
- 提供本地/CI 完全一致的检查器执行入口。

验收：

- 外部开发者或其他 Coding Agent 修改代码时，门禁仍然有效；
- PR 能直接展示受影响设计、违规项、测试证据和处理建议；
- 架构检查结果可复现，不依赖自然语言最终回答。

### P3：数据与生态护城河（3–6 个月）

- 将失败 Trace 和人工修订自动沉淀为候选 Eval Case；
- 模型、Prompt、工具或策略升级前自动重放历史变更集；
- 建立真实项目、负向样本、拒绝/恢复和漂移检测 benchmark；
- 通过语言适配器逐步支持 Java、TypeScript；
- 建立分层架构、领域边界、API 兼容、安全和合规规则包；
- 将插件 Provider 扩展到远程 KG、企业策略和托管评测服务。

## 10. 产品指标

### 10.1 北极星指标

> **完整满足设计契约并一次通过人工审核的联合变更比例。**

该指标同时约束设计质量、Agent 执行质量、测试证据和评审成本，比调用次数或生成代码量
更接近实际产品价值。

### 10.2 90 天目标

| 指标 | 目标 |
|---|---:|
| single/multiturn release-gate 通过率 | ≥ 90% |
| 审核前源码越权写入 | 0 |
| 需求→设计→代码→测试可追溯覆盖率 | ≥ 95% |
| 架构漂移误报率 | < 5% |
| Python 存量项目导入到首张可信架构图 | < 10 分钟 |
| 单任务 Token 中位数 | 相对当前基线下降 50% |
| 失败原因可归类率 | 100% |
| 过期 waiver 自动告警率 | 100% |

还应持续跟踪：首次审核接受率、评审耗时、工具错误率、恢复成功率、违规拦截率、
设计债务净变化、周活跃项目数和 PR 合并率。

## 11. 商业化形态建议

| 形态 | 主要能力 | 目标 |
|---|---|---|
| Community / Local | UML、Python import、基础 check、单机 DevAgent | 获取开发者和真实项目反馈 |
| Team | Run 工作台、共享规则、PR Check、债务面板、协作审核 | 进入团队研发流程 |
| Enterprise | 私有化、SSO/RBAC、策略包、审计、waiver 治理、远程 Provider | 架构治理和合规预算 |

开源或免费层应优先开放设计契约格式、CLI 检查和本地运行，以形成事实标准；企业价值
主要放在团队协作、治理策略、审计、规模化索引和托管评测。

## 12. 暂不优先

以下方向可以保留，但在 P0/P1 闭环完成前不应成为主要投入：

- 增加更多 Agent 范式或多 Agent 评审委员会；
- 向量化记忆和复杂记忆推荐；
- 扩充更多 UML 图类型；
- 通用聊天和普通代码补全；
- 未经完整验证的多语言广度；
- 只改善 TraceViewer 展示但不生成可执行回归；
- 与设计契约无关的插件数量扩张。

判断新功能是否进入路线图时，应回答三个问题：

1. 是否提高设计—实现一致性的确定性？
2. 是否缩短一次可信变更的交付时间？
3. 是否形成可复用的数据、规则或集成壁垒？

若三个问题都是否定答案，则不应成为当前优先项。

## 13. 相关文档

| 文档 | 关联 |
|---|---|
| [`current-architecture.md`](current-architecture.md) | 当前 Agent 装配、工具和运行边界 |
| [`baseagents-design.md`](baseagents-design.md) | BaseAgents 框架与工具系统 |
| [`knowledge-graph-design.md`](knowledge-graph-design.md) | 双向演进、影响分析和一致性 Diff 基础 |
| [`evaluation-system.md`](evaluation-system.md) | Case、Fixture、Checker、基线和发布门禁 |
| [`trace-replay-design.md`](trace-replay-design.md) | 证据回放和历史场景回归基础 |
| [`memory-system-design.md`](memory-system-design.md) | 当前 MemoryPort、SQLite provider、检索与生命周期实现 |
| [`context-management-design.md`](context-management-design.md) | Run/Session 上下文预算与恢复 |
| [`plugin-architecture-design.md`](plugin-architecture-design.md) | Provider 边界和后续生态扩展 |
