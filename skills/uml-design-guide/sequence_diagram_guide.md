# UML 时序图设计指导 (Sequence Diagram Design Guide)

> 面向 LLM 的时序图设计生成指南。遵循本文档的全部规范，生成的 JSON 可被 UML Designer 工具直接加载。

---

## 1. 数据模型参考

### 1.1 SeqLifeline（生命线）

```json
{
  "id": "life_<timestamp>_<random6>",    // 唯一标识
  "name": "Participant",                  // 生命线名称（如 "User", "OrderService"）
  "class_ref": "",                        // 可选：关联的 UML 类图 ID
  "x": 300.0,                             // 画布 X 坐标（Y 固定为 120）
  "activations": []                       // 激活条 Y 偏移量数组，如 [150, 280]
}
```

### 1.2 SeqMessage（消息）

```json
{
  "id": "msg_<timestamp>_<random6>",     // 唯一标识
  "from_lifeline": "<源生命线ID>",       // 发送方生命线 ID
  "to_lifeline": "<目标生命线ID>",       // 接收方生命线 ID
  "label": "methodName()",               // 消息标签 / 方法名
  "type": "sync",                        // 消息类型，见 §2.1
  "order": 1,                            // 垂直顺序号（从上到下递增）
  "y": 190.0,                            // 垂直 Y 位置（持久化）
  "note": ""                             // 功能备注（业务语义描述）
}
```

### 1.3 SeqFragment（组合片段 — UML 2.5.1）

```json
{
  "id": "frag_<timestamp>_<random6>",   // 唯一标识
  "type": "loop",                        // 片段类型，见 §2.2
  "label": "[for each item]",            // 守卫条件 / 标签
  "x": 80.0,                             // 片段左边界 X
  "width": 600.0,                        // 片段宽度（覆盖范围）
  "y_start": 200.0,                      // 片段顶部 Y
  "y_end": 380.0                         // 片段底部 Y
}
```

### 1.4 完整时序图 (UmlDiagram)

```json
{
  "version": "1.0",
  "name": "SequenceDiagramName",
  "diagram_type": "sequence",
  "classes": [],
  "relations": [],
  "lifelines": [ ... ],
  "messages": [ ... ],
  "fragments": [ ... ],
  "components": [],
  "comp_relations": [],
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

### 2.1 MessageType（消息类型）

| 值 | 含义 | 视觉样式 | 适用场景 |
|----|------|---------|---------|
| `sync` | 同步消息 | 蓝色(#1890ff)实线 + 实心三角箭头 | 同步调用，等待返回 |
| `async` | 异步消息 | 绿色(#52c41a)实线 + 实心三角箭头 | 异步调用，不等待 |
| `return` | 返回消息 | 灰色(#888)虚线 | 同步调用的返回 |
| `simple` | 简单消息 | 灰色(#333)实线 + 实心三角箭头 | 简单通知/信号 |
| `self` | 自反消息 | 蓝色(#1890ff)实线 + 弯曲回路箭头 | 对象自身调用 |

**重要约束：**
- `sync` 类型的消息通常后面跟着一个对应的 `return` 消息
- `self` 消息的 `from_lifeline` 和 `to_lifeline` 必须是同一 ID
- 消息的箭头方向自动由生命线位置决定：源在左→箭头向右，源在右→箭头向左

### 2.2 FragmentType（组合片段类型）

| 值 | 含义 | 视觉样式 | 典型用法 |
|----|------|---------|---------|
| `loop` | 循环 | 蓝色(#1890ff)边框 | `[for each item]` 遍历 |
| `alt` | 条件分支 | 紫色(#722ed1)边框 | `[if x > 0]` / `[else]` |
| `opt` | 可选执行 | 灰色虚线边框(#555) | `[optional]` 可选操作 |
| `break` | 中断退出 | 灰色边框 | `[异常条件]` 跳出 |
| `par` | 并行执行 | 灰色边框 | 并发操作 |
| `critical` | 临界区 | 灰色边框 | 原子操作区域 |
| `neg` | 否定/无效 | 灰色边框 | 不应发生的场景 |

---

## 3. 消息设计指导

### 3.1 消息顺序 (order)

- `order` 字段控制消息从上到下的垂直排列
- 从 1 开始递增
- 同一 `order` 值的消息水平位置由生命线 X 坐标决定
- 默认 Y 位置计算公式：`LIFELINE_Y(120) + 30 + order * 40`
- 如果指定了 `y` 值，则使用指定的 Y 值

### 3.2 消息标签 (label) 规范

消息标签应清晰表达交互内容：
- 方法调用：`"login(username, password)"`
- 数据返回：`"return userInfo"` 或 `"message()"`
- 事件通知：`"orderCreated"`、`"onDataReady()"`
- 自反消息：`"validate()`、`"processData()"`

### 3.3 消息备注 (note) 的使用

`note` 字段是**功能备注**，记录此消息的**业务含义和交互目的**。这是时序图的核心价值所在——LLM 会根据 note 生成方法体内的具体业务逻辑。

**好的 note 示例：**
- `"通知鸡叫进程OTA的请求和预计OTA时间"`
- `"查询用户信息，如果不存在则抛出异常"`
- `"计算订单金额，包含折扣和税费"`
- `"处理OTA场景：如果鸡叫时间可能在OTA升级期间，取消鸡叫任务"`

### 3.4 消息类型选择指南

```
场景                            推荐类型
─────────────────────────────────────────
方法调用，需要等待返回          sync
方法调用，不需要等待返回         async  
同步调用的返回值                return
简单通知/信号，无返回值         simple
对象自身的方法调用              self
```

### 3.5 同步调用模式（经典请求-响应）

```
lifeA ──sync(msg1)──► lifeB    "getOrder(id)"
lifeB ──return──► lifeA        "return orderData"
```

### 3.6 自反消息 (self)

```
lifeA ──self──► lifeA   "validate()"
```

自反消息在生命线右侧以弯曲回路形式渲染，用于表示对象自身的内部调用。

---

## 4. 生命线设计指导

### 4.1 生命线命名

- 命名应反映参与者角色：`User`、`OrderService`、`Database`
- 可使用 `角色: 类型` 格式：`"ota: OtaTask"`、`"scheduler: TaskScheduler"`
- 与类图中的类对应时，设置 `class_ref` 为对应的类 ID

### 4.2 生命线布局

- 生命线宽度固定为 140px，高度根据消息数量自动扩展
- Y 坐标固定为 120（头部位置）
- X 坐标建议间隔 ≥ 180px，避免重叠
- 参与者数量建议 2~8 个，过多难以阅读
- 从左到右排列：外部参与者 → 控制器 → 业务服务 → 数据层

### 4.3 激活条 (activations)

- 激活条是一个 Y 偏移量的数组，表示生命线上执行状态的时间段
- 在创建消息时会自动添加，不需要手动指定
- 删除消息不会自动移除激活条

### 4.4 生命周期约束

- 删除某生命线时，所有引用该生命线的消息都会自动删除
- 复制粘贴生命线时，复制包括名称、关联类、激活条

---

## 5. 组合片段设计指导

### 5.1 片段布局

- `x`：片段左边界，建议比最左生命线的 X 值小约 20px
- `width`：片段宽度，应覆盖所有涉及的生命线
- `y_start` 和 `y_end`：片段的上下边界，应包裹相关消息
- 片段高度 = `y_end - y_start`

### 5.2 片段类型选择

| 设计意图 | 使用片段 | label 示例 |
|---------|---------|-----------|
| 遍历集合中每个元素 | `loop` | `[for each order]` |
| if-else 分支 | `alt` | `[x > 0]` / `[else]` |
| 条件可选执行 | `opt` | `[if user is logged in]` |
| 异常/错误退出 | `break` | `[timeout]` |
| 并发操作 | `par` | (无 label 或说明块) |
| 事务/原子操作 | `critical` | `[transaction]` |
| 错误场景/不应发生 | `neg` | `[invalid state]` |

### 5.3 片段嵌套

- 片段可以嵌套（外层片段 y_start/y_end 包含内层）
- 嵌套层级建议 ≤ 2 层，避免过于复杂

---

## 6. 设计原则与最佳实践

### 6.1 时序图设计流程

1. **确定场景**：明确要描述的交互过程（如 "用户登录流程"、"OTA 升级流程"）
2. **识别参与者**：列出所有参与交互的对象/角色
3. **放置生命线**：从左到右排列：触发者→控制器→服务→数据层
4. **添加消息**：按时间顺序从上到下添加消息
5. **标记消息**：为每条消息添加清晰的标签和业务备注
6. **添加片段**：用组合片段包装条件/循环/并发逻辑
7. **添加返回消息**：同步调用后添加 return 消息

### 6.2 ID 生成规则

- 生命线 ID：`life_<timestamp>_<random6>`
- 消息 ID：`msg_<timestamp>_<random6>`
- 片段 ID：`frag_<timestamp>_<random6>`
- 每个 ID 必须全局唯一

### 6.3 常见错误

1. **order 不连续** — order 应从 1 开始连续递增
2. **消息 Y 与 order 不一致** — 默认 Y = 150 + order × 40
3. **缺少 return 消息** — 每个 sync 调用应有对应的 return
4. **self 消息的 from ≠ to** — 自反消息两端必须相同
5. **片段边界不含消息** — y_start/y_end 必须包裹相关消息
6. **片段类型拼写错误** — 只能用 7 个枚举值

---

## 7. LLM 输出规范

### 7.1 JSON 字段名严格对照

| 层级 | 字段 | 注意 |
|------|------|------|
| 生命线 | `id`, `name`, `class_ref`, `x`, `activations` | `activations` 是浮点数数组 |
| 消息 | `id`, `from_lifeline`, `to_lifeline`, `label`, `type`, `order`, `y`, `note` | `order` 从 1 开始递增 |
| 片段 | `id`, `type`, `label`, `x`, `width`, `y_start`, `y_end` | `y_end` > `y_start` |

### 7.2 关键约束检查清单

- [ ] 每个 `id` 全局唯一
- [ ] 消息的 `from_lifeline` 和 `to_lifeline` 必须引用已存在的生命线 ID
- [ ] `order` 从 1 开始连续递增（无跳号）
- [ ] `y` 值大约为 `150 + order × 40`（不强制但推荐）
- [ ] `sync` 消息通常有对应的 `return` 消息
- [ ] `self` 消息的 from/to 必须是同一个生命线
- [ ] 片段的 `y_start` < `y_end`，且包裹相关消息
- [ ] 片段 `type` 必须为 7 个枚举值之一
- [ ] 消息 `type` 必须为 5 个枚举值之一
- [ ] `note` 字段不应为空——这是业务逻辑的核心描述
- [ ] ⚠️ **坐标不能清零**：PRESERVE 所有 x/y/width/height 字段，NEVER zero them out
- [ ] ⚠️ **class_ref 必须有效**：指向 class diagram 中存在的类 ID
- [ ] ⚠️ **消息 label 必须匹配**：方法调用 label 应与目标类的实际方法签名一致

### 7.3 时序图优化检查清单

当被要求优化时序图时，从以下维度评估：
1. **消息完整性**：是否遗漏了必要的交互步骤？
2. **调用顺序**：时间顺序是否合理？是否有死锁或循环依赖？
3. **消息命名**：标签是否清晰表达含义？
4. **备注质量**：note 是否充分描述了业务逻辑？
5. **片段使用**：条件/循环/异常分支是否用组合片段表达？
6. **参与者合理性**：生命线数量是否合适？职责是否清晰？
7. **返回消息**：同步调用是否都有返回消息？
