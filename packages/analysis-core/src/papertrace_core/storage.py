from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Engine, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    JobStage,
    JobStatus,
    JobStatusResponse,
    JobSummary,
)
from papertrace_core.settings import get_settings


class Base(DeclarativeBase):
    pass


class AnalysisJobRecord(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paper_source: Mapped[str] = mapped_column(String(2048), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


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
    Base.metadata.create_all(get_engine())


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
    record = AnalysisJobRecord(
        id=job_id,
        status=JobStatus.QUEUED.value,
        stage=None,
        paper_source=request.paper_source,
        repo_url=request.repo_url,
        summary=None,
        error_message=None,
        result_payload=None,
        created_at=now,
        updated_at=now,
    )
    with session_scope() as session:
        session.add(record)
    return JobSummary(
        id=job_id,
        status=JobStatus.QUEUED,
        stage=None,
        paper_source=request.paper_source,
        repo_url=request.repo_url,
        summary=None,
        error_message=None,
    )


def get_job_summary(job_id: str) -> JobStatusResponse | None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None:
            return None
        return JobStatusResponse(
            id=record.id,
            status=JobStatus(record.status),
            stage=JobStage(record.stage) if record.stage else None,
            paper_source=record.paper_source,
            repo_url=record.repo_url,
            summary=record.summary,
            error_message=record.error_message,
            result_available=record.result_payload is not None,
        )


def get_job_result(job_id: str) -> AnalysisResult | None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None or record.result_payload is None:
            return None
        return AnalysisResult.model_validate(record.result_payload)


def update_job_status(
    job_id: str,
    *,
    status: JobStatus,
    stage: JobStage | None = None,
    summary: str | None = None,
    error_message: str | None = None,
    result: AnalysisResult | None = None,
) -> None:
    with session_scope() as session:
        record = session.get(AnalysisJobRecord, job_id)
        if record is None:
            raise ValueError(f"Job not found: {job_id}")
        record.status = status.value
        record.stage = stage.value if stage is not None else None
        if summary is not None:
            record.summary = summary
        if error_message is not None:
            record.error_message = error_message
        if result is not None:
            record.result_payload = result.model_dump(mode="json")
        record.updated_at = datetime.now(UTC)


def list_jobs() -> list[JobStatusResponse]:
    with session_scope() as session:
        records = session.scalars(select(AnalysisJobRecord).order_by(AnalysisJobRecord.created_at.desc())).all()
        return [
            JobStatusResponse(
                id=record.id,
                status=JobStatus(record.status),
                stage=JobStage(record.stage) if record.stage else None,
                paper_source=record.paper_source,
                repo_url=record.repo_url,
                summary=record.summary,
                error_message=record.error_message,
                result_available=record.result_payload is not None,
            )
            for record in records
        ]
