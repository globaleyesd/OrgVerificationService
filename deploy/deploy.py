"""One command line for every deploy step, so VS Code tasks (and you) run the same thing.

    python deploy/deploy.py all            # stack -> image -> page -> config -> update
    python deploy/deploy.py stack|image|ui|config|update|outputs
    python deploy/deploy.py cost-sheet     # writes deploy/aws-costs.json (what each AWS service costs; read by the Control Center)
    add --dry-run to print the commands without running them

Branches: work on dev (runs locally with Docker Compose), merge into prod to deploy. Steps that change AWS run only
on the prod branch; --dry-run, outputs and cost-sheet run anywhere.

Security rules this script follows (and tests/test_deploy.py checks):
  * It only uploads a fixed allow-list of NON-secret files. creds/, secrets*, keys, .env and data/ can never be sent.
  * AWS credentials from secrets.local.yaml (if you use them) are handed to the aws/docker commands through
    their environment only: never written to disk, never printed, never put in CloudFormation.
  * The container image is built from a context that .dockerignore keeps free of secrets.
Needs the aws CLI and Docker installed. Nothing here has been run against a real AWS account by its author:
read deploy/README.md and use --dry-run first.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config, ConfigError, load_config   # noqa: E402

TEMPLATE = "deploy/cloudformation/stack.yaml"
PARAMS_FILE = ROOT / "deploy" / "params.local.json"       # git-ignored: e-mail address, cached image ids
UI_DIR = "app/web"

# The ONLY local files that may ever be uploaded to AWS (to <data bucket>/deploy/).
UPLOAD_ALLOWLIST = ("docker-compose.aws.yml", "config.yaml", "config.local.yaml", "config.aws.local.yaml")
# The server's own overrides (git-ignored). When present it is uploaded AS the server's config.local.yaml, so this
# machine's config.local.yaml (for example the local model) stays here.
AWS_LOCAL_CONFIG = "config.aws.local.yaml"
FORBIDDEN_FOLDERS = {"creds", "data", ".aws", ".vscode"}
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12")


class DeployError(Exception):
    pass


def assert_safe_upload(path: str) -> None:
    p = Path(path)
    if p.as_posix() not in UPLOAD_ALLOWLIST:
        raise DeployError(f"Refusing to upload '{path}': not on the allow-list")
    name = p.name.lower()
    if (set(p.parts) & FORBIDDEN_FOLDERS or name.startswith("secrets") or name.startswith(".env")
            or name.endswith(FORBIDDEN_SUFFIXES) or name == "db_password" or "params.local" in name):
        raise DeployError(f"Refusing to upload '{path}': looks like a credential file")


# ---------------------------------------------------------------- command builders (pure, tested)
def stack_name(cfg: Config) -> str:
    return cfg.project_name


def stack_parameters(cfg: Config, extra: dict) -> dict:
    d, s = cfg.deployment, cfg.deployment.schedule
    p = {
        "ProjectName": cfg.project_name,
        "InstanceType": d.instance_type,
        "RootVolumeGb": str(d.root_volume_gb),
        "AllowBedrock": "true" if cfg.llm.provider == "bedrock" else "false",
        "BudgetLimitUsd": str(d.budget_limit_usd),
        "ScheduleEnabled": "true" if s.enabled else "false",
        "ScheduleTimezone": s.timezone,
        "StartHour": str(s.start_hour),
        "StopHour": str(s.stop_hour),
    }
    for key in ("AmiId", "CloudFrontPrefixListId", "BudgetEmail", "OriginVerifySecret"):
        if extra.get(key):
            p[key] = extra[key]
    missing = [k for k in ("AmiId", "CloudFrontPrefixListId", "OriginVerifySecret") if k not in p]
    if missing:
        raise DeployError("Missing stack parameter(s): " + ", ".join(missing))
    return p


def cmd_stack(cfg: Config, params: dict) -> list[str]:
    return ["aws", "cloudformation", "deploy", "--template-file", TEMPLATE, "--stack-name", stack_name(cfg),
            "--capabilities", "CAPABILITY_IAM", "--no-fail-on-empty-changeset",
            "--parameter-overrides", *[f"{k}={v}" for k, v in sorted(params.items())]]


def cmd_outputs(cfg: Config) -> list[str]:
    return ["aws", "cloudformation", "describe-stacks", "--stack-name", stack_name(cfg),
            "--query", "Stacks[0].Outputs", "--output", "json"]


def cmd_lookup_ami() -> list[str]:
    return ["aws", "ssm", "get-parameter", "--name", "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
            "--query", "Parameter.Value", "--output", "text"]


def cmd_lookup_prefix_list() -> list[str]:
    return ["aws", "ec2", "describe-managed-prefix-lists", "--filters",
            "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing",
            "--query", "PrefixLists[0].PrefixListId", "--output", "text"]


# COST: the server image is a file in the documents bucket (a few tenths of a cent a month), not in a paid registry.
IMAGE_NAME = "kb-verifier-server:latest"          # the name the server's compose file runs (update.sh loads the file)
IMAGE_KEY = "image/server-image.tar.gz"


def cmds_build_image(dest: str, cfg: Config | None = None) -> list[list[str]]:
    # arm64 because the server is a Graviton (t4g) instance. The build context is filtered by .dockerignore.
    from app.containers import build_image_file
    return build_image_file((cfg or Config()).containers, IMAGE_NAME, dest)


def cmd_upload_image(data_bucket: str, path: str) -> list[str]:
    return ["aws", "s3", "cp", path, f"s3://{data_bucket}/{IMAGE_KEY}", "--only-show-errors"]


def cmd_sync_ui(ui_bucket: str) -> list[str]:
    return ["aws", "s3", "sync", UI_DIR, f"s3://{ui_bucket}", "--delete", "--cache-control", "no-cache"]


def cmd_upload_config(data_bucket: str, filename: str, as_name: str | None = None) -> list[str]:
    assert_safe_upload(filename)
    return ["aws", "s3", "cp", filename, f"s3://{data_bucket}/deploy/{as_name or filename}"]


def server_local_config() -> str:
    """The file that becomes config.local.yaml on the server."""
    return AWS_LOCAL_CONFIG if (ROOT / AWS_LOCAL_CONFIG).exists() else "config.local.yaml"


def check_deployable(cfg: Config) -> None:
    if cfg.llm.provider == "local":
        raise DeployError("llm.provider is 'local' (a model run by Ollama), which the small AWS server can't run. "
                          f"Put the server's AI settings in {AWS_LOCAL_CONFIG} (see deploy/README.md).")


def cmd_update_server(instance_id: str) -> list[str]:
    # The command run on the server is a fixed script that contains no secrets.
    return ["aws", "ssm", "send-command", "--instance-ids", instance_id, "--document-name", "AWS-RunShellScript",
            "--comment", "Pull latest image and restart", "--parameters", "commands=/opt/app/update.sh"]


# ---------------------------------------------------------------- running
DEPLOY_BRANCH = "prod"
READ_ONLY_STEPS = ("outputs", "cost-sheet")


def current_branch() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def check_branch(step: str, dry_run: bool, branch: str | None) -> None:
    if dry_run or step in READ_ONLY_STEPS or branch == DEPLOY_BRANCH:
        return
    raise DeployError(f"Deploying to AWS runs only on the {DEPLOY_BRANCH} branch (this is '{branch or 'no branch'}'): "
                      f"merge your work into {DEPLOY_BRANCH}, then git checkout {DEPLOY_BRANCH}. "
                      "For local development use docker compose (docs/RUNNING_LOCALLY.md).")


def aws_env(cfg: Config) -> dict:
    env = dict(os.environ)
    a = cfg.secrets.aws
    if a.access_key_id and a.secret_access_key:
        env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"] = a.access_key_id, a.secret_access_key
        if a.session_token:
            env["AWS_SESSION_TOKEN"] = a.session_token
    elif a.profile:
        env["AWS_PROFILE"] = a.profile
    env["AWS_DEFAULT_REGION"] = cfg.deployment.region
    return env


SECRET_PARAMETERS = ("OriginVerifySecret",)


def redact(arg: str) -> str:
    """Commands are printed; secret stack parameters never are."""
    for k in SECRET_PARAMETERS:
        if arg.startswith(k + "="):
            return k + "=****"
    return arg


class Runner:
    def __init__(self, env: dict, dry_run: bool):
        self.env, self.dry = env, dry_run

    def run(self, cmd: list[str], *, capture=False, stdin_text: str | None = None) -> str:
        print("$ " + " ".join(shlex.quote(redact(c)) for c in cmd))
        if self.dry:
            return "<dry-run>"
        r = subprocess.run(cmd, env=self.env, input=stdin_text, text=True, capture_output=capture)
        if r.returncode != 0:
            raise DeployError(f"Command failed ({r.returncode}): {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}"
                              + (f"\n{r.stderr.strip()}" if capture and r.stderr else ""))
        return (r.stdout or "").strip() if capture else ""


def load_params_file() -> dict:
    try:
        return json.loads(PARAMS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_params_file(data: dict) -> None:
    PARAMS_FILE.write_text(json.dumps(data, indent=2))
    try:
        PARAMS_FILE.chmod(0o600)
    except OSError:
        pass


def resolve_extra(runner: Runner, refresh_ami: bool) -> dict:
    extra = load_params_file()
    changed = False
    if refresh_ami or not extra.get("AmiId"):
        extra["AmiId"] = runner.run(cmd_lookup_ami(), capture=True); changed = True
    if not extra.get("CloudFrontPrefixListId"):
        extra["CloudFrontPrefixListId"] = runner.run(cmd_lookup_prefix_list(), capture=True); changed = True
    if not extra.get("OriginVerifySecret"):
        extra["OriginVerifySecret"] = secrets.token_urlsafe(32); changed = True   # made here once, kept in params.local.json
    if changed and not runner.dry:
        save_params_file(extra)
    return extra


def get_outputs(runner: Runner, cfg: Config) -> dict:
    if runner.dry:
        return {k: f"<{k}>" for k in ("UiBucketName", "DataBucketName", "InstanceId", "SiteUrl")}
    raw = runner.run(cmd_outputs(cfg), capture=True)
    return {o["OutputKey"]: o["OutputValue"] for o in json.loads(raw or "[]")}


def step_stack(r: Runner, cfg: Config, refresh_ami=False) -> None:
    extra = resolve_extra(r, refresh_ami)
    r.run(cmd_stack(cfg, stack_parameters(cfg, extra)))


def step_image(r: Runner, cfg: Config) -> None:
    bucket = get_outputs(r, cfg)["DataBucketName"]
    work = Path(tempfile.mkdtemp(prefix="kb-image-"))      # outside the project, so it never enters a build context
    tar, gz = work / "server-image.tar", work / "server-image.tar.gz"
    try:
        for cmd in cmds_build_image(str(tar), cfg):        # docker or nerdctl (containers.engine)
            r.run(cmd)
        if not r.dry:
            with open(tar, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            print(f"image file: {gz.stat().st_size / 1e6:.0f} MB")
        r.run(cmd_upload_image(bucket, str(gz)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def step_ui(r: Runner, cfg: Config) -> None:
    r.run(cmd_sync_ui(get_outputs(r, cfg)["UiBucketName"]))


def step_config(r: Runner, cfg: Config) -> None:
    bucket = get_outputs(r, cfg)["DataBucketName"]
    for name in ("docker-compose.aws.yml", "config.yaml"):
        r.run(cmd_upload_config(bucket, name))
    local = server_local_config()
    if (ROOT / local).exists():
        r.run(cmd_upload_config(bucket, local, as_name="config.local.yaml"))


def step_update(r: Runner, cfg: Config) -> None:
    r.run(cmd_update_server(get_outputs(r, cfg)["InstanceId"]))


def step_outputs(r: Runner, cfg: Config) -> None:
    for k, v in get_outputs(r, cfg).items():
        print(f"{k}: {v}")


# ---------------------------------------------------------------- cost sheet for the Control Center
HOURS_PER_MONTH = 730
S3_STORED_GB = 1.0               # documents, database backups and the page: a generous guess for a small team
ALL_LEVELS = ["running", "stopped", "zero"]


def aws_cost_sheet(cfg: Config) -> dict:
    """Every AWS service this stack is billed for at least a cent a month, with the power levels it is billed at
    (running / stopped / zero). Priced from config.yaml so it agrees with the in-app cost dashboard."""
    r, dpl = cfg.costs.rates, cfg.deployment
    rows = [
        {"name": f"EC2 server ({dpl.instance_type})", "monthly_usd": r.instance_hourly_usd * HOURS_PER_MONTH, "billed_at": ["running"],
         "what": "The server running the app, database and search"},
        {"name": "Elastic IP (public IPv4 address)", "monthly_usd": r.elastic_ip_hourly_usd * HOURS_PER_MONTH, "billed_at": ["running", "stopped"],
         "what": "Billed every hour it exists, also while the server is stopped"},
        {"name": f"EBS disk ({dpl.root_volume_gb} GB gp3)", "monthly_usd": r.ebs_gb_month_usd * dpl.root_volume_gb, "billed_at": ["running", "stopped"],
         "what": "The server's disk, kept while stopped, deleted at zero"},
        {"name": f"S3 storage (about {S3_STORED_GB:g} GB)", "monthly_usd": r.s3_gb_month_usd * S3_STORED_GB, "billed_at": ALL_LEVELS,
         "what": "Documents, database backups, the server image, the page and the credential records"},
    ]
    model = cfg.llm.model_answer
    price = cfg.costs.llm_prices_per_mtok.get(model)
    if cfg.llm.provider != "local" and price and any(price):
        per_q = (3000 * price[0] + 300 * price[1]) / 1e6          # about 3,000 tokens in and 300 out per question
        rows.append({"name": f"AI answers ({model})", "monthly_usd": None, "usage": f"about ${per_q:.3f} per question",
                     "billed_at": ["running"], "what": "Only when someone asks a question"})
    else:   # a local model can't run on the small AWS server: there the answers come from Bedrock, per question
        rows.append({"name": "AI answers (Amazon Bedrock)", "monthly_usd": None, "usage": "per question; set llm.model_answer to a Bedrock model to price it",
                     "billed_at": ["running"], "what": "Only when someone asks a question"})
    services = [dict(x, monthly_usd=round(x["monthly_usd"], 2)) if x["monthly_usd"] is not None else x
                for x in rows if x["monthly_usd"] is None or x["monthly_usd"] >= 0.005]
    return {"project": cfg.project_name, "region": dpl.region, "currency": "USD", "basis": "config.yaml costs.rates (estimates)",
            "services": services,
            "free_tier": ["CloudFront (page and API)", "Lambda (switch and power functions)", "CloudWatch Logs (14 days)",
                          "Systems Manager", "AWS Budgets (first two)"]}


def step_cost_sheet(r: Runner, cfg: Config) -> None:
    sheet = aws_cost_sheet(cfg)
    if r.dry:
        print(json.dumps(sheet, indent=2))
        return
    (ROOT / "deploy" / "aws-costs.json").write_text(json.dumps(sheet, indent=2) + "\n", encoding="utf-8")
    print("wrote deploy/aws-costs.json")


STEPS = {"stack": step_stack, "image": step_image, "ui": step_ui, "config": step_config,
         "update": step_update, "outputs": step_outputs, "cost-sheet": step_cost_sheet}
ALL_ORDER = ("stack", "image", "ui", "config", "update", "outputs")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=[*STEPS, "all"])
    ap.add_argument("--dry-run", action="store_true", help="print commands without running them")
    ap.add_argument("--new-ami", action="store_true", help="look up the newest base image (REPLACES the server on the next stack update)")
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    try:
        branch = current_branch()
        print(f"branch: {branch or 'none'}")
        check_branch(a.step, a.dry_run, branch)
        cfg = load_config(require_secrets=False, local_path=ROOT / server_local_config())   # the server's settings
        if a.step != "cost-sheet":
            check_deployable(cfg)
        if cfg.ui.demo_mode:
            print("Note: ui.demo_mode is true. Set it to false before real use (see docs/UI.md).")
        r = Runner(aws_env(cfg), a.dry_run)
        for step in (ALL_ORDER if a.step == "all" else (a.step,)):
            print(f"\n== {step} ==")
            STEPS[step](r, cfg, a.new_ami) if step == "stack" else STEPS[step](r, cfg)
    except (ConfigError, DeployError) as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
