# UML 组件图设计指导 (Component Diagram Design Guide)

> 面向 LLM 的组件图设计生成指南。遵循本文档的全部规范，生成的 JSON 可被 ArchitectCoder 工具直接加载。

---

## 1. 数据模型参考

### 1.1 CompNode（组件节点）

```json
{
  "id": "comp_<timestamp>_<random6>",   // 唯一标识
  "name": "ComponentName",               // 组件名称
  "x": 100.0,                            // 画布 X 坐标
  "y": 100.0,                            // 画布 Y 坐标
  "width": 200.0,                        // 宽度（顶层200，子组件150）
  "height": 160.0,                       // 高度（顶层160，子组件100）
  "parent_id": "",                       // 父组件ID：空=顶层，非空=子组件
  "provided_interfaces": [],             // 提供的接口（UML 棒棒糖 ⊃）
  "required_interfaces": []              // 依赖的接口（UML 插座 ⊂）
}
```

### 1.2 CompRelation（组件依赖关系）

```json
{
  "id": "crel_<timestamp>_<random6>",   // 唯一标识
  "source": "<源 CompNode.id>",         // 源组件 ID（依赖方）
  "target": "<目标 CompNode.id>",       // 目标组件 ID（被依赖方）
  "type": "dependency"                   // 关系类型："dependency" | "delegation"
}
```

### 1.3 完整组件图 (UmlDiagram)

```json
{
  "version": "1.0",
  "name": "ComponentDiagramName",
  "diagram_type": "component",
  "classes": [],
  "relations": [],
  "lifelines": [],
  "messages": [],
  "fragments": [],
  "components": [ ... ],
  "comp_relations": [ ... ],
  "grid_visible": true,
  "grid_size": 20,
  "grid_color": "#e0e0e0",
  "grid_thickness": 1,
  "snap_to_grid": true,
  "zoom": 1.0,
  "pan_x": 0.0,
  "pan_y": 0.0
}
```

---

## 2. 枚举值完整列表

### 2.1 CompRelation Type（组件关系类型）

| 值 | 含义 | 视觉样式 | 适用场景 |
|----|------|---------|---------|
| `dependency` | 依赖关系 | 橙色(#d48806)虚线 + 实心箭头 | A 依赖 B 提供的接口 |
| `delegation` | 委托关系 | 橙色(#d48806)虚线 + 实心箭头 | A 将实现委托给 B |

**说明：** 当前组件图的关系类型只有这两种。`dependency` 是最常见的组件关系，表示一个组件需要另一个组件提供的功能。所有组件关系都使用橙色虚线渲染（`strokeDasharray: "6,4"`）。

---

## 3. 组件设计指导

### 3.1 组件命名与语义

- 组件表示系统的**模块、子系统或部署单元**
- 命名使用大写开头的名词：`AuthService`、`Database`、`MessageQueue`
- 顶层组件自动显示 `«component»` 构造型标记
- 子组件**不显示**构造型标记，以普通矩形显示在父组件内部

### 3.2 组件层次结构 (parent_id)

组件图支持**两层嵌套**：

```
顶层组件（parent_id = ""）
  └── 子组件（parent_id = "父组件ID"）
```

规则：
- `parent_id: ""` — 顶层组件，独立的矩形节点
- `parent_id: "<comp_id>"` — 子组件，嵌入在父组件内部
- 子组件在父组件内部定位（x, y 相对于父组件）
- 删除父组件时，子组件不会自动删除（需手动处理）

**使用建议：**
- 顶层组件代表独立的子系统/模块
- 子组件代表该模块内部的关键组成
- 嵌套层级仅支持一层（子组件不能再包含子组件）

### 3.3 接口设计 (provided_interfaces / required_interfaces)

接口是组件图的**核心概念**，表示组件之间的契约：

**提供的接口 (provided_interfaces)：**
- 该组件对外暴露的能力
- 在组件内以 `⊃ InterfaceName` 显示（绿色，棒棒糖 lollipop 符号）
- 示例：`["HttpApi", "WebSocketEndpoint"]`

**依赖的接口 (required_interfaces)：**
- 该组件正常运行所需的外部能力
- 在组件内以 `⊂ InterfaceName` 显示（红色，插座 socket 符号）
- 示例：`["Database", "MessageQueue"]`

**接口命名建议：**
- 使用 PascalCase：`IUserService`、`IDatabase`、`HttpEndpoint`
- 可以使用方法签名形式：`"CloudSendOtaRequestToTbox()"`
- 接口名称应反映**能力/契约**而非实现细节

**接口匹配规则：**
- A 的 `required_interfaces` 应该能匹配 B 的 `provided_interfaces`
- 组件依赖关系 (CompRelation) 的方向是 A(source) → B(target)，表示 A 依赖 B

---

## 4. 组件关系设计指导

### 4.1 依赖关系 (dependency)

```
[ComponentA] ──dependency──► [ComponentB]
    依赖方                      被依赖方
```

A 的 `required_interfaces` 与 B 的 `provided_interfaces` 匹配。

**示例：**
```json
// ComponentA 需要 DatabaseClient 接口
// ComponentB 提供 DatabaseClient 接口
{
  "components": [
    { "id": "comp_a", "name": "AuthService",
      "provided_interfaces": ["AuthApi"],
      "required_interfaces": ["DatabaseClient"] },
    { "id": "comp_b", "name": "Database",
      "provided_interfaces": ["DatabaseClient"],
      "required_interfaces": [] }
  ],
  "comp_relations": [
    { "id": "crel_1", "source": "comp_a", "target": "comp_b", "type": "dependency" }
  ]
}
```

### 4.2 委托关系 (delegation)

委托表示父组件将接口的实现委托给内部子组件：
```
[ParentComponent] ──delegation──► [ChildComponent]
```

### 4.3 布局建议

- 组件图从左到右排列：外部系统 → 中间件 → 核心业务 → 数据层
- 顶层组件间距 ≥ 60px
- 父子关系用位置嵌套 + parent_id 表达
- 数据流方向建议从左到右

---

## 5. 设计原则与最佳实践

### 5.1 组件划分原则

| 原则 | 说明 |
|------|------|
| 高内聚 | 组件内部功能紧密相关 |
| 低耦合 | 组件之间通过接口松耦合 |
| 单一职责 | 每个组件负责一个明确的业务领域 |
| 接口隔离 | 每个接口粒度适中，不过大过小 |
| 依赖倒置 | 依赖接口而非具体实现 |

### 5.2 组件图设计流程

1. **识别系统边界**：确定系统的顶层模块
2. **定义组件**：为每个模块创建顶层组件
3. **识别子组件**：对复杂模块，添加关键子组件
4. **定义接口**：为每个组件明确 provided 和 required 接口
5. **建立关系**：用 dependency/delegation 连接组件
6. **验证接口匹配**：required 应该能在某处被 provided

### 5.3 常见模式

**分层架构：**
```
[Presentation] → [Application] → [Domain] → [Infrastructure]
```

**微服务架构：**
```
[API Gateway] → [Service A] → [Service B]
                   ↓              ↓
              [Cache]       [MessageQueue]
```

**组件嵌套模式：**
```
[MDC 顶层组件]
  ├── [OtaTask 子组件]
  ├── [CrowTask 子组件]
  ├── [TaskScheduler 子组件]
  └── [MM_APP 子组件]
```

### 5.4 与类图/时序图的协调

组件图通常与类图和时序图**联合使用**：
- **类图**定义每个组件内部的类结构
- **时序图**定义组件之间的交互流程
- **组件图**定义系统的模块划分和接口契约
- 在代码生成时，组件图决定模块/包结构，类图决定类结构，时序图决定方法实现

### 5.5 ID 生成规则

- 组件 ID：`comp_<timestamp>_<random6>` 或语义 ID：`comp_cloud`、`comp_tbox`
- 关系 ID：`crel_<timestamp>_<random6>`
- 每个 ID 全局唯一

---

## 6. LLM 输出规范

### 6.1 JSON 字段名严格对照

| 层级 | 字段 | 注意 |
|------|------|------|
| 组件 | `id`, `name`, `x`, `y`, `width`, `height`, `parent_id`, `provided_interfaces`, `required_interfaces` | 接口是字符串数组 |
| 关系 | `id`, `source`, `target`, `type` | source/target 是组件 ID |

### 6.2 尺寸默认值

| 组件类型 | width | height |
|---------|-------|--------|
| 顶层组件 | 200 | 160 |
| 子组件 | 150 | 100 |

### 6.3 关键约束检查清单

- [ ] 每个 `id` 全局唯一
- [ ] `comp_relations` 中的 `source` 和 `target` 必须引用已存在的组件 ID
- [ ] `parent_id` 为空字符串 `""` 或引用已存在的组件 ID
- [ ] 接口列表 (`provided_interfaces`, `required_interfaces`) 是字符串数组
- [ ] 关系 `type` 必须是 `"dependency"` 或 `"delegation"`
- [ ] 子组件的坐标相对于父组件
- [ ] `provided_interfaces` 与 `required_interfaces` 在依赖关系两端应能匹配
- [ ] 空数组不能省略：`"provided_interfaces": []` 而不是不写
- [ ] ⚠️ **坐标不能清零**：PRESERVE 所有 x/y/width/height 字段，NEVER zero them out
- [ ] ⚠️ **接口一致性**：组件的 provided/required 接口必须与类图中的接口定义保持一致

### 6.4 组件图优化检查清单

当被要求优化组件图时，从以下维度评估：
1. **组件职责**：每个组件的职责是否单一明确？
2. **依赖关系**：依赖方向是否合理？是否遵循依赖倒置原则？
3. **接口设计**：接口粒度是否适中？命名是否清晰？
4. **父子嵌套**：子组件的划分是否合理？是否真正属于父组件？
5. **架构模式**：是否符合目标架构模式（分层/微服务/事件驱动）？
6. **循环依赖**：是否存在组件间的循环依赖？
7. **接口匹配**：required 接口是否都能在依赖链路上找到对应的 provided？
