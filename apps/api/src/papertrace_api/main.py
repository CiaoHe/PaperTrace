from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from json import JSONDecodeError

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from papertrace_core.cases import example_payloads
from papertrace_core.inputs import normalize_paper_source, normalize_repo_url
from papertrace_core.models import AnalysisRequest, HealthResponse, PaperSourceKind
from papertrace_core.settings import get_settings
from papertrace_core.storage import (
    create_job,
    get_engine,
    get_job_result,
    get_job_summary,
    init_db,
    list_jobs,
)
from papertrace_worker.tasks import enqueue_analysis
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
                                "required": ["paper_input", "repo_url"],
                                "properties": {
                                    "repo_url": {"type": "string"},
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
                        "required": ["repo_url"],
                        "properties": {
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
    settings = get_settings()
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            repo_url = form.get("repo_url")
            paper_source = form.get("paper_source")
            paper_source_kind = form.get("paper_source_kind")
            paper_file = form.get("paper_file")
            multipart_payload = CreateAnalysisMultipartRequest.model_validate(
                {
                    "repo_url": repo_url,
                    "paper_source": paper_source if isinstance(paper_source, str) else None,
                    "paper_source_kind": paper_source_kind if isinstance(paper_source_kind, str) else None,
                }
            )
            uploaded_file = paper_file if isinstance(paper_file, UploadFile) else None
            resolved_paper_source = (
                await persist_uploaded_pdf(uploaded_file, settings)
                if uploaded_file is not None
                else normalize_paper_source(str(multipart_payload.paper_source or ""))
            )
            analysis_request = AnalysisRequest(
                paper_source=resolved_paper_source,
                repo_url=normalize_repo_url(multipart_payload.repo_url),
            )
        else:
            raw_payload = await request.json()
            resolved_repo_url: str
            try:
                payload = CreateAnalysisRequest.model_validate(raw_payload)
                paper_source = payload.paper_input.source_ref
                resolved_repo_url = payload.repo_url
            except ValidationError:
                legacy_payload = LegacyCreateAnalysisRequest.model_validate(raw_payload)
                paper_source = legacy_payload.paper_source
                resolved_repo_url = legacy_payload.repo_url
            analysis_request = AnalysisRequest(
                paper_source=normalize_paper_source(paper_source),
                repo_url=normalize_repo_url(resolved_repo_url),
            )
    except HTTPException:
        raise
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    job = create_job(analysis_request)
    enqueue_analysis.delay(job.id, analysis_request.model_dump(mode="json"))
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
