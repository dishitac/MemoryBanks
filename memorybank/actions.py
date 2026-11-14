"""Final purchasing-policy checks and a synchronous check-to-dispatch boundary.

Validation is not external execution. The partner supplies dispatch, read-back verification,
idempotency, and run receipts inside the guarded context. Never await inside this context:
run the entire block in one synchronous worker thread in this single-process demo.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from memorybank.memory_gateway import AccessDenied
from memorybank.policy_locks import policy_lock
from memorybank.proposals import MAX_INTEGER, canonical_policy

PURCHASE_ACTIONS = {"create_draft_order", "request_finance_approval"}


def positive_integer(value):
    return type(value) is int and 1 <= value <= MAX_INTEGER


def decision(allowed, reason, action_to_dispatch=None, required_action=None):
    return {
        "allowed": allowed,
        "reason": reason,
        "action_to_dispatch": action_to_dispatch,
        "required_action": required_action,
    }


class ActionValidator:
    def __init__(self, gateway):
        self.gateway = gateway
        self.db = gateway.db

    def _purchaser(self, identity):
        scope = self.gateway._scope(identity)
        if not self.db.principals.find_one(
            {
                "_id": identity.agent_id,
                **scope,
                "role": "purchasing_agent",
            }
        ):
            raise AccessDenied("Only the purchasing agent can propose purchase actions")
        return scope

    def _check(
        self,
        identity,
        scope,
        action,
        supplier_id,
        currency,
        amount,
        memory_id,
        memory_version,
        policy_key=None,
    ):
        if action == "ask_for_clarification":
            return decision(
                False,
                "Clarification requested; no purchasing action authorized",
                required_action="ask_for_clarification",
            )
        if not isinstance(action, str) or action not in PURCHASE_ACTIONS:
            return decision(False, "Unsupported action", required_action="ask_for_clarification")
        if not isinstance(supplier_id, str) or not supplier_id or len(supplier_id) > 100:
            return decision(False, "Invalid supplier", required_action="ask_for_clarification")
        if currency != "USD" or not positive_integer(amount):
            return decision(
                False,
                "Purchases require USD and a positive whole-dollar amount",
                required_action="ask_for_clarification",
            )
        if (
            not isinstance(memory_id, str)
            or not memory_id
            or len(memory_id) > 200
            or not positive_integer(memory_version)
        ):
            return decision(
                False,
                "A valid supporting memory ID and version are required",
                required_action="ask_for_clarification",
            )
        selector = {"_id": memory_id, "version": memory_version}
        if policy_key is not None:
            selector["policy_key"] = policy_key
        memory = self.gateway._read_current(scope, selector, supplier_id)
        if memory is None:
            return decision(
                False,
                "Supporting memory is unavailable, stale, or not applicable",
                required_action="ask_for_clarification",
            )
        try:
            canonical_policy(memory["terms"])
        except ValueError:
            return decision(
                False,
                "Supporting purchasing terms are invalid",
                required_action="ask_for_clarification",
            )
        required = (
            "request_finance_approval"
            if amount > memory["terms"]["max_without_approval"]
            else "create_draft_order"
        )
        if action != required:
            return decision(False, f"Current policy requires {required}", required_action=required)
        # This dictionary is reconstructed from checked values and server identity.
        # The server adapter adds its idempotency key, maps action to a route, and verifies state.
        payload = {
            "action": required,
            "supplier_id": memory["terms"]["supplier_id"],
            "currency": "USD",
            "amount": amount,
            "memory_id": memory["memory_id"],
            "memory_version": memory["version"],
            "requested_by_user_id": identity.user_id,
            "submitted_by_agent_id": identity.agent_id,
            **scope,
        }
        return decision(
            True, "Current policy and identity authorize this action", payload, required
        )

    def _record(self, identity, scope, result):
        event_id = f"event_{uuid4().hex}"
        event = {
            "_id": event_id,
            **scope,
            "event_type": "action_allowed" if result["allowed"] else "action_blocked",
            "user_id": identity.user_id,
            "agent_id": identity.agent_id,
            "created_at": datetime.now(UTC),
            "reason": result["reason"],
            "required_action": result["required_action"],
        }
        if result["allowed"]:
            payload = result["action_to_dispatch"]
            event.update(
                memory_id=payload["memory_id"],
                memory_version=payload["memory_version"],
                action=payload["action"],
                amount=payload["amount"],
            )
        # Audit storage failure raises before any dispatch can occur.
        self.db.memory_events.insert_one(event)
        return {**result, "validation_event_id": event_id}

    @contextmanager
    def guarded(
        self,
        identity,
        action,
        supplier_id=None,
        currency=None,
        amount=None,
        memory_id=None,
        memory_version=None,
    ):
        scope = self._purchaser(identity)
        # Locate the policy's lock without returning content or accepting an injected filter.
        reference = None
        if isinstance(memory_id, str) and memory_id and len(memory_id) <= 200:
            reference = self.db.memories.find_one({"_id": memory_id, **scope}, {"policy_key": 1})
        if reference is None:
            result = self._check(
                identity, scope, action, supplier_id, currency, amount, memory_id, memory_version
            )
            if result["allowed"]:
                # Never authorize a record that appeared before its lock could be located.
                result = decision(
                    False,
                    "Supporting memory changed while locating its lock; retry",
                    required_action="ask_for_clarification",
                )
            yield self._record(identity, scope, result)
            return
        with policy_lock(self.db, scope, reference["policy_key"]):
            scope = self._purchaser(
                identity
            )  # Roles could have changed while waiting for the lock.
            result = self._check(
                identity,
                scope,
                action,
                supplier_id,
                currency,
                amount,
                memory_id,
                memory_version,
                reference["policy_key"],
            )
            recorded = self._record(identity, scope, result)
            if result["allowed"]:
                # Audit I/O takes time. Check the deadline/identity again immediately before
                # handing control to dispatch, rather than relying on the pre-audit read.
                scope = self._purchaser(identity)
                final = self._check(
                    identity,
                    scope,
                    action,
                    supplier_id,
                    currency,
                    amount,
                    memory_id,
                    memory_version,
                    reference["policy_key"],
                )
                if not final["allowed"]:
                    recorded = self._record(identity, scope, final)
            yield recorded
