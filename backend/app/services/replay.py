"""Compatibility import for the relocated trace replay adapter."""

import sys

from app.trace import replay as _implementation

sys.modules[__name__] = _implementation
