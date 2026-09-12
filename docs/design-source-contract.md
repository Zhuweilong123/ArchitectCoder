# 设计—源码一致性契约

> 状态：4.0 规划基线（第一阶段）
> 目的：定义设计文件、源码和测试之间的权威关系与可复用校验语义。
> 范围：项目工作区、UML 设计集合、源码目录、测试目录，以及后续一致性规则引擎。

第二阶段的事实采集由可替换插件 `extensions.design_contract:create` 提供，核心只依赖
`backend/app/agent_base/core/contracts.py` 中的 `ContractProvider` 契约。

## 1. 设计目标

一致性检查的目标不是强迫设计和源码逐字相同，而是识别以下三类状态：

1. 设计意图尚未实现（implementation drift）。
2. 源码已经演进但设计尚未同步（design drift）。
3. 测试、设计和源码描述的行为不一致（behavior drift）。

检查结果必须保留证据和来源，不得在没有人工确认时擅自推断哪一方正确。

## 2. 权威关系

| 内容 | 首要权威 | 次要证据 | 说明 |
|---|---|---|---|
| 系统结构、模块边界、接口意图 | UML 设计集合 | 源码依赖关系 | UML 表达设计目标，不等于已经实现 |
| 可执行类、函数、属性和签名 | 源码 | UML 类图 | 源码是实际运行契约 |
| 可观察行为和回归约束 | 测试 | 源码、UML 时序图 | 测试必须描述仍然有效的行为 |
| 文件访问范围和活动设计文件 | WorkspaceManifest | 前端请求、Trace | Manifest 是运行时唯一规范化结果 |
| 变更是否被接受 | 人工评审记录 | Diff、Trace、Checker | 自动检查不能替代设计决策 |

冲突处理原则：

- 设计与源码冲突时，报告漂移并标注 `design_vs_source`，不能直接修改测试来消除冲突。
- 源码与测试冲突时，先判断测试是否仍代表产品行为；评测测试文件默认不可由 Agent 修改。
- 测试通过不代表设计正确，设计加载失败也不能被测试通过掩盖。

## 3. 项目级契约实体

契约规则面向以下稳定实体，而不是面向某个项目的固定文件名：

| 实体 | 必要标识 | 来源 | 主要关系 |
|---|---|---|---|
| `Project` | `project_id` | 工作区/项目配置 | 包含设计文件、源码和测试 |
| `DesignFile` | `design_file_id`, `path` | `.umlproj`/`.uml` | 属于 Project，包含多个 Diagram |
| `Component` | UML `component.id` | 组件图 | 拥有接口和模块边界 |
| `Class` | UML `class.id` | 类图、源码 | 属于 Component |
| `Interface` | 接口名或稳定 ID | UML、源码 | 由 Component/Class 提供或依赖 |
| `Method` | 所属类 + 名称 + 参数 | UML、源码 | 被 SequenceMessage 或测试引用 |
| `Attribute` | 所属类 + 名称 | UML、源码 | 反映实例状态或配置 |
| `Relation` | UML relation ID | UML、源码依赖 | 表达拥有、依赖、继承等关系 |
| `SequenceMessage` | message ID + order | 时序图、调用证据 | 映射到方法调用 |
| `SourceModule` | 相对路径/模块名 | 源码 | 属于 Component 或 Project |
| `TestCase` | 测试节点 ID/路径/名称 | 测试 | 验证行为或结构规则 |

实体 ID 应在重命名和文件拆分时保持稳定；无法保持时必须提供迁移映射，而不能被视为全新实体。

## 4. 多设计文件模型

一个项目可以包含多个设计文件。`WorkspaceManifest` 中：

- `project_files` 表示项目内全部可识别的设计文件。
- `project_file` 表示当前活动设计文件，可以为空。
- `design_root` 是设计文件集合的边界，不等同于某个活动文件所在目录。
- 只有在目录中唯一发现设计文件时，系统才自动设置活动文件。
- 多文件项目必须通过显式选择、项目配置或 Agent 任务上下文确定活动文件。

一致性检查应支持两种范围：

```text
project scope  = 所有设计文件与整个源码/测试集合
file scope     = 一个活动设计文件及其声明的组件/模块
```

跨设计文件引用必须使用稳定实体 ID 或明确的 `design_file_id + entity_id` 组合，不能依赖数组位置。

## 5. 设计—源码映射矩阵

| 设计实体 | 源码实体 | 测试证据 | 基础校验 |
|---|---|---|---|
| Component | package/module/directory | 模块级测试 | 模块归属、依赖方向 |
| Interface | Protocol/ABC/public API | 接口行为测试 | 名称、提供/依赖关系 |
| Class | class definition | 类/集成测试 | 类名、所属模块 |
| Attribute | instance/class field | 状态或序列化测试 | 属性名、类型、可见性 |
| Method | method/function | 单元/集成测试 | 名称、参数、返回值 |
| Relation | import/call/field reference | 集成测试 | 关系类型和方向 |
| SequenceMessage | call site | 流程测试 | 调用者、被调用者、顺序 |
| Diagram | source module set | 设计一致性测试 | 非空加载、引用完整 |

映射必须保存来源位置，例如相对文件路径、类名、方法名和 UML 实体 ID；只保存名称不足以支持可靠的漂移分析。

## 6. 规则分级

### 6.1 阻断规则（`blocker`）

发现后不能声称契约已闭合：

- 设计文件无法解析或项目图集为空。
- UML 引用不存在的组件、类或生命线。
- 活动设计文件不在当前工作区范围内。
- 设计声明的公开接口在源码中完全不存在。
- 变更后项目无法通过必要的结构校验。

### 6.2 警告规则（`warning`）

需要人工判断，但不自动阻止实现：

- 源码新增未建模的内部类。
- UML 属性类型与源码类型表达不同但语义可能一致。
- 组件边界存在间接依赖。
- 时序图缺少非关键的返回消息。
- 测试覆盖不足但没有直接违反行为契约。

### 6.3 信息规则（`info`）

用于追踪而非判错：

- 布局、坐标或图形样式变化。
- 设计文件拆分或合并但实体映射保持不变。
- 文档、注释和命名风格变化。

规则必须返回 `rule_id`、`severity`、`status`、来源位置、对比对象和证据摘要。

## 7. 变更影响分析

一致性流程应先计算影响范围，再决定需要执行哪些规则：

```text
设计变更
  → 受影响 DesignFile/Diagram/Entity
  → 受影响 Component/SourceModule
  → 受影响 Interface/Method/Relation
  → 受影响 TestCase
  → 选择性执行 blocker/warning 规则
```

实现变更也采用同样流程：

```text
源码 diff
  → AST/API/依赖变化
  → 反向查找 UML 实体
  → 标记设计漂移或需要评审的设计变更
```

只修改实现细节时，不应强制修改 UML；改变公开接口、模块边界、数据模型或跨模块交互时，必须进入设计评审流程。

## 8. 统一检查结果格式

后续规则引擎的最小结果契约如下：

```json
{
  "project_id": "trade_sys",
  "scope": "project",
  "status": "pass|warn|fail|blocked",
  "rules": [
    {
      "rule_id": "method.signature",
      "severity": "blocker",
      "status": "pass|warn|fail|skipped",
      "design": {"file": "design/domain.umlproj", "entity_id": "class_order"},
      "source": {"file": "src/order/service.py", "symbol": "OrderService.create"},
      "tests": ["test/test_order.py::test_create"],
      "evidence": "...",
      "impact": ["OrderService.create", "test_create"]
    }
  ]
}
```

`fail` 表示规则违反，`blocked` 表示无法完成判断（例如设计文件不存在或资源状态不确定），二者不能混用。

## 9. 生命周期门禁

设计影响型任务的标准流程为：

```text
读取当前契约
  → 生成设计 Diff
  → 计算影响范围
  → 人工设计评审
  → 实施源码/设计变更
  → 执行受影响规则
  → 运行行为测试
  → 记录 Trace、证据和契约结果
```

规则引擎不得直接修改测试来消除失败；测试变更必须是用户明确要求的独立变更，并记录原因。

## 10. 第一阶段验收标准

本阶段完成的标志：

- 新项目不依赖固定设计文件名即可描述契约范围。
- 单设计文件和多设计文件项目使用同一模型。
- 能明确区分 `design_vs_source`、`source_vs_test` 和 `behavior` 冲突。
- 每条规则都能返回来源和证据，而不是只有布尔值。
- 阻断、警告、信息三类结果语义固定。
- 后续 Checker 可以读取本契约文档定义的实体和结果格式，而不再复制项目专用判断逻辑。

本文件只定义契约语义；具体 AST、UML 解析和测试执行实现属于下一阶段的规则引擎设计。

## 11. 知识图谱复用边界

设计契约插件可以复用知识图谱已经构建的节点和关系，尤其是 `implements`、`tests` 等跨设计/源码/测试关系。知识图谱通过 `KnowledgeGraphProvider.contract_facts` 提供有界、只读的中立事实；契约插件通过适配器将其转换为 `ContractMapping`。

知识图谱只作为可追溯的关系证据和影响分析索引，不改变 `ContractSnapshot` 的权威性，也不在契约采集过程中重建或修改图谱。图谱不可用、过期或由旧版插件提供时，契约插件继续使用本地 UML/AST 解析并记录图谱不可用原因。

解析层统一锚点为 `backend/app/agent_base/core/contracts.py` 中的 `ArtifactFacts`。`DesignContractProvider.collect_facts()` 负责产生事实，`collect()` 再将事实投影为 `ContractSnapshot`；知识图谱可通过 `KnowledgeGraphProvider.index_facts()` 增量消费该事实模型，逐步移除重复解析。

统一编排入口为 `app.agent_base.core.contract_pipeline.assemble_contract()`：一次收集事实，生成契约快照，并按需执行图谱投影；图谱优先调用 `KnowledgeGraphProvider.sync_facts()`，根据 artifact 指纹只同步新增、修改和删除的文件；旧版契约或图谱插件仍可通过兼容回退路径运行。

## 12. Harness 契约闸门

契约校验不依赖模型提示词，而由 `app.agent_base.core.contract_harness.ContractHarness` 独立执行。Agent 的文件变更批次完成后，执行层对候选工作区进行只读契约检查，并通过 WebSocket 推送 `contract_check` 事件：

```text
apply_changes → ContractHarness.check(index_graph=False)
              → PASS：继续提交
              → WARN：复用审核通道请求用户确认
              → BLOCK/INCONCLUSIVE：回滚变更并返回部分完成
```

只有通过校验或用户确认警告后，才提交 `ChangeSet`，随后使用 `index_graph=True` 将已接受事实增量同步到正式知识图谱。校验结果包含 `check_id`、状态、变更文件、违规项和图谱状态，前端可展示摘要及详情。

当前 Harness 提供基础的设计类实现、方法存在性、测试覆盖和解析诊断检查；复杂项目可通过替换或扩展校验器增加更严格的策略，而不改变 Agent 主循环。

`agent_execution` 只依赖 `ContractGatePort` 的 `evaluate/finalize` 决策接口。默认实现由
`load_contract_gate()` 注入到 Agent；替换校验引擎、审核策略或图谱后端时，不需要修改执行主流程。

执行状态投影中，Task 的顶层 `status` 继续表示看板状态（只有显式完成任务才变为
`completed`）；本次运行的终态以 `result_status` 和 `execution.status` 为准。为避免
消费者混淆，`execution.terminal` 表示本次运行是否已结束，`execution.resume_available`
表示是否存在可继续的检查点。

## 13. 阻断后的失败分析

当门禁返回 `block` 时，执行层只负责回滚并调用注入的
`ContractFailureAnalyzerPort.analyze()`。分析器将结构化门禁结果以内部
`summary` 消息追加到原有交互历史，然后发起一次只读的模型调用，生成失败分析笔记。

```text
ContractGate(block)
  → rollback
  → ContractFailureAnalyzer.analyze(result)
  → append internal summary
  → model call (same tool schema, tool_choice=auto, read-only analyzer)
  → return failure analysis note
```

门禁状态、是否允许提交和回滚结果始终以 Harness 为准，模型只能解释事实，不能修改门禁结论或执行修复。前端的 `contract_check` 阻断事件只显示固定提示
“设计契约校验阻止提交”，详细原因由模型分析笔记呈现。分析请求沿用正常交互的完整工具 schema 与
`tool_choice=auto`，避免因为请求配置差异降低提供商的前缀复用机会。分析器是一次性只读调用，不进入工具执行循环；即使模型返回
`tool_calls`，也只记录并忽略，不会修改文件或重试任务。分析器可替换而无需改动 Agent 主流程。

## 14. 交互式契约开关

AI 助手聊天框提供“设计契约”开关。开关状态作为当前聊天请求的
`design_contract_enabled` 字段发送，仅影响该次运行，不修改进程级 Settings。
服务端配置是上限：服务端关闭时始终关闭；`strict_production=true` 时忽略客户端的关闭请求。
生效值会写入 run metadata、checkpoint 和 `contract_policy` trace 事件，便于审计与复现。
开关在任务执行期间不可用，切换只对下一次运行生效。
