"""Repeat the trade orchestration case in isolated A/C processes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CASE_ID = "trade-orchestration-paid-cancel-001"
CASE_IDS = (DEFAULT_CASE_ID, "trade-orchestration-demand-001")
CASE_FILES = {
    DEFAULT_CASE_ID: "trade_orchestration_paid_cancel_001.json",
    "trade-orchestration-demand-001": "trade_orchestration_demand_001.json",
}
ARMS = {
    "A": {"AGENT_ORCHESTRATION_ENABLED": "false",
          "AGENT_ARCHITECTURE_SCHEDULING_ENABLED": "false"},
    "C": {"AGENT_ORCHESTRATION_ENABLED": "true",
          "AGENT_ARCHITECTURE_SCHEDULING_ENABLED": "true"},
}
SHARED_ENV = {
    "AGENT_MAIN_SUBAGENT_ENABLED": "false",
    "AGENT_ORCHESTRATOR_PROVIDER": "extensions.orchestration:create",
    "AGENT_KNOWLEDGE_GRAPH_ENABLED": "true",
    "AGENT_KNOWLEDGE_GRAPH_DB_PATH": "",
}


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fixture_hash() -> str:
    root = _root() / "backend/evals/fixtures/project_trade_cancel_v1"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _implementation_hash(case_id: str) -> str:
    root = _root()
    paths = [
        root / "backend/evals/cases" / CASE_FILES[case_id],
        root / "backend/evals/hidden_tests/project_trade/test_paid_cancel.py",
        root / "backend/app/agent_base/core/orchestration.py",
        root / "backend/app/agent_base/tools/my_tools/conversation_tools.py",
        root / "backend/app/services/agent_execution.py",
        root / "extensions/evals/runner.py",
        root / "extensions/evals/graph_fixture.py",
        root / "extensions/evals/orchestration_compare.py",
        root / "extensions/orchestration/provider.py",
        *(root / "extensions/orchestration/architecture_aware").glob("*.py"),
    ]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _trace_metrics(path: str) -> dict[str, Any]:
    events = _read_jsonl(Path(path)) if path else []
    main_calls = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "tool_call"
        and "child_agent" not in str(event.get("span_path") or "")
    ]
    first_edit = next((index for index, event in main_calls
                       if event.get("tool_name") == "apply_changes"), len(events))
    def is_exploration_call(event: dict[str, Any]) -> bool:
        if event.get("tool_name") == "explore_architecture":
            return True
        arguments = event.get("arguments") or {}
        return (event.get("tool_name") == "route_architecture"
                and isinstance(arguments, dict)
                and arguments.get("decision") == "explore")

    first_demand = next((index for index, event in main_calls
                         if is_exploration_call(event)), None)
    first_route = next((index for index, event in main_calls
                        if event.get("tool_name") == "route_architecture"), None)
    first_checkpoint = next((index for index, event in enumerate(events)
                             if event.get("event_type") == "llm_request"
                             and "child_agent" not in str(event.get("span_path") or "")
                             and any(
                                 str(message.get("content") or "").startswith(
                                     "## Architecture routing checkpoint")
                                 for message in (event.get("messages") or ())
                                 if isinstance(message, dict)
                             )), None)
    reads = [
        (index, event, str((event.get("arguments") or {}).get("path") or "")
         .replace("\\", "/").casefold())
        for index, event in main_calls if event.get("tool_name") == "read_file"
    ]
    pre_edit = [file for index, _, file in reads if index < first_edit and file]
    pre_edit_source = [file for file in pre_edit
                       if file.startswith(("src/", "source/")) or "/src/" in file
                       or "/source/" in file]
    pre_edit_tests = [file for file in pre_edit
                      if file.startswith(("test/", "tests/")) or "/test/" in file
                      or "/tests/" in file]
    pre_edit_spans = {event.get("span_id") for index, event, file in reads
                      if index < first_edit and file}
    pre_edit_chars = sum(
        len(str(event.get("observation") or "")) for index, event in enumerate(events)
        if index < first_edit and event.get("event_type") == "tool_result"
        and event.get("tool_name") == "read_file"
        and event.get("parent_span_id") in pre_edit_spans
    )
    pre_demand = [file for index, _, file in reads
                  if first_demand is not None and index < first_demand and file]
    pre_route = [file for index, _, file in reads
                 if first_route is not None and index < first_route and file]
    pre_checkpoint = [file for index, _, file in reads
                      if first_checkpoint is not None and index < first_checkpoint and file]
    pre_demand_files = set(pre_demand)
    post_demand = [file for index, _, file in reads
                   if first_demand is not None and index > first_demand and file]
    plans = [event for event in events if event.get("event_type") == "orchestrator_plan"]
    reports = []
    demand_calls = 0
    route_calls = 0
    direct_decisions = 0
    recorded_reasons = 0
    scheduler_ms = 0.0
    for event in events:
        if event.get("tool_name") not in {"explore_architecture", "route_architecture"}:
            continue
        if event.get("event_type") == "tool_call":
            if event.get("tool_name") == "route_architecture":
                route_calls += 1
                arguments = event.get("arguments") or {}
                if isinstance(arguments, dict):
                    direct_decisions += arguments.get("decision") == "direct"
                    recorded_reasons += bool(str(arguments.get("reason") or "").strip())
            if is_exploration_call(event):
                demand_calls += 1
        elif event.get("event_type") == "tool_result":
            try:
                report = json.loads(event.get("observation") or "{}")
                if isinstance(report, dict):
                    if event.get("tool_name") == "explore_architecture" or report.get("decision") == "explore":
                        scheduler_ms += float(event.get("duration_ms") or 0)
                        reports.append(report)
            except (TypeError, ValueError):
                pass
    findings = [
        finding for report in reports
        for finding in report.get("findings", [])
        if isinstance(finding, dict)
    ]
    slot_seconds: dict[int, float] = {}
    for finding in findings:
        slot = int(finding.get("slot", -1))
        if slot >= 0:
            slot_seconds[slot] = slot_seconds.get(slot, 0.0) + float(
                finding.get("seconds") or 0
            )
    loads = list(slot_seconds.values())
    prerun_planner_tokens = sum(int(plan.get("planner_tokens") or 0) for plan in plans)
    prerun_worker_tokens = sum(int(plan.get("worker_tokens") or 0) for plan in plans)
    demand_worker_tokens = sum(int(report.get("worker_tokens") or 0) for report in reports)
    return {
        "main_read_calls_before_edit": len(pre_edit),
        "main_unique_files_before_edit": len(set(pre_edit)),
        "main_source_read_calls_before_edit": len(pre_edit_source),
        "main_test_read_calls_before_edit": len(pre_edit_tests),
        "main_read_observation_chars_before_edit": pre_edit_chars,
        "main_read_calls_before_demand": len(pre_demand) if first_demand is not None else None,
        "main_read_calls_before_route": len(pre_route) if first_route is not None else None,
        "main_read_calls_before_checkpoint": (
            len(pre_checkpoint) if first_checkpoint is not None else None
        ),
        "routing_checkpoint_seen": first_checkpoint is not None,
        "main_unique_files_before_demand": (
            len(pre_demand_files) if first_demand is not None else None
        ),
        "main_repeat_read_calls_after_demand": (
            sum(file in pre_demand_files for file in post_demand)
            if first_demand is not None else None
        ),
        "demand_calls": demand_calls,
        "route_calls": route_calls,
        "direct_decisions": direct_decisions,
        "recorded_route_reasons": recorded_reasons,
        "scheduler_ms": round(scheduler_ms, 1),
        "prerun_planner_tokens": prerun_planner_tokens,
        "prerun_worker_tokens": prerun_worker_tokens,
        "demand_worker_tokens": demand_worker_tokens,
        "worker_tokens": prerun_worker_tokens + demand_worker_tokens,
        "delegated": prerun_worker_tokens > 0 or bool(findings),
        "worker_items": len(findings),
        "grounded_items": sum(bool(item.get("tool_evidence")) for item in findings),
        "structured_evidence_items": sum(len(item.get("evidence") or ()) for item in findings),
        "edit_candidates": sum(
            item.get("role") == "edit" for finding in findings
            for item in (finding.get("evidence") or ()) if isinstance(item, dict)
        ),
        "excluded_candidates": sum(len(item.get("excluded_candidates") or ()) for item in findings),
        "excerpt_only_items": sum(
            int(item.get("grounded_excerpts") or 0) > 0
            and not bool(item.get("tool_evidence"))
            for item in findings
        ),
        "partial_items": sum(item.get("status") == "partial" for item in findings),
        "failed_items": sum(item.get("status") == "failed" for item in findings),
        "pending_items": sum(int(report.get("pending_work_items") or 0) for report in reports),
        "repartitioned_schedules": sum(int(report.get("schedule_revision") or 0) > 1 for report in reports),
        "max_mean_worker_load": (
            round(max(loads) / statistics.mean(loads), 3)
            if loads and statistics.mean(loads) > 0 else None
        ),
        "exploration_statuses": [report.get("status", "unknown") for report in reports],
    }


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    graph = metadata.get("graph_preindex") or {}
    index_ms = float(graph.get("index_ms") or 0)
    duration_ms = float(row.get("duration_ms") or 0)
    return {
        "arm": metadata.get("comparison_arm"),
        "iteration": metadata.get("comparison_iteration"),
        "run_id": row.get("run_id"),
        "passed": bool(row.get("passed")),
        "status": row.get("status"),
        "failure_category": row.get("failure_category"),
        "model": row.get("model"),
        "duration_ms": duration_ms,
        "agent_duration_ms": max(0.0, duration_ms - index_ms),
        "graph_index_ms": index_ms,
        "production_budget": (metadata.get("eval_contract") or {}).get("production_budget", {}),
        "total_tokens": int(row.get("total_tokens") or 0),
        "tool_calls": int(row.get("tool_calls") or 0),
        "trace_path": row.get("trace_path"),
        **_trace_metrics(str(row.get("trace_path") or "")),
    }


def _summary(rows: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    runs = [_metrics(row) for row in rows if row.get("case_id") == case_id]
    arms = {}
    for arm in ARMS:
        selected = [run for run in runs if run["arm"] == arm]
        if not selected:
            continue
        arms[arm] = {
            "runs": len(selected),
            "passed": sum(run["passed"] for run in selected),
            "pass_rate": round(statistics.mean(run["passed"] for run in selected), 3),
            "median_agent_ms": round(statistics.median(run["agent_duration_ms"] for run in selected), 1),
            "median_total_ms": round(statistics.median(run["duration_ms"] for run in selected), 1),
            "median_graph_index_ms": round(statistics.median(run["graph_index_ms"] for run in selected), 1),
            "median_tokens": round(statistics.median(run["total_tokens"] for run in selected), 1),
            "median_tool_calls": round(statistics.median(run["tool_calls"] for run in selected), 1),
            "demand_activation_rate": round(statistics.mean(run["demand_calls"] > 0 for run in selected), 3),
            "route_decision_rate": round(statistics.mean(run["route_calls"] > 0 for run in selected), 3),
            "routing_checkpoint_rate": round(statistics.mean(
                run["routing_checkpoint_seen"] for run in selected), 3),
            "direct_decision_rate": round(statistics.mean(run["direct_decisions"] > 0 for run in selected), 3),
            "recorded_route_reason_rate": round(statistics.mean(
                run["recorded_route_reasons"] > 0 for run in selected), 3),
            "delegation_rate": round(statistics.mean(run["delegated"] for run in selected), 3),
            "median_worker_tokens": round(statistics.median(run["worker_tokens"] for run in selected), 1),
            "median_structured_evidence_items": round(statistics.median(
                run["structured_evidence_items"] for run in selected), 1),
            "median_main_read_calls_before_edit": round(statistics.median(
                run["main_read_calls_before_edit"] for run in selected), 1),
            "median_main_unique_files_before_edit": round(statistics.median(
                run["main_unique_files_before_edit"] for run in selected), 1),
            "median_main_source_read_calls_before_edit": round(statistics.median(
                run["main_source_read_calls_before_edit"] for run in selected), 1),
            "median_main_test_read_calls_before_edit": round(statistics.median(
                run["main_test_read_calls_before_edit"] for run in selected), 1),
            "median_main_read_observation_chars_before_edit": round(statistics.median(
                run["main_read_observation_chars_before_edit"] for run in selected), 1),
        }
        activated = [run for run in selected if run["main_read_calls_before_demand"] is not None]
        routed = [run for run in selected if run["main_read_calls_before_route"] is not None]
        checkpointed = [run for run in selected
                        if run["main_read_calls_before_checkpoint"] is not None]
        if checkpointed:
            arms[arm]["median_main_read_calls_before_checkpoint"] = round(statistics.median(
                run["main_read_calls_before_checkpoint"] for run in checkpointed), 1)
        if routed:
            arms[arm]["median_main_read_calls_before_route"] = round(statistics.median(
                run["main_read_calls_before_route"] for run in routed), 1)
        if activated:
            arms[arm].update({
                "median_main_read_calls_before_demand": round(statistics.median(
                    run["main_read_calls_before_demand"] for run in activated), 1),
                "median_main_unique_files_before_demand": round(statistics.median(
                    run["main_unique_files_before_demand"] for run in activated), 1),
                "median_main_repeat_read_calls_after_demand": round(statistics.median(
                    run["main_repeat_read_calls_after_demand"] for run in activated), 1),
            })
    return {
        "case_id": case_id,
        "arms": arms,
        "runs": runs,
        "scheduler_exercised": any(run["arm"] == "C" and run["worker_items"] > 0 for run in runs),
    }


async def _worker(args: argparse.Namespace) -> int:
    from .registry import load_cases
    from .runner import EvalRunner

    case = load_cases()[args.case_id]
    result = await EvalRunner(results_path=args.results).run_case(
        case,
        result_metadata={
            "comparison_arm": args.arm,
            "comparison_iteration": args.iteration,
            "comparison_fixture_sha256": args.fixture_hash,
            "comparison_implementation_sha256": args.implementation_hash,
        },
    )
    print(json.dumps({
        "run_id": result.run_id,
        "arm": args.arm,
        "iteration": args.iteration,
        "status": result.status,
        "passed": result.passed,
        "duration_ms": result.duration_ms,
        "total_tokens": result.total_tokens,
    }, ensure_ascii=False), flush=True)
    return 0


def _run_matrix(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve() if args.output else (
        _root() / "temp/evals/orchestration_compare"
        / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    )
    output.mkdir(parents=True, exist_ok=True)
    results = output / "results.jsonl"
    if results.exists():
        raise ValueError(f"comparison output already contains results: {results}")
    fixture_hash = _fixture_hash()
    implementation_hash = _implementation_hash(args.case_id)
    manifest = {
        "case_id": args.case_id,
        "fixture_sha256": fixture_hash,
        "implementation_sha256": implementation_hash,
        "repeats": args.repeats,
        "arms": {arm: ARMS[arm] for arm in args.arms},
        "shared_env": SHARED_ENV,
        "python": sys.version,
        "started_at": datetime.now().isoformat(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    errors = []
    for iteration in range(1, args.repeats + 1):
        order = list(args.arms)
        order = order[(iteration - 1) % len(order):] + order[:(iteration - 1) % len(order)]
        for arm in order:
            env = os.environ.copy()
            env.update(SHARED_ENV)
            env.update(ARMS[arm])
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (
                str(_root() / "backend"), str(_root()), env.get("PYTHONPATH", ""),
            )))
            print(f"[{iteration}/{args.repeats}] arm={arm}", flush=True)
            command = [
                sys.executable, "-m", "extensions.evals.orchestration_compare",
                "--worker", "--arm", arm, "--iteration", str(iteration),
                "--results", str(results), "--fixture-hash", fixture_hash,
                "--implementation-hash", implementation_hash,
                "--case-id", args.case_id,
            ]
            try:
                proc = subprocess.run(
                    command, cwd=_root() / "backend", env=env,
                    capture_output=True, text=True, timeout=args.process_timeout,
                )
                if proc.stdout.strip():
                    print(proc.stdout.strip().splitlines()[-1], flush=True)
                if proc.returncode:
                    errors.append({"iteration": iteration, "arm": arm,
                                   "error": proc.stderr[-2000:] or proc.stdout[-2000:]})
            except subprocess.TimeoutExpired:
                errors.append({"iteration": iteration, "arm": arm,
                               "error": "comparison process timeout"})
    report = _summary(_read_jsonl(results), args.case_id)
    report["harness_errors"] = errors
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "arms": report["arms"],
                      "scheduler_exercised": report["scheduler_exercised"],
                      "harness_errors": errors}, ensure_ascii=False), flush=True)
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case-id", choices=CASE_IDS, default=DEFAULT_CASE_ID)
    parser.add_argument("--arms", nargs="+", choices=tuple(ARMS), default=list(ARMS))
    parser.add_argument("--output", default="")
    parser.add_argument("--report", default="", help="Recalculate an existing comparison directory")
    parser.add_argument("--process-timeout", type=int, default=1800)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=tuple(ARMS), default="", help=argparse.SUPPRESS)
    parser.add_argument("--iteration", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--results", default="", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-hash", default="", help=argparse.SUPPRESS)
    parser.add_argument("--implementation-hash", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or args.process_timeout < 1:
        parser.error("repeats and process-timeout must be positive")
    if args.worker:
        if not args.arm or not args.results:
            parser.error("worker requires arm and results")
        raise SystemExit(asyncio.run(_worker(args)))
    if args.report:
        output = Path(args.report).resolve()
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        report = _summary(_read_jsonl(output / "results.jsonl"), manifest["case_id"])
        previous = output / "summary.json"
        report["harness_errors"] = (
            json.loads(previous.read_text(encoding="utf-8")).get("harness_errors", [])
            if previous.is_file() else []
        )
        (output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(json.dumps({"output": str(output), "arms": report["arms"],
                          "scheduler_exercised": report["scheduler_exercised"]},
                         ensure_ascii=False))
        return
    raise SystemExit(_run_matrix(args))


if __name__ == "__main__":
    main()
