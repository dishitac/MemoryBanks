"""HTTP API for the mock purchasing service.

The process reads PURCHASING_DB_PATH and does not load MongoDB settings. Agents do not
call these routes. The application server puts the server-bound identity in the body.
"""

import os
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from purchasing_service.store import DEFAULT_DB_PATH, Conflict, connect, create_record, get_record

app = FastAPI(title="Mock purchasing service")


class CreateRecord(BaseModel):
    supplier_id: str = Field(min_length=1)
    currency: Literal["USD"]
    amount: int = Field(gt=0)
    memory_id: str = Field(min_length=1)
    memory_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)
    submitted_by_agent_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)


class Record(CreateRecord):
    id: str
    kind: Literal["approval_request", "draft_order"]
    created_at: str


def get_connection() -> Iterator[sqlite3.Connection]:
    connection = connect(os.environ.get("PURCHASING_DB_PATH", DEFAULT_DB_PATH))
    try:
        yield connection
    finally:
        connection.close()


Connection = Annotated[sqlite3.Connection, Depends(get_connection)]


def _create(kind: str, body: CreateRecord, connection: sqlite3.Connection) -> Record:
    try:
        record = create_record(connection, kind, body.model_dump())
    except Conflict:
        raise HTTPException(
            status_code=409,
            detail="Idempotency key was reused with a different request",
        ) from None
    return Record.model_validate(record)


@app.post("/approval-requests", response_model=Record)
def create_approval_request(body: CreateRecord, connection: Connection) -> Record:
    return _create("approval_request", body, connection)


@app.post("/draft-orders", response_model=Record)
def create_draft_order(body: CreateRecord, connection: Connection) -> Record:
    return _create("draft_order", body, connection)


@app.get("/approval-requests/{record_id}", response_model=Record)
def read_approval_request(record_id: str, connection: Connection) -> Record:
    return _read(record_id, "approval_request", connection)


@app.get("/draft-orders/{record_id}", response_model=Record)
def read_draft_order(record_id: str, connection: Connection) -> Record:
    return _read(record_id, "draft_order", connection)


def _read(record_id: str, kind: str, connection: sqlite3.Connection) -> Record:
    record = get_record(connection, record_id, kind)
    if record is None:
        raise HTTPException(status_code=404, detail="Purchasing record not found")
    return Record.model_validate(record)
