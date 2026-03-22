from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from papertrace_core.models import JobStatus


class ReviewBuildStatus(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class ReviewBuildPhase(StrEnum):
    WAITING_FOR_ANALYSIS = "waiting_for_analysis"
    FILE_MAPPING = "file_mapping"
    DIFF_GENERATION = "diff_generation"
    FALLBACK_RENDER = "fallback_render"
    CLAIM_EXTRACTION = "claim_extraction"
    DETERMINISTIC_LINKING = "deterministic_linking"
    PERSISTING = "persisting"
    DONE = "done"


class ReviewRefinementStatus(StrEnum):
    DISABLED = "disabled"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class ReviewDiffType(StrEnum):
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"


class ReviewMatchType(StrEnum):
    EXACT_PATH = "exact_path"
    CONTENT_MOVED = "content_moved"
    ADDED = "added"
    DELETED = "deleted"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"


class ReviewSemanticStatus(StrEnum):
    ENHANCED = "enhanced"
    FALLBACK_TEXT = "fallback_text"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    EQUIVALENT = "equivalent"
    NEW_FILE = "new_file"
    DELETED_FILE = "deleted_file"
    LARGE_FILE = "large_file"


class ReviewFallbackMode(StrEnum):
    NONE = "none"
    DIFF2HTML_PREBUILT = "diff2html_prebuilt"
    RAW_DIFF_ONLY = "raw_diff_only"


class ReviewContributionStatus(StrEnum):
    MAPPED = "mapped"
    PARTIALLY_MAPPED = "partially_mapped"
    UNMAPPED = "unmapped"
    REFINING = "refining"
    SKIPPED_NON_PRIMARY_LANGUAGE = "skipped_non_primary_language"


class ReviewBucketKind(StrEnum):
    PRIMARY = "primary"
    ADDED = "added"
    DELETED = "deleted"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    OTHER_LANGUAGES = "other_languages"
    LARGE_FILES = "large_files"


class ReviewStats(BaseModel):
    added_lines: int = 0
    removed_lines: int = 0
    changed_line_count: int = 0
    hunk_count: int = 0


class ReviewFileEntry(BaseModel):
    file_id: str
    source_path: str | None = None
    current_path: str | None = None
    diff_type: ReviewDiffType
    match_type: ReviewMatchType
    semantic_status: ReviewSemanticStatus
    language: str
    bucket: ReviewBucketKind
    significance: str
    linked_claim_count: int = 0
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_contribution_keys: list[str] = Field(default_factory=list)
    stats: ReviewStats = Field(default_factory=ReviewStats)


class ReviewBucket(BaseModel):
    label: str
    count: int
    files: list[ReviewFileEntry]


class ReviewClaimIndexEntry(BaseModel):
    claim_id: str
    claim_label: str
    contribution_key: str
    contribution_id: str
    section: str
    claim_text: str
    status: ReviewContributionStatus


class ReviewContributionStatusEntry(BaseModel):
    contribution_id: str
    contribution_key: str
    status: ReviewContributionStatus


class ReviewSummaryCounts(BaseModel):
    total_files: int = 0
    primary_files: int = 0
    total_claims: int = 0
    total_contributions: int = 0


class ReviewFileTreeNode(BaseModel):
    name: str
    path: str
    is_file: bool
    file_id: str | None = None
    changed_count: int = 0
    children: list[ReviewFileTreeNode] = Field(default_factory=list)


class ReviewManifest(BaseModel):
    source_repo: str
    current_repo: str
    source_revision: str
    current_revision: str
    file_tree: list[ReviewFileTreeNode]
    review_queue: list[ReviewFileEntry]
    secondary_buckets: dict[str, ReviewBucket]
    claim_index: list[ReviewClaimIndexEntry]
    contribution_status: list[ReviewContributionStatusEntry]
    summary_counts: ReviewSummaryCounts
    artifact_version: str
    cache_key: str
    refinement_status: ReviewRefinementStatus


class ReviewHunk(BaseModel):
    hunk_id: str
    old_start: int
    old_length: int
    new_start: int
    new_length: int
    added_count: int = 0
    removed_count: int = 0
    semantic_kind: str | None = None
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_contribution_keys: list[str] = Field(default_factory=list)


class ReviewFilePayload(BaseModel):
    file_id: str
    source_path: str | None = None
    current_path: str | None = None
    source_content: str | None = None
    current_content: str | None = None
    diff_type: ReviewDiffType
    match_type: ReviewMatchType
    semantic_status: ReviewSemanticStatus
    stats: ReviewStats
    raw_unified_diff: str
    hunks: list[ReviewHunk]
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_cluster_ids: list[str] = Field(default_factory=list)
    fallback_mode: ReviewFallbackMode = ReviewFallbackMode.NONE
    fallback_html_path: str | None = None


class ReviewBuildStatusResponse(BaseModel):
    analysis_status: JobStatus
    build_status: ReviewBuildStatus
    build_phase: ReviewBuildPhase
    build_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    files_total: int = 0
    files_done: int = 0
    current_file: str | None = None
    refinement_status: ReviewRefinementStatus
    detail: str


class ReviewUnavailableResponse(BaseModel):
    analysis_status: JobStatus
    build_error: str
    detail: str


class ReviewManifestSummary(BaseModel):
    source_repo: str
    current_repo: str
    source_revision: str
    current_revision: str
    summary_counts: ReviewSummaryCounts
    primary_queue_count: int = 0
    secondary_bucket_counts: dict[str, int] = Field(default_factory=dict)
    artifact_version: str
    cache_key: str
    refinement_status: ReviewRefinementStatus


class StoredReviewFilePayload(BaseModel):
    file_id: str
    source_path: str | None = None
    current_path: str | None = None
    source_content: str | None = None
    current_content: str | None = None
    diff_type: ReviewDiffType
    match_type: ReviewMatchType
    semantic_status: ReviewSemanticStatus
    stats: ReviewStats
    hunks: list[ReviewHunk]
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_cluster_ids: list[str] = Field(default_factory=list)
    fallback_mode: ReviewFallbackMode = ReviewFallbackMode.NONE
    fallback_html_path: str | None = None


ReviewFileTreeNode.model_rebuild()
