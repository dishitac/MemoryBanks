"""Protected immutable proposals and explicit finance-administrator review.

No LLM calls or attack classifier decide authorization. Source kind is supplied by the
server's ingestion route; user/agent identity always comes from ServerIdentity.
"""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from memorybank.embeddings import content_hash, validate_vector
from memorybank.memory_gateway import AccessDenied
from memorybank.policy_locks import policy_lock

MAX_INTEGER = 2**63 - 1
PAYLOAD_FIELDS = (
    "_id",
    "tenant_id",
    "team_id",
    "policy_key",
    "expected_version",
    "content",
    "terms",
    "source_ref",
    "source_kind",
    "requested_by_user_id",
    "submitted_by_agent_id",
    "effective_content",
)


class StaleProposal(RuntimeError):
    """A proposal or the policy it targets no longer matches its reviewed state."""


def payload_hash(proposal):
    payload = {key: proposal.get(key) for key in PAYLOAD_FIELDS}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_text(value, field, max_length=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{field} must be nonempty text of at most {max_length} characters")


def require_version(value):
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError("expected_version must be a positive whole-number policy version")


def canonical_policy(terms):
    if not isinstance(terms, dict) or set(terms) != {
        "supplier_id",
        "currency",
        "max_without_approval",
    }:
        raise ValueError("terms must contain only supplier_id, currency, max_without_approval")
    supplier = terms["supplier_id"]
    # Supplier IDs are identifiers, not a place to inject another instruction.
    if (
        not isinstance(supplier, str)
        or not supplier
        or len(supplier) > 100
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in supplier)
    ):
        raise ValueError("supplier_id must be a lowercase supplier identifier")
    limit = terms["max_without_approval"]
    if terms["currency"] != "USD" or type(limit) is not int or not 0 <= limit <= MAX_INTEGER:
        raise ValueError("Policy requires USD and a nonnegative whole-dollar limit")
    return f"{supplier} purchases above USD {limit:,} require finance approval."


class MemoryWriter:
    def __init__(self, gateway):
        self.gateway = gateway
        self.db = gateway.db
        self.embedder = gateway.embedder

    def _event(self, identity, scope, event_type, *, reason, session=None, **references):
        self.db.memory_events.insert_one(
            {
                "_id": f"event_{uuid4().hex}",
                **scope,
                "event_type": event_type,
                "user_id": identity.user_id,
                "agent_id": identity.agent_id,
                "created_at": datetime.now(UTC),
                "reason": reason,
                **references,
            },
            session=session,
        )

    def _onboarding(self, identity, *, session=None):
        scope = self.gateway._scope(identity, session=session)
        agent = self.db.principals.find_one(
            {
                "_id": identity.agent_id,
                "role": "onboarding_agent",
                **scope,
            },
            session=session,
        )
        if not agent:
            self._event(
                identity,
                scope,
                "write_denied",
                reason="Agent cannot submit/review policies",
                session=session,
            )
            raise AccessDenied("Only the onboarding agent's server path can submit/review policies")
        return scope

    def _reviewer(self, identity, *, session=None):
        scope = self._onboarding(identity, session=session)
        if not self.db.principals.find_one(
            {
                "_id": identity.user_id,
                "role": "finance_admin",
                **scope,
            },
            session=session,
        ):
            raise AccessDenied("Explicit finance-administrator review is required")
        return scope

    def _target(self, scope, policy_key, version, *, session=None):
        now = datetime.now(UTC)
        return self.db.memories.find_one(
            {
                **scope,
                "policy_key": policy_key,
                "version": version,
                "status": "active",
                "kind": "supplier_purchase_policy",
                "valid_from": {"$lte": now},
                "$or": [{"valid_until": None}, {"valid_until": {"$gt": now}}],
            },
            session=session,
        )

    def propose_change(
        self, identity, policy_key, expected_version, content, terms, source_ref, source_kind
    ):
        scope = self._onboarding(identity)
        require_text(policy_key, "policy_key")
        require_version(expected_version)
        require_text(content, "content", 10000)
        require_text(source_ref, "source_ref")
        require_text(source_kind, "source_kind", 100)
        reason = "Protected purchasing-policy change requires explicit finance review"
        status = "pending_review"
        try:
            effective_content = canonical_policy(terms)
            if source_kind not in ("employee_message", "supplier_document", "finance_admin"):
                raise ValueError("Unsupported source_kind")
            if source_kind == "finance_admin" and not self.db.principals.find_one(
                {
                    "_id": identity.user_id,
                    "role": "finance_admin",
                    **scope,
                }
            ):
                raise ValueError("An employee cannot claim a finance-administrator source")
            target = self._target(scope, policy_key, expected_version)
            if not target or target["terms"]["supplier_id"] != terms["supplier_id"]:
                raise ValueError("Policy unavailable, version stale, or supplier does not match")
        except ValueError as error:
            status, reason, effective_content = "rejected", str(error), None

        proposal = {
            "_id": f"proposal_{uuid4().hex}",
            **scope,
            "policy_key": policy_key,
            "expected_version": expected_version,
            "content": content,
            "source_ref": source_ref,
            "source_kind": source_kind,
            "requested_by_user_id": identity.user_id,
            "submitted_by_agent_id": identity.agent_id,
            "status": status,
            "reason": reason,
            "created_at": datetime.now(UTC),
        }
        if effective_content is not None:
            proposal["terms"] = dict(terms)
            proposal["effective_content"] = effective_content
        proposal["payload_hash"] = payload_hash(proposal)

        def save(session):
            self._onboarding(identity, session=session)
            self.db.memory_proposals.insert_one(proposal, session=session)
            self._event(
                identity,
                scope,
                f"proposal_{status}",
                reason=reason,
                session=session,
                proposal_id=proposal["_id"],
                policy_key=policy_key,
            )

        with self.db.client.start_session() as session:
            session.with_transaction(
                save, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
            )
        return {"proposal_id": proposal["_id"], "status": status, "reason": reason}

    def get_proposal(self, identity, proposal_id):
        scope = self._reviewer(identity)
        require_text(proposal_id, "proposal_id")
        proposal = self.db.memory_proposals.find_one({"_id": proposal_id, **scope})
        if not proposal:
            return None
        # There is no edit operation. Any payload edit requires a new proposal/review.
        if proposal.get("payload_hash") != payload_hash(proposal):
            raise StaleProposal("Proposal contents changed; submit a new proposal")
        return proposal

    def review(self, identity, proposal_id, expected_version, *, approve):
        require_text(proposal_id, "proposal_id")
        require_version(expected_version)
        try:
            scope = self._reviewer(identity)
        except AccessDenied:
            # Log known in-scope users' unauthorized approval attempts without copying content.
            scope = self.gateway._scope(identity)
            self._event(
                identity,
                scope,
                "review_denied",
                reason="Reviewer lacks required authority",
                proposal_id=proposal_id,
            )
            raise
        proposal = self.get_proposal(identity, proposal_id)
        if not proposal or proposal["status"] != "pending_review":
            raise StaleProposal("Proposal is unavailable or already reviewed")
        if proposal["expected_version"] != expected_version:
            raise StaleProposal("Expected policy version does not match the proposal")
        if not self._target(scope, proposal["policy_key"], expected_version):
            raise StaleProposal("Policy is no longer current/valid; submit a new proposal")
        effective = canonical_policy(proposal["terms"])
        if effective != proposal["effective_content"]:
            raise StaleProposal("Proposal terms changed; submit a new proposal")
        # External API calls stay outside retryable transaction callbacks.
        vector = self.embedder.documents([effective])[0] if approve else None
        if approve:
            validate_vector(vector, self.embedder.dimensions)
        new_id = f"mem_{uuid4().hex}"

        def decide(session):
            self._reviewer(identity, session=session)
            current_proposal = self.db.memory_proposals.find_one(
                {
                    "_id": proposal_id,
                    **scope,
                    "status": "pending_review",
                    "payload_hash": proposal["payload_hash"],
                },
                session=session,
            )
            if not current_proposal or payload_hash(current_proposal) != proposal["payload_hash"]:
                raise StaleProposal("Proposal changed or was already reviewed")
            target = self._target(scope, proposal["policy_key"], expected_version, session=session)
            if not target:
                raise StaleProposal("Policy changed or expired while review was in progress")
            now = datetime.now(UTC)
            decision = "approved" if approve else "rejected"
            review = {
                "status": decision,
                "reviewed_by_user_id": identity.user_id,
                "reviewed_by_agent_id": identity.agent_id,
                "reviewed_at": now,
                "reason": "Explicit finance-administrator decision",
            }
            result = {"proposal_id": proposal_id, "status": decision, "reason": review["reason"]}
            if approve:
                new_version = expected_version + 1
                if new_version > MAX_INTEGER:
                    raise ValueError("Policy version limit reached")
                self.db.memories.update_one(
                    {"_id": target["_id"], "status": "active", "version": expected_version},
                    {"$set": {"status": "superseded", "superseded_by": new_id}},
                    session=session,
                )
                self.db.memories.insert_one(
                    {
                        "_id": new_id,
                        **scope,
                        "policy_key": proposal["policy_key"],
                        "kind": "supplier_purchase_policy",
                        "version": new_version,
                        "status": "active",
                        "content": effective,
                        "terms": proposal["terms"],
                        "created_at": now,
                        "valid_from": now,
                        "valid_until": target["valid_until"],
                        "supersedes": target["_id"],
                        "superseded_by": None,
                        "embedding": vector,
                        "embedding_metadata": {
                            "model": self.embedder.model,
                            "dimensions": self.embedder.dimensions,
                            "content_hash": content_hash(effective),
                        },
                        "provenance": {
                            "requested_by_user_id": proposal["requested_by_user_id"],
                            "submitted_by_agent_id": proposal["submitted_by_agent_id"],
                            "source_ref": proposal["source_ref"],
                            "source_kind": proposal["source_kind"],
                            "approved_by_user_id": identity.user_id,
                        },
                    },
                    session=session,
                )
                review["result_memory_id"] = new_id
                result.update(memory_id=new_id, version=new_version)
            self.db.memory_proposals.update_one(
                {"_id": proposal_id, "status": "pending_review"}, {"$set": review}, session=session
            )
            self._event(
                identity,
                scope,
                f"proposal_{decision}",
                reason=review["reason"],
                proposal_id=proposal_id,
                policy_key=proposal["policy_key"],
                **(
                    {
                        "memory_id": new_id,
                        "version": expected_version + 1,
                        "supersedes": target["_id"],
                    }
                    if approve
                    else {}
                ),
                session=session,
            )
            return result

        with policy_lock(self.db, scope, proposal["policy_key"]):
            with self.db.client.start_session() as session:
                return session.with_transaction(
                    decide,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                )
