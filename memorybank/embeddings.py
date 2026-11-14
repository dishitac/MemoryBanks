"""Voyage embeddings shared by document preparation and future search queries."""

import math
from hashlib import sha256

import voyageai


def content_hash(text):
    return sha256(text.encode("utf-8")).hexdigest()


def validate_vector(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError("Embedding length does not match EMBEDDING_DIM")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
        raise ValueError("Embedding must contain only finite numbers")
    if not any(value != 0 for value in vector):
        raise ValueError("A zero vector cannot be used with cosine similarity")


class Embeddings:
    def __init__(self, api_key, model, dimensions, *, client=None):
        if not api_key or not api_key.strip():
            raise ValueError("Set VOYAGE_API_KEY in your local .env")
        if not model or type(dimensions) is not int or dimensions <= 0:
            raise ValueError("Set a Voyage model and a positive EMBEDDING_DIM")
        self.model = model
        self.dimensions = dimensions
        self.client = (
            client
            if client is not None
            else voyageai.Client(api_key=api_key, timeout=20, max_retries=1)
        )

    def _embed(self, texts, input_type):
        if not texts or len(texts) > 32:
            raise ValueError("Embed between 1 and 32 texts per batch")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding text cannot be empty")
        response = self.client.embed(
            texts=texts,
            model=self.model,
            input_type=input_type,
            output_dimension=self.dimensions,
            output_dtype="float",
            truncation=False,
        )
        vectors = response.embeddings
        if len(vectors) != len(texts):
            raise ValueError("Voyage returned a different number of embeddings than requested")
        for vector in vectors:
            validate_vector(vector, self.dimensions)
        return vectors

    def documents(self, texts):
        return self._embed(texts, "document")

    def query(self, text):
        return self._embed([text], "query")[0]


def embed_demo_memories(db, embedder):
    """Developer-only batch preparation of known synthetic fixtures, not an agent tool.

    No lifecycle/permission changes. A guarded update refuses to attach vectors if content
    or lifecycle changed during the external API call. Superseded distractors are embedded
    deliberately so future retrieval tests can prove they are filtered out.
    """
    from memorybank.seed import search_demo_memories

    pending = []
    skipped = []
    for fixture in search_demo_memories():
        selector = {key: fixture[key] for key in ("_id", "tenant_id", "team_id", "policy_key")}
        memory = db.memories.find_one(selector)
        if not memory or memory["status"] not in ("active", "superseded"):
            skipped.append(fixture["_id"])
            continue
        metadata = {
            "model": embedder.model,
            "dimensions": embedder.dimensions,
            "content_hash": content_hash(memory["content"]),
        }
        if memory.get("embedding_metadata") == metadata and "embedding" in memory:
            validate_vector(memory["embedding"], embedder.dimensions)
            skipped.append(memory["_id"])
            continue
        pending.append((memory, metadata))

    if not pending:
        return {"updated": [], "skipped": skipped, "changed_during_embedding": []}
    vectors = embedder.documents([memory["content"] for memory, _ in pending])
    updated, changed = [], []
    for (memory, metadata), vector in zip(pending, vectors, strict=True):
        guard = {
            key: memory[key]
            for key in ("_id", "tenant_id", "team_id", "policy_key", "version", "status", "content")
        }
        result = db.memories.update_one(
            guard, {"$set": {"embedding": vector, "embedding_metadata": metadata}}
        )
        (updated if result.matched_count else changed).append(memory["_id"])
    return {"updated": updated, "skipped": skipped, "changed_during_embedding": changed}
