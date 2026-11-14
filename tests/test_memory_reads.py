"""Real Atlas permission/lifecycle checks with a simulated stale search result.

No search index or external embedding API is used by these boundary tests.
"""

from datetime import UTC, datetime, timedelta

import pytest

from memorybank.embeddings import content_hash
from memorybank.memory_gateway import AccessDenied, MemoryGateway, ServerIdentity
from memorybank.seed import search_demo_memories, seed_database

IDENTITY = ServerIdentity("employee", "agent_a", "demo_company", "procurement")


class TestEmbedder:
    __test__ = False
    model = "boundary-test-model"
    dimensions = 2

    def __init__(self):
        self.calls = 0

    def query(self, text):
        self.calls += 1
        return [1.0, 0.5]


@pytest.fixture
def gateway(atlas_db, monkeypatch):
    seed_database(atlas_db)
    # Reset only the isolated test fixtures so tests can run independently.
    for memory in search_demo_memories():
        memory["embedding"] = [1.0, 0.5]
        memory["embedding_metadata"] = {
            "model": TestEmbedder.model,
            "dimensions": 2,
            "content_hash": content_hash(memory["content"]),
        }
        atlas_db.memories.replace_one({"_id": memory["_id"]}, memory, upsert=True)
    atlas_db.principals.update_many({}, {"$set": {"enabled": True}})
    monkeypatch.setattr("memorybank.memory_gateway.require_search_ready", lambda *args: None)
    instance = MemoryGateway(atlas_db, TestEmbedder())
    # Deliberately include wrong-team and superseded records as if the index were stale.
    candidates = list(atlas_db.memories.find({}, {"_id": 1, "version": 1, "embedding_metadata": 1}))
    monkeypatch.setattr(instance, "_candidates", lambda *args: candidates)
    return instance


def test_current_recheck_excludes_private_and_superseded_candidates(gateway):
    results = gateway.retrieve_memories(IDENTITY, "What can I purchase?")
    assert {m["memory_id"] for m in results} == {
        "mem_northstar_v1",
        "mem_bluebird_v1",
        "mem_cedar_office_v1",
    }
    assert all(
        set(m) == {"memory_id", "version", "policy_key", "content", "terms"} for m in results
    )
    assert gateway.get_memory(IDENTITY, "mem_marketing_v1") is None
    assert gateway.get_memory(IDENTITY, "mem_northstar_retired_v1") is None


@pytest.mark.parametrize("principal", ["employee", "agent_a"])
def test_disabled_identity_is_denied_before_embedding(gateway, principal):
    gateway.db.principals.update_one({"_id": principal}, {"$set": {"enabled": False}})
    with pytest.raises(AccessDenied):
        gateway.retrieve_memories(IDENTITY, "Northstar policy")
    assert gateway.embedder.calls == 0


def test_forged_scope_is_rejected(gateway):
    identity = ServerIdentity("employee", "agent_a", "demo_company", "marketing")
    with pytest.raises(AccessDenied):
        gateway.retrieve_memories(identity, "Marketing policy")
    with pytest.raises(AccessDenied):
        gateway.get_memory(identity, "mem_marketing_v1")


def test_identity_fields_cannot_contain_database_operators(gateway):
    identity = ServerIdentity({"$ne": None}, "agent_a", "demo_company", "procurement")
    with pytest.raises(AccessDenied):
        gateway.retrieve_memories(identity, "Northstar")
    assert gateway.embedder.calls == 0


@pytest.mark.parametrize("status", ["revoked", "expired", "superseded", "deleted"])
def test_stale_candidate_cannot_expose_unusable_memory(gateway, status):
    update = {"$set": {"status": status}}
    if status == "deleted":
        update["$unset"] = {field: "" for field in ("content", "terms", "provenance", "embedding")}
    gateway.db.memories.update_one({"_id": "mem_northstar_v1"}, update)
    assert gateway.retrieve_memories(IDENTITY, "Northstar", "northstar") == []
    assert gateway.get_memory(IDENTITY, "mem_northstar_v1") is None


def test_deadline_is_enforced_even_when_status_is_active(gateway):
    gateway.db.memories.update_one(
        {"_id": "mem_northstar_v1"},
        {"$set": {"valid_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    assert gateway.retrieve_memories(IDENTITY, "Northstar", "northstar") == []


def test_future_start_and_open_ended_validity(gateway):
    gateway.db.memories.update_one(
        {"_id": "mem_northstar_v1"},
        {"$set": {"valid_from": datetime.now(UTC) + timedelta(days=1)}},
    )
    assert gateway.get_memory(IDENTITY, "mem_northstar_v1") is None
    gateway.db.memories.update_one(
        {"_id": "mem_northstar_v1"},
        {"$set": {"valid_from": datetime.now(UTC) - timedelta(seconds=1), "valid_until": None}},
    )
    assert gateway.get_memory(IDENTITY, "mem_northstar_v1") is not None


def test_supplier_and_embedding_content_are_rechecked(gateway):
    assert [
        m["memory_id"] for m in gateway.retrieve_memories(IDENTITY, "Bluebird", "bluebird")
    ] == ["mem_bluebird_v1"]
    gateway.db.memories.update_one(
        {"_id": "mem_northstar_v1"}, {"$set": {"content": "Changed without re-embedding"}}
    )
    assert gateway.retrieve_memories(IDENTITY, "Northstar", "northstar") == []


def test_identity_disabled_during_search_is_rejected(gateway, monkeypatch):
    def disable_during_search(*args):
        gateway.db.principals.update_one({"_id": "agent_a"}, {"$set": {"enabled": False}})
        return []

    monkeypatch.setattr(gateway, "_candidates", disable_during_search)
    with pytest.raises(AccessDenied):
        gateway.retrieve_memories(IDENTITY, "Northstar")
