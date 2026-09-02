# UML 类图设计指导 (Class Diagram Design Guide)

> 面向 LLM 的类图设计生成指南。遵循本文档的全部规范，生成的 JSON 可被 ArchitectCoder 工具直接加载。

---

## 1. 数据模型参考

### 1.1 UmlClass（类节点）

```json
{
  "id": "class_<timestamp>_<random6>",     // 唯一标识，不能用纯数字
  "name": "ClassName",                       // 类名，PascalCase
  "stereotype": "class",                     // 构造型，见 §2.1
  "attributes": [                            // 属性列表
    {
      "name": "attrName",                    // 属性名，camelCase
      "type": "string",                      // 类型名
      "visibility": "+",                     // 可见性：+ | - | #
      "default_value": null,                 // 默认值，无则 null
      "is_static": false                     // 是否静态
    }
  ],
  "methods": [                               // 方法列表
    {
      "name": "methodName",                  // 方法名，camelCase
      "return_type": "void",                 // 返回类型
      "params": "param1: Type1, param2: Type2", // 参数列表
      "visibility": "+",                     // 可见性
      "is_static": false,                    // 是否静态
      "is_abstract": false                   // 是否抽象方法
    }
  ],
  "position": { "x": 100.0, "y": 200.0 },   // 画布位置
  "size": { "width": 200.0, "height": 150.0 }, // 节点尺寸（默认 200×150）
  "note": "",                                // 业务规则备注（支持多行）
  "provided_interfaces": [],                 // UML 2.5.1 棒棒糖接口
  "required_interfaces": []                  // UML 2.5.1 插座接口
}
```

### 1.2 UmlRelation（类关系连线）

```json
{
  "id": "rel_<timestamp>_<random6>",        // 唯一标识
  "source": "<源Class.id>",                 // 源类 ID
  "target": "<目标Class.id>",              // 目标类 ID
  "type": "association",                    // 关系类型，见 §2.3
  "multiplicity_source": "",               // 源端多重性，如 "1", "0..1", "1..*", "*"
  "multiplicity_target": "",               // 目标端多重性
  "role_name": "",                          // 角色名
  "note": ""                                // 关系备注
}
```

### 1.3 UmlDiagram（完整图）

```json
{
  "version": "1.0",
  "name": "DiagramName",
  "diagram_type": "class",
  "classes": [ ... ],
  "relations": [ ... ],
  // 以下字段按需保留默认值：
  "lifelines": [],
  "messages": [],
  "fragments": [],
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

### 2.1 Stereotype（构造型）

| 值 | 含义 | 视觉表现 | 适用场景 |
|----|------|----------|---------|
| `class` | 普通类 | 标准矩形 | 具体实现类 |
| `interface` | 接口 | `«interface»` 标记 | 纯接口定义 |
| `abstract` | 抽象类 | *斜体下划线* 类名 | 含抽象方法的基类 |
| `enum` | 枚举 | `«enum»` 标记 | 枚举类型 |

### 2.2 Visibility（可见性）

| 值 | 符号 | 含义 |
|----|------|------|
| `+` | public | 公开 |
| `-` | private | 私有 |
| `#` | protected | 受保护 |

**严格约束：visibility 只能使用 `+`、`-`、`#` 三个字符。** 不接受 `"public"` 等字符串形式（后端会在 normalize 阶段自动转换，但直接输出正确值更可靠）。

### 2.3 RelationType（关系类型）

| 值 | 含义 | 视觉线条 | 箭头样式 | 说明 |
|----|------|---------|----------|------|
| `inheritance` | 继承/泛化 | 实线 | 空心三角 (block) | 子类→父类 |
| `realization` | 实现 | **虚线** | 空心三角 (block) | 实现类→接口 |
| `composition` | 组合 | 实线 | 实心菱形 | 强整体-部分 |
| `aggregation` | 聚合 | 实线 | 空心菱形 | 弱整体-部分 |
| `association` | 关联 | 实线 | 普通箭头 (classic) | 一般关联 |
| `dependency` | 依赖 | **虚线** | 普通箭头 (classic) | 使用依赖 |

**关键规则：**
- 实现和依赖关系为**虚线**（`strokeDasharray: "5,5"`）
- `source` 指向关系起点类，`target` 指向关系终点类
- 继承/实现：子类为 source，父类/接口为 target
- 别名兼容：后端会识别 `generalization→inheritance`、`implements→realization` 等

---

## 3. 属性与方法格式约定

### 3.1 属性 (UmlAttribute)

```
[可见性] [static?] name: type [= default]
```

示例：
- `+ username: string` — 公开字符串属性
- `- _count: int` — 私有整数属性
- `# status: StatusEnum` — 受保护枚举属性
- `+ static MAX_SIZE: int = 100` — 静态属性（前端会加下划线显示）

**建议：**
- 属性名使用 camelCase
- 类型名使用语言习惯（如 Java 用 `String`，Python 用 `str`，TypeScript 用 `string`）
- 静态属性标记 `is_static: true`

### 3.2 方法 (UmlMethod)

```
[可见性] [abstract?] [static?] name(params): return_type
```

示例：
- `+ execute(): void` — 公开无参方法
- `+ calculate(a: int, b: int): int` — 公开带参方法
- `# validate(): bool` — 受保护方法
- `+ abstract getData(): object` — 抽象方法（`is_abstract: true`，前端斜体显示）

**建议：**
- 方法名使用 camelCase
- 参数格式：`name: Type, name: Type`
- 抽象方法标记 `is_abstract: true`，其所属类应标记 `stereotype: "abstract"`
- 构造方法通常在方法列表第一个，命名为类名

---

## 4. 关系设计指导

### 4.1 关系方向约定

```
源(source) ──[type]──► 目标(target)
```

| 关系 | source（箭头起点） | target（箭头终点） |
|------|-------------------|-------------------|
| inheritance | 子类 | 父类 |
| realization | 实现类 | 接口 |
| composition | 整体 | 部分 |
| aggregation | 整体 | 部分 |
| association | A | B |
| dependency | 依赖者 | 被依赖者 |

### 4.2 多重性 (Multiplicity)

多重性放在对应端（source 或 target），常用值：
- `"1"` — 恰好一个
- `"0..1"` — 零或一个
- `"1..*"` — 至少一个
- `"*"` 或 `"0..*"` — 任意数量
- 具体数字如 `"2..4"` — 范围

示例：`aggregation` 关系中
- `multiplicity_source: "1"` — 整体端恰好一个
- `multiplicity_target: "*"` — 部分端零到多个

### 4.3 角色名 (role_name)

角色名标注在 target 端，描述 target 在 source 眼中的角色。例如：
- 类 `Order` 关联到类 `Customer`，role_name 可为 `"buyer"`
- 前端会在连线上显示此名称

---

## 5. 设计原则与最佳实践

### 5.1 ID 生成规则

- 类 ID：`class_<timestamp>_<random6>`，其中 timestamp 为毫秒时间戳，random6 为 6 位随机字母数字
- 关系 ID：`rel_<timestamp>_<random6>` 或使用语义化 ID 如 `rel_inherit_order_001`
- 后端 normalize 会自动为缺失 ID 的实体补全

### 5.2 位置布局建议

- 画布左上角为 (0, 0)
- 默认类尺寸 200×150，建议节点间距 ≥ 50px
- 父类/基类通常放在上方，子类放在下方
- 组合/聚合关系：整体放在部分上方或左侧
- 避免节点重叠

### 5.3 note 字段的使用

- **类的 note**：记录业务规则、设计决策、约束条件。代码生成时 LLM 会将 note 作为核心逻辑要求实现。
- **关系的 note**：记录关系约束、使用条件等。
- note 支持多行文本，前端渲染为类的备注区域。

### 5.4 接口字段 (provided_interfaces / required_interfaces)

- `provided_interfaces`：该类**提供**的接口（UML 棒棒糖 ◉），表示其他类可以依赖的能力
- `required_interfaces`：该类**依赖**的接口（UML 插座 ◡），表示该类需要外部提供的能力
- 前端会在类名下方、属性上方显示接口列表
- 在类图中，接口也可以单独创建为 `stereotype: "interface"` 的类

### 5.5 设计模式建议

| 模式 | 实现方式 | 关系组合 |
|------|---------|---------|
| 面向接口编程 | 定义 interface + realization | `realization` |
| 策略模式 | 抽象基类 + inheritance | `inheritance` |
| 组合模式 | 整体-部分关系 | `composition` |
| 工厂模式 | 工厂创建产品 | `dependency` + `realization` |
| 观察者模式 | Subject-Observer | `association` + `dependency` |

---

## 6. LLM 输出规范

### 6.1 JSON 字段名严格对照

生成类图时，务必使用以下字段名（区分大小写）：

| 层级 | 字段 | 注意 |
|------|------|------|
| 类 | `id`, `name`, `stereotype`, `attributes`, `methods`, `position`, `size`, `note`, `provided_interfaces`, `required_interfaces` | 无 |
| 属性 | `name`, `type`, `visibility`, `default_value`, `is_static` | 不是 `defaultValue` |
| 方法 | `name`, `return_type`, `params`, `visibility`, `is_static`, `is_abstract` | 不是 `returnType` |
| 关系 | `id`, `source`, `target`, `type`, `multiplicity_source`, `multiplicity_target`, `role_name`, `note` | **source/target 是 ID 不是 name** |
| 位置 | `x`, `y` | 浮点数 |
| 尺寸 | `width`, `height` | 浮点数 |

### 6.2 常见陷阱

1. **visibility 用 `+`/`-`/`#` 而不是 `"public"`/`"private"`**
2. **source/target 是类 ID，不是类名**
3. **每个 id 必须唯一**，不能有重复
4. **stereotype 只用四个值**：`class`, `interface`, `abstract`, `enum`
5. **relation type 只用六个值**：`inheritance`, `composition`, `aggregation`, `association`, `realization`, `dependency`
6. **空字段不要省略**：`note: ""` 而不是不写
7. **空数组不要省略**：`attributes: []` 而不是不写
8. **relations 中的 source/target 必须对应 classes 中存在的 id**
9. ⚠️ **坐标不能清零**：PRESERVE 所有 position/size 字段（x/y/width/height），NEVER zero them out。如用户要求调整位置则深思熟虑后修改
10. ⚠️ **跨图引用验证**：class_ref、component_id 等跨图引用字段必须指向实际存在的实体 ID

### 6.3 LLM 优化建议

当被要求优化或改进类图设计时，从以下维度考虑：
1. **职责单一**：每个类职责明确，避免 God Class
2. **高内聚低耦合**：减少不必要依赖，使用接口抽象
3. **命名规范**：类名 PascalCase，方法名 camelCase，语义清晰
4. **继承层次**：深度 ≤ 3 层，避免深层继承
5. **接口隔离**：provided_interfaces 粒度适中，不过粗不过细
6. **关系准确**：选择合适的 RelationType，添加多重性和角色名
