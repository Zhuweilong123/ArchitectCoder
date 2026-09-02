# Evaluation data

This directory contains versioned evaluation inputs, kept separate from the
runtime evaluator implementation in `backend/app/evals/`:

- `cases/` — case definitions consumed by `app.evals.registry`.
- `projects/` — project manifests that describe fixture boundaries.
- `fixtures/` — isolated design/source/test project snapshots.
- `baseline.json` — archived baseline metrics exposed by the evaluation API.

The application resolves these paths through `app.evals.paths`; callers should
use that module instead of reconstructing paths from the package location.
