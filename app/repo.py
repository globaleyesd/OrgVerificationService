"""The document store behind one interface: PgRepository (Postgres + pgvector) for real runs,
MemoryRepository for tests. Both apply the SAME clearance rule: a search only ever returns chunks
whose level is in the allowed list, and counts (never shows) the relevant chunks it held back."""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .config import Config


@dataclass
class Hit:
    id: int
    document_id: int
    document_title: str
    text: str
    location: dict
    level: str
    score: float


@dataclass
class DocRow:
    id: int
    title: str
    file_type: str
    level: str
    chunks: int
    size_bytes: int
    created_at: str


class Repository(Protocol):
    def add_document(self, title: str, file_type: str, storage_path: str, level: str, size_bytes: int) -> int: ...
    def add_chunks(self, document_id: int, level: str, chunks: list[tuple[str, dict, list[float]]]) -> None: ...
    def delete_document(self, document_id: int) -> None: ...
    def count_documents(self) -> int: ...
    def list_documents(self) -> list[DocRow]: ...
    def set_document_level(self, document_id: int, level: str) -> bool: ...
    def search(self, embedding: list[float], allowed: list[str], top_k: int, min_score: float, text: str = "") -> tuple[list[Hit], int]: ...
    def replace_chunks(self, document_id: int, chunks: list[tuple]) -> None: ...
    def storage_path(self, document_id: int) -> str | None: ...
    def record_usage(self, model: str, tokens_in: int, tokens_out: int) -> None: ...
    def tokens_today(self) -> int: ...


class MemoryRepository:
    def __init__(self):
        self.docs: dict[int, dict] = {}
        self.chunks: list[dict] = []
        self.usage: list[tuple] = []
        self._next = 1

    def add_document(self, title, file_type, storage_path, level, size_bytes):
        i, self._next = self._next, self._next + 1
        self.docs[i] = dict(id=i, title=title, file_type=file_type, path=storage_path, level=level, size=size_bytes,
                            created=datetime.now(timezone.utc).isoformat())
        return i

    def add_chunks(self, document_id, level, chunks):
        for text, loc, emb in chunks:
            self.chunks.append(dict(id=len(self.chunks) + 1, doc=document_id, text=text, loc=loc, level=level, emb=emb))

    def delete_document(self, document_id):
        self.docs.pop(document_id, None)
        self.chunks = [c for c in self.chunks if c["doc"] != document_id]

    def count_documents(self):
        return len(self.docs)

    def list_documents(self):
        return [DocRow(d["id"], d["title"], d["file_type"], d["level"], sum(c["doc"] == d["id"] for c in self.chunks), d["size"], d["created"])
                for d in sorted(self.docs.values(), key=lambda d: -d["id"])]

    def set_document_level(self, document_id, level):
        if document_id not in self.docs:
            return False
        self.docs[document_id]["level"] = level
        for c in self.chunks:
            if c["doc"] == document_id:
                c["level"] = level
        return True

    def search(self, embedding, allowed, top_k, min_score, text=""):
        from .retrieval import fuse, keyword_query

        def score(c):
            a, b = embedding, c["emb"]
            na, nb = math.sqrt(sum(x * x for x in a)) or 1.0, math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        def hit(c, s):
            return Hit(c["id"], c["doc"], self.docs[c["doc"]]["title"], c["text"], c["loc"], c["level"], s)
        scored = [(score(c), c) for c in self.chunks]
        scored = [(s, c) for s, c in scored if s >= min_score]
        hits = [hit(c, s) for s, c in sorted(scored, key=lambda x: -x[0]) if c["level"] in allowed][:top_k]
        words = set(keyword_query(text).split(" | ")) - {""}
        if words:   # stand-in for Postgres full-text search: count the question's words in each allowed chunk
            counted = [(sum(w in c["text"].lower() for w in words), c) for c in self.chunks if c["level"] in allowed]
            keyword = [hit(c, n) for n, c in sorted(counted, key=lambda x: -x[0]) if n][:top_k]
            hits = fuse([hits, keyword], top_k)
        withheld = sum(1 for _, c in scored if c["level"] not in allowed)
        return hits, withheld

    def replace_chunks(self, document_id, chunks):
        level = self.docs[document_id]["level"]
        self.chunks = [c for c in self.chunks if c["doc"] != document_id]
        self.add_chunks(document_id, level, chunks)

    def storage_path(self, document_id):
        d = self.docs.get(document_id)
        return d["path"] if d else None

    def record_usage(self, model, tokens_in, tokens_out):
        self.usage.append((datetime.now(timezone.utc), model, tokens_in, tokens_out))

    def tokens_today(self):
        today = datetime.now(timezone.utc).date()
        return sum(i + o for t, _, i, o in self.usage if t.date() == today)


class PgRepository:
    """Postgres + pgvector. One short connection per call. NOT yet exercised against a real database by the
    author (none in the build sandbox): scripts/smoke_local.py does that on your first local run."""

    def __init__(self, cfg: Config):
        self.cfg, self._ready, self._lock = cfg, False, threading.Lock()

    def _conn(self):
        from .db.conn import connect
        from .db.schema import render_schema
        conn = connect(self.cfg)
        if not self._ready:
            with self._lock:
                if not self._ready:
                    with conn.cursor() as cur:
                        cur.execute(render_schema(self.cfg.embeddings.dimensions))
                    conn.commit()
                    self._ready = True
        return conn

    def add_document(self, title, file_type, storage_path, level, size_bytes):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO documents (title, file_type, storage_path, clearance_level, size_bytes, status) "
                        "VALUES (%s, %s, %s, %s, %s, 'ready') RETURNING id", (title, file_type, storage_path, level, size_bytes))
            return cur.fetchone()[0]

    def add_chunks(self, document_id, level, chunks):
        rows = [(document_id, t, json.dumps(loc), level, "[" + ",".join(str(float(x)) for x in emb) + "]") for t, loc, emb in chunks]
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany("INSERT INTO chunks (document_id, text, location, clearance_level, embedding) "
                            "VALUES (%s, %s, %s::jsonb, %s, %s::vector)", rows)

    def delete_document(self, document_id):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))

    def count_documents(self):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM documents")
            return cur.fetchone()[0]

    def list_documents(self):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT d.id, d.title, d.file_type, d.clearance_level, d.size_bytes, d.created_at, "
                        "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) FROM documents d ORDER BY d.id DESC")
            return [DocRow(r[0], r[1], r[2], r[3], r[6], r[4], r[5].isoformat()) for r in cur.fetchall()]

    def set_document_level(self, document_id, level):
        with self._conn() as conn, conn.cursor() as cur:   # both updates commit together or not at all
            cur.execute("UPDATE documents SET clearance_level = %s WHERE id = %s", (level, document_id))
            found = cur.rowcount > 0
            cur.execute("UPDATE chunks SET clearance_level = %s WHERE document_id = %s", (level, document_id))
            return found

    def search(self, embedding, allowed, top_k, min_score, text=""):
        from .retrieval import KEYWORD_SQL, SEARCH_SQL, WITHHELD_SQL, build_params, fuse, keyword_query
        params = build_params(allowed, embedding, top_k, min_score)

        def rows(cur):
            cols = [d.name for d in cur.description]
            return [Hit(r["id"], r["document_id"], r["document_title"], r["text"], r["location"], r["clearance_level"], float(r["score"]))
                    for r in (dict(zip(cols, row)) for row in cur.fetchall())]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(SEARCH_SQL, params)
            hits = rows(cur)
            tsquery = keyword_query(text)
            if tsquery:
                cur.execute(KEYWORD_SQL, {"levels": params["levels"], "tsquery": tsquery, "top_k": params["top_k"]})
                hits = fuse([hits, rows(cur)], params["top_k"])
            cur.execute(WITHHELD_SQL, {k: params[k] for k in ("levels", "embedding", "min_score")})
            return hits, cur.fetchone()[0]

    def replace_chunks(self, document_id, chunks):
        """Swap a document's passages for new ones in one transaction (used by `cli reindex`), keeping its level."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT clearance_level FROM documents WHERE id = %s", (document_id,))
            level = cur.fetchone()[0]
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            cur.executemany("INSERT INTO chunks (document_id, text, location, clearance_level, embedding) "
                            "VALUES (%s, %s, %s::jsonb, %s, %s::vector)",
                            [(document_id, t, json.dumps(loc), level, "[" + ",".join(str(float(x)) for x in emb) + "]") for t, loc, emb in chunks])

    def storage_path(self, document_id):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT storage_path FROM documents WHERE id = %s", (document_id,))
            r = cur.fetchone()
            return r[0] if r else None

    def record_usage(self, model, tokens_in, tokens_out):
        from .usage import record_tokens
        with self._conn() as conn:
            record_tokens(conn, model, tokens_in, tokens_out)

    def tokens_today(self):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(sum(input_tokens + output_tokens), 0) FROM llm_usage WHERE at >= date_trunc('day', now())")
            return int(cur.fetchone()[0])
