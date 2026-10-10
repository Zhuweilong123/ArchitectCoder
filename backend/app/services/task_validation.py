"""Host task acceptance fallback when an optional gate supplies no coverage."""
import asyncio
import json
from pathlib import Path
from app.agent_base.host_api.validation import CheckResult, ValidationDiagnostic, ValidationReport
from app.services.design_validation import validate_project_diagrams, format_validation_report


def _check(manifest, requirements):
    files = list(manifest.get("project_files") or [])
    if manifest.get("project_file") and manifest["project_file"] not in files:
        files.append(manifest["project_file"])
    if not files and manifest.get("design_root"):
        files = [str(p) for p in Path(manifest["design_root"]).glob("*") if p.suffix.lower() in {".uml", ".umlproj"}]
    diagrams = []
    try:
        for file in files:
            payload = json.loads(Path(file).read_text(encoding="utf-8"))
            entries = payload.get("diagrams")
            if not isinstance(entries, list):
                raise ValueError(f"Invalid diagrams list in {file}")
            diagrams.extend(entries)
        return validate_project_diagrams(diagrams, workspace_root=manifest.get("workspace_root", ""),
                                         requirements=requirements)
    except Exception as exc:
        return ValidationReport((CheckResult("task.validation", "validation_requirements", "unavailable", (
            ValidationDiagnostic("error", "VALIDATION_REQUIREMENT_UNMET", "validation_requirements",
                                 f"Required validation could not run: {exc}"),)),))


async def ensure_task_validation(request, requirements, review_failure=None):
    """Reuse a freshly evaluated gate report; never depend on plugin presence."""
    request.checkpoint["validation_requirements"] = list(requirements)
    if review_failure and request.allowed:
        request.checkpoint["task_validation"] = review_failure
        request.allowed = False
        request.message = review_failure["message"]
        request.stop_reason = "validation_requirements_unmet"
    if not requirements or not request.allowed:
        return
    contract = request.checkpoint.get("contract_check") or {}
    metadata = contract.get("metadata") or {}
    if metadata.get("validation_requirements") == list(requirements) and metadata.get("design_validation"):
        return
    report = await asyncio.to_thread(_check, request.data.get("workspace_manifest", {}), requirements)
    payload = {"event": "task_validation", "run_id": request.run_id,
               "validation_requirements": list(requirements), "report": report.to_dict()}
    request.checkpoint["task_validation"] = payload
    record = request.data.get("record_event")
    if record:
        record("task_validation", run_id=request.run_id,
               validation_requirements=list(requirements), report=report.to_dict())
    if report.has_errors:
        request.allowed = False
        request.message = "Error: required task validation failed\n" + format_validation_report(report)
        request.stop_reason = "validation_requirements_unmet"
