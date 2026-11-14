"""Local checks on the synthetic search dataset; no cloud credentials needed."""

from memorybank.seed import search_demo_memories


def test_search_examples_are_distinct_and_terms_match_limits():
    memories = search_demo_memories()
    assert len(memories) == len({m["_id"] for m in memories}) == 5
    assert len({(m["team_id"], m["policy_key"]) for m in memories}) == 5
    for memory in memories:
        amount = f"{memory['terms']['max_without_approval']:,}"
        assert f"USD {amount}" in memory["content"]
        assert memory["valid_until"] > memory["valid_from"]
    assert sum(m["status"] == "active" for m in memories) == 4
    assert sum(m["team_id"] == "marketing" for m in memories) == 1
    # Keep all embedding generation real and separate from seeding.
    assert all("embedding" not in m for m in memories)
