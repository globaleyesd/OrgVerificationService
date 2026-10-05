import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from deploy import deploy as d  # noqa: E402


def cfg_with_keys():
    c = Config()
    c.secrets.aws.access_key_id = "AKIAFAKEKEYFORTESTING"
    c.secrets.aws.secret_access_key = "fake/secret/for/testing/only"
    c.secrets.aws.session_token = "fake-session-token"
    return c


class UploadSafetyTests(unittest.TestCase):
    def test_allow_list_files_pass(self):
        for name in d.UPLOAD_ALLOWLIST:
            d.assert_safe_upload(name)

    def test_credential_files_always_refused(self):
        for bad in ("secrets.local.yaml", "creds/service_switch.json", ".env", "data/db_password", "db_password",
                    "../secrets.local.yaml", "server.pem", "deploy/params.local.json", ".aws/credentials",
                    "config.local.yaml.bak", "config.aws.local.yaml.bak", "docker-compose.yml"):
            with self.assertRaises(d.DeployError, msg=bad):
                d.assert_safe_upload(bad)

    def test_upload_command_builder_enforces_it(self):
        with self.assertRaises(d.DeployError):
            d.cmd_upload_config("bucket", "creds/service_switch.json")
        cmd = d.cmd_upload_config("bucket", "config.yaml")
        self.assertEqual(cmd[-1], "s3://bucket/deploy/config.yaml")

    def test_server_overrides_go_up_as_the_servers_config_local(self):
        cmd = d.cmd_upload_config("bucket", "config.aws.local.yaml", as_name="config.local.yaml")
        self.assertEqual(cmd[-2:], ["config.aws.local.yaml", "s3://bucket/deploy/config.local.yaml"])
        with self.assertRaises(d.DeployError):
            d.cmd_upload_config("bucket", "secrets.local.yaml", as_name="config.local.yaml")

    def test_the_local_model_is_refused_for_aws(self):
        c = Config()
        c.llm.provider = "local"
        with self.assertRaises(d.DeployError):
            d.check_deployable(c)
        c.llm.provider = "anthropic"
        d.check_deployable(c)


class OriginSecretTests(unittest.TestCase):
    def test_the_origin_secret_is_a_parameter_but_never_printed(self):
        import contextlib
        import io
        params = d.stack_parameters(Config(), {"AmiId": "ami-1", "CloudFrontPrefixListId": "pl-1", "OriginVerifySecret": "s" * 43})
        cmd = d.cmd_stack(Config(), params)
        self.assertIn("OriginVerifySecret=" + "s" * 43, cmd)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            d.Runner({}, dry_run=True).run(cmd)
        self.assertNotIn("s" * 43, out.getvalue())
        self.assertIn("OriginVerifySecret=****", out.getvalue())

    def test_it_is_required(self):
        with self.assertRaises(d.DeployError):
            d.stack_parameters(Config(), {"AmiId": "ami-1", "CloudFrontPrefixListId": "pl-1"})


class BranchTests(unittest.TestCase):
    def test_aws_changes_only_from_the_prod_branch(self):
        for step in ("all", "stack", "image", "ui", "config", "update"):
            for branch in ("dev", "main", "feature-x", None):
                with self.assertRaises(d.DeployError, msg=(step, branch)):
                    d.check_branch(step, False, branch)
            d.check_branch(step, False, "prod")

    def test_previews_and_read_only_steps_run_anywhere(self):
        d.check_branch("all", True, "dev")
        d.check_branch("outputs", False, "dev")
        d.check_branch("cost-sheet", False, None)

    def test_main_stops_before_any_aws_command_on_dev(self):
        import contextlib
        import io
        from unittest import mock
        out = io.StringIO()
        with mock.patch.object(d, "current_branch", return_value="dev"), \
                mock.patch.object(d.subprocess, "run", side_effect=AssertionError("no command may run")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(d.main(["stack"]), 1)
        self.assertIn("only on the prod branch", out.getvalue())


class CommandTests(unittest.TestCase):
    def test_aws_keys_never_appear_in_any_command(self):
        c = cfg_with_keys()
        params = d.stack_parameters(c, {"AmiId": "ami-123", "CloudFrontPrefixListId": "pl-123", "OriginVerifySecret": "s" * 43})
        cmds = [d.cmd_stack(c, params), d.cmd_outputs(c), d.cmd_ecr_password("us-east-1"), d.cmd_docker_login("reg"),
                d.cmd_build_push("repo", "latest"), d.cmd_sync_ui("b"), d.cmd_upload_config("b", "config.yaml"),
                d.cmd_update_server("i-1"), d.cmd_lookup_ami(), d.cmd_lookup_prefix_list()]
        flat = " ".join(" ".join(x) for x in cmds)
        for secret in ("AKIAFAKEKEYFORTESTING", "fake/secret/for/testing/only", "fake-session-token"):
            self.assertNotIn(secret, flat)

    def test_keys_go_only_through_the_environment(self):
        env = d.aws_env(cfg_with_keys())
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIAFAKEKEYFORTESTING")
        self.assertEqual(env["AWS_DEFAULT_REGION"], "us-east-1")

    def test_profile_used_when_no_keys(self):
        c = Config(); c.secrets.aws.profile = "myprofile"
        self.assertEqual(d.aws_env(c)["AWS_PROFILE"], "myprofile")

    def test_commands_are_argument_lists_not_shell_strings(self):
        for cmd in (d.cmd_sync_ui("b"), d.cmd_update_server("i-1"), d.cmd_build_push("r", "t")):
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(x, str) for x in cmd))

    def test_image_is_built_for_arm_and_pushed(self):
        cmd = d.cmd_build_push("repo", "latest")
        self.assertIn("linux/arm64", cmd)
        self.assertEqual(cmd[-1], ".")

    def test_server_command_is_the_fixed_script(self):
        self.assertIn("commands=/opt/app/update.sh", d.cmd_update_server("i-1"))

    def test_stack_needs_image_and_prefix_list_ids(self):
        with self.assertRaises(d.DeployError):
            d.stack_parameters(Config(), {})

    def test_bedrock_permission_only_when_chosen(self):
        c = Config()
        extra = {"AmiId": "ami-1", "CloudFrontPrefixListId": "pl-1", "OriginVerifySecret": "s" * 43}
        self.assertEqual(d.stack_parameters(c, extra)["AllowBedrock"], "false")
        c.llm.provider = "bedrock"
        self.assertEqual(d.stack_parameters(c, extra)["AllowBedrock"], "true")

    def test_schedule_and_cost_settings_flow_into_the_stack(self):
        c = Config(); c.deployment.schedule.enabled = True; c.deployment.instance_type = "t4g.micro"
        p = d.stack_parameters(c, {"AmiId": "ami-1", "CloudFrontPrefixListId": "pl-1", "OriginVerifySecret": "s" * 43})
        self.assertEqual((p["ScheduleEnabled"], p["InstanceType"]), ("true", "t4g.micro"))


class CostSheetTests(unittest.TestCase):
    def test_lists_every_billed_service_with_its_levels(self):
        sheet = d.aws_cost_sheet(Config())
        by = {s["name"].split(" (")[0]: s for s in sheet["services"]}
        self.assertEqual(by["EC2 server"]["billed_at"], ["running"])
        self.assertEqual(by["Elastic IP"]["billed_at"], ["running", "stopped"])
        self.assertEqual(by["S3 storage"]["billed_at"], ["running", "stopped", "zero"])
        self.assertAlmostEqual(by["EC2 server"]["monthly_usd"], round(0.0168 * 730, 2))
        self.assertTrue(all(s["monthly_usd"] is None or s["monthly_usd"] >= 0.01 for s in sheet["services"]))   # a cent or more

    def test_ai_is_always_listed_as_usage_based(self):
        c = Config()
        ai = [s for s in d.aws_cost_sheet(c)["services"] if s["name"].startswith("AI answers")]
        self.assertEqual(len(ai), 1); self.assertIsNone(ai[0]["monthly_usd"]); self.assertIn("per question", ai[0]["usage"])
        c.llm.provider = "local"                                    # on AWS that becomes Bedrock: still listed, unpriced
        ai = [s for s in d.aws_cost_sheet(c)["services"] if s["name"].startswith("AI answers")]
        self.assertEqual((ai[0]["name"], ai[0]["monthly_usd"]), ("AI answers (Amazon Bedrock)", None))


class DocsAndTasksMatchTheScriptTests(unittest.TestCase):
    def _steps_used(self, text):
        import re
        return set(re.findall(r"deploy/deploy\.py ([a-z]+)", text))

    def test_every_step_in_commands_doc_exists(self):
        text = (ROOT / "docs" / "COMMANDS.md").read_text()
        used = self._steps_used(text)
        self.assertTrue(used)
        self.assertTrue(used <= set(d.STEPS) | {"all"}, used - set(d.STEPS))

    def test_every_vscode_task_uses_a_real_step(self):
        import json
        tasks = json.loads((ROOT / ".vscode" / "tasks.json").read_text())["tasks"]
        used = set()
        for t in tasks:
            used |= self._steps_used(t["command"])
        self.assertTrue(used <= set(d.STEPS) | {"all"}, used - set(d.STEPS))

    def test_readme_files_point_to_the_commands_doc(self):
        self.assertIn("docs/COMMANDS.md", (ROOT / "README.md").read_text())
        self.assertIn("COMMANDS.md", (ROOT / "deploy" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
