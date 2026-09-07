# 文档导航

## 当前事实

| 文档 | 说明 |
|---|---|
| [`current-architecture.md`](current-architecture.md) | 当前 Agent 架构、生命周期、工具和插件边界 |
| [`runtime-command-execution.md`](runtime-command-execution.md) | `run_task`、`run_program`、`shell` 和宿主命令安全契约 |
| [`evaluation-system.md`](evaluation-system.md) | 评测规则、实现细节和历史结果 |
| [`plugin-architecture-design.md`](plugin-architecture-design.md) | 插件加载、provider 和扩展所有权 |

## 子系统设计

- [`context-management-design.md`](context-management-design.md)：上下文预算、压缩和恢复。
- [`memory-system-design.md`](memory-system-design.md)：记忆模型、检索和生命周期。
- [`knowledge-graph-design.md`](knowledge-graph-design.md)：知识图谱模型、构建和查询。
- [`trace-replay-design.md`](trace-replay-design.md)：Trace 记录、回放、混合执行设计和使用手册。

## 历史与决策记录

历史基线、3.1 实施方案、本地模型分析和早期命令执行方案已从工作树移除；如需复盘，请通过 Git 历史查看。
- [`product-differentiation.md`](product-differentiation.md)：产品定位与路线讨论。

当前代码事实优先级高于历史文档；新增设计应先更新当前入口，再补充历史记录。
