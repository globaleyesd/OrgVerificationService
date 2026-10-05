"""The AWS status function (deploy/lambda/control.py), run against a fake EC2 client. It reports the server's state
and can't start or stop anything: only the Control Center turns the project on or off."""
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "deploy" / "lambda" / "control.py"


class FakeEC2:
    """state "zero" means no server exists (the project was taken to zero by the Control Center)."""
    def __init__(self, state="stopped"):
        self.state, self.calls, self.filters = state, [], None

    def describe_instances(self, Filters):
        self.filters = Filters
        if self.state == "zero":
            return {"Reservations": []}
        return {"Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": self.state}}]}]}

    def __getattr__(self, name):                 # any other EC2 call (start, stop, terminate...) is recorded
        def call(**kw):
            self.calls.append(name)
        return call


def load(ec2):
    boto3 = types.ModuleType("boto3")
    made = []
    boto3.client = lambda name: made.append(name) or ec2
    sys.modules["boto3"] = boto3
    os.environ["PROJECT"] = "kb-verifier"
    spec = importlib.util.spec_from_file_location("control_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.clients_made = made
    return mod


def ev(path, method="GET", body=None):
    return {"rawPath": path, "requestContext": {"http": {"method": method}}, "body": json.dumps(body) if body is not None else None}


class StatusFunctionTests(unittest.TestCase):
    def setUp(self):
        self.ec2 = FakeEC2("stopped")
        self.c = load(self.ec2)

    def call(self, path, **kw):
        r = self.c.handler(ev(path, **kw), None)
        return r["statusCode"], json.loads(r["body"])

    def test_fits_the_inline_limit(self):
        self.assertLess(len(SRC.read_bytes()), 4096)

    def test_status_reports_the_server_state(self):
        for st, on in (("running", True), ("pending", True), ("stopped", False), ("stopping", False), ("zero", False)):
            self.ec2.state = st
            self.assertEqual(self.call("/control/status"), (200, {"state": st, "on": on}))

    def test_the_server_is_found_by_its_project_tag(self):
        self.call("/control/status")
        self.assertIn({"Name": "tag:Project", "Values": ["kb-verifier"]}, self.ec2.filters)

    def test_nothing_here_can_turn_the_project_on_or_off(self):
        for state in ("stopped", "running", "zero"):
            self.ec2.state = state
            for path in ("/control/on", "/control/off", "/control/start", "/control/zero"):
                for method in ("POST", "GET", "PUT"):
                    code, body = self.call(path, method=method, body={"password": "anything"})
                    self.assertEqual(code, 404)
                    self.assertIn("Control Center", body["detail"])
        self.assertEqual(self.ec2.calls, [])
        self.assertEqual(self.c.clients_made, ["ec2"])              # no S3: it reads no password records at all
        src = SRC.read_text()
        for word in ("start_instances", "stop_instances", "terminate", "get_object", "s3"):
            self.assertNotIn(word, src)

    def test_api_requests_routed_here_at_zero_get_the_offline_answer(self):
        for method in ("GET", "POST"):
            self.assertEqual(self.call("/api/ask", method=method), (503, {"detail": "Service offline", "offline": True}))

    def test_status_only_answers_get(self):
        self.assertEqual(self.call("/control/status", method="POST", body={})[0], 404)

    def test_aws_errors_become_a_plain_503_without_details(self):
        class Boom(FakeEC2):
            def describe_instances(self, Filters):
                raise RuntimeError("secret internal detail")
        r = load(Boom()).handler(ev("/control/status"), None)
        self.assertEqual(r["statusCode"], 503)
        self.assertNotIn("secret internal", r["body"])

    def test_responses_are_never_cached(self):
        self.assertEqual(self.c.handler(ev("/control/status"), None)["headers"]["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
