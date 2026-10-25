"""Validate persisted evaluation data without dropping historical fields."""
from collections.abc import Callable
import logging
from pathlib import Path
from typing import TypedDict, cast

from pydantic import ConfigDict, TypeAdapter

from app.agent_base.host_api.data import JsonObject
from .batch_models import EvalBatch
from .models import EvalResult
from .plugin_api import EvalBaseline, EvalBatchRecord
from .summary import EvalSummary


logger = logging.getLogger(__name__)


class PersistedDataError(ValueError):
    """A file or record cannot satisfy its persisted data contract."""


class ArchiveSnapshot(TypedDict, total=False):
    archive_id: str
    created_at: str
    note: str
    batch: EvalBatchRecord | None


_OBJECT = TypeAdapter(JsonObject, config=ConfigDict(allow_inf_nan=False))
_BASELINE = TypeAdapter(EvalBaseline)
_ARCHIVE = TypeAdapter(ArchiveSnapshot)


def read_baseline(path: Path) -> EvalBaseline:
    """Missing baselines stay distinct from malformed ones at the HTTP boundary."""
    try:
        value = _OBJECT.validate_json(path.read_bytes(), strict=True)
        _BASELINE.validate_python(value, strict=True)
        if "case_count" not in value:
            raise ValueError("missing case_count")
    except ValueError as exc:
        raise PersistedDataError(f"invalid evaluation baseline: {path}") from exc
    # Validation checks known fields; returning the original object retains
    # additional fields from older snapshots and plugin-specific producers.
    return cast(EvalBaseline, value)


def read_archive(path: Path) -> ArchiveSnapshot:
    try:
        value = _OBJECT.validate_json(path.read_bytes(), strict=True)
        archive = _ARCHIVE.validate_python(value, strict=True)
        batch = archive.get("batch")
        if batch is not None:
            if "summary" in batch:
                EvalSummary.model_validate(batch["summary"], strict=True)
            for result in batch.get("results", []):
                EvalResult.model_validate(result, strict=True)
    except ValueError as exc:
        raise PersistedDataError(f"invalid evaluation archive: {path}") from exc
    return cast(ArchiveSnapshot, value)


def validate_batch(value: JsonObject) -> None:
    EvalBatch.model_validate(value, strict=True)
    # This legacy timestamp is still used as a fallback when sorting history.
    if "created_at" in value and not isinstance(value["created_at"], str):
        raise ValueError("created_at must be a string")


def validate_result(value: JsonObject) -> None:
    EvalResult.model_validate(value, strict=True)


def read_jsonl(
    path: Path,
    validate: Callable[[JsonObject], None],
    *,
    limit: int | None = None,
    require_clean: bool = False,
) -> list[JsonObject]:
    """Read valid rows; mutations must refuse to rewrite a damaged source file."""
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        if require_clean:
            raise
        logger.warning("Cannot read evaluation history: %s", path)
        return []
    rows: list[JsonObject] = []
    # Recent-result views validate from the end instead of rebuilding every
    # historical result. Full-file reads remain mandatory before a rewrite.
    bounded = limit is not None and not require_clean
    indices = range(len(lines) - 1, -1, -1) if bounded else range(len(lines))
    for index in indices:
        line = lines[index]
        if not line.strip():
            continue
        try:
            value = _OBJECT.validate_json(line, strict=True)
            validate(value)
        except ValueError as exc:
            location = f"{path}:{index + 1}"
            if require_clean:
                raise PersistedDataError(f"invalid evaluation history record: {location}") from exc
            logger.warning("Skipping invalid evaluation history record: %s", location)
            continue
        rows.append(value)
        if bounded and limit is not None and len(rows) >= max(1, limit):
            break
    if bounded:
        rows.reverse()
    return rows
