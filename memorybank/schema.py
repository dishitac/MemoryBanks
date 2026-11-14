"""Checkpoint 1: database shapes and constraints, not permission enforcement."""

from pymongo import ASCENDING, DESCENDING, IndexModel

TEXT = {"bsonType": "string", "minLength": 1}
DATE = {"bsonType": "date"}
SCOPE = {"tenant_id": TEXT, "team_id": TEXT}


def object_schema(required, properties):
    return {"bsonType": "object", "required": required, "properties": properties}


TERMS = object_schema(
    ["supplier_id", "currency", "max_without_approval"],
    {
        "supplier_id": TEXT,
        "currency": {"enum": ["USD"]},
        "max_without_approval": {"bsonType": ["int", "long"], "minimum": 0},
    },
)
PROVENANCE = object_schema(
    ["requested_by_user_id", "submitted_by_agent_id", "source_ref", "approved_by_user_id"],
    {
        key: TEXT
        for key in (
            "requested_by_user_id",
            "submitted_by_agent_id",
            "source_ref",
            "approved_by_user_id",
        )
    },
)


def validators():
    """Leave optional extension fields available for the partner's action/run code."""
    memory = object_schema(
        ["_id", "tenant_id", "team_id", "policy_key", "version", "status", "created_at"],
        {
            "_id": TEXT,
            **SCOPE,
            "policy_key": TEXT,
            "version": {"bsonType": ["int", "long"], "minimum": 1},
            "status": {"enum": ["active", "superseded", "expired", "revoked", "deleted"]},
            "kind": {"enum": ["supplier_purchase_policy"]},
            "content": TEXT,
            "terms": TERMS,
            "provenance": PROVENANCE,
            "created_at": DATE,
            "valid_from": DATE,
            "valid_until": {"bsonType": ["date", "null"]},
            "embedding": {
                "bsonType": "array",
                "minItems": 1,
                "items": {"bsonType": ["double", "int", "long"]},
            },
        },
    )
    # Tombstones cannot retain policy content; live/history records need complete policies.
    memory["anyOf"] = [
        {
            "properties": {"status": {"enum": ["deleted"]}},
            "not": {
                "anyOf": [
                    {"required": [field]}
                    for field in ("content", "embedding", "terms", "provenance")
                ]
            },
        },
        {
            "required": ["kind", "content", "terms", "provenance", "valid_from", "valid_until"],
            "properties": {"status": {"enum": ["active", "superseded", "expired", "revoked"]}},
        },
    ]
    return {
        "principals": {
            "$jsonSchema": object_schema(
                ["_id", "kind", "role", "tenant_id", "team_id", "enabled"],
                {
                    "_id": TEXT,
                    **SCOPE,
                    "kind": {"enum": ["user", "agent"]},
                    "role": {
                        "enum": [
                            "employee",
                            "finance_admin",
                            "purchasing_agent",
                            "onboarding_agent",
                        ]
                    },
                    "enabled": {"bsonType": "bool"},
                },
            )
        },
        "memories": {"$jsonSchema": memory},
        "memory_proposals": {
            "$jsonSchema": object_schema(
                [
                    "_id",
                    "tenant_id",
                    "team_id",
                    "policy_key",
                    "expected_version",
                    "status",
                    "requested_by_user_id",
                    "submitted_by_agent_id",
                    "created_at",
                ],
                {
                    "_id": TEXT,
                    **SCOPE,
                    "policy_key": TEXT,
                    "expected_version": {"bsonType": ["int", "long"], "minimum": 1},
                    "status": {"enum": ["pending_review", "approved", "rejected", "deleted"]},
                    "requested_by_user_id": TEXT,
                    "submitted_by_agent_id": TEXT,
                    "created_at": DATE,
                    "content": TEXT,
                    "terms": TERMS,
                    "source_ref": TEXT,
                },
            )
        },
        "memory_events": {
            "$jsonSchema": object_schema(
                ["_id", "tenant_id", "team_id", "event_type", "user_id", "agent_id", "created_at"],
                {
                    "_id": TEXT,
                    **SCOPE,
                    "event_type": TEXT,
                    "user_id": TEXT,
                    "agent_id": TEXT,
                    "created_at": DATE,
                    "memory_id": TEXT,
                    "reason": TEXT,
                },
            )
        },
        "runs": {
            "$jsonSchema": object_schema(
                ["_id", "tenant_id", "team_id", "user_id", "agent_id", "status", "created_at"],
                {
                    "_id": TEXT,
                    **SCOPE,
                    "user_id": TEXT,
                    "agent_id": TEXT,
                    "created_at": DATE,
                    "status": {
                        "enum": [
                            "started",
                            "blocked",
                            "clarification",
                            "verified",
                            "failed",
                            "uncertain",
                        ]
                    },
                },
            )
        },
    }


def setup_database(db):
    """Repeat safely. Refuse to replace existing validators or adopt nonempty raw data."""
    schemas = validators()
    existing = {c["name"]: c for c in db.list_collections()}
    # Preflight before changing anything; old/partner data needs an explicit migration.
    for name, validator in schemas.items():
        if name not in existing:
            continue
        current = existing[name].get("options", {}).get("validator")
        if current and current != validator:
            raise RuntimeError(f"{name}: different validator; explicit migration required")
        if not current and db[name].find_one({}, {"_id": 1}):
            raise RuntimeError(f"{name}: contains existing data; explicit migration required")
    for name, validator in schemas.items():
        options = dict(validator=validator, validationLevel="strict", validationAction="error")
        if name not in existing:
            db.create_collection(name, **options)
        else:
            db.command("collMod", name, **options)
    scope = [("tenant_id", ASCENDING), ("team_id", ASCENDING), ("policy_key", ASCENDING)]
    db.memories.create_indexes(
        [
            IndexModel([*scope, ("version", ASCENDING)], unique=True, name="unique_policy_version"),
            IndexModel(
                scope,
                unique=True,
                partialFilterExpression={"status": "active"},
                name="one_active_policy",
            ),
        ]
    )
    db.memory_proposals.create_index(
        [("tenant_id", ASCENDING), ("team_id", ASCENDING), ("status", ASCENDING)],
        name="review_queue",
    )
    for name in ("memory_events", "runs"):
        db[name].create_index(
            [("tenant_id", ASCENDING), ("team_id", ASCENDING), ("created_at", DESCENDING)],
            name="scope_history",
        )
    return list(schemas)
