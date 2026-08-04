<div align="center">

# UML Designer

**Turn Ideas into Architecture, Let Architecture Drive Development**

**English** | [中文](README.md)

</div>

Supports **Class Diagrams**, **Sequence Diagrams**, and **Component Diagrams** — from design to code generation, testing, and repair in a single integrated workflow. Built-in **AI Development Assistant (Conversational Agent)**, **Global Optimization Engine (V2)**, **Knowledge Graph**, and **BaseAgents framework**.

## Why UML Designer?

- **Design as source of truth**: Architecture diagrams drive code generation, verification, and testing.
- **AI-powered validation**: cross-diagram consistency checks with fuzzy-match auto-repair — machines catch what humans miss.
- **Natural language to running code**: Describe requirements → auto-generate UML → code gen + verification + test (pytest) → auto-repair on failure.

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

Floating chat panel (bottom-right robot button). A single ReActAgent handles every message:

- **7 execution tools**: `optimize_uml` (V2 direct engine: scope analysis + single LLM pass + programmatic validation) → `generate_code` (12 languages) → `validate_code` → `generate_tests` → `run_tests` (real pytest) → `fix_code` → `write_files`
- **`explore_project` sub-agent**: consolidates all read-only operations (knowledge graph queries, file reading, project info) — prevents main agent context bloat from reading files one by one
- **`request_review`**: human approval at critical checkpoints (post code-gen, post testing) — keeps AI development within guardrails
- **Streaming progress**: every tool call, arguments, and results pushed in real time
- **Interrupt control**: stop the Agent at any time
- **Session persistence**: conversation history survives refresh; session logs saved to disk (Markdown + JSONL trace)
- **Memory system**: cross-session memory with BM25 full-text search + jieba tokenization

### Global Optimization

Click "Global Optimization" in the toolbar, describe your needs:

- **Generate from scratch**: no blank diagrams needed — LLM auto-creates all required diagrams and tabs
- **V2 direct engine**: scope analysis (lightweight model) → single LLM pass (pro model) → programmatic cross-diagram validation + auto-repair + auto-layout
- **Streaming render**: elements appear on canvas in real time, cancelable mid-stream
- **Multi-diagram Diff**: per-diagram tab comparison, accept or iterate independently

### Knowledge Graph

SQLite graph database + FTS5 full-text index for structured project understanding:

- **Three knowledge layers**: Project → Entity → Relationship (inheritance/composition/dependency + design-code mapping + test coverage)
- **Dual-source build**: design layer (UML JSON auto-sync) + code layer (AST parsing of source directories)
- **Encapsulated in `explore_project`**: `kg_query` / `kg_expand` / `kg_trace` / `kg_diff` — the main Agent never touches knowledge graph details directly

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
| Agent | BaseAgents (ReActAgent + SimpleAgent) |
| LLM | DeepSeek API (v4-pro + v4-flash) |
| Knowledge Graph | SQLite + FTS5 |
| Memory System | SQLite + FTS5 + jieba |
| Testing | pytest (real subprocess execution) |
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
│   │   │   ├── DiffViewer/         # Diff comparison view
│   │   │   └── PipelineConsole/    # Pipeline console
│   │   ├── stores/                 # Zustand state management
│   │   ├── services/               # API service layer (incl. agentChat WebSocket)
│   │   ├── types/                  # TypeScript type definitions
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/                        # FastAPI backend
│   ├── app/
│   │   ├── api/                    # REST + WebSocket routes
│   │   ├── core/                   # Config / auth / security
│   │   ├── models/                 # Pydantic data models
│   │   ├── services/               # LLM / code gen / optimization engine / trace
│   │   ├── agent_base/             # BaseAgents framework (core/agents/tools)
│   │   └── main.py
│   ├── requirements.txt
│   └── .env
├── knowledge_graph/                # Knowledge Graph system (SQLite + FTS5)
├── memory_system/                  # Cross-session memory system (SQLite + FTS5 + jieba)
├── uml_guide/                      # UML design guides
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
python -m app.main          # http://localhost:8000

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

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
