"""Behavior checks: real MongoDB must reject invalid and conflicting records."""

from copy import deepcopy

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from memorybank.schema import setup_database
from memorybank.seed import initial_memory, seed_database


def test_setup_can_repeat_without_losing_records(atlas_db):
    seed_database(atlas_db)
    before = atlas_db.memories.find_one({"_id": "mem_northstar_v1"})
    setup_database(atlas_db)
    assert atlas_db.memories.find_one({"_id": before["_id"]}) == before
    assert set(atlas_db.list_collection_names()) == {
        "principals",
        "memories",
        "memory_proposals",
        "memory_events",
        "runs",
    }


def test_seed_preserves_modified_policy_and_disabled_identity(atlas_db):
    seed_database(atlas_db)
    atlas_db.memories.update_one({"_id": "mem_northstar_v1"}, {"$set": {"status": "revoked"}})
    atlas_db.principals.update_one({"_id": "employee"}, {"$set": {"enabled": False}})
    assert seed_database(atlas_db) is False
    assert atlas_db.memories.find_one({"_id": "mem_northstar_v1"})["status"] == "revoked"
    assert atlas_db.principals.find_one({"_id": "employee"})["enabled"] is False
    assert atlas_db.memory_events.count_documents({"_id": "event_seed_northstar_v1"}) == 1


def test_missing_owner_and_string_dates_are_rejected(atlas_db):
    for field, value in (("team_id", None), ("valid_until", "tomorrow")):
        memory = initial_memory()
        memory["_id"] = f"invalid_{field}"
        if value is None:
            memory.pop(field)
        else:
            memory[field] = value
        with pytest.raises(WriteError) as failure:
            atlas_db.memories.insert_one(memory)
        assert failure.value.code == 121  # Document validation failure.


def test_two_active_versions_are_rejected_but_history_is_allowed(atlas_db):
    first = initial_memory()
    first.update(_id="active_test_v1", policy_key="active_constraint_test")
    atlas_db.memories.insert_one(first)
    second = deepcopy(first)
    second.update(_id="active_test_v2", version=2)
    with pytest.raises(DuplicateKeyError):
        atlas_db.memories.insert_one(second)
    atlas_db.memories.update_one({"_id": first["_id"]}, {"$set": {"status": "superseded"}})
    atlas_db.memories.insert_one(second)
    duplicate = deepcopy(first)
    duplicate["_id"] = "same_version_different_id"
    with pytest.raises(DuplicateKeyError):
        atlas_db.memories.insert_one(duplicate)


def test_deleted_memory_cannot_keep_content(atlas_db):
    memory = initial_memory()
    memory.update(_id="forget_test", policy_key="forget_test", status="deleted")
    with pytest.raises(WriteError) as failure:
        atlas_db.memories.insert_one(memory)
    assert failure.value.code == 121
    for field in ("content", "terms", "provenance", "embedding"):
        memory.pop(field, None)
    atlas_db.memories.insert_one(memory)
    assert "content" not in atlas_db.memories.find_one({"_id": "forget_test"})


def test_setup_refuses_to_adopt_unvalidated_existing_records(atlas_db):
    # Temporarily relax only this test database's runs collection.
    atlas_db.command("collMod", "runs", validator={})
    atlas_db.runs.insert_one({"_id": "legacy_run"})
    with pytest.raises(RuntimeError, match="explicit migration required"):
        setup_database(atlas_db)
    atlas_db.runs.delete_one({"_id": "legacy_run"})
    setup_database(atlas_db)
