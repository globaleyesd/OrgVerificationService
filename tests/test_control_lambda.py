"""The AWS switch function (deploy/lambda/control.py), run against fake S3 and EC2 clients."""
import base64
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path

from app.passwords import make_record

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "deploy" / "lambda" / "control.py"
PW = "the-switch-password"


class NoSuchKey(Exception):
    pass


class FakeS3:
    exceptions = types.SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self):
        self.objects, self.puts = {}, []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey()
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body
        self.puts.append((Key, kw))


class FakeEC2:
    """state "zero" means no server exists (the project was taken to zero by the Control Center)."""
    def __init__(self, state="stopped"):
        self.state, self.calls, self.filters = state, [], None

    def describe_instances(self, Filters):
        self.filters = Filters
        if self.state == "zero":
            return {"Reservations": []}
        return {"Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": self.state}}]}]}

    def start_instances(self, InstanceIds):
        self.calls.append("start")

    def stop_instances(self, InstanceIds):
        self.calls.append("stop")


class Clock:
    t = 1_000_000.0

    def time(self):
        return Clock.t


def load(s3, ec2, **env):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name: s3 if name == "s3" else ec2
    sys.modules["boto3"] = boto3
    os.environ.update({"CREDS_BUCKET": "b", "PROJECT": "kb-verifier", **{k: str(v) for k, v in env.items()}})
    spec = importlib.util.spec_from_file_location("control_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time = Clock()
    return mod


def ev(path, method="POST", body=None, b64=False):
    raw = json.dumps(body) if body is not None else None
    if raw is not None and b64:
        raw = base64.b64encode(raw.encode()).decode()
    return {"rawPath": path, "requestContext": {"http": {"method": method}}, "body": raw, "isBase64Encoded": b64}


class ControlTests(unittest.TestCase):
    def setUp(self):
        Clock.t = 1_000_000.0
        self.s3, self.ec2 = FakeS3(), FakeEC2("stopped")
        self.s3.objects["service_switch.json"] = json.dumps(make_record(PW)).encode()
        self.c = load(self.s3, self.ec2, MAX_FAILS=3, LOCK_MIN=10)

    def call(self, path, **kw):
        r = self.c.handler(ev(path, **kw), None)
        return r["statusCode"], json.loads(r["body"])

    def test_fits_the_inline_limit_and_the_template_copy_is_identical(self):
        self.assertLess(len(SRC.read_bytes()), 4096)

    def test_the_password_record_from_the_app_is_understood(self):
        code, body = self.call("/control/on", body={"password": PW})
        self.assertEqual((code, body["state"]), (200, "pending"))

    def test_the_server_is_found_by_its_project_tag(self):
        self.call("/control/status", method="GET")
        self.assertIn({"Name": "tag:Project", "Values": ["kb-verifier"]}, self.ec2.filters)

    def test_at_zero_status_says_so_and_on_off_send_you_to_the_control_center(self):
        self.ec2.state = "zero"
        self.assertEqual(self.call("/control/status", method="GET"), (200, {"state": "zero", "on": False}))
        for which in ("on", "off"):
            code, body = self.call("/control/" + which, body={"password": PW})
            self.assertEqual(code, 409); self.assertIn("Control Center", body["detail"])
        self.assertEqual(self.ec2.calls, [])

    def test_api_requests_routed_here_at_zero_get_the_offline_answer(self):
        for method in ("GET", "POST"):
            self.assertEqual(self.call("/api/ask", method=method), (503, {"detail": "Service offline", "offline": True}))

    def test_turn_on_starts_a_stopped_server(self):
        self.call("/control/on", body={"password": PW})
        self.assertEqual(self.ec2.calls, ["start"])

    def test_turn_on_is_harmless_when_already_running_or_starting(self):
        for st in ("running", "pending"):
            self.ec2.state, self.ec2.calls = st, []
            code, body = self.call("/control/on", body={"password": PW})
            self.assertEqual((code, body["on"], self.ec2.calls), (200, True, []))

    def test_turn_on_while_still_stopping_asks_to_wait(self):
        self.ec2.state = "stopping"
        code, body = self.call("/control/on", body={"password": PW})
        self.assertEqual((code, self.ec2.calls), (409, []))
        self.assertIn("shutting down", body["detail"])

    def test_turn_off_stops_a_running_server(self):
        self.ec2.state = "running"
        code, body = self.call("/control/off", body={"password": PW})
        self.assertEqual((code, body["state"], self.ec2.calls), (200, "stopping", ["stop"]))

    def test_turn_off_while_starting_asks_to_wait(self):
        self.ec2.state = "pending"
        code, _ = self.call("/control/off", body={"password": PW})
        self.assertEqual((code, self.ec2.calls), (409, []))

    def test_turn_off_when_already_stopped_is_harmless(self):
        code, body = self.call("/control/off", body={"password": PW})
        self.assertEqual((code, body["on"], self.ec2.calls), (200, False, []))

    def test_wrong_password_changes_nothing(self):
        for path in ("/control/on", "/control/off"):
            code, body = self.call(path, body={"password": "nope"})
            self.assertEqual((code, body["detail"]), (401, "Wrong password"))
        self.assertEqual(self.ec2.calls, [])

    def test_bad_requests_are_refused_without_touching_the_server(self):
        for e in (ev("/control/on", body={}), ev("/control/on", body={"password": 123}), ev("/control/on", body={"password": None}),
                  ev("/control/on", body={"password": ["x"]})):
            self.s3.objects.pop("switch_lockout.json", None)   # each bad request counts as a failure; reset so the lockout doesn't mask the result
            self.assertEqual(self.c.handler(e, None)["statusCode"], 401)
        self.s3.objects.pop("switch_lockout.json", None)
        self.assertEqual(self.c.handler({"rawPath": "/control/on", "requestContext": {"http": {"method": "POST"}}, "body": "{not json"}, None)["statusCode"], 503)
        self.assertEqual(self.ec2.calls, [])

    def test_base64_bodies_work(self):
        code, _ = self.call("/control/on", body={"password": PW}, b64=True)
        self.assertEqual(code, 200)

    def test_lockout_after_repeated_failures_then_expires(self):
        for _ in range(2):
            self.assertEqual(self.call("/control/on", body={"password": "x"})[0], 401)
        self.assertEqual(self.call("/control/on", body={"password": "x"})[0], 429)
        code, body = self.call("/control/on", body={"password": PW})   # even the right password is refused while locked
        self.assertEqual((code, self.ec2.calls), (429, []))
        self.assertIn("10 minute", body["detail"])
        Clock.t += 601
        self.assertEqual(self.call("/control/on", body={"password": PW})[0], 200)

    def test_a_good_password_clears_earlier_failures(self):
        self.call("/control/on", body={"password": "x"})
        self.call("/control/off", body={"password": PW})
        self.assertEqual(json.loads(self.s3.objects["switch_lockout.json"])["n"], 0)

    def test_missing_password_record_is_a_clear_error_not_a_free_pass(self):
        del self.s3.objects["service_switch.json"]
        code, _ = self.call("/control/on", body={"password": PW})
        self.assertEqual((code, self.ec2.calls), (503, []))

    def test_malformed_record_never_matches(self):
        self.s3.objects["service_switch.json"] = json.dumps({"salt": "!!", "hash": "!!"}).encode()
        self.assertEqual(self.call("/control/on", body={"password": PW})[0], 401)

    def test_status_is_open_and_reports_the_server_state(self):
        for st, on in (("running", True), ("pending", True), ("stopped", False), ("stopping", False)):
            self.ec2.state = st
            code, body = self.call("/control/status", method="GET")
            self.assertEqual((code, body), (200, {"state": st, "on": on}))

    def test_unknown_paths_and_methods_are_404(self):
        self.assertEqual(self.call("/control/other")[0], 404)
        self.assertEqual(self.call("/control/on", method="GET")[0], 404)
        self.assertEqual(self.call("/control/status", method="POST", body={})[0], 404)

    def test_aws_errors_become_a_plain_503_without_details(self):
        class Boom(FakeEC2):
            def describe_instances(self, InstanceIds):
                raise RuntimeError("secret internal detail")
        c = load(self.s3, Boom())
        r = c.handler(ev("/control/status", method="GET"), None)
        self.assertEqual(r["statusCode"], 503)
        self.assertNotIn("secret internal", r["body"])

    def test_responses_are_never_cached_and_never_contain_the_password_record(self):
        r = self.c.handler(ev("/control/on", body={"password": PW}), None)
        self.assertEqual(r["headers"]["cache-control"], "no-store")
        self.assertNotIn("scrypt", r["body"])

    def test_lockout_record_is_written_encrypted(self):
        self.call("/control/on", body={"password": "x"})
        key, kw = self.s3.puts[-1]
        self.assertEqual((key, kw["ServerSideEncryption"]), ("switch_lockout.json", "AES256"))


if __name__ == "__main__":
    unittest.main()
