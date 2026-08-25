"""Knowledge-base documents, managed per campaign.

Ingestion runs in this process. The heavy parts are already off the event loop
(kb.ingest_file offloads extraction and chunking to a thread), so an upload does
not freeze the console for everyone else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import (APIRouter, Depends, File, HTTPException, Query, UploadFile,
                     status)
from fastapi.responses import StreamingResponse

from .. import audit, db, provider_keys as pk, kblib

# Read from the ingester rather than repeated here: a second list would let the
# upload accept a format nothing downstream can open, and the failure would land
# on someone who had already waited for a 30 MB file.
#
# Guarded through available(), not kb() - that raises when the mount is missing,
# and raising at import time takes the whole API down over a knowledge base
# feature nobody may be using.
SUPPORTED_EXT: tuple[str, ...] = (
    tuple(getattr(kblib.kb(), "SUPPORTED", (".pdf",)))
    if kblib.available() else (".pdf", ".docx"))
from ..deps import CurrentUser, active_user, assert_campaign_visible, require_roles
from ..schemas import KbDocument, KbIngestResult

log = logging.getLogger("admin-api")

router = APIRouter(tags=["knowledge base"])

editor = require_roles("tenant_admin")

# Uploaded files are kept so a document can be re-ingested after a chunking
# change without asking the client for the PDF again.
STORE_DIR = Path(os.getenv("KB_STORE_DIR", "/data/kb"))

MAX_BYTES = int(os.getenv("KB_MAX_UPLOAD_BYTES", str(30 * 1024 * 1024)))
CHUNK_READ = 1024 * 1024

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Reduce a client-supplied filename to something safe to join to a path.

    Path traversal is the obvious risk, but a bare '..' or '' after stripping is
    just as bad - it would resolve to the campaign directory itself.
    """
    base = Path(name).name                      # drops any directory component
    base = _SAFE.sub("_", base).strip("._")
    if not base or base in {".", ".."}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unusable filename")
    if not base.lower().endswith(SUPPORTED_EXT):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"only {' and '.join(SUPPORTED_EXT)} files are supported")
    return base[:200]


SELECT_DOC = """
    SELECT d.id, d.campaign_id, d.config_name, d.filename, d.title,
           d.page_count, d.chunk_count, d.language, d.enabled,
           d.created_at, d.updated_at,
           (SELECT COALESCE(sum(k.n_tokens), 0) FROM kb_chunks k WHERE k.doc_id = d.id)
               AS token_count
      FROM kb_documents d
"""


async def _doc_or_404(user: CurrentUser, doc_id: int) -> dict:
    row = await db.pool().fetchrow(
        """SELECT d.id, d.campaign_id, d.filename, d.config_name, d.enabled,
                  c.tenant_id
             FROM kb_documents d LEFT JOIN campaigns c ON c.id = d.campaign_id
            WHERE d.id = $1""", doc_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if not user.is_superadmin and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return dict(row)


@router.get("/campaigns/{campaign_id}/kb", response_model=list[KbDocument])
async def list_documents(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        SELECT_DOC + " WHERE d.campaign_id = $1 ORDER BY d.filename", campaign_id)
    return [KbDocument(**dict(r)) for r in rows]


@router.post("/campaigns/{campaign_id}/kb")
async def upload_document(campaign_id: int,
                          file: UploadFile = File(...),
                          force: bool = Query(False, description="re-ingest an unchanged file"),
                          actor: CurrentUser = Depends(editor)):
    """Upload and ingest a PDF, streaming progress as newline-delimited JSON.

    Validation happens before the stream starts, so a bad request is still a
    normal 400/413/409 with a JSON body. Once ingestion begins the status is
    already 200 and there is no way back, so failures after that point arrive as
    a {"stage": "error"} line.

    The alternative was a plain JSON response, which left the console on an
    opaque spinner for the whole embed - long enough to read as a hang.
    """
    if not kblib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"ingestion is not configured: {kblib.why_unavailable()}")

    tenant_id = await assert_campaign_visible(actor, campaign_id)
    cfg = await db.pool().fetchrow(
        "SELECT name FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1",
        campaign_id)
    if cfg is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this campaign has no agent config to attach documents to")

    # Embedding is billed to whoever owns the documents, so it runs on the
    # client's OpenAI key - the same one their calls use. Refused up front
    # rather than half way through a 200-chunk ingest, where the partial work
    # would already have been paid for by somebody.
    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get("openai"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no OpenAI key is set for this campaign or its client - "
            "embedding cannot be billed to anyone")

    filename = safe_filename(file.filename or "")
    dest_dir = STORE_DIR / str(campaign_id)
    staging_root = dest_dir / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    # Ingest from a staging copy and only publish it once that succeeds. Writing
    # straight to the final path would destroy the previous good file whenever a
    # replacement turns out to be unreadable - and leave a file behind for an
    # upload that never produced a document.
    #
    # The staging directory is per-request, and the file inside keeps its real
    # name because ingest_file() takes the document name from the path.
    staging_dir = Path(tempfile.mkdtemp(dir=staging_root))
    staged = staging_dir / filename
    written = 0
    try:
        # Size is checked as the bytes arrive. Buffering the whole upload first
        # would let one request decide how much RAM this container needs.
        with staged.open("wb") as out:
            while chunk := await file.read(CHUNK_READ):
                written += len(chunk)
                if written > MAX_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
                out.write(chunk)

        if written == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "the file is empty")
        # Trust the bytes, not the extension
        with staged.open("rb") as fh:
            head = fh.read(5)
        if base.lower().endswith(".pdf"):
            if head[:5] != b"%PDF-":
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "this does not look like a PDF")
        else:
            # .docx is a zip. The magic bytes alone would also accept any other
            # zip, so the member list is checked too - an .xlsx renamed to .docx
            # gets past the extension and past PK, and would otherwise fail
            # much later with something unreadable.
            if head[:2] != b"PK":
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "this does not look like a Word file")
            try:
                with zipfile.ZipFile(staged) as z:
                    if "word/document.xml" not in z.namelist():
                        raise KeyError
            except (zipfile.BadZipFile, KeyError):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "this is a zip but not a Word document - .doc and .xlsx "
                    "are not supported, save it as .docx")
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    async def stream():
        result: dict | None = None
        try:
            queue: asyncio.Queue[dict] = asyncio.Queue()

            async def on_progress(event: dict) -> None:
                await queue.put(event)

            task = asyncio.create_task(kblib.kb().ingest_file(
                str(staged), config_name=cfg["name"], force=force,
                campaign_id=campaign_id, on_progress=on_progress,
                api_key=keys["openai"]))

            # Drain progress events until ingestion finishes. Waiting on the
            # queue with a timeout, rather than on the task alone, keeps a slow
            # stage from starving the connection - some proxies and browsers
            # give up on a response that produces nothing for long enough.
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    event = {"stage": "working"}
                yield json.dumps(event) + "\n"

            result = await task

        except KeyError as e:
            # kb.py reads OPENAI_API_KEY lazily, on the first embedding call
            yield json.dumps({"stage": "error",
                              "message": f"ingestion is not configured: {e} is not set"}) + "\n"
        except Exception as e:
            log.exception("ingestion failed for %s", filename)
            yield json.dumps({"stage": "error",
                              "message": f"{type(e).__name__}: {e}"}) + "\n"
        finally:
            # Publish the file only when it actually produced a document, then
            # drop the staging copy either way.
            if result and result.get("status") != "empty" and staged.exists():
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(dest_dir / filename))
            shutil.rmtree(staging_dir, ignore_errors=True)

        if result is not None:
            await audit.record(
                actor, entity="kb_document", entity_id=filename,
                action=result.get("status", "ingest"),
                tenant_id=tenant_id, campaign_id=campaign_id,
                changes={"chunks": {"from": None, "to": result.get("chunks")}})
            yield json.dumps({"stage": "done", "filename": filename, **result}) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        # nginx buffers proxied responses by default, which would hold every
        # progress line back until the end and defeat the whole point.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@router.post("/kb/documents/{doc_id}/reingest", response_model=KbIngestResult)
async def reingest_document(doc_id: int, actor: CurrentUser = Depends(editor)):
    if not kblib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"ingestion is not configured: {kblib.why_unavailable()}")

    doc = await _doc_or_404(actor, doc_id)
    path = STORE_DIR / str(doc["campaign_id"]) / doc["filename"]
    if not path.is_file():
        # Documents ingested before the panel existed were dropped in
        # /opt/aivoice/kb/inbox and never copied here.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the original file is not stored - upload it again")

    try:
        result = await kblib.kb().ingest_file(
            str(path), config_name=doc["config_name"], force=True,
            campaign_id=doc["campaign_id"])
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"ingestion failed: {type(e).__name__}: {e}")

    await audit.record(actor, entity="kb_document", entity_id=doc["filename"],
                       action="reingest", tenant_id=doc["tenant_id"],
                       campaign_id=doc["campaign_id"])
    return KbIngestResult(**{"filename": doc["filename"], **result})


@router.patch("/kb/documents/{doc_id}", response_model=KbDocument)
async def set_enabled(doc_id: int, enabled: bool,
                      actor: CurrentUser = Depends(editor)):
    doc = await _doc_or_404(actor, doc_id)
    await db.pool().execute(
        "UPDATE kb_documents SET enabled = $2, updated_at = now() WHERE id = $1",
        doc_id, enabled)
    await audit.record(actor, entity="kb_document", entity_id=doc["filename"],
                       action="enable" if enabled else "disable",
                       tenant_id=doc["tenant_id"], campaign_id=doc["campaign_id"])
    row = await db.pool().fetchrow(SELECT_DOC + " WHERE d.id = $1", doc_id)
    return KbDocument(**dict(row))


@router.delete("/kb/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: int, actor: CurrentUser = Depends(editor)):
    doc = await _doc_or_404(actor, doc_id)
    # kb_chunks has ON DELETE CASCADE from kb_documents
    await db.pool().execute("DELETE FROM kb_documents WHERE id = $1", doc_id)
    (STORE_DIR / str(doc["campaign_id"]) / doc["filename"]).unlink(missing_ok=True)
    await audit.record(actor, entity="kb_document", entity_id=doc["filename"],
                       action="delete", tenant_id=doc["tenant_id"],
                       campaign_id=doc["campaign_id"])


@router.get("/kb/documents/{doc_id}/chunks")
async def list_chunks(doc_id: int, user: CurrentUser = Depends(active_user),
                      limit: int = Query(100, ge=1, le=500)):
    """What the agent will actually retrieve, in order.

    Worth looking at after an ingest: a PDF that extracted badly produces
    chunks that read as nonsense, and that is far easier to see here than to
    diagnose from a bad answer on a live call.
    """
    await _doc_or_404(user, doc_id)
    rows = await db.pool().fetch(
        """SELECT id, seq, page, heading, content, n_tokens
             FROM kb_chunks WHERE doc_id = $1 ORDER BY seq LIMIT $2""",
        doc_id, limit)
    return [dict(r) for r in rows]
