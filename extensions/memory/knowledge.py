"""Evidence ledger and current knowledge projection, independent of resource kind.

The host supplies opaque resource IDs/versions. File/environment adapters live in
the provider; this module also works with API revisions and other resource kinds.
Historical snapshots are append-only; the memories table remains the searchable
current projection, keeping the existing schema/API compatible.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from uuid import uuid4

from .models import MemoryType, _utc_now


class KnowledgeLedger:
    def __init__(self, db):
        self.db = db
        db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                event_type TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_events_project
                ON memory_events(project_id, created_at);
            CREATE TABLE IF NOT EXISTS memory_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL, memory_id TEXT NOT NULL,
                event_id TEXT NOT NULL, created_at TEXT NOT NULL,
                snapshot TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_versions_entry
                ON memory_versions(project_id, memory_id, id);
            CREATE TABLE IF NOT EXISTS memory_resources (
                project_id TEXT NOT NULL, resource_id TEXT NOT NULL,
                version TEXT NOT NULL, event_id TEXT NOT NULL,
                PRIMARY KEY(project_id, resource_id)
            );
        """)

    def event(self, project_id, event_type, payload, event_id=""):
        event_id = event_id or uuid4().hex
        self.db.conn.execute(
            "INSERT OR IGNORE INTO memory_events VALUES (?, ?, ?, ?, ?)",
            (event_id, project_id, event_type, _utc_now(), json.dumps(payload, ensure_ascii=False)),
        )
        self.db.conn.commit()
        return event_id

    def snapshot(self, entry, event_id, *, status=None):
        data = copy.deepcopy(entry.to_dict())
        if status:
            data.setdefault("metadata", {}).setdefault("knowledge", {})["status"] = status
        self.db.conn.execute(
            "INSERT INTO memory_versions(project_id,memory_id,event_id,created_at,snapshot) VALUES (?,?,?,?,?)",
            (entry.project_id, entry.id, event_id, _utc_now(), json.dumps(data, ensure_ascii=False)),
        )
        self.db.conn.commit()

    def versions(self, project_id, memory_id):
        return [dict(row, snapshot=json.loads(row["snapshot"])) for row in self.db.conn.execute(
            "SELECT * FROM memory_versions WHERE project_id=? AND memory_id=? ORDER BY id",
            (project_id, memory_id),
        )]

    def resources(self, project_id):
        return {row["resource_id"]: row["version"] for row in self.db.conn.execute(
            "SELECT resource_id,version FROM memory_resources WHERE project_id=?", (project_id,),
        )}

    def migrate_legacy(self, project_id):
        affected = []
        for entry in self.db.list_by_project(project_id):
            if entry.memory_type not in {MemoryType.INSIGHT, MemoryType.OPERATIONAL_LESSON} or "knowledge" in entry.metadata:
                continue
            event_id = self.event(project_id, "legacy_needs_review", {"memory_id": entry.id, "reason": "missing_source_conditions"})
            self.snapshot(entry, event_id)
            entry.metadata["knowledge"] = {"status": "needs_review", "verification": "legacy_unverified",
                                           "scope": {"kind": "project", "id": project_id}, "sources": [],
                                           "reason": "missing_source_conditions", "version": 1}
            self.db.update(entry)
            self.snapshot(entry, event_id)
            affected.append(entry.id)
        return affected

    def observe(self, project_id, event_type, resources, *, run_id="", trace_id="",
                event_id="", reason="", evidence=()):
        payload = {"resources": list(resources), "run_id": run_id, "trace_id": trace_id,
                   "reason": reason, "evidence": list(evidence)}
        if event_id and self.db.conn.execute("SELECT 1 FROM memory_events WHERE id=?", (event_id,)).fetchone():
            return {"event_id": event_id, "affected": [], "duplicate": True}
        event_id = self.event(project_id, event_type, payload, event_id)
        current = self.resources(project_id)
        incoming = {r["resource_id"]: r["version"] for r in resources if r.get("resource_id") and r.get("version")}
        changed = {key for key, value in incoming.items() if current.get(key) != value}
        affected = []
        for entry in self.db.list_by_project(project_id):
            knowledge = entry.metadata.get("knowledge", {})
            if knowledge.get("status", "active") in {"superseded", "rejected"}:
                continue
            refs = knowledge.get("sources", [])
            mismatched = [r["resource_id"] for r in refs if r.get("resource_id") in incoming
                          and r.get("version") != incoming[r["resource_id"]]]
            # Legacy unbound observations cannot be attributed to a resource.
            # Keep their evidence, but stop presenting them as current facts.
            unbound = event_type == "resource_changed" and changed and not refs and entry.memory_type == MemoryType.INSIGHT
            if not mismatched and not unbound:
                continue
            if knowledge.get("status") == "needs_review":
                continue
            self.snapshot(entry, event_id)
            knowledge = dict(knowledge)
            knowledge.update(status="needs_review", reason="source_changed" if mismatched else "unbound_observation_after_change",
                             changed_resources=mismatched or sorted(changed), event_id=event_id)
            entry.metadata["knowledge"] = knowledge
            self.db.update(entry)
            self.snapshot(entry, event_id)
            affected.append({"id": entry.id, "subject": entry.subject, "status": "needs_review", "reason": knowledge["reason"]})
        for key, value in incoming.items():
            self.db.conn.execute(
                "INSERT INTO memory_resources VALUES(?,?,?,?) ON CONFLICT(project_id,resource_id) DO UPDATE SET version=excluded.version,event_id=excluded.event_id",
                (project_id, key, value, event_id),
            )
        self.db.conn.commit()
        return {"event_id": event_id, "affected": affected}

    @staticmethod
    def scope_matches(entry, context):
        scope = entry.metadata.get("knowledge", {}).get("scope", {"kind": "project", "id": entry.project_id})
        kind, identifier = scope.get("kind", "project"), scope.get("id", entry.project_id)
        return identifier == entry.project_id if kind == "project" else context.get(kind) == identifier

    def select_current(self, project_id, results, *, scope_context=None):
        current = self.resources(project_id)
        selected, skipped = [], []
        for result in results:
            entry = result.entry
            knowledge = entry.metadata.get("knowledge", {})
            status = knowledge.get("status", "active")
            reason = ""
            if status != "active":
                reason = status
            elif not self.scope_matches(entry, scope_context or {}):
                reason = "scope_mismatch"
            elif knowledge.get("valid_until") and datetime.fromisoformat(knowledge["valid_until"]).timestamp() <= datetime.now(timezone.utc).timestamp():
                reason = "expired"
            elif any(current.get(r.get("resource_id")) != r.get("version") for r in knowledge.get("sources", [])):
                reason = "source_version_mismatch"
            if reason:
                skipped.append({"id": entry.id, "subject": entry.subject, "reason": reason})
            else:
                selected.append(result)
        return selected, skipped

    def validate(self, project_id, memory_ids, resources, *, event_type, reason, run_id="", trace_id=""):
        """Explicit host attestation; observation/recall never calls this method."""
        if not reason.strip():
            return {"affected": [], "skipped": [{"reason": "validation_reason_required"}]}
        supplied = {r["resource_id"]: r for r in resources if r.get("resource_id") and r.get("version")}
        current = self.resources(project_id)
        affected, skipped = [], []
        event_id = self.event(project_id, event_type, {"memory_ids": list(memory_ids), "reason": reason,
                                                     "resources": list(resources), "run_id": run_id, "trace_id": trace_id})
        for identifier in dict.fromkeys(memory_ids):
            entry = self.db.get(project_id, identifier)
            if entry is None:
                skipped.append({"id": identifier, "reason": "not_found"})
                continue
            knowledge = entry.metadata.get("knowledge", {})
            keys = {r["resource_id"] for r in knowledge.get("sources", [])}
            if knowledge.get("status") in {"superseded", "rejected"} or not keys.issubset(supplied) or any(current.get(k) != supplied[k]["version"] for k in keys):
                skipped.append({"id": identifier, "reason": "validation_sources_incomplete_or_stale"})
                continue
            self.snapshot(entry, event_id, status="superseded")
            version = knowledge.get("version", 1)
            knowledge = dict(knowledge)
            knowledge.update(status="active", verification="confirmed", reason=reason, version=version + 1,
                             sources=[dict(supplied[k]) for k in sorted(keys)], confirmation_event_id=event_id,
                             supersedes={"id": identifier, "version": version})
            entry.metadata["knowledge"] = knowledge
            entry.metadata.setdefault("governance", {})["confirmed"] = True
            self.db.update(entry)
            self.snapshot(entry, event_id)
            affected.append({"id": identifier, "subject": entry.subject, "status": "active", "reason": reason})
        return {"event_id": event_id, "affected": affected, "skipped": skipped}
