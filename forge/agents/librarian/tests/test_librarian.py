from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.agents.librarian.agent import LibrarianAgent
from forge.agents.librarian.ast_analyzer import ASTAnalyzer
from forge.agents.librarian.github_client import GitHubClient
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from tests.conftest import FakeRedisStreamClient


@pytest.mark.unit
def test_scanner_detects_fastapi_project(python_fastapi_project: Path) -> None:
    analyzer = ASTAnalyzer()

    result = analyzer.analyze_project(python_fastapi_project)

    assert result.language == "python"
    assert result.framework == "fastapi"
    assert result.entry_point == "main.py"
    assert result.port == 8000
    assert result.env_vars == ["DATABASE_URL", "SECRET_KEY"]
    assert result.database_connections == ["postgres"]


@pytest.mark.unit
def test_scanner_detects_all_sample_projects(
    python_fastapi_project: Path,
    node_express_project: Path,
    go_service_project: Path,
) -> None:
    analyzer = ASTAnalyzer()

    python_result = analyzer.analyze_project(python_fastapi_project)
    node_result = analyzer.analyze_project(node_express_project)
    go_result = analyzer.analyze_project(go_service_project)

    assert python_result.language == "python"
    assert node_result.language == "node"
    assert go_result.language == "go"
    assert node_result.framework == "express"
    assert go_result.framework == "standard-library"
    assert node_result.port == 3000
    assert go_result.port == 8080


@pytest.mark.unit
def test_ast_analyzer_distinguishes_logic_and_docstring_changes() -> None:
    analyzer = ASTAnalyzer()
    before_docstring = 'def greet() -> str:\n    """hello"""\n    return "hi"\n'
    after_docstring = 'def greet() -> str:\n    """updated"""\n    return "hi"\n'
    before_logic = "def increment(value: int) -> int:\n    return value + 1\n"
    after_logic = "def increment(value: int) -> int:\n    return value + 2\n"

    docstring_result = analyzer.classify_source_change(
        before_docstring,
        after_docstring,
        file_path="service.py",
    )
    logic_result = analyzer.classify_source_change(
        before_logic,
        after_logic,
        file_path="service.py",
    )

    assert docstring_result.change_type == "comment_or_style"
    assert logic_result.change_type == "logic"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_librarian_agent_processes_scan_event(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = LibrarianAgent(settings=test_settings, message_bus=bus)
    event = SwarmEvent(
        type=EventType.CODEBASE_SCAN_REQUESTED,
        task_id="scan-123",
        source_agent="captain",
        target_agent="librarian",
        payload={"project_path": str(python_fastapi_project)},
    )

    response = await agent.process_event(event)

    assert response is not None
    assert response.type == EventType.CODEBASE_SCAN_COMPLETED
    assert response.payload["framework"] == "fastapi"
    assert response.payload["port"] == 8000


class FakeRepo:
    def __init__(self) -> None:
        commit_author = SimpleNamespace(name="octocat")
        commit_body = SimpleNamespace(message="Initial commit\n\nbody", author=commit_author)
        self.commit = SimpleNamespace(
            sha="abc123",
            author=SimpleNamespace(login="octocat"),
            commit=commit_body,
            html_url="https://example.com/commit/abc123",
            files=[SimpleNamespace(filename="main.py", patch="@@ -1 +1 @@")],
        )
        self.pull = SimpleNamespace(
            number=7,
            title="Add scanner",
            state="open",
            user=SimpleNamespace(login="octocat"),
            html_url="https://example.com/pull/7",
            get_files=lambda: [SimpleNamespace(filename="main.py")],
        )

    def get_commits(self) -> list[SimpleNamespace]:
        return [self.commit]

    def get_pull(self, number: int) -> SimpleNamespace:
        assert number == 7
        return self.pull

    def get_commit(self, sha: str) -> SimpleNamespace:
        assert sha == "abc123"
        return self.commit


class FakeGitHubAPI:
    def get_repo(self, full_name_or_id: str) -> FakeRepo:
        assert full_name_or_id == "octocat/Hello-World"
        return FakeRepo()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_github_client_returns_typed_results(test_settings: Settings) -> None:
    client = GitHubClient(settings=test_settings, github_api=FakeGitHubAPI())

    commits = await client.recent_commits("octocat/Hello-World")
    pull = await client.pull_request_details("octocat/Hello-World", 7)
    diff = await client.commit_diff("octocat/Hello-World", "abc123")

    assert commits[0].sha == "abc123"
    assert commits[0].author == "octocat"
    assert pull.changed_files == ["main.py"]
    assert diff.files["main.py"] == "@@ -1 +1 @@"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_github_client_can_fetch_real_public_commits(
    test_settings: Settings,
) -> None:
    if os.getenv("RUN_GITHUB_INTEGRATION") != "1":
        pytest.skip("Set RUN_GITHUB_INTEGRATION=1 to enable the live GitHub test.")

    client = GitHubClient(settings=test_settings)
    commits = await client.recent_commits("octocat/Hello-World", limit=1)

    assert commits
