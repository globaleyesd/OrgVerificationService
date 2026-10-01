"""RBAC-filtered retrieval.

SECURITY: the clearance filter lives IN the SQL. Chunks the user may not read
are never fetched, so their text can never reach the model or the response.
Use %(name)s placeholders only; never build SQL from user text.
"""
from __future__ import annotations

import re

from .rbac import allowed_levels

SEARCH_SQL = """
SELECT c.id, c.document_id, d.title AS document_title, c.text, c.location, c.clearance_level,
       1 - (c.embedding <=> %(embedding)s::vector) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.clearance_level = ANY(%(levels)s)
  AND 1 - (c.embedding <=> %(embedding)s::vector) >= %(min_score)s
ORDER BY c.embedding <=> %(embedding)s::vector
LIMIT %(top_k)s
"""

# Keyword search over the same chunks with the same clearance filter. Catches exact words ("Published",
# "Moscow", a product code) that meaning-based vector search can rank too low.
KEYWORD_SQL = """
SELECT c.id, c.document_id, d.title AS document_title, c.text, c.location, c.clearance_level,
       ts_rank_cd(to_tsvector('english', c.text), q) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
CROSS JOIN to_tsquery('english', %(tsquery)s) q
WHERE c.clearance_level = ANY(%(levels)s)
  AND to_tsvector('english', c.text) @@ q
ORDER BY score DESC
LIMIT %(top_k)s
"""


def keyword_query(text: str) -> str:
    """The question's words joined with OR, for to_tsquery. Only letters and digits get through, so the user's
    text can never change the query's syntax (it is also passed as a parameter). Postgres drops stop words."""
    words = list(dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z0-9]+", text or "") if len(w) > 1))
    return " | ".join(words[:30])


def fuse(rankings: list[list], top_k: int, k: int = 60) -> list:
    """Reciprocal rank fusion: a chunk near the top of either list ranks high; one in both ranks highest.
    Items need an `id`; the first list's copy of an item is kept, with its score replaced by the fused score."""
    scores: dict = {}
    first: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (k + rank + 1)
            first.setdefault(item.id, item)
    out = []
    for cid in sorted(scores, key=lambda i: -scores[i])[:top_k]:
        item = first[cid]
        item.score = scores[cid]
        out.append(item)
    return out


# Counts relevant chunks the user is NOT cleared for. Returns a number only,
# never text, so the answer can say "some sources were not available at your level".
WITHHELD_SQL = """
SELECT count(*)
FROM chunks c
WHERE NOT (c.clearance_level = ANY(%(levels)s))
  AND 1 - (c.embedding <=> %(embedding)s::vector) >= %(min_score)s
"""


def search_params(user_level: str, levels: list[str], embedding: list[float], top_k: int, min_score: float) -> dict:
    return build_params(allowed_levels(user_level, levels), embedding, top_k, min_score)   # empty list for unknown users -> matches nothing


def build_params(allowed: list[str], embedding: list[float], top_k: int, min_score: float) -> dict:
    return {
        "levels": list(allowed),
        "embedding": "[" + ",".join(str(float(x)) for x in embedding) + "]",
        "top_k": int(top_k),
        "min_score": float(min_score),
    }


def search(conn, user_level, levels, embedding, top_k, min_score):
    """conn: a psycopg connection. Returns (chunks, withheld_count)."""
    params = search_params(user_level, levels, embedding, top_k, min_score)
    with conn.cursor() as cur:
        cur.execute(SEARCH_SQL, params)
        cols = [d.name for d in cur.description]
        chunks = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.execute(WITHHELD_SQL, {k: params[k] for k in ("levels", "embedding", "min_score")})
        withheld = cur.fetchone()[0]
    return chunks, withheld
