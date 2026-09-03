"""Fetching a knowledge base from a URL instead of a file.

Written against the file it has to read, not against the idea of a web page.
That file is an Excel workbook exported to HTML, and every assumption a generic
crawler would make about it is wrong:

  * the URL you are given has NO CONTENT. It is a <frameset> whose job is to
    load two other pages.
  * the links to the real pages are in a JAVASCRIPT ARRAY, not in <a href>.
    A crawler that follows anchors finds nothing at all and reports success.
  * the content is TABLES. An extractor built for articles flattens a
    four-column dispositions table into a paragraph and loses which column
    each phrase came from.
  * words are joined by \xa0, not spaces. Without normalising it, "When to
    Raise" is three tokens that never match a search for "when to raise" -
    which is how this was nearly missed while checking.

So this is a workbook reader, and it says so. Ordinary pages work too, but the
shape it was measured against is that one.

No new dependencies. stdlib urllib and html.parser handle machine-generated
markup perfectly well, and agent/kb.py runs inside the admin API container as
well as the CLI - a package added here has to be pinned in two requirements
files that a comment already begs people to keep in step.
"""
from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

logger = logging.getLogger("voice-agent")

# One page, not a mirror of the internet. A 40 MB PDF behind a link would
# otherwise be pulled into memory before anything looked at its type.
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 20

# Below this, a page has a picture on it and nothing to read. Measured, not
# guessed: in the workbook this was built for, a real content sheet holds 925
# and 4090 characters, and a sheet that is only screenshots holds 56.
MIN_TEXT_CHARS = 200

# Excel writes the image's name into the alt text, so every screenshot arrives
# as the word "Bitmap". Chunked and embedded, it is a document that says
# nothing and competes with the ones that do.
_ALT_NOISE = {"bitmap", "picture", "image", "logo", "shape", "s"}

# No real table is wider than this, and one that claims to be is Excel's
# formatting rather than data. Without the cap a single stray row at column
# 16,384 defines the width of every row beneath it.
_MAX_COLS = 24

_UA = "aivoice-kb/1.0 (+knowledge base import)"


# ─────────────────────────── where we may fetch from ───────────────────────

class Blocked(Exception):
    """Refused before any request was made."""


def _check_host(host: str) -> None:
    """Refuse anything that points back at us.

    NOT a block on private addresses. The knowledge base this was built for
    lives on 10.130.1.233 - an internal server on the customer's own network -
    so a rule of "no private IPs" would have refused the only thing anybody
    wanted to import, and it would have said "security" while doing it.

    What is actually dangerous is reaching OUR OWN services, so that is what is
    refused: loopback, the cloud metadata address, and anything not a normal
    routable host.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise Blocked(f"cannot resolve {host}: {e}")

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_loopback:
            raise Blocked(f"{host} resolves to {addr}, which is this server")
        if addr.is_link_local:
            # 169.254.169.254 is the cloud metadata service - credentials, on
            # an unauthenticated HTTP endpoint.
            raise Blocked(f"{host} resolves to a link-local address ({addr})")
        if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
            raise Blocked(f"{host} resolves to {addr}, which is not a host")


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Check every hop, not just the first.

    Without this the guard is decoration: a shortener, or any URL under
    someone else's control, answers 302 to http://127.0.0.1:5432 and the
    check that already passed is the one that mattered.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname
        if host:
            _check_host(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_GuardedRedirects)


def fetch(url: str) -> tuple[str, str, bytes]:
    """-> (final url, content type, body). Blocking; call it in a thread."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise Blocked(f"{parts.scheme or 'this'} is not a URL we can fetch")
    if not parts.hostname:
        raise Blocked("no host in that URL")
    _check_host(parts.hostname)

    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _opener.open(req, timeout=TIMEOUT_S) as resp:
        # One byte over the cap, so a file exactly at the limit is not reported
        # as truncated.
        body = resp.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise Blocked(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
        return resp.geturl(), (resp.headers.get_content_type() or ""), body


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        # Excel writes its charset in a meta tag and not in the header.
        m = re.search(rb"charset=([\w-]+)", body[:2048], re.I)
        if m:
            return body.decode(m.group(1).decode("ascii", "replace"), "replace")
        return body.decode("utf-8", "replace")
    return body.decode(m.group(1), "replace")


# ───────────────────────────── reading the html ────────────────────────────

class _Reader(HTMLParser):
    """Text and tables out of machine-generated HTML.

    Tables become markdown pipe tables, matching what kb.py already does for
    Word documents - the chunker and the model have both been fed that shape
    since the knowledge base existed, and a second table format would be a
    difference for no reason.

    Nested tables are flattened into the cell that holds them. Excel wraps
    every floating image in its own one-cell table, and treating those as real
    tables produces a document of empty grids.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0          # inside <script>/<style>
        self._depth = 0         # table nesting
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.images = 0

    # -- plumbing --
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "img":
            self.images += 1
            alt = dict(attrs).get("alt") or ""
            if alt.strip().lower() not in _ALT_NOISE:
                self._emit(alt.strip())
            return
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
            return
        if self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []
        if tag in ("br", "p", "div", "li"):
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "table":
            if self._depth == 1:
                self._flush_table()
            self._depth = max(0, self._depth - 1)
            return
        if self._depth == 1:
            if tag in ("td", "th") and self._cell is not None:
                text = _clean(" ".join(self._cell))
                (self._row if self._row is not None else self.out).append(text)
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self._rows.append(self._row)
                self._row = None

    def handle_data(self, data):
        if self._skip:
            return
        self._emit(data)

    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.out.append(text)

    # -- tables --
    def _flush_table(self) -> None:
        """Render one top-level table, or drop it if it holds nothing.

        The width is taken AFTER trailing empty cells are stripped from every
        row, and capped. Both of those are load-bearing.

        Measured on the workbook this was built for: one sheet held 1087 real
        rows of six cells and four formatting rows of 16,384 - Excel's entire
        column count. Padding every row to the widest turned 3 MB of HTML into
        35 MB of text and 17.8 million pipe characters, all of which would have
        been chunked and embedded.
        """
        rows = []
        for r in self._rows:
            while r and not r[-1].strip():
                r.pop()
            if any(c.strip() for c in r):
                rows.append(r)
        self._rows = []
        if not rows:
            return

        width = min(max(len(r) for r in rows), _MAX_COLS)
        # One cell wide is not a table. Excel uses those as layout boxes, and a
        # single-column pipe table is noise around text that reads fine plain.
        if width < 2:
            for r in rows:
                self._emit("\n" + " ".join(c for c in r if c.strip()))
            return

        def line(cells: list[str]) -> str:
            cut = list(cells[:width]) + [""] * (width - len(cells))
            return "| " + " | ".join(c.replace("|", "/") for c in cut) + " |"

        body = [line(rows[0]), "|" + " --- |" * width]
        body += [line(r) for r in rows[1:]]
        self._emit("\n\n" + "\n".join(body) + "\n\n")

    def text(self) -> str:
        return _clean_block("".join(self.out))


def _clean(s: str) -> str:
    """One line, no non-breaking spaces.

    \xa0 is the reason "When to Raise" could not be found in this workbook with
    a plain search. Left in, it reaches the embedding as a different token from
    a space and every phrase a caller might say misses.
    """
    return re.sub(r"[\s\xa0​]+", " ", s).strip()


def _clean_block(s: str) -> str:
    s = s.replace("\xa0", " ").replace("​", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


_TABLE_PUNCT = re.compile(r"[|\-\s]+")


def prose_length(text: str) -> int:
    """How much of this is actually words.

    A sheet of screenshots still produces a grid, and the grid has characters
    in it. Counting the raw length called an image-only sheet 236 characters of
    content, when 176 of those were pipes and dashes. What decides whether a
    page is worth embedding is what is left once the table drawing is removed.
    """
    return len(_TABLE_PUNCT.sub("", text))


def extract(markup: str) -> tuple[str, int]:
    """-> (text with tables as markdown, number of images seen)."""
    r = _Reader()
    try:
        r.feed(markup)
        r.close()
    except Exception:
        # A malformed page should cost one document, not the whole import.
        logger.exception("html parse failed - falling back to tag stripping")
        return _clean_block(html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))), 0
    return r.text(), r.images


# ─────────────────────────── workbooks and pages ───────────────────────────

_FILELIST = re.compile(r'HRef="([^"]+)"', re.I)
_FRAME_SRC = re.compile(r'(?is)<frame[^>]+src="([^"]+)"')
_SHEET = re.compile(r"(?i)^sheet\d+\.html?$")


def is_workbook(markup: str) -> bool:
    """An Excel or Word export whose pages are listed in filelist.xml."""
    head = markup[:4000].lower()
    return ("microsoft excel" in head or "excel.sheet" in head
            or "<frameset" in markup[:8000].lower())


# Excel writes the tab names into a JavaScript array in the frameset. They are
# the only place the real names exist - "Flex Fuel FAQ", "New Prices Oil &
# Consummables" - and without them every citation on this knowledge base would
# read "sheet031.htm", which tells a reader nothing about where an answer came
# from.
_SHEET_NAMES = re.compile(r"(?is)c_aSheetNames\s*=\s*new\s+Array\((.*?)\)")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def workbook_sheet_names(markup: str) -> list[str]:
    m = _SHEET_NAMES.search(markup)
    if not m:
        return []
    return [html.unescape(n).replace("\\'", "'").strip()
            for n in _QUOTED.findall(m.group(1))]


def workbook_pages(url: str, markup: str) -> list[str]:
    """Every sheet of a workbook, in order, as absolute URLs.

    Read from filelist.xml - Microsoft's own manifest, written by the same
    export - rather than from the JavaScript array in the frameset. The array
    holds the same names and is a script; the manifest is data, and it is the
    difference between parsing and guessing.
    """
    base = url
    frames = _FRAME_SRC.findall(markup)
    if frames:
        # The frameset points at <name>_files/sheet001.htm; filelist.xml sits
        # beside the sheets, not beside the frameset.
        base = urllib.parse.urljoin(url, frames[0])

    manifest = urllib.parse.urljoin(base, "filelist.xml")
    try:
        _, ctype, body = fetch(manifest)
    except (urllib.error.URLError, Blocked, OSError) as e:
        logger.warning("no filelist.xml at %s (%s)", manifest, e)
        return []

    pages = []
    for href in _FILELIST.findall(_decode(body, ctype)):
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if _SHEET.match(name):
            pages.append(urllib.parse.urljoin(manifest, href))
    return pages


_TITLE = re.compile(r"(?is)<title>(.*?)</title>")


def page_title(markup: str, fallback: str) -> str:
    m = _TITLE.search(markup)
    if m:
        title = _clean(html.unescape(m.group(1)))
        if title:
            return title[:200]
    return fallback
