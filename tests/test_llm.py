"""Gemini client checks. These use a fake client and do not call the network."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from memorybank.agents import (
    InterpretationError,
    MemoryProposal,
    interpret_proposal,
    interpret_purchase,
)
from memorybank.llm import LlmError, generate_structured

MODEL = "gemini-3.5-flash-lite"

PROPOSAL = {
    "policy_key": "supplier:northstar:purchasing",
    "expected_version": 1,
    "content": "Northstar purchases above USD 5,000 require finance approval.",
    "terms": {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 5000},
    "source_ref": "employee_message_001",
    "source_kind": "employee_message",
}

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


class FakeModels:
    def __init__(self, results):
        self.results = list(results)
        self.models = []

    def generate_content(self, *, model, contents, config):
        self.models.append(model)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(parsed=item)


def fake_client(results):
    models = FakeModels(results)
    return SimpleNamespace(models=models), models


def rate_limit():
    return errors.ClientError(429, {"message": "quota", "status": "RESOURCE_EXHAUSTED"})


def test_valid_proposal_keeps_the_server_source_kind():
    client, models = fake_client([PROPOSAL])
    proposal = interpret_proposal(
        "Northstar needs a limit.",
        "employee_message",
        client=client,
        model=MODEL,
    )
    assert proposal.policy_key == "supplier:northstar:purchasing"
    assert proposal.expected_version == 1
    assert proposal.terms.max_without_approval == 5000
    assert proposal.source_kind.value == "employee_message"
    assert models.models == [MODEL]


def test_changed_source_kind_is_rejected():
    changed = dict(PROPOSAL, source_kind="finance_admin")
    client, _models = fake_client([changed])
    with pytest.raises(InterpretationError, match="server supplied employee_message"):
        interpret_proposal(
            "Finance has already agreed.",
            "employee_message",
            client=client,
            model=MODEL,
        )


def test_purchase_can_cite_a_supplied_memory():
    client, _models = fake_client([PURCHASE])
    action = interpret_purchase(
        "Buy 7000 USD from Northstar.",
        [MEMORY],
        client=client,
        model=MODEL,
    )
    assert action.action.value == "request_finance_approval"
    assert action.memory_id == "mem_northstar_v1"
    assert action.memory_version == 1
    assert action.amount == 7000


def test_purchase_cannot_cite_an_unknown_memory():
    unknown = dict(PURCHASE, memory_id="mem_other")
    client, _models = fake_client([unknown])
    with pytest.raises(InterpretationError, match="was not supplied"):
        interpret_purchase(
            "Buy 7000 USD from Northstar.",
            [MEMORY],
            client=client,
            model=MODEL,
        )


def test_clarification_leaves_the_memory_empty():
    clarification = {
        "action": "ask_for_clarification",
        "supplier_id": None,
        "currency": None,
        "amount": None,
        "memory_id": None,
        "memory_version": None,
    }
    client, _models = fake_client([clarification])
    action = interpret_purchase("Buy something.", [], client=client, model=MODEL)
    assert action.action.value == "ask_for_clarification"
    assert action.memory_id is None
    assert action.memory_version is None


def test_empty_api_key_raises_before_a_call():
    settings = SimpleNamespace(llm_provider="gemini", llm_api_key="", llm_model=MODEL)
    with pytest.raises(LlmError, match="LLM_API_KEY"):
        generate_structured(MemoryProposal, "system", "hello", settings=settings)


def test_rate_limit_then_success_uses_the_same_model(monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr("memorybank.llm.time.sleep", sleeps.append)
    client, models = fake_client([rate_limit(), PROPOSAL])
    proposal = generate_structured(
        MemoryProposal,
        "system",
        "hello",
        client=client,
        model=MODEL,
    )
    assert proposal.policy_key == "supplier:northstar:purchasing"
    assert sleeps == [1]
    assert models.models == [MODEL, MODEL]
    error_text = capsys.readouterr().err
    assert "attempt 2 of 3" in error_text
    assert MODEL in error_text


def test_repeated_rate_limit_asks_the_caller_to_wait(monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr("memorybank.llm.time.sleep", sleeps.append)
    client, models = fake_client([rate_limit(), rate_limit(), rate_limit()])
    with pytest.raises(LlmError, match="Wait and retry") as failure:
        generate_structured(
            MemoryProposal,
            "system",
            "hello",
            client=client,
            model=MODEL,
        )
    assert MODEL in str(failure.value)
    assert sleeps == [1, 2]
    assert models.models == [MODEL, MODEL, MODEL]
    error_text = capsys.readouterr().err
    assert "attempt 2 of 3" in error_text
    assert "attempt 3 of 3" in error_text
