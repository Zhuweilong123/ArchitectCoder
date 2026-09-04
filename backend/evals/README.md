# Evaluation data

This directory contains versioned evaluation inputs, kept separate from the
runtime evaluator implementation in `extensions/evals/`:

- `cases/` — case definitions consumed by `extensions.evals.registry`.
- `projects/` — project manifests that describe fixture boundaries and optional
  `base_fixture` inheritance.
- `fixtures/` — isolated design/source/test project snapshots. The complete
  `radar_sim_v1` snapshot is reused as a base by sparse bug/diagnostic
  overlays; `radar_trace_remove_v1` remains standalone because its layout is
  intentionally different.
- `baseline.json` — archived baseline metrics exposed by the evaluation API.

The application resolves these paths through `extensions.evals.paths`; callers should
use that module instead of reconstructing paths from the package location.
`extensions.evals.fixture_materializer` expands a base fixture and overlay into a
normal writable temporary workspace before an evaluation starts.
