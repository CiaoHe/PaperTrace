from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx
from pypdf import PdfReader

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import PaperFixture, load_paper_fixture
from papertrace_core.inputs import detect_paper_source_kind, extract_arxiv_id
from papertrace_core.interfaces import FetchOutput, PaperSourceFetcher, StageProgressCallback
from papertrace_core.models import (
    AnalysisRequest,
    JobStage,
    PaperDocument,
    PaperSection,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.settings import Settings

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ABSTRACT_RE = re.compile(
    r"\babstract\b[:\s]*(.+?)(?:\n\s*\n|\n(?:1[\.\s]|introduction\b)|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
PDF_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*)?\s*(abstract|introduction|background|related work|our contributions|contributions|"
    r"method|methods|approach|architecture|experiments|evaluation|results|discussion|conclusion)s?$",
    flags=re.IGNORECASE,
)


class PaperFetchError(RuntimeError):
    pass


def paper_document_from_fixture(
    request: AnalysisRequest,
    paper_fixture: PaperFixture,
) -> PaperDocument:
    return PaperDocument(
        source_kind=PaperSourceKind.TEXT_REFERENCE,
        source_ref=request.paper_source,
        title=paper_fixture.title,
        abstract="",
        sections=[],
        text=paper_fixture.text,
    )


def build_arxiv_text(title: str, abstract: str) -> str:
    parts = [title.strip()]
    if abstract.strip():
        parts.extend(["Abstract", abstract.strip()])
    return "\n\n".join(parts)


def compact_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def infer_pdf_title(text: str, source_ref: str) -> str:
    for line in compact_text(text).splitlines():
        if len(line) >= 12:
            return line[:200]
    return Path(source_ref).stem.replace("_", " ").replace("-", " ") or "Untitled PDF"


def infer_pdf_abstract(text: str) -> str:
    compact = compact_text(text)
    if not compact:
        return ""
    match = ABSTRACT_RE.search(compact)
    if match is not None:
        return match.group(1).strip()[:1200]
    lines = compact.splitlines()
    if len(lines) <= 1:
        return compact[:1200]
    return "\n".join(lines[1:4])[:1200]


def infer_pdf_sections(text: str) -> list[PaperSection]:
    sections: list[PaperSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush_section() -> None:
        nonlocal current_heading, current_lines
        if current_heading is None:
            current_lines = []
            return
        body = "\n".join(line for line in current_lines if line).strip()
        if body:
            sections.append(PaperSection(heading=current_heading, text=body))
        current_lines = []

    for raw_line in compact_text(text).splitlines():
        line = raw_line.strip()
        if len(line) <= 80 and PDF_HEADING_RE.match(line):
            flush_section()
            current_heading = line
            continue
        if current_heading is not None:
            current_lines.append(line)

    flush_section()
    return sections


def build_pdf_document(
    text_by_page: list[str],
    source_kind: PaperSourceKind,
    source_ref: str,
    metadata_title: str | None = None,
) -> PaperDocument:
    non_empty_pages = [compact_text(page) for page in text_by_page if compact_text(page)]
    text = "\n\n".join(non_empty_pages).strip()
    if not text:
        raise PaperFetchError(f"PDF source {source_ref} did not yield extractable text")

    title = (metadata_title or "").strip() or infer_pdf_title(text, source_ref)
    abstract = infer_pdf_abstract(text)
    sections = infer_pdf_sections(text)
    if not sections:
        sections = [
            PaperSection(heading=f"Page {index}", text=page_text)
            for index, page_text in enumerate(non_empty_pages, start=1)
        ]
    return PaperDocument(
        source_kind=source_kind,
        source_ref=source_ref,
        title=title,
        abstract=abstract,
        sections=sections,
        text=text,
    )


@dataclass(frozen=True)
class FixturePaperSourceFetcher:
    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput:
        if progress is not None:
            progress(JobStage.PAPER_FETCH, 0.3, "Loading fixture paper content.")
        fixture = load_paper_fixture(detect_case_slug(request))
        if progress is not None:
            progress(JobStage.PAPER_FETCH, 1.0, f"Loaded fixture paper content for {fixture.title}.")
        return FetchOutput(
            paper_document=paper_document_from_fixture(request, fixture),
            mode=ProcessorMode.FIXTURE,
            warnings=[],
        )


@dataclass(frozen=True)
class ArxivPaperSourceFetcher:
    settings: Settings
    client: httpx.Client | None = None

    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput:
        arxiv_id = extract_arxiv_id(request.paper_source)
        if arxiv_id is None:
            raise PaperFetchError("Paper source does not contain a valid arXiv identifier")
        if progress is not None:
            progress(JobStage.PAPER_FETCH, 0.15, f"Fetching arXiv metadata for {arxiv_id}.")

        close_client = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.arxiv_timeout_seconds)
        endpoint = f"{self.settings.arxiv_api_base_url.rstrip('/')}/api/query"

        try:
            response = client.get(endpoint, params={"id_list": arxiv_id})
            response.raise_for_status()
            if progress is not None:
                progress(JobStage.PAPER_FETCH, 0.65, f"Received arXiv response for {arxiv_id}.")
            root = ET.fromstring(response.text)
            entry = root.find("atom:entry", ATOM_NS)
            if entry is None:
                raise PaperFetchError(f"No arXiv entry returned for {arxiv_id}")

            title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
            abstract = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
            if not title or not abstract:
                raise PaperFetchError(f"Incomplete arXiv metadata returned for {arxiv_id}")
        except (ET.ParseError, httpx.HTTPError) as exc:
            raise PaperFetchError(f"Failed to fetch arXiv content for {arxiv_id}: {exc}") from exc
        finally:
            if close_client:
                client.close()

        if progress is not None:
            progress(JobStage.PAPER_FETCH, 1.0, f"Loaded arXiv abstract for {title}.")
        return FetchOutput(
            paper_document=PaperDocument(
                source_kind=PaperSourceKind.ARXIV,
                source_ref=f"https://arxiv.org/abs/{arxiv_id}",
                title=title,
                abstract=abstract,
                sections=[PaperSection(heading="Abstract", text=abstract)],
                text=build_arxiv_text(title, abstract),
            ),
            mode=ProcessorMode.REMOTE_FETCH,
            warnings=[],
        )


@dataclass(frozen=True)
class PdfPaperSourceFetcher:
    settings: Settings
    client: httpx.Client | None = None

    def _load_pdf_reader(self, request: AnalysisRequest) -> tuple[PdfReader, PaperSourceKind, str]:
        paper_source_kind = detect_paper_source_kind(request.paper_source)
        if paper_source_kind == PaperSourceKind.PDF_FILE:
            pdf_path = Path(request.paper_source)
            if not pdf_path.is_file():
                raise PaperFetchError(f"PDF file does not exist: {pdf_path}")
            return PdfReader(str(pdf_path)), paper_source_kind, str(pdf_path)

        if paper_source_kind != PaperSourceKind.PDF_URL:
            raise PaperFetchError("Paper source is not a supported PDF URL or PDF file path")

        close_client = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.pdf_fetch_timeout_seconds)
        try:
            response = client.get(request.paper_source, follow_redirects=True)
            response.raise_for_status()
            return PdfReader(BytesIO(response.content)), paper_source_kind, request.paper_source
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperFetchError(f"Failed to fetch PDF content from {request.paper_source}: {exc}") from exc
        finally:
            if close_client:
                client.close()

    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput:
        try:
            if progress is not None:
                progress(JobStage.PAPER_FETCH, 0.1, "Loading PDF source.")
            reader, source_kind, source_ref = self._load_pdf_reader(request)
            if progress is not None:
                progress(JobStage.PAPER_FETCH, 0.55, "Extracting text from PDF pages.")
            text_by_page = [
                (reader.pages[index].extract_text() or "")
                for index in range(min(len(reader.pages), self.settings.pdf_max_pages))
            ]
            metadata_title = getattr(reader.metadata, "title", None)
            paper_document = build_pdf_document(
                text_by_page=text_by_page,
                source_kind=source_kind,
                source_ref=source_ref,
                metadata_title=metadata_title,
            )
        except Exception as exc:
            if isinstance(exc, PaperFetchError):
                raise
            raise PaperFetchError(f"Failed to parse PDF source {request.paper_source}: {exc}") from exc

        if progress is not None:
            progress(JobStage.PAPER_FETCH, 1.0, f"Extracted PDF text for {paper_document.title}.")
        return FetchOutput(
            paper_document=paper_document,
            mode=ProcessorMode.REMOTE_FETCH,
            warnings=[],
        )


@dataclass(frozen=True)
class SourceAwarePaperSourceFetcher:
    arxiv_fetcher: ArxivPaperSourceFetcher
    pdf_fetcher: PdfPaperSourceFetcher

    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput:
        paper_source_kind = detect_paper_source_kind(request.paper_source)
        if paper_source_kind == PaperSourceKind.ARXIV:
            return self.arxiv_fetcher.fetch(request, progress=progress)
        if paper_source_kind in {PaperSourceKind.PDF_URL, PaperSourceKind.PDF_FILE}:
            return self.pdf_fetcher.fetch(request, progress=progress)
        raise PaperFetchError(f"Paper source kind {paper_source_kind} does not support live fetching")


@dataclass(frozen=True)
class ChainedPaperSourceFetcher:
    primary: PaperSourceFetcher
    fallback: FixturePaperSourceFetcher

    def fetch(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> FetchOutput:
        try:
            return self.primary.fetch(request, progress=progress)
        except PaperFetchError as exc:
            if progress is not None:
                progress(JobStage.PAPER_FETCH, 0.8, "Live paper fetch failed; switching to fixture fallback.")
            fallback_output = self.fallback.fetch(request, progress=progress)
            return FetchOutput(
                paper_document=fallback_output.paper_document,
                mode=fallback_output.mode,
                warnings=[
                    "Paper fetch fell back to fixture paper content.",
                    str(exc),
                ],
            )
