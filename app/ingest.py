"""Add knowledge: check, read, split, embed, store. Every new item starts at the level set by
clearance.default_upload_level (the top level by default), so adding something never makes it readable by
lower roles until a reviewer relabels it."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from .chunking import chunk_segments
from .config import Config
from .files import safe_name
from .parsers import ParseError, Segment, extension, parse
from .rbac import initial_level

MAX_TEXT_ENTRY_CHARS = 200_000
BATCH = 32


class IngestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _store(*, title, filename, ext, data, segments, level, cfg, repo, embedder, files) -> dict:
    chunks = chunk_segments(segments, cfg.ingestion.chunk_size_chars, cfg.ingestion.chunk_overlap_chars)
    vectors: list = []
    for i in range(0, len(chunks), BATCH):
        vectors += embedder.embed([c.text for c in chunks[i:i + BATCH]])
    path = files.save(f"files/{uuid.uuid4().hex}/{safe_name(filename)}", data)
    doc_id = repo.add_document(title, ext, path, level, len(data))
    try:
        repo.add_chunks(doc_id, level, [(c.text, c.location, v) for c, v in zip(chunks, vectors)])
    except Exception:
        repo.delete_document(doc_id)    # never leave a document with no searchable text
        raise
    return {"id": doc_id, "title": title, "chunks": len(chunks)}


def _level(cfg: Config, level: str | None) -> str:
    if level is None:
        return initial_level(cfg.clearance.default_upload_level, cfg.clearance.levels)
    if level not in cfg.clearance.levels:
        raise IngestError(400, f"Unknown level '{level}'")
    return level


def _room(cfg, repo):
    if repo.count_documents() >= cfg.ingestion.max_documents:
        raise IngestError(409, "The document limit has been reached. Ask an administrator to remove some.")


def ingest_bytes(*, filename: str, data: bytes, cfg: Config, repo, embedder, files, level: str | None = None) -> dict:
    ext = extension(filename)
    if ext not in cfg.ingestion.allowed_extensions:
        raise IngestError(415, f"Files of type '.{ext}' are not allowed here")
    if len(data) > cfg.ingestion.max_file_mb * 1024 * 1024:
        raise IngestError(413, f"The file is larger than {cfg.ingestion.max_file_mb} MB")
    if not data:
        raise IngestError(422, "The file is empty")
    lvl = _level(cfg, level)
    _room(cfg, repo)
    try:
        segments = parse(filename, data)
    except ParseError as e:
        raise IngestError(422, str(e)) from e
    return _store(title=safe_name(filename), filename=filename, ext=ext, data=data, segments=segments, level=lvl,
                  cfg=cfg, repo=repo, embedder=embedder, files=files)


def ingest_text(*, text: str, cfg: Config, repo, embedder, files, level: str | None = None) -> dict:
    text = text.replace("\x00", "").strip()
    if not text:
        raise IngestError(422, "The entry is empty")
    if len(text) > MAX_TEXT_ENTRY_CHARS:
        raise IngestError(413, "The entry is too long")
    lvl = _level(cfg, level)
    _room(cfg, repo)
    data = text.encode("utf-8")
    first = re.sub(r"\s+", " ", text.split("\n", 1)[0]).strip()[:60] or "Entry"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    segs = [s for s in parse("entry.txt", data)] or [Segment(text, {"type": "lines", "label": "Entry"})]
    return _store(title=f"Entry: {first}", filename=f"entry-{stamp}.txt", ext="txt", data=data, segments=segs, level=lvl,
                  cfg=cfg, repo=repo, embedder=embedder, files=files)


def reindex_document(*, document_id: int, file_type: str, cfg: Config, repo, embedder, files) -> int:
    """Read a stored original again with the current parser and chunking, and replace its passages.
    Keeps the document's id, title and level. Returns the new passage count."""
    path = repo.storage_path(document_id)
    if not path:
        raise IngestError(404, "No such document")
    chunks = chunk_segments(parse(f"original.{file_type}", files.read(path)), cfg.ingestion.chunk_size_chars, cfg.ingestion.chunk_overlap_chars)
    vectors: list = []
    for i in range(0, len(chunks), BATCH):
        vectors += embedder.embed([c.text for c in chunks[i:i + BATCH]])
    repo.replace_chunks(document_id, [(c.text, c.location, v) for c, v in zip(chunks, vectors)])
    return len(chunks)
