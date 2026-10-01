"""Password-protected on/off switch for the whole service.

While OFF every API (except health, status and the switch itself) answers
"Service offline". See app/gate.py for the exact exemptions.

Password handling
- Only a salted scrypt hash is stored (never the password), in the credential store (a local folder, or a private S3 bucket on AWS).
- Comparison is constant-time.
- Wrong guesses are counted GLOBALLY: after `max_failed_attempts` the switch locks for
  `lockout_minutes`. Global (not per address) because traffic arrives via a CDN, so
  addresses can't be trusted. Trade-off: someone can lock you out by guessing wrong;
  the lockout expires on its own.

State
- on/off is saved to a small JSON file so it survives restarts.
- Missing, unreadable or malformed state means OFF (fail safe).
- Optional auto-off after N hours (COST: a forgotten switch stops costing money).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .config import Config, ConfigError
from .credstore import CredentialStore
from .passwords import check_password, make_record  # noqa: F401  (re-exported for callers and tests)

CRED_NAME = "service_switch"
# Demo-only starting password. The app refuses to run with it unless ui.demo_mode is true,
# and the switch password can be changed with:  python -m app.cli set-switch-password
DEMO_DEFAULT_PASSWORD = "5678"


class WrongPassword(Exception):
    pass


class LockedOut(Exception):
    def __init__(self, seconds_left: float):
        super().__init__("Too many attempts")
        self.seconds_left = max(1, int(seconds_left))


class WeakPassword(Exception):
    pass


class ServiceSwitch:
    def __init__(self, store: CredentialStore, state_path: str | Path, *, auto_off_hours: float = 0,
                 max_failed_attempts: int = 5, lockout_minutes: float = 10, min_password_length: int = 12,
                 clock=time.time):
        self.store = store
        self.state_path = Path(state_path)
        self.auto_off_seconds = float(auto_off_hours) * 3600
        self.max_failed = int(max_failed_attempts)
        self.lockout_seconds = float(lockout_minutes) * 60
        self.min_len = int(min_password_length)
        self.clock = clock
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: Config, store: CredentialStore) -> "ServiceSwitch":
        sw = cfg.service_switch
        return cls(store, sw.state_path, auto_off_hours=sw.auto_off_hours, max_failed_attempts=sw.max_failed_attempts,
                   lockout_minutes=sw.lockout_minutes, min_password_length=cfg.auth.password_min_length)

    # ---- credentials ----
    def ensure_credentials(self, allow_default: bool) -> None:
        """Make sure a password record exists. A missing record is only auto-created (with the
        demo password) when allow_default is true; otherwise start-up must stop."""
        rec = self.store.read(CRED_NAME)
        if rec is None:
            if not allow_default:
                raise ConfigError("No service-switch password is set. Run: python -m app.cli set-switch-password")
            self.store.write(CRED_NAME, make_record(DEMO_DEFAULT_PASSWORD, is_default=True))
        elif rec.get("is_default") and not allow_default:
            raise ConfigError("The service-switch password is still the demo default. "
                              "Run: python -m app.cli set-switch-password")

    def password_is_default(self) -> bool:
        rec = self.store.read(CRED_NAME)
        return bool(rec and rec.get("is_default"))

    def set_password(self, new_password: str) -> None:
        if len(new_password) < self.min_len:
            raise WeakPassword(f"Use at least {self.min_len} characters")
        self.store.write(CRED_NAME, make_record(new_password, is_default=False))

    # ---- state ----
    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text())
            if isinstance(data, dict) and data.get("on") is True and isinstance(data.get("since"), (int, float)):
                return data
        except (OSError, ValueError):
            pass
        return {"on": False, "since": 0}

    def _write_state(self, on: bool) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"on": on, "since": self.clock()}, f)
            os.replace(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def is_on(self) -> bool:
        st = self._read_state()
        if not st["on"]:
            return False
        if self.auto_off_seconds and self.clock() - st["since"] >= self.auto_off_seconds:
            return False
        return True

    def minutes_until_auto_off(self) -> int | None:
        if not self.auto_off_seconds or not self.is_on():
            return None
        left = self.auto_off_seconds - (self.clock() - self._read_state()["since"])
        return max(0, int(left // 60))

    # ---- actions ----
    def _verify(self, password: str) -> None:
        with self._lock:
            now = self.clock()
            if now < self._locked_until:
                raise LockedOut(self._locked_until - now)
            rec = self.store.read(CRED_NAME)
            ok = bool(rec) and isinstance(password, str) and check_password(password, rec)
            if ok:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= self.max_failed:
                self._failures = 0
                self._locked_until = now + self.lockout_seconds
                raise LockedOut(self.lockout_seconds)
            raise WrongPassword()

    def turn_on(self, password: str) -> None:
        self._verify(password)
        self._write_state(True)

    def turn_off(self, password: str) -> None:
        self._verify(password)
        self._write_state(False)
