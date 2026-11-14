"""Developer CLI for exercising server-bound identity and real semantic retrieval."""

import argparse
import json

from memorybank.config import get_settings
from memorybank.db import get_db
from memorybank.embeddings import Embeddings
from memorybank.memory_gateway import MemoryGateway, ServerIdentity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--supplier", default=None)
    args = parser.parse_args()
    settings = get_settings()
    gateway = MemoryGateway(
        get_db(), Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    )
    # Fixed developer-demo session, not identity supplied by the query or a model.
    identity = ServerIdentity("employee", "agent_a", "demo_company", "procurement")
    memories = gateway.retrieve_memories(identity, args.query, args.supplier)
    print(json.dumps(memories, indent=2))
    if not memories:
        print("No usable memory found. Ask for clarification; do not infer permission.")


if __name__ == "__main__":
    main()
