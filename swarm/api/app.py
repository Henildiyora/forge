from __future__ import annotations

from fastapi import FastAPI

from swarm.api.middleware import register_middleware
from swarm.api.routers import agents, approvals, health, swarm


def create_app() -> FastAPI:
    """Create the DevOps Swarm FastAPI application."""

    app = FastAPI(title="DevOps Swarm API", version="0.1.0")
    register_middleware(app)
    app.include_router(health.router)
    app.include_router(swarm.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    return app
