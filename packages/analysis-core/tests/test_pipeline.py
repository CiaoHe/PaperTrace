from papertrace_core.cases import detect_case_slug
from papertrace_core.models import AnalysisRequest, BaseRepoCandidate
from papertrace_core.pipeline import run_analysis
from papertrace_core.services import (
    StrategyDrivenRepoTracer,
    build_default_analysis_service,
    sort_repo_candidates,
)


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


def test_repo_tracer_prefers_readme_declaration_over_paper_mention() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    selected_candidate, candidates = StrategyDrivenRepoTracer().trace(request, [])

    assert selected_candidate.strategy == "readme_declaration"
    assert selected_candidate.repo_url == "https://github.com/huggingface/transformers"
    assert len(candidates) == 1
    assert candidates[0].strategy == "readme_declaration"


def test_repo_tracer_falls_back_to_code_fingerprint_when_no_mentions_exist() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )

    selected_candidate, candidates = StrategyDrivenRepoTracer().trace(request, [])

    assert selected_candidate.strategy == "code_fingerprint"
    assert candidates[0].repo_url == "https://github.com/openai/triton"


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
