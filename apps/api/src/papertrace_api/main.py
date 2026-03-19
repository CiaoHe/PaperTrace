from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from papertrace_core.cases import example_payloads
from papertrace_core.inputs import normalize_paper_source, normalize_repo_url
from papertrace_core.models import AnalysisRequest, HealthResponse
from papertrace_core.storage import (
    create_job,
    get_engine,
    get_job_result,
    get_job_summary,
    init_db,
    list_jobs,
)
from papertrace_worker.tasks import enqueue_analysis

from papertrace_api.schemas import (
    CreateAnalysisRequest,
    CreateAnalysisResponse,
    ExamplesResponse,
    JobsResponse,
    ResultResponse,
)


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
    return HealthResponse(status="ok", database=database_name, queue_mode=queue_mode)


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
)
def create_analysis(payload: CreateAnalysisRequest) -> CreateAnalysisResponse:
    try:
        request = AnalysisRequest(
            paper_source=normalize_paper_source(payload.paper_source),
            repo_url=normalize_repo_url(payload.repo_url),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    job = create_job(request)
    enqueue_analysis.delay(job.id, request.model_dump(mode="json"))
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
