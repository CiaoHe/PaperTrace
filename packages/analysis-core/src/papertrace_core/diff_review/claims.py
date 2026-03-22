from __future__ import annotations

import re
import unicodedata

from syntok import segmenter

from papertrace_core.diff_review.common import normalize_claim_text, normalize_identifier_text, stable_digest
from papertrace_core.diff_review.models import ReviewClaimIndexEntry, ReviewContributionStatus
from papertrace_core.models import PaperContribution

PROTECTED_ABBREVIATIONS = {
    "Eq.": "Eq<prd>",
    "Fig.": "Fig<prd>",
    "Sec.": "Sec<prd>",
    "Tab.": "Tab<prd>",
    "e.g.": "eg<prd>",
    "i.e.": "ie<prd>",
    "et al.": "et_al<prd>",
}
LIST_ITEM_RE = re.compile(r"(?:^|\n)\s*(?:[-*]|(?:\d+\.))\s+")


def contribution_key_for(contribution: PaperContribution) -> str:
    first_hint = contribution.impl_hints[0] if contribution.impl_hints else ""
    return stable_digest(
        {
            "title": normalize_identifier_text(contribution.title),
            "section": normalize_identifier_text(contribution.section),
            "first_impl_hint": normalize_identifier_text(first_hint),
        },
        length=16,
    )


def split_contribution_claims(
    contribution: PaperContribution,
    *,
    status: ReviewContributionStatus,
) -> list[ReviewClaimIndexEntry]:
    groups = [
        contribution.title,
        contribution.problem_solved or "",
        contribution.baseline_difference or "",
        *contribution.impl_hints,
    ]
    contribution_key = contribution_key_for(contribution)
    claim_texts: list[str] = []
    for group in groups:
        if not group.strip():
            continue
        claim_texts.extend(_split_claim_group(group))
    deduped: list[str] = []
    seen: set[str] = set()
    for claim_text in claim_texts:
        normalized = normalize_claim_text(claim_text)
        if len(normalized) < 12 or normalized in seen:
            continue
        if normalized.startswith("[") and normalized.endswith("]"):
            continue
        seen.add(normalized)
        deduped.append(claim_text.strip())

    claims: list[ReviewClaimIndexEntry] = []
    for index, claim_text in enumerate(deduped, start=1):
        normalized_claim = normalize_claim_text(claim_text)
        claims.append(
            ReviewClaimIndexEntry(
                claim_id=stable_digest(
                    {"contribution_key": contribution_key, "claim_text": normalized_claim},
                    length=20,
                ),
                claim_label=f"{contribution.id}.S{index}",
                contribution_key=contribution_key,
                contribution_id=contribution.id,
                section=contribution.section,
                claim_text=claim_text.strip(),
                status=status,
            )
        )
    return claims


def _split_claim_group(value: str) -> list[str]:
    protected = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    for original, replacement in PROTECTED_ABBREVIATIONS.items():
        protected = protected.replace(original, replacement)
    chunks = [chunk.strip() for chunk in LIST_ITEM_RE.split(protected) if chunk.strip()]
    sentences: list[str] = []
    for chunk in chunks:
        for paragraph in segmenter.process(chunk):
            for sentence in paragraph:
                sentence_text = "".join(f"{token.spacing}{token.value}" for token in sentence).strip()
                if not sentence_text:
                    continue
                restored = sentence_text
                for original, replacement in PROTECTED_ABBREVIATIONS.items():
                    restored = restored.replace(replacement, original)
                sentences.append(restored)
    return sentences
