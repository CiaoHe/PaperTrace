from typing import Any, cast

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import load_golden_case, load_paper_fixture
from papertrace_core.heuristics import infer_contributions, infer_mappings
from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.paper_sources import FixturePaperSourceFetcher, paper_document_from_fixture
from papertrace_core.pipeline import run_analysis
from papertrace_core.repo_metadata import FixtureRepoMetadataProvider
from papertrace_core.services import (
    AnalysisService,
    FixtureContributionMapper,
    FixtureDiffAnalyzer,
    HeuristicPaperParser,
    StrategyDrivenRepoTracer,
    build_default_analysis_service,
    sort_repo_candidates,
)


class EmptyLLMClient:
    def extract_contributions(self, _: object) -> list[object]:
        return []

    def map_contributions(self, _: object, __: object) -> list[object]:
        return []


def test_detect_case_slug_prefers_lora_fixture() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    assert detect_case_slug(request) == "lora"


def test_run_analysis_returns_fixture_payload() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2305.18290 DPO",
        repo_url="https://github.com/huggingface/trl",
    )

    result = run_analysis(request)

    assert result.case_slug == "dpo"
    assert result.selected_base_repo.repo_url == "https://github.com/huggingface/trl"
    assert len(result.diff_clusters) == 1


def test_default_analysis_service_recomposes_fixture_result() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )

    result = build_default_analysis_service().analyze(request)

    assert result.case_slug == "flash-attention"
    assert result.contributions[0].id == "C1"
    assert result.base_repo_candidates[0].strategy == "code_fingerprint"
    assert result.metadata.paper_source_kind == PaperSourceKind.ARXIV
    assert result.metadata.paper_fetch_mode == ProcessorMode.FIXTURE
    assert result.metadata.parser_mode == ProcessorMode.HEURISTIC
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.FIXTURE
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy == result.selected_base_repo.strategy
    assert "Diff analyzer is currently fixture-backed." in result.metadata.fallback_notes


def test_repo_tracer_prefers_readme_declaration_over_paper_mention() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    trace_output = StrategyDrivenRepoTracer(
        repo_metadata_provider=FixtureRepoMetadataProvider()
    ).trace(request, [])

    assert trace_output.selected_base_repo.strategy == "readme_declaration"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"
    assert len(trace_output.candidates) == 1
    assert trace_output.candidates[0].strategy == "readme_declaration"
    assert trace_output.mode == ProcessorMode.STRATEGY_CHAIN


def test_repo_tracer_falls_back_to_code_fingerprint_when_no_mentions_exist() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )

    trace_output = StrategyDrivenRepoTracer(
        repo_metadata_provider=FixtureRepoMetadataProvider()
    ).trace(request, [])

    assert trace_output.selected_base_repo.strategy == "code_fingerprint"
    assert trace_output.candidates[0].repo_url == "https://github.com/openai/triton"


def test_sort_repo_candidates_prioritizes_strategy_before_confidence() -> None:
    candidates = [
        BaseRepoCandidate(
            repo_url="https://github.com/example/high-confidence",
            strategy="code_fingerprint",
            confidence=0.98,
            evidence="fingerprint",
        ),
        BaseRepoCandidate(
            repo_url="https://github.com/example/lower-confidence",
            strategy="readme_declaration",
            confidence=0.75,
            evidence="readme",
        ),
    ]

    sorted_candidates = sort_repo_candidates(candidates)

    assert sorted_candidates[0].strategy == "readme_declaration"


def test_infer_contributions_extracts_lora_patterns() -> None:
    paper_fixture = load_paper_fixture("lora")

    contributions = infer_contributions("lora", paper_fixture.title, paper_fixture.text)

    assert len(contributions) == 2
    assert contributions[0].id == "C1"
    assert "low-rank" in contributions[0].keywords


def test_infer_mappings_matches_lora_clusters_to_contributions() -> None:
    golden = load_golden_case("lora")

    mappings = infer_mappings(golden.contributions, golden.diff_clusters)

    assert len(mappings) == 2
    assert mappings[0].diff_cluster_id == "D1"
    assert mappings[0].contribution_id == "C1"


def test_service_records_fallback_notes_when_llm_returns_empty_payloads() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    service = AnalysisService(
        paper_source_fetcher=FixturePaperSourceFetcher(),
        paper_parser=HeuristicPaperParser(llm_client=cast(Any, EmptyLLMClient())),
        repo_tracer=StrategyDrivenRepoTracer(repo_metadata_provider=FixtureRepoMetadataProvider()),
        diff_analyzer=FixtureDiffAnalyzer(),
        contribution_mapper=FixtureContributionMapper(llm_client=cast(Any, EmptyLLMClient())),
    )

    result = service.analyze(request)

    assert result.metadata.parser_mode == ProcessorMode.HEURISTIC
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert (
        "Paper parser received an empty llm response and fell back."
        in result.metadata.fallback_notes
    )
    assert (
        "Contribution mapper received an empty llm response and fell back."
        in result.metadata.fallback_notes
    )


def test_heuristic_paper_parser_uses_fetched_paper_document() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("lora"))

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert result.contributions
    assert result.contributions[0].id == "C1"
