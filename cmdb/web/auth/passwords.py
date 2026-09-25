"""Password hashing on the standard library alone.

`hashlib.scrypt` is a memory-hard KDF in the stdlib, which means local logins
cost this project no new dependency -- the same standing habit as the vendored
web assets and the hand-rolled SVG charts. Parameters are recorded *in* the
stored string, so raising them later leaves existing hashes verifiable and a
rehash-on-login can be added without a migration.

Format: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SCHEME = "scrypt"
# n=2**15 (32768), r=8, p=1 -> ~32 MiB and ~100ms per hash on a modern CPU.
# Python's scrypt refuses to allocate more than `maxmem`, which defaults to a
# value too small for these parameters, so it is passed explicitly below.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
# 128 * n * r * p, plus headroom. Without this hashlib raises
# "memory limit exceeded" rather than doing the work.
MAXMEM = 128 * SCRYPT_N * SCRYPT_R * SCRYPT_P * 2


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=KEY_BYTES,
        maxmem=128 * n * r * p * 2,
    )


def hash_password(password: str) -> str:
    """A fresh salt per call, so two users with one password get two hashes."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """True only for a hash this module produced and a matching password.

    Returns False -- never raises -- for None, an empty string, a hash written
    by some other scheme, or a corrupted one. `stored` is None for every
    federated-only user, and a NULL password hash must never authenticate
    anybody, so the safe answer for anything unparseable is "no".
    """
    if not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        return False
    try:
        n, r, p = (int(value) for value in parts[1:4])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return False
    if not salt or not expected:
        return False
    try:
        candidate = _derive(password, salt, n, r, p)
    except (ValueError, OverflowError, MemoryError):
        # Absurd parameters in a tampered hash must not crash a login handler.
        return False
    # Constant-time: a byte-by-byte `==` leaks how much of the digest matched.
    return hmac.compare_digest(candidate, expected)
