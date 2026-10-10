# 时序图片段结构与 Agent 修复

当前系统保存 `SeqFragment.operands`：每个分支有稳定 `id`、`guard`、直接归属的 `message_ids`、显示范围 `y_start/y_end`。片段同时保存 `lifeline_ids`；嵌套使用 `parent_fragment_id` 和 `parent_operand_id`。

旧文件缺省这些字段时仍能加载，没有自动迁移。旧 alt/par 会在验证时产生 `SEQ_LEGACY_OPERANDS` 警告；不能把“可加载”或“结构检查完成”当作控制流语义正确。现有项目设计图由项目 Agent 在获得修复任务后对照源码修改。

## 运行时支持

- 前后端序列化、流式元素处理保留分支字段；编辑器显示各分支守卫与虚线分隔，SVG 导出保留这些内容。
- 排列与适应片段依据显式消息归属处理结构化片段；坐标拖动不自动改变归属。旧框继续使用既有布局处理。
- 删除消息/生命线时清理引用；删除父片段时删除嵌套片段框，保留消息。撤销可恢复结构。
- 审核差异识别 guard、消息归属、覆盖范围及嵌套变化；知识图谱保存同样的语义字段。
- `run_task(task="validate", target="…umlproj", cwd="实际目录")` 检查结构；`submit_uml_review` 在提交前检查变更图，有结构错误时返回诊断，有旧格式警告时将警告附在审核内容。

检查包括：操作符、消息端点、自反消息、ID、operand 数量与守卫、else、范围及重叠、消息引用与唯一归属、嵌套引用与循环、生命线覆盖、break 作用范围。它不是源码控制流证明器，不判断任意自然语言守卫是否互斥、是否漏掉业务分支，也不自动修改不合理的图。

当前人工编辑可调整片段标题与位置；分支结构由 Agent JSON 或项目文件输入，尚未提供专门的分支表单。不要通过拖动消息来表达分支归属变更。

## 项目 Agent 后续任务

可以在重启后端并重新打开前端、新建 Agent 会话后使用以下任务描述。技能内容按任务快照加载，旧任务可能仍持有旧指南。

> 加载 uml-design-guide 的 sequence_diagram_guide.md，审查指定时序图及对应实现。区分备选分支、可选行为、失败早退和成功专属续行。修复时使用显式 operands、guard、message_ids、lifeline_ids，必要时记录嵌套引用；保留无关图和稳定 ID。先验证结构，处理错误并说明剩余警告，再提交 UML diff 审核。源码证据不足的地方明确说明，不仅凭坐标声称语义正确。

操作符选择与完整示例见 [时序图指南](../skills/uml-design-guide/sequence_diagram_guide.md) 和 [示例](../skills/uml-design-guide/sequence_diagram_example.md)。
