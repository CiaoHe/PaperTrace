from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import PaperFixture, load_paper_fixture
from papertrace_core.inputs import extract_arxiv_id
from papertrace_core.interfaces import FetchOutput
from papertrace_core.models import (
    AnalysisRequest,
    PaperDocument,
    PaperSection,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.settings import Settings

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


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


@dataclass(frozen=True)
class FixturePaperSourceFetcher:
    def fetch(self, request: AnalysisRequest) -> FetchOutput:
        fixture = load_paper_fixture(detect_case_slug(request))
        return FetchOutput(
            paper_document=paper_document_from_fixture(request, fixture),
            mode=ProcessorMode.FIXTURE,
            warnings=[],
        )


@dataclass(frozen=True)
class ArxivPaperSourceFetcher:
    settings: Settings
    client: httpx.Client | None = None

    def fetch(self, request: AnalysisRequest) -> FetchOutput:
        arxiv_id = extract_arxiv_id(request.paper_source)
        if arxiv_id is None:
            raise PaperFetchError("Paper source does not contain a valid arXiv identifier")

        close_client = self.client is None
        client = self.client or httpx.Client(timeout=self.settings.arxiv_timeout_seconds)
        endpoint = f"{self.settings.arxiv_api_base_url.rstrip('/')}/api/query"

        try:
            response = client.get(endpoint, params={"id_list": arxiv_id})
            response.raise_for_status()
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
class ChainedPaperSourceFetcher:
    primary: ArxivPaperSourceFetcher
    fallback: FixturePaperSourceFetcher

    def fetch(self, request: AnalysisRequest) -> FetchOutput:
        try:
            return self.primary.fetch(request)
        except PaperFetchError as exc:
            fallback_output = self.fallback.fetch(request)
            return FetchOutput(
                paper_document=fallback_output.paper_document,
                mode=fallback_output.mode,
                warnings=[
                    "Paper fetch fell back to fixture paper content.",
                    str(exc),
                ],
            )
