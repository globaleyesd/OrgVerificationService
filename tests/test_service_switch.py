import json
import tempfile
import unittest
from pathlib import Path

from app.config import ConfigError
from app.credstore import FileCredentialStore
from app.service_switch import (CRED_NAME, DEMO_DEFAULT_PASSWORD, LockedOut, ServiceSwitch, WeakPassword,
                                WrongPassword, check_password, make_record)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def build(**kw):
    d = Path(tempfile.mkdtemp())
    clock = kw.pop("clock", Clock())
    store = FileCredentialStore(d / "creds")
    sw = ServiceSwitch(store, d / "state.json", clock=clock, **kw)
    return sw, store, d, clock


class PasswordRecordTests(unittest.TestCase):
    def test_hash_roundtrip_and_no_plaintext(self):
        rec = make_record("correct horse battery")
        self.assertTrue(check_password("correct horse battery", rec))
        self.assertFalse(check_password("wrong", rec))
        self.assertNotIn("correct horse", json.dumps(rec))

    def test_same_password_gets_different_salts(self):
        self.assertNotEqual(make_record("x")["hash"], make_record("x")["hash"])

    def test_malformed_record_never_matches(self):
        self.assertFalse(check_password("x", {}))
        self.assertFalse(check_password("x", {"salt": "!!", "hash": "!!"}))


class SwitchTests(unittest.TestCase):
    def test_starts_off(self):
        sw, *_ = build()
        sw.ensure_credentials(allow_default=True)
        self.assertFalse(sw.is_on())

    def test_on_off_with_password_and_persistence(self):
        sw, store, d, clock = build()
        sw.ensure_credentials(allow_default=True)
        sw.turn_on(DEMO_DEFAULT_PASSWORD)
        self.assertTrue(sw.is_on())
        # a new process with the same files still sees it on
        sw2 = ServiceSwitch(store, d / "state.json", clock=clock)
        self.assertTrue(sw2.is_on())
        sw2.turn_off(DEMO_DEFAULT_PASSWORD)
        self.assertFalse(sw.is_on())

    def test_wrong_password_cannot_turn_on_or_off(self):
        sw, *_ = build()
        sw.ensure_credentials(allow_default=True)
        with self.assertRaises(WrongPassword):
            sw.turn_on("nope")
        self.assertFalse(sw.is_on())
        sw.turn_on(DEMO_DEFAULT_PASSWORD)
        with self.assertRaises(WrongPassword):
            sw.turn_off("nope")
        self.assertTrue(sw.is_on())

    def test_lockout_after_repeated_failures_then_expires(self):
        sw, _, _, clock = build(max_failed_attempts=3, lockout_minutes=10)
        sw.ensure_credentials(allow_default=True)
        for _ in range(2):
            with self.assertRaises(WrongPassword):
                sw.turn_on("bad")
        with self.assertRaises(LockedOut):
            sw.turn_on("bad")
        with self.assertRaises(LockedOut):          # even the right password is refused while locked
            sw.turn_on(DEMO_DEFAULT_PASSWORD)
        clock.t += 601
        sw.turn_on(DEMO_DEFAULT_PASSWORD)
        self.assertTrue(sw.is_on())

    def test_missing_or_corrupt_state_means_off(self):
        sw, _, d, _ = build()
        sw.ensure_credentials(allow_default=True)
        (d / "state.json").write_text("not json")
        self.assertFalse(sw.is_on())
        (d / "state.json").write_text(json.dumps({"on": "yes"}))
        self.assertFalse(sw.is_on())

    def test_auto_off(self):
        sw, _, _, clock = build(auto_off_hours=2)
        sw.ensure_credentials(allow_default=True)
        sw.turn_on(DEMO_DEFAULT_PASSWORD)
        self.assertEqual(sw.minutes_until_auto_off(), 120)
        clock.t += 3600
        self.assertTrue(sw.is_on())
        clock.t += 3601
        self.assertFalse(sw.is_on())

    def test_missing_record_without_demo_refuses_to_start(self):
        sw, *_ = build()
        with self.assertRaises(ConfigError):
            sw.ensure_credentials(allow_default=False)

    def test_default_password_refused_outside_demo(self):
        sw, *_ = build()
        sw.ensure_credentials(allow_default=True)
        self.assertTrue(sw.password_is_default())
        with self.assertRaises(ConfigError):
            sw.ensure_credentials(allow_default=False)

    def test_changing_password_clears_default_and_enforces_length(self):
        sw, *_ = build(min_password_length=12)
        sw.ensure_credentials(allow_default=True)
        with self.assertRaises(WeakPassword):
            sw.set_password("short")
        sw.set_password("a-much-longer-password")
        self.assertFalse(sw.password_is_default())
        sw.ensure_credentials(allow_default=False)   # now fine outside demo mode
        with self.assertRaises(WrongPassword):
            sw.turn_on(DEMO_DEFAULT_PASSWORD)
        sw.turn_on("a-much-longer-password")


class CredStoreTests(unittest.TestCase):
    def test_files_are_owner_only(self):
        d = Path(tempfile.mkdtemp()) / "creds"
        s = FileCredentialStore(d)
        s.write("service_switch", {"a": 1})
        self.assertEqual(oct((d / "service_switch.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")

    def test_bad_names_rejected(self):
        s = FileCredentialStore(tempfile.mkdtemp())
        for bad in ("../x", "a//b", "", "A B", "/abs", "a/../b"):
            with self.assertRaises(ValueError):
                s.write(bad, {})

    def test_unreadable_file_is_an_error_not_a_missing_record(self):
        from app.credstore import CredentialStoreError
        d = Path(tempfile.mkdtemp())
        (d / f"{CRED_NAME}.json").write_text("{broken")
        with self.assertRaises(CredentialStoreError):
            FileCredentialStore(d).read(CRED_NAME)


if __name__ == "__main__":
    unittest.main()
