"""Run the local stack with the container tool chosen in config (containers.engine): Docker Desktop or Rancher Desktop.

    python scripts/containers.py check [--deploy]   # what is installed, what is missing, how to install it
    python scripts/containers.py up [--build]       # start everything (app, database, AI model)
    python scripts/containers.py down               # stop and remove the containers (data and models are kept)
    python scripts/containers.py stop|start|restart [service]
    python scripts/containers.py ps | logs [service] [-f]
    python scripts/containers.py exec <service> <command...>
    python scripts/containers.py service on|off|status     # the app's own switch (asks for the switch password)
    python scripts/containers.py model <name>               # download a model for the local AI, e.g. qwen3:4b
    add --dry-run to print the command instead of running it

Standard library plus PyYAML (requirements-dev.txt). See docs/CONTAINERS.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import containers as ct  # noqa: E402
from app.config import ConfigError, load_config  # noqa: E402

INSTALL = {   # Windows commands (winget comes with Windows 11); other systems: the linked download pages
    "docker": "winget install -e --id Docker.DockerDesktop   (https://www.docker.com/products/docker-desktop/)",
    "rancher": "winget install -e --id SUSE.RancherDesktop   (https://rancherdesktop.io/), then pick the engine in its Preferences > Container Engine",
    "aws": "winget install -e --id Amazon.AWSCLI   (https://aws.amazon.com/cli/)",
    "node": "winget install -e --id OpenJS.NodeJS.LTS   (https://nodejs.org/)",
    "python": "winget install -e --id Python.Python.3.12   (https://www.python.org/downloads/)",
    "pip": "pip install -r requirements-dev.txt",
    "nvidia": "Install or update the NVIDIA driver with the NVIDIA app (https://www.nvidia.com/en-us/software/nvidia-app/)",
}


def run(cmd: list[str], dry: bool) -> int:
    print("$ " + " ".join(shlex.quote(c) for c in cmd))
    if dry:
        return 0
    if not shutil.which(cmd[0]):
        print(f"'{cmd[0]}' was not found. Run: python scripts/containers.py check")
        return 1
    return subprocess.call(cmd, cwd=ROOT)


def probe(cmd: list[str]) -> tuple[bool, str]:
    """Run a version/info command quietly: (worked, first line of output)."""
    if not shutil.which(cmd[0]):
        return False, "not found"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, type(e).__name__
    line = next((x.strip() for x in (r.stdout or r.stderr or "").splitlines() if x.strip()), "")
    return r.returncode == 0, line[:90]


def check(c, deploying: bool) -> int:
    rows, missing = [], 0

    def row(ok: bool, name: str, info: str, fix: str = "", required: bool = True):
        nonlocal missing
        mark = "OK  " if ok else ("MISS" if required else "opt ")
        if not ok and required:
            missing += 1
        rows.append((mark, name, info, "" if ok else fix))

    v = sys.version_info
    row(v >= (3, 12), "Python 3.12+", f"{v.major}.{v.minor}.{v.micro}", INSTALL["python"])
    row(importlib.util.find_spec("yaml") is not None, "PyYAML", "scripts and tests", INSTALL["pip"])
    row(importlib.util.find_spec("cfnlint") is not None, "cfn-lint", "checks the AWS template", INSTALL["pip"], required=deploying)
    engine_fix = INSTALL["docker"] if c.engine == "docker-desktop" else INSTALL["rancher"]
    for name, cmd, why in ct.tools_needed(c, deploying=deploying):
        ok, info = probe(cmd)
        row(ok, name, info if ok else why, engine_fix)
    ok, info = probe(["aws", "--version"])
    row(ok, "AWS CLI v2", info if ok else "deploys to AWS", INSTALL["aws"], required=deploying)
    ok, info = probe(["node", "--version"])
    row(ok, "Node.js", info if ok else "a few tests run the page and CloudFront code", INSTALL["node"], required=False)
    gpu = ct.gpu_enabled(c)
    ok, info = probe(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    row(ok, "NVIDIA driver", info if ok else "GPU for the local AI model", INSTALL["nvidia"], required=c.gpu == "on")
    print(f"Engine: {c.engine}" + (f" ({c.rancher_runtime})" if c.engine == "rancher-desktop" else "") +
          f", command: {ct.cli(c)}, AI model on the {'GPU' if gpu else 'CPU'}\n")
    for mark, name, info, fix in rows:
        print(f"  [{mark}] {name:<18} {info}")
        if fix:
            print(f"         {'':<18} -> {fix}")
    if c.engine == "rancher-desktop":
        print("\n  Rancher Desktop: choose the same engine in Preferences > Container Engine as containers.rancher_runtime"
              f" ({c.rancher_runtime}). Its GPU passthrough is not supported on Windows, so the AI model runs on the CPU.")
    print("\nAll required tools are present." if not missing else f"\n{missing} required item(s) missing (see -> lines).")
    return 1 if missing else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the local stack with Docker Desktop or Rancher Desktop")
    ap.add_argument("command", choices=["check", "up", "down", "stop", "start", "restart", "ps", "logs", "exec", "service", "model"])
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--build", action="store_true", help="up: rebuild the app image first")
    ap.add_argument("--deploy", action="store_true", help="check: also the tools needed to deploy to AWS")
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    try:
        c = load_config(require_secrets=False).containers
    except ConfigError as e:
        print(f"Error: {e}")
        return 1
    rest = [x for x in a.rest if x != "--dry-run"]
    dry = a.dry_run or "--dry-run" in a.rest
    if a.command == "check":
        return check(c, a.deploy or "--deploy" in rest)
    if a.command == "up":
        return run(ct.compose(c, "up", "-d", *(["--build"] if a.build or "--build" in rest else [])), dry)
    if a.command in ("down", "stop", "start", "restart", "ps", "logs"):
        return run(ct.compose(c, a.command, *rest), dry)
    if a.command == "exec":
        if not rest:
            print("exec needs a service and a command, e.g. exec api python -m app.cli list-users")
            return 2
        return run(ct.compose(c, "exec", *rest), dry)
    if a.command == "service":
        if rest not in (["on"], ["off"], ["status"]):
            print("service needs on, off or status")
            return 2
        return run(ct.compose(c, "exec", "api", "python", "-m", "app.cli", "service", rest[0]), dry)
    if a.command == "model":
        if len(rest) != 1:
            print("model needs one name, e.g. qwen3:4b")
            return 2
        return run(ct.compose(c, "exec", "ollama", "ollama", "pull", rest[0]), dry)
    return 2


if __name__ == "__main__":
    sys.exit(main())
