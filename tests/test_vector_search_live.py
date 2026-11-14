"""Opt-in end-to-end Voyage/Atlas search, with no fabricated vectors or keyword fallback."""

import time

import pytest

from memorybank.config import get_settings
from memorybank.embeddings import Embeddings, embed_demo_memories
from memorybank.memory_gateway import MemoryGateway, ServerIdentity
from memorybank.search import search_index_status, setup_search_index
from memorybank.seed import seed_database

NORTHSTAR_QUERY = "Can I spend seven thousand dollars with Northstar without asking finance?"
BLUEBIRD_QUERY = "What is the approval limit for a Bluebird order?"
IDENTITY = ServerIdentity("employee", "agent_a", "demo_company", "procurement")


@pytest.fixture(scope="module")
def live_gateway(request, atlas_db):
    if not request.config.getoption("--search"):
        pytest.skip("Pass --atlas --search to use Voyage and create a temporary search index")
    settings = get_settings()
    embedder = Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    seed_database(atlas_db)
    assert len(embed_demo_memories(atlas_db, embedder)["updated"]) == 5
    # Two real query embeddings in a single API call to conserve free-tier requests.
    vectors = embedder._embed([NORTHSTAR_QUERY, BLUEBIRD_QUERY], "query")

    class CachedRealQueries:
        model = embedder.model
        dimensions = embedder.dimensions

        def query(self, text):
            return dict(zip([NORTHSTAR_QUERY, BLUEBIRD_QUERY], vectors, strict=True))[text]

    setup_search_index(atlas_db.memories, embedder.dimensions)
    gateway = MemoryGateway(atlas_db, CachedRealQueries())
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        index = search_index_status(atlas_db.memories, embedder.dimensions)
        if index.get("status") == "FAILED":
            pytest.fail("Atlas search index build failed")
        if index.get("queryable") and index.get("status") == "READY":
            # Queryable doesn't guarantee all newly inserted records are indexed yet.
            results = gateway.retrieve_memories(IDENTITY, NORTHSTAR_QUERY)
            if len(results) == 3:
                return gateway
        time.sleep(2)
    pytest.fail("Search index/data did not become ready within 180 seconds; rerun later")


def test_real_paraphrase_retrieves_northstar_and_excludes_distractors(live_gateway):
    results = live_gateway.retrieve_memories(IDENTITY, NORTHSTAR_QUERY)
    assert results[0]["memory_id"] == "mem_northstar_v1"
    assert {m["memory_id"] for m in results} == {
        "mem_northstar_v1",
        "mem_bluebird_v1",
        "mem_cedar_office_v1",
    }


def test_real_supplier_filter_returns_bluebird(live_gateway):
    results = live_gateway.retrieve_memories(IDENTITY, BLUEBIRD_QUERY, "bluebird")
    assert [m["memory_id"] for m in results] == ["mem_bluebird_v1"]


def test_real_team_filter_does_not_expose_procurement_to_marketing(live_gateway):
    live_gateway.db.principals.insert_one(
        {
            "_id": "marketing_agent",
            "kind": "agent",
            "role": "purchasing_agent",
            "tenant_id": "demo_company",
            "team_id": "marketing",
            "enabled": True,
        }
    )
    identity = ServerIdentity("other_team_user", "marketing_agent", "demo_company", "marketing")
    results = live_gateway.retrieve_memories(identity, NORTHSTAR_QUERY)
    assert [m["memory_id"] for m in results] == ["mem_marketing_v1"]
    assert live_gateway.retrieve_memories(identity, NORTHSTAR_QUERY, "northstar") == []
    assert live_gateway.get_memory(identity, "mem_northstar_v1") is None
