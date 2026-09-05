<div align="center">

# ArchitectCoder

**Turn Ideas into Architecture, Let Architecture Drive Development**

**English** | [中文](README_ZH.md)

</div>

ArchitectCoder is an AI-assisted development workbench with UML as its design entry point. It supports **Class Diagrams**, **Sequence Diagrams**, and **Component Diagrams**, connecting design, code generation, testing, repair, and replay into one traceable workflow. It includes the **DevAgent development assistant**, **Capability Benchmark Center**, **TestHub Test Center**, **Trace Viewer & Replay**, **Knowledge Graph**, **Memory System**, and the **BaseAgents framework**.

![ArchitectCoder workspace](workSpace.PNG)

## Why ArchitectCoder?

- **Design as source of truth**: Architecture diagrams drive code generation, verification, and testing.
- **AI-powered validation**: cross-diagram consistency checks with fuzzy-match auto-repair — machines catch what humans miss.
- **Natural language to running code**: Describe requirements → auto-generate UML → human diff review → the Agent implements code and runs real pytest → auto-repair on failure.

## Core Capabilities

### Multi-Diagram Editor

| Diagram Type | Core Elements | Interaction |
|-------------|--------------|-------------|
| **Class Diagram** | Classes + 6 relationship types | Double-click to add / drag ports for relationships |
| **Sequence Diagram** | Lifelines + 5 message types | Double-click to add lifeline / click A→B for messages |
| **Component Diagram** | Components + dependencies + sub-components | Double-click to add / double-click inside for sub-components |

- Undo/Redo (50 steps), Zoom (toolbar + Ctrl+Scroll), Grid snapping
- Ctrl+C/V copy-paste, Ctrl+S save
- Property panel, Space+Drag to pan

### Project Management

- `.umlproj` project file: one project, multiple diagrams of different types
- **Component-Diagram hierarchy**: `Project → Component → Class/Sequence Diagram` three-tier organization. Right-click a component to create/switch linked diagrams
- Toolbar groups diagrams by type with color-coded dropdowns (orange/blue/green)
- Legacy `.uml` files auto-wrapped into projects

### AI Development Assistant

The bottom-right robot button opens the floating chat panel. The production **DevAgent** (implemented with the ReActAgent runtime) handles every message and supports coordinated development from UML design to code implementation. Depending on the task, it can analyze, generate, modify, and validate code. In v3.2, the main loop remains a direct ReAct workflow, with optional task orchestration and a bounded read-only strategy worker available through a pluggable provider:

- **File system primitives**: `read_file` / `write_file` / `edit_file` / `glob` / `bash` — reading code, writing code, running pytest, and fixing failures are all orchestrated by the Agent itself
- **Task planning**: `todo_write` maintains a session task list — multi-step tasks create 3–5 concise todos, include verification, and update status as work progresses
- **Design-first workflow**: design-impacting requirements follow `inspect → update UML → validate → human review → implementation → verification`; business code should be changed only after the affected design is accepted. Implementation-only tasks can proceed directly.
- **Optional task orchestration**: a planner can create a bounded plan, delegate read-only cross-artifact exploration, and hand structured evidence back to the main Agent; disabling the provider falls back to the direct ReAct path
- **Controlled sub-agent** `spawn_subagent`: only explicitly enabled strategy work is delegated with a separate context and restricted read-only tools; the main Agent remains responsible for edits and verification
- **Human review**: UML design changes go through `submit_uml_review` diff approval before the implementation phase; sensitive `bash` commands (force-delete, process kill, `git reset --hard`, etc.) pause for an approve/reject review card before running, while high-risk commands (format, partition, boot-record writes) are denied outright — keeping AI development within guardrails
- **UML 2.5.1 skill pack**: on-demand class, sequence, component, and cross-diagram guides, including a small loadable `.umlproj` reference case; advanced project cases remain outside the skill pack
- **Streaming progress**: every tool call, arguments, and results pushed in real time
- **Interrupt control**: stop the Agent at any time
- **Multi-session persistence**: create / switch sessions; conversation history survives refresh; session logs saved to disk (Markdown + JSONL trace)
- **Pluggable memory system**: cross-session memory is archived on completion and recalled by relevance into new tasks; the default SQLite/BM25 provider can be disabled or replaced without changing the Agent loop

### DevAgent Capability Benchmark

The evaluation system now covers only the production **DevAgent** path. Legacy / standalone ReAct evaluation routes are no longer maintained, preventing different Agent paths from contaminating DevAgent measurements. Each case is defined by controlled JSON, bound to a fixed project fixture and manifest, and executed in an isolated workspace:

- **Case catalog**: `backend/evals/cases/`, currently 18 cases organized into the `baseline`, `p0`, `p1`, `p2`, `diagnostic`, and `trace-3.1` suites.
- **Execution flow**: `case → fixture/project manifest → DevAgent → hard checkers/checkers → Trace + JSONL result`.
- **Deterministic checks**: pytest, UML validity/structure/method/sequence checks, file existence/content checks, and protected-path integrity checks.
- **Runtime limits**: every case can configure maximum seconds, Tool Calls, and Total Tokens; results retain status, score, duration, model, token/tool usage, Trace ID, and checker details.
- **Baseline snapshot**: the current baseline is version `a1122e8`: 10 of 16 cases passed, 1 failed, and 5 timed out; pass rate 62.5%, average score 66.67%, with 6,639,458 total tokens and 602 tool calls. Full metrics are recorded in `docs/devagent-evaluation-baseline-2026-09-01.md`.
- **Version identity**: the Evaluation Center automatically reads the current Git branch and HEAD commit and uses `branch@commit` as the version. An uncommitted working tree is marked `dirty`.
- **Run and archive**: the Evaluation Center supports one-click suite runs, live batch/result inspection, and one-click archiving for completed batches or the baseline snapshot under `temp/evals/archives/`. The CLI can run all cases or a selected suite:

  ```bash
  python -m extensions.evals.cli
  python -m extensions.evals.cli --suite p0
  ```

  The CLI returns a non-zero exit code when any case fails or times out. This means the evaluation result is not all green; it does not mean that the evaluation framework failed to start.

### Global Optimization

Click "Global Optimization" in the toolbar, describe your needs:

- **Generate from scratch**: no blank diagrams needed — LLM auto-creates all required diagrams and tabs
- **V2 direct engine**: scope analysis (lightweight model) → single LLM pass (pro model) → programmatic cross-diagram validation + auto-repair + auto-layout
- **Streaming render**: elements appear on canvas in real time, cancelable mid-stream
- **Multi-diagram Diff**: per-diagram tab comparison, accept or iterate independently

### TestHub Test Center

Excel test-case-driven test code generation:

- **Case management**: load Excel test case sheets from the testHub directory, edit inline, and save back
- **Test code generation**: full or incremental (changed cases only) modes; generated test code is viewable, copyable, and downloadable in the "Test Code" tab
- **Review audit trail**: view / edit / generate / accept / reject operations are uniformly logged to `dev_review.txt`

### Trace Viewer & Replay

- **Full recording**: every Agent session is persisted as a JSONL trace (LLM requests/responses + step-by-step tool call events)
- **TraceViewer**: a drawer-style UI for browsing historical sessions, expanding each tool call's arguments and results
- **Long prompt inspection**: oversized LLM prompts and tool schemas stay compact by default and can be expanded to inspect the complete content in a scrollable panel
- **Deterministic replay**: `mock` mode replays from the recording with zero network access (with cursor-consistency checks); `rerun` mode re-runs the real LLM while tools stay mocked from the recording — for prompt debugging and regression comparison

### Knowledge Graph

SQLite graph database + FTS5 full-text index, rebuilt on project save when the
knowledge-graph plugin is enabled, giving the AI assistant structured project understanding:

- **Three knowledge layers**: Project → Entity → Relationship (inheritance/composition/dependency + design-code mapping + test coverage)
- **Dual-source build**: design layer (UML JSON auto-sync) + code layer (AST parsing of source directories)

### Auto-Layout Engine

LLM-generated element positions auto-computed; manually positioned elements fully preserved:
- Class Diagram: inheritance layering + grid layout
- Sequence Diagram: lifelines evenly spaced + messages ordered vertically
- Component Diagram: flow layout with auto-wrap

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + TypeScript + AntV X6 + Zustand + Ant Design 5 |
| Backend | FastAPI (Python) + WebSocket |
| Agent | BaseAgents (ReActAgent, native function calling) |
| LLM | DeepSeek API (one fixed model per session, configured by `DEEPSEEK_MODEL`) |
| Knowledge Graph | SQLite + FTS5 |
| Memory System | SQLite + FTS5 + jieba |
| Testing | pytest (real subprocess execution) + openpyxl (Excel cases) |
| Build | Vite |

## Project Structure

```
ArchitectCoder/
├── frontend/                       # React frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── Canvas/             # Three diagram editors (Class/Sequence/Component)
│   │   │   ├── AgentChat/          # AI Assistant floating panel (WebSocket)
│   │   │   ├── PropertyPanel/      # Property editing panel
│   │   │   ├── Toolbar/            # Toolbar
│   │   │   ├── CodeViewer/         # Code viewer (Monaco)
│   │   │   ├── DiffViewer/         # UML diff comparison view
│   │   │   ├── TestCaseViewer/     # TestHub Excel case management
│   │   │   ├── TestCodeViewer/     # Generated test code viewer
│   │   │   └── TraceViewer/        # Session trace visualization + replay
│   │   ├── stores/                 # Zustand state management
│   │   ├── services/               # API service layer (incl. agentChat WebSocket)
│   │   ├── types/                  # TypeScript type definitions
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/                        # FastAPI backend
│   ├── config/                      # Settings and AgentConfig
│   ├── app/
│   │   ├── api/                    # REST + WebSocket routes (files/llm/optimize_v2/testhub/trace/metrics/evals)
│   │   ├── core/                   # Authentication / security
│   │   ├── models/                 # Pydantic data models
│   │   ├── services/               # LLM / optimization engine V2 / layout / trace replay / sessions
│   │   ├── agent_base/             # BaseAgents framework (core/agents/tools)
│   │   │   └── tools/my_tools/     # File system primitives / todo / sub-agent / review
│   │   └── main.py
│   ├── evals/                      # Versioned evaluation cases and fixtures
│   ├── tests/                      # Unit and integration tests
│   ├── requirements.txt
│   └── .env
├── docs/                           # Design docs, evaluation baseline, and system archives
├── extensions/                     # Unified provider implementations and entry points
│   ├── orchestration/              # Orchestration providers
│   ├── memory/                     # Memory providers
│   ├── trace/                      # Trace providers
│   ├── evals/                      # Evaluation providers and CLI
│   └── knowledge_graph/            # Knowledge graph providers
├── skills/uml-design-guide/         # UML design guides (SkillTool pack + optimization pipeline)
├── project/                        # Project code output (src/ + test/)
├── temp/                           # Runtime temp files (not committed)
├── .claude/                        # Claude Code configuration
├── README.md                       # English project documentation (default)
└── README_ZH.md                    # Chinese project documentation
```

## Extension layout

All plugin implementation code is physically kept under `extensions/`:
`orchestration/`, `memory/`, `trace/`, `evals/`, and `knowledge_graph/`.
The main flow loads implementations through the central plugin manager, while
stable ports and generic runtime infrastructure remain under `backend/app/`.
Application and Agent configuration are centralized under `backend/config/`.

Each extension exposes a `module:factory` entry point, for example
`extensions.memory:create`. Plugins can be enabled, disabled, or replaced by
setting the corresponding `AGENT_*_ENABLED` and `AGENT_*_PROVIDER` variables in
`backend/.env`. See [Plugin Architecture Design Archive](docs/plugin-architecture-design.md)
for the complete design, lifecycle, fallback behavior, and ownership rules.

Knowledge-graph tools use the same `AGENT_KNOWLEDGE_GRAPH_ENABLED` switch as
the graph provider. When enabled, the main Agent receives the default
`get_project_map`, `find_nodes`, and `expand_neighbors` tools; when disabled,
no knowledge-graph tools are registered.

## Quick Start

```bash
# Backend
cd backend
python -m pip install -r requirements.txt
# Create backend/.env and set at least: DEEPSEEK_API_KEY=your-key
python -X utf8 -m app.main          # http://localhost:8001

# Frontend
cd frontend
npm install
npm run dev                           # http://localhost:3000
```

Optional settings include `DEEPSEEK_MODEL` (one fixed model per session), the `AGENT_*_ENABLED` switches, and the `AGENT_*_PROVIDER` settings. Configuration definitions are centralized in `backend/config/settings.py` and `backend/config/agent_config.py`; provider entry points are managed through `backend/app/agent_base/core/plugins.py` and implemented under `extensions/`. Set a plugin enabled flag to `false`, or set its provider to `none`, `noop`, or `disabled`, to turn it off. Model routing and `SUB_AGENT_MODEL` are not used. If `INTERNAL_API_TOKEN` is set, configure the same value as `VITE_API_TOKEN` in `frontend/.env.local`. On Windows, command execution uses the configured WSL environment when available. After dependencies are installed, Windows users can also run `start.bat` to launch both backend and frontend.

## API and Development Checks

- Backend REST APIs use the `/api` prefix and cover file operations, LLM access, global optimization, TestHub, Trace, Agent metrics, and DevAgent evaluations.
- The conversational Agent uses WebSocket: `/api/ws/chat`.
- API docs: open `http://localhost:8001/api/docs` after starting the backend.
- Unit tests: `cd backend && python -m pytest -q`.
- Run the full DevAgent evaluation catalog from the repository root: `python -m extensions.evals.cli`.
- Production frontend build: `cd frontend && npm run build`.

Evaluation and runtime logs are written to `temp/`. Generated code and databases are runtime artifacts and are not committed.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+C / Ctrl+V | Copy / Paste |
| Ctrl+S | Save project |
| Delete | Delete selected |
| Ctrl+Scroll | Zoom |
| Space+Drag | Pan |

## Supported Programming Languages

Python, Java, TypeScript, JavaScript, C#, C++, Go, Rust, Ruby, Swift, Kotlin, PHP
