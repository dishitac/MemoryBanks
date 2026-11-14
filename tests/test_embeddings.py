"""Local boundary checks; fake responses test failures, never stand in for live search."""

from types import SimpleNamespace

import pytest

from memorybank.embeddings import Embeddings


class FakeVoyage:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(embeddings=self.vectors)


def test_document_and_query_use_the_correct_voyage_modes():
    client = FakeVoyage([[0.5, 0.5]])
    embedder = Embeddings("test-only", "test-model", 2, client=client)
    assert embedder.documents(["Policy text"]) == [[0.5, 0.5]]
    assert embedder.query("Do I need approval?") == [0.5, 0.5]
    assert [call["input_type"] for call in client.calls] == ["document", "query"]
    assert all(call["truncation"] is False for call in client.calls)


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1.0]],
        [[float("nan"), 1.0]],
        [[float("inf"), 1.0]],
        [[True, 1.0]],
        [[0.0, 0.0]],
    ],
)
def test_invalid_provider_responses_are_rejected(vectors):
    embedder = Embeddings("test-only", "test-model", 2, client=FakeVoyage(vectors))
    with pytest.raises(ValueError):
        embedder.query("Policy question")


def test_blank_text_is_rejected_before_sending_to_voyage():
    client = FakeVoyage([[1.0, 0.0]])
    embedder = Embeddings("test-only", "test-model", 2, client=client)
    with pytest.raises(ValueError):
        embedder.documents([" "])
    assert client.calls == []


def test_missing_key_has_a_clear_error():
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        Embeddings("", "test-model", 2)
