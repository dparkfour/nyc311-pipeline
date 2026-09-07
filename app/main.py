"""FastAPI application.

FastAPI over Flask for one concrete reason: `/docs` is a real, browsable
OpenAPI spec generated from the route signatures, and it is a second artifact
a screener can open without being asked to.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.api.routes import router
from app.config import settings

DESCRIPTION = """
A live data pipeline over the **NYC 311 Service Requests** feed.

Every fifteen minutes a scheduled job pulls records that changed upstream,
runs them through a validation and deduplication layer, and writes the result
here. What makes this more than a CRUD app is that **the data-quality metrics
are part of the product**: `/api/quality` reports what the last run rejected,
flagged and collapsed, per rule.

**Watermarking note.** 311 records are mutable -- a request created Monday is
closed on Thursday. This pipeline watermarks on Socrata's `:updated_at` system
field and upserts on `unique_key`, so revisions are seen. Watermarking on
`created_date` instead, which is the intuitive choice, ingests every record
once in its incomplete state and never sees an update.

Hosted on free-tier infrastructure. The first request after a quiet period
wakes the server and can take up to a minute.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is applied on boot. Every statement is IF NOT EXISTS, so this is
    # a no-op on a warm database and removes "forgot to run migrations" as a
    # deploy failure mode.
    try:
        db.migrate()
    except Exception as exc:  # noqa: BLE001
        # A database that is briefly unreachable must not take the API down --
        # /api/health reports the truth and the next request retries.
        print(f"startup: migration skipped -- {exc}")
    yield


app = FastAPI(
    title="NYC 311 Live Pipeline",
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
def root():
    return JSONResponse(
        {
            "service": "nyc311-pipeline",
            "docs": "/docs",
            "quality_panel": "/api/quality",
            "health": "/api/health",
        }
    )
