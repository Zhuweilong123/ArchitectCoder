"""从受控 cases 目录加载评测用例。"""

from __future__ import annotations

import json
import logging

from .models import EvalCase
from .paths import cases_dir
from .projects import load_projects

logger = logging.getLogger(__name__)


class EvalCatalogError(ValueError):
    """Raised when tracked case files violate the evaluation contract."""


def load_cases() -> dict[str, EvalCase]:
    result: dict[str, EvalCase] = {}
    errors: list[str] = []
    root = cases_dir()
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for field in ("schema_version", "tool_protocol_version"):
                if field not in payload:
                    raise ValueError(f"tracked case must declare {field}")
            case = EvalCase.model_validate(payload)
            if case.id in result:
                raise ValueError(f"duplicate evaluation case id: {case.id}")
            result[case.id] = case
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    if errors:
        message = "invalid evaluation case catalog:\n- " + "\n- ".join(errors)
        logger.error(message)
        # Silently dropping a case changes the denominator and can make a
        # broken catalog look better. The catalog is therefore fail-closed.
        raise EvalCatalogError(message)
    return result


def load_project_manifests():
    return load_projects()
