from __future__ import annotations

from dataclasses import dataclass

from papertrace_core.llm import (
    LLMClient,
    _build_llm_parse_batches,
    _build_llm_parse_sections,
    _extract_json_block,
    _normalize_contribution_payload,
)
from papertrace_core.models import PaperDocument, PaperSection, PaperSourceKind


def test_extract_json_block_supports_fenced_json() -> None:
    payload = _extract_json_block('```json\n[{"id": "C1"}]\n```')

    assert payload == [{"id": "C1"}]


def test_extract_json_block_supports_bare_json_array() -> None:
    payload = _extract_json_block('[{"id": "C1"}]')

    assert payload == [{"id": "C1"}]


def test_build_llm_parse_sections_prioritizes_contribution_and_method_sections() -> None:
    sections = _build_llm_parse_sections(
        PaperDocument(
            source_kind=PaperSourceKind.PDF_FILE,
            source_ref="/tmp/test.pdf",
            title="Structured Routing",
            abstract="We introduce a sparse routing encoder for retrieval.",
            sections=[
                PaperSection(heading="5 Appendix", text="Supplementary ablations and hyperparameters."),
                PaperSection(heading="2 Method", text="We present the routing encoder and sparse update rule."),
                PaperSection(heading="1 Our Contributions", text="We introduce sparse routing and a new objective."),
            ],
            text="Body",
        ),
        max_sections=3,
        section_char_limit=200,
        total_char_limit=500,
    )

    assert [heading for heading, _ in sections] == ["1 Our Contributions", "Abstract", "2 Method"]


def test_build_llm_parse_batches_spreads_long_papers_across_multiple_requests() -> None:
    batches = _build_llm_parse_batches(
        PaperDocument(
            source_kind=PaperSourceKind.PDF_FILE,
            source_ref="/tmp/test.pdf",
            title="Structured Routing",
            abstract="We introduce a sparse routing encoder for retrieval.",
            sections=[
                PaperSection(heading="1 Our Contributions", text="Contribution lane." * 20),
                PaperSection(heading="2 Method", text="Method lane." * 20),
                PaperSection(heading="3 Experiments", text="Experiment lane." * 20),
                PaperSection(heading="4 Appendix", text="Appendix lane." * 20),
            ],
            text="Body",
        ),
        max_sections=2,
        section_char_limit=120,
        total_char_limit=180,
        max_batches=3,
    )

    assert len(batches) == 3
    assert [heading for heading, _ in batches[0]] == ["1 Our Contributions", "Abstract"]
    assert [heading for heading, _ in batches[1]] == ["2 Method"]
    assert [heading for heading, _ in batches[2]] == ["3 Experiments"]


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]


class FakeCompletionAPI:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def create(self, *, model: str, messages: list[dict[str, str]]) -> FakeResponse:
        assert model == "test-model"
        self.prompts.append(messages[-1]["content"])
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=self.responses.pop(0)))])


class FakeOpenAIClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = type("FakeChat", (), {"completions": FakeCompletionAPI(responses)})()


def test_llm_client_extract_contributions_merges_multi_batch_results() -> None:
    client = FakeOpenAIClient(
        responses=[
            """
            [
              {
                "id": "L1",
                "title": "Sparse routing encoder",
                "section": "Method",
                "keywords": ["routing", "encoder"],
                "impl_hints": ["Introduce routing slots"],
                "evidence_refs": ["Algorithm 1"]
              }
            ]
            """,
            """
            [
              {
                "id": "L2",
                "title": "Sparse routing encoder",
                "section": "Experiments",
                "keywords": ["routing", "encoder"],
                "impl_hints": ["Cache slot reuse"],
                "evidence_refs": ["Table 2"]
              },
              {
                "id": "L3",
                "title": "Distillation objective",
                "section": "Experiments",
                "keywords": ["distillation", "objective"],
                "impl_hints": ["Optimize preference pairs"]
              }
            ]
            """,
        ]
    )
    llm_client = LLMClient(
        client=client,  # type: ignore[arg-type]
        model="test-model",
        paper_parse_max_sections=2,
        paper_parse_section_chars=180,
        paper_parse_total_chars=220,
        paper_parse_max_batches=2,
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.PDF_FILE,
        source_ref="/tmp/test.pdf",
        title="Structured Routing",
        abstract="We introduce a sparse routing encoder for retrieval.",
        sections=[
            PaperSection(heading="1 Our Contributions", text="Routing contribution." * 25),
            PaperSection(heading="2 Method", text="Routing method." * 25),
            PaperSection(heading="3 Experiments", text="Experiment evidence." * 25),
        ],
        text="Body",
    )

    contributions = llm_client.extract_contributions(paper_document)

    assert len(client.chat.completions.prompts) == 2
    assert "Section batch 1 of 2" in client.chat.completions.prompts[0]
    assert "Section batch 2 of 2" in client.chat.completions.prompts[1]
    assert len(contributions) == 2
    merged = next(contribution for contribution in contributions if contribution.title == "Sparse routing encoder")
    assert "Introduce routing slots" in merged.impl_hints
    assert "Cache slot reuse" in merged.impl_hints
    assert "Table 2" in merged.evidence_refs


def test_normalize_contribution_payload_fills_missing_optional_fields() -> None:
    contributions = _normalize_contribution_payload(
        [{"title": "Sparse routing encoder", "keywords": ["routing", "encoder"], "impl_hints": []}]
    )

    assert len(contributions) == 1
    assert contributions[0].id == "L1"
    assert contributions[0].section == "Body"
    assert contributions[0].impl_hints == ["Sparse routing encoder"]
