from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from papertrace_core.models import (
    ContributionMapping,
    DiffCluster,
    PaperContribution,
    PaperDocument,
)
from papertrace_core.settings import Settings

JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_json_block(content: str) -> Any:
    match = JSON_BLOCK_RE.search(content)
    if match:
        return json.loads(match.group(1).strip())
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end >= start:
        return json.loads(content[start : end + 1])
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return json.loads(content[start : end + 1])
    raise ValueError("No JSON payload found in LLM response")


@dataclass(frozen=True)
class LLMClient:
    client: OpenAI
    model: str

    def extract_contributions(self, paper_document: PaperDocument) -> list[PaperContribution]:
        prompt = (
            "Extract the technical contributions from the following paper excerpt as JSON. "
            "Return only a JSON array. Each item must contain: "
            "id, title, section, keywords, impl_hints.\n\n"
            f"Title: {paper_document.title}\n"
            f"Text: {paper_document.text}\n"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": ("You extract structured ML paper contributions and reply with JSON only."),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "[]"
        payload = _extract_json_block(content)
        if not isinstance(payload, list):
            raise ValueError("Expected a JSON array for contribution extraction")
        return [PaperContribution.model_validate(item) for item in payload]

    def map_contributions(
        self,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> list[ContributionMapping]:
        contributions_payload = json.dumps(
            [item.model_dump(mode="json") for item in contributions],
            ensure_ascii=True,
        )
        diff_clusters_payload = json.dumps(
            [item.model_dump(mode="json") for item in diff_clusters],
            ensure_ascii=True,
        )
        prompt = (
            "Map each diff cluster to the most relevant paper contribution as JSON. "
            "Return only a JSON array. Each item must contain: "
            "diff_cluster_id, contribution_id, confidence, evidence, completeness.\n\n"
            f"Contributions: {contributions_payload}\n"
            f"Diff clusters: {diff_clusters_payload}\n"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": ("You align ML paper contributions with code changes and reply with JSON only."),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "[]"
        payload = _extract_json_block(content)
        if not isinstance(payload, list):
            raise ValueError("Expected a JSON array for contribution mapping")
        return [ContributionMapping.model_validate(item) for item in payload]


def build_llm_client(settings: Settings) -> LLMClient | None:
    if not settings.llm_base_url or not settings.llm_model:
        return None
    client = OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "EMPTY",
        timeout=settings.llm_timeout_seconds,
    )
    return LLMClient(client=client, model=settings.llm_model)
