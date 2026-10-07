# Extensions

This directory is the repository-level home for plugin implementations and
provider entry points. A plugin's operational logic stays inside its own
subdirectory here, keeping the main application flow independent of concrete
implementations.

Each managed extension exposes a `create(**kwargs)` factory and is loaded using
`module:factory` syntax through `app.agent_base.core.plugins.PluginManager`.
The built-in entry points are:

- `extensions.orchestration:create`
- `extensions.memory:create`
- `extensions.trace:create`
- `extensions.evals:create`
- `extensions.knowledge_graph:create`
- `extensions.design_contract:create`

The complete built-in implementations live in the corresponding extension
package:

- `orchestration/`: architecture-aware graph exploration, partitioning and scheduling
- `memory/`: SQLite memory manager, lifecycle, policies, models and provider
- `trace/`: trace writer, reader, replay engine and provider adapter
- `evals/`: evaluation models, catalog, runner, checkers, batches, provider and plugin-owned API routers
- `knowledge_graph/`: graph models, SQLite database, builder, retriever, v2 tools and provider
- `design_contract/`: read-only UML, Python AST and test fact collectors, normalized mappings,
  and optional knowledge-graph relationship enrichment through the stable provider port

Only stable application-facing host APIs, generic tool/runtime infrastructure and
the central `PluginManager` remain in `backend/`. Trace contracts live in
`agent_base/host_api/tracing.py`, loading and fault isolation in
`agent_base/adapters/tracing.py`, event routing in `agent_base/core/observability.py`,
and session lifetimes in `runtime/trace_session.py` (all under `backend/app`).
Tool result continuation uses `runtime/tool_outputs.py` independently of tracing.
The old `backend/app/trace` package has been removed; no compatibility facade is retained.
