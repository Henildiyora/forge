from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from forge.core.config import Settings
from forge.core.hardening import HardeningReport, hardening_store, run_hardening_suite
from forge.core.observability import WorkflowObservabilitySummary, observability_store

router = APIRouter(prefix="/forge", tags=["forge"])


class HardeningRunRequest(BaseModel):
    project_path: str = Field(
        description="Local project path used for deploy-oriented scenarios."
    )
    max_iterations: int = Field(default=3, ge=1, le=10)


@router.get("/capabilities")
async def capabilities() -> dict[str, list[str]]:
    return {
        "workflows": ["deploy", "incident", "build_conversation"],
        "agents": [
            "librarian",
            "captain",
            "docker_specialist",
            "k8s_specialist",
            "cicd_specialist",
            "serverless_specialist",
            "platform_specialist",
            "watchman",
            "remediation",
            "sandbox_tester",
            "cloud_specialist",
        ],
    }


@router.get("/observability", response_model=WorkflowObservabilitySummary)
async def observability() -> WorkflowObservabilitySummary:
    return observability_store.summary()


@router.get("/hardening/latest", response_model=HardeningReport | None)
async def latest_hardening_report() -> HardeningReport | None:
    return hardening_store.latest()


@router.post("/hardening/run", response_model=HardeningReport)
async def run_hardening(request: HardeningRunRequest) -> HardeningReport:
    project_path = Path(request.project_path).expanduser()
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail="project_path must point to an existing directory",
        )
    return await run_hardening_suite(
        settings=Settings(),
        project_path=project_path,
        max_iterations=request.max_iterations,
    )
