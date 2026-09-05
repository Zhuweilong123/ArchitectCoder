# Evaluation data

This directory contains versioned evaluation inputs, kept separate from the
runtime evaluator implementation in `extensions/evals/`:

- `cases/` — case definitions consumed by `extensions.evals.registry`.
- `projects/` — project manifests that describe fixture boundaries and optional
  `base_fixture` inheritance.
- `fixtures/` — isolated `design/`, `src/`, and `test/` project snapshots. The
  complete `radar_sim_v1` snapshot is reused as a base by sparse
  bug/diagnostic overlays; `radar_trace_remove_v1` is a standalone continuous
  conversation fixture with the same directory contract.
- `baseline.json` — archived baseline metrics exposed by the evaluation API.

The application resolves these paths through `extensions.evals.paths`; callers should
use that module instead of reconstructing paths from the package location.
`extensions.evals.fixture_materializer` expands a base fixture and overlay into a
normal writable temporary workspace before an evaluation starts.

## Runtime parity

Official evaluations use the same `DevAgent` assembly and
`app.services.agent_execution` coordinator as the interactive chat path. A case
prompt is passed as the user message; the runner does not inject an evaluation-
only workspace or tool-policy prompt. Frontend delivery is replaced by an
in-memory sender, and review decisions use the existing `auto_stub` approval
adapter.

Normal cases inherit the production agent budget from `backend/.env` (or the
defaults in `backend/config`). `max_seconds` remains the outer evaluation
deadline. The only intentional exception is a case marked
`metadata.capability=budget_control`, which is used to verify budget behavior
itself and may provide its own limits.
