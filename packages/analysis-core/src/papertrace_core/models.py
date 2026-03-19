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


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    paper_source: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)


class PaperContribution(BaseModel):
    id: str
    title: str
    section: str
    keywords: list[str]
    impl_hints: list[str]


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


class ContributionMapping(BaseModel):
    diff_cluster_id: str
    contribution_id: str
    confidence: float
    evidence: str
    completeness: str


class AnalysisResult(BaseModel):
    case_slug: str
    summary: str
    selected_base_repo: BaseRepoCandidate
    base_repo_candidates: list[BaseRepoCandidate]
    contributions: list[PaperContribution]
    diff_clusters: list[DiffCluster]
    mappings: list[ContributionMapping]
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


class GoldenCaseExample(BaseModel):
    slug: str
    title: str
    paper_source: str
    repo_url: str


class JobsResponse(BaseModel):
    jobs: list[JobStatusResponse]


JsonDict = dict[str, Any]
