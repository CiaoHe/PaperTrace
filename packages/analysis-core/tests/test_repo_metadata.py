from __future__ import annotations

import base64

import httpx
from papertrace_core.models import AnalysisRequest
from papertrace_core.repo_metadata import (
    ChainedRepoMetadataProvider,
    FixtureRepoMetadataProvider,
    GitHubRepoMetadataProvider,
)
from papertrace_core.settings import Settings


def build_settings() -> Settings:
    return Settings.model_validate(
        {
            "ENABLE_LIVE_REPO_TRACE": True,
            "GITHUB_API_BASE_URL": "https://example.test",
            "GITHUB_TIMEOUT_SECONDS": 5,
        }
    )


def test_github_repo_metadata_provider_decodes_readme_and_parent() -> None:
    readme_content = base64.b64encode(b"Built on top of huggingface/transformers").decode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/example/project":
            return httpx.Response(
                200,
                json={
                    "description": "Example repo",
                    "parent": {"html_url": "https://github.com/upstream/base"},
                },
            )
        if request.url.path == "/repos/example/project/readme":
            return httpx.Response(
                200,
                json={"content": readme_content, "encoding": "base64"},
            )
        return httpx.Response(404)

    provider = GitHubRepoMetadataProvider(
        settings=build_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = provider.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/example/project",
        )
    )

    assert output.fork_parent == "https://github.com/upstream/base"
    assert output.notes == "Example repo"
    assert "huggingface/transformers" in output.readme_text.lower()


def test_chained_repo_metadata_provider_falls_back_to_fixture() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "error"})

    provider = ChainedRepoMetadataProvider(
        primary=GitHubRepoMetadataProvider(
            settings=build_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        fallback=FixtureRepoMetadataProvider(),
    )
    output = provider.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.fork_parent is None
    assert "transformers" in output.readme_text.lower()
    assert output.warnings[0] == "Repo tracer fell back to fixture repository metadata."
