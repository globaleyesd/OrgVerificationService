"""The Control Center power adapter (deploy/lambda/power.py), run against fake EC2, CloudFormation, SSM and S3."""
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "deploy" / "lambda" / "power.py"


class World:
    """One project: its stack (Power on/zero, a status) and, while powered, one server."""
    def __init__(self, power="on", server="running", stack_status="UPDATE_COMPLETE"):
        self.power, self.server, self.stack_status = power, server, stack_status
        self.calls, self.objects, self.now = [], {}, 1_000_000.0
        self.backup_result = "Success"


class FakeEC2:
    def __init__(self, w):
        self.w = w

    def describe_instances(self, Filters):
        assert {"Name": "tag:Project", "Values": ["kb-verifier"]} in Filters
        if self.w.power == "zero" or self.w.server is None:
            return {"Reservations": []}
        return {"Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": self.w.server}}]}]}

    def start_instances(self, InstanceIds):
        self.w.calls.append("start"); self.w.server = "running"

    def stop_instances(self, InstanceIds):
        self.w.calls.append("stop"); self.w.server = "stopping"


class FakeCfn:
    def __init__(self, w):
        self.w = w

    def describe_stacks(self, StackName):
        assert StackName == "kb-verifier-stack"
        return {"Stacks": [{"StackStatus": self.w.stack_status,
                            "Parameters": [{"ParameterKey": "Power", "ParameterValue": self.w.power},
                                           {"ParameterKey": "InstanceType", "ParameterValue": "t4g.small"}]}]}

    def update_stack(self, StackName, UsePreviousTemplate, Capabilities, Parameters):
        assert UsePreviousTemplate and Capabilities == ["CAPABILITY_IAM"]
        self.w.update_params = Parameters
        new = [p["ParameterValue"] for p in Parameters if p["ParameterKey"] == "Power"][0]
        self.w.calls.append("stack:" + new)
        self.w.stack_status = "UPDATE_IN_PROGRESS"
        self.w.power = new


class FakeSSM:
    def __init__(self, w):
        self.w = w

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online"}] if self.w.server == "running" else []}

    def send_command(self, InstanceIds, DocumentName, Parameters):
        assert DocumentName == "AWS-RunShellScript" and Parameters == {"commands": ["/opt/app/backup.sh"]}
        self.w.calls.append("backup")
        return {"Command": {"CommandId": "c-1"}}

    def get_command_invocation(self, CommandId, InstanceId):
        return {"Status": self.w.backup_result}


class FakeS3:
    def __init__(self, w):
        self.w = w

    def put_object(self, Bucket, Key, Body, **kw):
        assert kw.get("ServerSideEncryption") == "AES256"
        self.w.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.w.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.w.objects[Key])}


def load(w):
    clients = {"ec2": FakeEC2(w), "cloudformation": FakeCfn(w), "ssm": FakeSSM(w), "s3": FakeS3(w)}
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name: clients[name]
    sys.modules["boto3"] = boto3
    os.environ.update({"PROJECT": "kb-verifier", "STACK": "kb-verifier-stack", "DATA_BUCKET": "data"})
    spec = importlib.util.spec_from_file_location("power_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time = types.SimpleNamespace(time=lambda: w.now, sleep=lambda s: setattr(w, "now", w.now + s))
    return mod


class PowerTests(unittest.TestCase):
    def run_action(self, w, action):
        return load(w).handler({"action": action}, None)

    def test_fits_the_inline_limit(self):
        self.assertLess(len(SRC.read_bytes()), 4096)

    def test_status_reports_each_level(self):
        for server, level in (("running", "running"), ("pending", "starting"), ("stopping", "stopping"), ("stopped", "stopped")):
            self.assertEqual(self.run_action(World(server=server), "status")["level"], level)
        self.assertEqual(self.run_action(World(power="zero", server=None), "status")["level"], "zero")
        self.assertEqual(self.run_action(World(stack_status="UPDATE_IN_PROGRESS"), "status")["level"], "changing")

    def test_off_stops_and_on_starts(self):
        w = World(server="running")
        r = self.run_action(w, "off")
        self.assertEqual((r["ok"], w.calls), (True, ["stop"]))
        w.server = "stopped"
        r = self.run_action(w, "on")
        self.assertEqual((r["ok"], r["level"], w.calls), (True, "running", ["stop", "start"]))

    def test_zero_backs_up_first_then_removes_the_server(self):
        w = World(server="running")
        r = self.run_action(w, "zero")
        self.assertEqual(w.calls, ["backup", "stack:zero"])            # never remove before the backup succeeded
        self.assertEqual((r["ok"], r["level"]), (True, "changing"))
        kept = {p["ParameterKey"]: p for p in w.update_params}
        self.assertEqual(kept["InstanceType"], {"ParameterKey": "InstanceType", "UsePreviousValue": True})   # nothing else changes

    def test_zero_from_stopped_wakes_the_server_for_the_backup(self):
        w = World(server="stopped")
        self.run_action(w, "zero")
        self.assertEqual(w.calls, ["start", "backup", "stack:zero"])

    def test_a_failed_backup_removes_nothing_and_is_reported(self):
        w = World(server="running"); w.backup_result = "Failed"
        r = self.run_action(w, "zero")
        self.assertFalse(r["ok"]); self.assertIn("backup failed", r["detail"])
        self.assertEqual(w.calls, ["backup"]); self.assertEqual(w.power, "on")
        self.assertIn("backup failed", self.run_action(w, "status")["detail"])

    def test_on_from_zero_brings_the_server_back(self):
        w = World(power="zero", server=None, stack_status="UPDATE_COMPLETE")
        r = self.run_action(w, "on")
        self.assertEqual((r["ok"], w.calls, w.power), (True, ["stack:on"], "on"))

    def test_nothing_happens_while_a_change_is_in_progress_or_the_server_is_busy(self):
        w = World(stack_status="UPDATE_IN_PROGRESS")
        for a in ("on", "off", "zero"):
            self.assertFalse(self.run_action(w, a)["ok"])
        w = World(server="pending")
        self.assertFalse(self.run_action(w, "off")["ok"])
        self.assertEqual(w.calls, [])

    def test_unknown_actions_are_refused(self):
        w = World()
        self.assertEqual(self.run_action(w, "explode"), {"ok": False, "detail": "Unknown action"})
        self.assertEqual(w.calls, [])

    def test_repeating_a_level_is_harmless(self):
        w = World(power="zero", server=None)
        self.assertTrue(self.run_action(w, "zero")["ok"]); self.assertEqual(w.calls, [])
        w = World(server="stopped")
        self.assertTrue(self.run_action(w, "off")["ok"]); self.assertEqual(w.calls, [])


if __name__ == "__main__":
    unittest.main()
