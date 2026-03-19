from __future__ import annotations

from dataclasses import dataclass

from papertrace_core.inputs import normalize_repo_url
from papertrace_core.models import AnalysisRequest, GoldenCaseExample


@dataclass(frozen=True)
class GoldenCase:
    slug: str
    title: str
    aliases: tuple[str, ...]
    paper_source: str
    repo_url: str


GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        slug="lora",
        title="LoRA",
        aliases=("lora", "low-rank adaptation"),
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    ),
    GoldenCase(
        slug="dpo",
        title="DPO",
        aliases=("dpo", "direct preference optimization"),
        paper_source="https://arxiv.org/abs/2305.18290 DPO",
        repo_url="https://github.com/huggingface/trl",
    ),
    GoldenCase(
        slug="flash-attention",
        title="Flash Attention",
        aliases=("flash attention", "flash-attention"),
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    ),
)


def detect_case_slug(request: AnalysisRequest) -> str:
    haystack = f"{request.paper_source} {request.repo_url}".lower()
    for golden_case in GOLDEN_CASES:
        if any(alias in haystack for alias in golden_case.aliases):
            return golden_case.slug
    return GOLDEN_CASES[0].slug


def default_case_examples() -> list[GoldenCase]:
    return list(GOLDEN_CASES)


def example_payloads() -> list[GoldenCaseExample]:
    return [
        GoldenCaseExample(
            slug=golden_case.slug,
            title=golden_case.title,
            paper_source=golden_case.paper_source,
            repo_url=normalize_repo_url(golden_case.repo_url),
        )
        for golden_case in GOLDEN_CASES
    ]
