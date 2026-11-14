"""Authorized expiry, revocation, and selected-version forgetting.

Forgetting replaces affected records with small metadata-only tombstones. This intentionally
redacts the entire affected run/proposal/event, not arbitrary fragments of its free text.
All operations coordinate with correction and dispatch in the single-process demo.
"""

from datetime import UTC, datetime

from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from memorybank.policy_locks import policy_lock
from memorybank.proposals import MemoryWriter, require_text, require_version


class LifecycleConflict(RuntimeError):
    """Missing/out-of-scope memory, wrong version, or incompatible lifecycle transition."""


def references(value, ids):
    """Recognize nested memory/proposal references, including arrays of IDs."""
    if isinstance(value, dict):
        return any(references(item, ids) for item in value.values())
    if isinstance(value, list):
        return any(references(item, ids) for item in value)
    return isinstance(value, str) and value in ids


def contains_copy(value, content, embedding):
    if isinstance(value, str):
        return bool(content) and content in value
    if isinstance(value, dict):
        return any(contains_copy(item, content, embedding) for item in value.values())
    if isinstance(value, list):
        if embedding and value == embedding:
            return True
        return any(contains_copy(item, content, embedding) for item in value)
    return False


def metadata(document, fields):
    return {field: document[field] for field in fields if field in document}


class MemoryLifecycle:
    def __init__(self, gateway):
        self.gateway = gateway
        self.db = gateway.db
        self.writer = MemoryWriter(gateway)

    def _selected(self, scope, memory_id, version, *, session=None):
        memory = self.db.memories.find_one({"_id": memory_id, **scope}, session=session)
        if not memory:
            raise LifecycleConflict("Memory is unavailable or outside authorized scope")
        if memory["version"] != version:
            raise LifecycleConflict("Selected memory version does not match")
        return memory

    def change(self, identity, memory_id, expected_version, operation):
        if operation not in ("expire", "revoke", "forget"):
            raise ValueError("Unsupported lifecycle operation")
        require_text(memory_id, "memory_id", 200)
        require_version(expected_version)
        scope = self.writer._reviewer(identity)
        reference = self._selected(scope, memory_id, expected_version)

        def mutate(session):
            self.writer._reviewer(identity, session=session)
            memory = self._selected(scope, memory_id, expected_version, session=session)
            now = datetime.now(UTC)
            if operation == "forget":
                return self._forget(identity, scope, memory, now, session)
            state = "expired" if operation == "expire" else "revoked"
            if memory["status"] == state:
                return {
                    "memory_id": memory_id,
                    "version": expected_version,
                    "status": state,
                    "changed": False,
                }
            allowed = ("active",) if operation == "expire" else ("active", "expired")
            if memory["status"] not in allowed:
                raise LifecycleConflict("Selected version cannot make this lifecycle transition")
            changes = {"status": state, f"{state}_at": now}
            if operation == "expire":
                changes["valid_until"] = now
            self.db.memories.update_one({"_id": memory_id}, {"$set": changes}, session=session)
            self.writer._event(
                identity,
                scope,
                f"memory_{state}",
                reason=f"Administrator {operation} operation",
                memory_id=memory_id,
                memory_version=expected_version,
                policy_key=memory["policy_key"],
                session=session,
            )
            return {
                "memory_id": memory_id,
                "version": expected_version,
                "status": state,
                "changed": True,
            }

        with policy_lock(self.db, scope, reference["policy_key"]):
            with self.db.client.start_session() as session:
                return session.with_transaction(
                    mutate,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                )

    def _forget(self, identity, scope, memory, now, session):
        memory_id, version = memory["_id"], memory["version"]
        if memory["status"] == "deleted":
            return {
                "memory_id": memory_id,
                "version": version,
                "status": "deleted",
                "changed": False,
            }
        content, embedding = memory.get("content"), memory.get("embedding")
        ids = {memory_id}
        counts = {"proposals": 0, "runs": 0, "events": 0}
        policy_key = memory["policy_key"]
        # Find associated proposals before redacting their payload/reference fields.
        for proposal in self.db.memory_proposals.find(scope, session=session):
            related = (
                (
                    proposal.get("policy_key") == policy_key
                    and proposal.get("expected_version") == version
                )
                or references(proposal, ids)
                or contains_copy(proposal, content, embedding)
            )
            if not related:
                continue
            ids.add(proposal["_id"])
            tombstone = metadata(
                proposal,
                (
                    "_id",
                    "tenant_id",
                    "team_id",
                    "policy_key",
                    "expected_version",
                    "requested_by_user_id",
                    "submitted_by_agent_id",
                    "created_at",
                ),
            )
            tombstone.update(status="deleted", forgotten_memory_id=memory_id, redacted_at=now)
            self.db.memory_proposals.replace_one(
                {"_id": proposal["_id"]}, tombstone, session=session
            )
            counts["proposals"] += 1
        for collection, required in (
            (
                "runs",
                ("_id", "tenant_id", "team_id", "user_id", "agent_id", "status", "created_at"),
            ),
            (
                "memory_events",
                (
                    "_id",
                    "tenant_id",
                    "team_id",
                    "user_id",
                    "agent_id",
                    "event_type",
                    "created_at",
                    "memory_id",
                    "memory_version",
                    "proposal_id",
                    "policy_key",
                    "version",
                    "supersedes",
                ),
            ),
        ):
            for record in self.db[collection].find(scope, session=session):
                if not (references(record, ids) or contains_copy(record, content, embedding)):
                    continue
                tombstone = metadata(record, required)
                tombstone.update(redacted=True, redacted_at=now, forgotten_memory_id=memory_id)
                self.db[collection].replace_one({"_id": record["_id"]}, tombstone, session=session)
                counts["runs" if collection == "runs" else "events"] += 1
        tombstone = metadata(
            memory,
            (
                "_id",
                "tenant_id",
                "team_id",
                "policy_key",
                "version",
                "created_at",
            ),
        )
        tombstone.update(
            status="deleted",
            forgotten_at=now,
            forgotten_by_user_id=identity.user_id,
            forgotten_by_agent_id=identity.agent_id,
        )
        self.db.memories.replace_one({"_id": memory_id}, tombstone, session=session)
        self.writer._event(
            identity,
            scope,
            "memory_forgotten",
            reason="Selected memory and controlled linked copies removed",
            memory_id=memory_id,
            memory_version=version,
            policy_key=policy_key,
            session=session,
        )
        return {
            "memory_id": memory_id,
            "version": version,
            "status": "deleted",
            "changed": True,
            "redacted": counts,
        }
