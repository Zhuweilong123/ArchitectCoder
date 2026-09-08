"""Run the evaluation catalog from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.agent_base.core.evals import load_evals
from backend.config import evaluation_results_dir

from .batches import write_performance_result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Agent evaluation cases")
    parser.add_argument("--suite", default="", help="Filter by metadata.suite")
    parser.add_argument("--ids", nargs="*", default=[], help="Run only these case IDs")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of cases")
    parser.add_argument("--results", default="", help="Optional JSONL results path")
    parser.add_argument("--version", default="working-tree", help="Version recorded in Performance Results")
    parser.add_argument("--label", default="", help="Optional Performance Results label")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    # CLI output is always kept in the canonical evaluation results folder.
    # The argument remains a filename for backwards-compatible invocations.
    result_path = evaluation_results_dir() / args.results.replace("\\", "/").split("/")[-1] if args.results else None
    provider = load_evals(results_path=result_path)
    cases = {case.id: case for case in provider.list_cases()}
    selected = list(cases.values())
    if args.suite:
        selected = [case for case in selected if case.metadata.get("suite") == args.suite]
    if args.ids:
        requested = set(args.ids)
        selected = [case for case in selected if case.id in requested]
    selected.sort(key=lambda case: case.id)
    if args.limit > 0:
        selected = selected[:args.limit]

    print(json.dumps({"event": "eval_round_start", "cases": len(selected)}, ensure_ascii=False))
    failures = 0
    results = []
    for case in selected:
        print(json.dumps({"event": "case_start", "case_id": case.id}, ensure_ascii=False), flush=True)
        try:
            result = await provider.run_case(case)
            result.metadata["version"] = args.version
            results.append(result)
            if not result.passed:
                failures += 1
            print(json.dumps({
                "event": "case_end",
                "case_id": result.case_id,
                "run_id": result.run_id,
                "status": result.status,
                "passed": result.passed,
                "score": result.score,
                "duration_ms": result.duration_ms,
                "tool_calls": result.tool_calls,
                "total_tokens": result.total_tokens,
                "error": result.error,
                "failure_category": result.failure_category,
            }, ensure_ascii=False), flush=True)
        except Exception as exc:
            failures += 1
            print(json.dumps({
                "event": "case_end",
                "case_id": case.id,
                "status": "error",
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }, ensure_ascii=False), flush=True)
    performance_result = ""
    if results:
        performance_path = write_performance_result(
            results, version=args.version, label=args.label,
        )
        performance_result = str(performance_path)
        print(json.dumps({
            "event": "performance_result_registered",
            "version": args.version,
            "result_id": performance_result,
            "cases": len(results),
        }, ensure_ascii=False), flush=True)
    print(json.dumps({
        "event": "eval_round_end",
        "cases": len(selected),
        "failures": failures,
        "performance_result": performance_result,
    }, ensure_ascii=False))
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
