from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from json import JSONDecodeError

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from papertrace_core.cases import example_payloads
from papertrace_core.diff_review.models import ReviewBuildPhase, ReviewBuildStatus, ReviewRefinementStatus
from papertrace_core.inputs import normalize_paper_source, normalize_repo_url
from papertrace_core.models import AnalysisRequest, HealthResponse, JobStatus, PaperSourceKind
from papertrace_core.settings import get_settings
from papertrace_core.storage import (
    create_job,
    ensure_review_session,
    find_reusable_job_by_paper_source,
    get_engine,
    get_job_result,
    get_job_summary,
    get_review_file_payload,
    get_review_manifest,
    get_review_rendered_html,
    get_review_session_snapshot,
    get_review_status,
    init_db,
    list_jobs,
    reset_review_session_for_rebuild,
)
from papertrace_worker.tasks import build_review_artifact, dispatch_task, enqueue_analysis
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from papertrace_api.schemas import (
    CreateAnalysisMultipartRequest,
    CreateAnalysisRequest,
    CreateAnalysisResponse,
    ExamplesResponse,
    JobsResponse,
    LegacyCreateAnalysisRequest,
    ResultResponse,
    ReviewBuildStatusResponse,
    ReviewFileResponse,
    ReviewManifestResponse,
    ReviewUnavailableResponse,
)
from papertrace_api.uploads import persist_uploaded_pdf


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="PaperTrace API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3100",
        "http://localhost:3100",
    ],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def build_analysis_request_from_multipart(request: Request) -> tuple[AnalysisRequest, bool]:
    settings = get_settings()
    form = await request.form()
    repo_url = form.get("repo_url")
    paper_input = form.get("paper_input")
    paper_source = form.get("paper_source")
    paper_source_kind = form.get("paper_source_kind")
    paper_file = form.get("paper_file")
    raw_payload = {
        "repo_url": repo_url,
        "paper_input": paper_input if isinstance(paper_input, str) else None,
        "paper_source": paper_source if isinstance(paper_source, str) else None,
        "paper_source_kind": paper_source_kind if isinstance(paper_source_kind, str) else None,
    }
    force_reanalysis = form.get("force_reanalysis")
    if force_reanalysis is not None:
        raw_payload["force_reanalysis"] = force_reanalysis
    multipart_payload = CreateAnalysisMultipartRequest.model_validate(raw_payload)
    uploaded_file = paper_file if isinstance(paper_file, UploadFile) else None

    if uploaded_file is not None:
        if multipart_payload.paper_input is not None and multipart_payload.paper_input.source_kind != "pdf_file":
            raise ValueError("multipart paper_input.source_kind must be pdf_file when paper_file is provided")
        resolved_paper_source = await persist_uploaded_pdf(uploaded_file, settings)
    elif multipart_payload.paper_input is not None:
        if multipart_payload.paper_input.source_kind == "pdf_file":
            raise ValueError("paper_input.source_kind=pdf_file requires paper_file upload")
        resolved_paper_source = normalize_paper_source(str(multipart_payload.paper_input.source_ref))
    else:
        resolved_paper_source = normalize_paper_source(str(multipart_payload.paper_source or ""))

    return (
        AnalysisRequest(
            paper_source=resolved_paper_source,
            repo_url=normalize_repo_url(multipart_payload.repo_url) if multipart_payload.repo_url else "",
        ),
        multipart_payload.force_reanalysis,
    )


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    engine = get_engine()
    database_name = engine.url.get_backend_name()
    queue_mode = "celery"
    settings = get_settings()
    return HealthResponse(
        status="ok",
        database=database_name,
        queue_mode=queue_mode,
        live_by_default=settings.enable_live_by_default,
        live_paper_fetch=settings.use_live_paper_fetch(),
        live_repo_trace=settings.use_live_repo_trace(),
        live_repo_analysis=settings.use_live_repo_analysis(),
        llm_configured=bool(settings.llm_base_url and settings.llm_model),
        supported_paper_source_kinds=[
            PaperSourceKind.ARXIV,
            PaperSourceKind.PDF_URL,
            PaperSourceKind.PDF_FILE,
            PaperSourceKind.TEXT_REFERENCE,
        ],
    )


@app.head("/api/v1/health", status_code=status.HTTP_200_OK)
def health_head() -> Response:
    return Response(status_code=status.HTTP_200_OK)


@app.get("/api/v1/examples", response_model=ExamplesResponse)
def list_examples() -> ExamplesResponse:
    return ExamplesResponse(examples=example_payloads())


@app.get("/api/v1/analyses", response_model=JobsResponse)
def get_analyses() -> JobsResponse:
    return JobsResponse(jobs=list_jobs())


@app.post(
    "/api/v1/analyses",
    response_model=CreateAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": [
                            {
                                "type": "object",
                                "required": ["paper_input"],
                                "properties": {
                                    "repo_url": {"type": "string"},
                                    "force_reanalysis": {
                                        "type": "boolean",
                                        "default": False,
                                    },
                                    "paper_input": {
                                        "oneOf": [
                                            {
                                                "type": "object",
                                                "required": ["source_kind", "source_ref"],
                                                "properties": {
                                                    "source_kind": {"type": "string", "const": "arxiv"},
                                                    "source_ref": {"type": "string"},
                                                },
                                            },
                                            {
                                                "type": "object",
                                                "required": ["source_kind", "source_ref"],
                                                "properties": {
                                                    "source_kind": {"type": "string", "const": "pdf_url"},
                                                    "source_ref": {"type": "string"},
                                                },
                                            },
                                            {
                                                "type": "object",
                                                "required": ["source_kind", "source_ref"],
                                                "properties": {
                                                    "source_kind": {"type": "string", "const": "text_reference"},
                                                    "source_ref": {"type": "string"},
                                                },
                                            },
                                        ]
                                    },
                                },
                            },
                        ]
                    },
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "paper_input": {
                                "type": "string",
                                "description": (
                                    "JSON-encoded paper source envelope. "
                                    "Use source_kind=pdf_file together with paper_file uploads."
                                ),
                            },
                            "paper_source": {
                                "type": "string",
                                "description": "arXiv URL, PDF URL, or optional text hint when uploading a PDF",
                            },
                            "paper_source_kind": {
                                "type": "string",
                                "enum": ["arxiv", "pdf_url", "pdf_file", "text_reference"],
                                "description": "Optional explicit source-kind hint for non-file submissions.",
                            },
                            "repo_url": {"type": "string"},
                            "force_reanalysis": {"type": "boolean", "default": False},
                            "paper_file": {"type": "string", "format": "binary"},
                        },
                    }
                },
            },
        }
    },
)
async def create_analysis(
    request: Request,
) -> CreateAnalysisResponse:
    try:
        content_type = request.headers.get("content-type", "")
        force_reanalysis = False
        if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            analysis_request, force_reanalysis = await build_analysis_request_from_multipart(request)
        else:
            raw_payload = await request.json()
            resolved_repo_url: str
            try:
                payload = CreateAnalysisRequest.model_validate(raw_payload)
                paper_source = payload.paper_input.source_ref
                resolved_repo_url = payload.repo_url or ""
                force_reanalysis = payload.force_reanalysis
            except ValidationError:
                legacy_payload = LegacyCreateAnalysisRequest.model_validate(raw_payload)
                paper_source = legacy_payload.paper_source
                resolved_repo_url = legacy_payload.repo_url or ""
                force_reanalysis = legacy_payload.force_reanalysis
            analysis_request = AnalysisRequest(
                paper_source=normalize_paper_source(paper_source),
                repo_url=normalize_repo_url(resolved_repo_url) if resolved_repo_url else "",
            )
    except HTTPException:
        raise
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if not force_reanalysis:
        reusable_job = find_reusable_job_by_paper_source(analysis_request.paper_source)
        if reusable_job is not None:
            return CreateAnalysisResponse(job=reusable_job)
    job = create_job(analysis_request)
    dispatch_task(enqueue_analysis, job.id, analysis_request.model_dump(mode="json"))
    summary = get_job_summary(job.id)
    settings = get_settings()
    if summary is not None and settings.celery_task_always_eager and summary.status == JobStatus.QUEUED:
        enqueue_analysis.run(job.id, analysis_request.model_dump(mode="json"))
        summary = get_job_summary(job.id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Job not found",
        )
    return CreateAnalysisResponse(job=summary)


@app.get("/api/v1/analyses/{job_id}", response_model=CreateAnalysisResponse)
def get_analysis(job_id: str) -> CreateAnalysisResponse:
    summary = get_job_summary(job_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")
    return CreateAnalysisResponse(job=summary)


@app.get("/api/v1/analyses/{job_id}/result", response_model=ResultResponse)
def get_analysis_result(job_id: str) -> ResultResponse:
    result = get_job_result(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis result not available",
        )
    return ResultResponse(result=result)


@app.get(
    "/api/v1/analyses/{job_id}/review",
    response_model=None,
    responses={
        200: {"model": ReviewManifestResponse},
        202: {"model": ReviewBuildStatusResponse},
        409: {"model": ReviewUnavailableResponse},
    },
)
def get_analysis_review(job_id: str) -> ReviewManifestResponse | JSONResponse:
    summary = get_job_summary(job_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")

    manifest = get_review_manifest(job_id)
    if manifest is not None:
        return ReviewManifestResponse(review=manifest)

    review_status = get_review_status(job_id)
    if review_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")
    if summary.status.value == "failed" or isinstance(review_status, ReviewUnavailableResponse):
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=review_status.model_dump(mode="json"))
    session_snapshot = get_review_session_snapshot(job_id)
    should_enqueue = False

    if summary.status.value == "succeeded":
        if session_snapshot is None:
            ensure_review_session(job_id, paper_source=summary.paper_source, current_repo_url=summary.repo_url)
            should_enqueue = True
        elif session_snapshot.build_status == ReviewBuildStatus.READY:
            ensure_review_session(
                job_id,
                paper_source=summary.paper_source,
                current_repo_url=summary.repo_url,
                build_status=ReviewBuildStatus.PENDING,
                build_phase=ReviewBuildPhase.WAITING_FOR_ANALYSIS,
            )
            should_enqueue = True
        elif (
            session_snapshot.build_status == ReviewBuildStatus.PENDING
            and session_snapshot.build_phase == ReviewBuildPhase.WAITING_FOR_ANALYSIS
        ):
            should_enqueue = True

    if should_enqueue:
        dispatch_task(build_review_artifact, job_id)
        manifest = get_review_manifest(job_id)
        if manifest is not None:
            return ReviewManifestResponse(review=manifest)
        review_status = get_review_status(job_id)
        if review_status is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review status not found")
        if isinstance(review_status, ReviewUnavailableResponse):
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=review_status.model_dump(mode="json"))

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=review_status.model_dump(mode="json"))


@app.post(
    "/api/v1/analyses/{job_id}/review/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    responses={
        202: {"model": ReviewBuildStatusResponse},
        409: {"model": ReviewUnavailableResponse},
    },
)
def rebuild_analysis_review(job_id: str) -> Response:
    summary = get_job_summary(job_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")
    if summary.status.value == "failed":
        status_body = get_review_status(job_id)
        if status_body is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review status not found")
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=status_body.model_dump(mode="json"))
    settings = get_settings()
    ensure_review_session(job_id, paper_source=summary.paper_source, current_repo_url=summary.repo_url)
    reset_review_session_for_rebuild(
        job_id,
        refinement_status=(
            ReviewRefinementStatus.QUEUED
            if settings.llm_base_url and settings.llm_model
            else ReviewRefinementStatus.DISABLED
        ),
    )
    dispatch_task(build_review_artifact, job_id)
    refreshed_status = get_review_status(job_id)
    if refreshed_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review status not found")
    if isinstance(refreshed_status, ReviewUnavailableResponse):
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=refreshed_status.model_dump(mode="json"))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=refreshed_status.model_dump(mode="json"))


@app.get("/api/v1/analyses/{job_id}/review/files/{file_id}", response_model=ReviewFileResponse)
def get_analysis_review_file(job_id: str, file_id: str) -> ReviewFileResponse:
    payload = get_review_file_payload(job_id, file_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review file not available")
    return ReviewFileResponse(file=payload)


@app.get("/api/v1/analyses/{job_id}/review/files/{file_id}/rendered", response_class=HTMLResponse)
def get_analysis_review_file_rendered(job_id: str, file_id: str) -> HTMLResponse:
    html = get_review_rendered_html(job_id, file_id)
    if html is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered review file not available")
    return HTMLResponse(content=html)
