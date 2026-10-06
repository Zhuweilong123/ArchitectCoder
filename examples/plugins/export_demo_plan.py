"""Generate the public demo plan from repository defaults, without application .env."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
for root in (REPO, REPO / "backend"):
    sys.path.insert(0, str(root))

from backend.config.plugin_catalog import read_manifest, scan_manifests
from app.agent_base.core.plugins import PluginManager, manifest_spec
from app.agent_base.core.lifecycle import build_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/media/plugin-demo/plugin-plan.json")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    # Relative sources make this public sample independent of the author's machine.
    os.chdir(REPO)
    declarations = (*scan_manifests((REPO / "extensions",)),
                    read_manifest(REPO / "examples/plugins/task_notes/plugin.json"))
    specs = tuple(manifest_spec({**item, "source": Path(item["source"]).relative_to(REPO).as_posix()})
                  for item in declarations)
    plan = build_plan(PluginManager(specs), SimpleNamespace()).as_dict()
    failures = [item for item in plan["plugins"] if item["status"] == "unavailable"]
    if failures:
        raise RuntimeError("Demo declarations unavailable: " + "; ".join(
            f"{item['name']}: {item['error']}" for item in failures))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Demo plan: {len(plan['plugins'])} plugins, {len(plan['stages'])} phases; {output}")


if __name__ == "__main__":
    main()
