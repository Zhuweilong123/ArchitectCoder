# Multilanguage Execution Evolution

Status: Milestone 4 — C++ language-adapter foundation

## Goal

Allow projects in different languages and build systems to build, test, lint,
format, type-check, and run without adding every compiler or package manager to
a host-level executable allowlist. The model requests a semantic task; a
project resolver selects a toolchain; an execution broker enforces the sandbox.

This document is an evolution contract, not a promise that every language is
already implemented. The current production path keeps the legacy Python/Node
fallback while project manifests are resolved through the language-neutral
contracts below.

## Boundary

```text
Agent
  -> Task API (build/test/lint/typecheck/run)
  -> Project Task Resolver
  -> Toolchain Adapter
  -> Execution Broker
  -> isolated worker or restricted local executor
```

Code understanding is a separate path:

```text
source files -> Language Adapter -> Artifact Facts
             -> knowledge graph / UML contract / evidence
```

Language parsing must not be coupled to process execution. A C++ Clang
adapter, for example, may consume `compile_commands.json` while the broker
decides where CMake and Clang are allowed to run.

## Milestone 0 contracts

`backend/app/runtime/task_contracts.py` defines immutable, language-neutral
contracts:

- `TaskSpec`: semantic task, literal argv, working directory, toolchain,
  network/approval policy, resource limits, and expected outputs.
- `ToolchainProfile`: managed toolchain identity, version, executor, image, and
  capabilities.
- `ExecutionPolicy`: effective sandbox, network, writable roots, approval, and
  host environment policy.
- `ExecutionEvidence`: structured result including status, exit code, timeout
  reason, sandbox, network policy, output, and diagnostics.

The contracts intentionally do not start processes, inspect the host PATH, or
decide whether an executable name is globally trusted. They are the stable
boundary for later resolvers and brokers.

## Milestone 1 implementation

`backend/app/runtime/task_resolver.py` now resolves standard task names from
project metadata without changing the host executable policy. The first
adapters cover `package.json`, `pyproject.toml`, `CMakeLists.txt`, `Cargo.toml`,
and the initial `.architectcoder/tasks.json` marker. Resolution returns a
`TaskSpec` plus `ToolchainProfile`; it never launches a process.

`run_task` uses the resolver first and retains the old fixed mapping only when
no supported project marker is present. A supported marker with an unknown
task returns a typed error instead of silently running an unrelated command.
Resolved tasks now execute through the broker seam described below.

## Milestone 2 implementation

The resolver also accepts `.architectcoder/tasks.json` as an explicit project
manifest. Each task may declare literal `argv`, `cwd`, network and approval
policy, resource limits, and expected outputs. Toolchain identity and version
are recorded in `ToolchainProfile`. Invalid manifests and unknown tasks are
reported as resolution failures; they never fall through to the legacy Python
or Node commands. A manifest `cwd` is resolved relative to the detected project
root and is rejected if it escapes that root or names a missing directory.
The `run_task` tool accepts custom manifest task names (for example `coverage`
or `package`) without expanding a global task/language allowlist.

## Milestone 3 implementation foundation

`backend/app/runtime/execution_broker.py` introduces the `ExecutionBroker`
protocol and a `LocalExecutionBroker` migration backend. The broker consumes a
`TaskSpec`, checks the workspace boundary, approval/network policy, delegates
host-specific validation to the injected executor, enforces task timeouts and
runtime stop requests, terminates the process tree, caps output, and returns
`ExecutionEvidence` with a machine-readable category.

`LocalExecutionBroker` is deliberately not described as an OS sandbox. It is
the compatibility seam for a WSL/container worker; until a worker proves the
requested isolation capabilities, existing host executor restrictions remain
authoritative.

The production conversation-tool factory now injects a local broker into
resolved project tasks. Legacy tasks continue to use the compatibility path,
while resolved tasks return broker evidence through `ToolResult` so callers can
distinguish policy blocks, start failures, process failures, cancellation, and
timeouts.

`backend/app/runtime/sandbox_worker.py` adds capability discovery and a
`ContainerWorker` implementation. `WslWorker` reports launcher readiness
separately from filesystem, network, and resource isolation. It refuses a
policy that requires denied network or bounded writable roots unless deployment
explicitly declares those capabilities. `ContainerWorker` owns a Docker
executor that mounts only `/workspace` (the container root is read-only, with
a bounded `/tmp` tmpfs) and always passes `--network none`/`--pull never`;
Docker/image preflight failures are reported as unavailable rather than
falling back to the host executor. A successful preflight records the local
image ID in worker evidence for later trace/audit correlation.
`WorkerExecutionBroker` fails closed when a worker is unavailable, cannot
satisfy the task policy, or cannot enforce a declared CPU/memory/disk/process
limit. The Docker worker currently enforces CPU-time, memory, and
process-count limits; disk limits remain an explicit blocked capability until
a worker backend can enforce them portably.

The production factory selects the worker through
`agent_execution_worker` (`local` by default, `container` or `wsl` explicitly).
The selected worker's executor is bound into the broker, so capability checks
and process launch cannot diverge. Worker attestation is attached to both
blocked and terminal execution evidence, allowing traces to prove which worker
and image actually ran a task.

For a container rollout, configure a pre-pulled image that contains the
project toolchains, for example:

```dotenv
AGENT_EXECUTION_WORKER=container
AGENT_CONTAINER_IMAGE=architectcoder-toolchain:2026.09
# Recommended for production: require an immutable image reference.
AGENT_CONTAINER_REQUIRE_DIGEST=true
```

If Docker is unavailable or the image cannot start, the task is blocked with
structured evidence; it is never retried on the host automatically.

### Resolver-task execution policy (M3.1)

Interactive `run_program` remains a deliberately small compatibility surface
with a host executable policy.  Resolver-owned `run_task` commands follow a
different contract: the selected worker resolves the first argv element on its
own PATH, while the broker still enforces literal argv, shell-interpreter
rejection, workspace boundaries, approval, network, and resource limits.  A
missing command is reported as `toolchain_unavailable`; it is not conflated
with an interactive executable-policy denial.  Consequently, adding a new
compiler or build system does not require changing a global language/tool
allowlist.  The native Windows executor implements this contract through
`validate_resolved_program`; isolated workers own the equivalent check inside
their execution boundary.

## Milestone 4 implementation foundation

`backend/app/agent_base/core/language_adapters.py` introduces an extensible
`LanguageAdapterRegistry` and normalized `ArtifactFacts` output. The built-in
Python adapter uses the standard AST module; the C++ adapter consumes the
matching `compile_commands.json` entry and invokes Clang's JSON AST dump with
literal argv through an injected Broker-backed runner. It extracts namespaces,
classes, enums, functions, methods, and fields while preserving source paths
and line numbers. Missing Clang, an invalid compilation database, or an absent
controlled runner is reported as typed diagnostics rather than silently
falling back to a Python parser or launching a host process.

`broker_command_runner()` adapts the asynchronous execution Broker to Clang's
synchronous parser callback. It wraps each AST request as a `TaskSpec`, so
network, timeout, cancellation, workspace, and worker policies are applied by
the same execution boundary as build/test tasks. The contract gate discovers
that runner from the session's `run_task` tool when available; standalone
collectors must inject one explicitly.

The registry is plugin-style: adding another language means registering an
adapter, not expanding an execution allowlist. The design-contract provider
now consumes registered non-Python adapters for source facts and conventional
`test_*` functions in test roots. Execution remains decoupled and continues to
use the resolver/Broker path above.

## Invariants

1. Agent-facing execution is expressed as a semantic task, not a shell string.
2. Process arguments are literal argv values; shell operators are rejected at
   the contract boundary.
3. A task records the toolchain and effective execution policy used to run it.
4. A timeout must carry a machine-readable reason (`llm_timeout`,
   `process_timeout`, `worker_timeout`, or another explicit category).
5. Unknown project configuration is a typed resolution failure, not a fallback
   to an unrelated language command.
6. Project manifests and build scripts are untrusted inputs; they do not grant
   host or network privileges.

## Evolution milestones

### M1 — Dynamic task resolution (landed)

Add `TaskResolver` and adapters for `pyproject.toml`, `package.json`, and
`CMakeLists.txt`. Replace the fixed `run_task` mapping while preserving the
legacy path behind a feature flag.

### M2 — Project task manifest (landed)

Support `.architectcoder/tasks.json` for explicit task semantics and toolchain
selection. Auto-detected tasks remain available, but explicit project config
takes precedence and is shown in the approval preview.

### M3 — Execution broker and sandbox (worker foundation landed)

Move process launch behind `ExecutionBroker`. The restricted local backend,
structured evidence, timeout/cancellation handling, worker capability
contract, and optional Docker worker are landed. Remaining M3 hardening is
portable disk quotas, stronger toolchain attestation, and deployment-specific
environment scrubbing.

### M4 — C++ vertical slice (adapter foundation landed)

CMake/CTest task resolution, a Clang-based language adapter,
`compile_commands.json` support, and registry integration into the design
contract provider are landed. A checked-in minimal C++ fixture now provides the
source/header/test/design shape, and a regression covers the existing
UML/source/test consistency rules with registered C++ facts. Running the
fixture against a real Clang-enabled toolchain image remains an environment
validation step rather than a host-side fallback.

### M5 — Additional language adapters

Cargo, Java/Maven or Gradle, Go, and .NET task adapters now resolve standard
build/test/lint/format operations into the same `TaskSpec` contract. They do
not add executable allowlist entries. Language-specific facts extraction still
follows the registry path and should be added with a regression fixture before
each adapter is considered production-ready.

### M6 — Release hardening

Add per-language evaluation suites, toolchain/version evidence, sandbox escape
tests, timeout classification, CI execution, and operational documentation.

## Compatibility and rollout

M0 is behavior-neutral. M1–M3 are rolled out per project through resolver and
worker capability checks and fall
back only when a resolver has explicitly reported `legacy_compatibility`; an
unknown task must never silently run `npm`, `pytest`, or another unrelated
default. Existing `run_program` remains a restricted compatibility tool until
the broker is the default execution path.

## 当前已落地的多语言特性（中文说明）

本节以当前代码和测试为准，说明多语言支持已经具备的能力。这里的“多语言”分为两层：

1. **工具链任务执行**：不同语言的构建、测试、检查和运行任务，都通过统一任务契约执行。
2. **源码结构分析**：不同语言的源码由各自的语言适配器解析，再归一化为统一事实模型。

### 1. 语言无关的任务契约

`TaskSpec`、`ToolchainProfile`、`ExecutionPolicy` 和 `ExecutionEvidence` 位于
`backend/app/runtime/task_contracts.py`，是 Agent、解析器和执行器之间的稳定边界。

- Agent 请求的是 `build`、`test`、`lint`、`format`、`typecheck`、`run` 或 `custom` 等语义任务，而不是 shell 命令。
- 任务携带 literal argv、工作目录、工具链标识、网络/审批策略、资源限制和预期输出。
- 执行结果统一记录状态、退出码、耗时、超时原因、沙箱、网络策略和诊断信息。
- 契约本身不启动进程、不读取宿主机 PATH，也不决定某个可执行文件是否全局可信。

### 2. 基于项目元数据的任务发现

`TaskResolver` 通过可注册的 `ProjectTaskAdapter` 发现项目根目录并解析任务。目前已覆盖：

- Node：`package.json`
- Python：`pyproject.toml`
- C/C++：`CMakeLists.txt`
- Rust：`Cargo.toml`
- Go：`go.mod`
- Java：Maven `pom.xml`、Gradle 构建文件
- .NET：`.sln`、`.csproj`
- 任意项目：`.architectcoder/tasks.json` 显式任务清单

解析器输出 `TaskSpec + ToolchainProfile`，不会直接执行命令。新增项目类型通过注册适配器完成，避免继续扩展中央语言分支。

`.architectcoder/tasks.json` 支持声明自定义任务、literal argv、工作目录、网络和审批策略、资源限制、工具链版本及预期输出。项目可以因此接入 Zig、Swift、Kotlin 或内部构建系统，而不需要修改 Agent 核心代码。

### 3. 去除宿主机语言/工具白名单依赖

解析器产生的任务不再要求预先加入 `python`、`cmake`、`cargo` 等全局可执行文件白名单。执行时由选定 worker 在自己的 PATH 中解析 argv 首元素，同时仍强制执行：

- literal argv 和 shell 控制字符校验；
- 禁止调用 `bash`、`sh`、`cmd`、`powershell` 等嵌套 shell；
- 工作目录必须位于受控 workspace roots；
- 网络、审批和资源限制策略；
- 工具链缺失时返回 `toolchain_unavailable`，不静默回退到 Python 或 Node 命令。
- 当项目声明工具链版本时，Broker 会在目标 Worker 内执行受控的 `--version` 探测，并把声明版本、实际版本、匹配结果和探测状态写入执行证据；探测失败会记录诊断，但不会掩盖任务本身的执行结果。

版本校验策略通过配置项控制：

```dotenv
# off | observe | warn | block
AGENT_TOOLCHAIN_VERSION_POLICY=observe
# compatible（默认允许 18 匹配 18.1.8）| exact
AGENT_TOOLCHAIN_VERSION_MATCH_MODE=compatible
```

`observe` 只记录证据，`warn` 在证据中标记告警但继续执行，`block` 在启动任务前阻断版本缺失或不匹配的任务。建议开发环境使用 `observe`，CI 使用 `warn`，发布环境再启用 `block`。

因此，增加新的编译器或包管理器通常只需要项目任务声明或工具链环境准备，不需要修改全局白名单。

### 4. 统一执行边界与 Worker 能力证明

`ExecutionBroker` 负责所有解析任务的实际执行，当前提供本地受限执行、WSL Worker 和 Container Worker：

- 本地 Broker 负责路径校验、进程启动、取消、超时、输出上限和证据生成。
- Worker 负责预检、隔离能力和资源限制能力证明。
- Worker 不满足网络、文件系统或资源限制要求时直接阻断（fail-closed）。
- Container Worker 使用限定 workspace 挂载、`--network none` 和 `--pull never`，避免隐式联网或宿主机回退。
- Worker 的 ID、能力和容器镜像信息会写入执行证据，便于 Trace 和审计。

`WorkerExecutionBroker` 与本地 Broker 采用组合关系：Worker 策略与进程生命周期职责分离，后续替换容器、远程执行或其他沙箱实现时不需要改变任务解析层。

### 5. 语言源码分析适配器

`LanguageAdapterRegistry` 为源码结构分析提供插件式入口，统一接口为“是否支持文件 + 提取 `ArtifactFacts`”。

- Python 适配器使用标准库 `ast`，提取模块、类、函数、方法和导入关系。
- C/C++ 适配器读取匹配的 `compile_commands.json`，通过 Clang JSON AST 提取命名空间、类、枚举、函数、方法和字段。
- C++ 的 Clang 调用通过 Broker-backed runner 执行，不直接启动宿主机子进程。
- 解析失败、编译数据库缺失、Clang 不可用或 runner 未注入，都会转为结构化诊断。
- 上层契约检查和知识图谱只消费归一化后的 `ArtifactFacts`，不依赖具体语言 AST 类型。

当前内置源码 AST 适配器是 Python 和 C/C++；其他语言可沿用同一注册接口增加解析器，而无需修改 ContractHarness 主流程。

### 6. 契约检查、验证与证据闭环

设计契约检查通过注册的语言适配器判断变更是否涉及源代码，并通过显式注入的语言 runner 执行编译器分析。ContractGate 不再深入访问 Agent 工具的私有字段。

验证子 Agent 的运行时判断也改为依据解析后的任务类型：项目声明的自定义验证任务可以执行，格式化任务按策略阻止；旧版 schema 和 fallback 仍保持兼容。

所有执行结果都会进入 `ExecutionEvidence`、`ToolResult`、verification checkpoint 和 Trace，能够区分成功、失败、策略阻断、工具链缺失、取消和超时。

### 7. 新增语言/工具链的接入方式

新增一种语言时，推荐按以下顺序接入：

1. 为项目构建系统提供 `ProjectTaskAdapter`，或增加 `.architectcoder/tasks.json`。
2. 确认任务输出的 argv、工具链身份和资源/网络策略符合任务契约。
3. 如需源码结构理解，实现并注册对应 `LanguageAdapter`。
4. 通过 Broker-backed runner 接入编译器或 AST 工具，禁止绕过执行边界直接调用 subprocess。
5. 增加该语言的任务、AST、工具链缺失、超时和隔离能力测试。

接入完成后，Agent 的任务接口、执行安全策略、契约检查和 Trace 记录均可复用现有实现。

### 8. 当前边界与后续工作

- 多语言任务执行已经是语言无关的；真正的“源码理解”仍取决于是否提供对应语言适配器。
- 目前需要继续补充 Java、Go、Rust、.NET 等语言的源码事实提取适配器和垂直切片测试。
- 仍需完善跨平台 wrapper 处理、磁盘配额、工具链版本证明、环境清理和 CI 中的真实容器验证。
