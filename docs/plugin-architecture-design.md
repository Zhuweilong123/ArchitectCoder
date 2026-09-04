# Plugin Architecture Design Archive

> Version: v3.2
> Status: Implemented baseline
> Date: 2026-09-04
> Related commits: `fa3b507` (`v3.2: Centralize Configuration Management`),
> `9c439ed` (`feat: expose knowledge graph tools through plugin switch`)

## 1. Purpose

This document archives the plugin architecture introduced during the v3.2
architecture decoupling work.

The design separates the Agent application's stable runtime flow from domain
implementations that may be replaced, disabled, or extended independently.
The application depends on contracts and lifecycle boundaries. Concrete
implementations are kept under the repository-level `extensions/` directory.

The architecture currently manages five optional plugin slots:

1. orchestration
2. memory
3. trace
4. evals
5. knowledge_graph

## 2. Design goals

- Keep plugin implementation code outside the main application flow.
- Give every optional capability one consistent loading mechanism.
- Select implementations through configuration rather than hard-coded imports.
- Allow a plugin to be disabled without removing application code.
- Prevent plugin failures from taking down the main Agent execution path when
  a domain fallback is available.
- Keep domain contracts explicit so providers can be local, remote, or hosted.

## 3. Repository boundaries

```text
backend/
├── config/
│   ├── settings.py        # Application Settings and environment overrides
│   ├── agent_config.py     # Agent instance configuration
│   └── __init__.py         # Unified configuration exports
├── app/
│   ├── agent_base/
│   │   ├── assembly.py     # Production Agent composition boundary
│   │   ├── execution_summary.py # Transport-neutral run summaries
│   │   └── core/
│   │       ├── plugins.py      # Central plugin lifecycle manager
│   │       ├── memory.py       # Memory port and fallback
│   │       ├── evals.py        # Evals port and fallback
│   │       ├── orchestration.py # Orchestration port and fallback
│   │       └── knowledge_graph.py # Knowledge-graph port and fallback
│   ├── services/
│   │   ├── agent_execution.py # Transport-neutral Agent execution
│   │   └── agent_chat_ws.py   # WebSocket session/transport adapter
│   └── trace/
│       └── tracing.py      # Trace port and runtime session bridge
└── .env                    # Local runtime configuration, not committed

extensions/
├── orchestration/          # Planner and orchestration provider
├── memory/                 # SQLite memory implementation
├── trace/                  # JSONL writer, reader and replay implementation
├── evals/                  # Evaluation catalog, runner and provider
└── knowledge_graph/        # SQLite graph, retrieval and graph tools
```

The `backend` layer owns stable contracts, generic runtime infrastructure and
the central manager. The `extensions` layer owns operational plugin logic.

Agent construction is centralized in `app/agent_base/assembly.py`, so the
interactive WebSocket adapter and the Evals adapter share the same production
tool, memory, prompt, and budget assembly. A single Agent run is coordinated
by `app/services/agent_execution.py` through an injected async `send(payload)`
callback; it does not depend on FastAPI or WebSocket types. The WebSocket
module owns session state, message parsing, and transport events only.

`backend/app/trace` is intentionally retained as the Trace port because the
core Agent runtime needs the session lifecycle, event bridge and coroutine
local span context. It does not own the concrete storage or replay logic.

## 4. Central plugin manager

The central manager is implemented in:

```text
backend/app/agent_base/core/plugins.py
```

Each managed slot is described by a `PluginSpec` containing:

- logical plugin name
- enabled setting name
- provider setting name
- default provider entry point
- required provider methods

Provider entry points use the following format:

```text
module.path:factory
```

For example:

```text
extensions.memory:create
```

The manager performs the following lifecycle:

```text
domain loader
    ↓
PluginManager.load(name)
    ↓
read enabled/provider settings
    ↓
disabled? ── yes ──> domain no-op fallback
    ↓ no
import module and resolve factory
    ↓
create provider instance
    ↓
validate required methods
    ↓
loaded provider or unavailable fallback
```

Loading is lazy. A provider is loaded when its domain loader is called, rather
than importing every extension during application startup.

## 5. Built-in plugin slots

| Slot | Core port | Default provider | Default enabled state |
|---|---|---|---|
| orchestration | `load_orchestrator` | `extensions.orchestration:create` | true |
| memory | `load_memory` | `extensions.memory:create` | true |
| trace | `load_trace` | `extensions.trace:create` | true |
| evals | `load_evals` | `extensions.evals:create` | true |
| knowledge_graph | `load_knowledge_graph` | `extensions.knowledge_graph:create` | true |

### Orchestration

The optional orchestration provider prepares a plan and runtime directives.
The core can use `NoOpOrchestrator` when the planner is disabled or unavailable.

`AGENT_MAIN_SUBAGENT_ENABLED` is separate from the orchestration plugin switch.
It controls whether the main Agent exposes its subagent capability; it does not
select the optional planner provider.

### Memory

The default provider is the SQLite implementation in `extensions/memory`.
The core only depends on the memory port for recall, archive and reinforcement.
`NoOpMemory` allows the Agent to continue without cross-task memory.

### Trace

The default provider is the JSONL implementation in `extensions/trace`.
The core Trace port owns `TraceSession`, event bridging and coroutine-local
span tracking. The provider owns persistence, querying and replay.

### Evals

The default provider exposes the local evaluation MVP. The Evals port keeps
case lookup, execution and result access independent from the API layer.
External CI or hosted evaluation services can implement the same provider
contract.

### Knowledge graph

The default provider is the local SQLite graph implementation. Graph building,
retrieval, impact analysis, diffing and v2 tools are kept in
`extensions/knowledge_graph`. The same `AGENT_KNOWLEDGE_GRAPH_ENABLED` switch
also controls whether the default Agent-facing graph tools are registered. The
local provider exposes the bounded `get_project_map`, `find_nodes` and
`expand_neighbors` tools through its optional tool capability; the design-code
comparison tool remains an explicit opt-in at the composition layer.

## 6. Configuration model

Configuration definitions are centralized under:

```text
backend/config/
```

`settings.py` defines application-level configuration. `agent_config.py`
defines the Agent instance-level `AgentConfig` model. They are intentionally
separate: application deployment settings should not be confused with a
single Agent object's runtime options.

The runtime values are loaded from `backend/.env` when the backend starts from
its normal working directory. Environment variables override the defaults in
`backend/config/settings.py`.

The checked-in `backend/.env.example` uses a conservative runtime profile:
orchestration and knowledge-graph plugins are disabled there by default, while
the other three plugin slots remain enabled. A local `backend/.env` can enable
either feature without changing the code defaults.

The five plugin slots use these settings:

| Slot | Enabled setting | Provider setting |
|---|---|---|
| orchestration | `AGENT_ORCHESTRATION_ENABLED` | `AGENT_ORCHESTRATOR_PROVIDER` |
| memory | `AGENT_MEMORY_ENABLED` | `AGENT_MEMORY_PROVIDER` |
| trace | `AGENT_TRACE_ENABLED` | `AGENT_TRACE_PROVIDER` |
| evals | `AGENT_EVALS_ENABLED` | `AGENT_EVALS_PROVIDER` |
| knowledge_graph | `AGENT_KNOWLEDGE_GRAPH_ENABLED` | `AGENT_KNOWLEDGE_GRAPH_PROVIDER` |

Example:

```env
AGENT_MEMORY_ENABLED=false
AGENT_TRACE_PROVIDER=extensions.trace:create
```

A plugin is treated as disabled when its enabled flag is false or its provider
value is empty, `none`, `noop`, or `disabled`. Configuration changes require a
backend restart because application settings are cached for the process.

## 7. Failure and fallback behavior

Provider loading failures are recorded as `unavailable` and logged. Domain
loaders then return their no-op implementation where available:

- orchestration → `NoOpOrchestrator`
- memory → `NoOpMemory`
- trace → `NoOpTraceProvider`
- evals → `NoOpEvalProvider`
- knowledge_graph → `NoOpKnowledgeGraphProvider`

Trace and memory additionally wrap provider operations with resilience guards
so a storage or query failure does not break the Agent turn. The fallback is a
reliability boundary, not a substitute for provider health monitoring.

The manager tracks the last known state for each slot:

```text
not_loaded | loaded | disabled | unavailable
```

`PluginManager.status()` currently exposes this state only to internal Python
callers. There is not yet a public plugin administration API or frontend
configuration panel.

## 8. Extension provider contract

An extension should expose a `create(**kwargs)` factory from its package entry
point. The factory returns an object implementing the domain port.

Minimal example:

```python
class CustomMemoryProvider:
    async def recall(self, request):
        ...

    async def archive(self, request):
        ...

    async def reinforce(self, memory_ids, project_id=""):
        ...


def create(*, llm, settings, **kwargs):
    return CustomMemoryProvider()
```

Provider code may depend on extension-specific libraries and storage. Core
code must depend only on the corresponding port and must not import a concrete
provider directly.

## 9. Ownership rules

### Belongs in `backend/config`

- application settings loaded from environment variables
- Agent-level configuration models
- deployment paths and runtime limits
- plugin enable/provider selection
- shared LLM and infrastructure defaults when they are made configurable

### Belongs in `backend/app`

- stable ports and protocols
- generic lifecycle and runtime orchestration
- security enforcement and capability boundaries
- API and transport adapters

### Belongs in `extensions`

- storage implementations
- provider-specific algorithms
- domain-specific persistence and serialization
- evaluation runners and checkers
- trace formats and replay engines
- knowledge-graph indexes and graph tools

Security deny lists, domain schemas, SQL DDL, Trace event names and algorithm
constants should remain close to their enforcement or domain implementation;
they are not automatically deployment configuration.

## 10. Current known gaps

The v3.2 baseline establishes the plugin boundary but does not claim that all
runtime constants are configurable. The following areas remain candidates for
future configuration consolidation:

- LLM provider matrix and provider-specific environment variable mapping in
  `backend/app/agent_base/core/llm.py`
- duplicated context-budget defaults in `backend/app/services/context_manager.py`
- server host/port and session TTL
- repeated LLM timeout, retry and token defaults
- the static `DEFAULT_PLUGIN_SPECS` registry in `plugins.py`

These are follow-up configuration tasks and are intentionally not hidden by
this archive.

## 11. Verification baseline

After the v3.2 decoupling and configuration relocation, the backend full test
suite passed:

```text
271 passed
```

The test suite includes Agent, orchestration, memory, Trace, Evals,
knowledge-graph, plugin-manager and infrastructure coverage.
