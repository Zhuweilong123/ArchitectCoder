# remove_01 评测历史

> 用途：持续记录 `remove_01` 连续多轮评测的可比较指标、Trace 结论和修复效果。
>
> 固定用例：`trace-3-1-component-element-continuous-remove-001`
>
> 当前 fixture：`radar_trace_remove_v1`，布局版本 `design-src-test-v1`

## 记录规则

后续每次运行 `remove_01` 都追加一个“运行记录”小节，并保留以下字段：

- 运行时间、代码版本、模型、温度、用例 ID、fixture/project 版本。
- `run_id`、`trace_id`、结果文件和 Trace 文件路径。
- 状态、是否通过、得分、墙钟耗时、完成轮数。
- 工具调用数、LLM 请求/响应数、Prompt/Completion/Total tokens。
- 工具错误数及错误分类。
- Checker 总数、通过数、失败数，以及未执行的 checker。
- 每轮的工具调用数、LLM 请求数和 Token 消耗。
- 与上一次运行相比的绝对变化和百分比变化。

比较时必须保持用例、fixture、模型、温度和预算口径一致。若其中任一项变化，必须标记为新的基线，不能直接判定性能回归或收益。

`remove_01` 是当前连续对话用例的简称，不是独立的 case ID。正式执行命令使用上面的固定用例 ID。

## 评测 Trace 命名

从本次命名改造开始，评测 Trace 统一使用：

```text
trace_YYYYMMDD_HHMMSS_<16位run标识>_eval.jsonl
```

例如：

```text
trace_20260904_122142_bf569f830d3946d6_eval.jsonl
```

其中 16 位标识来自对应的 `run_id`，`_eval` 用于和普通聊天 Trace 区分。本文历史记录中的旧运行仍保留原始文件名 `trace_eval_<run_id>.jsonl`，不回写历史结果。

## 指标总览

| 指标 | 修复前 | 当前修复后 | 变化 |
|---|---:|---:|---:|
| 运行时间 | 2026-09-04 | 2026-09-05 | — |
| `run_id` | `eval_774290572c5c49a1` | `eval_1b059a4e3d304058` | — |
| `trace_id` | `717346b45eef440b` | `3ac1ffe547794b03` | — |
| 模型 | `deepseek-v4-flash` | `deepseek-v4-flash` | 不变 |
| Prompt 版本 | `devagent-3.1-r4` | `devagent-3.1-r4` | 不变 |
| 完成轮数 | 6/7 | 7/7 | +1 轮 |
| 最终状态 | `timeout` | `passed` | 通过 |
| 通过 | 否 | 是 | — |
| 得分 | 0.0 | 1.0 | +1.0 |
| 墙钟耗时 | 300.6 s（超时） | 191.0 s | -109.6 s（-36.5%） |
| 工具调用 | 97 | 86 | -11（-11.3%） |
| LLM 请求 | 55 | 51 | -4（-7.3%） |
| LLM 响应 | 54 | 51 | -3（-5.6%） |
| Prompt tokens | 342,001 | 325,801 | -16,200（-4.7%） |
| Completion tokens | 38,709 | 26,741 | -11,968（-30.9%） |
| Total tokens | 380,710 | 352,542 | -28,168（-7.4%） |
| LLM 累计耗时 | 295.5 s | 182.6 s | -112.9 s（-38.2%） |
| Trace 事件数 | 336 | 315 | -21（-6.3%） |
| 工具错误 | 7 | 1 | -6（-85.7%） |
| Checker 结果 | 19 个已执行 | 43 个，全部通过 | 最终质量门禁完成 |

### 运行配置

两次运行均使用以下评测配置：

```text
case_max_seconds: 300
turn_max_tool_calls: 100
case max_total_tokens: 500000
production agent per-turn token cap: 200000
fixture: radar_trace_remove_v1
entry_file: design/radar_design_0730.umlproj
source_dir: src
test_dir: test
```

## 修复前运行记录

### 结果

- 状态：`timeout`
- 结果：未通过，得分 `0.0`
- 运行耗时：`300.6 s`，触发 case 墙钟上限。
- 完成进度：`6/7` 轮，第 7 轮测试没有开始。
- Trace：`D:\AI_tools\temp\chat_log\trace_eval_774290572c5c49a1.jsonl`

### 逐轮指标

| 轮次 | 工具调用 | LLM 请求 | Total tokens | 结果 |
|---:|---:|---:|---:|---|
| 1 | 0 | 1 | 3,332 | 完成问候 |
| 2 | 14 | 11 | 54,296 | 完成组件查询 |
| 3 | 44 | 24 | 196,094 | 完成 UML 名称修改 |
| 4 | 22 | 10 | 70,665 | 完成源码同步 |
| 5 | 11 | 6 | 44,089 | 完成状态查询 |
| 6 | 6 | 3 | 12,234 | 完成文件删除 |
| 7 | — | — | — | 未执行，整体超时 |

### 错误分类

- `shell` 链式命令被策略拒绝：3 次。
- `shell` 高风险 `format` 命令被拒绝：1 次。
- `apply_changes` 参数 `content` 非字符串：1 次。
- `run_task validate` 使用旧的根目录 UML 路径：1 次。
- `run_task test` 运行目录/目标不匹配：1 次。

主要问题是旧 fixture 把 UML 文件放在根目录，而基础工具已经采用 `design/src/test` 三目录协议；模型因此反复探路、重试错误路径，并在最后一轮测试前耗尽时间。

## 当前修复后运行记录

### 结果

- 状态：`passed`
- 结果：通过，得分 `1.0`。
- 运行耗时：`191.0 s`。
- 完成进度：`7/7` 轮全部完成。
- Checker：共 `43` 个，`43` 个通过，`0` 个失败。
- Trace：`D:\AI_tools\temp\chat_log\trace_eval_1b059a4e3d304058.jsonl`
- 结果文件：`temp/evals/remove_01-contract-v2-results.jsonl`

### 逐轮指标

| 轮次 | 工具调用 | LLM 请求 | Total tokens | 结果 |
|---:|---:|---:|---:|---|
| 1 | 0 | 1 | 3,594 | 完成问候 |
| 2 | 17 | 13 | 67,840 | 完成组件查询 |
| 3 | 19 | 12 | 105,173 | 完成 UML 名称修改 |
| 4 | 30 | 12 | 93,656 | 完成源码同步 |
| 5 | 8 | 4 | 23,148 | 完成状态查询 |
| 6 | 11 | 7 | 48,955 | 完成文件删除 |
| 7 | 1 | 2 | 10,176 | 测试完成 |

### Checker 明细

| Checker | 数量 | 通过 |
|---|---:|---:|
| `review_auto_stub` | 1 | 1 |
| `uml_valid` | 2 | 2 |
| `uml_component_names` | 3 | 3 |
| `file_contains` | 16 | 16 |
| `file_not_contains` | 16 | 16 |
| `file_absent` | 3 | 3 |
| `pytest` | 2 | 2 |
| 合计 | 43 | 43 |

### 剩余错误

仅剩 1 个 `shell` 链式命令策略拒绝。该错误没有阻止评测完成，模型随后使用允许的工具完成了任务。它仍然说明工具提示可以继续强化“单条 PowerShell 命令、禁止链式语法”的引导，但不属于本次超时根因。

## 本轮修复内容

1. 将 trace fixture 的 UML 入口统一为 `design/radar_design_0730.umlproj`。
2. 增加评测运行前的 fixture 布局预检，错误在 LLM 执行前结构化返回。
3. 在结果中记录工具协议版本、fixture 布局版本、预算范围和 project manifest。
4. 让 `search_text` 与 `list_files` 的目录别名协议一致，支持 `src/test/design/workspace`。
5. 让 `run_task` 归一化 `target=test, cwd=test`，避免执行错误的 `test/test`。
6. 为每轮注入评测专属工作区契约，明确 UML、源码、测试路径和常用任务调用方式。
7. 新增基础工具与评测运行器回归测试，当前相关集成测试 `39 passed`。

## 后续追加模板

每次运行后复制以下模板追加到本文档末尾：

```markdown
## YYYY-MM-DD：<代码版本/commit>

- case_id：
- fixture/project：
- model / temperature：
- run_id / trace_id：
- status / passed / score：
- duration_ms：
- completed_turns / reference_turns：
- tool_calls：
- llm_requests / llm_responses：
- prompt_tokens / completion_tokens / total_tokens：
- tool_errors：
- checker_total / checker_passed / checker_failed：
- result_path：
- trace_path：
- 相对上次变化：

| 轮次 | 工具调用 | LLM 请求 | Total tokens | 结果 |
|---:|---:|---:|---:|---|
| 1 |  |  |  |  |
```
