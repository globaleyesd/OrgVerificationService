"""User accounts and sign-in sessions.

Accounts live in the credential store (a private S3 bucket on AWS, a local folder otherwise) as
`users/<username>` records: display name, role (a clearance level) and a salted password hash.

Sessions are a signed cookie value: base64(JSON claims) + "." + HMAC-SHA256. The signing key is a
random 32-byte secret kept in the credential store (`session_key`), created on first use. Tokens
are stateless, so they cannot be revoked before they expire (auth.session_minutes). The role is
inside the token, so a role change takes effect at the next sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass

from .credstore import CredentialStore
from .passwords import burn_time, check_password, make_record

USER_PREFIX = "users/"
SESSION_KEY_NAME = "session_key"
_USERNAME = re.compile(r"[a-z0-9_]{2,40}")

# The two demo accounts. Eileen sees everything (Super); AllMinusEileen stands for every other
# person (Employee), so one shared account shows what everyone but Eileen sees.
DEMO_USERS = (   # (username, display name, role). Two pairs that behave the same: a Super and an Employee each.
    ("eileen", "Eileen", "super"),
    ("allminuseileen", "AllMinusEileen", "employee"),
    ("mark", "Mark", "super"),
    ("allminusmark", "AllMinusMark", "employee"),
)


class BadLogin(Exception):
    pass


class LoginLocked(Exception):
    def __init__(self, seconds_left: float):
        super().__init__("Too many attempts")
        self.seconds_left = max(1, int(seconds_left))


class UserExists(Exception):
    pass


class DemoLoginDenied(Exception):
    """Demo sign-in is switched off, or the name is not one of the demo accounts."""


class DemoUsersMissing(Exception):
    """The demo accounts have not been created yet."""


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str


def normalise(username: str) -> str:
    return str(username or "").strip().lower()


class UserStore:
    def __init__(self, store: CredentialStore, levels: list[str], *, min_password_length: int = 12,
                 max_failed: int = 5, lockout_minutes: float = 15, clock=time.time):
        self.store, self.levels = store, list(levels)
        self.min_len, self.max_failed, self.lockout = int(min_password_length), int(max_failed), float(lockout_minutes) * 60
        self.clock = clock
        self._fails: dict[str, int] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def _key(self, username: str) -> str:
        return USER_PREFIX + username

    def create(self, username: str, display_name: str, role: str, password: str, *, replace: bool = False) -> None:
        u = normalise(username)
        if not _USERNAME.fullmatch(u):
            raise ValueError("Usernames use letters, digits and underscores (2-40 characters)")
        if role not in self.levels:
            raise ValueError(f"Unknown role '{role}'")
        if len(password) < self.min_len:
            raise ValueError(f"Passwords need at least {self.min_len} characters")
        if not replace and self.store.read(self._key(u)) is not None:
            raise UserExists(u)
        self.store.write(self._key(u), {"username": u, "display_name": str(display_name)[:60], "role": role,
                                         "password": make_record(password)})

    def get(self, username: str) -> User | None:
        rec = self.store.read(self._key(normalise(username)))
        if not rec or rec.get("role") not in self.levels:
            return None   # a record with an unknown role never signs in
        return User(rec["username"], rec.get("display_name") or rec["username"], rec["role"])

    def list_users(self) -> list[User]:
        out = []
        for name in self.store.list(USER_PREFIX):
            u = self.get(name[len(USER_PREFIX):])
            if u:
                out.append(u)
        return out

    def set_password(self, username: str, password: str) -> None:
        u = normalise(username)
        rec = self.store.read(self._key(u))
        if rec is None:
            raise ValueError("No such user")
        if len(password) < self.min_len:
            raise ValueError(f"Passwords need at least {self.min_len} characters")
        rec["password"] = make_record(password)
        self.store.write(self._key(u), rec)

    def verify(self, username: str, password: str) -> User:
        """Return the user, or raise BadLogin / LoginLocked. Same time and same error for an unknown
        name and a wrong password."""
        u = normalise(username)
        with self._lock:
            now = self.clock()
            if now < self._locked.get(u, 0):
                raise LoginLocked(self._locked[u] - now)
        rec = self.store.read(self._key(u)) if _USERNAME.fullmatch(u) else None
        ok = False
        if rec and isinstance(password, str) and rec.get("role") in self.levels:
            ok = check_password(password, rec.get("password") or {})
        else:
            burn_time()
        with self._lock:
            if ok:
                self._fails.pop(u, None)
                return User(rec["username"], rec.get("display_name") or rec["username"], rec["role"])
            self._fails[u] = self._fails.get(u, 0) + 1
            if self._fails[u] >= self.max_failed:
                self._fails.pop(u, None)
                self._locked[u] = self.clock() + self.lockout
                raise LoginLocked(self.lockout)
        raise BadLogin()


def demo_login(users: "UserStore", username: str, demo_mode: bool) -> User:
    """Passwordless sign-in for the demo: pick one of DEMO_USERS (Eileen, AllMinusEileen, Mark, AllMinusMark).

    SECURITY: only when demo_mode is on, and ONLY the demo accounts. Any other account, even one
    that exists, is refused, so turning on demo mode never makes a real account passwordless.
    """
    if not demo_mode:
        raise DemoLoginDenied("Demo sign-in is switched off")
    name = normalise(username)
    if name not in {u for u, _, _ in DEMO_USERS}:
        raise DemoLoginDenied("Not a demo account")
    user = users.get(name)
    if user is None:
        raise DemoUsersMissing(name)
    return user


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SessionSigner:
    def __init__(self, key: bytes, clock=time.time):
        if len(key) < 32:
            raise ValueError("Signing key too short")
        self.key, self.clock = key, clock

    def issue(self, user: User, minutes: float) -> str:
        claims = {"u": user.username, "n": user.display_name, "r": user.role, "exp": int(self.clock() + minutes * 60)}
        body = _b64(json.dumps(claims, separators=(",", ":")).encode())
        return body + "." + _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())

    def verify(self, token: str | None) -> User | None:
        """The user in a valid, unexpired token, else None. Never raises."""
        try:
            body, sig = (token or "").split(".")
            expected = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            claims = json.loads(_unb64(body))
            if not isinstance(claims, dict) or claims.get("exp", 0) <= self.clock():
                return None
            return User(str(claims["u"]), str(claims.get("n") or claims["u"]), str(claims["r"]))
        except Exception:
            return None


def load_signer(store: CredentialStore, clock=time.time) -> SessionSigner:
    """Load the signing key from the credential store, creating it on first use."""
    rec = store.read(SESSION_KEY_NAME)
    if rec is None:
        rec = {"key": _b64(secrets.token_bytes(32))}
        store.write(SESSION_KEY_NAME, rec)
    return SessionSigner(_unb64(rec["key"]), clock)
