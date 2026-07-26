# UML 跨图联合设计指南 (Cross-Diagram Design Guide)

> 面向 LLM 的跨图一致性设计指南。当同时生成或优化多张 UML 图时，严格遵循本文档确保跨图引用完整性。

---

## 1. 跨图引用字段规范

### 1.1 `component_id` — 图 → 组件归属

每个 `UmlDiagram` 都有一个 `component_id` 字段，指向组件图中某个 `CompNode.id`：

| 图类型 | `component_id` 语义 | 示例 |
|--------|-------------------|------|
| 类图 | 该图描述的类属于哪个组件 | `"comp_auth"` → 该图为 `AuthService` 组件设计内部类结构 |
| 时序图 | 该图描述的交互场景属于哪个组件 | `"comp_ota"` → 该图为 `OtaTask` 组件描述交互流程 |
| 组件图 | 通常为空（组件图是顶级架构视图） | `""` |

**规则**：
- **必须先创建组件图**，再创建类图和时序图。类图和时序图的 `component_id` 引用已创建的组件 ID。
- **同一组件可关联多张类图和时序图**，只要 `component_id` 指向同一个 `CompNode.id`。
- 生成新图时，如果描述了某个组件的内部结构或交互，**必须设置 `component_id`**；如果是全局视图（不属于特定组件），设为 `""`。
- 在 `diagrams` 数组中，`component_id` 出现在顶层（与 `type`、`name` 同级），而不是 `data` 内部。

### 1.2 `class_ref` — 生命线 → 类归属

每个 `SeqLifeline` 都有一个 `class_ref` 字段，指向类图中某个 `UmlClass.id`：

```
时序图:  lifeline "Client"  ──class_ref──►  类图: class "User"
```

**规则**：
- 时序图的生命线代表交互的参与者。如果该参与者对应类图中已定义的类，**必须设置 `class_ref`** 指向该类的 `id`。
- 如果参与者是外部系统或尚未在类图中定义，`class_ref` 可为空字符串 `""`。
- **生成顺序**：先生成类图（定义所有类结构），再生成时序图。时序图中的 `class_ref` 引用已生成的类 ID。
- 交叉校验时，检查每个非空 `class_ref` 是否能解析到类图中的实际类 ID。

### 1.3 接口一致性 — `provided_interfaces` / `required_interfaces`

类和组件都可以声明接口：

| 元素 | 字段 | 含义 |
|------|------|------|
| `UmlClass` | `provided_interfaces` | 该类对外提供的能力（棒棒糖 ◉） |
| `UmlClass` | `required_interfaces` | 该类依赖的外部能力（插座 ◡） |
| `CompNode` | `provided_interfaces` | 该组件对外暴露的接口 |
| `CompNode` | `required_interfaces` | 该组件所需的外部接口 |

**规则**：
- 组件 A 依赖组件 B（`CompRelation: A → B`）时，A 的 `required_interfaces` 应匹配 B 的 `provided_interfaces`。
- 类图的 `provided_interfaces` 应与其所属组件的 `provided_interfaces` 保持一致（类的接口是组件接口的具体实现）。
- 如果没有组件图，类图之间的接口匹配通过 `UmlRelation` 已经足够。

---

## 2. 三图一致性检查清单

当同时生成或优化多张图时，LLM 应逐条检查以下项目，发现问题记录到 `consistency_report`。

### 2.1 类图 ↔ 时序图一致性

- [ ] 时序图中每个有 `class_ref` 的生命线，其引用的类 ID 在类图中存在
- [ ] 时序图中每条消息的方法名（`label` 冒号前的部分）在目标生命线对应类的方法列表中存在
- [ ] 如果时序图描述了某个场景（如"用户登录"），类图中应该有对应的类和方法支持该场景
- [ ] 时序图的 `component_id`（如设置）对应的组件与类图的 `component_id` 一致（同属一个组件）

### 2.2 类图 ↔ 组件图一致性

- [ ] 类图的 `component_id` 指向的组件在组件图中存在
- [ ] 组件声明的 `provided_interfaces` 在关联的类图中能找到对应的实现类
- [ ] 组件声明的 `required_interfaces` 在关联的类图中能找到对应的依赖类
- [ ] `CompRelation` 两端的接口一致性：source 的 `required` 匹配 target 的 `provided`

### 2.3 时序图 ↔ 组件图一致性

- [ ] 时序图的 `component_id` 指向的组件在组件图中存在
- [ ] 时序图中的交互跨组件时，两个组件之间应有 `CompRelation`（或通过其他组件间接可达）
- [ ] 组件依赖线（`CompRelation`）的方向与时序图消息传递方向逻辑一致

---

## 3. 典型跨图设计模式

### 3.1 三层架构

```
组件图:  [Presentation] → [Application] → [Domain] → [Infrastructure]
类图:    每个组件各有 1+ 张类图（component_id 指向对应组件）
时序图:  每个用例有 1 张时序图（跨越多个组件的交互）
```

`diagrams` 输出顺序：
1. `type: "component"` — 先定义架构骨架和组件 ID
2. `type: "class"` — 每个组件的类图，`component_id` 引用组件 ID
3. `type: "sequence"` — 每个用例的时序图，`component_id` 引用主要组件 ID

### 3.2 微服务架构

```
组件图:  [API Gateway] → [User Service] → [Database]
                           ↓
                      [Message Queue] → [Notification Service]
类图:   每个微服务有 1 张类图
时序图:  跨服务场景（如"用户注册并发送欢迎邮件"）有 1 张时序图
```

### 3.3 事件驱动架构

```
组件图:  [Publisher] → [Event Bus] → [Subscriber A]
                                   → [Subscriber B]
时序图:  Publisher 发出事件（async 消息），Subscriber 异步处理
类图:    事件定义类 + Publisher/Subscriber 的类结构
```

---

## 4. 常见跨图错误与修正

### 4.1 ❌ 生命线引用了不存在的类

```json
// 时序图
{ "id": "life_1", "name": "User", "class_ref": "class_xyz" }
// 类图中没有 class_xyz
```

**修正**：检查类图中是否有语义匹配的类（如名为 "User" 的类），修正 `class_ref` 为正确的 ID。

### 4.2 ❌ 消息方法签名与类图不匹配

```json
// 时序图消息
{ "label": "authenticate(token)", "to_lifeline": "life_auth" }
// 类图中 life_auth.class_ref 指向的类没有 authenticate 方法
```

**修正**：在对应类中添加 `authenticate(token: string): bool` 方法，或修改消息 `label` 匹配已有方法。

### 4.3 ❌ 组件接口与类接口不一致

```json
// 组件图：AuthService.provided_interfaces: ["IAuth"]
// 类图（component_id="comp_auth"）：没有类提供 IAuth 接口
```

**修正**：在类图的某个类（如 `AuthManager`）上设置 `provided_interfaces: ["IAuth"]`。

### 4.4 ❌ 依赖关系链断裂

```json
// 时序图中 A→B→C 有消息传递
// 组件图中只有 CompRelation: A→B，缺少 B→C
```

**修正**：在组件图中补充 `CompRelation: B → C`。

### 4.5 ❌ 孤心生命线（无 class_ref）

```json
// 时序图生命线 class_ref: ""
// 但类图中明确存在匹配的类
```

**修正**：设置 `class_ref` 为匹配类的 ID。

### 4.6 ❌ component_id 指向不存在的组件

```json
// 类图 component_id: "comp_ghost"
// 组件图中没有 id="comp_ghost" 的组件
```

**修正**：在组件图中创建对应组件，或清空 `component_id`。

---

## 5. `diagrams` 数组输出规范

### 5.1 生成顺序

**严格按此顺序**生成 `diagrams` 数组中的条目：

1. **组件图**（`type: "component"`）— 先定义所有组件的 ID 和接口
2. **类图**（`type: "class"`）— 引用已定义的组件 ID 作为 `component_id`
3. **时序图**（`type: "sequence"`）— 引用已定义的组件 ID 和类 ID

### 5.2 `component_id` 交叉引用规则

```
组件图 entry:
  {"type": "component", "name": "Architecture", "component_id": "",
   "data": {"components": [{"id": "comp_auth", ...}, {"id": "comp_ota", ...}]}}

类图 entry:
  {"type": "class", "name": "Auth Domain", "component_id": "comp_auth",
   "data": {"classes": [{"id": "class_user", ...}, ...]}}

时序图 entry:
  {"type": "sequence", "name": "Login Flow", "component_id": "comp_auth",
   "data": {"lifelines": [{"id": "life_client", "class_ref": "class_user", ...}], ...}}
```

### 5.3 同类型多图命名规范

- 组件图：`"System Architecture"`、`"Infrastructure Layer"`
- 类图：`"Domain Model"`、`"Auth Module"`、`"Payment Module"`
- 时序图：`"Happy Path"`、`"Error Handling"`、`"OTA Upgrade Flow"`
- 命名应简洁且反映其设计范围，避免模糊名称如 `"Diagram 1"`、`"My Diagram"`

---

## 6. 自动校验信息

当收到 Cross-Diagram Reference Index 时，请注意其中的 "Issues Detected" 部分。
这些是程序自动检测到的问题，你应在优化输出中修复它们：

- **孤心生命线**：为其设置正确的 `class_ref`
- **未关联组件的图**：根据图的内容设置合适的 `component_id`
- **引用不存在的 ID**：修正为实际存在的 ID
