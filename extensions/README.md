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

The complete built-in implementations live in the corresponding extension
package:

- `orchestration/`: contracts, planner/orchestrator and provider adapter
- `memory/`: SQLite memory manager, lifecycle, policies, models and provider
- `trace/`: trace writer, reader, replay engine and provider adapter
- `evals/`: evaluation models, catalog, runner, checkers, batches and provider
- `knowledge_graph/`: graph models, SQLite database, builder, retriever, v2 tools and provider

Only stable application-facing ports, generic tool/runtime infrastructure and
the central `PluginManager` remain in `backend/`. The old paths under
`backend/memory_system`, `backend/knowledge_graph`, `backend/app/evals`,
`backend/app/trace` and `backend/app/agent_base/orchestration` are compatibility
facades; they contain no plugin implementation logic.
