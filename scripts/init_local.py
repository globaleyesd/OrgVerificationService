"""One-time local setup: creates a random DB password file and the creds/ and data/ folders (all git-ignored)."""
import os
import secrets
from pathlib import Path

for d in ("data", "creds"):
    Path(d).mkdir(exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
p = Path("data/db_password")
if p.exists():
    print("data/db_password already exists, leaving it alone.")
else:
    p.write_text(secrets.token_urlsafe(32))
    p.chmod(0o600)
    print("Created data/db_password")
if not Path("config.local.yaml").exists():
    Path("config.local.yaml").write_text("# Private overrides for this machine (git-ignored). See config.local.example.yaml.\n{}\n")
    print("Created empty config.local.yaml")
if not Path("secrets.local.yaml").exists():
    print("Next: cp secrets.example.yaml secrets.local.yaml  and fill it in.")
