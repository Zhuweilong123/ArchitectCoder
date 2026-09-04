"""Repository-level extension entry points.

Each subpackage exposes a ``create(**kwargs)`` factory.  The Agent Core loads
these entry points through ``PluginManager``; domain-specific provider code can
still live next to its storage or runtime implementation.
"""

import sys
from pathlib import Path

# The backend is commonly started with ``backend`` as the working directory,
# while extension entry points live one level above it.  Make both repository
# packages importable once the extensions package is discovered.
_backend_root = str(Path(__file__).resolve().parent.parent / "backend")
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)
