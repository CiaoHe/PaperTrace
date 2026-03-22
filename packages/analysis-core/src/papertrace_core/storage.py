from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Engine, Float, String, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from papertrace_core.diff_review.models import (
    ReviewBuildPhase,
    ReviewBuildStatus,
    ReviewBuildStatusResponse,
    ReviewFilePayload,
    ReviewManifest,
    ReviewManifestSummary,
    ReviewRefinementStatus,
    ReviewUnavailableResponse,
    StoredReviewFilePayload,
)
from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    DiffCluster,
    JobStage,
    JobStatus,
    JobStatusResponse,
    JobSummary,
    JobTelemetryEvent,
    ProcessorMode,
)
from papertrace_core.repos import RepoAccessError, ShallowGitRepoMirror
from papertrace_core.services import (
    build_file_code_anchors,
    dedupe_code_anchors,
    extract_semantic_tags,
    load_repo_snapshot,
)
from papertrace_core.settings import get_settings


class Base(DeclarativeBase):
    pass


class AnalysisJobRecord(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    stage_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    paper_source: Mapped[str] = mapped_column(String(2048), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timeline_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisReviewSessionRecord(Base):
    __tablename__ = "analysis_review_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_job_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    paper_source_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source_repo_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    current_repo_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    current_revision: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    artifact_dir: Mapped[str] = mapped_column(String(4096), nullable=False, default="")
    manifest_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    build_status: Mapped[str] = mapped_column(String(32), nullable=False, default=ReviewBuildStatus.PENDING.value)
    build_phase: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=ReviewBuildPhase.WAITING_FOR_ANALYSIS.value,
    )
    build_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    files_total: Mapped[int] = mapped_column(nullable=False, default=0)
    files_done: Mapped[int] = mapped_column(nullable=False, default=0)
    current_file: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    build_error: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    artifact_size_kb: Mapped[int | None] = mapped_column(nullable=True)
    difftastic_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    refinement_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ReviewRefinementStatus.DISABLED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def reset_storage_state() -> None:
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        settings = get_settings()
        if settings.database_url.startswith("sqlite:///"):
            sqlite_path = settings.database_url.removeprefix("sqlite:///")
            if sqlite_path and sqlite_path != ":memory:":
                path = sqlite_path.replace("pysqlite:///", "")
                if path:
                    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        _ENGINE = create_engine(settings.database_url, future=True, connect_args=connect_args)
    return _ENGINE


def get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            future=True,
        )
    return _SESSION_FACTORY


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_analysis_jobs_schema(engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_job(request: AnalysisRequest) -> JobSummary:
    now = datetime.now(UTC)
    job_id = str(uuid4())
    initial_event = JobTelemetryEvent(
        timestamp=now,
        status=JobStatus.QUEUED,
        stage=None,
        progress=0.0,
        detail="Job accepted and queued for execution.",
    )
    record = AnalysisJobRecord(
        id=job_id,
        status=JobStatus.QUEUED.value,
        stage=None,
        stage_progress=0.0,
        stage_detail=initial_event.detail,
        paper_source=request.paper_source,
        repo_url=request.repo_url,
        summary=None,
        error_message=None,
        result_payload=None,
        timeline_payload=[initial_event.model_dump(mode="json")],
        created_at=now,
        updated_at=now,
    )
    with session_scope() as session:
        session.add(record)
    return JobSummary(
        id=job_id,
        status=JobStatus.QUEUED,
        stage=None,
        stage_progress=0.0,
        stage_detail=initial_event.detail,
        paper_source=request.paper_source,
        repo_url=request.repo_url,
        summary=None,
        error_message=None,
        timeline=[initial_event],
    )


def find_reusable_job_by_paper_source(paper_source: str) -> JobStatusResponse | None:
    reusable_statuses = (
        JobStatus.QUEUED.value,
        JobStatus.RUNNING.value,
        JobStatus.SUCCEEDED.value,
    )
    with session_scope() as session:
        statement = (
            select(AnalysisJobRecord)
            .where(AnalysisJobRecord.paper_source == paper_source)
            .where(AnalysisJobRecord.status.in_(reusable_statuses))
            .order_by(AnalysisJobRecord.created_at.desc())
        )
        record = session.scalars(statement).first()
        if record is None:
            return None
        return _build_job_status_response(record)


def get_job_summary(job_id: str) -> JobStatusResponse | None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None:
            return None
        return _build_job_status_response(record)


def get_job_result(job_id: str) -> AnalysisResult | None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None or record.result_payload is None:
            return None
        result = AnalysisResult.model_validate(record.result_payload)
        enriched = enrich_analysis_result_with_code_anchors(result, record.repo_url)
        if enriched.model_dump(mode="json") != record.result_payload:
            record.result_payload = enriched.model_dump(mode="json")
        return enriched


def update_job_status(
    job_id: str,
    *,
    status: JobStatus,
    stage: JobStage | None = None,
    stage_progress: float | None = None,
    stage_detail: str | None = None,
    summary: str | None = None,
    error_message: str | None = None,
    result: AnalysisResult | None = None,
    repo_url: str | None = None,
) -> None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None:
            raise ValueError(f"Job not found: {job_id}")
        previous_stage = JobStage(record.stage) if record.stage else None
        record.status = status.value
        record.stage = stage.value if stage is not None else None
        if stage is None:
            record.stage_progress = None
            record.stage_detail = stage_detail
        else:
            if stage_progress is not None:
                record.stage_progress = max(0.0, min(1.0, stage_progress))
            elif previous_stage != stage:
                record.stage_progress = 0.0
            if stage_detail is not None:
                record.stage_detail = stage_detail
            elif previous_stage != stage:
                record.stage_detail = None
        if summary is not None:
            record.summary = summary
        if repo_url is not None:
            record.repo_url = repo_url
        if error_message is not None:
            record.error_message = error_message
        if result is not None:
            record.result_payload = result.model_dump(mode="json")
        timeline = _load_timeline(record.timeline_payload)
        next_event = JobTelemetryEvent(
            timestamp=datetime.now(UTC),
            status=status,
            stage=stage,
            progress=record.stage_progress,
            detail=record.stage_detail,
        )
        if _should_append_timeline_event(timeline, next_event):
            timeline.append(next_event)
        record.timeline_payload = [event.model_dump(mode="json") for event in timeline]
        record.updated_at = datetime.now(UTC)


def replace_job_result(job_id: str, result: AnalysisResult) -> None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None:
            raise ValueError(f"Job not found: {job_id}")
        record.result_payload = result.model_dump(mode="json")
        record.updated_at = datetime.now(UTC)


def list_jobs() -> list[JobStatusResponse]:
    with session_scope() as session:
        records = session.scalars(select(AnalysisJobRecord).order_by(AnalysisJobRecord.created_at.desc())).all()
        return [_build_job_status_response(record) for record in records]


def ensure_review_session(
    job_id: str,
    *,
    paper_source: str,
    current_repo_url: str,
    cache_key: str = "",
    source_repo_url: str = "",
    source_revision: str = "",
    current_revision: str = "",
    artifact_dir: Path | None = None,
    build_status: ReviewBuildStatus = ReviewBuildStatus.PENDING,
    build_phase: ReviewBuildPhase = ReviewBuildPhase.WAITING_FOR_ANALYSIS,
) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        record = session.scalars(
            select(AnalysisReviewSessionRecord).where(AnalysisReviewSessionRecord.analysis_job_id == job_id)
        ).first()
        if record is None:
            record = AnalysisReviewSessionRecord(
                id=str(uuid4()),
                analysis_job_id=job_id,
                cache_key=cache_key,
                paper_source_hash=_sha256_short(paper_source),
                source_repo_url=source_repo_url,
                current_repo_url=current_repo_url,
                source_revision=source_revision,
                current_revision=current_revision,
                artifact_dir=str(artifact_dir.resolve()) if artifact_dir is not None else "",
                manifest_summary_json={},
                build_status=build_status.value,
                build_phase=build_phase.value,
                build_progress=0.0,
                files_total=0,
                files_done=0,
                current_file=None,
                build_error=None,
                artifact_size_kb=None,
                difftastic_used=False,
                refinement_status=ReviewRefinementStatus.DISABLED.value,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            return
        if cache_key:
            record.cache_key = cache_key
        record.paper_source_hash = _sha256_short(paper_source)
        if source_repo_url:
            record.source_repo_url = source_repo_url
        if current_repo_url:
            record.current_repo_url = current_repo_url
        if source_revision:
            record.source_revision = source_revision
        if current_revision:
            record.current_revision = current_revision
        if artifact_dir is not None:
            record.artifact_dir = str(artifact_dir.resolve())
        record.build_status = build_status.value
        record.build_phase = build_phase.value
        record.updated_at = now


def reset_review_session_for_rebuild(
    job_id: str,
    *,
    refinement_status: ReviewRefinementStatus,
) -> None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.build_status = ReviewBuildStatus.PENDING.value
        record.build_phase = ReviewBuildPhase.WAITING_FOR_ANALYSIS.value
        record.build_progress = 0.0
        record.files_total = 0
        record.files_done = 0
        record.current_file = None
        record.build_error = None
        record.refinement_status = refinement_status.value
        record.manifest_summary_json = {
            **(record.manifest_summary_json if isinstance(record.manifest_summary_json, dict) else {}),
            "detail": "Review rebuild queued.",
        }
        record.updated_at = datetime.now(UTC)


def mark_review_session_building(
    job_id: str,
    *,
    build_phase: ReviewBuildPhase,
    build_progress: float | None = None,
    files_total: int | None = None,
    files_done: int | None = None,
    current_file: str | None = None,
    detail: str | None = None,
) -> None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.build_status = ReviewBuildStatus.BUILDING.value
        record.build_phase = build_phase.value
        if build_progress is not None:
            record.build_progress = max(0.0, min(1.0, build_progress))
        if files_total is not None:
            record.files_total = files_total
        if files_done is not None:
            record.files_done = files_done
        record.current_file = current_file
        if detail is not None:
            record.manifest_summary_json = {**record.manifest_summary_json, "detail": detail}
        record.updated_at = datetime.now(UTC)


def mark_review_session_ready(job_id: str, *, manifest: ReviewManifest, artifact_dir: Path) -> None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.cache_key = manifest.cache_key
        record.source_repo_url = manifest.source_repo
        record.current_repo_url = manifest.current_repo
        record.source_revision = manifest.source_revision
        record.current_revision = manifest.current_revision
        record.artifact_dir = str(artifact_dir.resolve())
        record.manifest_summary_json = ReviewManifestSummary(
            source_repo=manifest.source_repo,
            current_repo=manifest.current_repo,
            source_revision=manifest.source_revision,
            current_revision=manifest.current_revision,
            summary_counts=manifest.summary_counts,
            artifact_version=manifest.artifact_version,
            cache_key=manifest.cache_key,
            refinement_status=manifest.refinement_status,
        ).model_dump(mode="json")
        record.build_status = ReviewBuildStatus.READY.value
        record.build_phase = ReviewBuildPhase.DONE.value
        record.build_progress = 1.0
        record.files_total = manifest.summary_counts.total_files
        record.files_done = manifest.summary_counts.total_files
        record.current_file = None
        record.build_error = None
        record.artifact_size_kb = _artifact_size_kb(artifact_dir)
        record.refinement_status = manifest.refinement_status.value
        record.updated_at = datetime.now(UTC)


def mark_review_session_failed(job_id: str, error: str) -> None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.build_status = ReviewBuildStatus.FAILED.value
        record.build_error = error
        record.updated_at = datetime.now(UTC)


def mark_review_refinement_status(
    job_id: str,
    status: ReviewRefinementStatus,
    *,
    detail: str | None = None,
) -> None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.refinement_status = status.value
        if detail is not None:
            record.manifest_summary_json = {**record.manifest_summary_json, "detail": detail}
        record.updated_at = datetime.now(UTC)


def get_review_artifact_dir(job_id: str) -> Path | None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None or not record.artifact_dir:
            return None
        return Path(record.artifact_dir)


def update_review_manifest(job_id: str, manifest: ReviewManifest) -> None:
    artifact_dir = get_review_artifact_dir(job_id)
    if artifact_dir is None:
        raise ValueError(f"Review artifact directory not found: {job_id}")
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            raise ValueError(f"Review session not found: {job_id}")
        record.manifest_summary_json = ReviewManifestSummary(
            source_repo=manifest.source_repo,
            current_repo=manifest.current_repo,
            source_revision=manifest.source_revision,
            current_revision=manifest.current_revision,
            summary_counts=manifest.summary_counts,
            artifact_version=manifest.artifact_version,
            cache_key=manifest.cache_key,
            refinement_status=manifest.refinement_status,
        ).model_dump(mode="json")
        record.refinement_status = manifest.refinement_status.value
        record.updated_at = datetime.now(UTC)


def update_review_file_payload(job_id: str, file_id: str, payload: StoredReviewFilePayload) -> None:
    artifact_dir = get_review_artifact_dir(job_id)
    if artifact_dir is None:
        raise ValueError(f"Review artifact directory not found: {job_id}")
    file_path = artifact_dir / "files" / f"{file_id}.json"
    file_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def get_review_status(job_id: str) -> ReviewBuildStatusResponse | ReviewUnavailableResponse | None:
    summary = get_job_summary(job_id)
    if summary is None:
        return None
    if summary.status == JobStatus.FAILED:
        return ReviewUnavailableResponse(
            analysis_status=summary.status,
            build_error=summary.error_message or "Analysis failed before review build could start.",
            detail="Analysis failed and review build is unavailable.",
        )
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None:
            return ReviewBuildStatusResponse(
                analysis_status=summary.status,
                build_status=ReviewBuildStatus.PENDING,
                build_phase=ReviewBuildPhase.WAITING_FOR_ANALYSIS,
                build_progress=0.0,
                files_total=0,
                files_done=0,
                current_file=None,
                refinement_status=ReviewRefinementStatus.DISABLED,
                detail="Review build has not started.",
            )
        if record.build_status == ReviewBuildStatus.FAILED.value:
            return ReviewUnavailableResponse(
                analysis_status=summary.status,
                build_error=record.build_error or "Review build failed.",
                detail="Review build failed and the artifact is unavailable.",
            )
        detail = ""
        if isinstance(record.manifest_summary_json, dict):
            detail = str(record.manifest_summary_json.get("detail", ""))
        if not detail:
            detail = (
                "Review build is in progress." if record.build_status != ReviewBuildStatus.READY.value else "Ready."
            )
        return ReviewBuildStatusResponse(
            analysis_status=summary.status,
            build_status=ReviewBuildStatus(record.build_status),
            build_phase=ReviewBuildPhase(record.build_phase),
            build_progress=record.build_progress,
            files_total=record.files_total,
            files_done=record.files_done,
            current_file=record.current_file,
            refinement_status=ReviewRefinementStatus(record.refinement_status),
            detail=detail,
        )


def get_review_manifest(job_id: str) -> ReviewManifest | None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None or record.build_status != ReviewBuildStatus.READY.value or not record.artifact_dir:
            return None
        manifest_path = Path(record.artifact_dir) / "manifest.json"
        if not manifest_path.exists():
            return None
        return ReviewManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def get_review_file_payload(job_id: str, file_id: str) -> ReviewFilePayload | None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None or record.build_status != ReviewBuildStatus.READY.value or not record.artifact_dir:
            return None
        file_path = Path(record.artifact_dir) / "files" / f"{file_id}.json"
        raw_diff_path = Path(record.artifact_dir) / "raw" / f"{file_id}.diff"
        if not file_path.exists() or not raw_diff_path.exists():
            return None
        stored = StoredReviewFilePayload.model_validate_json(file_path.read_text(encoding="utf-8"))
        return ReviewFilePayload(
            file_id=stored.file_id,
            source_path=stored.source_path,
            current_path=stored.current_path,
            source_content=stored.source_content,
            current_content=stored.current_content,
            diff_type=stored.diff_type,
            match_type=stored.match_type,
            semantic_status=stored.semantic_status,
            stats=stored.stats,
            raw_unified_diff=raw_diff_path.read_text(encoding="utf-8"),
            hunks=stored.hunks,
            linked_claim_ids=stored.linked_claim_ids,
            linked_cluster_ids=stored.linked_cluster_ids,
            fallback_mode=stored.fallback_mode,
            fallback_html_path=stored.fallback_html_path,
        )


def get_review_rendered_html(job_id: str, file_id: str) -> str | None:
    with session_scope() as session:
        record = _get_review_session_record(session, job_id)
        if record is None or record.build_status != ReviewBuildStatus.READY.value or not record.artifact_dir:
            return None
        rendered_path = Path(record.artifact_dir) / "rendered" / f"{file_id}.html"
        if not rendered_path.exists():
            return None
        return rendered_path.read_text(encoding="utf-8")


def enrich_analysis_result_with_code_anchors(result: AnalysisResult, repo_url: str) -> AnalysisResult:
    if not result.diff_clusters:
        return result
    if any(cluster.code_anchors for cluster in result.diff_clusters):
        return result

    settings = get_settings()
    repo_mirror = ShallowGitRepoMirror(settings)
    try:
        base_root = repo_mirror.prepare(result.selected_base_repo.repo_url)
        target_root = repo_mirror.prepare(repo_url)
        base_snapshot = load_repo_snapshot(base_root, settings)
        target_snapshot = load_repo_snapshot(target_root, settings)
    except RepoAccessError:
        return result

    enriched_clusters = [
        _enrich_cluster_code_anchors(cluster, base_snapshot, target_snapshot, result)
        for cluster in result.diff_clusters
    ]
    if all(not cluster.code_anchors for cluster in enriched_clusters):
        return result

    return result.model_copy(update={"diff_clusters": enriched_clusters})


def _enrich_cluster_code_anchors(
    cluster: DiffCluster,
    base_snapshot: dict[str, str],
    target_snapshot: dict[str, str],
    result: AnalysisResult,
) -> DiffCluster:
    if cluster.code_anchors:
        return cluster

    anchors = []
    semantic_tags = list(cluster.semantic_tags)
    for file_path in cluster.files:
        target_content = target_snapshot.get(file_path)
        if target_content is None:
            continue
        base_content = base_snapshot.get(file_path)
        if not semantic_tags:
            semantic_tags = extract_semantic_tags(file_path, target_content, result.contributions)
        anchors.extend(
            build_file_code_anchors(
                file_path,
                file_path if base_content is not None else None,
                base_content,
                target_content,
                semantic_tags,
                result.contributions,
                cluster.summary,
            )
        )

    deduped = dedupe_code_anchors(anchors)[:6]
    if not deduped and result.metadata.diff_analyzer_mode == ProcessorMode.FIXTURE:
        return cluster
    return cluster.model_copy(update={"code_anchors": deduped, "semantic_tags": semantic_tags})


def _ensure_analysis_jobs_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(AnalysisJobRecord.__tablename__):
        return

    column_names = {column["name"] for column in inspector.get_columns(AnalysisJobRecord.__tablename__)}
    alter_statements: list[str] = []
    if "stage_progress" not in column_names:
        alter_statements.append("ALTER TABLE analysis_jobs ADD COLUMN stage_progress FLOAT")
    if "stage_detail" not in column_names:
        alter_statements.append("ALTER TABLE analysis_jobs ADD COLUMN stage_detail VARCHAR(1024)")
    if "timeline_payload" not in column_names:
        alter_statements.append("ALTER TABLE analysis_jobs ADD COLUMN timeline_payload JSON")

    if not alter_statements:
        return

    with engine.begin() as connection:
        for statement in alter_statements:
            connection.execute(text(statement))
        connection.execute(text("UPDATE analysis_jobs SET timeline_payload = '[]' WHERE timeline_payload IS NULL"))


def _load_timeline(payload: list[dict[str, Any]] | None) -> list[JobTelemetryEvent]:
    if not payload:
        return []
    return [JobTelemetryEvent.model_validate(item) for item in payload]


def _should_append_timeline_event(
    timeline: list[JobTelemetryEvent],
    next_event: JobTelemetryEvent,
) -> bool:
    if not timeline:
        return True
    latest_event = timeline[-1]
    return (
        latest_event.status != next_event.status
        or latest_event.stage != next_event.stage
        or latest_event.progress != next_event.progress
        or latest_event.detail != next_event.detail
    )


def _build_job_status_response(record: AnalysisJobRecord) -> JobStatusResponse:
    return JobStatusResponse(
        id=record.id,
        status=JobStatus(record.status),
        stage=JobStage(record.stage) if record.stage else None,
        stage_progress=record.stage_progress,
        stage_detail=record.stage_detail,
        paper_source=record.paper_source,
        repo_url=record.repo_url,
        summary=record.summary,
        error_message=record.error_message,
        timeline=_load_timeline(record.timeline_payload),
        result_available=record.result_payload is not None,
    )


def _get_review_session_record(session: Session, job_id: str) -> AnalysisReviewSessionRecord | None:
    return session.scalars(
        select(AnalysisReviewSessionRecord).where(AnalysisReviewSessionRecord.analysis_job_id == job_id)
    ).first()


def _artifact_size_kb(artifact_dir: Path) -> int:
    total_bytes = 0
    for path in artifact_dir.rglob("*"):
        if path.is_file():
            total_bytes += path.stat().st_size
    return total_bytes // 1024


def _sha256_short(value: str, *, length: int = 16) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
