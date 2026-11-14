"""Dispatch a purchase only inside the validated_action guard.

Interpretation happens first. The returned inspection from validate_stored_purchase is not a
permit, and this module does not call it. The idempotency key is created here.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from memorybank.agents import (
    InterpretationError,
    PurchaseAction,
    _stored_gateway,
    interpret_stored_purchase,
)
from memorybank.llm import GeminiSettings
from memorybank.memory_gateway import ServerIdentity

ROUTES = {
    "request_finance_approval": ("approval_request", "/approval-requests"),
    "create_draft_order": ("draft_order", "/draft-orders"),
}
TIMEOUT_SECONDS = 5.0


class PurchasingError(Exception):
    """The purchasing service rejected the record."""


class PurchasingUnavailable(Exception):
    """The purchasing call was not confirmed."""


class PurchaseExecution(BaseModel):
    """The guarded outcome. A denied purchase has no purchasing record."""

    proposed: PurchaseAction
    allowed: bool
    reason: str = Field(min_length=1)
    required_action: str | None = None
    status: Literal["blocked", "clarification", "verified", "failed", "uncertain"]
    run_id: str = Field(min_length=1)
    purchasing_record_id: str | None = None


class HttpPurchasingClient:
    """Synchronous purchasing calls. One timeout, no retries."""

    def __init__(
        self,
        base_url: str,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def create(self, kind: str, payload: dict) -> dict:
        response = self._send("POST", _path(kind), payload)
        if response.status_code >= 400:
            raise PurchasingError(
                f"Purchasing service rejected the record ({response.status_code})."
            )
        body = _json_object(response)
        if body is None or not isinstance(body.get("id"), str) or not body["id"]:
            raise PurchasingUnavailable("Purchasing response had no record id.")
        return body

    def read(self, kind: str, record_id: str) -> dict | None:
        response = self._send("GET", f"{_path(kind)}/{record_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise PurchasingUnavailable("Purchasing read-back was not confirmed.")
        body = _json_object(response)
        if body is None:
            raise PurchasingUnavailable("Purchasing read-back was not confirmed.")
        return body

    def _send(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        try:
            return self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise PurchasingUnavailable("Purchasing request was not confirmed.") from exc


def execute_stored_purchase(
    message: str,
    identity: ServerIdentity,
    supplier_id: str | None = None,
    *,
    client: Any = None,
    model: str | None = None,
    settings: GeminiSettings | None = None,
    purchasing: Any = None,
) -> PurchaseExecution:
    """Interpret a purchase, then post and read it back only inside the guard."""
    action = interpret_stored_purchase(
        message,
        identity,
        supplier_id,
        client=client,
        model=model,
        settings=settings,
    )
    resolved, gateway = _stored_gateway(settings)
    owns_client = purchasing is None
    if purchasing is None:
        buyer = HttpPurchasingClient(resolved.purchasing_service_url)
    else:
        buyer = purchasing
    try:
        with gateway.validated_action(identity, **action.model_dump(mode="json")) as gate:
            return _dispatch(gateway, identity, action, gate, buyer)
    finally:
        if owns_client:
            buyer.close()


def _dispatch(gateway, identity, action: PurchaseAction, gate, buyer) -> PurchaseExecution:
    if (
        not isinstance(gate, dict)
        or not isinstance(gate.get("allowed"), bool)
        or not isinstance(gate.get("reason"), str)
        or not gate["reason"]
    ):
        raise InterpretationError("validated_action did not return a decision.")
    required = gate.get("required_action")
    if required is not None and not isinstance(required, str):
        raise InterpretationError("validated_action did not return a decision.")
    if not gate["allowed"]:
        status = "clarification" if required == "ask_for_clarification" else "blocked"
        run_id = _save_run(gateway, identity, action, gate, status)
        return _execution(action, gate, status, run_id)

    payload = gate.get("action_to_dispatch")
    if not isinstance(payload, dict):
        run_id = _save_run(gateway, identity, action, gate, "failed")
        return _execution(action, gate, "failed", run_id)
    body = dict(payload)
    route = ROUTES.get(body.pop("action", None))
    if route is None:
        run_id = _save_run(gateway, identity, action, gate, "failed")
        return _execution(action, gate, "failed", run_id)
    kind, _route_path = route
    idempotency_key = f"purchase_{uuid4().hex}"
    body["idempotency_key"] = idempotency_key
    try:
        created = buyer.create(kind, body)
    except PurchasingError:
        run_id = _save_run(
            gateway, identity, action, gate, "failed", idempotency_key=idempotency_key, sent=body
        )
        return _execution(action, gate, "failed", run_id)
    except PurchasingUnavailable:
        run_id = _save_run(
            gateway,
            identity,
            action,
            gate,
            "uncertain",
            idempotency_key=idempotency_key,
            sent=body,
        )
        return _execution(action, gate, "uncertain", run_id)

    record_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(record_id, str) or not record_id:
        run_id = _save_run(
            gateway,
            identity,
            action,
            gate,
            "uncertain",
            idempotency_key=idempotency_key,
            sent=body,
        )
        return _execution(action, gate, "uncertain", run_id)
    try:
        verified = buyer.read(kind, record_id)
    except PurchasingUnavailable:
        verified = None
    if not isinstance(verified, dict):
        run_id = _save_run(
            gateway,
            identity,
            action,
            gate,
            "uncertain",
            idempotency_key=idempotency_key,
            purchasing_record_id=record_id,
            sent=body,
        )
        return _execution(action, gate, "uncertain", run_id, record_id)
    if not _matches(body, verified):
        run_id = _save_run(
            gateway,
            identity,
            action,
            gate,
            "failed",
            idempotency_key=idempotency_key,
            purchasing_record_id=record_id,
            sent=body,
        )
        return _execution(action, gate, "failed", run_id, record_id)
    run_id = _save_run(
        gateway,
        identity,
        action,
        gate,
        "verified",
        idempotency_key=idempotency_key,
        purchasing_record_id=record_id,
        sent=body,
    )
    return _execution(action, gate, "verified", run_id, record_id)


def _execution(
    action: PurchaseAction,
    gate: dict,
    status: str,
    run_id: str,
    purchasing_record_id: str | None = None,
) -> PurchaseExecution:
    required = gate.get("required_action")
    return PurchaseExecution(
        proposed=action,
        allowed=gate["allowed"],
        reason=gate["reason"],
        required_action=required if isinstance(required, str) else None,
        status=status,
        run_id=run_id,
        purchasing_record_id=purchasing_record_id,
    )


def _matches(sent: dict, record: dict) -> bool:
    return all(record.get(key) == value for key, value in sent.items())


def _save_run(
    gateway,
    identity: ServerIdentity,
    action: PurchaseAction,
    gate: dict,
    status: str,
    *,
    idempotency_key: str | None = None,
    purchasing_record_id: str | None = None,
    sent: dict | None = None,
) -> str:
    run_id = f"run_{uuid4().hex}"
    document = {
        "_id": run_id,
        "tenant_id": identity.tenant_id,
        "team_id": identity.team_id,
        "user_id": identity.user_id,
        "agent_id": identity.agent_id,
        "status": status,
        "created_at": datetime.now(UTC),
        "reason": gate["reason"],
    }
    event_id = gate.get("validation_event_id")
    if isinstance(event_id, str) and event_id:
        document["validation_event_id"] = event_id
    document.update(_memory_fields(action, sent))
    if idempotency_key is not None:
        document["idempotency_key"] = idempotency_key
    if purchasing_record_id is not None:
        document["purchasing_record_id"] = purchasing_record_id
    gateway.db.runs.insert_one(document)
    return run_id


def _memory_fields(action: PurchaseAction, sent: dict | None) -> dict:
    if sent is not None:
        memory_id = sent.get("memory_id")
        version = sent.get("memory_version")
    else:
        memory_id = action.memory_id
        version = action.memory_version
    fields = {}
    if isinstance(memory_id, str) and memory_id:
        fields["memory_id"] = memory_id
    if type(version) is int:
        fields["memory_version"] = version
    return fields


def _path(kind: str) -> str:
    for route_kind, path in ROUTES.values():
        if route_kind == kind:
            return path
    raise PurchasingError(f"Unknown purchasing record kind: {kind}.")


def _json_object(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
