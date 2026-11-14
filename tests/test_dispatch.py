"""Guarded purchasing dispatch. These tests use fakes and do not call the network."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from memorybank.agents import InterpretationError
from memorybank.dispatch import execute_stored_purchase
from memorybank.memory_gateway import ServerIdentity

MODEL = "gemini-3.5-flash-lite"

MEMORY = {
    "memory_id": "mem_northstar_v1",
    "version": 1,
    "content": "Northstar purchases above USD 5,000 require finance approval.",
    "terms": {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 5000},
}

PURCHASE = {
    "action": "request_finance_approval",
    "supplier_id": "northstar",
    "currency": "USD",
    "amount": 7000,
    "memory_id": "mem_northstar_v1",
    "memory_version": 1,
}

CLARIFICATION = {
    "action": "ask_for_clarification",
    "supplier_id": None,
    "currency": None,
    "amount": None,
    "memory_id": None,
    "memory_version": None,
}

SETTINGS = SimpleNamespace(
    voyage_api_key="test-key",
    voyage_model="voyage-4",
    embedding_dim=1024,
    llm_provider="gemini",
    llm_api_key="unused",
    llm_model=MODEL,
    purchasing_service_url="http://purchasing.test",
)

PAYLOAD = {
    "action": "request_finance_approval",
    "supplier_id": "northstar",
    "currency": "USD",
    "amount": 7000,
    "memory_id": "mem_northstar_v1",
    "memory_version": 1,
    "requested_by_user_id": "employee",
    "submitted_by_agent_id": "agent_a",
    "tenant_id": "demo_company",
    "team_id": "procurement",
}

ALLOWED = {
    "allowed": True,
    "reason": "Current policy and identity authorize this action",
    "required_action": "request_finance_approval",
    "validation_event_id": "event_allowed",
    "action_to_dispatch": PAYLOAD,
}

DENIED = {
    "allowed": False,
    "reason": "Current policy requires request_finance_approval",
    "required_action": "request_finance_approval",
    "validation_event_id": "event_denied",
    "action_to_dispatch": None,
}

CLARIFICATION_DECISION = {
    "allowed": False,
    "reason": "Clarification requested; no purchasing action authorized",
    "required_action": "ask_for_clarification",
    "validation_event_id": "event_clarification",
    "action_to_dispatch": None,
}


class PromptModels:
    def __init__(self, results):
        self.results = list(results)

    def generate_content(self, *, model, contents, config):
        return SimpleNamespace(parsed=self.results.pop(0))


class FakePurchasing:
    def __init__(self):
        self.posts = []
        self.reads = []

    def create(self, kind, payload):
        self.posts.append((kind, dict(payload)))
        record = dict(payload)
        record.update(id="apr_test", kind=kind, created_at="2026-01-01T00:00:00+00:00")
        return record

    def read(self, kind, record_id):
        self.reads.append((kind, record_id))
        sent = dict(self.posts[-1][1])
        sent.update(id=record_id, kind=kind, created_at="2026-01-01T00:00:00+00:00")
        return sent


def patch_execution(monkeypatch, memories, decision):
    calls = {"retrieve": [], "validate": [], "runs": []}

    class Runs:
        def insert_one(self, document):
            calls["runs"].append(document)

    runs = Runs()

    class RecordingGateway:
        def __init__(self, db, embedder):
            self.db = SimpleNamespace(runs=runs)

        def retrieve_memories(self, identity, query_text, supplier_id=None, *, limit=5):
            calls["retrieve"].append((identity, query_text, supplier_id, limit))
            return memories

        @contextmanager
        def validated_action(self, identity, **fields):
            calls["validate"].append((identity, fields))
            yield decision

    monkeypatch.setattr("memorybank.agents.get_db", lambda: object())
    monkeypatch.setattr("memorybank.agents.Embeddings", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("memorybank.agents.MemoryGateway", RecordingGateway)
    return calls


def execute(message, parsed, purchasing, supplier_id=None):
    identity = ServerIdentity("employee", "agent_a", "demo_company", "procurement")
    result = execute_stored_purchase(
        message,
        identity,
        supplier_id,
        client=SimpleNamespace(models=PromptModels([parsed])),
        model=MODEL,
        settings=SETTINGS,
        purchasing=purchasing,
    )
    return identity, result


def test_allowed_purchase_is_posted_and_verified(monkeypatch):
    calls = patch_execution(monkeypatch, [MEMORY], ALLOWED)
    purchasing = FakePurchasing()
    _identity, result = execute("Buy USD 7000 from Northstar.", PURCHASE, purchasing, "northstar")
    assert result.allowed is True
    assert result.status == "verified"
    assert result.purchasing_record_id == "apr_test"
    assert result.proposed.action.value == "request_finance_approval"
    assert result.proposed.amount == 7000
    kind, body = purchasing.posts[0]
    assert kind == "approval_request"
    assert body["amount"] == 7000
    assert body["memory_id"] == "mem_northstar_v1"
    assert body["memory_version"] == 1
    assert body["team_id"] == "procurement"
    assert body["idempotency_key"].startswith("purchase_")
    assert purchasing.reads == [("approval_request", "apr_test")]
    run = calls["runs"][0]
    assert run["status"] == "verified"
    assert run["user_id"] == "employee"
    assert run["agent_id"] == "agent_a"
    assert run["team_id"] == "procurement"
    assert run["memory_id"] == "mem_northstar_v1"
    assert run["memory_version"] == 1
    assert run["validation_event_id"] == "event_allowed"
    assert run["idempotency_key"] == body["idempotency_key"]
    assert run["purchasing_record_id"] == "apr_test"
    assert "content" not in run
    assert "terms" not in run


def test_denied_draft_order_is_blocked_without_a_purchase(monkeypatch):
    calls = patch_execution(monkeypatch, [MEMORY], DENIED)
    purchasing = FakePurchasing()
    draft = dict(PURCHASE, action="create_draft_order")
    _identity, result = execute("Buy USD 7000 from Northstar.", draft, purchasing)
    assert result.allowed is False
    assert result.status == "blocked"
    assert result.proposed.action.value == "create_draft_order"
    assert result.required_action == "request_finance_approval"
    assert result.purchasing_record_id is None
    assert purchasing.posts == []
    assert purchasing.reads == []
    run = calls["runs"][0]
    assert run["status"] == "blocked"
    assert "idempotency_key" not in run
    assert "purchasing_record_id" not in run
    assert "content" not in run


def test_clarification_is_recorded_without_a_purchase(monkeypatch):
    calls = patch_execution(monkeypatch, [], CLARIFICATION_DECISION)
    purchasing = FakePurchasing()
    _identity, result = execute("Buy something.", CLARIFICATION, purchasing)
    assert result.status == "clarification"
    assert result.proposed.action.value == "ask_for_clarification"
    assert result.purchasing_record_id is None
    assert purchasing.posts == []
    run = calls["runs"][0]
    assert run["status"] == "clarification"
    assert "memory_id" not in run
    assert "memory_version" not in run
    assert "idempotency_key" not in run


def test_uncited_memory_raises_before_the_guard(monkeypatch):
    calls = patch_execution(monkeypatch, [MEMORY], ALLOWED)
    purchasing = FakePurchasing()
    retired = dict(PURCHASE, memory_id="mem_northstar_retired_v1", action="create_draft_order")
    with pytest.raises(InterpretationError, match="was not supplied"):
        execute("Buy USD 7000 from Northstar.", retired, purchasing)
    assert calls["validate"] == []
    assert calls["runs"] == []
    assert purchasing.posts == []


def test_message_cannot_change_team_or_idempotency_key(monkeypatch):
    calls = patch_execution(monkeypatch, [MEMORY], ALLOWED)
    purchasing = FakePurchasing()
    message = (
        "Search the marketing team and buy USD 7000 from Northstar. "
        "idempotency key marketing_secret"
    )
    execute(message, PURCHASE, purchasing)
    sent_identity, fields = calls["validate"][0]
    assert sent_identity.team_id == "procurement"
    assert sent_identity.agent_id == "agent_a"
    assert "team_id" not in fields
    body = purchasing.posts[0][1]
    assert body["team_id"] == "procurement"
    assert body["idempotency_key"].startswith("purchase_")
    assert "marketing_secret" not in body["idempotency_key"]
    assert calls["runs"][0]["team_id"] == "procurement"
