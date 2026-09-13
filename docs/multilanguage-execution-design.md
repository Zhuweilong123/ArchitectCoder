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
