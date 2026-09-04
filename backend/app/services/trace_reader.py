"""Compatibility import for the relocated trace reader."""

import sys

from app.trace import trace_reader as _implementation

sys.modules[__name__] = _implementation
