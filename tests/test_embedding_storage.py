"""Atlas storage/race checks with explicitly simulated embedding responses.

These test update guards, not vector search or Voyage model quality.
"""

from memorybank.embeddings import Embeddings, embed_demo_memories
from memorybank.seed import seed_database
from tests.test_embeddings import FakeVoyage


def test_embedding_updates_are_repeatable_and_do_not_change_policy(atlas_db):
    seed_database(atlas_db)
    before = list(atlas_db.memories.find().sort("_id"))
    client = FakeVoyage([[1.0, 0.5] for _ in before])
    embedder = Embeddings("test-only", "test-model", 2, client=client)
    result = embed_demo_memories(atlas_db, embedder)
    assert len(result["updated"]) == 5
    after = list(atlas_db.memories.find().sort("_id"))
    for old, new in zip(before, after, strict=True):
        assert new.pop("embedding") == [1.0, 0.5]
        assert new.pop("embedding_metadata")["model"] == "test-model"
        assert new == old
    assert len(embed_demo_memories(atlas_db, embedder)["skipped"]) == 5
    assert len(client.calls) == 1  # Unchanged data does not spend API requests again.


def test_revocation_during_api_call_does_not_receive_stale_vector(atlas_db):
    seed_database(atlas_db)
    embed_demo_memories(
        atlas_db,
        Embeddings("test-only", "test-model", 2, client=FakeVoyage([[1.0, 0.5] for _ in range(5)])),
    )
    atlas_db.memories.update_one(
        {"_id": "mem_northstar_v1"},
        {"$unset": {"embedding": "", "embedding_metadata": ""}},
    )

    class RevokingVoyage(FakeVoyage):
        def embed(self, **kwargs):
            atlas_db.memories.update_one(
                {"_id": "mem_northstar_v1"}, {"$set": {"status": "revoked"}}
            )
            return super().embed(**kwargs)

    embedder = Embeddings("test-only", "test-model", 2, client=RevokingVoyage([[1.0, 0.5]]))
    result = embed_demo_memories(atlas_db, embedder)
    assert result["changed_during_embedding"] == ["mem_northstar_v1"]
    assert "embedding" not in atlas_db.memories.find_one({"_id": "mem_northstar_v1"})
    # Repeating preparation must not restore revoked content to the embedding pipeline.
    assert "mem_northstar_v1" in embed_demo_memories(atlas_db, embedder)["skipped"]
