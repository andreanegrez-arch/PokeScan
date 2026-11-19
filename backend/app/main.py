import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import CONNECTION_HINT, create_pool
from app.routers import cards, scan

log = logging.getLogger("pokescan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The pool connects in the background: the API starts even if PostgreSQL
    # is down, and requests that need it answer 503 until it is back.
    app.state.pool = create_pool()
    app.state.pool.open(wait=False)
    try:
        yield
    finally:
        app.state.pool.close()


app = FastAPI(title="PokeScan API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scan.router)
app.include_router(cards.router)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, error: psycopg.OperationalError) -> JSONResponse:
    log.error("%s\n%s", CONNECTION_HINT, error)
    return JSONResponse(status_code=503, content={"detail": "Database not reachable"})


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
