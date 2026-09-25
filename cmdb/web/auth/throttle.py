"""A brute-force floor for the local login form.

In-memory and per-process on purpose: no new table, no migration, and nothing
to clean up. A restart clears it, which is an acceptable trade for a homelab
tool whose live deployment does not use this login path at all -- there,
Authentik's ReputationPolicy is what throttles.

Keyed by username rather than address. That is the same trade Authentik's
`check_username: true` makes and it has the same downside: someone who knows a
username can keep that account locked from anywhere. The alternative -- keying
on address -- is defeated by any client with more than one, which is the more
likely attacker here.
"""

from __future__ import annotations

import time
from threading import Lock

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300.0

_failures: dict[str, tuple[int, float]] = {}
_lock = Lock()


def locked_for(username: str) -> float:
    """Seconds remaining on a lockout, or 0.0 when the account may try again."""
    with _lock:
        record = _failures.get(username.lower())
        if not record:
            return 0.0
        count, last = record
        if count < MAX_FAILURES:
            return 0.0
        remaining = LOCKOUT_SECONDS - (time.monotonic() - last)
        return remaining if remaining > 0 else 0.0


def record_failure(username: str) -> None:
    with _lock:
        key = username.lower()
        count, last = _failures.get(key, (0, 0.0))
        now = time.monotonic()
        # An expired lockout starts a fresh count rather than leaving the account
        # one attempt away from locking again forever.
        if count >= MAX_FAILURES and now - last >= LOCKOUT_SECONDS:
            count = 0
        _failures[key] = (count + 1, now)


def record_success(username: str) -> None:
    with _lock:
        _failures.pop(username.lower(), None)


def reset() -> None:
    """Test hook."""
    with _lock:
        _failures.clear()
