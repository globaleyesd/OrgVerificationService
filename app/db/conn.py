"""Database connection. The password comes from a file (a container secret), never from config."""
import os
from pathlib import Path

DEFAULT_PASSWORD_FILE = "/run/secrets/db_password"


def connect(cfg):
    import psycopg   # imported here so tests and tools that don't touch the DB don't need it

    pw_file = Path(os.environ.get("DB_PASSWORD_FILE", DEFAULT_PASSWORD_FILE))
    password = pw_file.read_text().strip()
    d = cfg.database
    return psycopg.connect(host=d.host, port=d.port, dbname=d.name, user=d.user, password=password, connect_timeout=5)
