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


def search_demo_memories(now=None):
    """Five synthetic candidates, including a private and an unusable distractor."""
    now = now or datetime.now(UTC)
    current = initial_memory(now)
    examples = [
        (
            "mem_bluebird_v1",
            "bluebird",
            "procurement",
            "supplier:bluebird:purchasing",
            "active",
            2000,
            "Bluebird purchases above USD 2,000 require finance approval.",
        ),
        (
            "mem_cedar_office_v1",
            "cedar",
            "procurement",
            "supplier:cedar:purchasing",
            "active",
            300,
            "Office-supply purchases from Cedar above USD 300 require finance approval.",
        ),
        (
            "mem_marketing_v1",
            "brightads",
            "marketing",
            "supplier:brightads:purchasing",
            "active",
            1000,
            "Marketing purchases from BrightAds above USD 1,000 require finance approval.",
        ),
        (
            "mem_northstar_retired_v1",
            "northstar",
            "procurement",
            "supplier:northstar:retired-purchasing",
            "superseded",
            20000,
            "Retired instruction: Northstar purchases above USD 20,000 require finance approval."
            " This instruction has been superseded and must not authorize purchases.",
        ),
    ]
    memories = [current]
    for memory_id, supplier, team, policy_key, status, limit, content in examples:
        memory = initial_memory(now)
        memory.update(
            _id=memory_id,
            team_id=team,
            policy_key=policy_key,
            status=status,
            content=content,
            terms={"supplier_id": supplier, "currency": "USD", "max_without_approval": limit},
        )
        memory["provenance"]["source_ref"] = f"demo_fixture:{memory_id}"
        memories.append(memory)
    return memories


def seed_database(db):
    for principal in demo_principals():
        db.principals.update_one(
            {"_id": principal["_id"]}, {"$setOnInsert": principal}, upsert=True
        )

    def insert_policy(session):
        created = False
        for memory in search_demo_memories():
            scope = {key: memory[key] for key in ("tenant_id", "team_id", "policy_key")}
            # Any history prevents resurrection, including revoked/deleted records.
            if db.memories.find_one(scope, session=session):
                continue
            db.memories.insert_one(memory, session=session)
            event_id = (
                "event_seed_northstar_v1"
                if memory["_id"] == "mem_northstar_v1"
                else f"event_seed:{memory['_id']}"
            )
            db.memory_events.insert_one(
                {
                    "_id": event_id,
                    **{key: memory[key] for key in ("tenant_id", "team_id")},
                    "event_type": "seeded",
                    "user_id": "finance_admin",
                    "agent_id": "agent_b",
                    "memory_id": memory["_id"],
                    "created_at": memory["created_at"],
                    "reason": "Synthetic approved demo fixture; not a live agent action",
                },
                session=session,
            )
            created = True
        return created

    with db.client.start_session() as session:
        return session.with_transaction(insert_policy)
