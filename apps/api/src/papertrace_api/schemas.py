from __future__ import annotations

from typing import Annotated, Literal

from papertrace_core.models import (
    AnalysisResult,
    GoldenCaseExample,
    HealthResponse,
    JobsResponse,
    JobStatusResponse,
    PaperSourceKind,
)
from pydantic import BaseModel, Field


class ArxivPaperSourceInput(BaseModel):
    source_kind: Literal["arxiv"]
    source_ref: str = Field(min_length=1)


class PdfUrlPaperSourceInput(BaseModel):
    source_kind: Literal["pdf_url"]
    source_ref: str = Field(min_length=1)


class TextReferencePaperSourceInput(BaseModel):
    source_kind: Literal["text_reference"]
    source_ref: str = Field(min_length=1)


StructuredPaperSourceInput = Annotated[
    ArxivPaperSourceInput | PdfUrlPaperSourceInput | TextReferencePaperSourceInput,
    Field(discriminator="source_kind"),
]


class CreateAnalysisRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    paper_input: StructuredPaperSourceInput


class LegacyCreateAnalysisRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    paper_source: str = Field(min_length=1)


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
