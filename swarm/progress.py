"""Append-only run event stream.

Each graph node emits events here as it executes. The dashboard tails the file,
so what it renders is the actual sequence of state transitions rather than a
timer pretending to be progress.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NODE_SEQUENCE = [
    "monitoring_agent",
    "code_analysis_agent",
    "ops_agent",
    "dry_run_validate",
]

TERMINAL_NODES = {"no_incident", "ready_to_apply", "needs_human_review"}


class RunRecorder:
    """Writes newline-delimited JSON events for a single run."""

    def __init__(self, run_id: str, runs_dir: Path) -> None:
        self.run_id = run_id
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runs_dir / f"{run_id}.jsonl"
        self.path.touch(exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        """Append one event. Never raises into the graph."""

        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
        except OSError:
            pass

    def node_started(self, node: str) -> None:
        self.emit("node_started", node=node)

    def node_finished(self, node: str, duration_seconds: float, payload: Any = None) -> None:
        self.emit(
            "node_finished",
            node=node,
            duration_seconds=round(duration_seconds, 4),
            payload=_jsonable(payload),
        )

    def node_failed(self, node: str, error: str) -> None:
        self.emit("node_failed", node=node, error=error)

    def tool_invoked(self, tool: str, agent: str, ok: bool, duration_ms: float) -> None:
        self.emit(
            "tool_invoked",
            tool=tool,
            agent=agent,
            ok=ok,
            duration_ms=round(duration_ms, 2),
        )

    def log(self, message: str) -> None:
        self.emit("log", message=message)


class NullRecorder(RunRecorder):
    """Recorder that drops everything, for tests and library use."""

    def __init__(self) -> None:  # noqa: D107 - deliberately skips file setup
        self.run_id = "null"
        self.path = Path("/dev/null")

    def emit(self, event: str, **fields: Any) -> None:
        return None


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read every well-formed event from a run file."""

    events: list[dict[str, Any]] = []
    if not Path(path).exists():
        return events
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def iter_runs(runs_dir: Path) -> Iterator[Path]:
    """Yield run files, newest first."""

    directory = Path(runs_dir)
    if not directory.exists():
        return
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    yield from files


def node_statuses(events: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse an event list into a per-node status map for the UI."""

    statuses = {node: "pending" for node in NODE_SEQUENCE}
    for event in events:
        node = event.get("node")
        if not isinstance(node, str):
            continue
        if node not in statuses:
            statuses[node] = "pending"
        kind = event.get("event")
        if kind == "node_started":
            statuses[node] = "running"
        elif kind == "node_finished":
            statuses[node] = "done"
        elif kind == "node_failed":
            statuses[node] = "failed"
    return statuses
