# 记忆知识生命周期 v1

这一版保留 SQLite / BM25、会话历史和任务 checkpoint，新增通用知识有效性层。宿主发布阶段事件，`extensions/memory/plugin.json` 声明的贡献负责召回、观察、刷新和归档；主循环与任务服务不调用记忆接口。

## 结构与边界

| 层次 | 实现 | 职责 |
|---|---|---|
| 历史证据 | 原 Trace + memory_events + memory_versions | 记录提取依据、资源变化、确认与历史快照，不静默抹掉旧结论 |
| 当前任务状态 | 已有 checkpoint / tool evidence / recovery controller | 保存进度和未解决问题；不把一次失败升级为长期经验 |
| 长期知识投影 | memories.metadata.knowledge | 保存当前状态、作用域、来源条件、验证标记、版本和替代关系 |

核心 MemoryPort 不依赖 SQLite 或文件路径。`MemoryEventRequest` 的 resources 是资源 ID / version 字典，支持 file、environment，以及宿主提供的 API ETag / 文档 revision 等资源。默认 provider 实现文件 hash 和运行环境指纹检查；其他资源需要宿主发送 observe 事件。

| 边界 | 通用宿主能力 | 记忆插件行为 |
|---|---|---|
| prepare | 提供项目、用户输入和可组装的 sections | recall 后加入 memory section |
| run_start | 提供本次用户输入与 run_id | 建立本次记忆观察状态 |
| tool_after | 每次工具结束立即提供 result / effects、参数、event_id | 捕获来源版本，判断失效，保存插件自己的资源目录 |
| model_before | 提供 messages 和 current_user_index | 按需替换自己的 project_memory 投影，失败时移除已知陈旧内容 |
| task_after（通知） | 最终检查或审核后发布任务结果 | 判断是否归档并调度后台提取 |

`ExtensionContext` 只提供按请求隔离的 provider 能力、命名空间状态和元数据；不识别记忆有效性、XML 格式或归档门槛。组装入口绑定 provider 后，`DevPromptBuilder` 仅消费 prepare 的通用 sections。待审核请求保留对应插件状态，接受审核时沿用原始证据；其他请求结束后清理。后台任务使用创建时的证据快照。

`interfaces` 中的 stage 是服务调用的调度绑定，阶段广播不会自动执行它。自动参与主流程需要声明 `contributions`。调整召回、来源校验、刷新、归档或存储策略时修改 `extensions/memory`；只有增加通用阶段数据或能力契约时才需要修改 backend。

示例元数据：

```json
{
  "knowledge": {
    "status": "active",
    "version": 2,
    "scope": {"kind": "workspace", "id": "canonical workspace root"},
    "sources": [{"resource_id": "file:canonical path", "kind": "file", "path": "canonical path", "version": "sha256"}],
    "source_kind": "model_extracted",
    "verification": "source_bound",
    "evidence_id": "archive event ID",
    "supersedes": {"id": "memory ID", "version": 1}
  }
}
```

作用域支持 project / workspace / environment；环境与工作区 ID 由宿主生成，提取模型不能指定任意作用域 ID。明确有截止时间的记忆可带 valid_until（ISO 8601，必须有时区）。

## 写入、来源变化和复核

1. 记忆插件在每个 tool_after 事件中立即捕获 canonical 文件来源及 hash，保存在当前请求的插件资源目录中。工具 detail 不附加记忆专用字段。批量操作返回部分修改而总体失败时，也会处理实际 changes。
2. 来源版本改变，将依赖它的观察标记为 needs_review，保留旧文本及历史证据。不声称来源改变必然使观察错误，也不影响与该资源无关的有来源记忆。
3. 每轮 recall 再检查来源，捕捉外部编辑、文件删除、权限变化和环境切换；待复核、过期或作用域不匹配的条目不注入当前知识。
4. 当前 FC 任务发生变化时，刷新当前 user 消息中的 project_memory 投影，保留用户诉求、工具结果与任务证据。
5. 新归档可以更新同作用域同主题的当前投影，旧内容写入版本表，并记录 supersedes。延迟归档的陈旧来源不能覆盖有效新状态。
6. 明确的宿主核验可使用 observe(memory_validated / user_confirmed)，提供 memory_ids、完整当前来源版本和核验 reason，使仍然成立的旧事实恢复有效。普通读取或召回不会自动触发语义确认。

状态变化：active → needs_review → 经新证据复核恢复 active，或由新版本替代。旧版本快照标记 superseded；当前投影保留原 ID，新增 version，因此历史引用以 `(memory_id, version)` 定位。

归档模型只能引用系统来源目录中已有的 source_refs；未知 ID 被拒绝。运行时 provenance / scope / confirmation 不接受模型 metadata 覆盖。来源版本一致只表明观察依赖未变，不等于内容已独立验证；注入文本分别标注“已确认”“来源版本匹配，观察未独立确认”“历史参考，未核验当前有效性”。

## 访问与强化

recall 只增加访问次数，不增加重要性或确认可信度。重复提取也不加分。

reinforce 保留为显式确认接口：记录确认事件和历史快照，提升重要性；不增加检索访问计数，不允许把待复核来源靠加分恢复。来源已变化时必须提供新的核验依据。

## partial、超时与讨论

来源失效在工具执行后处理，不依赖任务的 completed / partial / timed_out 终态。成功任务归档仍保留严格门槛，避免把失败任务写成全任务完成。

纯讨论第一版采用明确的保守启发式：任务 completed、没有工具步骤、最终答复至少 160 字符，允许后台提取长期决策。问候和简短回应不额外调用提取器；提取仍允许返回空数组。这会漏掉很短的有效决策，后续可用显式用户决策事件替代长度判断。

## 旧数据库兼容

新增三张表均为 CREATE IF NOT EXISTS；原 memories 表保持兼容。

没有来源条件的旧 insight / operational_lesson 在第一次默认 provider recall 时标记为 needs_review，保留原 ID、文本和版本证据，等待重新观察或确认。旧偏好、决策、规范保留为历史参考。新但未绑定来源的 insight 标注未核验；发生实际资源变化后会保守暂停这类无来源观察，而不会删除它们。

## 新 Trace 的检查位置

| 事件 | 关键字段 |
|---|---|
| memory_observed | event_id、resources、affected 中的 id / subject / status / reason |
| memory_recalled | selected 的 ID、分数与 knowledge；skipped 的 ID / subject / reason；legacy_needs_review |
| memory_archived | source_run_id、event_id、inserted、updated、rejected |
| llm_request.messages | 实际注入的 project_memory；发生变化后下一次模型请求应移除待复核内容 |

原有 memory 插件可不实现 observe；核心提供 NoOp / resilience，观察失败不阻断工具执行。发生已知修改但重取记忆失败时，当前任务移除旧记忆块。

## 当前限制与验证

- 文件 hash 是第一版依赖粒度，格式变化也会要求复核；暂不自动进行 AST / UML 元素级语义比较。
- 有效性检查、作用域隔离与通用事件已实现；没有新增全局跨用户记忆、前端管理页面、自动反思模型或全部事实的自动语义核验。
- 检查 hash 需要读取来源文件；新记忆应只绑定必要来源，避免扩大检查成本。
- 默认环境指纹涵盖运行时提供的工作区、系统、执行模式与策略；未声明的依赖版本变化需宿主发送资源事件。
- 历史快照和事件追加保存，当前没有单独的历史保留期限配置；原有当前投影的维护 / 淘汰机制继续使用。

回归测试位于 [test_knowledge_lifecycle.py](../backend/tests/memory_system/test_knowledge_lifecycle.py)，覆盖缺类→修改→停止旧召回→新状态替代、外部修改、部分修改、延迟归档、旧库兼容、环境 / 工作区隔离、显式复核、有效期、以及当前 FC 请求刷新。

插件边界测试位于 [test_plugin_boundary.py](../backend/tests/memory_system/test_plugin_boundary.py)，覆盖应用计划与独立运行、召回配置、逐工具版本捕获、审核后证据继承、观察失败降级和并发请求隔离，并限制主流程引入记忆专用依赖。
