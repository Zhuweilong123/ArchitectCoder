"""Canonical stage definitions remain separate from historical input names."""
import pytest

from app.agent_base.compat.hook_events import LEGACY_STAGE_ALIASES
from app.agent_base.core.hooks import HookContext, HookEvent, HookRegistry, NOTIFICATIONS, PUBLIC_STAGES


def test_core_enum_contains_only_canonical_stages_and_notifications():
    expected = {event.name for event in (*PUBLIC_STAGES, *NOTIFICATIONS)}
    assert set(HookEvent.__members__) == expected
    assert len(PUBLIC_STAGES) == 13
    assert all(event.name.lower() == event.value for event in HookEvent)
    assert all(alias.upper() not in HookEvent.__members__ for alias in LEGACY_STAGE_ALIASES)


@pytest.mark.parametrize('legacy,canonical', LEGACY_STAGE_ALIASES.items())
def test_historical_inputs_resolve_to_single_canonical_event(legacy, canonical):
    event = HookEvent(legacy)
    assert event is HookEvent(canonical)
    assert event.value == canonical
    seen = []
    registry = HookRegistry()
    registry.register(event, lambda context: seen.append(context.event), mode='observer')
    registry.trigger(HookEvent(canonical), HookContext(HookEvent(canonical), 'test'))
    assert seen == [HookEvent(canonical)]


@pytest.mark.parametrize('value', ['unknown_stage', 'MEMORY_REINFORCE', None, []])
def test_unknown_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        HookEvent(value)
