"""Export plugin organization without starting the backend."""
import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent
for _root in (_BACKEND_ROOT.parent, _BACKEND_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from backend.config import Settings
from app.agent_base.core.lifecycle import build_plan
from app.agent_base.core.plugins import get_plugin_manager


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Output directory; defaults to PLUGIN_PLAN_DIR")
    parser.add_argument("--check", action="store_true", help="Validate declarations without constructing providers; exit 1 for unavailable plugins")
    args = parser.parse_args(argv)
    settings = Settings(_env_file=_BACKEND_ROOT / ".env")
    plan = build_plan(get_plugin_manager(), settings)
    output = args.output or settings.plugin_plan_dir
    plan.write(output)
    print(f"Plugin plan {plan.as_dict()['plan_id']} written to {output}")
    if args.check:
        for plugin in plan.plugins:
            print(f"{plugin['name']} {plugin['version'] or 'unversioned'}: {plugin['status']}")
            for diagnostic in plugin["diagnostics"]:
                print(f"  [{diagnostic['phase']}/{diagnostic['code']}] {diagnostic['message']}")
        return int(any(plugin["status"] == "unavailable" for plugin in plan.plugins))
    return 0


if __name__ == "__main__":
    sys.exit(main())
