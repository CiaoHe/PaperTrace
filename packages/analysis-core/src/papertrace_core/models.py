from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobStage(StrEnum):
    PAPER_FETCH = "paper_fetch"
    PAPER_PARSE = "paper_parse"
    REPO_FETCH = "repo_fetch"
    ANCESTRY_TRACE = "ancestry_trace"
    DIFF_ANALYZE = "diff_analyze"
    CONTRIBUTION_MAP = "contribution_map"
    PERSIST_RESULT = "persist_result"


class DiffChangeType(StrEnum):
    NEW_MODULE = "NEW_MODULE"
    MODIFIED_CORE = "MODIFIED_CORE"
    MODIFIED_LOSS = "MODIFIED_LOSS"
    MODIFIED_TRAIN = "MODIFIED_TRAIN"
    MODIFIED_INFRA = "MODIFIED_INFRA"


class PaperSourceKind(StrEnum):
    ARXIV = "arxiv"
    PDF_URL = "pdf_url"
    PDF_FILE = "pdf_file"
    TEXT_REFERENCE = "text_reference"


class ProcessorMode(StrEnum):
    REMOTE_FETCH = "remote_fetch"
    HEURISTIC = "heuristic"
    LLM = "llm"
    STRATEGY_CHAIN = "strategy_chain"
    FIXTURE = "fixture"


class CoverageType(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    APPROXIMATED = "APPROXIMATED"
    MISSING = "MISSING"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    paper_source: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)


class PaperSection(BaseModel):
    heading: str
    text: str


class PaperDocument(BaseModel):
    source_kind: PaperSourceKind
    source_ref: str
    title: str
    abstract: str = ""
    sections: list[PaperSection] = Field(default_factory=list)
    text: str


class PaperContribution(BaseModel):
    id: str
    title: str
    section: str
    keywords: list[str]
    impl_hints: list[str]
    problem_solved: str | None = None
    baseline_difference: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    implementation_complexity: int | None = None


class BaseRepoCandidate(BaseModel):
    repo_url: str
    strategy: str
    confidence: float
    evidence: str


class DiffCluster(BaseModel):
    id: str
    label: str
    change_type: DiffChangeType
    files: list[str]
    summary: str
    semantic_tags: list[str] = Field(default_factory=list)
    related_cluster_ids: list[str] = Field(default_factory=list)


class ContributionMapping(BaseModel):
    diff_cluster_id: str
    contribution_id: str
    confidence: float
    evidence: str
    completeness: str
    implementation_coverage: float = 0.0
    coverage_type: CoverageType = CoverageType.PARTIAL
    missing_aspects: list[str] = Field(default_factory=list)
    engineering_divergences: list[str] = Field(default_factory=list)
    learning_entry_point: str | None = None
    reading_order: list[str] = Field(default_factory=list)


class AnalysisRuntimeMetadata(BaseModel):
    paper_source_kind: PaperSourceKind
    paper_fetch_mode: ProcessorMode
    parser_mode: ProcessorMode
    repo_tracer_mode: ProcessorMode
    diff_analyzer_mode: ProcessorMode
    contribution_mapper_mode: ProcessorMode
    selected_repo_strategy: str
    fallback_notes: list[str]


class AnalysisResult(BaseModel):
    case_slug: str
    summary: str
    selected_base_repo: BaseRepoCandidate
    base_repo_candidates: list[BaseRepoCandidate]
    contributions: list[PaperContribution]
    diff_clusters: list[DiffCluster]
    mappings: list[ContributionMapping]
    unmatched_contribution_ids: list[str] = Field(default_factory=list)
    unmatched_diff_cluster_ids: list[str] = Field(default_factory=list)
    metadata: AnalysisRuntimeMetadata
    warnings: list[str]


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    stage: JobStage | None = None
    paper_source: str
    repo_url: str
    summary: str | None = None
    error_message: str | None = None


class JobStatusResponse(JobSummary):
    result_available: bool


class HealthResponse(BaseModel):
    status: str
    database: str
    queue_mode: str
    live_by_default: bool
    live_paper_fetch: bool
    live_repo_trace: bool
    live_repo_analysis: bool
    llm_configured: bool
    supported_paper_source_kinds: list[PaperSourceKind]


class GoldenCaseExample(BaseModel):
    slug: str
    title: str
    paper_source: str
    repo_url: str


class JobsResponse(BaseModel):
    jobs: list[JobStatusResponse]


JsonDict = dict[str, Any]
