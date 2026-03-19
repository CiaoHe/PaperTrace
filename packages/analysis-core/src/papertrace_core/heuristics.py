from __future__ import annotations

from dataclasses import dataclass

from papertrace_core.fixtures import PaperFixture
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
            impl_hints=(
                "Insert trainable rank-decomposition matrices into attention projections.",
            ),
        ),
        ContributionPattern(
            contribution_id="C2",
            title="Frozen backbone fine-tuning",
            section="Section 4",
            keywords=("frozen", "backbone", "trainable"),
            impl_hints=(
                "Keep pretrained weights frozen and optimize only the adapter parameters.",
            ),
        ),
    ),
    "dpo": (
        ContributionPattern(
            contribution_id="C1",
            title="Direct preference optimization objective",
            section="Section 2",
            keywords=("preference", "objective", "trl"),
            impl_hints=(
                "Replace reward-model optimization with a direct preference loss over policy "
                "outputs.",
            ),
        ),
    ),
    "flash-attention": (
        ContributionPattern(
            contribution_id="C1",
            title="IO-aware fused attention kernel",
            section="Section 3",
            keywords=("io-aware", "attention", "kernel"),
            impl_hints=(
                "Fuse tiled attention steps into a memory-efficient exact attention kernel.",
            ),
        ),
    ),
}


def infer_contributions(case_slug: str, paper_fixture: PaperFixture) -> list[PaperContribution]:
    patterns = CASE_PATTERNS.get(case_slug, ())
    haystack = f"{paper_fixture.title}\n{paper_fixture.text}".lower()
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


def infer_mappings(
    contributions: list[PaperContribution],
    diff_clusters: list[DiffCluster],
) -> list[ContributionMapping]:
    mappings: list[ContributionMapping] = []
    used_contribution_ids: set[str] = set()
    for diff_cluster in diff_clusters:
        haystack = " ".join([diff_cluster.label, diff_cluster.summary, *diff_cluster.files]).lower()
        ranked_contributions: list[tuple[int, PaperContribution]] = []
        for contribution in contributions:
            score = sum(1 for keyword in contribution.keywords if keyword.lower() in haystack)
            if score > 0:
                ranked_contributions.append((score, contribution))
        if not ranked_contributions:
            continue
        ranked_contributions.sort(
            key=lambda item: (item[0], item[1].id not in used_contribution_ids, item[1].id),
            reverse=True,
        )
        _, selected_contribution = ranked_contributions[0]
        used_contribution_ids.add(selected_contribution.id)
        confidence = min(0.7 + 0.1 * len(selected_contribution.keywords), 0.96)
        mappings.append(
            ContributionMapping(
                diff_cluster_id=diff_cluster.id,
                contribution_id=selected_contribution.id,
                confidence=round(confidence, 2),
                evidence=(
                    f"Matched contribution keywords {', '.join(selected_contribution.keywords)} "
                    f"against diff cluster '{diff_cluster.label}'."
                ),
                completeness="complete" if confidence >= 0.85 else "partial",
            )
        )
    return mappings
