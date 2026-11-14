"""Governed memory access. Import only from trusted server/tool code.

ServerIdentity comes from a server session, never an LLM or an unverified HTTP body.
Database checks bind both identities to that scope; this is not a login implementation.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from pymongo import ReadPreference

from memorybank.embeddings import content_hash, validate_vector
from memorybank.search import INDEX_NAME, require_search_ready


@dataclass(frozen=True)
class ServerIdentity:
    user_id: str
    agent_id: str
    tenant_id: str
    team_id: str


class AccessDenied(PermissionError):
    pass


class MemoryGateway:
    def __init__(self, db, embedder):
        # Primary reads avoid accepting stale lifecycle data from a replica secondary.
        self.db = db.with_options(read_preference=ReadPreference.PRIMARY)
        self.embedder = embedder

    def _scope(self, identity, *, session=None):
        if not isinstance(identity, ServerIdentity):
            raise AccessDenied("A server-bound identity is required")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (identity.user_id, identity.agent_id, identity.tenant_id, identity.team_id)
        ):
            raise AccessDenied("Identity fields must be nonempty server-bound strings")
        scope = {"tenant_id": identity.tenant_id, "team_id": identity.team_id}
        user = self.db.principals.find_one(
            {
                "_id": identity.user_id,
                "kind": "user",
                "enabled": True,
                **scope,
                "role": {"$in": ["employee", "finance_admin"]},
            },
            session=session,
        )
        agent = self.db.principals.find_one(
            {
                "_id": identity.agent_id,
                "kind": "agent",
                "enabled": True,
                **scope,
                "role": {"$in": ["purchasing_agent", "onboarding_agent"]},
            },
            session=session,
        )
        if not user or not agent:
            raise AccessDenied("User and agent must be enabled and authorized for this scope")
        return scope

    def _usable_filter(self, scope, supplier_id=None):
        # PyMongo defaults to naive UTC datetime values; use naive UTC in these comparisons.
        now = datetime.now(UTC).replace(tzinfo=None)
        query = {
            **scope,
            "status": "active",
            "kind": "supplier_purchase_policy",
            "valid_from": {"$lte": now},
            "$or": [{"valid_until": None}, {"valid_until": {"$gt": now}}],
            "embedding_metadata.model": self.embedder.model,
            "embedding_metadata.dimensions": self.embedder.dimensions,
        }
        if supplier_id is not None:
            query["terms.supplier_id"] = supplier_id
        return query

    def _read_current(self, scope, selector, supplier_id=None):
        memory = self.db.memories.find_one({**selector, **self._usable_filter(scope, supplier_id)})
        if not memory:
            return None
        metadata = memory.get("embedding_metadata", {})
        if metadata.get("content_hash") != content_hash(memory["content"]):
            return None  # Text was changed without regenerating its embedding.
        try:
            validate_vector(memory.get("embedding"), self.embedder.dimensions)
        except ValueError:
            return None
        return {
            "memory_id": memory["_id"],
            "version": memory["version"],
            "policy_key": memory["policy_key"],
            "content": memory["content"],
            "terms": memory["terms"],
        }

    def _candidates(self, vector, scope, supplier_id, limit):
        prefilter = {
            **scope,
            "status": "active",
            "kind": "supplier_purchase_policy",
            "valid_from": {"$lte": datetime.now(UTC).replace(tzinfo=None)},
            "embedding_metadata.model": self.embedder.model,
            "embedding_metadata.dimensions": self.embedder.dimensions,
        }
        if supplier_id is not None:
            prefilter["terms.supplier_id"] = supplier_id
        # Exact nearest-neighbor search suits this five-record demo. Date expiry is
        # enforced on primary reads, including open-ended policies, not via index cleanup.
        return list(
            self.db.memories.aggregate(
                [
                    {
                        "$vectorSearch": {
                            "index": INDEX_NAME,
                            "path": "embedding",
                            "queryVector": vector,
                            "exact": True,
                            "limit": max(20, limit),
                            "filter": prefilter,
                        }
                    },
                    {"$project": {"_id": 1, "version": 1, "embedding_metadata.content_hash": 1}},
                ]
            )
        )

    def retrieve_memories(self, identity, query_text, supplier_id=None, *, limit=5):
        scope = self._scope(identity)
        if not isinstance(query_text, str) or not query_text.strip():
            raise ValueError("query_text must be nonempty text")
        if supplier_id is not None and (
            not isinstance(supplier_id, str) or not supplier_id.strip()
        ):
            raise ValueError("supplier_id must be nonempty text")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        require_search_ready(self.db.memories, self.embedder.dimensions)
        vector = self.embedder.query(query_text)
        validate_vector(vector, self.embedder.dimensions)
        candidates = self._candidates(vector, scope, supplier_id, limit)
        self._scope(identity)  # Identity may have been disabled during the API/search call.
        results = []
        for candidate in candidates:
            selector = {
                "_id": candidate["_id"],
                "version": candidate["version"],
                "embedding_metadata.content_hash": candidate.get("embedding_metadata", {}).get(
                    "content_hash", ""
                ),
            }
            memory = self._read_current(scope, selector, supplier_id)
            if memory:
                results.append(memory)
            if len(results) == limit:
                break
        return results

    def get_memory(self, identity, memory_id):
        """Direct-ID reads use the same primary-record scope/lifecycle protections."""
        scope = self._scope(identity)
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("memory_id must be nonempty text")
        return self._read_current(scope, {"_id": memory_id})

    def propose_change(
        self, identity, policy_key, expected_version, content, terms, source_ref, source_kind
    ):
        from memorybank.proposals import MemoryWriter

        return MemoryWriter(self).propose_change(
            identity, policy_key, expected_version, content, terms, source_ref, source_kind
        )

    def get_proposal(self, identity, proposal_id):
        from memorybank.proposals import MemoryWriter

        return MemoryWriter(self).get_proposal(identity, proposal_id)

    def approve_proposal(self, identity, proposal_id, expected_version):
        from memorybank.proposals import MemoryWriter

        return MemoryWriter(self).review(identity, proposal_id, expected_version, approve=True)

    def reject_proposal(self, identity, proposal_id, expected_version):
        from memorybank.proposals import MemoryWriter

        return MemoryWriter(self).review(identity, proposal_id, expected_version, approve=False)

    def validated_action(
        self,
        identity,
        action,
        supplier_id=None,
        currency=None,
        amount=None,
        memory_id=None,
        memory_version=None,
    ):
        """Hold the policy lock across final validation and synchronous server dispatch.

        Use as a `with` block. Never await inside it or dispatch after exiting the block.
        The caller must execute only when allowed, use the normalized payload, add a server
        idempotency key, read back the external record, and save the run outcome.
        """
        from memorybank.actions import ActionValidator

        return ActionValidator(self).guarded(
            identity, action, supplier_id, currency, amount, memory_id, memory_version
        )

    def validate_action(
        self,
        identity,
        action,
        supplier_id=None,
        currency=None,
        amount=None,
        memory_id=None,
        memory_version=None,
    ):
        """Inspection-only decision. Use validated_action for actual server dispatch."""
        with self.validated_action(
            identity, action, supplier_id, currency, amount, memory_id, memory_version
        ) as result:
            return result

    def expire_memory(self, identity, memory_id, expected_version):
        from memorybank.lifecycle import MemoryLifecycle

        return MemoryLifecycle(self).change(identity, memory_id, expected_version, "expire")

    def revoke_memory(self, identity, memory_id, expected_version):
        from memorybank.lifecycle import MemoryLifecycle

        return MemoryLifecycle(self).change(identity, memory_id, expected_version, "revoke")

    def forget_memory(self, identity, memory_id, expected_version):
        from memorybank.lifecycle import MemoryLifecycle

        return MemoryLifecycle(self).change(identity, memory_id, expected_version, "forget")
