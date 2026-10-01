"""Splits parsed segments into searchable chunks of about `size` characters, keeping a location for citations."""
from __future__ import annotations

from dataclasses import dataclass

from .parsers import Segment


@dataclass
class Chunk:
    text: str
    location: dict


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    pieces, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind(". "), window.rfind(" "))
            if cut >= size * 0.5:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_segments(segments: list[Segment], size: int, overlap: int) -> list[Chunk]:
    """Small neighbouring segments are packed together; long ones are split with a little overlap."""
    out: list[Chunk] = []
    buf, first, last = [], None, None

    def flush():
        nonlocal buf, first, last
        if buf:
            loc = dict(first.location)
            if last is not first and last.location.get("label") != first.location.get("label"):
                loc["label"] = f"{first.location.get('label', '')} to {last.location.get('label', '')}".strip()
            out.append(Chunk("\n".join(buf), loc))
        buf, first, last = [], None, None

    for s in segments:
        if len(s.text) > size:
            flush()
            out.extend(Chunk(p, dict(s.location)) for p in _split_long(s.text, size, overlap))
            continue
        if buf and sum(len(b) + 1 for b in buf) + len(s.text) > size:
            flush()
        if not buf:
            first = s
        buf.append(s.text)
        last = s
    flush()
    return out
