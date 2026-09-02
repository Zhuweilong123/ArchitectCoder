"""从受控 cases 目录加载评测用例。"""

from __future__ import annotations

import json

from .models import EvalCase
from .paths import cases_dir
from .projects import load_projects


def load_cases() -> dict[str, EvalCase]:
    result: dict[str, EvalCase] = {}
    root = cases_dir()
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            case = EvalCase.model_validate(json.loads(path.read_text(encoding="utf-8")))
            result[case.id] = case
        except Exception:
            # 单个损坏用例不应阻止其他用例列出；运行时由 CI 日志定位。
            continue
    return result


def load_project_manifests():
    return load_projects()
