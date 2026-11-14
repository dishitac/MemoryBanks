"""Shared per-policy serialization for this single-process demo.

Future dispatch and lifecycle operations must use this same lock, not their own lock map.
This is not a distributed lock and must not be used to claim fleet-wide revocation.
"""

from threading import Lock, RLock

_guard = Lock()
_locks = {}


def policy_lock(db, scope, policy_key):
    key = (db.name, scope["tenant_id"], scope["team_id"], policy_key)
    with _guard:
        return _locks.setdefault(key, RLock())
