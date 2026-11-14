"""Local checks for linked-copy detection and minimal metadata retention."""

from memorybank.lifecycle import contains_copy, metadata, references


def test_nested_references_and_arrays_are_found():
    assert references({"retrieved": [{"memory_id": "mem_1"}]}, {"mem_1"})
    assert references({"memory_ids": ["mem_1"]}, {"mem_1"})
    assert not references({"memory_id": "mem_2"}, {"mem_1"})


def test_full_content_and_embedding_copies_are_detected():
    assert contains_copy({"prompt": "Context: private instruction"}, "private instruction", [1, 2])
    assert contains_copy({"nested": {"embedding": [1, 2]}}, "private instruction", [1, 2])
    assert not contains_copy({"prompt": "other information"}, "private instruction", [1, 2])


def test_metadata_uses_a_whitelist_not_a_list_of_known_secret_fields():
    assert metadata({"_id": "run_1", "unknown_content_copy": "secret"}, ["_id"]) == {"_id": "run_1"}
