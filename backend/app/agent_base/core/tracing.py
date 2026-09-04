"""Compatibility import for the relocated Trace subsystem."""

import sys

from app.trace import tracing as _implementation

sys.modules[__name__] = _implementation
