"""
Knowledge base: layout-aware PDF ingestion + hybrid retrieval.

Extraction uses pymupdf4llm, not plain get_text(). On designed layouts
(quotes, brochures, multi-column policy docs) raw text extraction reads across
columns instead of down them, which silently fuses unrelated content - e.g. two
pricing options merging into one unusable chunk. pymupdf4llm resolves reading
order and emits markdown, giving real headings and tables to chunk on.

ingest_file() is the single entry point: CLI today, folder watcher and the
Step 11 admin UI later.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf4llm
import tiktoken
from openai import AsyncOpenAI

import store

log = logging.getLogger("kb")

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
TARGET_TOKENS = int(os.getenv("KB_CHUNK_TOKENS", "250"))
OVERLAP_TOKENS = int(os.getenv("KB_CHUNK_OVERLAP", "50"))
MIN_CHUNK_TOKENS = int(os.getenv("KB_MIN_CHUNK_TOKENS", "40"))
LEX_THRESHOLD = float(os.getenv("KB_LEX_THRESHOLD", "0.20"))
EMBED_BATCH = 96

try:
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _enc = None


def ntok(s: str) -> int:
    return len(_enc.encode(s)) if _enc else max(1, len(s) // 4)


# ────────────────────────────── extraction ──────────────────────────────

_PIC = re.compile(r"<!--\s*(?:Start|End) of picture text\s*-->")
_TAG = re.compile(r"</?(?:sup|sub|span|div)[^>]*>")


def clean_md(md: str) -> str:
    """Strip pymupdf4llm's markup scaffolding, keep the text it wraps.

    Picture-text markers wrap real content (logos with taglines, badge graphics),
    so the markers go but the text stays.
    """
    md = _PIC.sub("", md)
    md = md.replace("<br>", "\n").replace("<br/>", "\n")
    md = _TAG.sub("", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def extract_pdf(path: str) -> tuple[list[tuple[int, str]], int]:
    """-> ([(page_no, markdown)], page_count)"""
    pages = pymupdf4llm.to_markdown(path, page_chunks=True, show_progress=False)
    out = []
    for p in pages:
        md = clean_md(p.get("text", ""))
        if md:
            out.append((p.get("metadata", {}).get("page", len(out) + 1), md))
    return out, len(pages)


# ────────────────────────────── chunking ──────────────────────────────

@dataclass
class Chunk:
    seq: int
    page: int
    heading: str
    content: str
    n_tokens: int = 0
    embed_text: str = field(default="", repr=False)


_H = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _strip_md(s: str) -> str:
    return re.sub(r"[*_`]", "", s).strip()


def chunk_markdown(pages: list[tuple[int, str]], title: str) -> list[Chunk]:
    """Split on markdown headings, then pack to TARGET_TOKENS with overlap.

    Heading depth is tracked as a stack, so a chunk carries its full path
    ("DAY-BY-DAY ITINERARY > Desert Safari with BBQ Dinner"). Short questions
    retrieve noticeably better when a chunk knows where it came from.
    """
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_tok = 0
    cur_page = pages[0][0] if pages else 1

    def heading_path() -> str:
        return " > ".join(h for _, h in stack)

    def emit():
        nonlocal buf, buf_tok
        content = "\n".join(buf).strip()
        buf, buf_tok = [], 0
        if len(content) < 25:
            return
        path = " > ".join(x for x in (title, heading_path()) if x)
        chunks.append(Chunk(
            seq=len(chunks), page=cur_page, heading=heading_path(),
            content=content, n_tokens=ntok(content),
            embed_text=f"{path}\n\n{content}" if path else content,
        ))

    for page_no, md in pages:
        cur_page = page_no
        lines = md.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            m = _H.match(line)
            if m:
                emit()
                depth, text = len(m.group(1)), _strip_md(m.group(2))
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                if text:
                    stack.append((depth, text))
                i += 1
                continue

            # a markdown table is atomic - splitting one destroys the column
            # alignment and the rows become meaningless
            if _TABLE_ROW.match(line):
                tbl = []
                while i < len(lines) and (_TABLE_ROW.match(lines[i]) or not lines[i].strip()):
                    if lines[i].strip():
                        tbl.append(lines[i])
                    i += 1
                block = "\n".join(tbl)
                if buf and buf_tok + ntok(block) > TARGET_TOKENS:
                    emit()
                buf.append(block)
                buf_tok += ntok(block)
                continue

            if not line.strip():
                i += 1
                continue

            t = ntok(line)
            if buf and buf_tok + t > TARGET_TOKENS:
                tail, tail_tok = [], 0
                for prev in reversed(buf):
                    pt = ntok(prev)
                    if tail_tok + pt > OVERLAP_TOKENS:
                        break
                    tail.insert(0, prev)
                    tail_tok += pt
                emit()
                buf, buf_tok = tail, tail_tok
            buf.append(line)
            buf_tok += t
            i += 1
    emit()
    return _absorb_tiny(chunks)


def _absorb_tiny(chunks: list[Chunk]) -> list[Chunk]:
    """Fold sub-threshold chunks into a neighbour.

    A 5-token chunk answers nothing but still competes in retrieval and
    displaces a real result. Merge forward (keeps it under its own heading),
    else backward.
    """
    out: list[Chunk] = []
    i = 0
    while i < len(chunks):
        c = chunks[i]
        if c.n_tokens < MIN_CHUNK_TOKENS:
            if i + 1 < len(chunks):
                nxt = chunks[i + 1]
                nxt.content = c.content + "\n" + nxt.content
                nxt.embed_text = c.content + "\n" + nxt.embed_text
                nxt.n_tokens = ntok(nxt.content)
                i += 1
                continue
            if out:
                out[-1].content += "\n" + c.content
                out[-1].embed_text += "\n" + c.content
                out[-1].n_tokens = ntok(out[-1].content)
                i += 1
                continue
        out.append(c)
        i += 1
    for n, c in enumerate(out):
        c.seq = n
    return out


# ────────────────────────────── embeddings ──────────────────────────────

_client: AsyncOpenAI | None = None


def _openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


async def embed(texts: list[str], on_batch=None) -> list[list[float]]:
    """on_batch(done, total) is awaited after each batch, if given.

    Embedding is the long pole of an ingest and the only stage with a real
    denominator, which makes it the one worth reporting.
    """
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        r = await _openai().embeddings.create(
            model=EMBED_MODEL, input=texts[i:i + EMBED_BATCH])
        out.extend(d.embedding for d in sorted(r.data, key=lambda d: d.index))
        if on_batch:
            await on_batch(len(out), len(texts))
    return out


def _vec(v: list[float]) -> str:
    """asyncpg has no pgvector codec - send a literal and cast in SQL."""
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


# ────────────────────────────── ingest ──────────────────────────────

async def ingest_file(path: str, config_name: str = "default",
                      force: bool = False, campaign_id: int | None = None,
                      on_progress=None) -> dict:
    """on_progress(event: dict) is awaited at each stage, if given.

    Stages: hashing, extracting, chunking, embedding (with done/total), saving.
    The CLI passes nothing and behaves exactly as before.
    """
    async def emit(**event):
        if on_progress:
            await on_progress(event)

    p = Path(path)
    await emit(stage="hashing")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    pool = await store.pool()

    existing = await pool.fetchrow(
        "SELECT id, content_hash, chunk_count FROM kb_documents "
        "WHERE config_name=$1 AND filename=$2", config_name, p.name)
    if existing and existing["content_hash"] == digest and not force:
        return {"file": p.name, "status": "unchanged", "chunks": existing["chunk_count"]}

    # pymupdf4llm and the chunker are CPU-bound and hold the GIL for seconds on a
    # 50-page document. On the CLI that only makes the prompt wait; inside the
    # admin API it would freeze every other request for the length of an upload.
    await emit(stage="extracting")
    pages, n_pages = await asyncio.to_thread(extract_pdf, str(p))
    if not pages:
        return {"file": p.name, "status": "empty",
                "error": "no extractable text - scanned PDF? OCR would be needed"}

    await emit(stage="chunking", pages=n_pages)
    title = p.stem.replace("_", " ").replace("-", " ")
    chunks = await asyncio.to_thread(chunk_markdown, pages, title)
    if not chunks:
        return {"file": p.name, "status": "empty", "error": "no chunks produced"}

    await emit(stage="embedding", done=0, total=len(chunks))
    vectors = await embed(
        [c.embed_text for c in chunks],
        on_batch=lambda done, total: emit(stage="embedding", done=done, total=total),
    )
    await emit(stage="saving", chunks=len(chunks))

    # one transaction: a failed ingest leaves the previous version intact
    # rather than a half-updated KB
    async with pool.acquire() as conn:
        async with conn.transaction():
            if existing:
                doc_id = existing["id"]
                await conn.execute("DELETE FROM kb_chunks WHERE doc_id=$1", doc_id)
                # COALESCE so a CLI re-ingest (which passes no campaign_id) does
                # not orphan a document the panel had already scoped to a tenant
                await conn.execute(
                    "UPDATE kb_documents SET content_hash=$2, page_count=$3, "
                    "chunk_count=$4, title=$5, campaign_id=COALESCE($6, campaign_id), "
                    "updated_at=now() WHERE id=$1",
                    doc_id, digest, n_pages, len(chunks), title, campaign_id)
            else:
                doc_id = await conn.fetchval(
                    "INSERT INTO kb_documents (config_name, filename, title, "
                    "content_hash, page_count, chunk_count, campaign_id) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
                    config_name, p.name, title, digest, n_pages, len(chunks),
                    campaign_id)
            await conn.executemany(
                "INSERT INTO kb_chunks (doc_id, config_name, campaign_id, seq, page, "
                "heading, content, n_tokens, embedding) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::vector)",
                [(doc_id, config_name, campaign_id, c.seq, c.page, c.heading,
                  c.content, c.n_tokens, _vec(v)) for c, v in zip(chunks, vectors)])

    return {"file": p.name, "status": "updated" if existing else "created",
            "pages": n_pages, "chunks": len(chunks),
            "tokens": sum(c.n_tokens for c in chunks)}


# ────────────────────────────── retrieval ──────────────────────────────

async def search(query: str, config_name: str = "default",
                 top_k: int = 3, min_score: float = 0.25) -> list[dict]:
    """Hybrid: cosine similarity + trigram word similarity.

    word_similarity, NOT similarity: the latter compares whole strings, so a
    250-token chunk against a 6-word question scores near zero and the lexical
    leg never fires. word_similarity asks how well the query matches SOME PART
    of the chunk - which is the actual question.
    """
    if not query.strip():
        return []
    qvec = _vec((await embed([query]))[0])
    rows = await (await store.pool()).fetch(
        """
        WITH vec AS (
            SELECT id, doc_id, page, heading, content,
                   1 - (embedding <=> $1::vector) AS score, 'vec' AS src
              FROM kb_chunks
             WHERE config_name = $2 AND embedding IS NOT NULL
             ORDER BY embedding <=> $1::vector
             LIMIT $3
        ), lex AS (
            SELECT id, doc_id, page, heading, content,
                   word_similarity($4, content) AS score, 'lex' AS src
              FROM kb_chunks
             WHERE config_name = $2 AND word_similarity($4, content) > $5
             ORDER BY word_similarity($4, content) DESC
             LIMIT $3
        ), merged AS (SELECT * FROM vec UNION ALL SELECT * FROM lex)
        SELECT DISTINCT ON (id) id, doc_id, page, heading, content, score, src
          FROM merged ORDER BY id, score DESC
        """,
        qvec, config_name, top_k * 2, query, LEX_THRESHOLD)

    hits = [dict(r) for r in rows if r["score"] is not None and r["score"] >= min_score]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


def format_context(hits: list[dict]) -> str:
    if not hits:
        return ""
    return "\n\n".join(
        f"[{i}]{' (' + h['heading'] + ')' if h.get('heading') else ''}\n{h['content']}"
        for i, h in enumerate(hits, 1))


# ────────────────────────────── inline context ──────────────────────────────

async def load_inline(config_name: str = "default",
                      max_tokens: int = 6000) -> tuple[str, int, str]:
    """Build the always-in-prompt knowledge layer.

    Small KBs go into the prompt whole. That removes the per-turn embedding call
    (measured 390-1244 ms) AND the whole class of retrieval misses - the model
    sees everything and picks for itself.

    Above the budget we fall back to an index of document titles and headings.
    The model still needs to know WHAT is searchable, otherwise it either never
    calls the tool or calls it on every turn.

    -> (text, n_tokens, mode)   mode is "full" | "index" | "empty"
    """
    rows = await (await store.pool()).fetch(
        """SELECT d.title, c.heading, c.content, c.n_tokens
             FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
            WHERE c.config_name = $1 AND d.enabled
            ORDER BY d.filename, c.seq""", config_name)
    if not rows:
        return "", 0, "empty"

    total = sum(r["n_tokens"] or 0 for r in rows)
    if total <= max_tokens:
        parts, last = [], None
        for r in rows:
            if r["title"] != last:
                parts.append(f"\n## {r['title']}")
                last = r["title"]
            head = f"### {r['heading']}\n" if r["heading"] else ""
            parts.append(f"{head}{r['content']}")
        return "\n\n".join(parts).strip(), total, "full"

    seen, lines, last = set(), [], None
    for r in rows:
        if r["title"] != last:
            lines.append(f"\n## {r['title']}")
            last = r["title"]
        h = r["heading"]
        if h and h not in seen:
            seen.add(h)
            lines.append(f"  - {h}")
    idx = "\n".join(lines).strip()
    return idx, ntok(idx), "index"
