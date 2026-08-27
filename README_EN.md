<div align="center">

# ArchitectCoder

**Turn Ideas into Architecture, Let Architecture Drive Development**

**English** | [中文](README.md)

</div>

Supports **Class Diagrams**, **Sequence Diagrams**, and **Component Diagrams** — from design to code generation, testing, and repair in a single integrated workflow. Built-in **AI Development Assistant (Conversational Agent)**, **TestHub Test Center**, **Trace Viewer & Replay**, **Knowledge Graph**, and the **BaseAgents framework**.

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

Floating chat panel (bottom-right robot button). A single ReActAgent (**design + code co-evolution**: evolve existing code rather than redesigning from scratch) handles every message. Since v2.0 the fixed pipeline orchestration is gone — the Agent holds native file system tools and plans its own development steps:

- **File system primitives**: `read_file` / `write_file` / `edit_file` / `glob` / `bash` — reading code, writing code, running pytest, and fixing failures are all orchestrated by the Agent itself
- **Task planning**: `todo_write` maintains a session task list — plan before multi-step work, update status as you go
- **General-purpose sub-agent** `spawn_subagent`: delegates self-contained sub-tasks (exploration, summarization, isolated changes) to a flash-model sub-agent holding only a restricted file system toolset, returning just the conclusion to keep the main context lean; the review channel is passed through, so delegated work cannot bypass human approval
- **Persistent task system**: a task DAG (create / depend / claim / complete) persisted to disk + git worktree isolation — long tasks can be decomposed and resumed across sessions
- **Human review**: UML design changes go through `submit_uml_review` diff approval; sensitive `bash` commands (force-delete, process kill, `git reset --hard`, etc.) pause for an approve/reject review card before running, while high-risk commands (format, partition, boot-record writes) are denied outright — keeping AI development within guardrails
- **Streaming progress**: every tool call, arguments, and results pushed in real time
- **Interrupt control**: stop the Agent at any time
- **Multi-session persistence**: create / switch sessions; conversation history survives refresh; session logs saved to disk (Markdown + JSONL trace)
- **Memory system**: cross-session memory — tasks are archived on completion and recalled by relevance into new tasks, with BM25 full-text search + jieba tokenization

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
uml_designer/
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
│   │   ├── api/                    # REST + WebSocket routes (files/llm/optimize_v2/testhub/trace)
│   │   ├── core/                   # Config / auth / security
│   │   ├── models/                 # Pydantic data models
│   │   ├── services/               # LLM / optimization engine V2 / layout / trace replay / sessions
│   │   ├── agent_base/             # BaseAgents framework (core/agents/tools)
│   │   │   └── tools/my_tools/     # File system primitives / todo / sub-agent / review
│   │   └── main.py
│   ├── knowledge_graph/            # Knowledge Graph system (SQLite + FTS5)
│   ├── memory_system/              # Cross-session memory system (SQLite + FTS5 + jieba)
│   ├── requirements.txt
│   └── .env
├── docs/                           # Design docs (BaseAgents / KG / memory / trace)
├── skills/uml-design-guide/         # UML design guides (SkillTool pack + optimization pipeline)
├── generated/                      # Generated code output (src/ + test/)
├── temp/                           # Runtime temp files (not committed)
├── .claude/                        # Claude Code configuration
└── README.md
```

## Quick Start

```bash
# Backend
cd backend && pip install -r requirements.txt
# Edit .env: set DEEPSEEK_API_KEY
python -m app.main          # http://localhost:8001

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

On Windows, you can also run `start.bat` to launch both backend and frontend in one click.

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
