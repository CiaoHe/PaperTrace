from __future__ import annotations

from papertrace_core.llm import (
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


def test_normalize_contribution_payload_fills_missing_optional_fields() -> None:
    contributions = _normalize_contribution_payload(
        [{"title": "Sparse routing encoder", "keywords": ["routing", "encoder"], "impl_hints": []}]
    )

    assert len(contributions) == 1
    assert contributions[0].id == "L1"
    assert contributions[0].section == "Body"
    assert contributions[0].impl_hints == ["Sparse routing encoder"]
