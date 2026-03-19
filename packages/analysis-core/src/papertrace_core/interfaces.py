from __future__ import annotations

from typing import Protocol

from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    ContributionMapping,
    DiffCluster,
    PaperContribution,
)


class PaperParser(Protocol):
    def parse(self, request: AnalysisRequest) -> list[PaperContribution]: ...


class RepoTracer(Protocol):
    def trace(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
    ) -> tuple[BaseRepoCandidate, list[BaseRepoCandidate]]: ...


class DiffAnalyzer(Protocol):
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> list[DiffCluster]: ...


class ContributionMapper(Protocol):
    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> list[ContributionMapping]: ...
