"""Atlas Vector Search index management; never silently replace an existing index."""

from pymongo.operations import SearchIndexModel

INDEX_NAME = "memory_vectors"
FILTER_FIELDS = (
    "tenant_id",
    "team_id",
    "status",
    "kind",
    "terms.supplier_id",
    "valid_from",
    "embedding_metadata.model",
    "embedding_metadata.dimensions",
)


def index_definition(dimensions):
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dimensions,
                "similarity": "cosine",
            },
            *[{"type": "filter", "path": field} for field in FILTER_FIELDS],
        ]
    }


def check_definition(index, dimensions):
    definition = index.get("latestDefinition", {})
    fields = {field["path"]: field for field in definition.get("fields", [])}
    for expected in index_definition(dimensions)["fields"]:
        actual = fields.get(expected["path"], {})
        if any(actual.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Search index configuration differs; explicit migration required")
    if index.get("type") != "vectorSearch":
        raise RuntimeError("Expected a vectorSearch index")


def search_index_status(collection, dimensions):
    indexes = list(collection.list_search_indexes(name=INDEX_NAME))
    if not indexes:
        raise RuntimeError("Search index missing. Run python -m scripts.setup_indexes")
    index = indexes[0]
    check_definition(index, dimensions)
    return index


def require_search_ready(collection, dimensions):
    index = search_index_status(collection, dimensions)
    if not index.get("queryable") or index.get("status") != "READY":
        raise RuntimeError(f"Search index is not ready (status: {index.get('status')})")


def setup_search_index(collection, dimensions):
    indexes = list(collection.list_search_indexes(name=INDEX_NAME))
    if indexes:
        check_definition(indexes[0], dimensions)
        return False
    collection.create_search_index(
        SearchIndexModel(
            name=INDEX_NAME, type="vectorSearch", definition=index_definition(dimensions)
        )
    )
    return True
