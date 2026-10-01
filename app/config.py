"""Config loading. Only stdlib + PyYAML (COST: fewer dependencies, smaller image).

Files, merged in this order:
  config.yaml          non-secret settings (committed)
  config.local.yaml    private/machine-specific overrides (git-ignored, optional)
  secrets.local.yaml   credentials (git-ignored, local only)

Unknown keys are rejected so typos fail loudly instead of being ignored.
"""
from __future__ import annotations

import dataclasses
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLACEHOLDER = "CHANGE_ME"
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- sections
@dataclass
class Theme:
    accent: str = "#1F7A3D"
    accent_text: str = "#F9F6EE"
    background: str = "#EFEADC"
    surface: str = "#F9F6EE"
    text: str = "#000000"
    muted: str = "#4A4A40"
    line: str = "#CFC8B4"
    highlight: str = "#F2DE7A"


@dataclass
class Branding:
    app_name: str = "Knowledge Verifier"
    theme: Theme = field(default_factory=Theme)


@dataclass
class Server:
    serve_ui: bool = True


@dataclass
class Credentials:
    backend: str = "file"
    path: str = "creds"
    s3_bucket: str = ""
    s3_prefix: str = ""


@dataclass
class ServiceSwitch:
    mode: str = "local"
    auto_off_hours: float = 0
    max_failed_attempts: int = 5
    lockout_minutes: float = 10
    state_path: str = "data/service_state.json"


@dataclass
class Rates:
    instance_hourly_usd: float = 0.0168
    elastic_ip_hourly_usd: float = 0.005
    ebs_gb_month_usd: float = 0.08
    s3_gb_month_usd: float = 0.023


@dataclass
class Costs:
    heartbeat_seconds: int = 300
    max_range_days: int = 366
    rates: Rates = field(default_factory=Rates)
    llm_prices_per_mtok: dict = field(default_factory=lambda: {
        "claude-haiku-4-5-20251001": [1.0, 5.0],
        "claude-sonnet-5-5": [3.0, 15.0],
    })


@dataclass
class Schedule:
    enabled: bool = False
    timezone: str = "UTC"
    start_hour: int = 8
    stop_hour: int = 18


@dataclass
class Containers:
    engine: str = "docker-desktop"       # docker-desktop | rancher-desktop (see docs/CONTAINERS.md)
    rancher_runtime: str = "moby"         # rancher-desktop only: moby (docker commands) | containerd (nerdctl)
    gpu: str = "auto"                     # auto | on | off: NVIDIA GPU for the local AI model


@dataclass
class Deployment:
    region: str = "us-east-1"
    instance_type: str = "t4g.small"
    use_spot: bool = False
    root_volume_gb: int = 20
    allowed_ingress_cidrs: list = field(default_factory=lambda: ["0.0.0.0/0"])
    domain: str = ""
    schedule: Schedule = field(default_factory=Schedule)
    budget_limit_usd: float = 40


@dataclass
class Clearance:
    levels: list = field(default_factory=lambda: ["employee", "super"])
    default_upload_level: str = "highest"


@dataclass
class Ingestion:
    allowed_extensions: list = field(default_factory=lambda: ["pdf", "docx", "txt", "md", "csv", "xlsx", "pptx", "html", "eml"])
    max_documents: int = 100
    max_file_mb: int = 25
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    ocr_enabled: bool = False


@dataclass
class Embeddings:
    provider: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    dimensions: int = 384


@dataclass
class LLM:
    provider: str = "anthropic"
    model_extract: str = "claude-haiku-4-5-20251001"
    model_answer: str = "claude-sonnet-5-5"
    local_url: str = "http://ollama:11434"
    max_output_tokens: int = 800
    max_context_chunks: int = 8
    daily_token_cap: int = 500000
    cache_answers: bool = True


@dataclass
class Retrieval:
    top_k: int = 20
    min_score: float = 0.25


@dataclass
class Speech:
    mode: str = "browser"


@dataclass
class UI:
    demo_mode: bool = True
    allow_mock: bool = True
    ask_roles: list = field(default_factory=lambda: ["super", "employee"])


@dataclass
class Auth:
    session_minutes: int = 480
    password_min_length: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15


@dataclass
class Audit:
    enabled: bool = True
    retain_days: int = 365


@dataclass
class Storage:
    backend: str = "local"
    local_path: str = "/srv/data/files"
    s3_bucket: str = ""


@dataclass
class Database:
    host: str = "db"
    port: int = 5432
    name: str = "app"
    user: str = "app"


@dataclass
class AwsSecrets:
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    profile: str = ""


@dataclass
class LLMSecrets:
    api_key: str = ""


@dataclass
class AdminSecrets:
    username: str = ""
    password: str = ""


@dataclass
class Secrets:
    aws: AwsSecrets = field(default_factory=AwsSecrets)
    llm: LLMSecrets = field(default_factory=LLMSecrets)
    admin: AdminSecrets = field(default_factory=AdminSecrets)


@dataclass
class Config:
    project_name: str = "kb-verifier"
    branding: Branding = field(default_factory=Branding)
    server: Server = field(default_factory=Server)
    credentials: Credentials = field(default_factory=Credentials)
    service_switch: ServiceSwitch = field(default_factory=ServiceSwitch)
    costs: Costs = field(default_factory=Costs)
    deployment: Deployment = field(default_factory=Deployment)
    containers: Containers = field(default_factory=Containers)
    clearance: Clearance = field(default_factory=Clearance)
    ingestion: Ingestion = field(default_factory=Ingestion)
    embeddings: Embeddings = field(default_factory=Embeddings)
    llm: LLM = field(default_factory=LLM)
    retrieval: Retrieval = field(default_factory=Retrieval)
    speech: Speech = field(default_factory=Speech)
    ui: UI = field(default_factory=UI)
    auth: Auth = field(default_factory=Auth)
    audit: Audit = field(default_factory=Audit)
    storage: Storage = field(default_factory=Storage)
    database: Database = field(default_factory=Database)
    secrets: Secrets = field(default_factory=Secrets)


# ---------------------------------------------------------------- building
def _build(cls, data, path: str):
    """Recursively turn a dict into dataclass `cls`, rejecting unknown keys."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path or 'config'} must be a mapping, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Unknown setting(s) at '{path or 'top level'}': {', '.join(sorted(unknown))}")
    kwargs = {}
    for name, value in data.items():
        hint = hints[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = _build(hint, value, f"{path}.{name}" if path else name)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def deep_merge(base: dict, override: dict) -> dict:
    """Return base with override applied on top. Dicts merge; everything else is replaced."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- validation
def validate(cfg: Config) -> None:
    """Sanity checks that catch the mistakes that cause the worst surprises."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cfg.project_name):
        raise ConfigError("project_name: use lowercase letters, digits and hyphens only")
    name = cfg.branding.app_name
    if not (1 <= len(name) <= 60) or any(ord(c) < 32 for c in name):
        raise ConfigError("branding.app_name: 1-60 printable characters")
    for key, val in dataclasses.asdict(cfg.branding.theme).items():
        if not isinstance(val, str) or not HEX_COLOR.fullmatch(val):
            raise ConfigError(f"branding.theme.{key}: must look like #RRGGBB")
    levels = cfg.clearance.levels
    if not levels or len(set(levels)) != len(levels):
        raise ConfigError("clearance.levels: must be a non-empty list of unique names, lowest first")
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", str(l)) for l in levels):
        raise ConfigError("clearance.levels: use lowercase letters, digits and underscores")
    d = cfg.clearance.default_upload_level
    if d != "highest" and d not in levels:
        raise ConfigError("clearance.default_upload_level: must be 'highest' or one of clearance.levels")
    if cfg.ingestion.chunk_overlap_chars >= cfg.ingestion.chunk_size_chars:
        raise ConfigError("ingestion.chunk_overlap_chars must be smaller than chunk_size_chars")
    from .containers import ENGINES, GPU_MODES, RANCHER_RUNTIMES
    if isinstance(cfg.containers.gpu, bool):   # YAML reads a bare on/off as true/false
        cfg.containers.gpu = "on" if cfg.containers.gpu else "off"
    if cfg.containers.engine not in ENGINES:
        raise ConfigError("containers.engine: must be " + " or ".join(ENGINES))
    if cfg.containers.rancher_runtime not in RANCHER_RUNTIMES:
        raise ConfigError("containers.rancher_runtime: must be " + " or ".join(RANCHER_RUNTIMES))
    if cfg.containers.gpu not in GPU_MODES:
        raise ConfigError("containers.gpu: must be " + ", ".join(GPU_MODES))
    if cfg.llm.provider not in ("anthropic", "bedrock", "local"):
        raise ConfigError("llm.provider: must be 'anthropic', 'bedrock' or 'local'")
    if cfg.storage.backend not in ("local", "s3"):
        raise ConfigError("storage.backend: must be 'local' or 's3'")
    if cfg.speech.mode not in ("browser", "local", "off"):
        raise ConfigError("speech.mode: must be 'browser', 'local' or 'off'")
    if cfg.embeddings.provider not in ("local", "hashing"):
        raise ConfigError("embeddings.provider: must be 'local' or 'hashing' (hashing is for tests and offline demos only)")
    from .parsers import SUPPORTED_EXTENSIONS
    bad = [e for e in cfg.ingestion.allowed_extensions if e not in SUPPORTED_EXTENSIONS]
    if bad:
        raise ConfigError(f"ingestion.allowed_extensions: no reader exists for {', '.join(map(str, bad))} (supported: {', '.join(SUPPORTED_EXTENSIONS)})")
    if cfg.ingestion.max_documents < 1:
        raise ConfigError("ingestion.max_documents must be at least 1")
    if cfg.storage.backend == "s3" and not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", cfg.storage.s3_bucket or ""):
        raise ConfigError("storage.s3_bucket: set a valid bucket name (the AWS setup supplies it automatically)")
    if not 0 <= cfg.retrieval.min_score <= 1:
        raise ConfigError("retrieval.min_score: must be between 0 and 1")
    if cfg.ui.allow_mock and not cfg.ui.demo_mode:
        raise ConfigError("ui.allow_mock must be false when ui.demo_mode is false (no fake data outside demos)")
    if not isinstance(cfg.ui.ask_roles, list) or any(r not in levels for r in cfg.ui.ask_roles):
        raise ConfigError("ui.ask_roles: must be a list of names from clearance.levels")
    cr = cfg.credentials
    if cr.backend not in ("file", "s3", "secrets_manager"):
        raise ConfigError("credentials.backend: must be 'file', 's3' or 'secrets_manager'")
    if not cr.path:
        raise ConfigError("credentials.path: must not be empty")
    if cr.backend == "s3":
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", cr.s3_bucket or ""):
            raise ConfigError("credentials.s3_bucket: set a valid bucket name (the AWS setup supplies it automatically)")
        if not re.fullmatch(r"([a-z0-9_-]+/)*", cr.s3_prefix or ""):
            raise ConfigError("credentials.s3_prefix: empty, or folders like 'creds/'")
    sw = cfg.service_switch
    if sw.mode not in ("local", "aws"):
        raise ConfigError("service_switch.mode: must be 'local' or 'aws'")
    if sw.auto_off_hours < 0 or sw.lockout_minutes < 0 or sw.max_failed_attempts < 1:
        raise ConfigError("service_switch: auto_off_hours and lockout_minutes must be >= 0, max_failed_attempts >= 1")
    if cfg.costs.heartbeat_seconds < 0 or cfg.costs.max_range_days < 1:
        raise ConfigError("costs: heartbeat_seconds must be >= 0 and max_range_days >= 1")
    for k, v in dataclasses.asdict(cfg.costs.rates).items():
        if not isinstance(v, (int, float)) or v < 0:
            raise ConfigError(f"costs.rates.{k}: must be a number >= 0")
    for model, pair in cfg.costs.llm_prices_per_mtok.items():
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(x, (int, float)) or x < 0 for x in pair)):
            raise ConfigError(f"costs.llm_prices_per_mtok.{model}: use [input_price, output_price]")
    for h in ("start_hour", "stop_hour"):
        if not 0 <= getattr(cfg.deployment.schedule, h) <= 23:
            raise ConfigError(f"deployment.schedule.{h}: must be 0-23")


def has_llm_key(cfg: Config) -> bool:
    """True when an Anthropic API key is set. Without one the app still runs; only asking questions is off."""
    key = cfg.secrets.llm.api_key
    return isinstance(key, str) and key.strip() not in ("", PLACEHOLDER)   # a bare "api_key:" loads as None


def check_secrets(cfg: Config, secrets_file_found: bool = True) -> None:
    """Refuse to run with placeholder secrets. Call at app start and before deploy.
    The API key is optional: without it the app starts and only questions are refused (see llm.make_llm)."""
    problems = []
    for name, val in (("admin.username", cfg.secrets.admin.username), ("admin.password", cfg.secrets.admin.password)):
        if val == PLACEHOLDER:
            problems.append(name)
    if problems:
        hint = "" if secrets_file_found else " (secrets.local.yaml was not found)"
        raise ConfigError(
            "Missing or placeholder secrets: " + ", ".join(problems) + hint
            + ". Fill them in or leave them blank in secrets.local.yaml."
        )


def load_config(
    config_path: str | Path = "config.yaml",
    secrets_path: str | Path = "secrets.local.yaml",
    require_secrets: bool = True,
    local_path: str | Path | None = None,
    env: dict | None = None,
) -> Config:
    """env: only used for the settings a deployment supplies itself (credential store, switch mode); default: the process environment."""
    import os
    env = os.environ if env is None else env
    config_path, secrets_path = Path(config_path), Path(secrets_path)
    local_path = Path(local_path) if local_path else config_path.with_name("config.local.yaml")
    if not config_path.exists():
        raise ConfigError(f"Missing {config_path}")
    raw = yaml.safe_load(config_path.read_text()) or {}
    if local_path.exists():
        local = yaml.safe_load(local_path.read_text()) or {}
        if not isinstance(local, dict):
            raise ConfigError(f"{local_path} must be a mapping")
        raw = deep_merge(raw, local)
    if "secrets" in raw:
        raise ConfigError("Do not put secrets in config.yaml or config.local.yaml. Use secrets.local.yaml.")
    found = secrets_path.exists()
    if found:
        raw["secrets"] = yaml.safe_load(secrets_path.read_text()) or {}
    cfg = _build(Config, raw, "")
    # The AWS setup tells the app where its credential store is through the environment (set by the compose file),
    # so nothing has to be edited in the config for a deployment.
    if env.get("APP_CREDENTIALS_BACKEND"):
        cfg.credentials.backend = env["APP_CREDENTIALS_BACKEND"]
    if env.get("APP_STORAGE_BACKEND"):
        cfg.storage.backend = env["APP_STORAGE_BACKEND"]
    if env.get("APP_STORAGE_S3_BUCKET"):
        cfg.storage.s3_bucket = env["APP_STORAGE_S3_BUCKET"]
    if env.get("APP_SERVICE_SWITCH_MODE"):
        cfg.service_switch.mode = env["APP_SERVICE_SWITCH_MODE"]
    if env.get("APP_CREDENTIALS_S3_BUCKET"):
        cfg.credentials.s3_bucket = env["APP_CREDENTIALS_S3_BUCKET"]
    validate(cfg)
    if require_secrets:
        check_secrets(cfg, found)
    return cfg
