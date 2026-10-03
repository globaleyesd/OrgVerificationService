# Containers: Docker Desktop or Rancher Desktop

The local stack (app, database, local AI model) and the AWS image build run with either **Docker Desktop** or
**Rancher Desktop**. Pick one per machine in `config.local.yaml`:

```yaml
containers:
  engine: rancher-desktop      # docker-desktop (default) | rancher-desktop
  rancher_runtime: moby        # rancher-desktop only: moby | containerd
  gpu: auto                    # auto | on | off
```

| Setting | Commands used | Notes |
|---|---|---|
| `docker-desktop` | `docker`, `docker compose`, `docker buildx` | NVIDIA GPU works (WSL 2 backend on Windows) |
| `rancher-desktop` + `moby` | the same `docker` commands (Rancher Desktop ships them) | Set **Preferences > Container Engine > dockerd (moby)** in Rancher Desktop |
| `rancher-desktop` + `containerd` | `nerdctl`, `nerdctl compose`, `nerdctl build` + `nerdctl push` | Set **Preferences > Container Engine > containerd** |

`gpu: auto` uses the NVIDIA GPU on Docker Desktop when `nvidia-smi` exists, and the CPU on Rancher Desktop (it has no
supported GPU passthrough on Windows). On the CPU the local AI model still works, but each answer takes much longer.

## Run everything through one command
`scripts/containers.py` reads the setting and runs the right command, so you never type `docker` or `nerdctl` yourself:

| Job | Command |
|---|---|
| Check what is installed and what is missing | `python scripts/containers.py check` (add `--deploy` for the AWS tools too) |
| Start everything | `python scripts/containers.py up` (`--build` after code changes) |
| Stop and remove the containers (data and models are kept) | `python scripts/containers.py down` |
| Stop / start / restart without removing | `python scripts/containers.py stop` / `start` / `restart [service]` |
| What is running, logs | `python scripts/containers.py ps`, `python scripts/containers.py logs api -f` |
| Turn the app's switch on or off (asks for the switch password) | `python scripts/containers.py service on` / `off` / `status` |
| Download a model for the local AI | `python scripts/containers.py model qwen3:4b` |
| Any other command in a container | `python scripts/containers.py exec api python -m app.cli list-users` |

Add `--dry-run` to see the command without running it. The deploy script (`python deploy/deploy.py image`) builds and
pushes the server image with the same tool.

## Dependencies
`python scripts/containers.py check --deploy` checks all of these and prints how to install anything missing.

| Needed for | Tool | Install (Windows) |
|---|---|---|
| Everything | **Python 3.12+** | `winget install -e --id Python.Python.3.12` |
| Scripts and tests | **PyYAML**; **cfn-lint** (template checks, needed to deploy) | `pip install -r requirements-dev.txt` |
| Running locally | **Docker Desktop** *or* **Rancher Desktop**, with Compose (both include it) | `winget install -e --id Docker.DockerDesktop` or `winget install -e --id SUSE.RancherDesktop` |
| Building the AWS image | `docker buildx` (Docker Desktop, Rancher moby) or `nerdctl build` (Rancher containerd); both included | - |
| Deploying | **AWS CLI v2** (a recent version) | `winget install -e --id Amazon.AWSCLI` |
| Optional: a few tests | **Node.js** (runs the page and CloudFront code in tests) | `winget install -e --id OpenJS.NodeJS.LTS` |
| Optional: fast local AI | **NVIDIA driver** (recent; the GPU build needs CUDA 12.8+) | NVIDIA app |

The app's own Python packages (`requirements.txt`) are installed inside its image; you don't install them yourself.
The AI model is downloaded into the `ollama` volume with `python scripts/containers.py model qwen3:4b`.

## Switching between them
Both keep their own containers, images and volumes, so after switching:
1. `python scripts/containers.py check`
2. `python scripts/containers.py up --build` (builds the app image in the new engine)
3. `python scripts/containers.py model qwen3:4b` (the model volume is per engine)
4. Your documents in the database are per engine too. Re-add them, or move them with the database backup:
   `python scripts/containers.py exec -T db pg_dump -U app -d app > backup.sql` in the old engine, and
   `python scripts/containers.py exec -T db psql -U app -d app < backup.sql` in the new one.

## Known differences (check on your first run with Rancher Desktop + containerd)
- `nerdctl compose` may not wait for the database's health check before starting the app. If the app reports a
  database error on its first start, run `python scripts/containers.py restart api`.
- Images from Docker Hub: if your network blocks Docker Hub's download servers (as on the first setup of this project),
  pull through the mirror: `nerdctl pull mirror.gcr.io/ollama/ollama:latest` then `nerdctl tag ... ollama/ollama:latest`
  (or the same with `docker`).
- The `.env` file's `COMPOSE_FILE` line is read only by `docker compose`. The script names the files itself, so it works
  with either tool.
