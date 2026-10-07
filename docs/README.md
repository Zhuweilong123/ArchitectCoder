# 文档导航

> 文档核查更新：2026-09-27。当前事实以源码、配置和下列实现文档为准。

## 当前事实

| 文档 | 说明 |
|---|---|
| [`current-architecture.md`](current-architecture.md) | 当前 Agent 架构、生命周期、工具和插件边界 |
| [`runtime-command-execution.md`](runtime-command-execution.md) | `run_task`、`run_program`、`shell` 和宿主命令安全契约 |
| [`evaluation-system.md`](evaluation-system.md) | 评测规则、实现细节和历史结果 |
| [`current-architecture.md`](current-architecture.md) | 当前 Agent 架构、生命周期、工具和插件边界（含插件架构与扩展契约） |
| [`skills-plugin.md`](skills-plugin.md) | Skill 插件协议、任务内版本快照和自定义 provider |
| [`plugin-lifecycle.md`](plugin-lifecycle.md) | 13 个公共阶段、全插件接口调度、操作树、独立通知、组织图与执行回放 |
| [`plugin-discovery.md`](plugin-discovery.md) | 插件自带声明、目录扫描、配置覆盖、依赖与新增插件示例 |
| [`plugin-quickstart.md`](plugin-quickstart.md) / [English](plugin-quickstart.en.md) | 插件本地验证、应用加载与执行回放教程，附中英文离线 HTML 示例 |
| [`plugin-development.md`](plugin-development.md) | 插件骨架生成、契约检查、独立接口及阶段试运行工具 |
| [`baseagents-design.md`](baseagents-design.md) | Agent 范式、工具注册和框架公开入口 |
| [`design-source-contract.md`](design-source-contract.md) | 设计、源码与测试的一致性契约、实体映射和规则分级 |
| [`multilanguage-execution-design.md`](multilanguage-execution-design.md) | 多语言任务解析、TaskPlan、自动编排和执行证据 |
| [`project-html-export.md`](project-html-export.md) | 项目单文件 HTML 导出与离线阅读操作 |

## 子系统设计

- [`context-management-design.md`](context-management-design.md)：上下文预算、压缩和恢复。
- [`memory-system-design.md`](memory-system-design.md)：记忆模型、检索和生命周期。
- [`knowledge-graph-design.md`](knowledge-graph-design.md)：知识图谱模型、构建和查询。
- [`trace-replay-design.md`](trace-replay-design.md)：Trace 记录、回放、混合执行设计和使用手册。
- [`trace-to-eval-case-factory-design.md`](trace-to-eval-case-factory-design.md)：Trace 转评测用例草稿、fixture、Checker 审核和发布流程。

## 设计提案

- [`architecture-aware-dynamic-scheduling.md`](architecture-aware-dynamic-scheduling.md)：Architecture-aware Dynamic Scheduling；第一版静态只读调度已实现，成本校准、动态重分区与隔离并行写入仍在方案中。

## 历史与决策记录

历史基线、3.1 实施方案、本地模型分析和早期命令执行方案已从工作树移除；如需复盘，请通过 Git 历史查看。
- [`product-differentiation.md`](product-differentiation.md)：产品定位与路线讨论。

当前代码事实优先级高于历史文档；新增设计应先更新当前入口，再补充历史记录。
