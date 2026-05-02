"""Manager agent — previews the project and narrates the build flow for users."""

from __future__ import annotations

from typing import Any

from forge.agents.base import BaseAgent
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.config import Settings
from forge.core.events import SwarmEvent
from forge.core.message_bus import MessageBus


class ManagerAgent(BaseAgent):
    """Central coordinator for CLI build: project summary and user-facing copy."""

    agent_name = "manager"

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        """Manager is CLI-orchestrated; bus-driven handling is a no-op for now."""

        del event
        return None

    async def health_check(self) -> dict[str, Any]:
        return {**self.default_health_status(), "role": "manager"}

    def format_project_preview(self, scan: CodebaseScanResult) -> str:
        """Human-readable summary so the user can confirm the scan."""

        lines = [
            "Project scan summary (from Librarian)",
            "-------------------------------------",
            f"Path:        {scan.project_path}",
            f"Language:    {scan.language}",
            f"Framework:   {scan.framework or 'unknown'}",
            f"Entry:       {scan.entry_point or 'unknown'}",
            f"Port:        {scan.port}",
            f"Services:    {scan.service_count} (heuristic count — confirm below)",
            f"Has infra:   {scan.has_existing_infra}",
            f"Confidence:  {scan.confidence:.2f}",
        ]
        if scan.env_vars:
            lines.append(f"Env vars:    {', '.join(scan.env_vars)}")
        if scan.detected_infra:
            lines.append(f"Detected:    {', '.join(scan.detected_infra)}")
        if scan.evidence:
            lines.append("Evidence (sample):")
            for ev in scan.evidence[:5]:
                lines.append(f"  - {ev}")
        return "\n".join(lines) + "\n"
