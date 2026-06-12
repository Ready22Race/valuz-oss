"""FastAPI application — entry point, lifespan, middleware, health check."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import AppConfig
from app.dependencies import init_dependencies, shutdown_dependencies
from app.mcp_toolkit_router import mcp_router_lifespan, mount_mcp_router
from app.routes.events import router as events_router
from app.routes.messages import router as messages_router
from app.routes.run import router as run_router
from app.routes.sessions import router as sessions_router
from app.routes.usage import router as usage_router

config = AppConfig()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_dependencies(config)
    async with mcp_router_lifespan():
        yield
    await shutdown_dependencies()


app = FastAPI(
    title="Agent Harness",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


if config.auth_token:

    @app.middleware("http")
    async def _require_bearer_token(request: Request, call_next: Any) -> Any:
        """Standalone-kernel auth: every route except /health requires the
        configured bearer token. The WS run channel enforces the same token
        inside its handler (HTTP middleware doesn't cover websockets)."""
        if request.url.path != "/health":
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {config.auth_token}":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(run_router)
app.include_router(events_router)
app.include_router(usage_router)
mount_mcp_router(app)
