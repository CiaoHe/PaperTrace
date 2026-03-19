from __future__ import annotations

from papertrace_core.models import (
    AnalysisResult,
    GoldenCaseExample,
    HealthResponse,
    JobsResponse,
    JobStatusResponse,
)
from pydantic import BaseModel, Field


class CreateAnalysisRequest(BaseModel):
    paper_source: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)


class CreateAnalysisResponse(BaseModel):
    job: JobStatusResponse


class ResultResponse(BaseModel):
    result: AnalysisResult


class ExamplesResponse(BaseModel):
    examples: list[GoldenCaseExample]


__all__ = [
    "CreateAnalysisRequest",
    "CreateAnalysisResponse",
    "ExamplesResponse",
    "ResultResponse",
    "HealthResponse",
    "JobsResponse",
]
