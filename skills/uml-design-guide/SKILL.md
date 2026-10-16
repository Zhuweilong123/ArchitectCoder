---
name: uml-design-guide
description: UML 2.5.1 schemas, semantics, validation and cross-diagram consistency for creating, reviewing, repairing and optimizing ArchitectCoder .umlproj files and LLM class, sequence and component diagram outputs.
---

# UML 2.5.1 Design Guide

生成或修改设计后，使用通用项目校验入口检查最终产物，按图类型指南提供所需证据。区分结构检查、源码一致性和跨图一致性的覆盖范围；报告中 partial、not_applicable、unavailable、unsupported 均不代表完整语义通过。发现问题应修复实际设计数据，再提交审核；工具描述不承担各图类型的规则定义。

开始有明确验证目标的任务时，在 `todo_write` 的顶层 `validation_requirements` 声明必需检查：`[{"rule_id": "注册规则 ID", "diagram_name": "准确图名"}]`。只声明任务明确要求的机器验收维度，不把所有可用规则强加给任务。根据源码理解、审查或修订设计时，应引用证据并说明审查范围，不因此默认要求自动语义证明。只有任务明确要求自动源码一致性检查时，才声明相应源码规则并补齐机器证据。已声明要求在本任务及恢复执行中保留；未适用、未执行或证据不足不满足验收，不能用结构检查替代。报告的规则 ID 可用于声明，未注册规则会报告缺失。

修复任务按图或独立问题逐项完成“读取必要证据 → 修改 → 校验 → 审核”，再扩展到下一项；不要先遍历整个项目耗尽执行资源后才开始修改。未执行修改时如实报告分析成果和待办，不声称已修复。

这是一套面向 LLM 的 UML 2.5.1 设计参考。目标是生成语义正确、引用完整、可被 ArchitectCoder 加载的 UML JSON。

## 先区分两种 JSON 格式

项目中存在两种不同的 JSON 契约，不能混用。

### 1. `.umlproj` 持久化格式

这是 ArchitectCoder 实际保存和加载的格式：

```json
{
  "version": "1.0",
  "revision": 0,
  "name": "ProjectName",
  "diagrams": [
    {
      "version": "1.0",
      "name": "System Architecture",
      "diagram_type": "component",
      "component_id": "",
      "classes": [],
      "relations": [],
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
  ],
  "active_diagram_index": 0
}
```

`diagrams[]` 中是完整的 `UmlDiagram`，使用 `diagram_type` 和各自的内容数组。

### 2. LLM 优化输出格式

全局设计或优化接口使用包装格式：

```json
{
  "diagrams": [
    {
      "type": "component",
      "name": "System Architecture",
      "component_id": "",
      "data": { "components": [], "comp_relations": [] }
    }
  ],
  "consistency_report": [],
  "changes_summary": "",
  "design_constraints": {},
  "diff": ""
}
```

这里使用 `type` 和 `data`。它是 LLM 的中间输出，不应直接当作 `.umlproj` 根对象保存。除非用户明确要求局部更新，否则输出应保留完整图数据。

## 按任务加载文件

| 任务 | 文件 |
|---|---|
| 类图：类、属性、方法、关系 | `class_diagram_guide.md` |
| 时序图：生命线、消息、组合片段 | `sequence_diagram_guide.md` |
| 组件图：组件、接口、依赖、委托 | `component_diagram_guide.md` |
| 多图联动、`component_id`、`class_ref`、接口一致性 | `cross_diagram_guide.md` |

每份专用指南依次覆盖：schema、允许值、UML 2.5.1 语义、布局约束、设计原则和 LLM 输出检查清单。

## 示例文件

每个单图 `*_example.md` 只包含可解析的完整图示例。多图任务额外加载 `cross_diagram_example.md` 了解案例结构；需要在设计器中查看时，打开同目录下的 `cross_diagram_example.umlproj`。其中 `.umlproj` 是唯一案例数据源，Markdown 只保存说明和引用检查清单。

不要把跨图案例复制到每个单图指南中：类图、时序图、组件图指南负责单图语义，`cross_diagram_guide.md` 和 `cross_diagram_example.md` 负责跨图引用与一致性。

## 通用规则

- 使用规范字段名，不要把优化输出包装格式写入持久化 `.umlproj`。
- ID 必须在项目范围内稳定且唯一；可使用 `class_user`、`life_auth` 等语义 ID，也可使用 `class_<timestamp>_<random6>`。时间戳只是生成策略，不是语义要求。
- `source`、`target`、`from_lifeline`、`to_lifeline`、`class_ref`、`component_id`、`parent_id` 都是 ID 引用，不是显示名称。
- 不要发明字段或枚举值。兼容别名只属于 LLM 规范化层，持久化 JSON 应使用规范值。
- 修改既有图时，必须保留已有的 ID、引用、位置、尺寸和未修改字段；不要把坐标批量清零。
- 关系、消息、父子组件和跨图引用必须在输出前做引用完整性检查。
- UML 2.5.1 的语义优先于视觉习惯：组合、聚合、实现、依赖、同步/异步消息和组合片段必须按语义选择。
- 时序图的分支语义与结构化 operands 以 `sequence_diagram_guide.md` §2.2、§5、§6.4 为准。旧格式仅保留可视框，不能推断为完整备选分支；修图前先核对守卫、退出范围和成功专属续行。
- 分析/讨论任务不修改设计文件；修复任务先检查当前文件与源码证据，保留无关数据，再验证并按项目已有审核流程提交。验证工具检查结构，不能证明与任意源码的控制流完全一致。

## 现有项目迁移与恢复

- 完整同步一个既有 `.umlproj` 时，以当前有效项目为规范源；先复制或转换完整项目，再做局部修正。
- 语法正确不代表项目可用。有效项目必须有非空 `diagrams`，且每个图符合对应 schema。
- 修改后至少检查 JSON 解析、ID 唯一性、图内端点引用和跨图引用；不要为查看文件专门创建辅助脚本。
