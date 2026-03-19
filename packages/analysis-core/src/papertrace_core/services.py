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
from papertrace_core.interfaces import (
    ContributionMapper,
    DiffAnalyzer,
    PaperParser,
    RepoTracer,
)
from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    BaseRepoCandidate,
    ContributionMapping,
    DiffCluster,
    PaperContribution,
)

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


class FixturePaperParser:
    def parse(self, request: AnalysisRequest) -> list[PaperContribution]:
        case_slug = detect_case_slug(request)
        paper_fixture = load_paper_fixture(case_slug)
        contributions = infer_contributions(case_slug, paper_fixture)
        if contributions:
            return contributions
        fixture = load_golden_case(case_slug)
        return fixture.contributions


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
    ) -> tuple[BaseRepoCandidate, list[BaseRepoCandidate]]:
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
        return deduped[0], deduped


class FixtureDiffAnalyzer:
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> list[DiffCluster]:
        del selected_base_repo, contributions
        fixture = load_golden_case(detect_case_slug(request))
        return fixture.diff_clusters


class FixtureContributionMapper:
    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> list[ContributionMapping]:
        mappings = infer_mappings(contributions, diff_clusters)
        if mappings:
            return mappings
        fixture = load_golden_case(detect_case_slug(request))
        return fixture.mappings


@dataclass(frozen=True)
class AnalysisService:
    paper_parser: PaperParser
    repo_tracer: RepoTracer
    diff_analyzer: DiffAnalyzer
    contribution_mapper: ContributionMapper

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        fixture = load_golden_case(detect_case_slug(request))
        contributions = self.paper_parser.parse(request)
        selected_base_repo, base_repo_candidates = self.repo_tracer.trace(request, contributions)
        diff_clusters = self.diff_analyzer.analyze(request, selected_base_repo, contributions)
        mappings = self.contribution_mapper.map(request, contributions, diff_clusters)
        return AnalysisResult(
            case_slug=fixture.case_slug,
            summary=fixture.summary,
            selected_base_repo=selected_base_repo,
            base_repo_candidates=base_repo_candidates,
            contributions=contributions,
            diff_clusters=diff_clusters,
            mappings=mappings,
            warnings=fixture.warnings,
        )


def build_default_analysis_service() -> AnalysisService:
    return AnalysisService(
        paper_parser=FixturePaperParser(),
        repo_tracer=StrategyDrivenRepoTracer(),
        diff_analyzer=FixtureDiffAnalyzer(),
        contribution_mapper=FixtureContributionMapper(),
    )
