# UML 时序图设计指导 (Sequence Diagram Design Guide)

> 面向 LLM 的时序图设计生成指南。遵循本文档的全部规范，生成的 JSON 可被 ArchitectCoder 工具直接加载。

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
  "y_end": 380.0,                        // 片段底部 Y
  "lifeline_ids": ["life_a", "life_b"],   // 片段覆盖的生命线 ID
  "parent_fragment_id": "",              // 嵌套时指定外层片段
  "parent_operand_id": "",               // 嵌套时指定外层分支
  "operands": [
    {
      "id": "operand_items",
      "guard": "[for each item]",
      "message_ids": ["msg_process"],     // 本分支直接拥有的消息 ID
      "y_start": 224.0,
      "y_end": 372.0
    }
  ]
}
```

### 1.4 完整时序图 (UmlDiagram)

```json
{
  "version": "1.0",
  "name": "SequenceDiagramName",
  "diagram_type": "sequence",
  "component_id": "",
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
| `async` | 异步消息 | 绿色实线 + 开放箭头 | 异步调用，不等待 |
| `return` | 返回消息 | 灰色(#888)虚线 | 同步调用的返回 |
| `simple` | 简单消息 | 灰色(#333)实线 + 实心三角箭头 | 简单通知/信号 |
| `self` | 自反消息 | 蓝色(#1890ff)实线 + 弯曲回路箭头 | 对象自身调用 |

**重要约束：**
- `sync` 表示调用方等待被调用方完成；是否显式画 `return` 取决于抽象层级。业务流程图可以省略无信息量的返回，接口级时序图应画出有意义的返回值或异常
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

操作符按控制流选择，不能按框的外观选择。UML 标准语义与本系统的生成约定分别如下：

- `alt`：从守卫为真的备选分支中选择至多一个；不是“else 特殊分支”的名称。else 可选，表示其他守卫均为假。本系统新建 `alt` 要求同一个片段中至少两个 `operands`，分别填写 guard 和 message_ids；一个可选分支用 `opt`。这是生成约定，不是声称 UML 禁止单 operand 的 alt。
- `opt`：守卫成立时执行唯一 operand，否则跳过；不表示退出。失败处理后还会继续主流程时，不能用 opt 冒充短路退出。
- `break`：守卫成立时执行唯一 operand，并替代其直接所在交互片段的剩余部分；为假则继续。必须覆盖所在交互片段的全部生命线。嵌套 break 不应声称退出所有外层交互或等价于语言中的任意 return/break。
- `loop`：重复执行唯一 operand；guard 写循环条件或遍历对象。源码的循环 break 与 UML break 的作用范围不同，需要逐例核对。
- `par`：多个 operand 可交错执行，各 operand 内部仍保持自身顺序。
- `critical`：临界区域，不能把任意数据库事务都直接等同于 UML 临界区；`neg` 表示不允许的交互，不是正常失败处理。

`label` 是片段标题；结构化片段的条件在 `operands[].guard`。多个相邻 `alt` 框是多个独立交互片段，**不等价于一个 alt 的多个分支**；在 label 中写“if/else”也不能替代分支结构。一个 alt 内部的 operands 由编辑器绘制守卫和虚线分隔。

旧文件可能只有 label 和矩形，没有 operands。系统保留它们，不根据坐标或文字自动推断分支。分析时明确“旧格式分支归属未显式建模”；修复时由 Agent 对照源码补充结构，不能因 JSON 可加载或坐标已核对就宣称符合 UML 语义。

规范依据：[OMG UML 标准（InteractionOperatorKind / CombinedFragment）](https://www.omg.org/spec/UML/ISO/19505-2/PDF)。本系统支持上述七种操作符，是 UML 的有限子集。

---

## 3. 消息设计指导

### 3.1 消息顺序 (order)

- `order` 字段控制消息从上到下的垂直排列
- 从 1 开始递增
- 同一 `order` 值的消息水平位置由生命线 X 坐标决定
- `order` 从 1 开始，用于表达逻辑先后；同一层并行消息可以共享逻辑阶段，但应避免无意义的跳号
- `y` 是持久化的画布坐标。保留与任务无关的布局；修复分支归属、遮挡或执行显式布局任务时，可以调整相关消息与片段坐标，并同步 operand 边界。
- 不要在 LLM 中自行假设固定间距；前端和后端布局器可能使用不同的视觉间距，最终以显式 `y` 为准

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

`return` 消息没有单独的 request ID 字段。需要配对时，使用端点、顺序和语义标签推断；不要把不相关的返回消息画成某次调用的返回。

子调用返回失败值只结束子调用，不自动结束调用方的交互。若调用方检查失败值后立即返回，应画出检查、失败状态/清理以及向上层调用方的返回，并用作用范围正确的 break（或完整的备选分支结构）表达跳过后续流程。不要使用对象销毁标记代替方法返回。

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
- `activations` 是显式持久化的 Y 偏移数组；可以留空
- 编辑器还会根据非 `return` 消息临时绘制自动激活条，自动激活条不要求写回 `activations`
- 如果使用显式激活条，Y 值是相对生命线头部的偏移，不是消息的绝对 Y 坐标

### 4.4 生命周期约束

- 删除某生命线时，所有引用该生命线的消息都会自动删除
- 复制粘贴生命线时，复制包括名称、关联类、激活条

---

## 5. 组合片段设计指导

### 5.1 片段布局

- `x`：片段左边界，建议比最左生命线的 X 值小约 20px
- `width`：片段宽度，应覆盖 lifeline_ids 对应的生命线；break 覆盖全部所在交互生命线，包括失败块里没有发消息的参与者
- `y_start` 和 `y_end`：片段的上下边界，应包裹相关消息
- 片段高度 = `y_end - y_start`
- `operands[].message_ids` 决定直接消息归属；坐标只决定绘制范围。消息必须位于所归属 operand 的内部，不能靠拖进矩形自动改分支。
- 新建分支填写稳定 operand ID、guard、message_ids、y_start/y_end；同一片段内各 operand 上下排列且不重叠，guard 与第一条消息之间预留约 24px。分支 ID/消息 ID 必须唯一且引用有效。
- 外层片段上方留标题空间，边界满足 fragment.y_start <= operand.y_start < operand.y_end <= fragment.y_end。各分支直接消息不重复归属；外层 operand 不重复列出嵌套片段直接拥有的消息。

### 5.2 片段类型选择

| 设计意图 | 使用片段 | guard / 标题示例 |
|---------|---------|-----------|
| 遍历集合中每个元素 | `loop` | `[for each order]` |
| 多个备选分支 | `alt` | 各 operand：`[x > 0]` / `[else]` |
| 条件可选执行 | `opt` | `[if user is logged in]` |
| 异常/错误退出 | `break` | `[timeout]` |
| 并发操作 | `par` | (无 label 或说明块) |
| 不允许交错执行的临界区域 | `critical` | `exclusive update` |
| 错误场景/不应发生 | `neg` | `[invalid state]` |

### 5.3 片段嵌套

- 嵌套片段同时设置 parent_fragment_id 和 parent_operand_id，位于对应外层 operand 内，覆盖范围不超过外层片段；禁止循环引用。仅矩形相交/包含不能证明语义嵌套。
- 嵌套层级建议 ≤ 2 层，避免过于复杂

---

## 6. 设计原则与最佳实践

### 6.1 时序图设计流程

1. **确定场景**：明确要描述的交互过程（如 "用户登录流程"、"OTA 升级流程"）
2. **识别参与者**：列出所有参与交互的对象/角色
3. **放置生命线**：从左到右排列：触发者→控制器→服务→数据层
4. **核对控制流**：先阅读实现/设计契约，列出守卫、失败出口、返回对象与可继续执行的路径，再按各路径添加消息
5. **标记消息**：为每条消息添加清晰的标签和业务备注
6. **添加片段**：用组合片段包装条件/循环/并发逻辑
7. **检查返回与续行**：需要时补充返回消息；区分子调用返回与整个场景结束，检查失败之后是否还会执行后续步骤

### 6.2 ID 生成规则

- 生命线 ID：`life_<timestamp>_<random6>`
- 消息 ID：`msg_<timestamp>_<random6>`
- 片段 ID：`frag_<timestamp>_<random6>`
- 每个 ID 必须全局唯一

### 6.3 常见错误

1. **order 从 0 开始** — 新消息的 order 应从 1 开始
2. **无理由覆盖既有消息的 y** — 保留无关布局；语义修复或显式布局任务调整相关坐标时，同步更新 operand 边界
3. **把所有 sync 都强行配 return** — 只有返回值、异常或控制流语义需要时才画 return
4. **self 消息的 from ≠ to** — 自反消息两端必须相同
5. **片段边界不含消息** — y_start/y_end 必须包裹相关消息
6. **片段类型拼写错误** — 只能用 7 个枚举值

### 6.4 早退场景与真实备选分支

对于“调用 → 失败立即返回 → 成功继续”的场景，推荐线性成功主线 + `break [失败条件]`。先画产生判定结果的调用/校验，再在 break 内画失败处理和向调用方返回；成功专属后续步骤留在 break 后。guard 为假时才执行这些步骤，它们不是“两个分支都会执行的公共续行”。

对于两个结果都继续的真实备选流程，使用一个 alt：主分支消息放在第一个 operand，备选分支消息放在另一个 operand。只有所有可继续分支都会执行的行为才是公共续行；条件产生步骤放在 alt 前。若通过 alt 表达早退，成功专属步骤也必须放在成功 operand 内，不能假设画一个 return 就取消框外续行。

审查至少回答：判定结果由哪条消息产生？哪条分支退出哪个交互？哪些消息只在成功时执行？哪些消息是公共续行？检查范围只覆盖部分源码时应说明证据边界，不以框的位置替代源码判断。

反例：只画 `alt [else: failed]`，把失败返回框进去、成功流程放框外；把无条件执行的验证放入 `[validation failed]` operand；把仅成功时执行的下一阶段称为公共续行。这些不能通过调整框的高度修复语义。

---

## 7. LLM 输出规范

### 7.1 JSON 字段名严格对照

| 层级 | 字段 | 注意 |
|------|------|------|
| 生命线 | `id`, `name`, `class_ref`, `x`, `activations` | `activations` 是浮点数数组 |
| 消息 | `id`, `from_lifeline`, `to_lifeline`, `label`, `type`, `order`, `y`, `note` | `order` 从 1 开始递增 |
| 片段 | `id`, `type`, `label`, `x`, `width`, `y_start`, `y_end`, `lifeline_ids`, `parent_fragment_id`, `parent_operand_id`, `operands` | 旧文件可缺省扩展字段 |
| operand | `id`, `guard`, `message_ids`, `y_start`, `y_end` | 消息直接归属、条件与显示区域 |

### 7.2 关键约束检查清单

- [ ] 每个 `id` 全局唯一
- [ ] 消息的 `from_lifeline` 和 `to_lifeline` 必须引用已存在的生命线 ID
- [ ] 新消息的 `order` 从 1 开始；同一时序层级可以共享 order
- [ ] 新消息有合理的 `y`；保留无关布局，相关坐标改变后重新检查 operand 边界
- [ ] 需要表达结果、异常或控制流时，为 `sync` 消息补充有意义的 `return`
- [ ] `self` 消息的 from/to 必须是同一个生命线
- [ ] 片段的 `y_start` < `y_end`，且包裹相关消息
- [ ] 片段 `type` 必须为 7 个枚举值之一
- [ ] 新 alt/par 使用多个显式 operands；单条件可选操作用 opt，早退用作用范围正确的 break
- [ ] guard、message_ids、嵌套引用与坐标一致，else 不重复且放在 alt 最后
- [ ] break 覆盖所在交互的全部生命线；失败返回与后续成功步骤的关系已对照实现
- [ ] 运行 `run_task(task="validate", target="设计文件路径", cwd="实际目录")`，读取全部诊断；成功但有旧格式警告不能称为完整语义验证
- [ ] 消息 `type` 必须为 5 个枚举值之一
- [ ] 关键业务消息应填写 `note`；简单通知或显而易见的返回可以为空
- [ ] ⚠️ **坐标不能清零**：PRESERVE 所有 x/y/width/height 字段，NEVER zero them out
- [ ] ⚠️ **class_ref 必须有效**：非空时必须指向项目中存在的类 ID；外部系统、用户角色可以为空
- [ ] ⚠️ **消息 label 必须匹配**：方法调用 label 应与目标类的实际方法签名一致

### 7.3 时序图优化检查清单

当被要求优化时序图时，从以下维度评估：
1. **消息完整性**：是否遗漏了必要的交互步骤？
2. **调用顺序**：时间顺序是否合理？是否有死锁或循环依赖？
3. **消息命名**：标签是否清晰表达含义？
4. **备注质量**：note 是否充分描述了业务逻辑？
5. **片段使用**：条件/循环/异常分支是否用组合片段表达？
6. **参与者合理性**：生命线数量是否合适？职责是否清晰？
7. **返回消息**：关键结果是否表达清楚？是否错误地把子调用返回当成调用方场景终止？
