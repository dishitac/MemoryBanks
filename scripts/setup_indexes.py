"""Create/inspect the Vector Search index. Re-run to inspect asynchronous build status."""

from memorybank.config import get_settings
from memorybank.db import get_db
from memorybank.search import search_index_status, setup_search_index


def main():
    settings = get_settings()
    memories = get_db().memories
    created = setup_search_index(memories, settings.embedding_dim)
    print("Index creation requested." if created else "Existing index configuration matches.")
    status = search_index_status(memories, settings.embedding_dim)
    print(f"Status: {status.get('status')}; queryable: {status.get('queryable', False)}")
    if not status.get("queryable"):
        print("Atlas builds asynchronously. Re-run this command to check readiness.")


if __name__ == "__main__":
    main()
