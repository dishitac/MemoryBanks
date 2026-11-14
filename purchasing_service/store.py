"""SQLite persistence for approval requests and draft orders.

The purchasing process owns this file. It does not read Atlas or application settings.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

DEFAULT_DB_PATH = "purchasing_service/data/purchasing.sqlite"

KIND_PREFIX = {"approval_request": "apr_", "draft_order": "ord_"}

PAYLOAD_FIELDS = (
    "supplier_id",
    "currency",
    "amount",
    "memory_id",
    "memory_version",
    "requested_by_user_id",
    "submitted_by_agent_id",
    "tenant_id",
    "team_id",
)

COLUMNS = (
    "id",
    "kind",
    "supplier_id",
    "currency",
    "amount",
    "memory_id",
    "memory_version",
    "idempotency_key",
    "requested_by_user_id",
    "submitted_by_agent_id",
    "tenant_id",
    "team_id",
    "created_at",
)


class Conflict(Exception):
    """The idempotency key already belongs to a different record."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database file, creating the parent directory and table if needed."""
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('approval_request', 'draft_order')),
            supplier_id TEXT NOT NULL,
            currency TEXT NOT NULL CHECK (currency = 'USD'),
            amount INTEGER NOT NULL CHECK (amount > 0),
            memory_id TEXT NOT NULL,
            memory_version INTEGER NOT NULL CHECK (memory_version >= 1),
            idempotency_key TEXT NOT NULL UNIQUE,
            requested_by_user_id TEXT NOT NULL,
            submitted_by_agent_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def create_record(connection: sqlite3.Connection, kind: str, payload: dict) -> dict:
    """Insert a record, or return the existing one when the key and payload match."""
    if kind not in KIND_PREFIX:
        raise ValueError(f"Unknown record kind: {kind}")
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = _find_by_key(connection, payload["idempotency_key"])
        if existing is not None:
            if not _same_request(existing, kind, payload):
                raise Conflict(payload["idempotency_key"])
            record = existing
        else:
            record = _insert(connection, kind, payload)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return record


def get_record(connection: sqlite3.Connection, record_id: str, kind: str) -> dict | None:
    """Return one record of this kind, or None when the id is missing or a different kind."""
    row = connection.execute(
        "SELECT * FROM records WHERE id = ? AND kind = ?",
        (record_id, kind),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _find_by_key(connection: sqlite3.Connection, idempotency_key: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM records WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _same_request(existing: dict, kind: str, payload: dict) -> bool:
    if existing["kind"] != kind:
        return False
    return all(existing[field] == payload[field] for field in PAYLOAD_FIELDS)


def _insert(connection: sqlite3.Connection, kind: str, payload: dict) -> dict:
    record = {
        "id": KIND_PREFIX[kind] + uuid4().hex,
        "kind": kind,
        "idempotency_key": payload["idempotency_key"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    for field in PAYLOAD_FIELDS:
        record[field] = payload[field]
    connection.execute(
        f"INSERT INTO records ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
        tuple(record[column] for column in COLUMNS),
    )
    return record
