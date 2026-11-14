"""Real Atlas lifecycle transitions and transactional redaction of synthetic data."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest

from memorybank.embeddings import content_hash
from memorybank.lifecycle import LifecycleConflict
from memorybank.memory_gateway import AccessDenied, MemoryGateway, ServerIdentity
from memorybank.proposals import MemoryWriter, StaleProposal, canonical_policy
from memorybank.seed import initial_memory, seed_database

ADMIN = ServerIdentity("finance_admin", "agent_b", "demo_company", "procurement")
EMPLOYEE = ServerIdentity("employee", "agent_b", "demo_company", "procurement")
BUYER = ServerIdentity("employee", "agent_a", "demo_company", "procurement")


class FakeEmbeddings:
    model = "lifecycle-test-model"
    dimensions = 2

    def documents(self, texts):
        return [[0.4, 0.5] for _ in texts]


@pytest.fixture
def case(atlas_db):
    seed_database(atlas_db)
    supplier = f"test_{uuid4().hex}"
    memory = initial_memory()
    memory.update(_id=f"mem_{supplier}", policy_key=f"supplier:{supplier}:purchasing")
    memory["terms"]["supplier_id"] = supplier
    memory["content"] = canonical_policy(memory["terms"])
    memory["embedding"] = [float(int(uuid4().hex[:8], 16)), 0.5]
    memory["embedding_metadata"] = {
        "model": FakeEmbeddings.model,
        "dimensions": 2,
        "content_hash": content_hash(memory["content"]),
    }
    atlas_db.memories.insert_one(memory)
    return MemoryGateway(atlas_db, FakeEmbeddings()), memory


def purchase(memory):
    return {
        "action": "create_draft_order",
        "supplier_id": memory["terms"]["supplier_id"],
        "currency": "USD",
        "amount": 1000,
        "memory_id": memory["_id"],
        "memory_version": memory["version"],
    }


def proposal_for(gateway, memory):
    return gateway.propose_change(
        EMPLOYEE,
        memory["policy_key"],
        memory["version"],
        memory["content"],
        {**memory["terms"], "max_without_approval": 10000},
        "synthetic_source",
        "employee_message",
    )


@pytest.mark.parametrize(
    "method,state", [("expire_memory", "expired"), ("revoke_memory", "revoked")]
)
def test_transition_retains_history_but_blocks_reads_and_actions(case, method, state):
    gateway, memory = case
    assert gateway.validate_action(BUYER, **purchase(memory))["allowed"] is True
    result = getattr(gateway, method)(ADMIN, memory["_id"], 1)
    assert result["status"] == state
    stored = gateway.db.memories.find_one({"_id": memory["_id"]})
    assert stored["content"] == memory["content"]
    assert gateway.get_memory(BUYER, memory["_id"]) is None
    assert (
        gateway.validate_action(BUYER, **purchase(memory))["required_action"]
        == "ask_for_clarification"
    )
    assert getattr(gateway, method)(ADMIN, memory["_id"], 1)["changed"] is False


@pytest.mark.parametrize("method", ["expire_memory", "revoke_memory", "forget_memory"])
def test_employee_cannot_change_lifecycle(case, method):
    gateway, memory = case
    with pytest.raises(AccessDenied):
        getattr(gateway, method)(EMPLOYEE, memory["_id"], 1)
    assert gateway.get_memory(BUYER, memory["_id"]) is not None


def test_wrong_scope_or_version_cannot_change_memory(case):
    gateway, memory = case
    forged = ServerIdentity("finance_admin", "agent_b", "demo_company", "marketing")
    with pytest.raises(AccessDenied):
        gateway.forget_memory(forged, memory["_id"], 1)
    with pytest.raises(LifecycleConflict):
        gateway.revoke_memory(ADMIN, memory["_id"], 2)
    with pytest.raises(LifecycleConflict):
        gateway.forget_memory(ADMIN, "missing_memory", 1)


def test_forget_removes_payloads_and_linked_copies_but_preserves_unrelated_data(case):
    gateway, memory = case
    pending = proposal_for(gateway, memory)
    now = datetime.now(UTC)
    base = {
        "tenant_id": "demo_company",
        "team_id": "procurement",
        "user_id": "employee",
        "agent_id": "agent_a",
        "created_at": now,
    }
    run_id, unrelated_id = f"run_{uuid4().hex}", f"run_{uuid4().hex}"
    gateway.db.runs.insert_one(
        {
            "_id": run_id,
            **base,
            "status": "verified",
            "retrieved_memories": [{"memory_id": memory["_id"], "content": memory["content"]}],
            "unusual_payload_name": {"copy": memory["content"], "terms": memory["terms"]},
        }
    )
    gateway.db.runs.insert_one(
        {"_id": unrelated_id, **base, "status": "started", "note": "keep me"}
    )
    unrelated = gateway.db.runs.find_one({"_id": unrelated_id})
    event_id = f"event_{uuid4().hex}"
    gateway.db.memory_events.insert_one(
        {
            "_id": event_id,
            **base,
            "event_type": "legacy_note",
            "memory_id": memory["_id"],
            "reason": memory["content"],
            "embedding": memory["embedding"],
        }
    )
    result = gateway.forget_memory(ADMIN, memory["_id"], 1)
    assert result["status"] == "deleted" and result["redacted"]["runs"] == 1
    stored = gateway.db.memories.find_one({"_id": memory["_id"]})
    assert set(stored) == {
        "_id",
        "tenant_id",
        "team_id",
        "policy_key",
        "version",
        "created_at",
        "status",
        "forgotten_at",
        "forgotten_by_user_id",
        "forgotten_by_agent_id",
    }
    proposal = gateway.get_proposal(ADMIN, pending["proposal_id"])
    assert proposal["status"] == "deleted"
    assert (
        "content" not in proposal
        and "effective_content" not in proposal
        and "terms" not in proposal
    )
    run = gateway.db.runs.find_one({"_id": run_id})
    assert run["redacted"] is True and "unusual_payload_name" not in run
    event = gateway.db.memory_events.find_one({"_id": event_id})
    assert "reason" not in event and "embedding" not in event
    assert gateway.db.runs.find_one({"_id": unrelated_id}) == unrelated
    assert gateway.get_memory(BUYER, memory["_id"]) is None
    assert gateway.validate_action(BUYER, **purchase(memory))["allowed"] is False
    assert gateway.forget_memory(ADMIN, memory["_id"], 1)["changed"] is False
    seed_database(gateway.db)
    assert gateway.db.memories.find_one({"_id": memory["_id"]})["status"] == "deleted"


def test_forgetting_is_scoped_to_team(case):
    gateway, memory = case
    other = {
        "_id": f"run_{uuid4().hex}",
        "tenant_id": "demo_company",
        "team_id": "marketing",
        "user_id": "other_team_user",
        "agent_id": "agent_a",
        "status": "started",
        "created_at": datetime.now(UTC),
        "memory_id": memory["_id"],
        "note": "keep other team",
    }
    gateway.db.runs.insert_one(other)
    before = gateway.db.runs.find_one({"_id": other["_id"]})
    gateway.forget_memory(ADMIN, memory["_id"], 1)
    assert gateway.db.runs.find_one({"_id": other["_id"]}) == before


def test_forgetting_old_version_does_not_delete_approved_replacement(case):
    gateway, memory = case
    proposal = proposal_for(gateway, memory)
    approved = gateway.approve_proposal(ADMIN, proposal["proposal_id"], 1)
    gateway.forget_memory(ADMIN, memory["_id"], 1)
    assert gateway.get_memory(BUYER, approved["memory_id"])["version"] == 2
    assert gateway.get_proposal(ADMIN, proposal["proposal_id"])["status"] == "deleted"


def test_approval_of_forgotten_proposal_is_blocked(case):
    gateway, memory = case
    proposal = proposal_for(gateway, memory)
    gateway.forget_memory(ADMIN, memory["_id"], 1)
    with pytest.raises(StaleProposal):
        gateway.approve_proposal(ADMIN, proposal["proposal_id"], 1)


def test_forget_rolls_back_if_audit_write_fails(case, monkeypatch):
    gateway, memory = case
    proposal = proposal_for(gateway, memory)
    original = gateway.db.memories.find_one({"_id": memory["_id"]})

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated audit failure")

    monkeypatch.setattr(MemoryWriter, "_event", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        gateway.forget_memory(ADMIN, memory["_id"], 1)
    assert gateway.db.memories.find_one({"_id": memory["_id"]}) == original
    assert gateway.get_proposal(ADMIN, proposal["proposal_id"])["status"] == "pending_review"


def test_late_proposal_cannot_restore_content_after_forgetting(case, monkeypatch):
    gateway, memory = case
    original_target = MemoryWriter._target
    first = True

    def forget_during_initial_check(writer, *args, **kwargs):
        nonlocal first
        target = original_target(writer, *args, **kwargs)
        if first:
            first = False
            gateway.forget_memory(ADMIN, memory["_id"], 1)
        return target

    monkeypatch.setattr(MemoryWriter, "_target", forget_during_initial_check)
    proposal = proposal_for(gateway, memory)
    assert proposal["status"] == "rejected"
    stored = gateway.get_proposal(ADMIN, proposal["proposal_id"])
    assert "content" not in stored and "terms" not in stored and "source_ref" not in stored


def test_revoke_waits_for_guarded_dispatch_then_blocks_next_action(case, monkeypatch):
    gateway, memory = case
    lock_attempted = Event()
    from memorybank.policy_locks import policy_lock as real_lock

    def observed_lock(*args):
        lock_attempted.set()
        return real_lock(*args)

    monkeypatch.setattr("memorybank.lifecycle.policy_lock", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with gateway.validated_action(BUYER, **purchase(memory)) as allowed:
            assert allowed["allowed"] is True
            future = pool.submit(gateway.revoke_memory, ADMIN, memory["_id"], 1)
            assert lock_attempted.wait(10) and not future.done()
        assert future.result(timeout=15)["status"] == "revoked"
    assert gateway.validate_action(BUYER, **purchase(memory))["allowed"] is False
