from __future__ import annotations

import re
from dataclasses import dataclass

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import (
    load_golden_case,
    load_paper_fixture,
    load_repo_fixture,
)
from papertrace_core.heuristics import infer_contributions, infer_mappings
from papertrace_core.inputs import detect_paper_source_kind
from papertrace_core.interfaces import (
    ContributionMapper,
    DiffAnalyzer,
    DiffOutput,
    MappingOutput,
    PaperParser,
    ParseOutput,
    RepoTracer,
    TraceOutput,
)
from papertrace_core.llm import LLMClient, build_llm_client
from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    DiffCluster,
    PaperContribution,
    ProcessorMode,
)
from papertrace_core.settings import get_settings

STRATEGY_PRIORITY: dict[str, int] = {
    "github_fork": 5,
    "readme_declaration": 4,
    "paper_mention": 3,
    "code_fingerprint": 2,
    "fallback": 1,
}

DECLARATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbased on\b", flags=0),
    re.compile(r"\bbuilt on top of\b", flags=0),
    re.compile(r"\bfollow(?:ing|s)?\b", flags=0),
    re.compile(r"\btransformers\b", flags=0),
    re.compile(r"\btrl\b", flags=0),
)


def dedupe_preserving_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


@dataclass(frozen=True)
class FixturePaperParser:
    llm_client: LLMClient | None = None

    def parse(self, request: AnalysisRequest) -> ParseOutput:
        case_slug = detect_case_slug(request)
        paper_fixture = load_paper_fixture(case_slug)
        warnings: list[str] = []
        if self.llm_client is not None:
            try:
                llm_contributions = self.llm_client.extract_contributions(paper_fixture)
                if llm_contributions:
                    return ParseOutput(
                        contributions=llm_contributions,
                        mode=ProcessorMode.LLM,
                        warnings=[],
                    )
                warnings.append("Paper parser received an empty llm response and fell back.")
            except Exception:
                warnings.append("Paper parser fell back from llm to heuristic extraction.")
        contributions = infer_contributions(case_slug, paper_fixture)
        if contributions:
            return ParseOutput(
                contributions=contributions,
                mode=ProcessorMode.HEURISTIC,
                warnings=warnings,
            )
        fixture = load_golden_case(case_slug)
        return ParseOutput(
            contributions=fixture.contributions,
            mode=ProcessorMode.FIXTURE,
            warnings=[*warnings, "Paper parser fell back to fixture contributions."],
        )


def sort_repo_candidates(candidates: list[BaseRepoCandidate]) -> list[BaseRepoCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            STRATEGY_PRIORITY.get(candidate.strategy, 0),
            candidate.confidence,
            candidate.repo_url,
        ),
        reverse=True,
    )


def dedupe_repo_candidates(candidates: list[BaseRepoCandidate]) -> list[BaseRepoCandidate]:
    by_repo_url: dict[str, BaseRepoCandidate] = {}
    for candidate in sort_repo_candidates(candidates):
        by_repo_url.setdefault(candidate.repo_url, candidate)
    return sort_repo_candidates(list(by_repo_url.values()))


class StrategyDrivenRepoTracer:
    def trace(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
    ) -> TraceOutput:
        del contributions
        case_slug = detect_case_slug(request)
        golden = load_golden_case(case_slug)
        paper_fixture = load_paper_fixture(case_slug)
        repo_fixture = load_repo_fixture(case_slug)

        candidates: list[BaseRepoCandidate] = []
        if repo_fixture.fork_parent:
            candidates.append(
                BaseRepoCandidate(
                    repo_url=repo_fixture.fork_parent,
                    strategy="github_fork",
                    confidence=0.99,
                    evidence="Repository metadata exposes an upstream fork parent.",
                )
            )

        readme_haystack = f"{repo_fixture.readme}\n{repo_fixture.notes}".lower()
        for mention in repo_fixture.explicit_mentions:
            if mention.alias.lower() in readme_haystack or any(
                pattern.search(readme_haystack) for pattern in DECLARATION_PATTERNS
            ):
                candidates.append(
                    BaseRepoCandidate(
                        repo_url=mention.repo_url,
                        strategy="readme_declaration",
                        confidence=mention.confidence,
                        evidence=mention.evidence,
                    )
                )

        paper_haystack = paper_fixture.text.lower()
        for mention in paper_fixture.codebase_mentions:
            if mention.alias.lower() in paper_haystack:
                candidates.append(
                    BaseRepoCandidate(
                        repo_url=mention.repo_url,
                        strategy="paper_mention",
                        confidence=mention.confidence,
                        evidence=mention.evidence,
                    )
                )

        if not any(candidate.strategy == "code_fingerprint" for candidate in candidates):
            candidates.extend(
                candidate
                for candidate in golden.base_repo_candidates
                if candidate.strategy == "code_fingerprint"
            )

        if not candidates:
            candidates.extend(
                BaseRepoCandidate(
                    repo_url=candidate.repo_url,
                    strategy="fallback",
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                )
                for candidate in golden.base_repo_candidates
            )

        deduped = dedupe_repo_candidates(candidates)
        return TraceOutput(
            selected_base_repo=deduped[0],
            candidates=deduped,
            mode=ProcessorMode.STRATEGY_CHAIN,
            warnings=[],
        )


class FixtureDiffAnalyzer:
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> DiffOutput:
        del selected_base_repo, contributions
        fixture = load_golden_case(detect_case_slug(request))
        return DiffOutput(
            diff_clusters=fixture.diff_clusters,
            mode=ProcessorMode.FIXTURE,
            warnings=["Diff analyzer is currently fixture-backed."],
        )


@dataclass(frozen=True)
class FixtureContributionMapper:
    llm_client: LLMClient | None = None

    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> MappingOutput:
        warnings: list[str] = []
        if self.llm_client is not None:
            try:
                llm_mappings = self.llm_client.map_contributions(contributions, diff_clusters)
                if llm_mappings:
                    return MappingOutput(
                        mappings=llm_mappings,
                        mode=ProcessorMode.LLM,
                        warnings=[],
                    )
                warnings.append("Contribution mapper received an empty llm response and fell back.")
            except Exception:
                warnings.append("Contribution mapper fell back from llm to heuristic matching.")
        mappings = infer_mappings(contributions, diff_clusters)
        if mappings:
            return MappingOutput(
                mappings=mappings,
                mode=ProcessorMode.HEURISTIC,
                warnings=warnings,
            )
        fixture = load_golden_case(detect_case_slug(request))
        return MappingOutput(
            mappings=fixture.mappings,
            mode=ProcessorMode.FIXTURE,
            warnings=[*warnings, "Contribution mapper fell back to fixture mappings."],
        )


@dataclass(frozen=True)
class AnalysisService:
    paper_parser: PaperParser
    repo_tracer: RepoTracer
    diff_analyzer: DiffAnalyzer
    contribution_mapper: ContributionMapper

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        fixture = load_golden_case(detect_case_slug(request))
        parse_output = self.paper_parser.parse(request)
        trace_output = self.repo_tracer.trace(request, parse_output.contributions)
        diff_output = self.diff_analyzer.analyze(
            request,
            trace_output.selected_base_repo,
            parse_output.contributions,
        )
        mapping_output = self.contribution_mapper.map(
            request,
            parse_output.contributions,
            diff_output.diff_clusters,
        )
        stage_warnings = dedupe_preserving_order(
            [
                *parse_output.warnings,
                *trace_output.warnings,
                *diff_output.warnings,
                *mapping_output.warnings,
            ]
        )
        warnings = dedupe_preserving_order([*fixture.warnings, *stage_warnings])
        return AnalysisResult(
            case_slug=fixture.case_slug,
            summary=fixture.summary,
            selected_base_repo=trace_output.selected_base_repo,
            base_repo_candidates=trace_output.candidates,
            contributions=parse_output.contributions,
            diff_clusters=diff_output.diff_clusters,
            mappings=mapping_output.mappings,
            metadata=AnalysisRuntimeMetadata(
                paper_source_kind=detect_paper_source_kind(request.paper_source),
                parser_mode=parse_output.mode,
                repo_tracer_mode=trace_output.mode,
                diff_analyzer_mode=diff_output.mode,
                contribution_mapper_mode=mapping_output.mode,
                selected_repo_strategy=trace_output.selected_base_repo.strategy,
                fallback_notes=stage_warnings,
            ),
            warnings=warnings,
        )


def build_default_analysis_service() -> AnalysisService:
    llm_client = build_llm_client(get_settings())
    return AnalysisService(
        paper_parser=FixturePaperParser(llm_client=llm_client),
        repo_tracer=StrategyDrivenRepoTracer(),
        diff_analyzer=FixtureDiffAnalyzer(),
        contribution_mapper=FixtureContributionMapper(llm_client=llm_client),
    )
