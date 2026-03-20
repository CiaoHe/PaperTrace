from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from papertrace_core.models import (
    ContributionMapping,
    CoverageType,
    DiffCluster,
    PaperContribution,
    PaperDocument,
    PaperSection,
)


@dataclass(frozen=True)
class ContributionPattern:
    contribution_id: str
    title: str
    section: str
    keywords: tuple[str, ...]
    impl_hints: tuple[str, ...]


@dataclass(frozen=True)
class ContentSignature:
    contribution_id: str
    title: str
    section: str
    keywords: tuple[str, ...]
    impl_hints: tuple[str, ...]
    triggers: tuple[str, ...]


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

CONTENT_SIGNATURES: tuple[ContentSignature, ...] = (
    ContentSignature(
        contribution_id="C1",
        title="Low-rank adaptation modules",
        section="Abstract",
        keywords=("low-rank", "adapter", "rank-decomposition"),
        impl_hints=("Insert trainable rank-decomposition matrices into attention projections.",),
        triggers=("low-rank", "rank decomposition", "adapter", "adaptation matrices"),
    ),
    ContentSignature(
        contribution_id="C2",
        title="Frozen backbone fine-tuning",
        section="Abstract",
        keywords=("frozen", "backbone", "trainable"),
        impl_hints=("Keep pretrained weights frozen and optimize only the adapter parameters.",),
        triggers=("frozen", "freeze", "pretrained weights", "trainable parameters"),
    ),
    ContentSignature(
        contribution_id="C1",
        title="Direct preference optimization objective",
        section="Abstract",
        keywords=("preference", "objective", "alignment"),
        impl_hints=("Replace reward-model optimization with a direct preference loss over policy outputs.",),
        triggers=("direct preference optimization", "preference objective", "reward model", "preference data"),
    ),
    ContentSignature(
        contribution_id="C1",
        title="IO-aware fused attention kernel",
        section="Abstract",
        keywords=("io-aware", "attention", "kernel"),
        impl_hints=("Fuse tiled attention steps into a memory-efficient exact attention kernel.",),
        triggers=("io-aware", "exact attention", "fused attention", "attention kernel", "tiling"),
    ),
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*•]|(?:\(?\d+[\.\)])|(?:\(?[a-zA-Z][\.\)]))\s+(?P<content>.+)$",
    flags=re.MULTILINE,
)
LEADING_PHRASE_RE = re.compile(
    r"^(?:we|our work|our method|this paper|the paper)\s+"
    r"(?:introduce|propose|present|develop|show|demonstrate|study|build|derive)\s+",
    flags=re.IGNORECASE,
)
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
CONTRIBUTION_MARKERS = (
    "we introduce",
    "we propose",
    "we present",
    "our contributions",
    "our method",
    "this paper",
    "we show",
)
TECHNICAL_MARKERS = (
    "module",
    "objective",
    "kernel",
    "attention",
    "adapter",
    "frozen",
    "alignment",
    "loss",
    "efficient",
    "exact",
    "routing",
    "encoder",
)
SECTION_HEADING_BOOSTS: dict[str, int] = {
    "abstract": 3,
    "introduction": 2,
    "contributions": 5,
    "our contributions": 5,
    "main contributions": 5,
    "method": 3,
    "approach": 3,
    "overview": 2,
    "experiments": 1,
}
SECTION_KIND_MARKERS: dict[str, tuple[str, ...]] = {
    "contributions": ("contributions", "our contributions", "main contributions"),
    "method": ("method", "methods", "approach", "architecture"),
    "abstract": ("abstract",),
    "experiments": ("experiments", "evaluation", "results"),
    "appendix": ("appendix", "supplementary", "implementation details"),
}
REFERENCE_RE = re.compile(r"\b(?:eq(?:uation)?\.?\s*\d+|algorithm\s*\d+|table\s*\d+|fig(?:ure)?\.?\s*\d+)\b", re.I)
DIFFERENCE_MARKERS = ("instead of", "rather than", "without", "compared to", "unlike", "differs from")
PROBLEM_MARKERS = ("for ", "to ", "so that ", "while ", "which ")
IMPL_DETAIL_MARKERS = ("implementation", "training", "hyperparameter", "batch size", "optimizer", "warmup", "kernel")
STEP_SPLIT_RE = re.compile(r"[.;:]\s+|\s+(?:and|then|while)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class SectionFinding:
    section_kind: str
    section_heading: str
    snippet: str
    score: int


@dataclass
class ContributionCluster:
    findings: list[SectionFinding] = field(default_factory=list)
    theme_tokens: set[str] = field(default_factory=set)
    reference_markers: list[str] = field(default_factory=list)


def infer_contributions(case_slug: str, title: str, text: str) -> list[PaperContribution]:
    haystack = f"{title}\n{text}".lower()
    contributions: list[PaperContribution] = []

    for signature in CONTENT_SIGNATURES:
        matched_triggers = [trigger for trigger in signature.triggers if trigger in haystack]
        if not matched_triggers:
            continue
        contributions.append(
            PaperContribution(
                id=signature.contribution_id,
                title=signature.title,
                section=signature.section,
                keywords=list(dict.fromkeys([*signature.keywords, *matched_triggers]))[:4],
                impl_hints=list(signature.impl_hints),
            )
        )

    patterns = CASE_PATTERNS.get(case_slug, ())
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

    if contributions:
        return dedupe_contributions(contributions)

    generic_contributions = infer_sentence_contributions(title, text)
    if generic_contributions:
        return generic_contributions
    return []


def infer_document_contributions(case_slug: str, paper_document: PaperDocument) -> list[PaperContribution]:
    structured_contributions = infer_structured_contributions(paper_document)
    fallback_contributions = infer_contributions(case_slug, paper_document.title, paper_document.text)
    if structured_contributions and fallback_contributions:
        return merge_contribution_sets(structured_contributions, fallback_contributions)
    if structured_contributions:
        return structured_contributions
    return fallback_contributions


def dedupe_contributions(contributions: list[PaperContribution]) -> list[PaperContribution]:
    deduped: dict[str, PaperContribution] = {}
    for contribution in contributions:
        key = contribution.title.lower()
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = contribution
            continue
        deduped[key] = PaperContribution(
            id=existing.id,
            title=existing.title,
            section=existing.section if existing.section != "Abstract" else contribution.section,
            keywords=list(dict.fromkeys([*existing.keywords, *contribution.keywords]))[:6],
            impl_hints=list(dict.fromkeys([*existing.impl_hints, *contribution.impl_hints]))[:4],
        )
    return list(deduped.values())


def tokenize(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS and not token.isdigit()}


def split_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in SENTENCE_SPLIT_RE.split(text) if sentence.strip()]


def extract_list_items(text: str) -> list[str]:
    items = [match.group("content").strip() for match in LIST_ITEM_RE.finditer(text)]
    return [item for item in items if len(item) >= 25]


def normalize_heading(heading: str) -> str:
    normalized = re.sub(r"^\d+(?:\.\d+)*\s+", "", heading.strip().lower())
    return re.sub(r"\s+", " ", normalized)


def section_heading_boost(section_heading: str) -> int:
    normalized = normalize_heading(section_heading)
    for marker, boost in SECTION_HEADING_BOOSTS.items():
        if marker in normalized:
            return boost
    return 0


def classify_section_heading(section_heading: str) -> str:
    normalized = normalize_heading(section_heading)
    for section_kind, markers in SECTION_KIND_MARKERS.items():
        if any(marker in normalized for marker in markers):
            return section_kind
    return "other"


def extract_reference_markers(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in REFERENCE_RE.finditer(text)))


def infer_problem_solved(snippet: str) -> str | None:
    lowered = snippet.lower()
    for marker in PROBLEM_MARKERS:
        if marker in lowered:
            tail = snippet[lowered.index(marker) :].strip()
            return tail[:160]
    return None


def infer_baseline_difference(snippet: str) -> str | None:
    lowered = snippet.lower()
    for marker in DIFFERENCE_MARKERS:
        if marker in lowered:
            start = lowered.index(marker)
            return snippet[start : start + 180].strip()
    return None


def infer_complexity(snippet: str, reference_markers: list[str]) -> int:
    complexity = 2
    token_count = len(tokenize(snippet))
    if token_count >= 12:
        complexity += 1
    if reference_markers:
        complexity += 1
    if any(marker in snippet.lower() for marker in ("algorithm", "objective", "kernel", "architecture")):
        complexity += 1
    return min(complexity, 5)


def score_contribution_sentence(sentence: str) -> int:
    lower_sentence = sentence.lower()
    marker_score = sum(3 for marker in CONTRIBUTION_MARKERS if marker in lower_sentence)
    technical_score = sum(1 for marker in TECHNICAL_MARKERS if marker in lower_sentence)
    token_score = min(len(tokenize(sentence)) // 6, 3)
    return marker_score + technical_score + token_score


def sentence_to_title(sentence: str) -> str:
    normalized = LEADING_PHRASE_RE.sub("", sentence.strip().rstrip("."))
    if not normalized:
        normalized = sentence.strip().rstrip(".")
    words = normalized.split()
    return " ".join(words[:8])[:120]


def infer_sentence_contributions(title: str, text: str) -> list[PaperContribution]:
    candidate_sentences = split_sentences(f"{title}. {text}")
    return infer_ranked_sentences(candidate_sentences, default_section="Abstract")


def infer_ranked_sentences(candidate_sentences: list[str], default_section: str) -> list[PaperContribution]:
    ranked_sentences = sorted(
        ((score_contribution_sentence(sentence), sentence) for sentence in candidate_sentences),
        key=lambda item: (item[0], len(item[1])),
        reverse=True,
    )
    contributions: list[PaperContribution] = []
    for index, (score, sentence) in enumerate(ranked_sentences, start=1):
        if score < 4:
            continue
        keywords = list(tokenize(sentence))[:4]
        if not keywords:
            continue
        contributions.append(
            PaperContribution(
                id=f"H{index}",
                title=sentence_to_title(sentence),
                section=default_section,
                keywords=keywords,
                impl_hints=[sentence.strip()],
                problem_solved=infer_problem_solved(sentence),
                baseline_difference=infer_baseline_difference(sentence),
                evidence_refs=extract_reference_markers(sentence),
                implementation_complexity=infer_complexity(sentence, extract_reference_markers(sentence)),
            )
        )
        if len(contributions) >= 3:
            break
    return dedupe_contributions(contributions)


def iter_candidate_sections(paper_document: PaperDocument) -> list[PaperSection]:
    sections: list[PaperSection] = []
    if paper_document.abstract.strip():
        sections.append(PaperSection(heading="Abstract", text=paper_document.abstract))
    sections.extend(section for section in paper_document.sections if section.text.strip())
    if not sections:
        sections.append(PaperSection(heading="Body", text=paper_document.text))
    return sections


def infer_structured_contributions(paper_document: PaperDocument) -> list[PaperContribution]:
    method_findings = collect_section_findings(paper_document, {"contributions", "method", "abstract"})
    detail_findings = collect_section_findings(paper_document, {"experiments", "appendix"})
    contributions = synthesize_global_contributions(method_findings, detail_findings)
    if not contributions:
        return []
    fallback_contributions = findings_to_contributions(method_findings, prefix="S")
    if fallback_contributions:
        contributions = merge_contribution_sets(contributions, fallback_contributions)
    return merge_contribution_details(contributions, detail_findings)


def collect_section_findings(paper_document: PaperDocument, allowed_kinds: set[str]) -> list[SectionFinding]:
    findings: list[SectionFinding] = []
    for section in iter_candidate_sections(paper_document):
        section_kind = classify_section_heading(section.heading)
        if section_kind not in allowed_kinds:
            continue
        boost = section_heading_boost(section.heading)
        if section_kind in {"contributions", "method"}:
            boost += 2
        elif section_kind in {"experiments", "appendix"}:
            boost += 1
        snippets = [*extract_list_items(section.text), *split_sentences(section.text)]
        for snippet in snippets:
            score = score_contribution_sentence(snippet) + boost
            if section_kind in {"experiments", "appendix"} and any(
                marker in snippet.lower() for marker in IMPL_DETAIL_MARKERS
            ):
                score += 2
            findings.append(
                SectionFinding(
                    section_kind=section_kind,
                    section_heading=section.heading,
                    snippet=snippet,
                    score=score,
                )
            )
    findings.sort(key=lambda item: (item.score, len(item.snippet)), reverse=True)
    return findings


def findings_to_contributions(findings: list[SectionFinding], prefix: str) -> list[PaperContribution]:
    contributions: list[PaperContribution] = []
    for index, finding in enumerate(findings, start=1):
        if finding.score < 5:
            continue
        keywords = list(tokenize(finding.snippet))[:5]
        if not keywords:
            continue
        references = extract_reference_markers(finding.snippet)
        contributions.append(
            PaperContribution(
                id=f"{prefix}{index}",
                title=sentence_to_title(finding.snippet),
                section=finding.section_heading or "Body",
                keywords=keywords,
                impl_hints=[finding.snippet.strip()],
                problem_solved=infer_problem_solved(finding.snippet),
                baseline_difference=infer_baseline_difference(finding.snippet),
                evidence_refs=references,
                implementation_complexity=infer_complexity(finding.snippet, references),
            )
        )
        if len(contributions) >= 5:
            break
    return dedupe_contributions(contributions)


def finding_theme_tokens(finding: SectionFinding) -> set[str]:
    title_tokens = tokenize(sentence_to_title(finding.snippet))
    snippet_tokens = tokenize(finding.snippet)
    return {
        token
        for token in (title_tokens | snippet_tokens)
        if len(token) >= 4 and token not in {"paper", "method", "results", "using", "show", "present"}
    }


def cluster_finding_similarity(cluster: ContributionCluster, finding: SectionFinding) -> int:
    finding_tokens = finding_theme_tokens(finding)
    overlap = len(cluster.theme_tokens & finding_tokens)
    section_bonus = 1 if any(item.section_kind != finding.section_kind for item in cluster.findings) else 0
    reference_bonus = 1 if set(cluster.reference_markers) & set(extract_reference_markers(finding.snippet)) else 0
    return overlap + section_bonus + reference_bonus


def merge_finding_into_cluster(cluster: ContributionCluster, finding: SectionFinding) -> None:
    cluster.findings.append(finding)
    cluster.theme_tokens.update(finding_theme_tokens(finding))
    cluster.reference_markers = list(
        dict.fromkeys([*cluster.reference_markers, *extract_reference_markers(finding.snippet)])
    )[:6]


def build_contribution_clusters(findings: list[SectionFinding]) -> list[ContributionCluster]:
    clusters: list[ContributionCluster] = []
    for finding in findings:
        if finding.score < 4:
            continue
        best_cluster: ContributionCluster | None = None
        best_similarity = 0
        for cluster in clusters:
            similarity = cluster_finding_similarity(cluster, finding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_cluster = cluster
        if best_cluster is not None and best_similarity >= 2:
            merge_finding_into_cluster(best_cluster, finding)
            continue
        cluster = ContributionCluster()
        merge_finding_into_cluster(cluster, finding)
        clusters.append(cluster)
    return clusters


def select_cluster_lead_finding(cluster: ContributionCluster) -> SectionFinding:
    return max(
        cluster.findings,
        key=lambda finding: (
            finding.score,
            finding.section_kind == "contributions",
            finding.section_kind == "method",
            len(finding.snippet),
        ),
    )


def cluster_keywords(cluster: ContributionCluster) -> list[str]:
    counts = Counter(
        token for finding in cluster.findings for token in finding_theme_tokens(finding) if len(token) >= 4
    )
    return [token for token, _ in counts.most_common(6)]


def synthesize_global_contributions(
    core_findings: list[SectionFinding],
    detail_findings: list[SectionFinding],
) -> list[PaperContribution]:
    all_findings = [*core_findings, *detail_findings]
    clusters = build_contribution_clusters(all_findings)
    ranked_clusters = sorted(
        clusters,
        key=lambda cluster: (
            sum(finding.score for finding in cluster.findings)
            + len({finding.section_kind for finding in cluster.findings}),
            len(cluster.findings),
        ),
        reverse=True,
    )
    contributions: list[PaperContribution] = []
    for index, cluster in enumerate(ranked_clusters, start=1):
        if len(contributions) >= 5:
            break
        lead_finding = select_cluster_lead_finding(cluster)
        keywords = cluster_keywords(cluster)
        if len(keywords) < 2:
            continue
        impl_hints = list(
            dict.fromkeys(finding.snippet.strip() for finding in cluster.findings if finding.snippet.strip())
        )[:4]
        references = list(
            dict.fromkeys(
                reference for finding in cluster.findings for reference in extract_reference_markers(finding.snippet)
            )
        )[:6]
        contribution_complexity = max(
            infer_complexity(finding.snippet, extract_reference_markers(finding.snippet))
            for finding in cluster.findings
        )
        contribution_complexity = min(
            contribution_complexity + max(len({finding.section_kind for finding in cluster.findings}) - 1, 0),
            5,
        )
        contributions.append(
            PaperContribution(
                id=f"G{index}",
                title=sentence_to_title(lead_finding.snippet),
                section=lead_finding.section_heading or "Body",
                keywords=keywords,
                impl_hints=impl_hints,
                problem_solved=next(
                    (
                        inferred
                        for finding in cluster.findings
                        if (inferred := infer_problem_solved(finding.snippet)) is not None
                    ),
                    None,
                ),
                baseline_difference=next(
                    (
                        inferred
                        for finding in cluster.findings
                        if (inferred := infer_baseline_difference(finding.snippet)) is not None
                    ),
                    None,
                ),
                evidence_refs=references,
                implementation_complexity=contribution_complexity,
            )
        )
    return dedupe_contributions(contributions)


def contribution_similarity(left: PaperContribution, right: PaperContribution) -> int:
    return len(set(left.keywords) & set(right.keywords)) + len(tokenize(left.title) & tokenize(right.title))


def merge_contribution_sets(
    primary: list[PaperContribution],
    secondary: list[PaperContribution],
) -> list[PaperContribution]:
    merged = list(primary)
    for contribution in secondary:
        best_match = next((item for item in merged if contribution_similarity(item, contribution) >= 2), None)
        if best_match is None:
            merged.append(contribution)
            continue
        merged[merged.index(best_match)] = PaperContribution(
            id=best_match.id,
            title=best_match.title,
            section=best_match.section,
            keywords=list(dict.fromkeys([*best_match.keywords, *contribution.keywords]))[:6],
            impl_hints=list(dict.fromkeys([*best_match.impl_hints, *contribution.impl_hints]))[:5],
            problem_solved=best_match.problem_solved or contribution.problem_solved,
            baseline_difference=best_match.baseline_difference or contribution.baseline_difference,
            evidence_refs=list(dict.fromkeys([*best_match.evidence_refs, *contribution.evidence_refs]))[:4],
            implementation_complexity=max(
                best_match.implementation_complexity or 1,
                contribution.implementation_complexity or 1,
            ),
        )
    return dedupe_contributions(merged)


def merge_contribution_details(
    contributions: list[PaperContribution],
    detail_findings: list[SectionFinding],
) -> list[PaperContribution]:
    merged = list(contributions)
    for finding in detail_findings:
        if finding.score < 4:
            continue
        detail_tokens = tokenize(finding.snippet)
        if not detail_tokens:
            continue
        best_index = -1
        best_score = 0
        for index, contribution in enumerate(merged):
            score = len(detail_tokens & set(contribution.keywords)) + len(tokenize(contribution.title) & detail_tokens)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index == -1 or best_score < 2:
            continue
        contribution = merged[best_index]
        merged[best_index] = PaperContribution(
            id=contribution.id,
            title=contribution.title,
            section=contribution.section,
            keywords=list(dict.fromkeys([*contribution.keywords, *list(detail_tokens)[:3]]))[:6],
            impl_hints=list(dict.fromkeys([*contribution.impl_hints, finding.snippet.strip()]))[:5],
            problem_solved=contribution.problem_solved,
            baseline_difference=contribution.baseline_difference,
            evidence_refs=list(
                dict.fromkeys([*contribution.evidence_refs, *extract_reference_markers(finding.snippet)])
            )[:4],
            implementation_complexity=min((contribution.implementation_complexity or 2) + 1, 5),
        )
    return dedupe_contributions(merged)


def parser_gap_warnings(paper_document: PaperDocument, contributions: list[PaperContribution]) -> list[str]:
    section_kinds = {classify_section_heading(section.heading) for section in iter_candidate_sections(paper_document)}
    warnings: list[str] = []
    if "contributions" not in section_kinds:
        warnings.append("Paper parser did not find an explicit contributions section.")
    if "method" not in section_kinds:
        warnings.append("Paper parser did not find an explicit method section.")
    if contributions and all(contribution.section.lower() == "abstract" for contribution in contributions):
        warnings.append("Paper parser relied on abstract-level evidence only.")
    if (
        contributions
        and any(contribution.evidence_refs for contribution in contributions)
        and not any(len(contribution.impl_hints) > 1 for contribution in contributions)
    ):
        warnings.append("Paper parser found theoretical references without matching implementation details.")
    if len(contributions) < 2 and len(paper_document.text) > 1200:
        warnings.append("Paper parser extracted limited contribution structure from a longer document.")
    return warnings


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
    haystack = " ".join(
        [diff_cluster.label, diff_cluster.summary, *diff_cluster.files, *diff_cluster.semantic_tags]
    ).lower()
    keyword_hits = [keyword for keyword in contribution.keywords if keyword.lower() in haystack]
    title_hits = sorted(token for token in tokenize(contribution.title) if token in haystack)
    hint_hits = sorted({token for hint in contribution.impl_hints for token in tokenize(hint) if token in haystack})
    reference_hits = [reference for reference in contribution.evidence_refs if reference.lower() in haystack]

    step_hits, missing_steps = trace_contribution_steps(contribution, diff_cluster)
    score = (
        len(keyword_hits) * 5
        + len(title_hits) * 2
        + min(len(hint_hits), 3)
        + len(reference_hits) * 2
        + len(step_hits) * 3
    )
    if score == 0:
        return 0, ""

    evidence_parts: list[str] = []
    if keyword_hits:
        evidence_parts.append(f"keyword hits: {', '.join(keyword_hits[:3])}")
    if title_hits:
        evidence_parts.append(f"title overlap: {', '.join(title_hits[:3])}")
    if hint_hits:
        evidence_parts.append(f"impl hints: {', '.join(hint_hits[:3])}")
    if reference_hits:
        evidence_parts.append(f"reference overlap: {', '.join(reference_hits[:2])}")
    if step_hits:
        evidence_parts.append(f"algorithm steps: {', '.join(step_hits[:2])}")
    elif missing_steps:
        evidence_parts.append(f"step gaps: {', '.join(missing_steps[:2])}")
    if diff_cluster.semantic_tags:
        evidence_parts.append(f"semantic tags: {', '.join(diff_cluster.semantic_tags[:3])}")
    evidence_parts.append(f"cluster files: {', '.join(diff_cluster.files[:2])}")
    evidence_parts.append(f"cluster type: {diff_cluster.change_type}")
    return score, "; ".join(evidence_parts)


def extract_impl_steps(contribution: PaperContribution) -> list[str]:
    raw_steps = [contribution.title, *contribution.impl_hints]
    steps: list[str] = []
    for raw_step in raw_steps:
        for segment in STEP_SPLIT_RE.split(raw_step):
            normalized = segment.strip(" -")
            if len(normalized) < 18:
                continue
            steps.append(normalized)
    return list(dict.fromkeys(steps))[:5]


def trace_contribution_steps(
    contribution: PaperContribution,
    diff_cluster: DiffCluster,
) -> tuple[list[str], list[str]]:
    haystack = " ".join(
        [diff_cluster.label, diff_cluster.summary, *diff_cluster.files, *diff_cluster.semantic_tags]
    ).lower()
    supported_steps: list[str] = []
    missing_steps: list[str] = []
    for step in extract_impl_steps(contribution):
        step_tokens = [token for token in tokenize(step) if len(token) >= 4]
        overlap = [token for token in step_tokens if token in haystack]
        if len(overlap) >= 2 or (overlap and any(tag in overlap for tag in diff_cluster.semantic_tags)):
            supported_steps.append(sentence_to_title(step))
        else:
            missing_steps.append(sentence_to_title(step))
    return supported_steps, missing_steps


def path_review_tokens(relative_path: str) -> set[str]:
    normalized = relative_path.replace("/", " ").replace("_", " ").replace(".", " ")
    return tokenize(normalized)


def select_learning_entry_point(contribution: PaperContribution, diff_cluster: DiffCluster) -> str | None:
    best_file: str | None = None
    best_score = -1
    contribution_tokens = tokenize(contribution.title) | set(contribution.keywords)
    for relative_path in diff_cluster.files:
        file_score = len(contribution_tokens & path_review_tokens(relative_path))
        if file_score > best_score:
            best_score = file_score
            best_file = relative_path
    return best_file or (diff_cluster.files[0] if diff_cluster.files else None)


def order_cluster_files_for_review(contribution: PaperContribution, diff_cluster: DiffCluster) -> list[str]:
    contribution_tokens = tokenize(contribution.title) | set(contribution.keywords)
    return sorted(
        diff_cluster.files,
        key=lambda relative_path: (
            -len(contribution_tokens & path_review_tokens(relative_path)),
            -int(any(tag in relative_path.lower() for tag in diff_cluster.semantic_tags)),
            relative_path,
        ),
    )


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
        supported_steps, missing_steps = trace_contribution_steps(selected_contribution, diff_cluster)
        total_steps = max(len(extract_impl_steps(selected_contribution)), 1)
        step_coverage = len(supported_steps) / total_steps
        confidence = min(0.6 + 0.035 * score + 0.12 * step_coverage, 0.97)
        implementation_coverage = min(0.15 + 0.06 * score + 0.25 * step_coverage, 1.0)
        missing_aspects: list[str] = []
        if missing_steps:
            missing_aspects.append(f"untraced implementation steps: {', '.join(missing_steps[:2])}")
        if selected_contribution.evidence_refs and len(selected_contribution.impl_hints) < 2:
            missing_aspects.append("theoretical reference is present but implementation detail remains sparse")
        if selected_contribution.baseline_difference and not any(
            token in diff_cluster.summary.lower() for token in tokenize(selected_contribution.baseline_difference)
        ):
            missing_aspects.append("baseline difference is not directly visible in the chosen diff cluster")
        engineering_divergences: list[str] = []
        if diff_cluster.change_type.name == "MODIFIED_INFRA":
            engineering_divergences.append("implementation is exposed mostly through infrastructure-level changes")
        if diff_cluster.semantic_tags and not set(diff_cluster.semantic_tags) & set(selected_contribution.keywords):
            engineering_divergences.append(
                "cluster semantics only partially align with the paper contribution vocabulary"
            )
        if implementation_coverage >= 0.85:
            coverage_type = CoverageType.FULL
            completeness = "complete"
        elif implementation_coverage >= 0.6:
            coverage_type = CoverageType.PARTIAL
            completeness = "partial"
        elif score >= 3:
            coverage_type = CoverageType.APPROXIMATED
            completeness = "approximate"
        else:
            coverage_type = CoverageType.MISSING
            completeness = "missing"
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
                completeness=completeness,
                implementation_coverage=round(implementation_coverage, 2),
                coverage_type=coverage_type,
                missing_aspects=missing_aspects,
                engineering_divergences=engineering_divergences,
                learning_entry_point=select_learning_entry_point(selected_contribution, diff_cluster),
                reading_order=order_cluster_files_for_review(selected_contribution, diff_cluster),
            )
        )
    return mappings
