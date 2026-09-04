"""Compatibility import for the relocated trace adapter."""

import sys

from app.trace import chat_trace as _implementation

sys.modules[__name__] = _implementation
