from __future__ import annotations

import textwrap

import httpx
from papertrace_core.fixtures import load_paper_fixture
from papertrace_core.inputs import extract_arxiv_id
from papertrace_core.models import AnalysisRequest, PaperSourceKind, ProcessorMode
from papertrace_core.paper_sources import (
    ArxivPaperSourceFetcher,
    ChainedPaperSourceFetcher,
    FixturePaperSourceFetcher,
    paper_document_from_fixture,
)
from papertrace_core.settings import Settings


def build_settings() -> Settings:
    return Settings.model_validate(
        {
            "ENABLE_LIVE_PAPER_FETCH": True,
            "ARXIV_API_BASE_URL": "https://export.example.test",
            "ARXIV_TIMEOUT_SECONDS": 5,
        }
    )


def test_extract_arxiv_id_returns_identifier() -> None:
    assert extract_arxiv_id("https://arxiv.org/abs/2106.09685 LoRA") == "2106.09685"


def test_paper_document_from_fixture_preserves_fixture_text() -> None:
    request = AnalysisRequest(
        paper_source="fixture:LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    document = paper_document_from_fixture(request, load_paper_fixture("lora"))

    assert document.source_kind == PaperSourceKind.TEXT_REFERENCE
    assert document.title
    assert "low-rank" in document.text.lower()


def test_arxiv_paper_source_fetcher_parses_atom_feed() -> None:
    atom_feed = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>LoRA: Low-Rank Adaptation of Large Language Models</title>
            <summary>We reduce adaptation cost with low-rank updates.</summary>
          </entry>
        </feed>
        """
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=atom_feed)

    fetcher = ArxivPaperSourceFetcher(
        settings=build_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.mode == ProcessorMode.REMOTE_FETCH
    assert output.paper_document.source_kind == PaperSourceKind.ARXIV
    assert output.paper_document.title.startswith("LoRA")
    assert "low-rank" in output.paper_document.text.lower()


def test_chained_paper_source_fetcher_falls_back_to_fixture() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="error")

    fetcher = ChainedPaperSourceFetcher(
        primary=ArxivPaperSourceFetcher(
            settings=build_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        fallback=FixturePaperSourceFetcher(),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.mode == ProcessorMode.FIXTURE
    assert output.paper_document.title
    assert output.warnings[0] == "Paper fetch fell back to fixture paper content."
