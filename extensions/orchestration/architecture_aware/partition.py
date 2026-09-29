"""Explainable exploration cost and bounded two-way graph partitioning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from app.agent_base.tools.my_tools.file_inventory import inspect_file

from .graph_files import resolve_node_file
from .impact import ImpactSlice


# Fixed initial reference values. They are score scales, not measured budgets.
_REFERENCE = (3000, 20, 12, 20, 20)
_FEATURE_NAMES = ("tokens", "symbols", "dependencies", "complexity", "change_impact")
_FEATURE_WEIGHTS = {name: 0.2 for name in _FEATURE_NAMES}
_COST_MODEL_VERSION = "file-signals-log-mean-v1"
_RELATION_WEIGHT = {
    "implements": 5.0,
    "realization": 5.0,
    "dependency": 3.0,
    "imports": 3.0,
    "messages": 3.0,
    "references": 3.0,
    "tests": 1.0,
    "contains": 0.5,
}


@dataclass(frozen=True)
class NodeCost:
    score: float
    features: dict[str, float]
    normalized_features: dict[str, float]
    measurements: dict[str, int | None]
    feature_source: str


@dataclass(frozen=True)
class ExplorationPackage:
    id: str
    node_ids: tuple[str, ...]
    cost: float


@dataclass(frozen=True)
class PartitionDecision:
    packages: tuple[ExplorationPackage, ...]
    node_costs: dict[str, NodeCost]
    objective: float
    max_load: float
    cut_weight: float
    conflict: float = 0.0  # First release delegates read-only work only.
    units: dict[str, tuple[str, ...]] | None = None
    unit_costs: dict[str, float] | None = None
    node_units: dict[str, str] | None = None
    unit_assignments: dict[str, str] | None = None
    total_link_weight: float = 0.0
    unit_links: tuple[dict[str, Any], ...] = ()
    weighted_edges: tuple[dict[str, Any], ...] = ()


def estimate_costs(
    impact: ImpactSlice, *, project_file: str = "", source_dir: str = "",
    test_dir: str = "",
) -> dict[str, NodeCost]:
    degree: dict[str, int] = {node_id: 0 for node_id in impact.nodes}
    incoming: dict[str, int] = {node_id: 0 for node_id in impact.nodes}
    for source, target, _ in impact.edges:
        degree[source] = degree.get(source, 0) + 1
        degree[target] = degree.get(target, 0) + 1
        incoming[target] = incoming.get(target, 0) + 1

    result: dict[str, NodeCost] = {}
    for node_id, node in impact.nodes.items():
        span = max(1, min(int(node.get("limit") or 1), 500))
        node_type = str(node.get("node_type") or "")
        symbols = {
            "source_file": 8, "test_file": 4,
            "class": 3, "interface": 3, "component": 3,
        }.get(node_type, 1)
        token_count = {
            "source_file": 800, "test_file": 400,
        }.get(node_type, 80 + span * 8)
        path = resolve_node_file(
            node, project_file=project_file, source_dir=source_dir,
            test_dir=test_dir,
        ) if node.get("source") in {"code", "test"} else None
        signals = inspect_file(path) if path else None
        if signals:
            if node_type in {"source_file", "test_file"}:
                token_count = max(80, math.ceil(signals.bytes / 4))
                symbols = max(1, (signals.symbol_hints or 0)
                              + (signals.interface_hints or 0))
            else:
                token_count = max(80, min(math.ceil(signals.bytes / 4), 80 + span * 8))
                symbols = max(1, min(signals.symbol_hints or 1, 8))
        dependency_count = degree.get(node_id, 0) + (
            (signals.dependency_hints or 0)
            if signals and node_type in {"source_file", "test_file"} else 0
        )
        complexity = 1 + math.log1p(signals.lines if signals and signals.lines is not None
                                     and node_type in {"source_file", "test_file"} else span)
        raw = (
            token_count,
            symbols,
            dependency_count,
            complexity,
            incoming.get(node_id, 0),
        )
        scaled = tuple(
            math.log1p(value) / math.log1p(reference)
            for value, reference in zip(raw, _REFERENCE)
        )
        names = _FEATURE_NAMES
        normalized_features = dict(zip(names, scaled))
        measurements = {
            "file_bytes": signals.bytes if signals else None,
            "file_lines": signals.lines if signals else None,
            "symbol_hints": signals.symbol_hints if signals else None,
            "interface_hints": signals.interface_hints if signals else None,
            "dependency_hints": signals.dependency_hints if signals else None,
            "graph_degree": degree.get(node_id, 0),
            "incoming_edges": incoming.get(node_id, 0),
            "source_span_lines": span,
        }
        result[node_id] = NodeCost(
            score=max(0.1, sum(scaled) / len(scaled)),
            features=dict(zip(names, raw)),
            normalized_features=normalized_features,
            measurements=measurements,
            feature_source="file_signals" if signals else "graph_fallback",
        )
    return result


def _unit_key(node_id: str, node: dict[str, Any]) -> str:
    location = str(node.get("file") or node.get("path") or "").strip()
    if location:
        return "file:" + str(PurePath(location))
    # Design entities without a file coordinate are independently readable.
    return "entity:" + node_id


def partition_impact(
    impact: ImpactSlice, *, max_workers: int = 2, project_file: str = "",
    source_dir: str = "", test_dir: str = "",
) -> PartitionDecision:
    """Minimize worst read load plus weighted cross-package coupling.

    Groups co-located file entities, makes a deterministic greedy split, and
    improves it through bounded single-group moves. A one-package candidate
    remains available when splitting has no useful gain.
    """

    if not impact.nodes:
        raise ValueError("impact slice is empty")
    costs = estimate_costs(
        impact, project_file=project_file, source_dir=source_dir,
        test_dir=test_dir,
    )
    units: dict[str, list[str]] = {}
    for node_id, node in impact.nodes.items():
        units.setdefault(_unit_key(node_id, node), []).append(node_id)
    keys = sorted(units)
    unit_cost: dict[str, float] = {}
    for key, ids in units.items():
        scores = sorted((costs[node_id].score for node_id in ids), reverse=True)
        # Reading several symbols from one file shares context.
        unit_cost[key] = scores[0] + 0.35 * sum(scores[1:])

    node_unit = {node_id: key for key, ids in units.items() for node_id in ids}
    links: dict[tuple[str, str], float] = {}
    for source, target, relation in impact.edges:
        left, right = node_unit.get(source), node_unit.get(target)
        if not left or not right or left == right:
            continue
        pair = tuple(sorted((left, right)))
        links[pair] = max(links.get(pair, 0.0), _RELATION_WEIGHT.get(relation, 1.0))

    total_load = sum(unit_cost.values())
    total_link = max(1.0, sum(links.values()))

    def evaluate(assignment: dict[str, int]) -> tuple[float, float, float]:
        loads = [0.0, 0.0]
        for key, owner in assignment.items():
            loads[owner] += unit_cost[key]
        cut = sum(
            weight for (left, right), weight in links.items()
            if assignment[left] != assignment[right]
        )
        worst = max(loads)
        return worst / max(total_load, 0.1) + 0.45 * cut / total_link, worst, cut

    single = {key: 0 for key in keys}
    chosen = single
    score, worst, cut = evaluate(single)
    if max_workers >= 2 and len(keys) >= 2:
        greedy: dict[str, int] = {}
        loads = [0.0, 0.0]
        for key in sorted(keys, key=lambda item: (-unit_cost[item], item)):
            owner = 0 if loads[0] <= loads[1] else 1
            greedy[key] = owner
            loads[owner] += unit_cost[key]
        best = greedy
        best_score, best_worst, best_cut = evaluate(best)
        for _ in range(20):
            candidate = None
            for key in keys:
                trial = dict(best)
                trial[key] = 1 - trial[key]
                if len(set(trial.values())) < 2:
                    continue
                trial_score, trial_worst, trial_cut = evaluate(trial)
                if trial_score + 0.01 < best_score and (
                    candidate is None or trial_score < candidate[0]
                ):
                    candidate = (trial_score, trial_worst, trial_cut, trial)
            if candidate is None:
                break
            best_score, best_worst, best_cut, best = candidate
        if best_score + 0.05 < score:
            chosen, score, worst, cut = best, best_score, best_worst, best_cut

    packages: list[ExplorationPackage] = []
    for owner in sorted(set(chosen.values())):
        assigned = tuple(
            node_id for key in keys if chosen[key] == owner
            for node_id in sorted(units[key])
        )
        packages.append(ExplorationPackage(
            id=f"explore_{owner + 1}",
            node_ids=assigned,
            cost=round(sum(unit_cost[key] for key in keys if chosen[key] == owner), 4),
        ))
    return PartitionDecision(
        packages=tuple(packages),
        node_costs=costs,
        objective=round(score, 4),
        max_load=round(worst, 4),
        cut_weight=round(cut, 4),
        units={key: tuple(sorted(ids)) for key, ids in units.items()},
        unit_costs={key: round(value, 6) for key, value in unit_cost.items()},
        node_units=node_unit,
        unit_assignments={key: packages[chosen[key]].id for key in keys},
        total_link_weight=total_link,
        unit_links=tuple({
            "left_unit": left,
            "right_unit": right,
            "weight": weight,
            "cut": chosen[left] != chosen[right],
        } for (left, right), weight in sorted(links.items())),
        weighted_edges=tuple({
            "source": source,
            "target": target,
            "relation": relation,
            "weight": _RELATION_WEIGHT.get(relation, 1.0),
            "cut": node_unit.get(source) != node_unit.get(target)
                   and chosen[node_unit[source]] != chosen[node_unit[target]],
        } for source, target, relation in sorted(impact.edges)),
    )
