import json
import tempfile
import time
import unittest
from pathlib import Path

from app.auth import (BadLogin, DEMO_USERS, DemoLoginDenied, DemoUsersMissing, LoginLocked, SessionSigner, User, UserExists,
                      UserStore, demo_login, load_signer, normalise)
from app.credstore import (CredentialStoreError, FileCredentialStore, S3CredentialStore)

LEVELS = ["employee", "super"]
GOOD_PW = "a-long-enough-password"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make_users(**kw):
    clock = kw.pop("clock", Clock())
    store = FileCredentialStore(Path(tempfile.mkdtemp()) / "creds")
    return UserStore(store, LEVELS, clock=clock, **kw), store, clock


class UserTests(unittest.TestCase):
    def test_create_and_sign_in_case_insensitively(self):
        u, store, _ = make_users()
        u.create("Eileen", "Eileen", "super", GOOD_PW)
        user = u.verify("EILEEN", GOOD_PW)
        self.assertEqual((user.username, user.display_name, user.role), ("eileen", "Eileen", "super"))

    def test_password_is_not_stored_in_plain_text(self):
        u, store, _ = make_users()
        u.create("eileen", "Eileen", "super", GOOD_PW)
        self.assertNotIn(GOOD_PW, json.dumps(store.read("users/eileen")))

    def test_wrong_password_and_unknown_user_look_the_same(self):
        u, *_ = make_users()
        u.create("eileen", "Eileen", "super", GOOD_PW)
        with self.assertRaises(BadLogin):
            u.verify("eileen", "nope")
        with self.assertRaises(BadLogin):
            u.verify("nobody", "nope")

    def test_weird_usernames_cannot_reach_the_store(self):
        u, *_ = make_users()
        for bad in ("../x", "a/b", "", "x" * 100, None):
            with self.assertRaises(BadLogin):
                u.verify(bad, "x")

    def test_lockout_is_per_user_and_expires(self):
        u, _, clock = make_users(max_failed=3, lockout_minutes=15)
        u.create("eileen", "Eileen", "super", GOOD_PW)
        u.create("allminuseileen", "AllMinusEileen", "employee", GOOD_PW)
        for _ in range(2):
            with self.assertRaises(BadLogin):
                u.verify("eileen", "bad")
        with self.assertRaises(LoginLocked):
            u.verify("eileen", "bad")
        with self.assertRaises(LoginLocked):          # right password refused while locked
            u.verify("eileen", GOOD_PW)
        self.assertEqual(u.verify("allminuseileen", GOOD_PW).role, "employee")   # others unaffected
        clock.t += 15 * 60 + 1
        self.assertEqual(u.verify("eileen", GOOD_PW).role, "super")

    def test_create_rules(self):
        u, *_ = make_users(min_password_length=12)
        with self.assertRaises(ValueError):
            u.create("eileen", "E", "boss", GOOD_PW)           # unknown role
        with self.assertRaises(ValueError):
            u.create("eileen", "E", "super", "short")           # weak password
        with self.assertRaises(ValueError):
            u.create("Bad Name!", "E", "super", GOOD_PW)
        u.create("eileen", "E", "super", GOOD_PW)
        with self.assertRaises(UserExists):
            u.create("eileen", "E", "super", GOOD_PW)
        u.create("eileen", "E", "super", GOOD_PW + "2", replace=True)
        self.assertEqual(u.verify("eileen", GOOD_PW + "2").role, "super")

    def test_record_with_unknown_role_never_signs_in(self):
        u, store, _ = make_users()
        u.create("eileen", "Eileen", "super", GOOD_PW)
        rec = store.read("users/eileen"); rec["role"] = "admin"; store.write("users/eileen", rec)
        with self.assertRaises(BadLogin):
            u.verify("eileen", GOOD_PW)
        self.assertIsNone(u.get("eileen"))

    def test_set_password_and_list(self):
        u, *_ = make_users()
        u.create("eileen", "Eileen", "super", GOOD_PW)
        u.create("allminuseileen", "AllMinusEileen", "employee", GOOD_PW)
        u.set_password("eileen", "another-long-password")
        with self.assertRaises(BadLogin):
            u.verify("eileen", GOOD_PW)
        self.assertEqual(u.verify("eileen", "another-long-password").role, "super")
        self.assertEqual([x.username for x in u.list_users()], ["allminuseileen", "eileen"])
        with self.assertRaises(ValueError):
            u.set_password("ghost", "another-long-password")

    def test_store_failure_is_not_treated_as_a_wrong_password_or_missing_user(self):
        class Broken:
            def read(self, n): raise CredentialStoreError("down")
            def write(self, n, d): raise CredentialStoreError("down")
            def list(self, p=""): raise CredentialStoreError("down")
        u = UserStore(Broken(), LEVELS)
        with self.assertRaises(CredentialStoreError):
            u.verify("eileen", "x")

    def test_demo_users_are_the_agreed_accounts(self):
        self.assertEqual([(a, c) for a, _, c in DEMO_USERS],
                         [("eileen", "super"), ("allminuseileen", "employee"), ("mark", "super"), ("allminusmark", "employee")])
        self.assertEqual([b for _, b, _ in DEMO_USERS], ["Eileen", "AllMinusEileen", "Mark", "AllMinusMark"])
        self.assertEqual(normalise("  AllMinusEileen "), "allminuseileen")


class DemoLoginTests(unittest.TestCase):
    def setUp(self):
        self.users, self.store, _ = make_users()
        self.users.create("eileen", "Eileen", "super", GOOD_PW)
        self.users.create("allminuseileen", "AllMinusEileen", "employee", GOOD_PW)
        self.users.create("mark", "Mark", "super", GOOD_PW)
        self.users.create("allminusmark", "AllMinusMark", "employee", GOOD_PW)
        self.users.create("bob", "Bob", "employee", GOOD_PW)

    def test_picking_a_demo_user_signs_in_without_a_password(self):
        self.assertEqual(demo_login(self.users, "Eileen", True).role, "super")
        self.assertEqual(demo_login(self.users, "AllMinusEileen", True).role, "employee")
        self.assertEqual(demo_login(self.users, "  eileen ", True).display_name, "Eileen")

    def test_mark_and_allminusmark_behave_like_eileen_and_allminuseileen(self):
        for a, b in (("Mark", "Eileen"), ("AllMinusMark", "AllMinusEileen")):
            self.assertEqual(demo_login(self.users, a, True).role, demo_login(self.users, b, True).role)
        for who in ("mark", "allminusmark"):
            with self.assertRaises(DemoLoginDenied):
                demo_login(self.users, who, False)

    def test_refused_when_demo_mode_is_off(self):
        for who in ("eileen", "allminuseileen"):
            with self.assertRaises(DemoLoginDenied):
                demo_login(self.users, who, False)

    def test_real_accounts_never_become_passwordless(self):
        with self.assertRaises(DemoLoginDenied):
            demo_login(self.users, "bob", True)          # exists, but is not a demo account
        for odd in ("", "../x", "eileen/../bob", None, "ei"):
            with self.assertRaises(DemoLoginDenied):
                demo_login(self.users, odd, True)

    def test_clear_error_when_the_demo_accounts_were_not_created(self):
        empty, *_ = make_users()
        with self.assertRaises(DemoUsersMissing):
            demo_login(empty, "eileen", True)

    def test_a_demo_account_with_a_bad_role_is_not_signed_in(self):
        rec = self.store.read("users/eileen"); rec["role"] = "admin"; self.store.write("users/eileen", rec)
        with self.assertRaises(DemoUsersMissing):
            demo_login(self.users, "eileen", True)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.signer = SessionSigner(b"k" * 32, self.clock)
        self.user = User("eileen", "Eileen", "super")

    def test_roundtrip(self):
        t = self.signer.issue(self.user, 60)
        self.assertEqual(self.signer.verify(t), self.user)

    def test_expires(self):
        t = self.signer.issue(self.user, 1)
        self.clock.t += 61
        self.assertIsNone(self.signer.verify(t))

    def test_tampering_is_rejected(self):
        t = self.signer.issue(User("allminuseileen", "A", "employee"), 60)
        body, sig = t.split(".")
        import base64
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        claims["r"] = "super"
        forged = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=") + "." + sig
        self.assertIsNone(self.signer.verify(forged))
        self.assertIsNone(self.signer.verify(t[:-2] + "xx"))

    def test_other_key_and_garbage_rejected(self):
        t = self.signer.issue(self.user, 60)
        self.assertIsNone(SessionSigner(b"z" * 32, self.clock).verify(t))
        for bad in (None, "", "abc", "a.b", "a.b.c", "....", "e30.e30"):
            self.assertIsNone(self.signer.verify(bad))

    def test_short_key_refused(self):
        with self.assertRaises(ValueError):
            SessionSigner(b"short")

    def test_key_is_created_once_and_reused(self):
        store = FileCredentialStore(Path(tempfile.mkdtemp()))
        a = load_signer(store, self.clock); t = a.issue(self.user, 60)
        b = load_signer(store, self.clock)
        self.assertEqual(b.verify(t), self.user)


class FakeS3:
    """Just enough of the S3 client to test the store."""
    def __init__(self):
        self.objects, self.calls = {}, []

    class _NoKey(Exception):
        response = {"Error": {"Code": "NoSuchKey"}}

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        if Key not in self.objects:
            raise self._NoKey()
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.calls.append(("put", Bucket, Key, kw))
        self.objects[Key] = Body

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


class S3StoreTests(unittest.TestCase):
    def test_roundtrip_missing_and_list(self):
        c = FakeS3(); s = S3CredentialStore("my-bucket", "v1/", client=c)
        self.assertIsNone(s.read("users/eileen"))
        s.write("users/eileen", {"a": 1}); s.write("service_switch", {"b": 2})
        self.assertEqual(s.read("users/eileen"), {"a": 1})
        self.assertEqual(s.list("users/"), ["users/eileen"])
        self.assertIn("v1/users/eileen.json", c.objects)

    def test_writes_ask_for_server_side_encryption(self):
        c = FakeS3(); S3CredentialStore("b", client=c).write("x", {})
        self.assertEqual(c.calls[-1][3]["ServerSideEncryption"], "AES256")

    def test_service_errors_are_errors_not_missing_records(self):
        class Boom(FakeS3):
            def get_object(self, **kw): raise RuntimeError("network down")
        with self.assertRaises(CredentialStoreError):
            S3CredentialStore("b", client=Boom()).read("x")

    def test_corrupt_record_is_an_error(self):
        c = FakeS3(); c.objects["x.json"] = b"{not json"
        with self.assertRaises(CredentialStoreError):
            S3CredentialStore("b", client=c).read("x")

    def test_bad_names_and_empty_bucket_refused(self):
        with self.assertRaises(ValueError):
            S3CredentialStore("b", client=FakeS3()).read("../secret")
        from app.config import ConfigError
        with self.assertRaises(ConfigError):
            S3CredentialStore("", client=FakeS3())

    def test_users_and_switch_work_on_the_s3_backend(self):
        c = FakeS3(); store = S3CredentialStore("b", client=c)
        users = UserStore(store, LEVELS)
        users.create("eileen", "Eileen", "super", GOOD_PW)
        self.assertEqual(users.verify("eileen", GOOD_PW).role, "super")
        from app.service_switch import DEMO_DEFAULT_PASSWORD, ServiceSwitch
        sw = ServiceSwitch(store, Path(tempfile.mkdtemp()) / "state.json")
        sw.ensure_credentials(allow_default=True)
        self.assertIn("service_switch.json", c.objects)
        sw.turn_on(DEMO_DEFAULT_PASSWORD)
        self.assertTrue(sw.is_on())


class FileStoreErrorTests(unittest.TestCase):
    def test_corrupt_file_is_an_error_not_missing(self):
        d = Path(tempfile.mkdtemp()); (d / "x.json").write_text("{broken")
        with self.assertRaises(CredentialStoreError):
            FileCredentialStore(d).read("x")

    def test_corrupt_switch_record_does_not_get_reset_to_the_demo_password(self):
        from app.service_switch import ServiceSwitch
        d = Path(tempfile.mkdtemp()); (d / "service_switch.json").write_text("{broken")
        sw = ServiceSwitch(FileCredentialStore(d), d / "state.json")
        with self.assertRaises(CredentialStoreError):
            sw.ensure_credentials(allow_default=True)
        self.assertEqual((d / "service_switch.json").read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
