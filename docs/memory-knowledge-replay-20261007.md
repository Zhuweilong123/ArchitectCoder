# 记忆机制 v1：原 trace 证据回放

验证方式：读取原 trace 的记忆候选和真实文件变更 hash，在独立临时 SQLite 数据库中运行新机制。没有改动被分析项目，没有调用真实 LLM；新候选由已记录的补丁与最终说明构造。本回放验证状态管理与来源治理，不验证模型提取的准确率。

来源：

- [L2146](../temp/chat_log/2026-10-06/trace_20261006_203320_jqx6.jsonl:2146)：旧 insight，“UML 类图未包含当前横向 DP+QP 模块……”，subject=`uml:class_diagram:dp_qp_coverage`。
- [L2878](../temp/chat_log/2026-10-06/trace_20261006_203320_jqx6.jsonl:2878)：实际 UML 修改成功，before hash=`d531f890f06a…`，after hash=`ebe9b1e3ec83…`。
- [L3067](../temp/chat_log/2026-10-06/trace_20261006_203320_jqx6.jsonl:3067)：说明新增 `dp_qp`、`DPQPPathPlanner` 等类。补丁成功记录提供实际修改依据。

| 阶段 | 新机制结果 |
|---|---|
| 变更前 | 召回 1 条“类图缺少 DP+QP”观察，并标注来源版本匹配、观察未独立确认 |
| 使用 L2878 真实变更版本后 | 原观察进入 needs_review，reason=source_changed；同样查询召回 0 条旧结论 |
| 以新来源版本写入“类图已包含 DP+QP”候选后 | 同样查询召回 1 条新结论；当前投影使用原 ID、新 version 和 supersedes 关系 |
| 历史审计 | 保存 5 份内容 / 状态快照；旧文本仍能追溯 |

此外，完整运行时测试直接运行真实 FC 工具循环：携带旧观察进入任务 → apply_changes 改文件 → 下一次模型请求的 project_memory 移除旧观察。工具返回和用户诉求保持可见。

相关回归结果：**244 passed，1 skipped**，覆盖记忆、核心端口、插件分发与发现、FC 循环、失败恢复、执行契约、基础工具与统一路径契约。测试使用 Mock LLM。运行目录采用独立系统临时目录，避免测试项目被仓库配置识别影响任务解析。

查看完整回放数据：[memory_trace_replay.json](../temp/memory_trace_replay.json)。脚本：[replay_memory_trace.py](../temp/replay_memory_trace.py)。机制说明：[memory-knowledge-lifecycle.md](memory-knowledge-lifecycle.md)。

## 后续实际试用

重启后端后重复一个“观察 → 修改相关来源 → 再次提问”的任务。关注：

1. memory_observed.affected 是否记录 source_changed / needs_review。
2. memory_recalled.skipped 是否说明旧条目被跳过，selected 是否带来源和版本。
3. llm_request.messages 中的 project_memory 是否停止注入旧观察，包括同一长任务的后续请求。
4. memory_archived 是否明确 inserted / updated / rejected，新事实是否保留原证据及替代关系。

旧数据库中无来源条件的 insight / operational_lesson 首次召回会保留并标记待复核，需要重新观察或显式确认后才作为当前知识使用。
