"""The containers setting: Docker Desktop or Rancher Desktop (moby or containerd), and the commands built from it."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import containers as ct  # noqa: E402
from app.config import Config, ConfigError, Containers, load_config  # noqa: E402
from deploy import deploy as d  # noqa: E402

DOCKER = Containers("docker-desktop", "moby", "auto")
RANCHER_MOBY = Containers("rancher-desktop", "moby", "auto")
RANCHER_CONTAINERD = Containers("rancher-desktop", "containerd", "auto")


class ContainerCommandTests(unittest.TestCase):
    def test_docker_desktop_and_rancher_moby_use_docker_containerd_uses_nerdctl(self):
        self.assertEqual([ct.cli(c) for c in (DOCKER, RANCHER_MOBY, RANCHER_CONTAINERD)], ["docker", "docker", "nerdctl"])
        self.assertEqual(ct.compose(RANCHER_CONTAINERD, "up", "-d")[:3], ["nerdctl", "compose", "-f"])

    def test_compose_always_names_its_files(self):
        self.assertEqual(ct.compose(DOCKER, "ps", has_nvidia=False), ["docker", "compose", "-f", "docker-compose.yml", "ps"])
        self.assertEqual(ct.compose(DOCKER, "ps", has_nvidia=True),
                         ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml", "ps"])

    def test_gpu_auto_only_on_docker_desktop_with_an_nvidia_driver(self):
        self.assertTrue(ct.gpu_enabled(DOCKER, has_nvidia=True))
        self.assertFalse(ct.gpu_enabled(DOCKER, has_nvidia=False))
        self.assertFalse(ct.gpu_enabled(RANCHER_MOBY, has_nvidia=True))       # no supported passthrough on Windows
        self.assertTrue(ct.gpu_enabled(Containers("rancher-desktop", "moby", "on"), has_nvidia=False))   # forced
        self.assertFalse(ct.gpu_enabled(Containers("docker-desktop", "moby", "off"), has_nvidia=True))

    def test_image_build_and_push_for_arm64_with_either_tool(self):
        self.assertEqual(ct.build_and_push(DOCKER, "r:t"), [["docker", "buildx", "build", "--platform", "linux/arm64", "-t", "r:t", "--push", "."]])
        build, push = ct.build_and_push(RANCHER_CONTAINERD, "r:t")
        self.assertEqual((build[0], build[-1], push[:2]), ("nerdctl", ".", ["nerdctl", "push"]))
        self.assertIn("linux/arm64", build); self.assertIn("linux/arm64", push)

    def test_registry_login_reads_the_token_from_stdin(self):
        for c in (DOCKER, RANCHER_CONTAINERD):
            self.assertIn("--password-stdin", ct.registry_login(c, "123.dkr.ecr.us-east-1.amazonaws.com"))

    def test_deploy_uses_the_chosen_tool(self):
        cfg = Config(); cfg.containers = RANCHER_CONTAINERD
        cmds = d.cmds_build_push("repo", "latest", cfg)
        self.assertEqual([c[0] for c in cmds], ["nerdctl", "nerdctl"])
        self.assertEqual(d.cmd_docker_login("reg", cfg)[0], "nerdctl")
        self.assertEqual(d.cmd_build_push("repo", "latest")[0], "docker")      # default: Docker Desktop

    def test_check_lists_the_tools_for_each_setting(self):
        names = [n for n, _, _ in ct.tools_needed(RANCHER_CONTAINERD, deploying=True)]
        self.assertEqual(names, ["nerdctl", "nerdctl compose", "nerdctl engine", "nerdctl build"])
        self.assertNotIn("docker build", [n for n, _, _ in ct.tools_needed(DOCKER)])


class ContainerSettingTests(unittest.TestCase):
    def load(self, text):
        d_ = Path(tempfile.mkdtemp())
        (d_ / "c.yaml").write_text("project_name: x\n" + text)
        return load_config(d_ / "c.yaml", d_ / "none.yaml", require_secrets=False, env={})

    def test_default_is_docker_desktop(self):
        self.assertEqual(load_config("config.yaml", "nope.yaml", require_secrets=False, env={}).containers, DOCKER)

    def test_rancher_desktop_settings_load(self):
        c = self.load("containers:\n  engine: rancher-desktop\n  rancher_runtime: containerd\n  gpu: off\n").containers
        self.assertEqual((c.engine, c.rancher_runtime, c.gpu), ("rancher-desktop", "containerd", "off"))

    def test_bad_values_are_refused(self):
        for bad in ("engine: podman", "rancher_runtime: crio", "gpu: maybe"):
            with self.assertRaises(ConfigError, msg=bad):
                self.load("containers:\n  " + bad + "\n")


class ContainersScriptTests(unittest.TestCase):
    def run_script(self, *args):
        sys.path.insert(0, str(ROOT / "scripts"))
        import containers as script
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = script.main(list(args))
        return code, out.getvalue()

    def test_dry_run_prints_the_command_for_the_configured_tool(self):
        code, out = self.run_script("exec", "api", "python", "-m", "app.cli", "list-users", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("compose -f docker-compose.yml", out); self.assertIn("exec api python -m app.cli list-users", out)
        code, out = self.run_script("model", "qwen3:4b", "--dry-run")
        self.assertIn("exec ollama ollama pull qwen3:4b", out)

    def test_only_the_control_center_starts_or_stops_it(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTROL_CENTER_LAUNCH", None)
            for cmd in ("up", "down", "stop", "start", "restart"):
                with mock.patch("subprocess.call", side_effect=AssertionError("must not run")):
                    code, out = self.run_script(cmd)
                self.assertEqual(code, 1, cmd)
                self.assertIn("only from the Control Center", out)
            os.environ["CONTROL_CENTER_LAUNCH"] = "control-center"          # what the Control Center's adapter sets
            self.assertEqual(self.run_script("up", "--dry-run")[0], 0)
        self.assertNotIn("CONTROL_CENTER_LAUNCH", os.environ)               # nothing leaks into the caller

    def test_the_compose_file_refuses_to_run_without_the_control_center(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("${CONTROL_CENTER_LAUNCH:?", compose)
        self.assertNotIn("unless-stopped", compose)                          # no coming back by itself after a reboot
        self.assertIn("APP_SERVICE_SWITCH_MODE: control-center", compose)

    def test_bad_arguments_are_explained(self):
        self.assertEqual(self.run_script("exec")[0], 2)
        with self.assertRaises(SystemExit):
            self.run_script("service", "on")                                 # gone: the Control Center is the switch

if __name__ == "__main__":
    unittest.main()
