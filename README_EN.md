<div align="center">

# ArchitectCoder

**Turn Ideas into Architecture, Let Architecture Drive Development**

**English** | [中文](README.md)

</div>

ArchitectCoder is an AI-assisted development workbench with UML as its design entry point. It supports **Class Diagrams**, **Sequence Diagrams**, and **Component Diagrams**, connecting design, code generation, testing, repair, and replay into one traceable workflow. It includes the **DevAgent development assistant**, **Capability Benchmark Center**, **TestHub Test Center**, **Trace Viewer & Replay**, **Knowledge Graph**, **Memory System**, and the **BaseAgents framework**.

## Why ArchitectCoder?

- **Design as source of truth**: Architecture diagrams drive code generation, verification, and testing.
- **AI-powered validation**: cross-diagram consistency checks with fuzzy-match auto-repair — machines catch what humans miss.
- **Natural language to running code**: Describe requirements → auto-generate UML → the Agent writes code and runs real pytest → auto-repair on failure.

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

The bottom-right robot button opens the floating chat panel. The production **DevAgent** (implemented with the ReActAgent runtime) handles every message and supports coordinated development from UML design to code implementation. Depending on the task, it can analyze, generate, modify, and validate code. In v3.0 the fixed pipeline orchestration remains removed — the Agent holds controlled file-system tools and plans and executes its own development steps:

- **File system primitives**: `read_file` / `write_file` / `edit_file` / `glob` / `bash` — reading code, writing code, running pytest, and fixing failures are all orchestrated by the Agent itself
- **Task planning**: `todo_write` maintains a session task list — plan before multi-step work, update status as you go
- **General-purpose sub-agent** `spawn_subagent`: delegates self-contained sub-tasks (exploration, summarization, isolated changes) to a flash-model sub-agent holding only a restricted file system toolset, returning just the conclusion to keep the main context lean; the review channel is passed through, so delegated work cannot bypass human approval
- **Persistent task system**: a task DAG (create / depend / claim / complete) persisted to disk + git worktree isolation — long tasks can be decomposed and resumed across sessions
- **Human review**: UML design changes go through `submit_uml_review` diff approval; sensitive `bash` commands (force-delete, process kill, `git reset --hard`, etc.) pause for an approve/reject review card before running, while high-risk commands (format, partition, boot-record writes) are denied outright — keeping AI development within guardrails
- **Streaming progress**: every tool call, arguments, and results pushed in real time
- **Interrupt control**: stop the Agent at any time
- **Multi-session persistence**: create / switch sessions; conversation history survives refresh; session logs saved to disk (Markdown + JSONL trace)
- **Memory system**: cross-session memory — tasks are archived on completion and recalled by relevance into new tasks, with BM25 full-text search + jieba tokenization

### DevAgent Capability Benchmark

The evaluation system now covers only the production **DevAgent** path. Legacy / standalone ReAct evaluation routes are no longer maintained, preventing different Agent paths from contaminating DevAgent measurements. Each case is defined by controlled JSON, bound to a fixed project fixture and manifest, and executed in an isolated workspace:

- **Case catalog**: `backend/app/evals/cases/`, currently 16 cases organized into the `baseline`, `p0`, `p1`, `p2`, and `diagnostic` suites.
- **Execution flow**: `case → fixture/project manifest → DevAgent → hard checkers/checkers → Trace + JSONL result`.
- **Deterministic checks**: pytest, UML validity/structure/method/sequence checks, file existence/content checks, and protected-path integrity checks.
- **Runtime limits**: every case can configure maximum seconds, Tool Calls, and Total Tokens; results retain status, score, duration, model, token/tool usage, Trace ID, and checker details.
- **Baseline snapshot**: the current baseline is version `a1122e8`: 10 of 16 cases passed, 1 failed, and 5 timed out; pass rate 62.5%, average score 66.67%, with 6,639,458 total tokens and 602 tool calls. Full metrics are recorded in `docs/devagent-evaluation-baseline-2026-09-01.md`.
- **Version identity**: the Evaluation Center automatically reads the current Git branch and HEAD commit and uses `branch@commit` as the version. An uncommitted working tree is marked `dirty`.
- **Run and archive**: the Evaluation Center supports one-click suite runs, live batch/result inspection, and one-click archiving for completed batches or the baseline snapshot under `temp/evals/archives/`. The CLI can run all cases or a selected suite:

  ```bash
  cd backend
  python -m app.evals.cli
  python -m app.evals.cli --suite p0
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
- **Deterministic replay**: `mock` mode replays from the recording with zero network access (with cursor-consistency checks); `rerun` mode re-runs the real LLM while tools stay mocked from the recording — for prompt debugging and regression comparison

### Knowledge Graph

SQLite graph database + FTS5 full-text index, automatically rebuilt on project save, giving the AI assistant structured project understanding:

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
| LLM | DeepSeek API (v4-pro + v4-flash) |
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
│   ├── app/
│   │   ├── api/                    # REST + WebSocket routes (files/llm/optimize_v2/testhub/trace/metrics/evals)
│   │   ├── core/                   # Config / auth / security
│   │   ├── models/                 # Pydantic data models
│   │   ├── services/               # LLM / optimization engine V2 / layout / trace replay / sessions
│   │   ├── evals/                  # DevAgent cases, runner, baseline, and archives
│   │   ├── agent_base/             # BaseAgents framework (core/agents/tools)
│   │   │   └── tools/my_tools/     # File system primitives / todo / sub-agent / review
│   │   └── main.py
│   ├── knowledge_graph/            # Knowledge Graph system (SQLite + FTS5)
│   ├── memory_system/              # Cross-session memory system (SQLite + FTS5 + jieba)
│   ├── requirements.txt
│   └── .env
├── docs/                           # Design docs, evaluation baseline, and system archives
├── skills/uml-design-guide/         # UML design guides (SkillTool pack + optimization pipeline)
├── generated/                      # Generated code output (src/ + test/)
├── temp/                           # Runtime temp files (not committed)
├── .claude/                        # Claude Code configuration
└── README.md
```

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

Optional settings include `DEEPSEEK_MODEL`, `DEEPSEEK_MODEL_FLASH`, and `SUB_AGENT_MODEL` for model routing. If `INTERNAL_API_TOKEN` is set, configure the same value as `VITE_API_TOKEN` in `frontend/.env.local`. After dependencies are installed, Windows users can also run `start.bat` to launch both backend and frontend.

## API and Development Checks

- Backend REST APIs use the `/api` prefix and cover file operations, LLM access, global optimization, TestHub, Trace, Agent metrics, and DevAgent evaluations.
- The conversational Agent uses WebSocket: `/api/ws/chat`.
- API docs: open `http://localhost:8001/api/docs` after starting the backend.
- Unit tests: `cd backend && python -m pytest -q`.
- Run the full DevAgent evaluation catalog: `cd backend && python -m app.evals.cli`.
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
