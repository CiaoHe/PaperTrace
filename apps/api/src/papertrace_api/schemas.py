from __future__ import annotations

from json import loads
from typing import Annotated, Literal

from papertrace_core.diff_review.models import (
    ReviewBuildStatusResponse,
    ReviewFilePayload,
    ReviewManifest,
    ReviewUnavailableResponse,
)
from papertrace_core.models import (
    AnalysisResult,
    GoldenCaseExample,
    HealthResponse,
    JobsResponse,
    JobStatusResponse,
    PaperSourceKind,
)
from pydantic import BaseModel, Field, model_validator


class ArxivPaperSourceInput(BaseModel):
    source_kind: Literal["arxiv"]
    source_ref: str = Field(min_length=1)


class PdfUrlPaperSourceInput(BaseModel):
    source_kind: Literal["pdf_url"]
    source_ref: str = Field(min_length=1)


class TextReferencePaperSourceInput(BaseModel):
    source_kind: Literal["text_reference"]
    source_ref: str = Field(min_length=1)


class PdfFilePaperSourceInput(BaseModel):
    source_kind: Literal["pdf_file"]
    source_ref: str | None = None


StructuredPaperSourceInput = Annotated[
    ArxivPaperSourceInput | PdfUrlPaperSourceInput | TextReferencePaperSourceInput,
    Field(discriminator="source_kind"),
]

MultipartPaperSourceInput = Annotated[
    ArxivPaperSourceInput | PdfUrlPaperSourceInput | TextReferencePaperSourceInput | PdfFilePaperSourceInput,
    Field(discriminator="source_kind"),
]


class CreateAnalysisRequest(BaseModel):
    repo_url: str | None = None
    paper_input: StructuredPaperSourceInput
    force_reanalysis: bool = False


class LegacyCreateAnalysisRequest(BaseModel):
    repo_url: str | None = None
    paper_source: str = Field(min_length=1)
    force_reanalysis: bool = False


class CreateAnalysisMultipartRequest(BaseModel):
    repo_url: str | None = None
    paper_input: MultipartPaperSourceInput | None = None
    paper_source: str | None = None
    paper_source_kind: PaperSourceKind | None = None
    force_reanalysis: bool = False

    @model_validator(mode="before")
    @classmethod
    def decode_paper_input(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        paper_input = value.get("paper_input")
        if isinstance(paper_input, str):
            value = dict(value)
            if not paper_input.strip():
                value["paper_input"] = None
                return value
            decoded = loads(paper_input)
            if not isinstance(decoded, dict):
                raise ValueError("paper_input must decode to an object")
            value["paper_input"] = decoded
        return value


class CreateAnalysisResponse(BaseModel):
    job: JobStatusResponse


class ResultResponse(BaseModel):
    result: AnalysisResult


class ReviewManifestResponse(BaseModel):
    review: ReviewManifest


class ReviewFileResponse(BaseModel):
    file: ReviewFilePayload


class ExamplesResponse(BaseModel):
    examples: list[GoldenCaseExample]


__all__ = [
    "CreateAnalysisRequest",
    "CreateAnalysisResponse",
    "ExamplesResponse",
    "ResultResponse",
    "ReviewBuildStatusResponse",
    "ReviewFileResponse",
    "ReviewManifestResponse",
    "ReviewUnavailableResponse",
    "HealthResponse",
    "JobsResponse",
]
