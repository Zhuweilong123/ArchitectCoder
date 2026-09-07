# Evaluation data

This directory contains versioned evaluation inputs, kept separate from the
runtime evaluator implementation in `extensions/evals/`:

- `cases/` — case definitions consumed by `extensions.evals.registry`.
- `projects/` — project manifests that describe fixture boundaries and optional
  `base_fixture` inheritance.
- `fixtures/` — isolated `design/`, `src/`, and `test/` project snapshots. The
  current 16-case radar suite is based on `project/project_radar`: the base
  project covers read-only understanding, while overlay projects seed source,
  UML, test, and cross-artifact defects for CRUD tasks. `radar_trace_remove_v1`
  remains only for the two retained `trace_3_1` conversation cases.
- `baseline.json` — archived baseline metrics exposed by the evaluation API.

The application resolves these paths through `extensions.evals.paths`; callers should
use that module instead of reconstructing paths from the package location.
`extensions.evals.fixture_materializer` expands a base fixture and overlay into a
normal writable temporary workspace before an evaluation starts.

## Radar case catalog

The active catalog contains 16 newly rebuilt cases plus the two retained
`trace_3_1` cases:

- 4 project-understanding cases: component/source/test inventory, UML diagram
  inventory, API contract lookup, and sequence-flow tracing.
- 8 single-turn cases: end-to-end read, dependency read, create/remove target,
  create seeded noise, UML contract repair, delay-direction repair, and removal
  of two debug-only artifacts.
- 4 multi-turn cases: greeting without tools followed by component lookup,
  sequence verification, a source-to-UML creation task, and a changed seed
  requirement.
- The performance baseline catalog contains only the 16 rebuilt cases; the two
  retained `trace_3_1` cases remain available for regression history but are
  excluded from baseline scoring and baseline archives.

Mutation cases use hidden regression tests plus visible project tests. Read-only
cases protect every project file with `paths_unchanged`; cross-artifact cases
protect the files that are outside the requested change scope. Answer and trace
checkers also verify factual responses and required tool behavior, including the
zero-tool greeting turns.

## Evaluation contract

The current catalog is pinned to case schema `1.0`, tool protocol
`foundation-tools-v1`, checker protocol `deterministic-checkers-v1`, and fixture
layout `design-src-test-v1`. Every tracked case declares its case/tool versions;
an unsupported checker, version mismatch, malformed JSON, or legacy mutation
tool name such as `edit_file` makes the complete catalog fail to load. This
fail-closed behavior prevents a broken case from silently reducing the scoring
denominator.

Criterion roles are intentionally separate:

- `hard_checkers` are acceptance gates. Every hard criterion must pass.
- `checkers` are diagnostic scoring criteria. They affect the mean score but do
  not turn a hard-gate pass into a failure.
- A legacy/local case with no hard criteria keeps the old all-checkers pass rule.

Every checker result records its criterion role and scope. Every run records a
machine-readable `failure_category`: `agent_failure`, `tool_failure`,
`environment_failure`, `checker_failure`, `timeout`, or `budget_exceeded` (and
`none` for a successful run). Batch summaries aggregate these categories so
capability regressions are not mixed with harness failures.

## Runtime parity

Official evaluations use the same `DevAgent` assembly and
`app.services.agent_execution` coordinator as the interactive chat path. A case
prompt is passed as the user message; the runner does not inject an evaluation-
only workspace or tool-policy prompt. Frontend delivery is replaced by an
in-memory sender, and review decisions use the existing `auto_stub` approval
adapter.

Normal cases inherit the production agent budget from `backend/.env` (or the
defaults in `backend/config`). A single-turn case consumes one production
task budget. A multi-turn case reuses Agent history, but each user turn gets a
fresh production task budget; cumulative Token/tool counts are report-only.
`max_seconds` is a per-turn harness deadline, so the aggregate case deadline
is `max_seconds * turn_count` (still capped by the production run budget per
turn). A case marked `metadata.capability=budget_control` may lower these
limits for the test, but can never raise them above production settings.

Each completed run also persists its final materialized workspace under the
runtime evaluation artifacts directory. The result's `workspace` field points
to that snapshot, allowing file, UML, and test checkers to be audited after the
temporary execution directory has been removed.

## Batch and performance-result boundaries

The Evaluation Center treats one execution as a runtime batch. Multiple
completed batches from the same version can be selected and merged into one
performance-result JSONL file. Results are keyed by `case_id`: exact duplicate
results are kept once, while conflicting results for the same case are rejected.
The merge does not modify `baseline.json`; only an explicit baseline promotion
or archive operation changes the tracked baseline.

The baseline remains a versioned repository asset under `backend/evals`, while
runtime batches and merged performance results remain under `temp/evals`.

The CLI follows the same performance-result boundary as the Evaluation Center.
Use `python -m extensions.evals.cli --version <version> --label <label>` for a
run; it writes the raw JSONL output and, after the run completes, registers the
same result rows as a `performance-*.jsonl` artifact under `temp/evals/results`.
The latter is what the frontend Performance Results view indexes, so CLI runs
are visible there without a separate manual merge step.
