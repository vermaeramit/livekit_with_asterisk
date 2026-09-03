from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import alerting, db, postback
from .config import settings
from .routers import (agent_config, alerts, analytics, auth, calls,
                      diallers, gaps,
                      rates, roles,
                      campaigns, kb, kb_sources, live,
                      provider_keys, system, tenants,
                      tools, users)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("admin-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    log.info("db pool ready")

    # In-process rather than a separate service: it needs this pool and nothing
    # else, and another process would be one more thing to notice had died.
    # NOTE: assumes a single API instance. Two replicas would evaluate the same
    # rules twice and double-fire every webhook.
    evaluator = asyncio.create_task(alerting.run_forever())

    # Same reasoning, and the same caveat. Delivery cannot live in the agent:
    # its job process exits when the call ends, so it can write the row but
    # never retry it. This service is still here a minute later.
    postback.start()
    try:
        yield
    finally:
        evaluator.cancel()
        with suppress(asyncio.CancelledError):
            await evaluator
        await postback.stop()
        await db.disconnect()


app = FastAPI(
    title="AI Voice Admin API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(calls.router, prefix="/api")
app.include_router(campaigns.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(live.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(gaps.router, prefix="/api")
app.include_router(rates.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(diallers.router, prefix="/api")
app.include_router(kb.router, prefix="/api")
app.include_router(kb_sources.router, prefix="/api")
app.include_router(agent_config.router, prefix="/api")
app.include_router(tenants.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(provider_keys.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(system.router, prefix="/api")


@app.get("/api/health")
async def health():
    """Liveness + a real database round trip, so a dead pool fails the check."""
    await db.pool().fetchval("SELECT 1")
    return {"status": "ok"}
