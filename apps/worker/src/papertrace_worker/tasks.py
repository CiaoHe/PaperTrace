from __future__ import annotations

from papertrace_core.models import AnalysisRequest
from papertrace_core.pipeline import process_analysis_job

from papertrace_worker.celery_app import celery_app


@celery_app.task(name="papertrace.analysis.enqueue")  # type: ignore[untyped-decorator]
def enqueue_analysis(job_id: str, payload: dict[str, str]) -> dict[str, str]:
    request = AnalysisRequest.model_validate(payload)
    result = process_analysis_job(job_id, request)
    return {"job_id": job_id, "case_slug": result.case_slug}
