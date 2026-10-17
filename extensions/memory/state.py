"""Memory lifecycle state owned by one extension request."""
from dataclasses import dataclass, field

from app.agent_base.host_api.data import JsonObject
from .plugin_api import MemoryResource


@dataclass
class MemoryRunState:
    query: str = ""
    run_id: str = ""
    resources: dict[str, MemoryResource] = field(default_factory=dict)
    recall_report: JsonObject = field(default_factory=dict)
    dirty: bool = False
    refreshed: bool = False
    archive_scheduled: bool = False

    def reset(self, *, query: str = "", run_id: str = "") -> None:
        self.query = query
        self.run_id = run_id
        self.resources = {}
        self.recall_report = {}
        self.dirty = False
        self.refreshed = False
        self.archive_scheduled = False
