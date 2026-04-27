from __future__ import annotations

import re

from pydantic import BaseModel, Field


class SmokeCheck(BaseModel):
    """Single sandbox validation check."""

    name: str = Field(description="Human-readable check name.")
    passed: bool = Field(description="Whether the check succeeded.")
    details: str = Field(description="Reason for the outcome.")


class SmokeTestSummary(BaseModel):
    """Aggregate result of sandbox smoke validation."""

    passed: bool = Field(description="Whether all checks passed.")
    checks: list[SmokeCheck] = Field(
        default_factory=list,
        description="Individual smoke-test checks.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Supporting evidence collected during validation.",
    )


def run_smoke_tests(
    *,
    manifests: dict[str, str],
    expected_port: int | None = None,
    pod_status: dict[str, str] | None = None,
    pod_logs: str | None = None,
    events: list[dict[str, str]] | None = None,
) -> SmokeTestSummary:
    """Validate generated artifacts and optional sandbox runtime observations."""

    checks: list[SmokeCheck] = []
    evidence: list[str] = []

    deployment_manifest = manifests.get("deployment.yaml")
    service_manifest = manifests.get("service.yaml")

    checks.append(
        SmokeCheck(
            name="deployment_manifest_present",
            passed=deployment_manifest is not None,
            details=(
                "deployment.yaml present."
                if deployment_manifest
                else "deployment.yaml missing."
            ),
        )
    )
    checks.append(
        SmokeCheck(
            name="service_manifest_present",
            passed=service_manifest is not None,
            details="service.yaml present." if service_manifest else "service.yaml missing.",
        )
    )

    if expected_port is not None:
        deployment_port_ok = deployment_manifest is not None and (
            f"containerPort: {expected_port}" in deployment_manifest
        )
        service_port_ok = service_manifest is not None and (
            f"targetPort: {expected_port}" in service_manifest
            or f"port: {expected_port}" in service_manifest
        )
        checks.append(
            SmokeCheck(
                name="deployment_port_matches",
                passed=deployment_port_ok,
                details=(
                    f"Deployment exposes expected port {expected_port}."
                    if deployment_port_ok
                    else f"Deployment is missing expected port {expected_port}."
                ),
            )
        )
        checks.append(
            SmokeCheck(
                name="service_port_matches",
                passed=service_port_ok,
                details=(
                    f"Service routes expected port {expected_port}."
                    if service_port_ok
                    else f"Service is missing expected port {expected_port}."
                ),
            )
        )

    if pod_status is not None:
        phase = pod_status.get("phase", "")
        ready = pod_status.get("ready", "")
        restart_count = _parse_int(pod_status.get("restart_count"))
        pod_running = phase in {"Running", "Succeeded"}
        ready_ok = _ready_count_ok(ready)
        restart_ok = restart_count <= 1
        checks.extend(
            [
                SmokeCheck(
                    name="pod_phase_healthy",
                    passed=pod_running,
                    details=f"Pod phase is {phase or 'unknown'}.",
                ),
                SmokeCheck(
                    name="pod_ready",
                    passed=ready_ok,
                    details=f"Pod readiness is {ready or 'unknown'}.",
                ),
                SmokeCheck(
                    name="pod_restart_count",
                    passed=restart_ok,
                    details=f"Pod restart count is {restart_count}.",
                ),
            ]
        )
        evidence.append(
            f"Observed pod phase={phase or 'unknown'}, ready={ready or 'unknown'}, "
            f"restart_count={restart_count}."
        )

    if pod_logs is not None:
        suspicious_pattern = re.compile(r"(?i)traceback|exception|panic|fatal")
        logs_ok = suspicious_pattern.search(pod_logs) is None
        checks.append(
            SmokeCheck(
                name="pod_logs_clean",
                passed=logs_ok,
                details=(
                    "Pod logs do not contain obvious failure markers."
                    if logs_ok
                    else "Pod logs contain failure markers."
                ),
            )
        )
        evidence.append("Collected pod logs for smoke validation.")

    if events is not None:
        warnings = [event for event in events if event.get("type") == "Warning"]
        checks.append(
            SmokeCheck(
                name="cluster_events_clear",
                passed=not warnings,
                details=(
                    "No warning events observed."
                    if not warnings
                    else f"Observed {len(warnings)} warning event(s)."
                ),
            )
        )
        evidence.append(f"Collected {len(events)} namespace event(s).")

    passed = all(check.passed for check in checks)
    return SmokeTestSummary(
        passed=passed,
        checks=checks,
        evidence=evidence,
    )


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _ready_count_ok(ready: str) -> bool:
    if "/" not in ready:
        return False
    actual, expected = ready.split("/", maxsplit=1)
    try:
        return int(actual) == int(expected) and int(expected) > 0
    except ValueError:
        return False
