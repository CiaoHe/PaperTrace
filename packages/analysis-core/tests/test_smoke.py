from __future__ import annotations

import pytest
from papertrace_core.fixtures import load_paper_fixture
from papertrace_core.llm import build_llm_client
from papertrace_core.models import AnalysisRequest
from papertrace_core.repo_metadata import GitHubRepoMetadataProvider
from papertrace_core.settings import get_settings


@pytest.mark.smoke
def test_smoke_llm_extracts_contributions() -> None:
    settings = get_settings()
    llm_client = build_llm_client(settings)
    if llm_client is None:
        pytest.skip("LLM_BASE_URL and LLM_MODEL are required for smoke LLM coverage")

    contributions = llm_client.extract_contributions(load_paper_fixture("lora"))

    assert contributions
    assert contributions[0].id
    assert contributions[0].title


@pytest.mark.smoke
def test_smoke_github_repo_metadata_fetch() -> None:
    settings = get_settings()
    provider = GitHubRepoMetadataProvider(settings)

    output = provider.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.readme_text
    assert "lora" in output.readme_text.lower()
