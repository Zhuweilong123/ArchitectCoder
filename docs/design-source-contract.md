# 设计—源码一致性契约

> 状态：4.0 规划基线（第一阶段）
> 目的：定义设计文件、源码和测试之间的权威关系与可复用校验语义。
> 范围：项目工作区、UML 设计集合、源码目录、测试目录，以及后续一致性规则引擎。

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
