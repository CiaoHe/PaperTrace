from __future__ import annotations

from papertrace_core.llm import _extract_json_block


def test_extract_json_block_supports_fenced_json() -> None:
    payload = _extract_json_block('```json\n[{"id": "C1"}]\n```')

    assert payload == [{"id": "C1"}]


def test_extract_json_block_supports_bare_json_array() -> None:
    payload = _extract_json_block('[{"id": "C1"}]')

    assert payload == [{"id": "C1"}]
