from __future__ import annotations

from papertrace_core.interfaces import StageProgressCallback
from papertrace_core.models import AnalysisRequest, AnalysisResult, JobStage, JobStatus
from papertrace_core.services import build_default_analysis_service
from papertrace_core.storage import update_job_status


def run_analysis(
    request: AnalysisRequest,
    *,
    progress: StageProgressCallback | None = None,
) -> AnalysisResult:
    service = build_default_analysis_service()
    return service.analyze(request, progress=progress)


def process_analysis_job(job_id: str, request: AnalysisRequest) -> AnalysisResult:
    active_stage: JobStage | None = None

    def on_progress(stage: JobStage, stage_progress: float, detail: str) -> None:
        nonlocal active_stage
        active_stage = stage
        update_job_status(
            job_id,
            status=JobStatus.RUNNING,
            stage=stage,
            stage_progress=stage_progress,
            stage_detail=detail,
        )

    try:
        update_job_status(
            job_id,
            status=JobStatus.RUNNING,
            stage=JobStage.PAPER_FETCH,
            stage_progress=0.0,
            stage_detail="Worker started analysis execution.",
        )
        result = run_analysis(request, progress=on_progress)
        update_job_status(
            job_id,
            stage=JobStage.PERSIST_RESULT,
            status=JobStatus.RUNNING,
            stage_progress=0.0,
            stage_detail="Persisting final analysis result.",
        )
        active_stage = JobStage.PERSIST_RESULT
        update_job_status(
            job_id,
            status=JobStatus.SUCCEEDED,
            stage=JobStage.PERSIST_RESULT,
            stage_progress=1.0,
            stage_detail="Analysis result persisted.",
            summary=result.summary,
            result=result,
        )
        return result
    except Exception as exc:
        update_job_status(
            job_id,
            status=JobStatus.FAILED,
            stage=active_stage,
            stage_progress=None,
            stage_detail=f"Analysis failed during {(active_stage or JobStage.PAPER_FETCH).value}.",
            error_message=str(exc),
        )
        raise
