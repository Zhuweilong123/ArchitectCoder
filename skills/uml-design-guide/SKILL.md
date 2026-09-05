---
name: uml-design-guide
description: UML 2.5.1 data-model schemas, naming conventions, diagram semantics and cross-diagram consistency rules for authoring ArchitectCoder class, sequence and component diagrams.
---

# UML 2.5.1 Design Guide

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

每个 `*_example.md` 只包含可解析的完整图示例。示例是模式参考，不替代跨图规则；跨图项目必须按 `cross_diagram_guide.md` 补齐 `component_id`、`class_ref` 和接口关系。

## 通用规则

- 使用规范字段名，不要把优化输出包装格式写入持久化 `.umlproj`。
- ID 必须在项目范围内稳定且唯一；可使用 `class_user`、`life_auth` 等语义 ID，也可使用 `class_<timestamp>_<random6>`。时间戳只是生成策略，不是语义要求。
- `source`、`target`、`from_lifeline`、`to_lifeline`、`class_ref`、`component_id`、`parent_id` 都是 ID 引用，不是显示名称。
- 不要发明字段或枚举值。兼容别名只属于 LLM 规范化层，持久化 JSON 应使用规范值。
- 修改既有图时，必须保留已有的 ID、引用、位置、尺寸和未修改字段；不要把坐标批量清零。
- 关系、消息、父子组件和跨图引用必须在输出前做引用完整性检查。
- UML 2.5.1 的语义优先于视觉习惯：组合、聚合、实现、依赖、同步/异步消息和组合片段必须按语义选择。

## 现有项目迁移与恢复

- 完整同步一个既有 `.umlproj` 时，以当前有效项目为规范源；先复制或转换完整项目，再做局部修正。
- 语法正确不代表项目可用。有效项目必须有非空 `diagrams`，且每个图符合对应 schema。
- 修改后至少检查 JSON 解析、ID 唯一性、图内端点引用和跨图引用；不要为查看文件专门创建辅助脚本。
