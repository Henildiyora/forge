"""Code Analysis Agent: correlate the incident window to recent commits.

This agent depends on the Monitoring Agent's output. It reads
``state.incident.start_timestamp`` off the shared graph state and narrows its
commit search to a window around that moment, which is the whole point of
running these agents on one state object instead of as three isolated calls.

Ranking is an explicit heuristic, not a learned model. The formula is in
:func:`score_commit` and every score ships with the reasons behind it.
"""

from __future__ import annotations

from datetime import timedelta

from swarm.schemas import CommitCandidate, IncidentSignal, SwarmConfig, SwarmState, ToolCall, ToolResult
from swarm.tools.github import CommitRecord, CommitsInWindowArgs, CommitsInWindowResult
from swarm.tools.registry import ToolRegistry

AGENT_NAME = "code_analysis_agent"

# Weights of the three heuristic signals. They sum to 1.0.
WEIGHT_PROXIMITY = 0.55
WEIGHT_PATH_OVERLAP = 0.35
WEIGHT_CONFIG_TOUCH = 0.10

_CONFIG_HINTS = (
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".env",
    "config",
    "settings",
    "Dockerfile",
    "values",
)


def _proximity_score(minutes_before: float, config: SwarmConfig) -> tuple[float, str]:
    """Score how suspiciously close a commit is to the incident start.

    Commits landing shortly before the incident score highest. Commits after
    the incident began are heavily discounted but not zeroed, since a deploy
    can trail its commit.
    """

    if minutes_before >= 0:
        window = max(config.commit_window_before_minutes, 1)
        score = max(0.0, 1.0 - (minutes_before / window))
        return score, f"landed {minutes_before:.0f} min before the incident started"

    minutes_after = -minutes_before
    window = max(config.commit_window_after_minutes, 1)
    score = 0.25 * max(0.0, 1.0 - (minutes_after / window))
    return score, f"landed {minutes_after:.0f} min after the incident started"


def _path_overlap_score(files: list[str], service_paths: list[str]) -> tuple[float, str]:
    """Fraction of touched files that sit inside a known service path."""

    if not service_paths:
        return 0.5, "no service paths configured, path signal treated as neutral"
    if not files:
        return 0.0, "commit touched no files we could resolve"

    matched = [
        path
        for path in files
        if any(path.startswith(prefix) or prefix in path for prefix in service_paths)
    ]
    score = len(matched) / len(files)
    if matched:
        preview = ", ".join(matched[:3])
        return score, f"{len(matched)}/{len(files)} files in service paths ({preview})"
    return 0.0, f"none of {len(files)} files are in the service paths"


def _config_touch_score(files: list[str]) -> tuple[float, str]:
    """Small bonus for touching configuration, a common source of runtime breakage."""

    hits = [path for path in files if any(hint in path for hint in _CONFIG_HINTS)]
    if not hits:
        return 0.0, ""
    return 1.0, f"changed configuration ({', '.join(hits[:3])})"


def score_commit(
    record: CommitRecord, incident: IncidentSignal, config: SwarmConfig
) -> CommitCandidate:
    """Turn a raw commit into a scored candidate.

    ``relevance = 0.55*proximity + 0.35*path_overlap + 0.10*config_touch``
    """

    minutes_before = (incident.start_timestamp - record.timestamp).total_seconds() / 60.0

    proximity, proximity_reason = _proximity_score(minutes_before, config)
    overlap, overlap_reason = _path_overlap_score(record.files_changed, config.service_paths)
    config_touch, config_reason = _config_touch_score(record.files_changed)

    score = (
        WEIGHT_PROXIMITY * proximity
        + WEIGHT_PATH_OVERLAP * overlap
        + WEIGHT_CONFIG_TOUCH * config_touch
    )

    reasons = [proximity_reason, overlap_reason]
    if config_reason:
        reasons.append(config_reason)

    return CommitCandidate(
        sha=record.sha,
        author=record.author,
        message=record.message,
        files_changed=record.files_changed,
        timestamp=record.timestamp,
        relevance_score=min(1.0, max(0.0, score)),
        relevance_reasons=reasons,
        minutes_before_incident=minutes_before,
        url=record.url,
    )


class CodeAnalysisAgent:
    """Finds and ranks the commits most likely to have caused the incident."""

    name = AGENT_NAME

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def run(
        self, state: SwarmState
    ) -> tuple[list[CommitCandidate], list[ToolCall], list[ToolResult]]:
        """Execute the agent, returning ranked candidates and the tool audit trail."""

        incident = state.incident
        if incident is None:
            raise RuntimeError("code analysis requires an IncidentSignal in shared state")

        config = state.config
        since = incident.start_timestamp - timedelta(
            minutes=config.commit_window_before_minutes
        )
        until = incident.start_timestamp + timedelta(
            minutes=config.commit_window_after_minutes
        )

        call, result = self.registry.call(
            tool="github.commits_in_window",
            agent=self.name,
            arguments=CommitsInWindowArgs(
                repository=config.repository or "unknown/unknown",
                since=since,
                until=until,
            ),
        )
        if not result.ok:
            raise RuntimeError(f"commit lookup failed: {result.error}")

        payload = CommitsInWindowResult.model_validate(result.payload)
        candidates = [score_commit(record, incident, config) for record in payload.commits]
        candidates.sort(key=lambda item: item.relevance_score, reverse=True)
        return candidates, [call], [result]
