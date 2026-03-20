from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    ContributionMapping,
    DiffCluster,
    JobStage,
    PaperContribution,
    PaperDocument,
    ProcessorMode,
)


@dataclass(frozen=True)
class FetchOutput:
    paper_document: PaperDocument
    mode: ProcessorMode
    warnings: list[str]


@dataclass(frozen=True)
class ParseOutput:
    contributions: list[PaperContribution]
    mode: ProcessorMode
    warnings: list[str]


@dataclass(frozen=True)
class TraceOutput:
    selected_base_repo: BaseRepoCandidate
    candidates: list[BaseRepoCandidate]
    mode: ProcessorMode
    warnings: list[str]


@dataclass(frozen=True)
class RepoMetadataOutput:
    fork_parent: str | None
    readme_text: str
    notes: str
    warnings: list[str]


@dataclass(frozen=True)
class DiffOutput:
    diff_clusters: list[DiffCluster]
    mode: ProcessorMode
    warnings: list[str]


@dataclass(frozen=True)
class MappingOutput:
    mappings: list[ContributionMapping]
    unmatched_contribution_ids: list[str]
    unmatched_diff_cluster_ids: list[str]
    mode: ProcessorMode
    warnings: list[str]


StageProgressCallback = Callable[[JobStage, float, str], None]


class PaperSourceFetcher(Protocol):
    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput: ...


class PaperParser(Protocol):
    def parse(
        self,
        request: AnalysisRequest,
        paper_document: PaperDocument,
        *,
        progress: StageProgressCallback | None = None,
    ) -> ParseOutput: ...


class RepoTracer(Protocol):
    def trace(
        self,
        request: AnalysisRequest,
        paper_document: PaperDocument,
        contributions: list[PaperContribution],
        *,
        progress: StageProgressCallback | None = None,
    ) -> TraceOutput: ...


class RepoMetadataProvider(Protocol):
    def fetch(self, request: AnalysisRequest) -> RepoMetadataOutput: ...


class DiffAnalyzer(Protocol):
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
        *,
        progress: StageProgressCallback | None = None,
    ) -> DiffOutput: ...


class ContributionMapper(Protocol):
    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
        *,
        progress: StageProgressCallback | None = None,
    ) -> MappingOutput: ...


class RepoMirror(Protocol):
    def prepare(self, repo_url: str) -> Path: ...
