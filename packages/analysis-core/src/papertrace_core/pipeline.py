from __future__ import annotations

from papertrace_core.models import AnalysisRequest, AnalysisResult, JobStage, JobStatus
from papertrace_core.services import build_default_analysis_service
from papertrace_core.storage import update_job_status


def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    service = build_default_analysis_service()
    return service.analyze(request)


def process_analysis_job(job_id: str, request: AnalysisRequest) -> AnalysisResult:
    try:
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.PAPER_FETCH)
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.PAPER_PARSE)
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.REPO_FETCH)
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.ANCESTRY_TRACE)
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.DIFF_ANALYZE)
        update_job_status(job_id, status=JobStatus.RUNNING, stage=JobStage.CONTRIBUTION_MAP)
        result = run_analysis(request)
        update_job_status(
            job_id,
            status=JobStatus.SUCCEEDED,
            stage=JobStage.PERSIST_RESULT,
            summary=result.summary,
            result=result,
        )
        return result
    except Exception as exc:
        update_job_status(
            job_id,
            status=JobStatus.FAILED,
            stage=JobStage.PERSIST_RESULT,
            error_message=str(exc),
        )
        raise
