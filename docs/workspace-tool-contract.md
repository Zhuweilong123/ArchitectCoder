# 工作区工具路径与错误契约

文件读取、列表、文本搜索、文件修改以及执行工具的工作目录共同使用
`backend/app/runtime/workspace_paths.py` 中的 `WorkspacePathResolver`。

## 路径解析

- 绝对路径必须位于已配置的工作区根目录内。
- `workspace/` 明确指向工作目录，可附加子路径，如 `workspace/engine/planner.py`。
- 真实工作区条目优先于兼容别名：存在实际 `tests/` 时，`tests/a.py` 不会被展开到
  另一处配置的测试目录；同样适用于 `src/`、`design/` 等名称。
- 没有同名实际条目时，`source`/`src`、`test`/`tests`、`design` 可以指向对应配置目录，
  支持附加子路径。未配置的别名报错，不会补成默认目录。
- 普通相对路径优先识别真实工作区条目；兼容查找其他已配置根目录时，多处命中报
  `PATH_AMBIGUOUS`，要求使用明确的绝对路径。
- 新建路径和已有文件采用同一解析规则。相对路径不能越出所选根目录，符号链接不能
  扩大工作区访问范围，`.architectcoder` 由系统管理。

`list_files`、`search_text` 和缺失文件的候选提示统一返回规范绝对路径。一个文件无论
从哪个查询范围发现，路径都一致，可直接传给 `read_file`、`apply_changes`、
`run_task.target` 或 `run_program.args`。搜索定位格式为 `绝对路径:行:列: 文本`；
Windows 盘符中的冒号属于路径。

宿主机路径在执行适配器中转换：WSL 将完整 Windows 路径参数转换为 `/mnt/...`，
容器将挂载工作区内的完整宿主机路径参数转换为 `/workspace/...`。
转换保留空格和 pytest `::` 后缀，不拆分字符串，也不改写任意内嵌表达式。

列表默认查询源码目录，未配置源码目录时查询工作目录。执行工具默认使用工作目录，
未配置工作目录时使用已有根目录；`cwd` 支持同一套路径和别名子路径。
`run_task` 的普通 target 相对 cwd，绝对路径及带别名的 target 使用公共解析器；
pytest 的 `::` 用例选择后缀保留。`run_program.args` 始终是原样的字符串参数，
不对任意参数猜测或改写路径。

## 结构化结果

生产基础工具通过 `run_result` 返回 `ToolResult`，正文仅用于展示，不能决定执行状态。
读取内容以 `Error:` 开头仍属于成功；搜索或列表没有匹配项也属于成功。

| 错误码 | 含义 |
|---|---|
| `INVALID_ARGUMENT` | 参数类型、范围或格式错误 |
| `WORKSPACE_NOT_CONFIGURED` | 未配置可用的工作区根目录 |
| `WORKSPACE_ALIAS_NOT_CONFIGURED` | 所请求的目录别名未配置 |
| `PATH_NOT_FOUND` | 所请求的路径不存在 |
| `PATH_OUTSIDE_WORKSPACE` | 路径或符号链接越出所选工作区边界 |
| `PATH_AMBIGUOUS` | 普通相对路径在多个根目录下有不同匹配 |
| `NOT_A_FILE` / `NOT_A_DIRECTORY` | 所请求的对象类型不符 |
| `FILE_READ_ERROR` / `FILE_LIST_ERROR` / `SEARCH_IO_ERROR` | 文件读取、列表或搜索不完整 |
| `PROCESS_START_ERROR` / `PROCESS_IO_ERROR` | 进程启动或输出采集失败 |
| `PROCESS_TIMEOUT` / `PROCESS_CANCELED` / `PROCESS_EXIT_ERROR` | 执行超时、取消或退出码非零 |
| `PROJECT_JSON_INVALID` / `PROJECT_STRUCTURE_INVALID` | 设计文件格式或结构无效 |

文件变更已有的补丁冲突、版本冲突等专用错误码继续保留。能力策略或审批阻止执行时
使用 `blocked` 和对应策略/审批错误码。策略入口依然校验绝对路径的范围和受保护文件，
包含空格的程序路径参数按完整 argv 校验。

`ToolRoundExecutor` 将明确状态和错误码传给 Hook、Evidence Ledger 和 Trace，
搜索的中文错误不再被记录为成功。兼容旧工具的文字识别只保留在执行边界，
生产基础工具不依赖这个回退机制。
