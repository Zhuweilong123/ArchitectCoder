"""Read the project's validation target catalog without executing checks."""
import json
from pathlib import Path

from app.services.design_validation import default_validation_registry


def project_validation_targets(manifest):
    files = list(manifest.get("project_files") or [])
    if manifest.get("project_file") and manifest["project_file"] not in files:
        files.append(manifest["project_file"])
    if not files and manifest.get("design_root"):
        files = [str(path) for path in Path(manifest["design_root"]).glob("*")
                 if path.suffix.lower() in {".uml", ".umlproj"}]
    if not files:
        raise ValueError("No design project available to verify validation targets")
    registry = default_validation_registry()
    catalog = {"schema": set()}
    for file in files:
        diagrams = json.loads(Path(file).read_text(encoding="utf-8")).get("diagrams")
        if not isinstance(diagrams, list):
            raise ValueError(f"Invalid diagrams list in {file}")
        for diagram in diagrams:
            name = diagram.get("name")
            if not isinstance(name, str) or not name:
                continue
            catalog["schema"].add(name)
            for rule in registry.rules_for(diagram.get("diagram_type", "class")):
                catalog.setdefault(rule.rule_id, set()).add(name)
    return catalog
