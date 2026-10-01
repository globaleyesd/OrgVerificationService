"""Turns an uploaded file into plain-text segments, each with a location label used in citations.

Standard library only, except PDF (pypdf). Office files are zips of XML, so they are read directly.
Defences: unsupported types are refused with a hint, zip contents are size-capped (zip bombs), damaged
files give a clear message instead of a crash, and total text is capped.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

SUPPORTED_EXTENSIONS = ("pdf", "docx", "txt", "md", "csv", "xlsx", "pptx", "html", "eml")
LEGACY_HINT = {"doc": "docx", "xls": "xlsx", "ppt": "pptx", "rtf": "docx"}
MAX_TEXT_CHARS = 3_000_000
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 500


class ParseError(ValueError):
    pass


@dataclass
class Segment:
    text: str
    location: dict = field(default_factory=dict)   # {"type": ..., "label": ...}


def _seg(text: str, kind: str, label: str) -> Segment:
    return Segment(text, {"type": kind, "label": label})


def extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _blocks(text: str, kind: str, one: str, many: str) -> list[Segment]:
    """Paragraph blocks (separated by blank lines) with line numbers."""
    out, buf, start = [], [], 1
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip():
            if not buf:
                start = n
            buf.append(line)
        elif buf:
            out.append(_seg("\n".join(buf), kind, one.format(start) if start == n - 1 else many.format(start, n - 1)))
            buf = []
    if buf:
        end = start + len(buf) - 1
        out.append(_seg("\n".join(buf), kind, one.format(start) if start == end else many.format(start, end)))
    return out


def _plain(data: bytes) -> list[Segment]:
    return _blocks(_decode(data), "lines", "Line {}", "Lines {}-{}")


def _csv(data: bytes) -> list[Segment]:
    try:
        rows = list(csv.reader(io.StringIO(_decode(data))))
    except csv.Error as e:
        raise ParseError("This CSV file could not be read") from e
    if not rows:
        return []
    header, out = rows[0], []
    for i in range(1, max(len(rows), 2), 20):
        group = rows[i:i + 20]
        lines = ["; ".join(f"{h}: {v}" for h, v in zip(header, r) if v.strip()) for r in group]
        if any(lines):
            out.append(_seg("\n".join(l for l in lines if l), "rows", f"Rows {i + 1}-{i + len(group)}"))
    if len(rows) == 1:
        out.append(_seg("; ".join(header), "rows", "Row 1"))
    return out


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, d):
        if not self.skip:
            self.parts.append(d)


def _html_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(p.parts))


def _html(data: bytes) -> list[Segment]:
    return _blocks(_html_text(_decode(data)), "section", "Section at line {}", "Section at lines {}-{}")


def _eml(data: bytes) -> list[Segment]:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
        head = "\n".join(f"{k}: {msg[k]}" for k in ("Subject", "From", "To", "Date") if msg[k])
        body = msg.get_body(preferencelist=("plain", "html"))
        content = body.get_content() if body else ""
        if body is not None and body.get_content_subtype() == "html":
            content = _html_text(content)
    except Exception as e:
        raise ParseError("This e-mail file could not be read") from e
    return [_seg(head, "email", "Headers")] + _blocks(content, "email", "Message line {}", "Message lines {}-{}")


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ParseError("The file is damaged or is not a valid Office file") from e
    infos = z.infolist()
    if sum(i.file_size for i in infos) > MAX_ZIP_BYTES or any(i.file_size > MAX_ZIP_BYTES // 2 for i in infos):
        raise ParseError("The file expands to too much data")
    return z


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(z.read(name))
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as e:
        raise ParseError(f"The file is missing or has a damaged part ({name})") from e


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _docx(data: bytes) -> list[Segment]:
    root, out = _xml(_zip(data), "word/document.xml"), []
    for p in root.iter(W + "p"):
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if text:
            out.append(_seg(text, "paragraph", f"Paragraph {len(out) + 1}"))
    return out


def _xlsx(data: bytes) -> list[Segment]:
    z, out = _zip(data), []
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(S + "t")) for si in _xml(z, "xl/sharedStrings.xml").iter(S + "si")]
    targets = {r.get("Id"): r.get("Target", "") for r in _xml(z, "xl/_rels/workbook.xml.rels").iter(PR + "Relationship")}
    for sh in _xml(z, "xl/workbook.xml").iter(S + "sheet"):
        t = targets.get(sh.get(R + "id"), "")
        path = t[1:] if t.startswith("/") else "xl/" + t
        for row in _xml(z, path).iter(S + "row"):
            vals = []
            for c in row.findall(S + "c"):
                v = c.find(S + "v")
                if c.get("t") == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                    vals.append(shared[int(v.text)])
                elif c.get("t") == "inlineStr":
                    vals.append("".join(x.text or "" for x in c.iter(S + "t")))
                elif v is not None and v.text:
                    vals.append(v.text)
            if any(x.strip() for x in vals):
                out.append(_seg(" | ".join(x for x in vals if x.strip()), "row", f"{sh.get('name', 'Sheet')}, row {row.get('r', '?')}"))
    return out


def _pptx(data: bytes) -> list[Segment]:
    z, out = _zip(data), []
    slides = sorted((int(m.group(1)), n) for n in z.namelist() if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)))
    for num, name in slides:
        paras = ["".join(t.text or "" for t in p.iter(A + "t")).strip() for p in _xml(z, name).iter(A + "p")]
        out.append(_seg("\n".join(p for p in paras if p), "slide", f"Slide {num}"))
    return out


def _drop_running_lines(pages: list[str], edge: int = 3) -> list[str]:
    """Remove running headers and footers (journal name, author line, page number): lines in the first or last
    `edge` lines of a page that repeat, ignoring digits, on at least half of the pages. They add noise to every
    passage and pull unrelated pages into searches."""
    if len(pages) < 3:
        return pages

    def key(line: str) -> str:
        return re.sub(r"\d+", "#", " ".join(line.split()).lower())

    def edges(lines: list[str]) -> set[int]:
        filled = [i for i, line in enumerate(lines) if line.strip()]
        return set(filled[:edge] + filled[-edge:])
    split = [p.splitlines() for p in pages]
    counts: dict[str, int] = {}
    for lines in split:
        for k in {key(lines[i]) for i in edges(lines)}:
            counts[k] = counts.get(k, 0) + 1
    running = {k for k, n in counts.items() if n >= max(3, len(pages) // 2)}
    return ["\n".join(line for i, line in enumerate(lines) if not (i in edges(lines) and key(line) in running)) for lines in split]


def _pdf(data: bytes) -> list[Segment]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ParseError("PDF support is not installed on this server") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("Password-protected PDFs can't be read")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ParseError(f"PDFs are limited to {MAX_PDF_PAGES} pages")
        pages = _drop_running_lines([p.extract_text() or "" for p in reader.pages])
        segs = [_seg(text, "page", f"Page {i}") for i, text in enumerate(pages, 1)]
    except ParseError:
        raise
    except Exception as e:
        raise ParseError("This PDF could not be read") from e
    if not any(s.text.strip() for s in segs):
        raise ParseError("No text found in this PDF (scanned PDFs need OCR, which is off)")
    return segs


_PARSERS = {"txt": _plain, "md": _plain, "csv": _csv, "html": _html, "eml": _eml, "docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "pdf": _pdf}


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def parse(filename: str, data: bytes) -> list[Segment]:
    ext = extension(filename)
    fn = _PARSERS.get(ext)
    if fn is None:
        hint = f" Save it as .{LEGACY_HINT[ext]} and try again." if ext in LEGACY_HINT else ""
        raise ParseError(f"Files of type '.{ext}' are not supported." + hint)
    segs = [Segment(_clean(s.text), s.location) for s in fn(data)]
    segs = [s for s in segs if s.text]
    if not segs:
        raise ParseError("No readable text was found in this file")
    if sum(len(s.text) for s in segs) > MAX_TEXT_CHARS:
        raise ParseError("This document is too large to index")
    return segs
