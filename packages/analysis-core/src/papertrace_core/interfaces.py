from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    ContributionMapping,
    DiffCluster,
    PaperContribution,
    ProcessorMode,
)


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
class DiffOutput:
    diff_clusters: list[DiffCluster]
    mode: ProcessorMode
    warnings: list[str]


@dataclass(frozen=True)
class MappingOutput:
    mappings: list[ContributionMapping]
    mode: ProcessorMode
    warnings: list[str]


class PaperParser(Protocol):
    def parse(self, request: AnalysisRequest) -> ParseOutput: ...


class RepoTracer(Protocol):
    def trace(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
    ) -> TraceOutput: ...


class DiffAnalyzer(Protocol):
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> DiffOutput: ...


class ContributionMapper(Protocol):
    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> MappingOutput: ...


class RepoMirror(Protocol):
    def prepare(self, repo_url: str) -> Path: ...
