from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import (
    load_golden_case,
)
from papertrace_core.heuristics import (
    collect_unmatched_ids,
    infer_document_contributions,
    infer_mappings,
    parser_gap_warnings,
    tokenize,
)
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
    PdfPaperSourceFetcher,
    SourceAwarePaperSourceFetcher,
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
    "framework_signature": 4,
    "fossil_evidence": 4,
    "paper_mention": 3,
    "dependency_archaeology": 3,
    "shape_similarity": 3,
    "code_reference": 2,
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
DIRECT_DEPENDENCY_RE = re.compile(r"(?:git\+)?(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?)")
LOCAL_IMPORT_RE = re.compile(r"\b(?:from|import)\s+([A-Za-z_][A-Za-z0-9_\.]*)")
SEMANTIC_TAG_PATTERNS: dict[str, tuple[str, ...]] = {
    "attention": ("attention", "attn", "qkv"),
    "adapter": ("adapter", "lora", "rank"),
    "loss": ("loss", "objective", "reward", "preference"),
    "training": ("trainer", "optimizer", "warmup", "scheduler", "train"),
    "kernel": ("kernel", "triton", "cuda", "fused"),
    "data": ("dataset", "dataloader", "tokenizer", "preprocess"),
    "inference": ("decode", "generate", "inference", "sampling"),
}


@dataclass(frozen=True)
class FrameworkSignature:
    repo_url: str
    imports: tuple[str, ...]
    base_classes: tuple[str, ...]
    dir_markers: tuple[str, ...]


@dataclass(frozen=True)
class ChangedFile:
    relative_path: str
    content: str
    change_type: DiffChangeType
    label: str
    rationale: str
    semantic_tags: list[str]
    imports: set[str]
    parent_dir: str
    stem: str


@dataclass
class ClusterState:
    change_type: DiffChangeType
    label: str
    files: list[str]
    semantic_tags: list[str]
    imports: list[str]
    stems: list[str]
    parent_dir: str
    rationales: list[str]


FRAMEWORK_SIGNATURES: dict[str, FrameworkSignature] = {
    "transformers": FrameworkSignature(
        repo_url="https://github.com/huggingface/transformers",
        imports=("transformers", "AutoModelForCausalLM", "PreTrainedModel", "from_pretrained"),
        base_classes=("PreTrainedModel", "PretrainedConfig", "PreTrainedTokenizer"),
        dir_markers=("src/transformers/models",),
    ),
    "trl": FrameworkSignature(
        repo_url="https://github.com/huggingface/trl",
        imports=("trl", "DPOTrainer", "PPOTrainer", "reward_trainer"),
        base_classes=("DPOTrainer", "PPOTrainer"),
        dir_markers=("trl/trainer",),
    ),
    "triton": FrameworkSignature(
        repo_url="https://github.com/openai/triton",
        imports=("triton", "triton.language", "triton.jit"),
        base_classes=(),
        dir_markers=("python/triton",),
    ),
    "fairseq": FrameworkSignature(
        repo_url="https://github.com/facebookresearch/fairseq",
        imports=("fairseq", "register_model", "register_task", "FairseqCriterion"),
        base_classes=("BaseFairseqModel", "FairseqCriterion", "FairseqTask"),
        dir_markers=("fairseq_cli", "criterions"),
    ),
}

DEPENDENCY_REPO_MAP: dict[str, str] = {
    "transformers": "https://github.com/huggingface/transformers",
    "trl": "https://github.com/huggingface/trl",
    "triton": "https://github.com/openai/triton",
    "fairseq": "https://github.com/facebookresearch/fairseq",
    "peft": "https://github.com/huggingface/peft",
    "accelerate": "https://github.com/huggingface/accelerate",
}


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


def read_repo_file(repo_root: Path, relative_path: str) -> str | None:
    file_path = repo_root / relative_path
    if not file_path.is_file():
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def load_repo_supporting_files(repo_root: Path) -> dict[str, str]:
    supporting_paths = (
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "setup.py",
        ".gitmodules",
    )
    return {
        relative_path: content
        for relative_path in supporting_paths
        if (content := read_repo_file(repo_root, relative_path)) is not None
    }


def parse_dependency_names_from_supporting_files(supporting_files: dict[str, str]) -> set[str]:
    dependency_names = set(
        extract_dependency_names(
            "\n".join(
                [
                    supporting_files.get("requirements.txt", ""),
                    supporting_files.get("requirements-dev.txt", ""),
                    supporting_files.get("setup.py", ""),
                ]
            )
        )
    )
    dependency_names.update(parse_pyproject_dependencies(supporting_files.get("pyproject.toml", "")))
    return dependency_names


def list_repo_shape_tokens(snapshot: dict[str, str]) -> set[str]:
    tokens: set[str] = set()
    for relative_path in snapshot:
        path = Path(relative_path)
        if path.parts:
            tokens.add(path.parts[0].lower())
        if len(path.suffix) > 1:
            tokens.add(path.suffix.lower())
    return tokens


def git_first_commit_info(repo_root: Path, timeout_seconds: float) -> tuple[str, list[str]]:
    try:
        root_commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-list", "--max-parents=0", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        ).stdout.strip()
        if not root_commit:
            return "", []
        message = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%s", root_commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        ).stdout.strip()
        files = subprocess.run(
            ["git", "-C", str(repo_root), "show", "--pretty=", "--name-only", root_commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        ).stdout.splitlines()
        return message, [line.strip() for line in files if line.strip()]
    except (FileNotFoundError, subprocess.SubprocessError):
        return "", []


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


def extract_semantic_tags(
    relative_path: str,
    content: str,
    contributions: list[PaperContribution],
) -> list[str]:
    haystack = build_repo_file_haystack(relative_path, content)
    tags = [tag for tag, patterns in SEMANTIC_TAG_PATTERNS.items() if any(pattern in haystack for pattern in patterns)]
    for contribution in contributions:
        contribution_tokens = tokenize(contribution.title) | set(contribution.keywords)
        if contribution_tokens & tokenize(haystack):
            tags.extend(sorted(token for token in contribution_tokens if len(token) >= 4)[:2])
    return dedupe_preserving_order(tags)


def extract_local_import_targets(content: str) -> set[str]:
    targets: set[str] = set()
    for imported_name in LOCAL_IMPORT_RE.findall(content):
        parts = [part for part in imported_name.split(".") if part]
        targets.update(part.lower() for part in parts[-2:])
    return targets


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
        contributions = infer_document_contributions(case_slug, paper_document)
        if contributions:
            return ParseOutput(
                contributions=contributions,
                mode=ProcessorMode.HEURISTIC,
                warnings=[*warnings, *parser_gap_warnings(paper_document, contributions)],
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


def extract_github_repo_urls(text: str) -> list[str]:
    repo_urls: list[str] = []
    for matched_url in GITHUB_URL_RE.findall(text):
        try:
            repo_urls.append(normalize_repo_url(matched_url))
        except ValueError:
            continue
    return dedupe_preserving_order(repo_urls)


def extract_alias_repo_urls(text: str) -> list[str]:
    haystack = text.lower()
    repo_urls: list[str] = []
    for alias, repo_url in KNOWN_UPSTREAM_ALIAS_MAP.items():
        if text_contains_alias(haystack, alias):
            repo_urls.append(repo_url)
    return dedupe_preserving_order(repo_urls)


def extract_dependency_names(raw_value: str) -> list[str]:
    dependency_names: list[str] = []
    for line in raw_value.splitlines():
        normalized = line.strip()
        if not normalized or normalized.startswith("#"):
            continue
        dependency_name = re.split(r"[<>=!~\[\]\s]", normalized, maxsplit=1)[0].strip().lower()
        dependency_name = dependency_name.removeprefix("-e").strip()
        if dependency_name:
            dependency_names.append(dependency_name)
    return dependency_names


def parse_pyproject_dependencies(content: str) -> list[str]:
    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []

    dependencies: list[str] = []
    project = payload.get("project")
    if isinstance(project, dict):
        raw_dependencies = project.get("dependencies")
        if isinstance(raw_dependencies, list):
            dependencies.extend(entry for entry in raw_dependencies if isinstance(entry, str))

    tool = payload.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            poetry_dependencies = poetry.get("dependencies")
            if isinstance(poetry_dependencies, dict):
                dependencies.extend(key for key in poetry_dependencies if key != "python")
    return extract_dependency_names("\n".join(dependencies))


def build_readme_candidates(
    request: AnalysisRequest,
    readme_haystack: str,
    paper_candidates: list[BaseRepoCandidate],
    candidate_repo_urls: list[str],
) -> list[BaseRepoCandidate]:
    candidates: list[BaseRepoCandidate] = []
    declaration_match = any(pattern.search(readme_haystack) for pattern in DECLARATION_PATTERNS)

    for repo_url in extract_github_repo_urls(readme_haystack):
        if repo_url == request.repo_url:
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="readme_declaration",
                confidence=0.88 if declaration_match else 0.82,
                evidence=f"Repository README includes a direct GitHub upstream reference to {repo_url}.",
            )
        )

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


def build_code_reference_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None or settings is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
        target_snapshot = load_repo_snapshot(target_root, settings)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped code-reference analysis: {exc}"]

    snapshot_text = "\n".join(f"{relative_path}\n{content}" for relative_path, content in target_snapshot.items())
    candidate_repo_urls = extract_alias_repo_urls(snapshot_text)
    candidates = [
        BaseRepoCandidate(
            repo_url=repo_url,
            strategy="code_reference",
            confidence=0.74,
            evidence=f"Target repository imports or references the {repo_aliases(repo_url)[0]} ecosystem directly.",
        )
        for repo_url in candidate_repo_urls
        if repo_url != request.repo_url
    ]
    return dedupe_repo_candidates(candidates), []


def build_framework_signature_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None or settings is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
        target_snapshot = load_repo_snapshot(target_root, settings)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped framework-signature analysis: {exc}"]

    snapshot_text = "\n".join(
        f"{relative_path}\n{content}" for relative_path, content in target_snapshot.items()
    ).lower()
    candidates: list[BaseRepoCandidate] = []
    for framework_name, signature in FRAMEWORK_SIGNATURES.items():
        import_hits = [item for item in signature.imports if item.lower() in snapshot_text]
        base_hits = [item for item in signature.base_classes if item.lower() in snapshot_text]
        dir_hits = [
            marker
            for marker in signature.dir_markers
            if any(relative_path.startswith(marker) for relative_path in target_snapshot)
        ]
        total_hits = len(import_hits) + len(base_hits) + len(dir_hits)
        if total_hits == 0:
            continue
        evidence_parts: list[str] = []
        if import_hits:
            evidence_parts.append(f"imports/signatures: {', '.join(import_hits[:3])}")
        if base_hits:
            evidence_parts.append(f"base classes: {', '.join(base_hits[:3])}")
        if dir_hits:
            evidence_parts.append(f"dir markers: {', '.join(dir_hits[:2])}")
        candidates.append(
            BaseRepoCandidate(
                repo_url=signature.repo_url,
                strategy="framework_signature",
                confidence=round(min(0.72 + 0.05 * total_hits, 0.94), 2),
                evidence=f"Framework signature matched {framework_name} via {'; '.join(evidence_parts)}.",
            )
        )
    return dedupe_repo_candidates(candidates), []


def build_dependency_archaeology_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped dependency-archaeology analysis: {exc}"]

    supporting_files = load_repo_supporting_files(target_root)
    candidates: list[BaseRepoCandidate] = []

    direct_dependency_hits: list[str] = []
    for relative_path, content in supporting_files.items():
        for matched_url in DIRECT_DEPENDENCY_RE.findall(content):
            try:
                repo_url = normalize_repo_url(matched_url)
            except ValueError:
                continue
            direct_dependency_hits.append(repo_url)
            candidates.append(
                BaseRepoCandidate(
                    repo_url=repo_url,
                    strategy="dependency_archaeology",
                    confidence=0.93,
                    evidence=f"Dependency configuration {relative_path} directly references {repo_url}.",
                )
            )

    dependency_names = extract_dependency_names(
        "\n".join(
            [
                supporting_files.get("requirements.txt", ""),
                supporting_files.get("requirements-dev.txt", ""),
                supporting_files.get("setup.py", ""),
            ]
        )
    )
    dependency_names.extend(parse_pyproject_dependencies(supporting_files.get("pyproject.toml", "")))
    for dependency_name in dedupe_preserving_order(dependency_names):
        mapped_repo_url = DEPENDENCY_REPO_MAP.get(dependency_name)
        if mapped_repo_url is None or mapped_repo_url in direct_dependency_hits:
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=mapped_repo_url,
                strategy="dependency_archaeology",
                confidence=0.79,
                evidence=f"Dependency configuration includes the {dependency_name} framework package.",
            )
        )

    gitmodules = supporting_files.get(".gitmodules", "")
    for matched_url in DIRECT_DEPENDENCY_RE.findall(gitmodules):
        try:
            repo_url = normalize_repo_url(matched_url)
        except ValueError:
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="dependency_archaeology",
                confidence=0.91,
                evidence=f"Repository submodule configuration references {repo_url}.",
            )
        )

    return dedupe_repo_candidates(candidates), []


def build_fossil_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None or settings is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped fossil detection: {exc}"]

    message, first_commit_files = git_first_commit_info(target_root, settings.repo_clone_timeout_seconds)
    if not message and not first_commit_files:
        return [], []

    candidates: list[BaseRepoCandidate] = []
    bulk_import = len(first_commit_files) >= 8
    for repo_url in extract_github_repo_urls(message):
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="fossil_evidence",
                confidence=0.9 if bulk_import else 0.84,
                evidence=(
                    f"First commit references {repo_url} and imports {len(first_commit_files)} tracked files."
                    if bulk_import
                    else f"First commit message references {repo_url}."
                ),
            )
        )
    lowered_message = message.lower()
    for alias, repo_url in KNOWN_UPSTREAM_ALIAS_MAP.items():
        if text_contains_alias(lowered_message, alias):
            candidates.append(
                BaseRepoCandidate(
                    repo_url=repo_url,
                    strategy="fossil_evidence",
                    confidence=0.86 if bulk_import else 0.8,
                    evidence=(
                        f"First commit mentions {alias} and looks like a bulk import."
                        if bulk_import
                        else f"First commit mentions {alias}."
                    ),
                )
            )
    return dedupe_repo_candidates(candidates), []


def shape_similarity_candidate(
    target_snapshot: dict[str, str],
    target_supporting_files: dict[str, str],
    candidate_snapshot: dict[str, str],
    candidate_supporting_files: dict[str, str],
) -> tuple[float, str]:
    target_shape_tokens = list_repo_shape_tokens(target_snapshot)
    candidate_shape_tokens = list_repo_shape_tokens(candidate_snapshot)
    path_score = overlap_ratio(target_shape_tokens, candidate_shape_tokens)
    target_dependencies = parse_dependency_names_from_supporting_files(target_supporting_files)
    candidate_dependencies = parse_dependency_names_from_supporting_files(candidate_supporting_files)
    dependency_score = overlap_ratio(target_dependencies, candidate_dependencies)
    combined_score = 0.55 * path_score + 0.45 * dependency_score
    evidence = (
        f"Shape similarity found {len(target_shape_tokens & candidate_shape_tokens)} shared layout tokens and "
        f"{len(target_dependencies & candidate_dependencies)} shared dependencies."
    )
    return combined_score, evidence


def build_shape_similarity_candidates(
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
        target_supporting_files = load_repo_supporting_files(target_root)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped shape-similarity analysis: {exc}"]

    candidates: list[BaseRepoCandidate] = []
    warnings: list[str] = []
    for candidate_repo_url in candidate_repo_urls:
        if candidate_repo_url == request.repo_url:
            continue
        try:
            candidate_root = repo_mirror.prepare(candidate_repo_url)
            candidate_snapshot = load_repo_snapshot(candidate_root, settings)
            candidate_supporting_files = load_repo_supporting_files(candidate_root)
        except (RepoAccessError, KeyError) as exc:
            warnings.append(f"Repo tracer skipped shape candidate {candidate_repo_url}: {exc}")
            continue
        score, evidence = shape_similarity_candidate(
            target_snapshot,
            target_supporting_files,
            candidate_snapshot,
            candidate_supporting_files,
        )
        if score < 0.2:
            continue
        candidates.append(
            BaseRepoCandidate(
                repo_url=candidate_repo_url,
                strategy="shape_similarity",
                confidence=round(min(0.58 + score, 0.88), 2),
                evidence=evidence,
            )
        )
    return dedupe_repo_candidates(candidates), warnings


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
        fossil_candidates, fossil_warnings = build_fossil_candidates(
            request,
            self.repo_mirror,
            self.settings,
        )
        warnings.extend(fossil_warnings)
        candidates.extend(fossil_candidates)
        framework_signature_candidates, framework_signature_warnings = build_framework_signature_candidates(
            request,
            self.repo_mirror,
            self.settings,
        )
        warnings.extend(framework_signature_warnings)
        candidates.extend(framework_signature_candidates)
        dependency_candidates, dependency_warnings = build_dependency_archaeology_candidates(
            request,
            self.repo_mirror,
        )
        warnings.extend(dependency_warnings)
        candidates.extend(dependency_candidates)
        code_reference_candidates, code_reference_warnings = build_code_reference_candidates(
            request,
            self.repo_mirror,
            self.settings,
        )
        warnings.extend(code_reference_warnings)
        candidates.extend(code_reference_candidates)
        candidate_repo_urls = unique_repo_urls(
            [
                *([metadata_output.fork_parent] if metadata_output.fork_parent else []),
                *[candidate.repo_url for candidate in paper_candidates],
                *[candidate.repo_url for candidate in fossil_candidates],
                *[candidate.repo_url for candidate in framework_signature_candidates],
                *[candidate.repo_url for candidate in dependency_candidates],
                *[candidate.repo_url for candidate in code_reference_candidates],
                *extract_github_repo_urls(metadata_output.readme_text),
                *extract_github_repo_urls(metadata_output.notes),
                *extract_alias_repo_urls(metadata_output.readme_text),
                *extract_alias_repo_urls(metadata_output.notes),
                *known_upstream_repo_urls(),
            ]
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
        shape_candidates, shape_warnings = build_shape_similarity_candidates(
            request,
            self.repo_mirror,
            self.settings,
            candidate_repo_urls,
        )
        warnings.extend(shape_warnings)
        candidates.extend(shape_candidates)

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

        changed_files: list[ChangedFile] = []
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
            changed_files.append(
                ChangedFile(
                    relative_path=relative_path,
                    content=content,
                    change_type=change_type,
                    label=label,
                    rationale=rationale,
                    semantic_tags=extract_semantic_tags(relative_path, content, contributions),
                    imports=extract_local_import_targets(content),
                    parent_dir=Path(relative_path).parent.as_posix().lower(),
                    stem=Path(relative_path).stem.lower(),
                )
            )

        if not changed_files:
            return DiffOutput(
                diff_clusters=fixture.diff_clusters,
                mode=ProcessorMode.FIXTURE,
                warnings=[
                    ("Diff analyzer found no meaningful tracked-file changes and fell back to fixture diff clusters."),
                ],
            )

        cluster_states: list[ClusterState] = []
        for changed_file in changed_files:
            assigned_cluster: ClusterState | None = None
            for cluster_state in cluster_states:
                shared_tags = set(changed_file.semantic_tags) & set(cluster_state.semantic_tags)
                shared_imports = set(changed_file.imports) & set(cluster_state.stems)
                same_bucket = cluster_state.change_type == changed_file.change_type
                same_label = cluster_state.label == changed_file.label
                same_parent = cluster_state.parent_dir == changed_file.parent_dir
                if same_bucket and (shared_tags or shared_imports or (same_label and same_parent)):
                    assigned_cluster = cluster_state
                    break

            if assigned_cluster is None:
                cluster_states.append(
                    ClusterState(
                        change_type=changed_file.change_type,
                        label=changed_file.label,
                        files=[changed_file.relative_path],
                        semantic_tags=list(changed_file.semantic_tags),
                        imports=list(changed_file.imports),
                        stems=[changed_file.stem],
                        parent_dir=changed_file.parent_dir,
                        rationales=[changed_file.rationale],
                    )
                )
                continue

            assigned_cluster.files.append(changed_file.relative_path)
            assigned_cluster.semantic_tags = dedupe_preserving_order(
                [*assigned_cluster.semantic_tags, *changed_file.semantic_tags]
            )
            assigned_cluster.imports = dedupe_preserving_order([*assigned_cluster.imports, *changed_file.imports])
            assigned_cluster.stems = dedupe_preserving_order([*assigned_cluster.stems, changed_file.stem])
            assigned_cluster.rationales = dedupe_preserving_order(
                [*assigned_cluster.rationales, changed_file.rationale]
            )

        diff_clusters = []
        for index, cluster_state in enumerate(cluster_states, start=1):
            files = sorted(cluster_state.files)
            semantic_tags = list(cluster_state.semantic_tags)
            rationale = "; ".join(cluster_state.rationales[:2])
            summary = summarize_cluster(
                cluster_state.label,
                files,
                cluster_state.change_type,
                rationale,
            )
            if semantic_tags:
                summary = f"{summary} Semantic tags: {', '.join(semantic_tags[:4])}."
            diff_clusters.append(
                DiffCluster(
                    id=f"D{index}",
                    label=cluster_state.label,
                    change_type=cluster_state.change_type,
                    files=files,
                    summary=summary,
                    semantic_tags=semantic_tags,
                )
            )

        for diff_cluster in diff_clusters:
            diff_cluster.related_cluster_ids = [
                other.id
                for other in diff_clusters
                if other.id != diff_cluster.id and set(diff_cluster.semantic_tags) & set(other.semantic_tags)
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
            primary=SourceAwarePaperSourceFetcher(
                arxiv_fetcher=ArxivPaperSourceFetcher(settings),
                pdf_fetcher=PdfPaperSourceFetcher(settings),
            ),
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
