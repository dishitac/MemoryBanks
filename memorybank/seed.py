"""Synthetic demo fixtures. Rerunning never resets policy versions or permissions."""

from datetime import UTC, datetime, timedelta

SCOPE = {"tenant_id": "demo_company", "team_id": "procurement"}
POLICY_KEY = "supplier:northstar:purchasing"


def demo_principals():
    identities = [
        ("employee", "user", "employee", "procurement"),
        ("finance_admin", "user", "finance_admin", "procurement"),
        ("other_team_user", "user", "employee", "marketing"),
        ("agent_a", "agent", "purchasing_agent", "procurement"),
        ("agent_b", "agent", "onboarding_agent", "procurement"),
    ]
    return [
        {
            "_id": name,
            "kind": kind,
            "role": role,
            "tenant_id": "demo_company",
            "team_id": team,
            "enabled": True,
        }
        for name, kind, role, team in identities
    ]


def initial_memory(now=None):
    now = now or datetime.now(UTC)
    return {
        "_id": "mem_northstar_v1",
        **SCOPE,
        "policy_key": POLICY_KEY,
        "version": 1,
        "status": "active",
        "kind": "supplier_purchase_policy",
        "content": "Northstar purchases above USD 5,000 require finance approval.",
        "terms": {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 5000},
        "provenance": {
            "requested_by_user_id": "finance_admin",
            "submitted_by_agent_id": "agent_b",
            "approved_by_user_id": "finance_admin",
            "source_ref": "demo_finance_policy_001",
        },
        "created_at": now,
        "valid_from": now,
        "valid_until": now + timedelta(days=1),
        "supersedes": None,
        "superseded_by": None,
    }


def seed_database(db):
    for principal in demo_principals():
        db.principals.update_one(
            {"_id": principal["_id"]}, {"$setOnInsert": principal}, upsert=True
        )

    def insert_policy(session):
        # Even revoked/deleted history prevents resurrection by re-running the seed.
        if db.memories.find_one({**SCOPE, "policy_key": POLICY_KEY}, session=session):
            return False
        memory = initial_memory()
        db.memories.insert_one(memory, session=session)
        db.memory_events.insert_one(
            {
                "_id": "event_seed_northstar_v1",
                **SCOPE,
                "event_type": "seeded",
                "user_id": "finance_admin",
                "agent_id": "agent_b",
                "memory_id": memory["_id"],
                "created_at": memory["created_at"],
                "reason": "Synthetic approved demo fixture",
            },
            session=session,
        )
        return True

    with db.client.start_session() as session:
        return session.with_transaction(insert_policy)
