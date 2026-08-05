"""Ops Agent: synthesize Monitoring + Code Analysis into a ProposedFix.

Never applies anything. It produces a structured proposal and hands it to the
dry-run node. When ``ANTHROPIC_API_KEY`` is set it uses Claude tool calling;
otherwise it falls back to a deterministic rule-based planner so the graph and
benchmark still run offline. Which path ran is recorded in ``ProposedFix.source``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from swarm.llm import AnthropicClient, LLMError
from swarm.schemas import (
    CommitCandidate,
    FixAction,
    FixActionKind,
    IncidentSignal,
    ProposedFix,
    RiskLevel,
    SwarmState,
)

AGENT_NAME = "ops_agent"

# Known knobs the sandbox target service understands. The heuristic planner
# maps common failure modes onto these; the LLM is free to propose others that
# the dry-run will then accept or reject based on real checks.
SERVICE_CONTRACT: dict[str, dict[str, str]] = {
    "PAYMENT_TIMEOUT_MS": {
        "healthy": "2000",
        "broken": "50",
        "description": "Upstream payment gateway timeout. Too low causes 5xx spikes.",
    },
    "FEATURE_CHECKOUT_V2": {
        "healthy": "false",
        "broken": "true",
        "description": "Checkout v2 feature flag. Enabling without backend support errors.",
    },
    "MAX_RETRIES": {
        "healthy": "3",
        "broken": "0",
        "description": "Retry budget for transient upstream failures.",
    },
}


class ProposeFixToolInput(BaseModel):
    """Schema forced onto the Anthropic tool call."""

    summary: str = Field(description="One-line description of the fix.")
    root_cause: str = Field(description="Stated cause grounded in the two prior agents.")
    actions: list[dict[str, Any]] = Field(
        description="List of {kind, target, value, reason} actions."
    )
    target_files: list[str] = Field(default_factory=list)
    referenced_commits: list[str] = Field(default_factory=list)
    risk_level: str = Field(description="low, medium, or high.")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """\
You are the Ops Agent in a DevOps Swarm. You receive an IncidentSignal and a
ranked list of CommitCandidates. Propose a concrete, machine-applicable fix.

Rules:
- Prefer env_override actions against known service knobs when the evidence
  points at a misconfiguration.
- Never invent SHAs that were not provided.
- Keep risk_level honest: changing production knobs is at least medium.
- Output only via the propose_fix tool.
"""


def _build_user_prompt(
    incident: IncidentSignal,
    candidates: list[CommitCandidate],
    runtime_env: dict[str, str],
    previous_failures: list[str],
) -> str:
    top = candidates[:5]
    body = {
        "incident": incident.model_dump(mode="json"),
        "top_commits": [c.model_dump(mode="json") for c in top],
        "runtime_env": runtime_env,
        "known_service_knobs": SERVICE_CONTRACT,
        "previous_dry_run_failures": previous_failures,
    }
    return (
        "Propose a fix for this incident. Prefer the smallest change that restores "
        "the healthy values of the known service knobs when evidence supports it.\n\n"
        + json.dumps(body, indent=2, default=str)
    )


def heuristic_propose(
    incident: IncidentSignal,
    candidates: list[CommitCandidate],
    runtime_env: dict[str, str],
    *,
    previous_failures: list[str] | None = None,
) -> ProposedFix:
    """Deterministic offline planner.

    Inspects runtime_env against SERVICE_CONTRACT and the top commit's message
    / files. Produces env_override actions that restore healthy values for any
    knobs that look broken.
    """

    previous_failures = previous_failures or []
    actions: list[FixAction] = []
    rationale: list[str] = []
    referenced = [c.sha for c in candidates[:3]]

    # Prefer knobs that the top-ranked commit's message or files mention.
    top = candidates[0] if candidates else None
    hints = " ".join(
        [
            (top.message if top else "").lower(),
            " ".join(top.files_changed if top else []).lower(),
            " ".join(incident.evidence).lower(),
            " ".join(previous_failures).lower(),
        ]
    )

    for knob, meta in SERVICE_CONTRACT.items():
        current = runtime_env.get(knob)
        healthy = meta["healthy"]
        broken = meta["broken"]
        mentioned = knob.lower() in hints or any(
            token in hints for token in knob.lower().split("_") if len(token) > 3
        )
        is_broken = current is not None and current == broken
        # Also treat "obviously wrong" values (e.g. timeout far below healthy).
        obviously_wrong = False
        if knob == "PAYMENT_TIMEOUT_MS" and current is not None:
            try:
                obviously_wrong = int(current) < int(healthy) // 2
            except ValueError:
                obviously_wrong = False

        if is_broken or (mentioned and current != healthy) or obviously_wrong:
            if current == healthy:
                continue
            actions.append(
                FixAction(
                    kind=FixActionKind.ENV_OVERRIDE,
                    target=knob,
                    value=healthy,
                    reason=(
                        f"{knob} is {current!r}; healthy value is {healthy!r}. "
                        f"{meta['description']}"
                    ),
                )
            )
            rationale.append(
                f"Restoring {knob} from {current!r} to {healthy!r} based on "
                f"severity severity={incident.severity.value} and commit hints."
            )

    if not actions:
        # Fallback: restore every known broken knob present in runtime_env.
        for knob, meta in SERVICE_CONTRACT.items():
            current = runtime_env.get(knob)
            if current == meta["broken"]:
                actions.append(
                    FixAction(
                        kind=FixActionKind.ENV_OVERRIDE,
                        target=knob,
                        value=meta["healthy"],
                        reason=f"Default restore of broken knob {knob}.",
                    )
                )
                rationale.append(f"No specific hint; restoring broken knob {knob}.")

    if not actions:
        # Last resort so dry-run still has something to evaluate: a no-op that
        # will pass if the service is already healthy, fail otherwise.
        actions.append(
            FixAction(
                kind=FixActionKind.ENV_OVERRIDE,
                target="MAX_RETRIES",
                value=runtime_env.get("MAX_RETRIES", SERVICE_CONTRACT["MAX_RETRIES"]["healthy"]),
                reason="No clear misconfiguration found; proposing a no-op retry restore.",
            )
        )
        rationale.append("No broken knobs detected; proposing a no-op for dry-run evaluation.")

    risk = RiskLevel.MEDIUM
    if incident.severity.value in {"high", "critical"}:
        risk = RiskLevel.HIGH

    confidence = 0.72 if top and top.relevance_score >= 0.5 else 0.55
    summary = (
        f"Restore misconfigured knobs for {incident.service} "
        f"({', '.join(a.target for a in actions)})"
    )
    return ProposedFix(
        summary=summary,
        root_cause=(
            f"{incident.metric_name} spiked {incident.spike_magnitude:.1f}x at "
            f"{incident.start_timestamp.isoformat()}"
            + (f"; top suspect commit {top.sha[:8]}: {top.message}" if top else "")
        ),
        actions=actions,
        target_files=list({f for c in candidates[:3] for f in c.files_changed}),
        referenced_commits=referenced,
        risk_level=risk,
        confidence=confidence,
        source="heuristic",
        rationale=rationale,
    )


def _coerce_fix(raw: dict[str, Any], *, source: str) -> ProposedFix:
    actions: list[FixAction] = []
    for item in raw.get("actions") or []:
        if not isinstance(item, dict):
            continue
        kind_raw = str(item.get("kind", FixActionKind.ENV_OVERRIDE.value))
        try:
            kind = FixActionKind(kind_raw)
        except ValueError:
            kind = FixActionKind.ENV_OVERRIDE
        actions.append(
            FixAction(
                kind=kind,
                target=str(item.get("target", "")),
                value=str(item.get("value", "")),
                reason=str(item.get("reason", "")),
            )
        )
    try:
        risk = RiskLevel(str(raw.get("risk_level", "medium")).lower())
    except ValueError:
        risk = RiskLevel.MEDIUM
    confidence = float(raw.get("confidence", 0.5))
    confidence = min(1.0, max(0.0, confidence))
    return ProposedFix(
        summary=str(raw.get("summary", "Proposed fix")),
        root_cause=str(raw.get("root_cause", "")),
        actions=actions,
        target_files=[str(x) for x in raw.get("target_files") or []],
        referenced_commits=[str(x) for x in raw.get("referenced_commits") or []],
        risk_level=risk,
        confidence=confidence,
        source=source,
        rationale=[str(x) for x in raw.get("rationale") or []],
    )


class OpsAgent:
    """Produces a ProposedFix from shared state; never applies it."""

    name = AGENT_NAME

    def __init__(self, llm: AnthropicClient | None = None) -> None:
        self.llm = llm

    def run(self, state: SwarmState) -> ProposedFix:
        """Plan a fix from IncidentSignal + CommitCandidate[]."""

        if state.incident is None:
            raise RuntimeError("ops agent requires an IncidentSignal in shared state")

        previous_failures = [
            r.rejection_reason or "dry-run failed"
            for r in state.dry_run_results
            if not r.passed
        ]

        if self.llm is not None:
            try:
                raw = self.llm.structured_call(
                    system=_SYSTEM_PROMPT,
                    user=_build_user_prompt(
                        state.incident,
                        state.commit_candidates,
                        state.config.runtime_env,
                        previous_failures,
                    ),
                    tool_name="propose_fix",
                    tool_description="Submit a concrete ProposedFix for dry-run validation.",
                    input_schema=ProposeFixToolInput.model_json_schema(),
                )
                try:
                    ProposeFixToolInput.model_validate(raw)
                except ValidationError as exc:
                    raise LLMError(f"tool payload failed validation: {exc}") from exc
                return _coerce_fix(raw, source="anthropic")
            except LLMError:
                # Fall through to heuristic rather than crashing the graph.
                pass

        return heuristic_propose(
            state.incident,
            state.commit_candidates,
            state.config.runtime_env,
            previous_failures=previous_failures,
        )
