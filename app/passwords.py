"""Password hashing shared by the service switch and user sign-in.

Only a salted scrypt hash is ever stored (never the password). Comparison is constant-time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def make_record(password: str, is_default: bool = False) -> dict:
    salt = _secrets.token_bytes(16)
    return {
        "algo": "scrypt", **_SCRYPT,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(_hash(password, salt)).decode(),
        "is_default": bool(is_default),
    }


def check_password(password: str, record: dict) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(_hash(password, salt), expected)


# Used to spend the same time on an unknown user name as on a real one (stops "does this user exist?" probing).
_DUMMY = make_record("not-a-real-password")


def burn_time() -> None:
    check_password("x", _DUMMY)
