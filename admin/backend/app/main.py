from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .routers import agent_config, auth, calls, campaigns, kb, tenants, users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("admin-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    log.info("db pool ready")
    yield
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
app.include_router(kb.router, prefix="/api")
app.include_router(agent_config.router, prefix="/api")
app.include_router(tenants.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.get("/api/health")
async def health():
    """Liveness + a real database round trip, so a dead pool fails the check."""
    await db.pool().fetchval("SELECT 1")
    return {"status": "ok"}
