"""Exercise the action gate with real Voyage/Atlas and the purchasing SQLite store.

This is an in-process developer adapter, not the complete Gemini/HTTP application.
All data is synthetic and temporary. No real order is placed.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from memorybank.config import get_settings
from memorybank.db import get_client
from memorybank.embeddings import Embeddings, embed_demo_memories
from memorybank.memory_gateway import MemoryGateway, ServerIdentity
from memorybank.schema import setup_database
from memorybank.seed import POLICY_KEY, seed_database
from purchasing_service.store import connect, create_record, get_record


def dispatch_and_verify(gateway, identity, request, connection):
    with gateway.validated_action(identity, **request) as result:
        if not result["allowed"]:
            print("Blocked:", result["reason"])
            return None
        payload = dict(result["action_to_dispatch"])
        action = payload.pop("action")
        kind = {
            "request_finance_approval": "approval_request",
            "create_draft_order": "draft_order",
        }[action]
        payload["idempotency_key"] = f"demo_{uuid4().hex}"
        receipt = create_record(connection, kind, payload)
        verified = get_record(connection, receipt["id"], kind)
        assert verified is not None
        for key in payload:
            assert verified[key] == payload[key]
        print(f"Verified {kind}: USD {verified['amount']:,}, memory v{verified['memory_version']}")
        return verified


def main():
    settings = get_settings()
    client = get_client()
    name = f"memorybank_action_demo_{uuid4().hex}"
    db = client[name]
    embedder = Embeddings(settings.voyage_api_key, settings.voyage_model, settings.embedding_dim)
    buyer = ServerIdentity("employee", "agent_a", "demo_company", "procurement")
    admin = ServerIdentity("finance_admin", "agent_b", "demo_company", "procurement")
    try:
        setup_database(db)
        seed_database(db)
        embed_demo_memories(db, embedder)
        gateway = MemoryGateway(db, embedder)
        with TemporaryDirectory(prefix="memorybank-action-demo-") as folder:
            connection = connect(Path(folder) / "purchasing.sqlite")
            try:
                request = {
                    "action": "create_draft_order",
                    "supplier_id": "northstar",
                    "currency": "USD",
                    "amount": 7000,
                    "memory_id": "mem_northstar_v1",
                    "memory_version": 1,
                }
                print("1. Try a USD 7,000 draft order under the USD 5,000 policy:")
                assert dispatch_and_verify(gateway, buyer, request, connection) is None
                assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
                print("2. Submit the policy-required finance-approval request:")
                approval = dispatch_and_verify(
                    gateway, buyer, {**request, "action": "request_finance_approval"}, connection
                )
                assert approval["kind"] == "approval_request"
                proposal = gateway.propose_change(
                    admin,
                    POLICY_KEY,
                    1,
                    "Raise Northstar's purchasing limit to USD 10,000.",
                    {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 10000},
                    "synthetic_finance_update",
                    "finance_admin",
                )
                approved = gateway.approve_proposal(admin, proposal["proposal_id"], 1)
                print("3. Administrator approved v2. Try reusing v1:")
                assert dispatch_and_verify(gateway, buyer, request, connection) is None
                print("4. Repeat the same USD 7,000 purchase using v2:")
                order = dispatch_and_verify(
                    gateway,
                    buyer,
                    {**request, "memory_id": approved["memory_id"], "memory_version": 2},
                    connection,
                )
                assert order["kind"] == "draft_order"
                assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
                print("Only the approval request and permitted draft order were stored.")
            finally:
                connection.close()
    finally:
        assert name.startswith("memorybank_action_demo_")
        client.drop_database(name)
        print("Temporary Atlas/SQLite data removed. Main policies and purchases unchanged.")


if __name__ == "__main__":
    main()
