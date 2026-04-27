from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

import structlog
from pydantic import BaseModel, Field

from swarm.core.config import Settings


class SupportsGitHubCommitAuthor(Protocol):
    login: str


class SupportsGitHubGitAuthor(Protocol):
    name: str


class SupportsGitHubCommitBody(Protocol):
    message: str
    author: SupportsGitHubGitAuthor | None


class SupportsGitHubChangedFile(Protocol):
    filename: str
    patch: str | None


class SupportsGitHubCommitObject(Protocol):
    sha: str
    author: SupportsGitHubCommitAuthor | None
    commit: SupportsGitHubCommitBody
    html_url: str
    files: Sequence[SupportsGitHubChangedFile]


class SupportsGitHubPullFile(Protocol):
    filename: str


class SupportsGitHubPullUser(Protocol):
    login: str


class SupportsGitHubPull(Protocol):
    number: int
    title: str
    state: str
    user: SupportsGitHubPullUser
    html_url: str

    def get_files(self) -> Sequence[SupportsGitHubPullFile]: ...


class GitHubCommit(BaseModel):
    """Commit summary returned by GitHubClient."""

    sha: str = Field(description="Commit SHA.")
    message: str = Field(description="Commit message headline.")
    author: str = Field(description="Author login or display name.")
    url: str = Field(description="Commit web URL.")


class PullRequestDetails(BaseModel):
    """Pull request metadata used by incident investigation flows."""

    number: int = Field(description="Pull request number.")
    title: str = Field(description="Pull request title.")
    state: str = Field(description="Current pull request state.")
    author: str = Field(description="Login of the pull request author.")
    changed_files: list[str] = Field(
        default_factory=list,
        description="Files touched by the pull request.",
    )
    url: str = Field(description="Pull request web URL.")


class CommitDiff(BaseModel):
    """Commit file diff payload for a single commit."""

    sha: str = Field(description="Commit SHA.")
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of filename to patch content.",
    )


class SupportsGitHubRepository(Protocol):
    """Subset of repository methods used by GitHubClient."""

    def get_commits(self) -> Sequence[SupportsGitHubCommitObject]: ...

    def get_pull(self, number: int) -> SupportsGitHubPull: ...

    def get_commit(self, sha: str) -> SupportsGitHubCommitObject: ...


class SupportsGitHubAPI(Protocol):
    """Subset of GitHub API methods used by GitHubClient."""

    def get_repo(self, full_name_or_id: str) -> SupportsGitHubRepository: ...


class GitHubClient:
    """Small wrapper around PyGithub with typed outputs."""

    def __init__(
        self,
        settings: Settings,
        github_api: SupportsGitHubAPI | None = None,
    ):
        self.settings = settings
        self.logger = structlog.get_logger().bind(component="github_client")
        self._github_api = github_api

    async def recent_commits(
        self,
        repository: str,
        limit: int = 10,
    ) -> list[GitHubCommit]:
        repo = self._api().get_repo(repository)
        commits = repo.get_commits()
        results: list[GitHubCommit] = []
        for index, commit in enumerate(commits):
            if index >= limit:
                break
            author_name = ""
            commit_author = commit.author
            git_author = commit.commit.author
            if commit_author is not None:
                author_name = str(commit_author.login)
            elif git_author is not None:
                author_name = str(git_author.name)
            results.append(
                GitHubCommit(
                    sha=str(commit.sha),
                    message=str(commit.commit.message).splitlines()[0],
                    author=author_name or "unknown",
                    url=str(commit.html_url),
                )
            )
        return results

    async def pull_request_details(
        self,
        repository: str,
        number: int,
    ) -> PullRequestDetails:
        repo = self._api().get_repo(repository)
        pull = repo.get_pull(number)
        changed_files = [str(file.filename) for file in pull.get_files()]
        return PullRequestDetails(
            number=int(pull.number),
            title=str(pull.title),
            state=str(pull.state),
            author=str(pull.user.login),
            changed_files=changed_files,
            url=str(pull.html_url),
        )

    async def commit_diff(self, repository: str, sha: str) -> CommitDiff:
        repo = self._api().get_repo(repository)
        commit = repo.get_commit(sha)
        files: dict[str, str] = {}
        for changed_file in commit.files:
            patch = getattr(changed_file, "patch", "") or ""
            files[str(changed_file.filename)] = str(patch)
        return CommitDiff(sha=str(commit.sha), files=files)

    def _api(self) -> SupportsGitHubAPI:
        if self._github_api is not None:
            return self._github_api
        from github import Github

        token = self.settings.github_token
        github_token = token.get_secret_value() if token is not None else None
        return cast(SupportsGitHubAPI, Github(login_or_token=github_token))
