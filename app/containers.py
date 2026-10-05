"""Which container tool runs the local stack and builds the image: Docker Desktop or Rancher Desktop.

Set it per machine in config.local.yaml (section `containers`, see docs/CONTAINERS.md):
  * docker-desktop                          -> docker / docker compose / docker buildx
  * rancher-desktop, rancher_runtime: moby  -> the same docker commands (Rancher Desktop ships docker, compose, buildx)
  * rancher-desktop, rancher_runtime: containerd -> nerdctl / nerdctl compose / nerdctl build + push
GPU for the local AI model: Docker Desktop with an NVIDIA GPU (WSL 2 on Windows). Rancher Desktop has no supported GPU
passthrough on Windows, so `gpu: auto` runs the model on the CPU there.

Pure functions that build argument lists; scripts/containers.py and deploy/deploy.py run them.
"""
from __future__ import annotations

import shutil

ENGINES = ("docker-desktop", "rancher-desktop")
RANCHER_RUNTIMES = ("moby", "containerd")
GPU_MODES = ("auto", "on", "off")
COMPOSE_FILE = "docker-compose.yml"
GPU_COMPOSE_FILE = "docker-compose.gpu.yml"


def cli(c) -> str:
    """The container command: docker, or nerdctl for Rancher Desktop's containerd runtime."""
    return "nerdctl" if c.engine == "rancher-desktop" and c.rancher_runtime == "containerd" else "docker"


def gpu_enabled(c, has_nvidia: bool | None = None) -> bool:
    if c.gpu == "off":
        return False
    if c.gpu == "on":
        return True
    if c.engine != "docker-desktop":
        return False                          # auto: only Docker Desktop passes an NVIDIA GPU through on Windows
    return bool(shutil.which("nvidia-smi")) if has_nvidia is None else has_nvidia


def compose(c, *args: str, has_nvidia: bool | None = None) -> list[str]:
    """`<cli> compose -f docker-compose.yml [-f docker-compose.gpu.yml] <args>`. The files are always named, so a
    COMPOSE_FILE value in .env (Docker Desktop only) can't disagree with this setting."""
    files = ["-f", COMPOSE_FILE] + (["-f", GPU_COMPOSE_FILE] if gpu_enabled(c, has_nvidia) else [])
    return [cli(c), "compose", *files, *args]


def build_image_file(c, image: str, dest: str) -> list[list[str]]:
    """Build the server image for arm64 (the AWS server is Graviton) into a file (`docker load` format). The deploy puts
    the file in S3 for the server, so no image registry is needed. The context is filtered by .dockerignore."""
    if cli(c) == "nerdctl":
        return [["nerdctl", "build", "--platform", "linux/arm64", "-t", image, "."],
                ["nerdctl", "save", "--platform", "linux/arm64", "-o", dest, image]]
    return [["docker", "buildx", "build", "--platform", "linux/arm64", "-t", image, "--output", f"type=docker,dest={dest}", "."]]


def tools_needed(c, *, deploying: bool = False) -> list[tuple[str, list[str], str]]:
    """(name, version command, why) for each external tool this setting needs."""
    x = cli(c)
    need = [(x, [x, "--version"], "runs the containers"),
            (x + " compose", [x, "compose", "version"], "starts the app, database and AI model together"),
            (x + " engine", [x, "info", "--format", "{{.ServerVersion}}"], "the engine must be running (start " + ("Docker Desktop" if c.engine == "docker-desktop" else "Rancher Desktop") + ")")]
    if deploying:
        need.append((x + " build", [x, "buildx", "version"] if x == "docker" else [x, "build", "--help"], "builds the arm64 server image"))
    return need
