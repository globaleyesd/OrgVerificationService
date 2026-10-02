"""Small admin commands.

  python -m app.cli service on|off|status          local only: turn the service on or off (asks for the switch password)
  python -m app.cli set-switch-password
  python -m app.cli ai-costs [--days N]              AI usage and its cost per model as JSON (this month, or the last N days)
  python -m app.cli create-demo-users [--reset]
  python -m app.cli set-user-password USERNAME
  python -m app.cli list-users
  python -m app.cli ingest PATH [--level LEVEL]      read a file or a folder into the document store
  python -m app.cli list-documents
  python -m app.cli set-document-level DOC_ID LEVEL   e.g. 12 employee
  python -m app.cli reindex                          read every stored original again (after parser or chunking changes)

Run them where the credentials live: on your machine for local runs (they write to creds/),
or on the AWS server (they write to the private credentials bucket through the server's role).
Passwords are never printed except the one-time generated demo passwords below.
"""
import getpass
import secrets
import sys
from pathlib import Path

from .auth import DEMO_USERS, UserExists, UserStore
from .config import ConfigError, load_config
from .credstore import CredentialStoreError, make_store
from .ingest import IngestError, ingest_bytes, reindex_document
from .parsers import SUPPORTED_EXTENSIONS, ParseError
from .service_switch import LockedOut, ServiceSwitch, WeakPassword, WrongPassword

USAGE = __doc__


def _build():
    cfg = load_config(require_secrets=False)
    store = make_store(cfg)
    users = UserStore(store, cfg.clearance.levels, min_password_length=cfg.auth.password_min_length)
    return cfg, store, users


def _ask_password(prompt: str) -> str:
    pw = getpass.getpass(prompt)
    if pw != getpass.getpass("Repeat it: "):
        raise ValueError("Passwords do not match")
    return pw


def _ingest(cfg, sv, path: str, level: str | None) -> int:
    root = Path(path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        print(f"Nothing found at {path}")
        return 1
    failed = 0
    for f in files:
        if f.suffix.lstrip(".").lower() not in SUPPORTED_EXTENSIONS:
            print(f"skipped   {f.name} (type not supported)")
            continue
        try:
            r = ingest_bytes(filename=f.name, data=f.read_bytes(), cfg=cfg, repo=sv.repo, embedder=sv.embedder, files=sv.files, level=level)
            print(f"added     #{r['id']} {r['title']} ({r['chunks']} passages, level {level or 'default'})")
        except IngestError as e:
            failed += 1
            print(f"refused   {f.name}: {e.message}")
    return 1 if failed else 0


def main(argv: list[str], services=None) -> int:
    if not argv:
        print(USAGE)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        cfg, store, users = _build()
        if cmd == "service" and rest in (["on"], ["off"], ["status"]):
            # The pages no longer have an on/off button: on AWS the Control Center turns projects on and off,
            # locally this command does, with the same password and lockout as before.
            if cfg.service_switch.mode != "local":
                raise ValueError("On AWS, turn the service on or off from the Control Center")
            sw = ServiceSwitch.from_config(cfg, store)
            if rest == ["status"]:
                print("The service is " + ("on" if sw.is_on() else "off") + ".")
                return 0
            try:
                pw = getpass.getpass("Switch password: ")
                (sw.turn_on if rest == ["on"] else sw.turn_off)(pw)
            except WrongPassword:
                print("Wrong password.")
                return 1
            except LockedOut as e:
                print(f"Too many attempts. Try again in {max(1, round(e.seconds_left / 60))} minute(s).")
                return 1
            print("The service is now " + rest[0] + ".")
        elif cmd == "set-switch-password" and not rest:
            ServiceSwitch.from_config(cfg, store).set_password(_ask_password("New service-switch password: "))
            print("Saved. Only a salted hash is stored.")
        elif cmd == "create-demo-users" and rest in ([], ["--reset"]):
            reset = bool(rest)
            top = cfg.clearance.levels[-1]
            created = []
            for username, display, role in DEMO_USERS:
                if role not in cfg.clearance.levels:
                    print(f"Skipped {display}: role '{role}' is not in clearance.levels")
                    continue
                pw = secrets.token_urlsafe(12)
                try:
                    users.create(username, display, role, pw, replace=reset)
                    created.append((display, role, pw))
                except UserExists:
                    print(f"{display} already exists (use --reset to replace it with a new password)")
            if created:
                print("\nCreated. These passwords are shown ONCE and are not stored anywhere readable.")
                print("Save them in a password manager now:\n")
                for display, role, pw in created:
                    print(f"  {display:<16} role: {role:<10} password: {pw}")
                print(f"\n({top} sees everything; the other role sees only what its level allows.)")
        elif cmd == "set-user-password" and len(rest) == 1:
            users.set_password(rest[0], _ask_password(f"New password for {rest[0]}: "))
            print("Saved. Only a salted hash is stored.")
        elif cmd == "ingest" and rest and (len(rest) == 1 or (len(rest) == 3 and rest[1] == "--level")):
            from .services import build_services
            return _ingest(cfg, services or build_services(cfg), rest[0], rest[2] if len(rest) == 3 else None)
        elif cmd == "list-documents" and not rest:
            from .services import build_services
            for d in (services or build_services(cfg)).repo.list_documents():
                print(f"#{d.id:<4} {d.level:<10} {d.chunks:>3} passages  {d.title}")
        elif cmd == "set-document-level" and len(rest) == 2 and rest[0].isdigit():
            from .services import build_services
            if rest[1] not in cfg.clearance.levels:
                raise ValueError(f"Level must be one of: {', '.join(cfg.clearance.levels)}")
            ok = (services or build_services(cfg)).repo.set_document_level(int(rest[0]), rest[1])
            print("Updated." if ok else "No such document.")
            return 0 if ok else 1
        elif cmd == "reindex" and not rest:
            from .services import build_services
            sv = services or build_services(cfg)
            failed = 0
            for d in sv.repo.list_documents():
                try:
                    n = reindex_document(document_id=d.id, file_type=d.file_type, cfg=cfg, repo=sv.repo, embedder=sv.embedder, files=sv.files)
                    print(f"reindexed #{d.id} {d.title} ({d.chunks} -> {n} passages)")
                except (IngestError, ParseError, OSError) as e:
                    failed += 1
                    print(f"failed    #{d.id} {d.title}: {getattr(e, 'message', None) or e}")
            return 1 if failed else 0
        elif cmd == "ai-costs" and rest in ([], ) + tuple([["--days", str(n)] for n in range(1, 367)]):
            import json
            from datetime import datetime, timedelta, timezone
            from .costs import ai_costs
            from .db.conn import connect
            from .usage import read_usage
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=int(rest[1])) if rest else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            conn = connect(cfg)
            try:
                usage = read_usage(conn, start, now, cfg.costs.heartbeat_seconds)
            finally:
                conn.close()
            out = ai_costs(usage.llm_tokens, cfg.costs.llm_prices_per_mtok)
            print(json.dumps(dict(out, since=start.isoformat(), provider=cfg.llm.provider, model=cfg.llm.model_answer)))
        elif cmd == "list-users" and not rest:
            for u in users.list_users():
                print(f"{u.username:<20} {u.display_name:<20} {u.role}")
        else:
            print(USAGE)
            return 2
    except (ConfigError, CredentialStoreError, WeakPassword, ValueError) as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
