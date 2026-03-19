from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import (
    load_golden_case,
)
from papertrace_core.heuristics import collect_unmatched_ids, infer_contributions, infer_mappings
from papertrace_core.inputs import detect_paper_source_kind, normalize_repo_url
from papertrace_core.interfaces import (
    ContributionMapper,
    DiffAnalyzer,
    DiffOutput,
    MappingOutput,
    PaperParser,
    PaperSourceFetcher,
    ParseOutput,
    RepoMetadataProvider,
    RepoMirror,
    RepoTracer,
    TraceOutput,
)
from papertrace_core.llm import LLMClient, build_llm_client
from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    DiffChangeType,
    DiffCluster,
    PaperContribution,
    PaperDocument,
    ProcessorMode,
)
from papertrace_core.paper_sources import (
    ArxivPaperSourceFetcher,
    ChainedPaperSourceFetcher,
    FixturePaperSourceFetcher,
)
from papertrace_core.repo_metadata import (
    ChainedRepoMetadataProvider,
    FixtureRepoMetadataProvider,
    GitHubRepoMetadataProvider,
    repo_aliases,
)
from papertrace_core.repos import RepoAccessError, ShallowGitRepoMirror
from papertrace_core.settings import Settings, get_settings

STRATEGY_PRIORITY: dict[str, int] = {
    "github_fork": 5,
    "readme_declaration": 4,
    "paper_mention": 3,
    "code_fingerprint": 2,
    "fallback": 1,
}

KNOWN_UPSTREAM_ALIAS_MAP: dict[str, str] = {
    "transformers": "https://github.com/huggingface/transformers",
    "huggingface/transformers": "https://github.com/huggingface/transformers",
    "trl": "https://github.com/huggingface/trl",
    "huggingface/trl": "https://github.com/huggingface/trl",
    "triton": "https://github.com/openai/triton",
    "openai/triton": "https://github.com/openai/triton",
}

DECLARATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbased on\b", flags=0),
    re.compile(r"\bbuilt on top of\b", flags=0),
    re.compile(r"\bfollow(?:ing|s)?\b", flags=0),
    re.compile(r"\btransformers\b", flags=0),
    re.compile(r"\btrl\b", flags=0),
)
GITHUB_URL_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
SYMBOL_RE = re.compile(r"\b(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)")
IMPORT_RE = re.compile(r"\b(?:from|import)\s+([A-Za-z_][A-Za-z0-9_\.]*)")
FINGERPRINT_SCORE_THRESHOLD = 0.12


def dedupe_preserving_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def build_repo_file_haystack(relative_path: str, content: str) -> str:
    return f"{relative_path}\n{content}".lower()


def should_include_repo_file(relative_path: str, settings: Settings) -> bool:
    path = Path(relative_path)
    if path.name.startswith(".") or ".git" in path.parts:
        return False
    if any(part.lower() in settings.repo_analysis_exclude_dirs for part in path.parts):
        return False
    if path.name.lower() in settings.repo_analysis_exclude_filenames:
        return False

    if settings.repo_analysis_include_dirs and not any(
        relative_path == prefix or relative_path.startswith(f"{prefix}/")
        for prefix in settings.repo_analysis_include_dirs
    ):
        return False

    if path.suffix.lower() not in settings.repo_analysis_extensions:
        return False

    return True


def list_tracked_files(repo_root: Path, settings: Settings) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.repo_clone_timeout_seconds,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise RepoAccessError(f"Failed to enumerate tracked files for {repo_root}: {exc}") from exc

    tracked = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return [path for path in tracked if should_include_repo_file(path, settings)]


def load_repo_snapshot(repo_root: Path, settings: Settings) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for relative_path in list_tracked_files(repo_root, settings):
        if len(snapshot) >= settings.repo_max_files:
            break
        file_path = repo_root / relative_path
        if not file_path.is_file():
            continue
        if file_path.stat().st_size > settings.repo_max_file_size_bytes:
            continue
        try:
            snapshot[relative_path] = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return snapshot


def classify_change_type(
    relative_path: str,
    content: str,
    is_new_file: bool,
) -> tuple[DiffChangeType, str]:
    haystack = build_repo_file_haystack(relative_path, content)
    if any(token in haystack for token in ("loss", "reward", "preference", "logits")):
        return DiffChangeType.MODIFIED_LOSS, "content includes loss, reward, or preference tokens"
    if any(token in haystack for token in ("train", "trainer", "optimizer", "finetune")):
        return (
            DiffChangeType.MODIFIED_TRAIN,
            "content includes train, trainer, or optimizer tokens",
        )
    if any(token in haystack for token in ("docker", "workflow", "infra", "config", "script")):
        return (
            DiffChangeType.MODIFIED_INFRA,
            "path or content looks like config or workflow plumbing",
        )
    if is_new_file:
        return (
            DiffChangeType.NEW_MODULE,
            "file is newly introduced relative to the selected base repo",
        )
    return (
        DiffChangeType.MODIFIED_CORE,
        "existing implementation file changed outside infra buckets",
    )


def select_cluster_label(
    relative_path: str,
    content: str,
    contributions: list[PaperContribution],
    change_type: DiffChangeType,
) -> str:
    haystack = build_repo_file_haystack(relative_path, content)
    ranked: list[tuple[int, str]] = []
    for contribution in contributions:
        score = sum(1 for keyword in contribution.keywords if keyword.lower() in haystack)
        if score > 0:
            ranked.append((score, contribution.title))
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][1]
    default_labels = {
        DiffChangeType.NEW_MODULE: "New implementation modules",
        DiffChangeType.MODIFIED_CORE: "Core implementation changes",
        DiffChangeType.MODIFIED_LOSS: "Loss and objective changes",
        DiffChangeType.MODIFIED_TRAIN: "Training flow changes",
        DiffChangeType.MODIFIED_INFRA: "Infrastructure changes",
    }
    return default_labels[change_type]


def summarize_cluster(
    label: str,
    files: list[str],
    change_type: DiffChangeType,
    rationale: str,
) -> str:
    if len(files) == 1:
        return f"{label} inferred from {files[0]}; bucketed as {change_type.lower()} because {rationale}."
    return f"{label} inferred from {len(files)} files; bucketed as {change_type.lower()} because {rationale}."


@dataclass(frozen=True)
class HeuristicPaperParser:
    llm_client: LLMClient | None = None

    def parse(self, request: AnalysisRequest, paper_document: PaperDocument) -> ParseOutput:
        case_slug = detect_case_slug(request)
        warnings: list[str] = []
        if self.llm_client is not None:
            try:
                llm_contributions = self.llm_client.extract_contributions(paper_document)
                if llm_contributions:
                    return ParseOutput(
                        contributions=llm_contributions,
                        mode=ProcessorMode.LLM,
                        warnings=[],
                    )
                warnings.append("Paper parser received an empty llm response and fell back.")
            except Exception:
                warnings.append("Paper parser fell back from llm to heuristic extraction.")
        contributions = infer_contributions(case_slug, paper_document.title, paper_document.text)
        if contributions:
            return ParseOutput(
                contributions=contributions,
                mode=ProcessorMode.HEURISTIC,
                warnings=warnings,
            )
        fixture = load_golden_case(case_slug)
        return ParseOutput(
            contributions=fixture.contributions,
            mode=ProcessorMode.FIXTURE,
            warnings=[*warnings, "Paper parser fell back to fixture contributions."],
        )


def sort_repo_candidates(candidates: list[BaseRepoCandidate]) -> list[BaseRepoCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            STRATEGY_PRIORITY.get(candidate.strategy, 0),
            candidate.confidence,
            candidate.repo_url,
        ),
        reverse=True,
    )


def dedupe_repo_candidates(candidates: list[BaseRepoCandidate]) -> list[BaseRepoCandidate]:
    by_repo_url: dict[str, BaseRepoCandidate] = {}
    for candidate in sort_repo_candidates(candidates):
        by_repo_url.setdefault(candidate.repo_url, candidate)
    return sort_repo_candidates(list(by_repo_url.values()))


def unique_repo_urls(repo_urls: list[str]) -> list[str]:
    return list(dict.fromkeys(repo_urls))


def known_upstream_repo_urls() -> list[str]:
    return unique_repo_urls(list(KNOWN_UPSTREAM_ALIAS_MAP.values()))


def text_contains_alias(haystack: str, alias: str) -> bool:
    if "/" in alias:
        return alias in haystack
    return re.search(rf"\b{re.escape(alias)}\b", haystack) is not None


def build_paper_mention_candidates(paper_document: PaperDocument) -> list[BaseRepoCandidate]:
    haystack = paper_document.text.lower()
    candidates: list[BaseRepoCandidate] = []

    for matched_url in GITHUB_URL_RE.findall(paper_document.text):
        try:
            repo_url = normalize_repo_url(matched_url)
        except ValueError:
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="paper_mention",
                confidence=0.9,
                evidence=f"Paper text directly references {repo_url}.",
            )
        )

    for alias, repo_url in KNOWN_UPSTREAM_ALIAS_MAP.items():
        if not text_contains_alias(haystack, alias):
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="paper_mention",
                confidence=0.76 if "/" in alias else 0.7,
                evidence=f"Paper text mentions the {alias} codebase.",
            )
        )

    return dedupe_repo_candidates(candidates)


def build_readme_candidates(
    request: AnalysisRequest,
    readme_haystack: str,
    paper_candidates: list[BaseRepoCandidate],
    candidate_repo_urls: list[str],
) -> list[BaseRepoCandidate]:
    candidates: list[BaseRepoCandidate] = []
    declaration_match = any(pattern.search(readme_haystack) for pattern in DECLARATION_PATTERNS)

    for candidate in paper_candidates:
        aliases = repo_aliases(candidate.repo_url)
        if candidate.repo_url.lower() in readme_haystack or any(alias in readme_haystack for alias in aliases):
            evidence = (
                f"Repository README declares an upstream relationship with {candidate.repo_url}."
                if declaration_match
                else f"Repository README references {candidate.repo_url}."
            )
            candidates.append(
                BaseRepoCandidate(
                    repo_url=candidate.repo_url,
                    strategy="readme_declaration",
                    confidence=min(candidate.confidence + 0.02, 0.98),
                    evidence=evidence,
                )
            )

    derived_readme_targets = [repo_url for repo_url in candidate_repo_urls if repo_url != request.repo_url]
    for repo_url in derived_readme_targets:
        aliases = repo_aliases(repo_url)
        if repo_url.lower() not in readme_haystack and not any(alias in readme_haystack for alias in aliases):
            continue
        evidence = (
            f"Repository README references the {aliases[0]} codebase in an upstream declaration."
            if declaration_match
            else f"Repository README references the {aliases[0]} codebase."
        )
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="readme_declaration",
                confidence=0.8 if repo_url != request.repo_url else 0.76,
                evidence=evidence,
            )
        )

    if not candidates:
        request_aliases = repo_aliases(request.repo_url)
        if request.repo_url.lower() in readme_haystack or any(alias in readme_haystack for alias in request_aliases):
            evidence = (
                "Repository README references the submitted repository ecosystem in an upstream declaration."
                if declaration_match
                else "Repository README references the submitted repository ecosystem."
            )
            candidates.append(
                BaseRepoCandidate(
                    repo_url=request.repo_url,
                    strategy="readme_declaration",
                    confidence=0.76,
                    evidence=evidence,
                )
            )

    return dedupe_repo_candidates(candidates)


def build_snapshot_path_tokens(snapshot: dict[str, str]) -> set[str]:
    tokens: set[str] = set()
    for relative_path in snapshot:
        path = Path(relative_path)
        tokens.update(part.lower() for part in path.parts if len(part) >= 3)
        if len(path.stem) >= 3:
            tokens.add(path.stem.lower())
    return tokens


def build_snapshot_symbol_tokens(snapshot: dict[str, str]) -> set[str]:
    tokens: set[str] = set()
    for content in snapshot.values():
        tokens.update(symbol.lower() for symbol in SYMBOL_RE.findall(content))
        for imported_name in IMPORT_RE.findall(content):
            tokens.update(part.lower() for part in imported_name.split(".") if len(part) >= 3)
        tokens.update(token.lower() for token in IDENTIFIER_RE.findall(content) if len(token) >= 4)
    return tokens


def overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def fingerprint_candidate(
    target_snapshot: dict[str, str],
    candidate_snapshot: dict[str, str],
) -> tuple[float, str]:
    target_path_tokens = build_snapshot_path_tokens(target_snapshot)
    candidate_path_tokens = build_snapshot_path_tokens(candidate_snapshot)
    target_symbol_tokens = build_snapshot_symbol_tokens(target_snapshot)
    candidate_symbol_tokens = build_snapshot_symbol_tokens(candidate_snapshot)

    path_score = overlap_ratio(target_path_tokens, candidate_path_tokens)
    symbol_score = overlap_ratio(target_symbol_tokens, candidate_symbol_tokens)
    combined_score = 0.35 * path_score + 0.65 * symbol_score
    shared_paths = len(target_path_tokens & candidate_path_tokens)
    shared_symbols = len(target_symbol_tokens & candidate_symbol_tokens)
    evidence = f"Fingerprint overlap found {shared_paths} shared path tokens and {shared_symbols} shared symbol tokens."
    return combined_score, evidence


def build_code_fingerprint_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
    candidate_repo_urls: list[str],
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None or settings is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
        target_snapshot = load_repo_snapshot(target_root, settings)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped fingerprint analysis: {exc}"]

    fingerprint_candidates: list[BaseRepoCandidate] = []
    warnings: list[str] = []
    for candidate_repo_url in candidate_repo_urls:
        if candidate_repo_url == request.repo_url:
            continue
        try:
            candidate_root = repo_mirror.prepare(candidate_repo_url)
            candidate_snapshot = load_repo_snapshot(candidate_root, settings)
        except (RepoAccessError, KeyError) as exc:
            warnings.append(f"Repo tracer skipped fingerprint candidate {candidate_repo_url}: {exc}")
            continue

        score, evidence = fingerprint_candidate(target_snapshot, candidate_snapshot)
        if score < FINGERPRINT_SCORE_THRESHOLD:
            continue
        fingerprint_candidates.append(
            BaseRepoCandidate(
                repo_url=candidate_repo_url,
                strategy="code_fingerprint",
                confidence=round(min(0.55 + score, 0.92), 2),
                evidence=evidence,
            )
        )

    return dedupe_repo_candidates(fingerprint_candidates), warnings


@dataclass(frozen=True)
class StrategyDrivenRepoTracer:
    repo_metadata_provider: RepoMetadataProvider
    repo_mirror: RepoMirror | None = None
    settings: Settings | None = None

    def trace(
        self,
        request: AnalysisRequest,
        paper_document: PaperDocument,
        contributions: list[PaperContribution],
    ) -> TraceOutput:
        del contributions
        case_slug = detect_case_slug(request)
        golden = load_golden_case(case_slug)
        metadata_output = self.repo_metadata_provider.fetch(request)
        warnings = list(metadata_output.warnings)

        candidates: list[BaseRepoCandidate] = []
        if metadata_output.fork_parent:
            candidates.append(
                BaseRepoCandidate(
                    repo_url=metadata_output.fork_parent,
                    strategy="github_fork",
                    confidence=0.99,
                    evidence="Repository metadata exposes an upstream fork parent.",
                )
            )

        paper_candidates = build_paper_mention_candidates(paper_document)
        candidates.extend(paper_candidates)
        candidate_repo_urls = unique_repo_urls(
            [metadata_output.fork_parent]
            if metadata_output.fork_parent
            else [] + [candidate.repo_url for candidate in paper_candidates] + known_upstream_repo_urls()
        )
        readme_haystack = f"{metadata_output.readme_text}\n{metadata_output.notes}".lower()
        candidates.extend(
            build_readme_candidates(
                request,
                readme_haystack,
                paper_candidates,
                candidate_repo_urls,
            )
        )

        fingerprint_candidates, fingerprint_warnings = build_code_fingerprint_candidates(
            request,
            self.repo_mirror,
            self.settings,
            candidate_repo_urls,
        )
        warnings.extend(fingerprint_warnings)
        candidates.extend(fingerprint_candidates)

        if not candidates:
            candidates.extend(
                BaseRepoCandidate(
                    repo_url=candidate.repo_url,
                    strategy="fallback",
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                )
                for candidate in golden.base_repo_candidates
            )

        deduped = dedupe_repo_candidates(candidates)
        return TraceOutput(
            selected_base_repo=deduped[0],
            candidates=deduped,
            mode=ProcessorMode.STRATEGY_CHAIN,
            warnings=warnings,
        )


class FixtureDiffAnalyzer:
    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> DiffOutput:
        del selected_base_repo, contributions
        fixture = load_golden_case(detect_case_slug(request))
        return DiffOutput(
            diff_clusters=fixture.diff_clusters,
            mode=ProcessorMode.FIXTURE,
            warnings=["Diff analyzer is currently fixture-backed."],
        )


@dataclass(frozen=True)
class LiveRepoDiffAnalyzer:
    repo_mirror: RepoMirror
    settings: Settings

    def analyze(
        self,
        request: AnalysisRequest,
        selected_base_repo: BaseRepoCandidate,
        contributions: list[PaperContribution],
    ) -> DiffOutput:
        fixture = load_golden_case(detect_case_slug(request))
        try:
            base_root = self.repo_mirror.prepare(selected_base_repo.repo_url)
            target_root = self.repo_mirror.prepare(request.repo_url)
            base_snapshot = load_repo_snapshot(base_root, self.settings)
            target_snapshot = load_repo_snapshot(target_root, self.settings)
        except RepoAccessError as exc:
            return DiffOutput(
                diff_clusters=fixture.diff_clusters,
                mode=ProcessorMode.FIXTURE,
                warnings=[
                    "Diff analyzer fell back to fixture diff clusters.",
                    str(exc),
                ],
            )

        grouped: dict[tuple[DiffChangeType, str], list[str]] = {}
        rationales: dict[tuple[DiffChangeType, str], str] = {}
        for relative_path, content in target_snapshot.items():
            base_content = base_snapshot.get(relative_path)
            if base_content == content:
                continue
            change_type, rationale = classify_change_type(
                relative_path,
                content,
                is_new_file=relative_path not in base_snapshot,
            )
            label = select_cluster_label(relative_path, content, contributions, change_type)
            group_key = (change_type, label)
            grouped.setdefault(group_key, []).append(relative_path)
            rationales.setdefault(group_key, rationale)

        if not grouped:
            return DiffOutput(
                diff_clusters=fixture.diff_clusters,
                mode=ProcessorMode.FIXTURE,
                warnings=[
                    ("Diff analyzer found no meaningful tracked-file changes and fell back to fixture diff clusters."),
                ],
            )

        diff_clusters = [
            DiffCluster(
                id=f"D{index}",
                label=label,
                change_type=change_type,
                files=sorted(files),
                summary=summarize_cluster(
                    label,
                    sorted(files),
                    change_type,
                    rationales[(change_type, label)],
                ),
            )
            for index, ((change_type, label), files) in enumerate(grouped.items(), start=1)
        ]
        return DiffOutput(
            diff_clusters=diff_clusters,
            mode=ProcessorMode.HEURISTIC,
            warnings=[],
        )


@dataclass(frozen=True)
class FixtureContributionMapper:
    llm_client: LLMClient | None = None

    def map(
        self,
        request: AnalysisRequest,
        contributions: list[PaperContribution],
        diff_clusters: list[DiffCluster],
    ) -> MappingOutput:
        warnings: list[str] = []
        if self.llm_client is not None:
            try:
                llm_mappings = self.llm_client.map_contributions(contributions, diff_clusters)
                if llm_mappings:
                    unmatched_contribution_ids, unmatched_diff_cluster_ids = collect_unmatched_ids(
                        contributions,
                        diff_clusters,
                        llm_mappings,
                    )
                    return MappingOutput(
                        mappings=llm_mappings,
                        unmatched_contribution_ids=unmatched_contribution_ids,
                        unmatched_diff_cluster_ids=unmatched_diff_cluster_ids,
                        mode=ProcessorMode.LLM,
                        warnings=[],
                    )
                warnings.append("Contribution mapper received an empty llm response and fell back.")
            except Exception:
                warnings.append("Contribution mapper fell back from llm to heuristic matching.")
        mappings = infer_mappings(contributions, diff_clusters)
        unmatched_contribution_ids, unmatched_diff_cluster_ids = collect_unmatched_ids(
            contributions,
            diff_clusters,
            mappings,
        )
        if not mappings:
            warnings.append("Contribution mapper found no confident heuristic matches.")
        return MappingOutput(
            mappings=mappings,
            unmatched_contribution_ids=unmatched_contribution_ids,
            unmatched_diff_cluster_ids=unmatched_diff_cluster_ids,
            mode=ProcessorMode.HEURISTIC,
            warnings=warnings,
        )


@dataclass(frozen=True)
class AnalysisService:
    paper_source_fetcher: PaperSourceFetcher
    paper_parser: PaperParser
    repo_tracer: RepoTracer
    diff_analyzer: DiffAnalyzer
    contribution_mapper: ContributionMapper

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        fixture = load_golden_case(detect_case_slug(request))
        fetch_output = self.paper_source_fetcher.fetch(request)
        parse_output = self.paper_parser.parse(request, fetch_output.paper_document)
        trace_output = self.repo_tracer.trace(
            request,
            fetch_output.paper_document,
            parse_output.contributions,
        )
        diff_output = self.diff_analyzer.analyze(
            request,
            trace_output.selected_base_repo,
            parse_output.contributions,
        )
        mapping_output = self.contribution_mapper.map(
            request,
            parse_output.contributions,
            diff_output.diff_clusters,
        )
        stage_warnings = dedupe_preserving_order(
            [
                *fetch_output.warnings,
                *parse_output.warnings,
                *trace_output.warnings,
                *diff_output.warnings,
                *mapping_output.warnings,
            ]
        )
        warnings = dedupe_preserving_order([*fixture.warnings, *stage_warnings])
        return AnalysisResult(
            case_slug=fixture.case_slug,
            summary=fixture.summary,
            selected_base_repo=trace_output.selected_base_repo,
            base_repo_candidates=trace_output.candidates,
            contributions=parse_output.contributions,
            diff_clusters=diff_output.diff_clusters,
            mappings=mapping_output.mappings,
            unmatched_contribution_ids=mapping_output.unmatched_contribution_ids,
            unmatched_diff_cluster_ids=mapping_output.unmatched_diff_cluster_ids,
            metadata=AnalysisRuntimeMetadata(
                paper_source_kind=detect_paper_source_kind(request.paper_source),
                paper_fetch_mode=fetch_output.mode,
                parser_mode=parse_output.mode,
                repo_tracer_mode=trace_output.mode,
                diff_analyzer_mode=diff_output.mode,
                contribution_mapper_mode=mapping_output.mode,
                selected_repo_strategy=trace_output.selected_base_repo.strategy,
                fallback_notes=stage_warnings,
            ),
            warnings=warnings,
        )


def build_default_analysis_service() -> AnalysisService:
    settings = get_settings()
    llm_client = build_llm_client(settings)
    paper_source_fetcher: PaperSourceFetcher
    diff_analyzer: DiffAnalyzer
    repo_tracer_provider: RepoMetadataProvider
    repo_mirror: RepoMirror | None = None
    if settings.use_live_paper_fetch():
        paper_source_fetcher = ChainedPaperSourceFetcher(
            primary=ArxivPaperSourceFetcher(settings),
            fallback=FixturePaperSourceFetcher(),
        )
    else:
        paper_source_fetcher = FixturePaperSourceFetcher()
    if settings.use_live_repo_trace():
        repo_tracer_provider = ChainedRepoMetadataProvider(
            primary=GitHubRepoMetadataProvider(settings),
            fallback=FixtureRepoMetadataProvider(),
        )
    else:
        repo_tracer_provider = FixtureRepoMetadataProvider()
    if settings.use_live_repo_trace() or settings.use_live_repo_analysis():
        repo_mirror = ShallowGitRepoMirror(settings)
    if settings.use_live_repo_analysis():
        diff_analyzer = LiveRepoDiffAnalyzer(
            repo_mirror=repo_mirror or ShallowGitRepoMirror(settings),
            settings=settings,
        )
    else:
        diff_analyzer = FixtureDiffAnalyzer()
    return AnalysisService(
        paper_source_fetcher=paper_source_fetcher,
        paper_parser=HeuristicPaperParser(llm_client=llm_client),
        repo_tracer=StrategyDrivenRepoTracer(
            repo_metadata_provider=repo_tracer_provider,
            repo_mirror=repo_mirror,
            settings=settings,
        ),
        diff_analyzer=diff_analyzer,
        contribution_mapper=FixtureContributionMapper(llm_client=llm_client),
    )
