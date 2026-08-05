"""Tiny checkout-api used as the dry-run subject.

Behavior is driven entirely by environment variables so a ProposedFix can
restore health by overriding knobs — no code patch required for the common
case. Intentionally broken defaults (PAYMENT_TIMEOUT_MS=50) reproduce the
seeded incident when the swarm launches the sandbox without a fix.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="checkout-api", version="0.1.0")

# Counters exposed as Prometheus text for /metrics. Not scraped by the swarm's
# Prometheus during dry-run; they exist so the smoke checks can assert health.
_REQUESTS_OK = 0
_REQUESTS_ERR = 0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _config() -> dict[str, Any]:
    return {
        "payment_timeout_ms": _env_int("PAYMENT_TIMEOUT_MS", 2000),
        "feature_checkout_v2": _env_bool("FEATURE_CHECKOUT_V2", False),
        "max_retries": _env_int("MAX_RETRIES", 3),
    }


def _is_healthy(cfg: dict[str, Any]) -> bool:
    """Contract the dry-run smoke test encodes.

    - PAYMENT_TIMEOUT_MS must be at least 500 (anything lower times out and 5xxs).
    - FEATURE_CHECKOUT_V2 must be off (the v2 path is not backed).
    - MAX_RETRIES must be at least 1.
    """

    return (
        cfg["payment_timeout_ms"] >= 500
        and not cfg["feature_checkout_v2"]
        and cfg["max_retries"] >= 1
    )


@app.get("/healthz")
def healthz() -> JSONResponse:
    cfg = _config()
    if not _is_healthy(cfg):
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "config": cfg},
        )
    return JSONResponse(content={"status": "ok", "config": cfg})


@app.post("/checkout")
def checkout() -> JSONResponse:
    global _REQUESTS_OK, _REQUESTS_ERR
    cfg = _config()
    if not _is_healthy(cfg):
        _REQUESTS_ERR += 1
        return JSONResponse(
            status_code=500,
            content={
                "error": "checkout_failed",
                "detail": "misconfigured payment/timeout or unsupported feature flag",
                "config": cfg,
            },
        )
    # Simulate a successful payment under a sane timeout.
    time.sleep(min(0.01, cfg["payment_timeout_ms"] / 1000.0))
    _REQUESTS_OK += 1
    return JSONResponse(content={"status": "paid", "retries_allowed": cfg["max_retries"]})


@app.get("/metrics")
def metrics() -> Response:
    body = (
        "# HELP http_requests_total Total HTTP requests.\n"
        "# TYPE http_requests_total counter\n"
        f'http_requests_total{{service="checkout-api",status="2xx"}} {_REQUESTS_OK}\n'
        f'http_requests_total{{service="checkout-api",status="5xx"}} {_REQUESTS_ERR}\n'
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "checkout-api", "docs": "/docs"}
