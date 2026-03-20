from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from papertrace_core.heuristics import merge_contribution_sets
from papertrace_core.inputs import normalize_repo_url
from papertrace_core.models import (
    BaseRepoCandidate,
    ContributionMapping,
    DiffCluster,
    PaperContribution,
    PaperDocument,
)
from papertrace_core.settings import Settings

JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
SECTION_SPLIT_RE = re.compile(r"\n\s*\n+")
SECTION_KIND_PRIORITY: dict[str, int] = {
    "contributions": 7,
    "our contributions": 7,
    "main contributions": 7,
    "abstract": 6,
    "method": 5,
    "methods": 5,
    "approach": 5,
    "architecture": 4,
    "implementation details": 4,
    "experiments": 3,
    "evaluation": 3,
    "results": 3,
    "appendix": 2,
}


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


def _normalize_heading(heading: str) -> str:
    compact = re.sub(r"^\d+(?:\.\d+)*\s*", "", heading.strip().lower())
    return re.sub(r"\s+", " ", compact)


def _section_priority(heading: str) -> int:
    normalized = _normalize_heading(heading)
    for marker, priority in SECTION_KIND_PRIORITY.items():
        if marker in normalized:
            return priority
    return 1


def _trim_text(value: str, limit: int) -> str:
    compact = value.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 3, 0)].rstrip()}..."


def _tokenize(text: str) -> list[str]:
    return list(dict.fromkeys(TOKEN_RE.findall(text.lower())))


def _build_llm_parse_sections(
    paper_document: PaperDocument,
    *,
    max_sections: int,
    section_char_limit: int,
    total_char_limit: int,
) -> list[tuple[str, str]]:
    ranked_sections: list[tuple[int, str, str]] = []
    if paper_document.abstract.strip():
        ranked_sections.append((SECTION_KIND_PRIORITY["abstract"], "Abstract", paper_document.abstract.strip()))
    for section in paper_document.sections:
        text = section.text.strip()
        if not text:
            continue
        ranked_sections.append((_section_priority(section.heading), section.heading.strip() or "Untitled", text))

    if not ranked_sections and paper_document.text.strip():
        paragraphs = [part.strip() for part in SECTION_SPLIT_RE.split(paper_document.text) if part.strip()]
        for index, paragraph in enumerate(paragraphs[:max_sections], start=1):
            ranked_sections.append((1, f"Body chunk {index}", paragraph))

    selected_sections: list[tuple[str, str]] = []
    consumed_chars = 0
    for _, heading, text in sorted(
        ranked_sections,
        key=lambda item: (item[0], len(item[2])),
        reverse=True,
    ):
        if len(selected_sections) >= max_sections or consumed_chars >= total_char_limit:
            break
        remaining_budget = total_char_limit - consumed_chars
        if remaining_budget < 200:
            break
        trimmed_text = _trim_text(text, min(section_char_limit, remaining_budget))
        if not trimmed_text:
            continue
        selected_sections.append((heading, trimmed_text))
        consumed_chars += len(trimmed_text)

    if selected_sections:
        return selected_sections

    fallback_text = _trim_text(paper_document.text, total_char_limit)
    return [("Body", fallback_text)] if fallback_text else []


def _build_llm_parse_batches(
    paper_document: PaperDocument,
    *,
    max_sections: int,
    section_char_limit: int,
    total_char_limit: int,
    max_batches: int,
) -> list[list[tuple[str, str]]]:
    if max_batches <= 1:
        sections = _build_llm_parse_sections(
            paper_document,
            max_sections=max_sections,
            section_char_limit=section_char_limit,
            total_char_limit=total_char_limit,
        )
        return [sections] if sections else []

    ranked_sections: list[tuple[int, str, str]] = []
    if paper_document.abstract.strip():
        ranked_sections.append((SECTION_KIND_PRIORITY["abstract"], "Abstract", paper_document.abstract.strip()))
    for section in paper_document.sections:
        text = section.text.strip()
        if not text:
            continue
        ranked_sections.append((_section_priority(section.heading), section.heading.strip() or "Untitled", text))

    if not ranked_sections and paper_document.text.strip():
        paragraphs = [part.strip() for part in SECTION_SPLIT_RE.split(paper_document.text) if part.strip()]
        for index, paragraph in enumerate(paragraphs, start=1):
            ranked_sections.append((1, f"Body chunk {index}", paragraph))

    sorted_sections = sorted(
        ranked_sections,
        key=lambda item: (item[0], len(item[2])),
        reverse=True,
    )
    batches: list[list[tuple[str, str]]] = []
    current_batch: list[tuple[str, str]] = []
    consumed_chars = 0

    for _, heading, text in sorted_sections:
        trimmed_text = _trim_text(text, min(section_char_limit, total_char_limit))
        if not trimmed_text:
            continue
        would_overflow = current_batch and (
            len(current_batch) >= max_sections or consumed_chars + len(trimmed_text) > total_char_limit
        )
        if would_overflow:
            batches.append(current_batch)
            if len(batches) >= max_batches:
                return batches
            current_batch = []
            consumed_chars = 0
        current_batch.append((heading, trimmed_text))
        consumed_chars += len(trimmed_text)

    if current_batch and len(batches) < max_batches:
        batches.append(current_batch)

    return batches


def _normalize_contribution_item(item: dict[str, Any], index: int) -> PaperContribution:
    title = str(item.get("title") or "").strip()
    if not title:
        raise ValueError("LLM contribution item missing title")
    section = str(item.get("section") or "Body").strip() or "Body"

    raw_keywords = item.get("keywords")
    keywords = (
        [str(keyword).strip() for keyword in raw_keywords if str(keyword).strip()]
        if isinstance(raw_keywords, list)
        else _tokenize(title)[:4]
    )
    if not keywords:
        keywords = _tokenize(title)[:4]

    raw_impl_hints = item.get("impl_hints")
    impl_hints = (
        [str(hint).strip() for hint in raw_impl_hints if str(hint).strip()] if isinstance(raw_impl_hints, list) else []
    )
    if not impl_hints and isinstance(item.get("problem_solved"), str) and item["problem_solved"].strip():
        impl_hints = [item["problem_solved"].strip()]
    if not impl_hints:
        impl_hints = [title]

    raw_refs = item.get("evidence_refs")
    evidence_refs = [str(ref).strip() for ref in raw_refs if str(ref).strip()] if isinstance(raw_refs, list) else []

    raw_complexity = item.get("implementation_complexity")
    complexity = int(raw_complexity) if isinstance(raw_complexity, int | float) else None
    if complexity is not None:
        complexity = max(1, min(5, complexity))

    contribution_id = str(item.get("id") or f"L{index}").strip() or f"L{index}"
    return PaperContribution(
        id=contribution_id,
        title=title,
        section=section,
        keywords=list(dict.fromkeys(keywords))[:6],
        impl_hints=list(dict.fromkeys(impl_hints))[:5],
        problem_solved=(
            str(item.get("problem_solved")).strip()
            if isinstance(item.get("problem_solved"), str) and str(item.get("problem_solved")).strip()
            else None
        ),
        baseline_difference=(
            str(item.get("baseline_difference")).strip()
            if isinstance(item.get("baseline_difference"), str) and str(item.get("baseline_difference")).strip()
            else None
        ),
        evidence_refs=list(dict.fromkeys(evidence_refs))[:6],
        implementation_complexity=complexity,
    )


def _normalize_contribution_payload(payload: Any) -> list[PaperContribution]:
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON array for contribution extraction")
    return [
        _normalize_contribution_item(item, index)
        for index, item in enumerate(payload, start=1)
        if isinstance(item, dict)
    ]


def _normalize_base_repo_payload(payload: Any) -> list[BaseRepoCandidate]:
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON array for base repo suggestion")

    candidates: list[BaseRepoCandidate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_repo_url = str(item.get("repo_url") or "").strip()
        if not raw_repo_url:
            continue
        try:
            repo_url = normalize_repo_url(raw_repo_url)
        except ValueError:
            continue

        raw_confidence = item.get("confidence")
        confidence = float(raw_confidence) if isinstance(raw_confidence, int | float) else 0.45
        evidence = str(item.get("evidence") or "LLM inferred an ancestry relationship from available context.").strip()
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="llm_reasoning",
                confidence=max(0.0, min(0.99, confidence)),
                evidence=evidence,
            )
        )
    return candidates


@dataclass(frozen=True)
class LLMClient:
    client: OpenAI
    model: str
    paper_parse_max_sections: int = 8
    paper_parse_section_chars: int = 3500
    paper_parse_total_chars: int = 14000
    paper_parse_max_batches: int = 3

    def _extract_contribution_batch(
        self,
        paper_document: PaperDocument,
        *,
        sections: list[tuple[str, str]],
        batch_index: int,
        batch_count: int,
    ) -> list[PaperContribution]:
        batch_label = f"Section batch {batch_index} of {batch_count}.\n" if batch_count > 1 else ""
        sections_payload = "\n\n".join(f"## Section: {heading}\n{text}" for heading, text in sections)
        prompt = (
            "Extract the concrete technical contributions from the paper context below as JSON only.\n"
            "Focus on novel methods, objectives, kernels, architectures, or training procedures.\n"
            "Avoid background claims, evaluation-only statements, and vague motivation.\n"
            "Return a JSON array. Each item must contain:\n"
            "- id\n"
            "- title\n"
            "- section\n"
            "- keywords\n"
            "- impl_hints\n"
            "- problem_solved\n"
            "- baseline_difference\n"
            "- evidence_refs\n"
            "- implementation_complexity\n\n"
            f"Title: {paper_document.title}\n"
            f"{batch_label}"
            f"Structured context:\n{sections_payload}\n"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured ML paper contributions. "
                        "Prefer implementation-relevant contributions and reply with JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "[]"
        payload = _extract_json_block(content)
        return _normalize_contribution_payload(payload)

    def extract_contributions(self, paper_document: PaperDocument) -> list[PaperContribution]:
        batches = _build_llm_parse_batches(
            paper_document,
            max_sections=self.paper_parse_max_sections,
            section_char_limit=self.paper_parse_section_chars,
            total_char_limit=self.paper_parse_total_chars,
            max_batches=self.paper_parse_max_batches,
        )
        merged_contributions: list[PaperContribution] = []
        for index, sections in enumerate(batches, start=1):
            batch_contributions = self._extract_contribution_batch(
                paper_document,
                sections=sections,
                batch_index=index,
                batch_count=len(batches),
            )
            if not batch_contributions:
                continue
            merged_contributions = (
                merge_contribution_sets(merged_contributions, batch_contributions)
                if merged_contributions
                else batch_contributions
            )
        return merged_contributions

    def suggest_base_repos(
        self,
        *,
        request_repo_url: str,
        paper_document: PaperDocument,
        readme_text: str,
        notes: str,
        existing_candidates: list[BaseRepoCandidate],
    ) -> list[BaseRepoCandidate]:
        candidate_context = json.dumps(
            [candidate.model_dump(mode="json") for candidate in existing_candidates[:8]],
            ensure_ascii=True,
        )
        prompt = (
            "Infer likely upstream or base repositories for the target GitHub repository.\n"
            "Return only a JSON array. Each item must contain: repo_url, confidence, evidence.\n"
            "Only return GitHub repository URLs that are plausible code ancestry candidates.\n"
            "Prefer upstream frameworks or parent repos rather than unrelated dependencies.\n\n"
            f"Target repo: {request_repo_url}\n"
            f"Paper title: {paper_document.title}\n"
            f"Paper abstract: {_trim_text(paper_document.abstract or paper_document.text, 2500)}\n"
            f"Repo README excerpt: {_trim_text(readme_text, 2500)}\n"
            f"Repo notes: {_trim_text(notes, 1200)}\n"
            f"Existing heuristic candidates: {candidate_context}\n"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You infer repository ancestry for ML codebases. "
                        "Return JSON only and keep evidence concise and concrete."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "[]"
        payload = _extract_json_block(content)
        return _normalize_base_repo_payload(payload)

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
    return LLMClient(
        client=client,
        model=settings.llm_model,
        paper_parse_max_sections=settings.llm_paper_parse_max_sections,
        paper_parse_section_chars=settings.llm_paper_parse_section_chars,
        paper_parse_total_chars=settings.llm_paper_parse_total_chars,
        paper_parse_max_batches=settings.llm_paper_parse_max_batches,
    )
