<div align="center">

# UML Designer

**Turn Ideas into Architecture, Let Architecture Drive Development**

**English** | [中文](README.md)

</div>

Supports **Class Diagrams**, **Sequence Diagrams**, and **Component Diagrams** — from design to code generation, test construction, and code optimization, all in a single integrated workflow. It also ships with a built-in **Conversational Agent**, a **Knowledge Graph**, and the **BaseAgents framework**, so AI genuinely participates in architecture design and development.

https://github.com/user-attachments/assets/6e78effa-e00b-4e69-bdfb-2c3edbb011b9

## Why UML Designer?

Architecture diagrams shouldn't be throw-away documentation that rots the moment you start coding — they should be the **single source of truth** that drives your entire workflow.

- **Consistency at scale — machines are better at this than humans.** 50 classes, 30 components, thousands of cross-references. No one can manually verify every `class_ref` and `component_id`. Our auto-validation engine can — 5-point cross-diagram consistency checks with fuzzy-match auto-repair, fixing issues before you even notice them.
- **Design intent preserved from blueprint to implementation.** The AI auto-extracts critical design constraints — which entities are immutable, which relationships must endure, which architectural decisions are foundational — and injects them into every downstream stage. It's like having your architect review every line of generated code.
- **From natural language to verified, running code — end to end.** A product manager describes requirements in words → auto-generated UML architecture → code generation with ReAct guard → test generation with real pytest execution → failure-driven source optimization. AI watches every step.

## Features

### Multi-Diagram Editor

| Diagram Type | Core Elements | Interaction |
|-------------|--------------|-------------|
| **Class Diagram** | Class nodes + 6 relationship types | Double-click to add class / drag ports to create relationships |
| **Sequence Diagram** | Lifelines + 5 message arrow types | Double-click to add lifeline / click A→B to create message |
| **Component Diagram** | Component nodes + dependency arrows | Double-click to add component / double-click inside to create sub-component |

### Core Editing
- **Undo/Redo** (50 steps): drag, property panel edits auto-merged into single steps; full undo support for classes, relationships, lifelines, messages, components, and fragments
- **Zoom**: toolbar +/−/reset synced with Ctrl+Scroll; zoom level persisted per project; Space/Middle-button pan
- **Grid snapping** with customization (size/color/weight)
- **Ctrl+C/V** copy-paste elements, **Ctrl+S** save project
- **Property editing**: select any element and edit in the right panel

### Project Management
- `.umlproj` project file: one project contains multiple diagrams of different types
- **Grouped dropdown switching**: toolbar organizes diagrams into three dropdown buttons by type (Component/Class/Sequence), color-coded (orange/blue/green), one-click switching
- Delete diagrams directly from dropdown menu items (🗑 icon) — no context menu needed
- **Component-Diagram hierarchy**: Class and Sequence diagrams can be linked to Component nodes in a Component Diagram. Right-click a component to view/create linked diagrams, forming a `Project → Component → Class/Sequence Diagram` three-tier organization
- Toolbar shows component context (e.g. `AuthService › Class Diagram`)
- Backward-compatible: opening legacy `.uml` files auto-wraps them into a project
- Directory selection auto-persisted (localStorage)

### LLM Integration

#### Two Entry Points

| Feature | Button | Description |
|---------|--------|-------------|
| **Single-Diagram Design** | Blue toolbar button | Optimize/generate only the currently open diagram (Class/Sequence/Component); modal collects requirements |
| **Global Optimization** | Purple toolbar button | Requirement-driven comprehensive design — cross-validates all diagrams for consistency, **no blank diagram needed**, generates all required UML designs from natural language description |

#### Conversational Agent (AI Development Assistant · Floating Chat Panel)

Click the robot button in the bottom-right corner to open a draggable/resizable chat panel. **A single ReActAgent handles every message** — small talk gets a direct text reply, while development requests are automatically orchestrated through tools for the full development workflow.

- **Single-agent design**: the same Agent decides whether to call tools based on its system prompt — small talk gets a direct reply, development requests are auto-orchestrated through tools — reusing conversation history across turns
- **7 development tools + human review**: `optimize_uml` (UML optimization) → `generate_code` (code generation) → `validate_code` (ReAct verification) → `generate_tests` (test generation) → `run_tests` (real pytest) → `fix_code` (failure-driven repair) → `write_files` (write to disk), plus `request_review` for human approval at critical decision points
- **Knowledge-graph aware**: registers 4 graph query tools (`kg_query`/`kg_expand`/`kg_trace`/`kg_diff`) and injects a project-structure summary, so the Agent proactively queries project structure on demand instead of passively receiving everything
- **Streaming progress**: every tool call, its arguments, and its result stream to the panel in real time — the whole process is visible
- **Interrupt control**: stop the Agent at any time, gracefully terminating the tool loop
- **Session logs**: every session lands in `temp/chat_log/` — human-readable Markdown (`chat_*.md`) + machine-replayable JSONL trace (`trace_*.jsonl`, including raw LLM round-trips, tool calls, and review records)
- **Message persistence**: conversation history survives page refresh

#### Global Optimization (Requirement-Driven · No Blank Diagram Needed)
- **Generate from scratch**: just describe your requirements (e.g. *"Design an automotive OTA update system with a three-tier architecture: cloud dispatch, TBox forwarding, MDC execution"*). The LLM auto-generates Component, Class, and Sequence diagrams (supports multiple diagrams of the same type)
- **Auto-create diagram tabs**: the frontend creates diagram tabs automatically based on the `diagrams` array returned by the LLM — no manual blank-diagram creation
- **Multi-diagram cross-validation**: when diagrams already exist, the LLM analyzes all diagrams simultaneously for cross-diagram consistency checks and collaborative optimization
- **`component_id` semantic auto-detection**: the LLM automatically establishes component-diagram hierarchy associations
- **Two modes**: Full mode (single response) + Streaming mode (live rendering as each element is generated, cancelable mid-stream)
- **Multi-diagram Diff panel**: results viewable per-diagram with tab switching; accept or iterate on each diagram independently

#### Cross-Diagram Reference Index & Auto-Validation (Global Optimization Enhancement)
- **Pre-computed reference index**: before each LLM call, the backend scans all diagrams for classes, lifelines, components, and interfaces, injecting a structured cross-diagram reference index into the prompt so the LLM has full context without manually parsing JSON
- **Post-validation engine**: after the LLM returns, **5 checks** are run automatically for cross-diagram consistency:
  - Lifeline `class_ref` → class ID validity (auto fuzzy-match repair)
  - Sequence diagram message method names → class method signature matching
  - Diagram `component_id` → component ID validity
  - Component interfaces ↔ Class interfaces consistency
  - **Component coverage**: checks whether every component has associated Class and Sequence diagrams (❌ missing → warning / ⚠️ missing one type → info)
- **Auto-repair**: unambiguous reference errors (e.g. `class_ref` pointing to a non-existent ID) are auto-corrected via name fuzzy matching, marked as "auto-repaired"
- **Categorized Diff display**: consistency report distinguishes ❌ errors / ⚠️ warnings / 🟢 auto-repaired, at a glance
- **Cross-diagram design guide**: `uml_guide/cross_diagram_guide.md` — covers `component_id`/`class_ref` usage conventions, three-diagram consistency checklist, typical design patterns, common mistakes with corrections

#### Component Manifest & Multi-Diagram Association (Global Optimization Enhancement)
- **Component Manifest**: injects a component coverage status table into the prompt (❌ missing / ⚠️ partial / ✅ complete), so the LLM knows exactly which diagrams are missing for each component
- **Multi-diagram association rules**: prompt defines the component-diagram `1:N` relationship — one component can link to multiple Class diagrams (split by layer) and multiple Sequence diagrams (split by scenario), all sharing the same `component_id`
- **From-scratch workflow**: when the project is empty, the prompt guides the LLM through Step 1 (Component Diagram, establishing ID anchors) → Step 2 (per-component Class Diagrams) → Step 3 (per-component Sequence Diagrams), ensuring complete associations
- **Association completeness check**: post-validation includes Check 5, verifying every component in the LLM output has at least one Class Diagram and one Sequence Diagram
- **Design guide synchronized**: `cross_diagram_guide.md` updated with §1.4 component-diagram many-to-many association spec (association model, when multiple diagrams are needed, checklist); §4.7 added error example for insufficient component coverage

#### Auto-Layout Engine
- **Minimal integration**: `backend/app/services/layout_engine.py` — standalone module, single `auto_layout(result)` call
- **Three layout algorithms**:
  - **Class Diagram**: inheritance chain layering (parent on top, child below) + grid layout for non-inheritance classes
  - **Sequence Diagram**: lifelines evenly spaced horizontally (200px gap), messages vertically spaced by `order` (45px interval)
  - **Component Diagram**: three-phase flow layout — child components sized first → parent components expanded as needed → top-level components flow-arranged with auto-wrap
- **Only affects newly generated elements**: elements with (0,0) coordinates from LLM generation are auto-laid-out; manually positioned elements are fully preserved
- **Frontend height adaptation**: sequence diagram lifeline height dynamically calculated from actual message Y coordinates instead of fixed formula estimates — no more dangling connection lines regardless of message count
- **Insertion pipeline**: LLM response → field normalization → cross-diagram validation → `auto_layout()` → return to frontend, with zero additional latency

#### Single-Diagram Design
- **Single-diagram optimization**: LLM analyzes and optimizes a single diagram, producing a diff comparison
- **Single-diagram generation**: when the current diagram is empty, the LLM generates a brand-new design from your requirements
- Optimization results auto-pushed to the Diff panel; accept/reject/continue optimizing
- **Dual-model strategy**: primary model `deepseek-v4-pro` (complex tasks) + lightweight model `deepseek-v4-flash` (simple tasks), selected by scenario to reduce cost; sub-agents default to flash model
- **Code generation**: LLM generates code in 12 programming languages (Class Diagram)
- **Existing code adaptation**: load existing source code; LLM adapts/optimizes it according to the UML design
- **Incremental test updates**: load existing tests; LLM incrementally modifies them based on use-case changes, with ReAct correctness verification
- **ReAct code guardian**: both source and test generation include automatic ReAct engine verification (native Function Calling). Verification toolchain: `check_imports` (read from disk → syntax + import detection), `run_module` (read from disk → runtime validation), `run_bash` (allowlist sandbox → environment query/test execution), `analyze_error` (error localization analysis), `finish_optimization` (exit signal). Configurable change cap (%) auto-blocks oversized changes, requiring the LLM to trim down
- **LLM call reliability**:
  - Empty-response self-healing: auto-retry on empty/truncated API responses (degraded json_mode + doubled max_tokens)
  - Response fallback parsing: on JSON parse failure, auto-degrades to extracting markdown code blocks or wrapping raw output
  - Tool argument JSON truncation guard: Function Calling parameter anomalies are rejected with feedback, preventing silent crashes
  - Code files written to disk before verification: generated code lands in `generated/src/` before ReAct validation; tools read from disk — no need to shuttle source code around
- **Design constraint cross-stage propagation**: after design optimization completes, the LLM auto-extracts key design constraints (immutable entities, preserved relationships, design rationale) and injects them into the system prompts of the code-generation and testing stages, preventing drift from the original design intent
- **Per-file test generation**: with large test suites, calls are split per module — each invocation generates tests for a single source file (with UML global architecture + dependency class API signatures included), avoiding single-call token-limit truncation

### Knowledge Graph System

**From "passive injection" to "active exploration"** — provides structured project understanding for AI assistants, letting the model query project structure on demand instead of pre-filling the context window.

- **Three layers of knowledge**: Project layer (which diagrams exist) → Entity layer (classes/methods/attributes in each diagram) → Relationship layer (inheritance/composition/dependency links + design-code mapping + test coverage)
- **SQLite graph database + FTS5 full-text index**: node/edge/full-text tables with content-sync triggers auto-maintaining the index — no extra database service needed
- **Dual-source building**:
  - **Design layer (declarative)**: daemon thread automatically rebuilds idempotently from UML JSON when a project is saved
  - **Code layer (exploratory)**: the first Agent `kg_diff` call auto-parses the source directory via AST; `IMPLEMENTS` edges bridge design and code
- **4 Agent tools**: `kg_query` (BM25 full-text search), `kg_expand` (n-hop neighborhood expansion), `kg_trace` (dependency path tracing), `kg_diff` (design-vs-code diff: missing implementation / extra code / signature mismatch / no test coverage)
- **Integration points**: `file_service` save hooks auto-rebuild + Agent tool registration in the conversational agent — transparent to the user

### Cross-Session Memory System

A memory module (`memory_system/`) built on **SQLite + FTS5 + jieba tokenization**, giving Agents cross-session context awareness:

- **BM25 full-text retrieval**: Chinese semantic tokenization with significantly better recall than character-level bigram; auto-fallback when jieba is unavailable
- **Automatic memory extraction**: LLM interactions auto-extract dual-field memories (summary + original_text)
- **Lifecycle management**: three-stage reinforce / decay / prune mechanism with LFU eviction protecting pinned + hot memories
- **WAL mode**: concurrent reads/writes with atomic operations; built-in `migrate.py` migrates from the legacy JSON store

### BaseAgents — Lightweight Agent Framework

The project ships a built-in Agent framework (`backend/app/agent_base/`) designed around **layered decoupling, single responsibility, and unified interfaces**:

| Layer | Modules | Responsibility |
|-------|---------|----------------|
| **core** | `agent` / `llm` / `message` / `config` / `exceptions` | Abstract base classes + unified LLM interface + messages & config |
| **agents** | `SimpleAgent` / `ReActAgent` / `ReflectionAgent` / `PlanAndSolveAgent` | Four classic agent paradigms |
| **tools** | `base` / `registry` / `chain` / `async_executor` | Everything is a tool: register / discover / execute / parallelize |

- **Four paradigms**: Simple (basic chat), ReAct (Thought→Action→Observe loop), Reflection (generate→review→refine, with external verification hooks), Plan-and-Solve (plan first, then execute)
- **Tool system**: Tool ABC + ToolRegistry + ToolChain + AsyncToolExecutor, supporting native Function Calling
- **Ready to use**: `BaseAgentsLLM.from_settings()` wires up existing project config in one line; `optimize_project_v2` replaces single-shot calls with a three-phase reflection loop

### Security
- Bearer Token API authentication (configurable; auto-skipped in local dev)
- Path security validation with directory-traversal prevention
- API Key isolated in environment variables

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend Framework | React 18 + TypeScript |
| Graphics Engine | AntV X6 |
| State Management | Zustand |
| UI Component Library | Ant Design 5 |
| Code Editor | Monaco Editor |
| Backend Framework | FastAPI (Python) |
| Agent Framework | BaseAgents (Simple / ReAct / Reflection / Plan-and-Solve) |
| Knowledge Graph | SQLite graph database + FTS5 full-text index |
| Memory System | SQLite + FTS5 + jieba tokenization |
| LLM | DeepSeek API (v4-pro + v4-flash dual-model) |
| Test Framework | pytest (real subprocess execution) |
| Build Tool | Vite |

## Project Structure

```
uml_designer/
├── frontend/                       # React frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── Canvas/
│   │   │   │   ├── UMLEditor.tsx   # Class Diagram editor
│   │   │   │   ├── SeqEditor.tsx   # Sequence Diagram editor
│   │   │   │   └── CompEditor.tsx  # Component Diagram editor
│   │   │   ├── AgentChat/          # Conversational Agent floating panel (WebSocket)
│   │   │   ├── PropertyPanel/      # Property editing panel
│   │   │   ├── Toolbar/            # Toolbar
│   │   │   ├── CodeViewer/         # Code viewer (Monaco)
│   │   │   ├── TestCodeViewer/     # Test code viewer
│   │   │   ├── TestCaseViewer/     # Test case table
│   │   │   ├── PipelineConsole/    # Pipeline console
│   │   │   └── DiffViewer/         # Diff comparison view
│   │   ├── stores/                 # Zustand state management
│   │   ├── services/               # API service layer (incl. agentChat WebSocket)
│   │   ├── types/                  # TypeScript types (uml, sequence, component, pipeline)
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/                        # FastAPI backend
│   ├── app/
│   │   ├── api/                    # REST + WebSocket routes (incl. /api/agent/ws/chat)
│   │   ├── core/                   # Config / auth / security
│   │   ├── models/                 # Pydantic data models
│   │   ├── services/               # LLM / code gen / ReAct / pipeline / conversational agent / trace
│   │   ├── agent_base/             # BaseAgents framework (core/agents/tools)
│   │   └── main.py
│   ├── requirements.txt
│   └── .env
├── knowledge_graph/                # Knowledge Graph system (SQLite + FTS5)
├── memory_system/                  # Cross-session memory system (SQLite + FTS5 + jieba)
├── uml_guide/                      # UML design guides (incl. cross-diagram consistency spec)
├── generated/                      # Generated code output (src/ + test/)
├── temp/                           # Backend temp files (not committed)
│   ├── uml_files/                  # UML / umlproj save directory
│   ├── chat_log/                   # Conversational agent session logs (chat_*.md + trace_*.jsonl)
│   ├── pipeline_log/               # Pipeline run reports + LLM interaction logs
│   ├── testHub/                    # Default Excel test case library directory
│   └── dev_review.txt              # Unified review records
├── .claude/                        # Claude Code configuration
└── README.md
```

## Quick Start

### 1. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Edit `backend/.env`:

```env
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro         # Default primary model (complex tasks)
SUB_AGENT_MODEL=deepseek-v4-flash      # Sub-agent model (lightweight, cost-saving)
INTERNAL_API_TOKEN=                     # Optional; when set, enables API authentication
```

### 3. Start Backend

```bash
cd backend
python -m app.main          # http://localhost:8000 | API docs: /api/docs
```

### 4. Start Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

### Production Authentication

```env
# backend/.env
INTERNAL_API_TOKEN=your-random-secret

# frontend/.env.local
VITE_API_TOKEN=your-random-secret
```

Generate a random secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## Usage Guide

### Project Management

1. Toolbar "+" dropdown → Add Class / Sequence / Component Diagram
2. Diagram tabs switch the currently-edited diagram; **right-click** to delete unwanted diagrams
3. Ctrl+S saves as a `.umlproj` project file
4. Toolbar "Open" → browse any directory, manually enter a path or click shortcuts (Desktop/Documents/Drives), select a `.umlproj` or `.uml` file to open

### Component-Diagram Association

1. In a **Component Diagram**, create components (e.g. `AuthService`, `PaymentService`)
2. **Right-click** a component node → click "New Class Diagram" or "New Sequence Diagram" → auto-linked and switches to the new diagram
3. The toolbar diagram tab shows component context (`Component Name › Diagram Type`)
4. Right-click the component again → the menu lists all linked Class and Sequence diagrams → click to switch
5. Select a component → the right property panel also shows linked diagrams at the bottom
6. During **Global Optimization**, the LLM auto-detects component hierarchy and generates/optimizes linked Class and Sequence diagrams for each component, supporting complete three-tier architecture design directly from requirements

### Class Diagram
- **Add class**: double-click empty canvas area
- **Create relationship**: drag from a node port to a target node
- **Edit properties**: click a class or relationship, edit in the right panel

### Sequence Diagram
- **Add lifeline**: double-click empty canvas area
- **Create message**: click lifeline A → then click lifeline B
- **Self-message**: click the same lifeline twice
- **Edit message**: click a message line, modify type and notes in the right panel

### Component Diagram
- **Add top-level component**: double-click empty canvas area
- **Add sub-component**: double-click inside a parent component
- **Create dependency**: drag from a node port to a target node
- **Resize**: drag component corners
- **Component context menu**: right-click a component node → view linked Class and Sequence diagrams → one-click create or switch
- **Property panel**: with a component selected, view linked diagrams in the right panel with quick-create shortcuts

### Conversational Agent (AI Development Assistant)

Click the robot button in the bottom-right corner to open the chat panel — treat it as an AI colleague who can write code.

1. Click the **🤖 AI Development Assistant** button (bottom-right) to open the floating chat panel (draggable, resizable)
2. Type your request directly, e.g.: *"Design a user authentication system with registration, login, and password reset"* or *"Create a calculator system supporting add, subtract, multiply, divide"*
3. Small talk gets a direct reply; development requests are auto-orchestrated through tools for the full UML design → code generation → verification → testing → repair → write-to-disk flow
4. The panel shows every tool call, its arguments, and its result in real time — the whole process is visible
5. **Human review** can pop up at critical points (Approve / Reject / Later), keeping AI development within guardrails
6. Click **Stop** at any time to interrupt the Agent; conversation history survives page refresh
7. When a `.umlproj` project is open and source/test directories are set, the Agent automatically senses project context and can query the knowledge graph for project structure

### Global Optimization (Requirement-Driven)

**No blank diagram needed** — just describe your requirements and the LLM auto-generates all required UML designs.

1. Click the "Global Optimization" button (purple icon) on the toolbar
2. Enter your requirements, e.g.: *"Design an automotive OTA update system with cloud, TBox, and MDC three-tier component architecture, supporting OTA task scheduling and chicken-play task management"*
3. Check "Live Render" to enable streaming real-time rendering (elements appear as they are generated)
4. Full mode: after the LLM returns, diagram tabs are auto-created and populated; the Diff panel supports per-diagram tab comparison
5. Streaming mode: the LLM outputs elements one by one to the canvas in real time, auto-creating the required diagram types
6. The LLM can generate multiple diagrams of the same type (e.g. two Sequence Diagrams for different business scenarios)

### Code Generation & Testing

1. On a Class Diagram, click **Generate Code** → choose a language (12 supported)
2. Load an existing source directory → **Existing Code Adaptation**: the LLM optimizes code according to the UML design
3. Load a test-case library (Excel) → generate tests → run real pytest → auto-repair source on failures
4. Expand the reasoning details to view the round-by-round ReAct engine verification process

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+C | Copy selected element |
| Ctrl+V | Paste |
| Ctrl+S | Save project |
| Delete | Delete selected element |
| Ctrl+Scroll | Zoom canvas |
| Space+Drag | Pan canvas |

## Supported Programming Languages

Python, Java, TypeScript, JavaScript, C#, C++, Go, Rust, Ruby, Swift, Kotlin, PHP
