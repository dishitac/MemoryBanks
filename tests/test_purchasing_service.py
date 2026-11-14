"""Purchasing-service checks. These use a temporary SQLite file and do not need Atlas."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from purchasing_service.app import app

ROOT = Path(__file__).resolve().parents[1]

NORTHSTAR_APPROVAL = {
    "supplier_id": "northstar",
    "currency": "USD",
    "amount": 7000,
    "memory_id": "mem_northstar_v1",
    "memory_version": 1,
    "idempotency_key": "northstar-7000",
    "requested_by_user_id": "employee",
    "submitted_by_agent_id": "agent_a",
    "tenant_id": "demo_company",
    "team_id": "procurement",
}


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "purchasing.sqlite"
    monkeypatch.setenv("PURCHASING_DB_PATH", str(path))
    return path


def test_usd_7000_approval_request_is_stored_and_read_back(database_path):
    with TestClient(app) as client:
        created = client.post("/approval-requests", json=NORTHSTAR_APPROVAL)
        assert created.status_code == 200
        record = created.json()
        assert record["id"].startswith("apr_")
        assert record["kind"] == "approval_request"
        assert record["amount"] == 7000
        assert record["memory_id"] == "mem_northstar_v1"
        assert record["memory_version"] == 1
        assert record["requested_by_user_id"] == "employee"
        assert record["submitted_by_agent_id"] == "agent_a"

        read = client.get(f"/approval-requests/{record['id']}")
        assert read.status_code == 200
        assert read.json() == record

        replay = client.post("/approval-requests", json=NORTHSTAR_APPROVAL)
        assert replay.status_code == 200
        assert replay.json() == record

    with TestClient(app) as reopened:
        persisted = reopened.get(f"/approval-requests/{record['id']}")
    assert persisted.status_code == 200
    assert persisted.json() == record
    assert _read_in_new_process(database_path, record["id"]) == record["amount"]


def test_reused_key_with_a_different_amount_is_rejected(database_path):
    changed = dict(NORTHSTAR_APPROVAL, amount=10000)
    with TestClient(app) as client:
        created = client.post("/approval-requests", json=NORTHSTAR_APPROVAL)
        conflict = client.post("/approval-requests", json=changed)
        assert conflict.status_code == 409
        kept = client.get(f"/approval-requests/{created.json()['id']}")
    assert kept.json()["amount"] == 7000


def test_reused_key_cannot_change_an_approval_into_an_order(database_path):
    with TestClient(app) as client:
        created = client.post("/approval-requests", json=NORTHSTAR_APPROVAL)
        conflict = client.post("/draft-orders", json=NORTHSTAR_APPROVAL)
        assert conflict.status_code == 409
        assert client.get(f"/draft-orders/{created.json()['id']}").status_code == 404
        assert client.get(f"/approval-requests/{created.json()['id']}").status_code == 200


def test_draft_order_round_trip(database_path):
    order = dict(NORTHSTAR_APPROVAL, amount=4000, idempotency_key="northstar-4000")
    with TestClient(app) as client:
        created = client.post("/draft-orders", json=order)
        assert created.status_code == 200
        record = created.json()
        assert record["id"].startswith("ord_")
        assert record["kind"] == "draft_order"
        assert client.get(f"/draft-orders/{record['id']}").json() == record
        assert client.get(f"/approval-requests/{record['id']}").status_code == 404


def test_missing_record_is_not_found(database_path):
    with TestClient(app) as client:
        assert client.get("/approval-requests/apr_missing").status_code == 404


def test_service_imports_without_mongodb_settings(database_path):
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT),
        "PURCHASING_DB_PATH": str(database_path),
    }
    completed = subprocess.run(
        [sys.executable, "-c", "import purchasing_service.app"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


def _read_in_new_process(database_path: Path, record_id: str) -> int:
    code = (
        "import os\n"
        "from purchasing_service.store import connect, get_record\n"
        "connection = connect(os.environ['PURCHASING_DB_PATH'])\n"
        "record = get_record(connection, os.environ['RECORD_ID'], 'approval_request')\n"
        "print(record['amount'])\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT),
        "PURCHASING_DB_PATH": str(database_path),
        "RECORD_ID": record_id,
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return int(completed.stdout.strip())
