# DevAgent 运行时命令执行契约

> 当前实现基线：`65f8fab`

本文说明“OS 差异下沉到 runtime 层”的完整机制。Agent、工具和业务服务不拼接
Windows/Posix 命令，也不自行判断宿主系统；它们只依赖 runtime 提供的稳定契约。

## 1. 端到端流程

```text
Settings.agent_command_environment
              |
              v
resolve_command_environment()
              |
              v
build_command_executor(settings)
              |
       +------+-------+----------------+
       |              |                |
       v              v                v
NativePowerShell  NativeLinuxBash  WslBashExecutor
       |              |                |
       +--------------+----------------+
                      v
             CommandExecutor.profile
                      |
                      v
build_environment_context(executor, cwd, roots, layout)
                      |
                      v
             runtime facts -> system prompt
                      |
                      v
run_task / run_program / shell
                      |
                      v
        profile validation + native process launch
```

生产组合入口是 `backend/app/agent_base/assembly.py`：先创建命令执行器，再把同一
执行器注入基础工具和 `EnvironmentContext`。工具只使用逻辑目录别名
`source`、`test`、`design`、`workspace`；绝对路径解析和宿主差异由 runtime 负责。

## 2. 配置到执行器

配置定义在 `backend/config/settings.py`：

| 配置 | 可选值 | 含义 |
|---|---|---|
| `agent_command_environment` | `auto` | 生产默认值：Windows → 原生 PowerShell；Linux/macOS → 原生 POSIX |
|  | `native_windows` | 强制 `NativePowerShellExecutor` |
|  | `native_posix` / `native_linux` | 强制原生 Bash 执行器；`native_linux` 在统一入口中归一化为 `native_posix` |
|  | `wsl` | 强制 `WslBashExecutor` |
| `agent_wsl_distribution` | 字符串 | WSL 发行版，可为空使用默认发行版 |
| `agent_wsl_executable` | 默认 `wsl.exe` | WSL 启动器 |
| `agent_wsl_preflight_timeout_seconds` | 默认 `20` | WSL 可用性预检超时 |

`resolve_command_environment()` 只负责把 `auto` 和别名解析成执行目标，不启动进程。
`build_command_executor()` 是生产路径，按解析结果创建唯一 executor；不满足宿主条件
时抛出 `ExecutionEnvironmentError`，不会偷偷切换到另一个 Shell。

代码中还保留 `build_linux_command_executor()`，它服务于明确要求“Linux 命令契约”的
兼容调用方，使用 `resolve_linux_command_environment()`。这条兼容路径的 `auto` 在
Windows 上仍可选择 WSL，不应与生产 `build_command_executor()` 的默认策略混用。

## 3. Runtime 环境事实

`build_environment_context()` 从 executor 的 `profile.name` 生成不可变的
`EnvironmentContext`，包括：

- `host_os`：后端进程实际运行的宿主系统；
- `execution_os`：命令最终运行的系统（Windows 或 Linux）；
- `shell`：`powershell` 或 `bash`；
- `execution_mode`：`windows-powershell`、`linux-native` 或 `linux-wsl`；
- `cwd`、`workspace_roots`、`workspace_layout`：已解析的工作区路径；
- `path_style`、`executable_suffix`：Windows 路径/`.exe` 或 POSIX 路径/无后缀；
- `network_access`、`filesystem_policy`：当前运行策略。

`EnvironmentContext.to_prompt()` 只提供事实描述，不承担安全控制。Prompt 会明确
“runtime facts are authoritative”，防止模型自行假设另一个 Shell、工作区或路径风格。
真正的能力和风险控制仍在工具 schema、`CapabilityPolicy` 和 executor 校验中。

## 4. 执行器适配层

所有执行器遵守 `CommandExecutor` 协议：

```text
profile
preflight()
validate_command(command)
start(command, cwd)
start_program(program, args, cwd)
terminate(process)
```

### 4.1 Windows 原生 PowerShell

`NativePowerShellExecutor`：

- 自动选择 `pwsh.exe`，不可用时选择 `powershell.exe`；
- `shell` 通过 `PowerShell -Command` 启动；
- `run_program` 使用 `shell=False` 和字面量 argv；`.cmd/.bat` 才通过受控 `cmd.exe` 兼容启动；
- 使用进程组和 `taskkill /T /F` 终止进程；
- 允许受控只读表达式，例如 `(Get-Content file).Count`；
- 禁止命令串联、管道、重定向、替换、嵌套 Shell 和未允许的 executable。

### 4.2 Linux 原生 Bash

`NativeLinuxBashExecutor`：

- 预检 `bash` 是否存在；
- `shell` 通过 `bash -lc` 启动；
- `run_program` 使用 `shell=False` 和字面量 argv；
- 使用独立 session 和进程组，终止时发送 POSIX 信号；
- 使用 POSIX 路径和无 executable 后缀。

### 4.3 Windows 上的 WSL Bash

`WslBashExecutor` 是显式选择的 Linux 适配器：

- 预检 `wsl.exe`、发行版和 `bash`，失败时返回明确错误；
- 工作目录通过 `--cd` 传入，Windows `C:\x\y` 映射为 `/mnt/c/x/y`；
- 命令通过 `wsl.exe --exec bash -lc ...` 执行；
- 命令正文禁止 Windows 驱动器路径，Agent 应使用 `cwd` 参数或工作区相对路径；
- WSL 冷启动超时不会永久污染 executor，下一次调用仍可重试。

## 5. 工具层如何消费 runtime

| 工具 | runtime 依赖 | 是否自行处理 OS 差异 |
|---|---|---|
| `run_task` | 任务解析、cwd 别名、`start_program` | 否 |
| `run_program` | executable/argv 校验、`start_program` | 否 |
| `shell` | profile 描述、`validate_command`、`start` | 否 |
| `read_file` / `search_text` / `list_files` | 工作区路径解析 | 否 |
| `apply_changes` | `NativeFileSystem` 和工作区边界 | 否 |

生产工具路由固定为：`run_task` → `run_program` → `shell`。Shell 只是最后的受限
通道，不能绕过结构化文件工具或标准任务工具。

## 6. 文件系统差异也下沉到 runtime

`backend/app/runtime/filesystem.py` 的 `NativeFileSystem` 使用 Python 原生
`pathlib`、`os` 和 `shutil`，不生成 Shell 命令，因此同一套文件工具可以跨 Windows、
Linux 和 WSL 使用：

- `workspace_root_for()` 计算已配置目录的公共绝对根；不同 Windows 驱动器无法求公共路径时安全回退到首个配置路径；
- `write_bytes()` 使用同目录临时文件、`flush/fsync` 和 `os.replace()` 原子替换；
- `move()`、`copy_file()`、`delete_*()` 和 `make_directory()` 都通过原生文件 API 完成；
- 上层仍需先执行工作区白名单和 capability 校验，runtime 不扩大允许范围。

因此“文件怎么写”与“命令在哪个 OS 上跑”是两个独立适配点：文件操作不依赖
PowerShell/Bash，只有执行命令时才进入 `CommandExecutor`。

## 7. 安全边界和失败语义

- Prompt 中的环境事实不能提升权限；所有命令仍须经过 executor 和工具层校验。
- `run_program` 只接受允许列表中的程序和字面量参数，不接受 Shell 解释器或控制字符。
- `shell` 只接受一条简单命令，禁止 `;`、`&&`、`||`、管道、重定向、命令替换和嵌套 Shell。
- 高风险操作直接拒绝，敏感操作需要人工审批；没有审核通道时 fail-closed。
- 预检失败、路径不合法和 executable 不允许都返回结构化错误，不自动更换 OS 或 Shell。

这样，Agent 只需要理解稳定的工具契约；OS 选择、路径格式、进程启动、终止和预检
全部由 runtime 层承担。

## 8. 代码索引

| 文件 | 职责 |
|---|---|
| `backend/config/settings.py` | 命令环境和 WSL 配置 |
| `backend/app/runtime/command.py` | profile、executor、环境解析和进程启动 |
| `backend/app/runtime/environment.py` | Runtime 环境事实和 Prompt 描述 |
| `backend/app/runtime/filesystem.py` | OS 无关的原生文件操作和工作区根计算 |
| `backend/app/runtime/__init__.py` | runtime 公开导出 |
| `backend/app/agent_base/assembly.py` | 创建 executor、环境上下文和基础工具 |
| `backend/app/agent_base/tools/my_tools/foundation_tools.py` | 将稳定工具契约绑定到 executor |
