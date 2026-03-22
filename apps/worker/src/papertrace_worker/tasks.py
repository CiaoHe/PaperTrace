from __future__ import annotations

from papertrace_core.diff_review.builder import build_review_artifact_for_job
from papertrace_core.diff_review.models import ReviewRefinementStatus
from papertrace_core.diff_review.refinement import refine_review_links_for_job
from papertrace_core.models import AnalysisRequest
from papertrace_core.pipeline import process_analysis_job
from papertrace_core.settings import get_settings
from papertrace_core.storage import ensure_review_session, mark_review_refinement_status, update_review_manifest

from papertrace_worker.celery_app import celery_app


@celery_app.task(name="papertrace.analysis.enqueue")  # type: ignore[untyped-decorator]
def enqueue_analysis(job_id: str, payload: dict[str, str]) -> dict[str, str]:
    request = AnalysisRequest.model_validate(payload)
    result = process_analysis_job(job_id, request)
    settings = get_settings()
    if settings.use_live_repo_analysis():
        ensure_review_session(job_id, paper_source=request.paper_source, current_repo_url=request.repo_url)
        build_review_artifact.delay(job_id)
    return {"job_id": job_id, "case_slug": result.case_slug}


@celery_app.task(name="papertrace.review.build")  # type: ignore[untyped-decorator]
def build_review_artifact(job_id: str) -> dict[str, str]:
    try:
        settings = get_settings()
        manifest = build_review_artifact_for_job(job_id)
        status = "ready" if manifest is not None else "pending"
        if manifest is not None and settings.llm_base_url and settings.llm_model:
            if manifest.refinement_status != ReviewRefinementStatus.QUEUED:
                manifest = manifest.model_copy(update={"refinement_status": ReviewRefinementStatus.QUEUED})
                update_review_manifest(job_id, manifest)
            mark_review_refinement_status(job_id, ReviewRefinementStatus.QUEUED, detail="LLM refinement queued.")
            refine_review_links.delay(job_id)
        return {"job_id": job_id, "status": status}
    except Exception as exc:
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


@celery_app.task(name="papertrace.review.refine")  # type: ignore[untyped-decorator]
def refine_review_links(job_id: str) -> dict[str, str]:
    try:
        manifest = refine_review_links_for_job(job_id)
        status = manifest.refinement_status.value if manifest is not None else "missing"
        return {"job_id": job_id, "status": status}
    except Exception as exc:
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
