from __future__ import annotations

from typing import Literal

from papertrace_core.models import (
    AnalysisResult,
    GoldenCaseExample,
    HealthResponse,
    JobsResponse,
    JobStatusResponse,
    PaperSourceKind,
)
from pydantic import BaseModel, Field, model_validator


class StructuredPaperSourceInput(BaseModel):
    source_kind: Literal["arxiv", "pdf_url", "text_reference"]
    source_ref: str = Field(min_length=1)


class CreateAnalysisRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    paper_source: str | None = Field(default=None, min_length=1)
    paper_input: StructuredPaperSourceInput | None = None

    @model_validator(mode="after")
    def validate_paper_input(self) -> CreateAnalysisRequest:
        if not self.paper_source and self.paper_input is None:
            raise ValueError("Either paper_source or paper_input is required")
        return self


class CreateAnalysisMultipartRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    paper_source: str | None = None
    paper_source_kind: PaperSourceKind | None = None


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
