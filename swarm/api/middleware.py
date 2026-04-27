from __future__ import annotations

from fastapi import FastAPI


def register_middleware(app: FastAPI) -> FastAPI:
    """Register API middleware. No custom middleware is required in Sprint 1."""

    return app
