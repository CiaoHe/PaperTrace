from __future__ import annotations

import re
from dataclasses import dataclass

from papertrace_core.models import ContributionMapping, DiffCluster, PaperContribution


@dataclass(frozen=True)
class ContributionPattern:
    contribution_id: str
    title: str
    section: str
    keywords: tuple[str, ...]
    impl_hints: tuple[str, ...]


CASE_PATTERNS: dict[str, tuple[ContributionPattern, ...]] = {
    "lora": (
        ContributionPattern(
            contribution_id="C1",
            title="Low-rank adaptation modules",
            section="Section 3",
            keywords=("low-rank", "adapter", "transformers"),
            impl_hints=("Insert trainable rank-decomposition matrices into attention projections.",),
        ),
        ContributionPattern(
            contribution_id="C2",
            title="Frozen backbone fine-tuning",
            section="Section 4",
            keywords=("frozen", "backbone", "trainable"),
            impl_hints=("Keep pretrained weights frozen and optimize only the adapter parameters.",),
        ),
    ),
    "dpo": (
        ContributionPattern(
            contribution_id="C1",
            title="Direct preference optimization objective",
            section="Section 2",
            keywords=("preference", "objective", "trl"),
            impl_hints=("Replace reward-model optimization with a direct preference loss over policy outputs.",),
        ),
    ),
    "flash-attention": (
        ContributionPattern(
            contribution_id="C1",
            title="IO-aware fused attention kernel",
            section="Section 3",
            keywords=("io-aware", "attention", "kernel"),
            impl_hints=("Fuse tiled attention steps into a memory-efficient exact attention kernel.",),
        ),
    ),
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
STOPWORDS = {
    "with",
    "from",
    "into",
    "using",
    "section",
    "modules",
    "module",
    "changes",
    "change",
    "implementation",
    "implement",
}


def infer_contributions(case_slug: str, title: str, text: str) -> list[PaperContribution]:
    patterns = CASE_PATTERNS.get(case_slug, ())
    haystack = f"{title}\n{text}".lower()
    contributions: list[PaperContribution] = []
    for pattern in patterns:
        matched_keywords = [keyword for keyword in pattern.keywords if keyword in haystack]
        if not matched_keywords:
            continue
        contributions.append(
            PaperContribution(
                id=pattern.contribution_id,
                title=pattern.title,
                section=pattern.section,
                keywords=matched_keywords,
                impl_hints=list(pattern.impl_hints),
            )
        )
    return contributions


def tokenize(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS and not token.isdigit()}


def collect_unmatched_ids(
    contributions: list[PaperContribution],
    diff_clusters: list[DiffCluster],
    mappings: list[ContributionMapping],
) -> tuple[list[str], list[str]]:
    matched_contribution_ids = {mapping.contribution_id for mapping in mappings}
    matched_diff_cluster_ids = {mapping.diff_cluster_id for mapping in mappings}
    unmatched_contribution_ids = [
        contribution.id for contribution in contributions if contribution.id not in matched_contribution_ids
    ]
    unmatched_diff_cluster_ids = [
        diff_cluster.id for diff_cluster in diff_clusters if diff_cluster.id not in matched_diff_cluster_ids
    ]
    return unmatched_contribution_ids, unmatched_diff_cluster_ids


def rank_contribution_match(
    contribution: PaperContribution,
    diff_cluster: DiffCluster,
) -> tuple[int, str]:
    haystack = " ".join([diff_cluster.label, diff_cluster.summary, *diff_cluster.files]).lower()
    keyword_hits = [keyword for keyword in contribution.keywords if keyword.lower() in haystack]
    title_hits = sorted(token for token in tokenize(contribution.title) if token in haystack)
    hint_hits = sorted({token for hint in contribution.impl_hints for token in tokenize(hint) if token in haystack})

    score = len(keyword_hits) * 5 + len(title_hits) * 2 + min(len(hint_hits), 3)
    if score == 0:
        return 0, ""

    evidence_parts: list[str] = []
    if keyword_hits:
        evidence_parts.append(f"keyword hits: {', '.join(keyword_hits[:3])}")
    if title_hits:
        evidence_parts.append(f"title overlap: {', '.join(title_hits[:3])}")
    if hint_hits:
        evidence_parts.append(f"impl hints: {', '.join(hint_hits[:3])}")
    evidence_parts.append(f"cluster files: {', '.join(diff_cluster.files[:2])}")
    evidence_parts.append(f"cluster type: {diff_cluster.change_type}")
    return score, "; ".join(evidence_parts)


def infer_mappings(
    contributions: list[PaperContribution],
    diff_clusters: list[DiffCluster],
) -> list[ContributionMapping]:
    mappings: list[ContributionMapping] = []
    used_contribution_ids: set[str] = set()
    for diff_cluster in diff_clusters:
        ranked_contributions: list[tuple[int, PaperContribution]] = []
        evidence_by_contribution_id: dict[str, str] = {}
        for contribution in contributions:
            score, evidence = rank_contribution_match(contribution, diff_cluster)
            if score > 0:
                ranked_contributions.append((score, contribution))
                evidence_by_contribution_id[contribution.id] = evidence
        if not ranked_contributions:
            continue
        ranked_contributions.sort(
            key=lambda item: (item[0], item[1].id not in used_contribution_ids, item[1].id),
            reverse=True,
        )
        score, selected_contribution = ranked_contributions[0]
        used_contribution_ids.add(selected_contribution.id)
        confidence = min(0.62 + 0.04 * score, 0.96)
        mappings.append(
            ContributionMapping(
                diff_cluster_id=diff_cluster.id,
                contribution_id=selected_contribution.id,
                confidence=round(confidence, 2),
                evidence=(
                    f"Matched contribution '{selected_contribution.title}' to "
                    f"diff cluster '{diff_cluster.label}' via "
                    f"{evidence_by_contribution_id[selected_contribution.id]}."
                ),
                completeness="complete" if confidence >= 0.85 else "partial",
            )
        )
    return mappings
