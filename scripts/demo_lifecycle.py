"""Demonstrate lifecycle governance with real embeddings and disposable Atlas data."""

from datetime import UTC, datetime
from uuid import uuid4

from memorybank.config import get_settings
from memorybank.db import get_client
from memorybank.embeddings import Embeddings, embed_demo_memories
from memorybank.memory_gateway import MemoryGateway, ServerIdentity
from memorybank.schema import setup_database
from memorybank.seed import seed_database


def main():
    settings = get_settings()
    client = get_client()
    name = f"memorybank_lifecycle_demo_{uuid4().hex}"
    db = client[name]
    admin = ServerIdentity("finance_admin", "agent_b", "demo_company", "procurement")
    employee = ServerIdentity("employee", "agent_b", "demo_company", "procurement")
    buyer = ServerIdentity("employee", "agent_a", "demo_company", "procurement")
    embedder = Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    try:
        setup_database(db)
        seed_database(db)
        embed_demo_memories(db, embedder)
        gateway = MemoryGateway(db, embedder)
        request = {
            "action": "create_draft_order",
            "supplier_id": "northstar",
            "currency": "USD",
            "amount": 1000,
            "memory_id": "mem_northstar_v1",
            "memory_version": 1,
        }
        assert gateway.validate_action(buyer, **request)["allowed"] is True
        print("1. Before expiry: Northstar's policy authorizes a USD 1,000 draft order.")
        gateway.expire_memory(admin, "mem_northstar_v1", 1)
        assert gateway.get_memory(buyer, "mem_northstar_v1") is None
        assert (
            gateway.validate_action(buyer, **request)["required_action"] == "ask_for_clarification"
        )
        print(
            "2. Expired: history remains, but the next action requires clarification."
        )
        gateway.revoke_memory(admin, "mem_bluebird_v1", 1)
        assert gateway.get_memory(buyer, "mem_bluebird_v1") is None
        assert "content" in db.memories.find_one({"_id": "mem_bluebird_v1"})
        print("3. Revoked: Bluebird's policy is unusable; history is retained.")
        cedar = db.memories.find_one({"_id": "mem_cedar_office_v1"})
        proposal = gateway.propose_change(
            employee,
            cedar["policy_key"],
            1,
            cedar["content"],
            {**cedar["terms"], "max_without_approval": 1000},
            "synthetic_note",
            "employee_message",
        )
        run_id = f"run_{uuid4().hex}"
        db.runs.insert_one(
            {
                "_id": run_id,
                "tenant_id": buyer.tenant_id,
                "team_id": buyer.team_id,
                "user_id": buyer.user_id,
                "agent_id": buyer.agent_id,
                "created_at": datetime.now(UTC),
                "status": "started",
                "retrieved_memories": [
                    {
                        "memory_id": cedar["_id"],
                        "version": 1,
                        "content": cedar["content"],
                        "embedding": cedar["embedding"],
                    }
                ],
            }
        )
        result = gateway.forget_memory(admin, cedar["_id"], 1)
        tombstone = db.memories.find_one({"_id": cedar["_id"]})
        assert "content" not in tombstone and "embedding" not in tombstone
        assert gateway.get_proposal(admin, proposal["proposal_id"])["status"] == "deleted"
        assert "retrieved_memories" not in db.runs.find_one({"_id": run_id})
        assert gateway.get_memory(buyer, cedar["_id"]) is None
        print("4. Forgotten: content, embedding, linked proposal and run copies removed.")
        print("   Redacted linked records:", result["redacted"])
        denied = gateway.validate_action(
            buyer, **{**request, "supplier_id": "cedar", "amount": 100, "memory_id": cedar["_id"]}
        )
        assert denied["allowed"] is False
        print("5. Previously valid Cedar action now requires clarification.")
        assert gateway.forget_memory(admin, cedar["_id"], 1)["changed"] is False
        print("6. Repeating forgetting is safe. No policy was restored and no purchase executed.")
    finally:
        assert name.startswith("memorybank_lifecycle_demo_")
        client.drop_database(name)
        print("Temporary demo database removed. Main application data unchanged.")


if __name__ == "__main__":
    main()
