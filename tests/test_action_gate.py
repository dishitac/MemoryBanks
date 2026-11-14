"""Real Atlas checks and correction/dispatch serialization; simulated embeddings."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest

from memorybank.embeddings import content_hash
from memorybank.memory_gateway import AccessDenied, MemoryGateway, ServerIdentity
from memorybank.proposals import canonical_policy
from memorybank.seed import initial_memory, seed_database

BUYER = ServerIdentity("employee", "agent_a", "demo_company", "procurement")
ADMIN = ServerIdentity("finance_admin", "agent_b", "demo_company", "procurement")


class TestEmbeddings:
    __test__ = False
    model = "action-test-model"
    dimensions = 2

    def __init__(self):
        self.embedding_ready = None

    def documents(self, texts):
        if self.embedding_ready:
            self.embedding_ready.set()
        return [[1.0, 0.5] for _ in texts]


@pytest.fixture
def case(atlas_db):
    seed_database(atlas_db)
    atlas_db.principals.update_many({}, {"$set": {"enabled": True}})
    supplier = f"test_{uuid4().hex}"
    memory = initial_memory()
    memory.update(_id=f"mem_{supplier}", policy_key=f"supplier:{supplier}:purchasing")
    memory["terms"]["supplier_id"] = supplier
    memory["content"] = canonical_policy(memory["terms"])
    memory["embedding"] = [1.0, 0.5]
    memory["embedding_metadata"] = {
        "model": TestEmbeddings.model,
        "dimensions": 2,
        "content_hash": content_hash(memory["content"]),
    }
    atlas_db.memories.insert_one(memory)
    gateway = MemoryGateway(atlas_db, TestEmbeddings())
    request = {
        "action": "create_draft_order",
        "supplier_id": supplier,
        "currency": "USD",
        "amount": 7000,
        "memory_id": memory["_id"],
        "memory_version": 1,
    }
    return gateway, memory, request


def replacement_proposal(gateway, memory):
    return gateway.propose_change(
        ADMIN,
        memory["policy_key"],
        1,
        "Raise the limit to ten thousand dollars.",
        {**memory["terms"], "max_without_approval": 10000},
        "synthetic_test",
        "finance_admin",
    )


def test_above_limit_order_is_blocked_but_approval_request_is_allowed(case):
    gateway, _, request = case
    blocked = gateway.validate_action(BUYER, **request)
    assert blocked["allowed"] is False and blocked["action_to_dispatch"] is None
    assert blocked["required_action"] == "request_finance_approval"
    allowed = gateway.validate_action(BUYER, **{**request, "action": "request_finance_approval"})
    assert allowed["allowed"] is True
    payload = allowed["action_to_dispatch"]
    assert payload["amount"] == 7000 and payload["requested_by_user_id"] == "employee"
    assert payload["submitted_by_agent_id"] == "agent_a"
    event = gateway.db.memory_events.find_one({"_id": allowed["validation_event_id"]})
    assert event["event_type"] == "action_allowed"


def test_exact_limit_order_is_allowed(case):
    gateway, _, request = case
    assert gateway.validate_action(BUYER, **{**request, "amount": 5000})["allowed"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"supplier_id": "different_supplier"},
        {"currency": "CAD"},
        {"amount": True},
        {"amount": "7000"},
        {"amount": -1},
        {"amount": 1.5},
        {"memory_version": 2},
        {"memory_id": {"$ne": None}},
        {"action": "send_money"},
    ],
)
def test_invalid_or_mismatched_action_cannot_dispatch(case, change):
    gateway, _, request = case
    result = gateway.validate_action(BUYER, **{**request, **change})
    assert result["allowed"] is False and result["action_to_dispatch"] is None


def test_clarification_never_authorizes_external_dispatch(case):
    gateway, _, _ = case
    result = gateway.validate_action(BUYER, "ask_for_clarification")
    assert result["allowed"] is False
    assert result["required_action"] == "ask_for_clarification"


def test_disabled_user_or_wrong_agent_cannot_authorize_a_purchase(case):
    gateway, _, request = case
    with pytest.raises(AccessDenied):
        gateway.validate_action(ADMIN, **request)
    gateway.db.principals.update_one({"_id": "employee"}, {"$set": {"enabled": False}})
    with pytest.raises(AccessDenied):
        gateway.validate_action(BUYER, **request)


def test_forged_team_cannot_authorize_a_purchase(case):
    gateway, _, request = case
    forged = ServerIdentity("employee", "agent_a", "demo_company", "marketing")
    with pytest.raises(AccessDenied):
        gateway.validate_action(forged, **request)


@pytest.mark.parametrize("status", ["expired", "revoked", "superseded", "deleted"])
def test_unusable_memory_requires_clarification(case, status):
    gateway, memory, request = case
    update = {"$set": {"status": status}}
    if status == "deleted":
        update["$unset"] = {field: "" for field in ("content", "terms", "provenance", "embedding")}
    gateway.db.memories.update_one({"_id": memory["_id"]}, update)
    result = gateway.validate_action(BUYER, **request)
    assert result["allowed"] is False and result["required_action"] == "ask_for_clarification"


def test_active_memory_with_expired_deadline_cannot_authorize(case):
    gateway, memory, request = case
    gateway.db.memories.update_one(
        {"_id": memory["_id"]},
        {"$set": {"valid_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    assert gateway.validate_action(BUYER, **request)["allowed"] is False


def test_v2_changes_allowed_action_and_v1_cannot_be_reused(case):
    gateway, memory, request = case
    proposal = replacement_proposal(gateway, memory)
    approved = gateway.approve_proposal(ADMIN, proposal["proposal_id"], 1)
    assert gateway.validate_action(BUYER, **request)["allowed"] is False
    result = gateway.validate_action(
        BUYER,
        **{
            **request,
            "memory_id": approved["memory_id"],
            "memory_version": approved["version"],
        },
    )
    assert result["allowed"] is True
    assert result["action_to_dispatch"]["action"] == "create_draft_order"


def test_audit_failure_prevents_entering_dispatch_block(case, monkeypatch):
    gateway, _, request = case
    dispatched = False

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Simulated audit outage")

    monkeypatch.setattr("memorybank.actions.ActionValidator._record", fail_audit)
    with pytest.raises(RuntimeError, match="audit outage"):
        with gateway.validated_action(BUYER, **{**request, "amount": 1000}):
            dispatched = True
    assert dispatched is False


def test_deadline_is_rechecked_after_audit_io(case, monkeypatch):
    from memorybank.actions import ActionValidator

    gateway, memory, request = case
    original_record = ActionValidator._record

    def expire_during_audit(validator, identity, scope, result):
        recorded = original_record(validator, identity, scope, result)
        if result["allowed"]:
            gateway.db.memories.update_one(
                {"_id": memory["_id"]},
                {"$set": {"valid_until": datetime.now(UTC) - timedelta(seconds=1)}},
            )
        return recorded

    monkeypatch.setattr(ActionValidator, "_record", expire_during_audit)
    with gateway.validated_action(BUYER, **{**request, "amount": 1000}) as result:
        assert result["allowed"] is False
        assert result["action_to_dispatch"] is None


def test_correction_waits_until_guarded_dispatch_finishes(case, monkeypatch):
    gateway, memory, request = case
    proposal = replacement_proposal(gateway, memory)
    embedding_ready, lock_attempted = Event(), Event()
    gateway.embedder.embedding_ready = embedding_ready
    from memorybank.policy_locks import policy_lock as real_lock

    def observed_lock(*args):
        lock_attempted.set()
        return real_lock(*args)

    monkeypatch.setattr("memorybank.proposals.policy_lock", observed_lock)

    def approve():
        return gateway.approve_proposal(ADMIN, proposal["proposal_id"], 1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with gateway.validated_action(
            BUYER, **{**request, "action": "request_finance_approval"}
        ) as gate:
            assert gate["allowed"] is True
            future = pool.submit(approve)
            assert embedding_ready.wait(10) and lock_attempted.wait(10)
            assert not future.done()
            assert gateway.db.memories.find_one({"_id": memory["_id"]})["status"] == "active"
        assert future.result(timeout=15)["status"] == "approved"
    assert gateway.validate_action(BUYER, **request)["allowed"] is False
