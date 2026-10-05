"""API. All routes live under /api.

Real endpoints (upload processing and ask) come next; see the README roadmap.
Everything that needs a decision is in plain modules with unit tests (gate.py, access.py,
auth.py, service_switch.py, costs.py), so this file stays thin.
"""
import dataclasses
import logging
import math
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .access import can_ask, can_view_costs
from .answering import AnswerError, answer_question, suggest_question
from .auth import DEMO_USERS, BadLogin, DemoLoginDenied, DemoUsersMissing, LoginLocked, UserStore, demo_login, load_signer
from .config import ConfigError, load_config
from .costs import RangeError, estimate, parse_range
from .credstore import CredentialStoreError, make_store
from .gate import OFFLINE_MESSAGE, blocked_when_off, from_our_cdn
from .ingest import IngestError, ingest_bytes, ingest_text
from .llm import LlmError, LlmNotConfigured, use_stored_key
from .pages import CSP, PAGES, with_versions
from .rbac import can_relabel
from .services import build_services
from .service_switch import LockedOut, ServiceSwitch, WrongPassword

log = logging.getLogger("app")

CONFIG_PATH = os.environ.get("APP_CONFIG", "config.yaml")
SECRETS_PATH = os.environ.get("APP_SECRETS", "secrets.local.yaml")
LOCAL_PATH = os.environ.get("APP_CONFIG_LOCAL") or None
COOKIE = "session"

try:
    cfg = load_config(CONFIG_PATH, SECRETS_PATH, local_path=LOCAL_PATH)   # refuses to start on bad or placeholder config
    store = make_store(cfg)
    use_stored_key(cfg, store)
    switch = ServiceSwitch.from_config(cfg, store)
    # A missing/default switch password is only tolerated in demo mode.
    switch.ensure_credentials(allow_default=cfg.ui.demo_mode)
except (ConfigError, CredentialStoreError) as e:
    raise SystemExit(f"Start-up error: {e}")

# "local": this app has its own on/off switch. "aws": the AWS control function is the only switch (it starts and
# stops the server), so "on" simply means this server is running and the app has no switch of its own.
SWITCH_LOCAL = cfg.service_switch.mode == "local"

users = UserStore(store, cfg.clearance.levels, min_password_length=cfg.auth.password_min_length,
                  max_failed=cfg.auth.max_failed_logins, lockout_minutes=cfg.auth.lockout_minutes)
_signer = None
_signer_lock = threading.Lock()
_services = None
_services_lock = threading.Lock()


def get_services():
    """Database, embedder, file store and AI client, built on first use."""
    global _services
    with _services_lock:
        if _services is None:
            _services = build_services(cfg)
        return _services

app = FastAPI(title=cfg.project_name, docs_url=None, redoc_url=None, openapi_url=None)   # no public API docs page
api = APIRouter(prefix="/api")


# ------------------------------------------------------------ middleware
ORIGIN_SECRET = os.environ.get("APP_ORIGIN_SECRET", "")


@app.middleware("http")
async def gate_and_headers(request: Request, call_next):
    if not from_our_cdn(request.url.path, request.headers.get("x-origin-verify"), ORIGIN_SECRET):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    if SWITCH_LOCAL and blocked_when_off(request.url.path) and not switch.is_on():
        resp = JSONResponse({"detail": OFFLINE_MESSAGE, "offline": True}, status_code=503)
    else:
        resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    else:
        resp.headers.setdefault("Cache-Control", "no-cache")   # revalidate every load (cheap 304), never run stale page code
    return resp


# ------------------------------------------------------------ helpers
def signer():
    global _signer
    with _signer_lock:
        if _signer is None:
            _signer = load_signer(store)   # may raise CredentialStoreError; the caller turns that into a 503
        return _signer


def current_user(request: Request):
    """The signed-in user from the session cookie, or None."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        return signer().verify(token)
    except CredentialStoreError:
        return None


def current_role(request: Request):
    u = current_user(request)
    return u.role if u else None


def require_ask(request: Request) -> None:
    if current_user(request) is None:
        raise HTTPException(status_code=401, detail="Please sign in")
    if not can_ask(current_role(request), cfg.ui):
        raise HTTPException(status_code=403, detail="The Ask page is not available for this account")


def user_payload(user):
    return {"username": user.username, "display_name": user.display_name, "role": user.role} if user else None


def branding_payload() -> dict:
    return {"app_name": cfg.branding.app_name, "theme": dataclasses.asdict(cfg.branding.theme)}


def status_payload() -> dict:
    return {"on": switch.is_on() if SWITCH_LOCAL else True,
            "auto_off_minutes": switch.minutes_until_auto_off() if SWITCH_LOCAL else None,
            "mode": cfg.service_switch.mode, "allow_mock": cfg.ui.allow_mock, "branding": branding_payload()}


class PasswordBody(BaseModel):
    password: str = Field(..., max_length=200)


class LoginBody(BaseModel):
    username: str = Field(..., max_length=60)
    password: str = Field(..., max_length=200)


class DemoLoginBody(BaseModel):
    username: str = Field(..., max_length=60)


class AskBody(BaseModel):
    question: str = Field(..., max_length=2000)
    history: list = Field(default_factory=list)


class SuggestBody(BaseModel):
    text: str = Field(..., max_length=500)
    history: list = Field(default_factory=list)


class LevelBody(BaseModel):
    id: int
    level: str = Field(..., max_length=40)


class CostBody(BaseModel):
    start: str = Field(..., max_length=64)
    end: str = Field(..., max_length=64)


class CostServicesBody(BaseModel):
    days: int = Field(30, ge=1, le=366)            # the last N days, today included
    start: str | None = Field(None, max_length=64)   # or an exact range (both set): ISO date-times
    end: str | None = Field(None, max_length=64)


def _minutes(seconds_left: float) -> int:
    return math.ceil(seconds_left / 60)


def _switch_action(action, body: PasswordBody) -> dict:
    if not SWITCH_LOCAL:
        raise HTTPException(status_code=404, detail="The service switch is handled by the AWS control service")
    try:
        action(body.password)
    except WrongPassword:
        raise HTTPException(status_code=401, detail="Wrong password")
    except LockedOut as e:
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {_minutes(e.seconds_left)} minute(s).")
    except CredentialStoreError as e:
        log.warning("credential store problem: %s", e)
        raise HTTPException(status_code=503, detail="The credential store is unavailable right now")
    return status_payload()


# ------------------------------------------------------------ routes: always reachable
@api.get("/health")
def health():
    return {"status": "ok"}


@api.get("/service/status")
def service_status():
    return status_payload()


@api.post("/service/on")
def service_on(body: PasswordBody):
    return _switch_action(switch.turn_on, body)


@api.post("/service/off")
def service_off(body: PasswordBody):
    return _switch_action(switch.turn_off, body)


# ------------------------------------------------------------ routes: blocked while the service is off
def _signed_in_response(user) -> JSONResponse:
    resp = JSONResponse({"user": user_payload(user)})
    # HttpOnly (scripts can't read it), Secure (HTTPS only), SameSite=Strict (not sent on cross-site requests),
    # and limited to /api. Safari does not accept Secure cookies on plain http://localhost; use Chrome or Firefox locally.
    resp.set_cookie(COOKIE, signer().issue(user, cfg.auth.session_minutes), max_age=int(cfg.auth.session_minutes * 60),
                    httponly=True, secure=True, samesite="strict", path="/api")
    return resp


@api.post("/auth/login")
def login(body: LoginBody):
    try:
        return _signed_in_response(users.verify(body.username, body.password))
    except BadLogin:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    except LoginLocked as e:
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {_minutes(e.seconds_left)} minute(s).")
    except CredentialStoreError as e:
        log.warning("credential store problem: %s", e)
        raise HTTPException(status_code=503, detail="Sign-in is unavailable right now")


@api.post("/auth/demo-login")
def demo_sign_in(body: DemoLoginBody):
    """Passwordless sign-in as one of the two demo accounts. Only works while ui.demo_mode is true."""
    try:
        return _signed_in_response(demo_login(users, body.username, cfg.ui.demo_mode))
    except DemoLoginDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DemoUsersMissing:
        raise HTTPException(status_code=404, detail="The demo accounts have not been created yet. Run: python -m app.cli create-demo-users")
    except CredentialStoreError as e:
        log.warning("credential store problem: %s", e)
        raise HTTPException(status_code=503, detail="Sign-in is unavailable right now")


@api.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/api")
    return resp


@api.get("/auth/me")
def me(request: Request):
    return {"user": user_payload(current_user(request))}


@api.get("/settings/public")
def public_settings(request: Request):
    """Only non-secret values the pages need. Never add anything from cfg.secrets here."""
    user = current_user(request)
    role = user.role if user else None
    return {
        "project_name": cfg.project_name,
        "branding": branding_payload(),
        "allowed_extensions": cfg.ingestion.allowed_extensions,
        "max_file_mb": cfg.ingestion.max_file_mb,
        "speech_mode": cfg.speech.mode,
        "user": user_payload(user),
        "can_view_costs": can_view_costs(role, cfg.clearance.levels),
        "can_review": can_relabel(role or "", cfg.clearance.levels),
        "ui": {"demo_mode": cfg.ui.demo_mode, "demo_login": cfg.ui.demo_mode, "can_ask": can_ask(role, cfg.ui), "allow_mock": cfg.ui.allow_mock},
        # the accounts the sign-in screen offers in demo mode (names and roles only; they need no password there)
        "demo_users": [{"username": u, "display_name": d, "role": r} for u, d, r in DEMO_USERS if r in cfg.clearance.levels] if cfg.ui.demo_mode else [],
    }


def _clean_history(raw) -> list[dict]:
    out = []
    for h in (raw if isinstance(raw, list) else [])[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("text"), str):
            out.append({"role": h["role"], "text": h["text"][:1000]})
    return out


def _problem(e: Exception) -> HTTPException:
    """Turn an unexpected failure into a plain message, never a stack trace or internal detail."""
    if isinstance(e, LlmNotConfigured):
        return HTTPException(status_code=503, detail="Questions are turned off: no AI key is set. Add llm.api_key to secrets.local.yaml (on AWS: set-llm-key) and restart.")
    if isinstance(e, LlmError):
        log.warning("AI service problem: %s", e)
        return HTTPException(status_code=502, detail="The AI service is unavailable right now")
    log.warning("request failed: %s", type(e).__name__)
    return HTTPException(status_code=503, detail="This is unavailable right now. Please try again.")


@api.post("/upload")
async def upload(request: Request):
    """Add page. Form field `file` or `text`. Needs a signed-in user (any role).
    The server assigns the access level (the top one by default); uploaders never choose it."""
    if current_user(request) is None:
        raise HTTPException(status_code=401, detail="Please sign in")
    limit = cfg.ingestion.max_file_mb * 1024 * 1024
    length = request.headers.get("content-length")
    if length is None:
        raise HTTPException(status_code=411, detail="Length required")
    if not length.isdigit() or int(length) > limit + 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"The file is larger than {cfg.ingestion.max_file_mb} MB")
    form = await request.form()
    try:
        sv = get_services()
        common = dict(cfg=cfg, repo=sv.repo, embedder=sv.embedder, files=sv.files)
        f, text = form.get("file"), form.get("text")
        if f is not None and hasattr(f, "read"):
            data = await f.read(limit + 1)
            res = await run_in_threadpool(lambda: ingest_bytes(filename=f.filename or "file", data=data, **common))
        elif isinstance(text, str):
            res = await run_in_threadpool(lambda: ingest_text(text=text, **common))
        else:
            raise HTTPException(status_code=400, detail="Send a file or some text")
    except IngestError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        raise _problem(e)
    return {"id": res["id"], "title": res["title"], "chunks": res["chunks"]}   # no level: uploaders are not told


@api.post("/ask")
def ask(body: AskBody, request: Request):
    """Ask page. Needs sign-in and an allowed role. Response contract is in docs/UI.md."""
    require_ask(request)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question")
    try:
        sv = get_services()
        return answer_question(question=question, history=_clean_history(body.history), role=current_role(request), cfg=cfg,
                               repo=sv.repo, embedder=sv.embedder, llm=sv.llm)
    except AnswerError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    except Exception as e:
        raise _problem(e)


@api.post("/suggest")
def suggest(body: SuggestBody, request: Request):
    """Ask page, after the microphone: the question the person probably meant, in case speech recognition misheard.
    Same sign-in and role as Ask. Optional by nature: any problem just means no suggestion."""
    require_ask(request)
    try:
        sv = get_services()
        s = suggest_question(text=body.text, history=_clean_history(body.history), role=current_role(request), cfg=cfg,
                             repo=sv.repo, embedder=sv.embedder, llm=sv.llm)
    except Exception as e:
        log.warning("suggestion failed: %s", type(e).__name__)
        s = None
    return {"suggestion": s}


def require_reviewer(request: Request) -> None:
    if current_user(request) is None:
        raise HTTPException(status_code=401, detail="Please sign in")
    if not can_relabel(current_role(request) or "", cfg.clearance.levels):
        raise HTTPException(status_code=403, detail="Only the top role can review documents")


@api.get("/documents")
def documents(request: Request):
    """Review page: every document with its level. Top role only."""
    require_reviewer(request)
    try:
        rows = get_services().repo.list_documents()
    except Exception as e:
        raise _problem(e)
    return {"levels": cfg.clearance.levels, "documents": [dataclasses.asdict(r) for r in rows]}


@api.post("/documents/level")
def set_level(body: LevelBody, request: Request):
    """Mark a document readable by a level (this is how Employees get to see a document). Top role only."""
    require_reviewer(request)
    if body.level not in cfg.clearance.levels:
        raise HTTPException(status_code=400, detail="Unknown level")
    try:
        found = get_services().repo.set_document_level(body.id, body.level)
    except Exception as e:
        raise _problem(e)
    if not found:
        raise HTTPException(status_code=404, detail="No such document")
    log.info("document %s relabelled to %s", body.id, body.level)
    return {"id": body.id, "level": body.level}


def _service_status(documents: list) -> dict:
    """What is running right now, checked live with short timeouts. Never raises."""
    import json as _json
    import urllib.request
    from .config import has_llm_key
    st = {"server": {"state": "running", "detail": "Answering requests now"}}
    try:
        from .db.conn import connect
        conn = connect(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
        st["database"] = {"state": "running", "detail": "Reachable; included in the server's cost"}
    except Exception:
        st["database"] = {"state": "stopped", "detail": "Not reachable"}
    used_locally = cfg.llm.provider == "local"
    try:
        with urllib.request.urlopen(cfg.llm.local_url.rstrip("/") + "/api/ps", timeout=2) as r:
            loaded = _json.loads(r.read()).get("models") or []
        if loaded:
            m = loaded[0]
            gpu = round(100 * (m.get("size_vram") or 0) / (m.get("size") or 1))
            st["local_ai"] = {"state": "running", "detail": f"{m.get('name', 'model')} loaded, {gpu}% on the GPU; no fee per question"}
        else:
            st["local_ai"] = {"state": "idle", "detail": "Up; no model loaded (it loads on the next question)" if used_locally
                              else f"Up but not used (llm.provider is {cfg.llm.provider})"}
    except Exception:
        st["local_ai"] = {"state": "stopped" if used_locally else "off", "detail": "Not reachable" if used_locally else "Not in use"}
    if cfg.llm.provider == "bedrock":
        st["hosted_ai"] = {"state": "running", "detail": "Amazon Bedrock; charged per question"}
    elif cfg.llm.provider == "anthropic" and has_llm_key(cfg):
        st["hosted_ai"] = {"state": "running", "detail": "Anthropic API key set; charged per question"}
    else:
        st["hosted_ai"] = {"state": "off", "detail": f"Not in use (llm.provider is {cfg.llm.provider})"}
    mb = sum(size for _, size in documents) / 1e6
    st["storage"] = {"state": "running", "detail": f"{len(documents)} file{'' if len(documents) == 1 else 's'}, {mb:.1f} MB stored"}
    for key in ("disk", "public_ip"):
        st[key] = ({"state": "aws_only", "detail": "Exists only on AWS, not on this machine: the amount is what it would cost there"}
                   if SWITCH_LOCAL else {"state": "running", "detail": "Allocated; billed every hour, even while the server is stopped"})
    return st


@api.post("/costs/services")
def costs_services(body: CostServicesBody, request: Request):
    """Cost by service, day by day, for the last `days` days or an exact start/end, with what is running now. Super only."""
    if not can_view_costs(current_role(request), cfg.clearance.levels):
        raise HTTPException(status_code=403, detail="Cost details are not available for this account")
    import datetime as _dt
    from .cost_services import by_service, read_meters
    if body.start or body.end:
        try:
            start, end = parse_range(body.start or "", body.end or "", cfg.costs.max_range_days)
        except RangeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        end = _dt.datetime.now(_dt.timezone.utc)
        start = (end - _dt.timedelta(days=min(body.days, cfg.costs.max_range_days) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        from .db.conn import connect
        conn = connect(cfg)
        try:
            meters = read_meters(conn, start, end)
        finally:
            conn.close()
    except Exception as e:
        log.warning("cost meters unavailable: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Usage records are not available right now")
    out = by_service(meters, start, end, rates=cfg.costs.rates, llm_prices=cfg.costs.llm_prices_per_mtok,
                     root_volume_gb=cfg.deployment.root_volume_gb, heartbeat_seconds=cfg.costs.heartbeat_seconds,
                     status=_service_status(meters.documents))
    out["mode"] = "local" if SWITCH_LOCAL else "aws"
    return out


@api.post("/costs/estimate")
def costs_estimate(body: CostBody, request: Request):
    """Estimated cost between two times. Contract in docs/SERVICE_SWITCH_AND_COSTS.md."""
    if not can_view_costs(current_role(request), cfg.clearance.levels):
        raise HTTPException(status_code=403, detail="Cost details are not available for this account")
    try:
        start, end = parse_range(body.start, body.end, cfg.costs.max_range_days)
    except RangeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        from .db.conn import connect
        from .usage import read_usage
        conn = connect(cfg)
        try:
            usage = read_usage(conn, start, end, cfg.costs.heartbeat_seconds)
        finally:
            conn.close()
    except Exception as e:   # database unreachable, tables missing, ...
        log.warning("cost meters unavailable: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="Usage records are not available right now")
    return estimate(start, end, usage, cfg.costs.rates, cfg.costs.llm_prices_per_mtok, cfg.deployment.root_volume_gb)


app.include_router(api)


# ------------------------------------------------------------ uptime heartbeat (feeds the cost calculator)
def _heartbeat_loop() -> None:
    from .db.conn import connect
    from .db.schema import render_schema
    from .usage import write_heartbeat
    schema_done = False
    while True:
        try:
            conn = connect(cfg)
            try:
                if not schema_done:
                    with conn.cursor() as cur:
                        cur.execute(render_schema(cfg.embeddings.dimensions))
                    conn.commit()
                    schema_done = True
                write_heartbeat(conn)
            finally:
                conn.close()
        except Exception as e:   # never let metering break the app
            log.warning("heartbeat failed: %s", type(e).__name__)
        time.sleep(cfg.costs.heartbeat_seconds)


if cfg.costs.heartbeat_seconds > 0 and not os.environ.get("DISABLE_HEARTBEAT"):
    threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True).start()



# ------------------------------------------------------------ web pages (local runs; S3 + CloudFront serve them on AWS)
_web = Path(__file__).parent / "web"
if cfg.server.serve_ui and _web.is_dir():
    def _page(filename: str):
        def handler():
            return HTMLResponse(with_versions((_web / filename).read_text(encoding="utf-8"), _web))
        return handler

    for _route, _file in PAGES.items():       # explicit routes first so "/ask" works without ".html"
        app.add_api_route(_route, _page(_file), methods=["GET"], include_in_schema=False)
    app.mount("/", StaticFiles(directory=_web), name="web")   # style.css and the .js files
