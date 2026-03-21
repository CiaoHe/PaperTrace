from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import load_repo_fixture
from papertrace_core.interfaces import RepoMetadataOutput
from papertrace_core.models import AnalysisRequest
from papertrace_core.settings import Settings


class RepoMetadataError(RuntimeError):
    pass


def parse_github_repo_path(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != "github.com" or len(path_parts) < 2:
        raise RepoMetadataError(f"Unsupported GitHub repository URL: {repo_url}")
    return path_parts[0], path_parts[1].removesuffix(".git")


def repo_aliases(repo_url: str) -> tuple[str, ...]:
    owner, repo = parse_github_repo_path(repo_url)
    return (repo.lower(), f"{owner.lower()}/{repo.lower()}")


@dataclass(frozen=True)
class FixtureRepoMetadataProvider:
    def fetch(self, request: AnalysisRequest) -> RepoMetadataOutput:
        case_slug = detect_case_slug(request)
        if case_slug is None:
            return RepoMetadataOutput(
                fork_parent=None,
                readme_text="",
                notes="",
                warnings=["Repo metadata fallback had no matching golden case and returned an empty generic payload."],
            )
        fixture = load_repo_fixture(case_slug)
        return RepoMetadataOutput(
            fork_parent=fixture.fork_parent,
            readme_text=fixture.readme,
            notes=fixture.notes,
            warnings=[],
        )


@dataclass(frozen=True)
class GitHubRepoMetadataProvider:
    settings: Settings
    client: httpx.Client | None = None

    def fetch(self, request: AnalysisRequest) -> RepoMetadataOutput:
        owner, repo = parse_github_repo_path(request.repo_url)
        repo_endpoint = f"{self.settings.github_api_base_url}/repos/{owner}/{repo}"
        readme_endpoint = f"{repo_endpoint}/readme"
        close_client = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.github_timeout_seconds)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PaperTrace",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"token {self.settings.github_token}"

        try:
            repo_response = client.get(repo_endpoint, headers=headers)
            repo_response.raise_for_status()
            repo_payload = repo_response.json()

            readme_text = ""
            readme_response = client.get(readme_endpoint, headers=headers)
            if readme_response.status_code != httpx.codes.NOT_FOUND:
                readme_response.raise_for_status()
                readme_payload = readme_response.json()
                encoded_content = readme_payload.get("content") or ""
                encoding = (readme_payload.get("encoding") or "").lower()
                if encoding == "base64":
                    readme_text = base64.b64decode(encoded_content).decode("utf-8")
                elif isinstance(encoded_content, str):
                    readme_text = encoded_content
        except (httpx.HTTPError, ValueError, UnicodeDecodeError) as exc:
            raise RepoMetadataError(f"Failed to fetch GitHub metadata for {request.repo_url}: {exc}") from exc
        finally:
            if close_client:
                client.close()

        parent_payload = repo_payload.get("parent") or {}
        return RepoMetadataOutput(
            fork_parent=parent_payload.get("html_url"),
            readme_text=readme_text,
            notes=repo_payload.get("description") or "",
            warnings=[],
        )


@dataclass(frozen=True)
class ChainedRepoMetadataProvider:
    primary: GitHubRepoMetadataProvider
    fallback: FixtureRepoMetadataProvider

    def fetch(self, request: AnalysisRequest) -> RepoMetadataOutput:
        try:
            return self.primary.fetch(request)
        except RepoMetadataError as exc:
            fallback_output = self.fallback.fetch(request)
            return RepoMetadataOutput(
                fork_parent=fallback_output.fork_parent,
                readme_text=fallback_output.readme_text,
                notes=fallback_output.notes,
                warnings=[
                    *fallback_output.warnings,
                    "Repo tracer fell back to fixture repository metadata.",
                    str(exc),
                ],
            )
