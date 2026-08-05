"""Commit-history tools.

Adapted from the previous ``librarian/github_client.py``, which was a real
PyGithub wrapper that nothing ever called and which could not window commits by
time. This version adds ``since``/``until`` windowing, commit timestamps, and
changed-file lists, and exposes two interchangeable sources:

``live``
    The real GitHub REST API via PyGithub. Requires ``GITHUB_TOKEN``.
``fixture``
    Replay of a labelled benchmark commit history from disk, so benchmark runs
    are deterministic and do not depend on a third party being reachable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from swarm.tools.registry import Tool


class CommitRecord(BaseModel):
    """A commit as returned by any commit source."""

    sha: str = Field(description="Commit SHA.")
    author: str = Field(description="Author login or git name.")
    message: str = Field(description="Commit message headline.")
    timestamp: datetime = Field(description="Authored time, UTC.")
    files_changed: list[str] = Field(
        default_factory=list, description="Repo-relative paths touched."
    )
    url: str = Field(default="", description="Commit web URL when available.")


class CommitsInWindowArgs(BaseModel):
    """Arguments for ``github.commits_in_window``."""

    repository: str = Field(description="Repository in owner/name form.")
    since: datetime = Field(description="Inclusive lower bound on commit time.")
    until: datetime = Field(description="Inclusive upper bound on commit time.")
    limit: int = Field(default=50, ge=1, le=250, description="Maximum commits to return.")


class CommitsInWindowResult(BaseModel):
    """Result of ``github.commits_in_window``."""

    repository: str = Field(description="Repository that was queried.")
    source: str = Field(description="'live' or 'fixture'.")
    since: datetime = Field(description="Lower bound actually applied.")
    until: datetime = Field(description="Upper bound actually applied.")
    commits: list[CommitRecord] = Field(
        default_factory=list, description="Commits inside the window, newest first."
    )


class CommitSource(Protocol):
    """Anything that can return commits in a time window."""

    source_name: str

    def commits_in_window(self, args: CommitsInWindowArgs) -> CommitsInWindowResult: ...


class LiveGitHubCommitSource:
    """Real GitHub REST API commit source, backed by PyGithub."""

    source_name = "live"

    def __init__(self, token: str | None, github_api: object | None = None) -> None:
        self._token = token
        self._api = github_api

    def commits_in_window(self, args: CommitsInWindowArgs) -> CommitsInWindowResult:
        repo = self._client().get_repo(args.repository)
        commits: list[CommitRecord] = []
        for index, commit in enumerate(repo.get_commits(since=args.since, until=args.until)):
            if index >= args.limit:
                break
            commits.append(_to_record(commit))
        commits.sort(key=lambda item: item.timestamp, reverse=True)
        return CommitsInWindowResult(
            repository=args.repository,
            source=self.source_name,
            since=args.since,
            until=args.until,
            commits=commits,
        )

    def _client(self):  # type: ignore[no-untyped-def]
        if self._api is not None:
            return self._api
        from github import Auth, Github

        auth = Auth.Token(self._token) if self._token else None
        return Github(auth=auth)


class FixtureCommitSource:
    """Replays a recorded commit history from a JSON file.

    The file is a benchmark fixture, not production data, and is labelled as
    such in the result via ``source='fixture'``.
    """

    source_name = "fixture"

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = Path(fixture_path)

    def commits_in_window(self, args: CommitsInWindowArgs) -> CommitsInWindowResult:
        raw = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        records = [CommitRecord.model_validate(item) for item in raw.get("commits", [])]
        windowed = [
            record
            for record in records
            if args.since <= _as_utc(record.timestamp) <= args.until
        ]
        windowed.sort(key=lambda item: item.timestamp, reverse=True)
        return CommitsInWindowResult(
            repository=raw.get("repository", args.repository),
            source=self.source_name,
            since=args.since,
            until=args.until,
            commits=windowed[: args.limit],
        )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _to_record(commit: object) -> CommitRecord:
    sha = str(getattr(commit, "sha", ""))
    git_commit = getattr(commit, "commit", None)
    message = str(getattr(git_commit, "message", "") or "")
    headline = message.splitlines()[0] if message else ""

    author_obj = getattr(commit, "author", None)
    git_author = getattr(git_commit, "author", None)
    author = ""
    if author_obj is not None and getattr(author_obj, "login", None):
        author = str(author_obj.login)
    elif git_author is not None and getattr(git_author, "name", None):
        author = str(git_author.name)

    timestamp = getattr(git_author, "date", None)
    if not isinstance(timestamp, datetime):
        timestamp = datetime.now(UTC)

    files = []
    for changed in getattr(commit, "files", []) or []:
        filename = getattr(changed, "filename", None)
        if filename:
            files.append(str(filename))

    return CommitRecord(
        sha=sha,
        author=author or "unknown",
        message=headline,
        timestamp=_as_utc(timestamp),
        files_changed=files,
        url=str(getattr(commit, "html_url", "") or ""),
    )


def build_github_tool(source: CommitSource) -> Tool:
    """Register the windowed commit lookup as a swarm tool."""

    return Tool(
        name="github.commits_in_window",
        description=(
            "Return commits authored between two timestamps for a repository, with "
            "changed-file lists, so an incident window can be correlated to code."
        ),
        args_model=CommitsInWindowArgs,
        result_model=CommitsInWindowResult,
        handler=source.commits_in_window,
    )
