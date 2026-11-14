"""Embed existing synthetic seed policies without changing their content or validity."""

from memorybank.config import get_settings
from memorybank.db import get_db
from memorybank.embeddings import Embeddings, embed_demo_memories


def main():
    settings = get_settings()
    embedder = Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    result = embed_demo_memories(get_db(), embedder)
    print(f"Model: {embedder.model}; dimensions: {embedder.dimensions}")
    for category, ids in result.items():
        print(f"{category}: {len(ids)}")
        for memory_id in ids:
            print(f"  {memory_id}")
    if result["changed_during_embedding"]:
        raise RuntimeError("Records changed during embedding; no vectors attached to those records")


if __name__ == "__main__":
    main()
