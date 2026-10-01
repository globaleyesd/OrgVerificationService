"""Reads the usage meters from Postgres for the cost calculator, and writes the uptime heartbeat.

NOT yet exercised against a real database (the build sandbox has none). Treat the SQL as
reviewed-but-unproven until the first integration run.
"""
from __future__ import annotations

from datetime import datetime

from .costs import Usage

UPTIME_SQL = "SELECT count(*) FROM usage_heartbeat WHERE at >= %(start)s AND at < %(end)s"
TOKENS_SQL = ("SELECT model, coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0) "
              "FROM llm_usage WHERE at >= %(start)s AND at < %(end)s GROUP BY model")
STORAGE_SQL = "SELECT coalesce(sum(size_bytes),0) FROM documents WHERE created_at < %(end)s"
HEARTBEAT_SQL = "INSERT INTO usage_heartbeat DEFAULT VALUES"
RECORD_TOKENS_SQL = "INSERT INTO llm_usage (model, input_tokens, output_tokens) VALUES (%s, %s, %s)"


def read_usage(conn, start: datetime, end: datetime, heartbeat_seconds: int) -> Usage:
    p = {"start": start, "end": end}
    with conn.cursor() as cur:
        cur.execute(UPTIME_SQL, p)
        beats = cur.fetchone()[0]
        cur.execute(TOKENS_SQL, p)
        tokens = {m: (int(i), int(o)) for m, i, o in cur.fetchall()}
        cur.execute(STORAGE_SQL, p)
        stored = float(cur.fetchone()[0])
    return Usage(uptime_seconds=beats * heartbeat_seconds, llm_tokens=tokens, storage_bytes=stored)


def write_heartbeat(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(HEARTBEAT_SQL)
    conn.commit()


def record_tokens(conn, model: str, input_tokens: int, output_tokens: int) -> None:
    with conn.cursor() as cur:
        cur.execute(RECORD_TOKENS_SQL, (model, int(input_tokens), int(output_tokens)))
    conn.commit()
