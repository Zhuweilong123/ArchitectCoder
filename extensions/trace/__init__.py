"""Trace extension implementations."""

from .chat_trace import create

__all__ = ["create"]


def list_contributions(*, settings=None):
    from app.agent_base.core.hooks import HookEvent
    from app.agent_base.core.lifecycle import Contribution
    return tuple(Contribution(
        id=f"trace.lifecycle.{stage.value}", stage=stage,
        handler="extensions.trace.lifecycle:observe", mode="observer", priority=-100,
    ) for stage in HookEvent)
