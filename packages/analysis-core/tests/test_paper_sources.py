from __future__ import annotations

import tarfile
import textwrap
from io import BytesIO
from pathlib import Path

import httpx
from papertrace_core.fixtures import load_paper_fixture
from papertrace_core.inputs import extract_arxiv_id
from papertrace_core.models import AnalysisRequest, PaperSourceKind, ProcessorMode
from papertrace_core.paper_sources import (
    ArxivPaperSourceFetcher,
    ChainedPaperSourceFetcher,
    FixturePaperSourceFetcher,
    PdfPaperSourceFetcher,
    SourceAwarePaperSourceFetcher,
    infer_pdf_sections,
    paper_document_from_fixture,
)
from papertrace_core.settings import Settings


def build_settings() -> Settings:
    return Settings.model_validate(
        {
            "ENABLE_LIVE_PAPER_FETCH": True,
            "ARXIV_API_BASE_URL": "https://export.example.test",
            "ARXIV_TIMEOUT_SECONDS": 5,
            "PDF_FETCH_TIMEOUT_SECONDS": 5,
            "PDF_MAX_PAGES": 4,
        }
    )


def build_tex_archive(files: dict[str, str]) -> bytes:
    archive = BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, text in files.items():
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))
    return archive.getvalue()


def build_pdf_bytes(title: str, body: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            "<< /Length {length} >>\nstream\nBT\n/F1 16 Tf\n36 96 Td\n({text}) Tj\nET\nendstream".format(
                length=len(body.encode("latin-1")) + 31,
                text=body.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"),
            )
        ).encode("latin-1"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("latin-1"))
        document.extend(payload)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    document.extend(
        ("trailer\n<< /Size {size} /Root 1 0 R /Info << /Title ({title}) >> >>\nstartxref\n{xref}\n%%EOF\n")
        .format(
            size=len(objects) + 1,
            title=title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"),
            xref=xref_offset,
        )
        .encode("latin-1")
    )
    return bytes(document)


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
            <author><name>Edward J. Hu</name></author>
            <author><name>Yelong Shen</name></author>
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
    assert output.paper_document.authors == ["Edward J. Hu", "Yelong Shen"]
    assert "low-rank" in output.paper_document.text.lower()


def test_arxiv_paper_source_fetcher_prefers_latex_source_archive() -> None:
    atom_feed = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Fallback LoRA Title</title>
            <author><name>Edward J. Hu</name></author>
            <summary>Fallback abstract from Atom metadata.</summary>
          </entry>
        </feed>
        """
    )
    tex_archive = build_tex_archive(
        {
            "README.md": "# ignored",
            "paper.tex": textwrap.dedent(
                """\
                \\documentclass{article}
                \\title{LoRA from Source Archive}
                \\author{Edward J. Hu and Yelong Shen}
                \\begin{document}
                \\maketitle
                \\begin{abstract}
                We introduce source-backed low-rank adaptation with explicit injected rank modules.
                \\end{abstract}
                \\section{Introduction}
                The source archive exposes the paper body rather than metadata only.
                \\section{Method}
                We inject trainable rank decomposition matrices into transformer layers.
                \\end{document}
                """
            ),
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/query"):
            return httpx.Response(200, text=atom_feed)
        if request.url.path.endswith("/e-print/2106.09685"):
            return httpx.Response(200, content=tex_archive)
        raise AssertionError(f"Unexpected request path: {request.url}")

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
    assert output.warnings == []
    assert output.paper_document.title == "LoRA from Source Archive"
    assert output.paper_document.authors == ["Edward J. Hu", "Yelong Shen"]
    assert output.paper_document.abstract.startswith("We introduce source-backed low-rank adaptation")
    assert [section.heading for section in output.paper_document.sections] == ["Introduction", "Method"]
    assert "rank decomposition matrices" in output.paper_document.text.lower()


def test_arxiv_paper_source_fetcher_falls_back_to_metadata_when_source_fetch_fails() -> None:
    atom_feed = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>FlashAttention Metadata Title</title>
            <summary>IO-aware exact attention from metadata.</summary>
          </entry>
        </feed>
        """
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/query"):
            return httpx.Response(200, text=atom_feed)
        if request.url.path.endswith("/e-print/2205.14135"):
            return httpx.Response(404, text="not found")
        raise AssertionError(f"Unexpected request path: {request.url}")

    fetcher = ArxivPaperSourceFetcher(
        settings=build_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135",
            repo_url="https://github.com/Dao-AILab/flash-attention",
        )
    )

    assert output.mode == ProcessorMode.REMOTE_FETCH
    assert "source fetch failed" in output.warnings[0]
    assert output.paper_document.title == "FlashAttention Metadata Title"
    assert output.paper_document.sections[0].heading == "Abstract"
    assert "metadata" in output.paper_document.text.lower()


def test_arxiv_paper_source_fetcher_accepts_raw_tex_payload() -> None:
    atom_feed = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>DPO Metadata Title</title>
            <summary>Metadata-only summary.</summary>
          </entry>
        </feed>
        """
    )
    raw_tex = textwrap.dedent(
        """\
        \\documentclass{article}
        \\title{Direct Preference Optimization}
        \\begin{document}
        \\begin{abstract}
        We replace reward-model optimization with a direct preference objective.
        \\end{abstract}
        \\section{Approach}
        The objective can be optimized with standard language-model fine-tuning.
        \\end{document}
        """
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/query"):
            return httpx.Response(200, text=atom_feed)
        if request.url.path.endswith("/e-print/2305.18290"):
            return httpx.Response(200, content=raw_tex)
        raise AssertionError(f"Unexpected request path: {request.url}")

    fetcher = ArxivPaperSourceFetcher(
        settings=build_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2305.18290",
            repo_url="https://github.com/huggingface/trl",
        )
    )

    assert output.mode == ProcessorMode.REMOTE_FETCH
    assert output.paper_document.title == "Direct Preference Optimization"
    assert output.paper_document.sections[0].heading == "Approach"
    assert "direct preference objective" in output.paper_document.abstract.lower()


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


def test_pdf_paper_source_fetcher_reads_pdf_url() -> None:
    pdf_bytes = build_pdf_bytes(
        title="Flash Attention PDF",
        body="Flash Attention PDF Abstract with fused attention kernel implementation.",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf_bytes, headers={"Content-Type": "application/pdf"})

    fetcher = PdfPaperSourceFetcher(
        settings=build_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://example.test/flash-attention.pdf",
            repo_url="https://github.com/Dao-AILab/flash-attention",
        )
    )

    assert output.mode == ProcessorMode.REMOTE_FETCH
    assert output.paper_document.source_kind == PaperSourceKind.PDF_URL
    assert "Flash Attention PDF".lower() in output.paper_document.title.lower()
    assert "fused attention kernel" in output.paper_document.text.lower()


def test_pdf_paper_source_fetcher_reads_local_pdf_file(tmp_path: Path) -> None:
    pdf_path = tmp_path / "lora-paper.pdf"
    pdf_path.write_bytes(
        build_pdf_bytes(
            title="LoRA PDF",
            body="Abstract LoRA low-rank adaptation modules keep the backbone frozen during training.",
        )
    )

    fetcher = PdfPaperSourceFetcher(settings=build_settings())
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source=str(pdf_path),
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.mode == ProcessorMode.REMOTE_FETCH
    assert output.paper_document.source_kind == PaperSourceKind.PDF_FILE
    assert "low-rank adaptation" in output.paper_document.text.lower()


def test_source_aware_paper_source_fetcher_routes_pdf_sources() -> None:
    pdf_bytes = build_pdf_bytes(
        title="DPO PDF",
        body="Abstract Direct preference optimization objective replaces the reward model.",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf_bytes, headers={"Content-Type": "application/pdf"})

    fetcher = SourceAwarePaperSourceFetcher(
        arxiv_fetcher=ArxivPaperSourceFetcher(
            settings=build_settings(),
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
        ),
        pdf_fetcher=PdfPaperSourceFetcher(
            settings=build_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
    )
    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://example.test/dpo-paper.pdf",
            repo_url="https://github.com/huggingface/trl",
        )
    )

    assert output.paper_document.source_kind == PaperSourceKind.PDF_URL
    assert "direct preference optimization" in output.paper_document.text.lower()


def test_infer_pdf_sections_recovers_named_sections() -> None:
    sections = infer_pdf_sections(
        textwrap.dedent(
            """\
            Abstract
            We introduce a robust paper parser.
            1 Introduction
            The parser supports section-aware extraction.
            2 Contributions
            We present structured contribution normalization.
            3 Method
            We rank candidate snippets with section priors.
            """
        )
    )

    assert [section.heading for section in sections] == ["Abstract", "1 Introduction", "2 Contributions", "3 Method"]
    assert "section-aware extraction" in sections[1].text.lower()
