"""Local checks: missing, building, and incompatible indexes fail explicitly."""

import pytest

from memorybank.search import index_definition, require_search_ready


class IndexCollection:
    def __init__(self, indexes):
        self.indexes = indexes

    def list_search_indexes(self, **kwargs):
        return self.indexes


def test_missing_index_is_an_error_not_an_empty_search():
    with pytest.raises(RuntimeError, match="missing"):
        require_search_ready(IndexCollection([]), 1024)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"status": "BUILDING", "queryable": False}, "not ready"),
        ({"latestDefinition": index_definition(2)}, "configuration differs"),
        ({"type": "search"}, "vectorSearch"),
    ],
)
def test_unusable_index_is_rejected(change, message):
    index = {
        "type": "vectorSearch",
        "latestDefinition": index_definition(1024),
        "status": "READY",
        "queryable": True,
        **change,
    }
    with pytest.raises(RuntimeError, match=message):
        require_search_ready(IndexCollection([index]), 1024)
