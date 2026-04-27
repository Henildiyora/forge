from __future__ import annotations

from fastapi import FastAPI

from forge.api.middleware import register_middleware
from forge.api.routers import agents, approvals, health, slack_webhooks, swarm


def create_app() -> FastAPI:
    """Create the FORGE FastAPI application."""

    app = FastAPI(title="FORGE API", version="0.2.0")
    register_middleware(app)
    app.include_router(health.router)
    app.include_router(swarm.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(slack_webhooks.router, prefix="/api/v1")
    return app
