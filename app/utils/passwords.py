"""Password hashing helpers.

This code intentionally uses stdlib-only PBKDF2 to avoid extra dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_SCHEME = "pbkdf2_sha256"
_ITERATIONS = 260_000
_SALT_BYTES = 16
_DKLEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=_DKLEN)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    dk_b64 = base64.urlsafe_b64encode(dk).decode("ascii").rstrip("=")
    return f"{_SCHEME}${_ITERATIONS}${salt_b64}${dk_b64}"


def verify_password(password: str, stored: str) -> bool:
    # Back-compat: if the value doesn't look hashed, treat it as plaintext.
    if not stored.startswith(f"{_SCHEME}$"):
        return hmac.compare_digest(password, stored)

    try:
        _, iterations_str, salt_b64, dk_b64 = stored.split("$", 3)
        iterations = int(iterations_str)
        salt = base64.urlsafe_b64decode(_pad_b64(salt_b64))
        expected = base64.urlsafe_b64decode(_pad_b64(dk_b64))
    except Exception:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(derived, expected)


def _pad_b64(value: str) -> bytes:
    padding = "=" * ((4 - (len(value) % 4)) % 4)
    return (value + padding).encode("ascii")

