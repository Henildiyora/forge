from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.config import Settings


class ConnectionProfile(BaseModel):
    """Persisted backend and approval preferences for a project workspace."""

    llm_backend: str = Field(description="Configured LLM backend name.")
    llm_model: str = Field(description="Configured model identifier.")
    approval_transport: str = Field(
        default="web",
        description='Approval transport such as "slack" or "web".',
    )
    cloud_provider: str | None = Field(
        default=None,
        description="Preferred cloud provider when the user has chosen one.",
    )


class ConversationSession(BaseModel):
    """Persisted conversation state for `forge build` sessions."""

    task_id: str = Field(description="Task identifier for the most recent build session.")
    goal: str = Field(description="User-stated deployment goal.")
    strategy: str | None = Field(
        default=None,
        description="Final strategy chosen for the session when available.",
    )
    questions_asked: int = Field(
        default=0,
        ge=0,
        description="Number of clarification questions already asked.",
    )
    last_question: str | None = Field(
        default=None,
        description="Most recent clarification question shown to the user.",
    )
    decision_payload: dict[str, object] = Field(
        default_factory=dict,
        description="Serialized deployment decision data when confirmed.",
    )


class ArtifactManifest(BaseModel):
    """Manifest of generated artifacts written by FORGE."""

    task_id: str = Field(description="Build task that created the artifacts.")
    strategy: str = Field(description="Deployment strategy used for generation.")
    files: list[str] = Field(
        default_factory=list,
        description="Artifact paths written relative to the output directory.",
    )


class ForgeWorkspace:
    """Filesystem-backed project workspace for scan, session, and artifact metadata."""

    def __init__(self, project_root: str | Path, settings: Settings) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.settings = settings
        self.workspace_dir = self.project_root / settings.workspace_dir_name

    def ensure(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.workspace_dir / "index.json"

    @property
    def session_path(self) -> Path:
        return self.workspace_dir / "session.json"

    @property
    def connection_path(self) -> Path:
        return self.workspace_dir / "connection.json"

    @property
    def artifacts_path(self) -> Path:
        return self.workspace_dir / "artifacts.json"

    def save_index(self, scan_result: CodebaseScanResult) -> None:
        self.ensure()
        self.index_path.write_text(
            json.dumps(scan_result.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def load_index(self) -> CodebaseScanResult | None:
        if not self.index_path.exists():
            return None
        return CodebaseScanResult.model_validate(
            json.loads(self.index_path.read_text(encoding="utf-8"))
        )

    def save_connection(self, profile: ConnectionProfile) -> None:
        self.ensure()
        self.connection_path.write_text(
            json.dumps(profile.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def load_connection(self) -> ConnectionProfile | None:
        if not self.connection_path.exists():
            return None
        return ConnectionProfile.model_validate(
            json.loads(self.connection_path.read_text(encoding="utf-8"))
        )

    def save_session(self, session: ConversationSession) -> None:
        self.ensure()
        self.session_path.write_text(
            json.dumps(session.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def load_session(self) -> ConversationSession | None:
        if not self.session_path.exists():
            return None
        return ConversationSession.model_validate(
            json.loads(self.session_path.read_text(encoding="utf-8"))
        )

    def save_artifacts(self, manifest: ArtifactManifest) -> None:
        self.ensure()
        self.artifacts_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def load_artifacts(self) -> ArtifactManifest | None:
        if not self.artifacts_path.exists():
            return None
        return ArtifactManifest.model_validate(
            json.loads(self.artifacts_path.read_text(encoding="utf-8"))
        )
