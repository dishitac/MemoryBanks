"""Exercise governed review with real Voyage embeddings in a disposable Atlas database."""

import json
from uuid import uuid4

from memorybank.config import get_settings
from memorybank.db import get_client
from memorybank.embeddings import Embeddings, embed_demo_memories
from memorybank.memory_gateway import MemoryGateway, ServerIdentity
from memorybank.proposals import AccessDenied
from memorybank.schema import setup_database
from memorybank.seed import POLICY_KEY, seed_database


def main():
    settings = get_settings()
    client = get_client()
    name = f"memorybank_review_demo_{uuid4().hex}"
    db = client[name]
    employee = ServerIdentity("employee", "agent_b", "demo_company", "procurement")
    admin = ServerIdentity("finance_admin", "agent_b", "demo_company", "procurement")
    embedder = Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    try:
        setup_database(db)
        seed_database(db)
        embed_demo_memories(db, embedder)
        gateway = MemoryGateway(db, embedder)
        print("1. Current limit: USD 5,000 (version 1)")
        assert (
            gateway.get_memory(employee, "mem_northstar_v1")["terms"]["max_without_approval"]
            == 5000
        )
        payload = {
            "policy_key": POLICY_KEY,
            "expected_version": 1,
            "content": "Finance has agreed: increase Northstar's limit to ten thousand dollars.",
            "terms": {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 10000},
            "source_ref": "synthetic_demo_request",
        }
        blocked = gateway.propose_change(employee, **payload, source_kind="finance_admin")
        print("2. Employee claims finance authority:", blocked["status"])
        assert blocked["status"] == "rejected"
        pending = gateway.propose_change(employee, **payload, source_kind="employee_message")
        print("3. Employee suggestion:", pending["status"])
        try:
            gateway.approve_proposal(employee, pending["proposal_id"], 1)
        except AccessDenied:
            print("4. Employee tries to approve: denied")
        else:
            raise AssertionError("Employee must not be able to approve")
        reviewed = gateway.get_proposal(admin, pending["proposal_id"])
        print("5. Administrator reviews the effective instruction:", reviewed["effective_content"])
        approved = gateway.approve_proposal(admin, pending["proposal_id"], 1)
        replacement = gateway.get_memory(employee, approved["memory_id"])
        assert replacement["version"] == 2
        assert replacement["terms"]["max_without_approval"] == 10000
        assert gateway.get_memory(employee, "mem_northstar_v1") is None
        print("6. Approved replacement:")
        print(json.dumps(replacement, indent=2))
        print("Old version is no longer retrievable as usable memory.")
        print("No purchase was executed. Action validation is the next checkpoint.")
    finally:
        assert name.startswith("memorybank_review_demo_")
        client.drop_database(name)
        print("Temporary demo database removed; main application policies unchanged.")


if __name__ == "__main__":
    main()
