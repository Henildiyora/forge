from __future__ import annotations

from pathlib import Path

from swarm.agents.base import BaseAgent
from swarm.agents.librarian.ast_analyzer import ASTAnalyzer, CodebaseScanResult
from swarm.core.config import Settings
from swarm.core.events import EventType, SwarmEvent
from swarm.core.message_bus import MessageBus


class LibrarianAgent(BaseAgent):
    """Code intelligence agent responsible for repository scanning."""

    agent_name = "librarian"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        analyzer: ASTAnalyzer | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.analyzer = analyzer or ASTAnalyzer()

    async def analyze_codebase(self, project_path: str) -> CodebaseScanResult:
        """Analyze a repository path and return a structured scan result."""

        path = Path(project_path).expanduser().resolve()
        self.logger.info("codebase_scan_started", project_path=str(path))
        result = self.analyzer.analyze_project(path)
        self.logger.info(
            "codebase_scan_completed",
            project_path=str(path),
            language=result.language,
            framework=result.framework,
            entry_point=result.entry_point,
        )
        return result

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type != EventType.CODEBASE_SCAN_REQUESTED:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={
                    "error": "unsupported_event_type",
                    "received_type": event.type.value,
                },
                parent_event_id=event.id,
            )

        project_path = event.payload.get("project_path")
        if not isinstance(project_path, str) or not project_path:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={"error": "missing_project_path"},
                parent_event_id=event.id,
            )

        result = await self.analyze_codebase(project_path)
        return SwarmEvent(
            type=EventType.CODEBASE_SCAN_COMPLETED,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload=result.model_dump(mode="json"),
            metadata={
                "confidence": result.confidence,
                "evidence_count": len(result.evidence),
            },
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, object]:
        status = self.default_health_status()
        status["capabilities"] = ["codebase_scan", "diff_classification"]
        return status
