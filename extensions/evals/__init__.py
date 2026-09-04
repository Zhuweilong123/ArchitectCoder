"""Local evaluation extension.

Evaluation case discovery, execution, batch management and persistence all
belong to this package.  ``app.evals`` remains a compatibility facade.
"""

from .models import EvalCase, EvalResult, EvalTurn, CheckerResult, ProjectManifest
from .provider import create
from .runner import EvalRunner

__all__ = [
    "create", "EvalCase", "EvalResult", "EvalTurn", "CheckerResult",
    "ProjectManifest", "EvalRunner",
]
