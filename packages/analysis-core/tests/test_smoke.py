from __future__ import annotations

import pytest
from papertrace_core.fixtures import load_paper_fixture
from papertrace_core.llm import build_llm_client
from papertrace_core.models import AnalysisRequest, ProcessorMode
from papertrace_core.paper_sources import ArxivPaperSourceFetcher, paper_document_from_fixture
from papertrace_core.repo_metadata import GitHubRepoMetadataProvider
from papertrace_core.repos import ShallowGitRepoMirror
from papertrace_core.services import (
    AnalysisService,
    FixtureContributionMapper,
    HeuristicPaperParser,
    LiveRepoDiffAnalyzer,
    StrategyDrivenRepoTracer,
)
from papertrace_core.settings import get_settings


@pytest.mark.smoke
def test_smoke_llm_extracts_contributions() -> None:
    settings = get_settings()
    llm_client = build_llm_client(settings)
    if llm_client is None:
        pytest.skip("LLM_BASE_URL and LLM_MODEL are required for smoke LLM coverage")
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    contributions = llm_client.extract_contributions(
        paper_document_from_fixture(request, load_paper_fixture("lora"))
    )

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


@pytest.mark.smoke
def test_smoke_arxiv_paper_fetch() -> None:
    settings = get_settings()
    fetcher = ArxivPaperSourceFetcher(settings)

    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.paper_document.title
    assert output.paper_document.abstract


@pytest.mark.smoke
def test_smoke_flash_attention_runs_non_fixture_primary_path() -> None:
    settings = get_settings()
    repo_mirror = ShallowGitRepoMirror(settings)
    service = AnalysisService(
        paper_source_fetcher=ArxivPaperSourceFetcher(settings),
        paper_parser=HeuristicPaperParser(),
        repo_tracer=StrategyDrivenRepoTracer(
            repo_metadata_provider=GitHubRepoMetadataProvider(settings),
        ),
        diff_analyzer=LiveRepoDiffAnalyzer(
            repo_mirror=repo_mirror,
            settings=settings,
        ),
        contribution_mapper=FixtureContributionMapper(),
    )

    result = service.analyze(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/Dao-AILab/flash-attention",
        )
    )

    assert result.metadata.paper_fetch_mode == ProcessorMode.REMOTE_FETCH
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.HEURISTIC
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy != "fallback"
    assert result.diff_clusters
