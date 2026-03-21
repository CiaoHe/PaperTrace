from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx

from papertrace_core.cases import default_case_examples, detect_case_slug
from papertrace_core.fixtures import (
    load_golden_case,
)
from papertrace_core.heuristics import (
    collect_unmatched_ids,
    infer_document_contributions,
    infer_mappings,
    is_comparable_code_anchor,
    is_weak_mapping,
    merge_contribution_sets,
    order_cluster_files_for_review,
    parser_gap_warnings,
    select_learning_entry_point,
    tokenize,
    trace_contribution_anchors,
    trace_contribution_steps,
)
from papertrace_core.inputs import detect_paper_source_kind, extract_arxiv_id, normalize_repo_url
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
    StageProgressCallback,
    TraceOutput,
)
from papertrace_core.llm import LLMClient, build_llm_client
from papertrace_core.models import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    ContributionMapping,
    CoverageType,
    DiffChangeType,
    DiffCluster,
    DiffCodeAnchor,
    JobStage,
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
    "readme_base_declaration": 5,
    "readme_declaration": 4,
    "framework_signature": 4,
    "fossil_evidence": 4,
    "metadata_url": 4,
    "paper_mention": 3,
    "dependency_archaeology": 3,
    "citation_graph": 3,
    "author_graph": 3,
    "temporal_topic_search": 3,
    "github_code_search": 3,
    "shape_similarity": 3,
    "llm_reasoning": 2,
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
BASE_CLASS_RE = re.compile(r"class\s+[A-Za-z_][A-Za-z0-9_]*\((?P<bases>[^)]*)\)")
FINGERPRINT_SCORE_THRESHOLD = 0.12
DIRECT_DEPENDENCY_RE = re.compile(
    r"(?:git\+)?(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\.git)?(?:@[A-Za-z0-9_.-]+)?"
)
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
GENERIC_SYMBOL_NAMES = {
    "train",
    "forward",
    "main",
    "setup",
    "run",
    "build",
    "load",
    "save",
    "evaluate",
}
TOPIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "attention",
    "for",
    "from",
    "into",
    "language",
    "large",
    "learning",
    "models",
    "model",
    "of",
    "on",
    "paper",
    "system",
    "the",
    "to",
    "using",
    "with",
}
AUTHOR_STOPWORDS = {"jr", "sr", "ii", "iii", "iv", "dr", "prof"}
GENERIC_CASE_SLUG = "custom"
URL_TRAILING_PUNCTUATION = ").,;:!?]}>\"'"
LOCAL_SIGNAL_STRATEGIES = {
    "github_fork",
    "readme_declaration",
    "framework_signature",
    "fossil_evidence",
    "metadata_url",
    "dependency_archaeology",
    "code_reference",
    "code_fingerprint",
    "shape_similarity",
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
    base_relative_path: str | None
    base_content: str | None
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
    link_reasons: list[str]


@dataclass(frozen=True)
class CandidateDiffPreview:
    repo_url: str
    comparable_anchor_count: int
    comparable_file_count: int
    raw_anchor_count: int
    changed_file_count: int
    modified_file_count: int
    new_file_count: int

    @property
    def score(self) -> tuple[int, int, int, int, int]:
        return (
            self.comparable_anchor_count,
            self.comparable_file_count,
            self.modified_file_count,
            self.raw_anchor_count,
            -self.new_file_count,
        )


FRAMEWORK_SIGNATURES: dict[str, FrameworkSignature] = {
    "transformers": FrameworkSignature(
        repo_url="https://github.com/huggingface/transformers",
        imports=("transformers", "AutoModelForCausalLM", "PreTrainedModel", "from_pretrained"),
        base_classes=("PreTrainedModel", "PretrainedConfig", "PreTrainedTokenizer"),
        dir_markers=("src/transformers/models",),
    ),
    "trl": FrameworkSignature(
        repo_url="https://github.com/huggingface/trl",
        imports=(
            "trl",
            "DPOTrainer",
            "PPOTrainer",
            "SFTTrainer",
            "GRPOTrainer",
            "GRPOConfig",
            "TrlParser",
            "reward_trainer",
            "GOLDConfig",
        ),
        base_classes=("DPOTrainer", "PPOTrainer", "SFTTrainer", "GRPOTrainer"),
        dir_markers=("trl/trainer", "trl/experimental/gold"),
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


def dedupe_code_anchors(anchors: list[DiffCodeAnchor]) -> list[DiffCodeAnchor]:
    by_key: dict[tuple[str, int, int, str], DiffCodeAnchor] = {}
    for anchor in anchors:
        key = (anchor.file_path, anchor.start_line, anchor.end_line, anchor.anchor_kind)
        by_key.setdefault(key, anchor)
    return list(by_key.values())


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
        "CITATION.cff",
        "CITATION.bib",
        ".gitmodules",
        ".git/config",
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


def infer_anchor_reason(
    snippet: str,
    semantic_tags: list[str],
    contributions: list[PaperContribution],
    rationale: str,
) -> str:
    lowered_snippet = snippet.lower()
    matched_tags = [tag for tag in semantic_tags if tag in lowered_snippet]
    matched_contributions = [
        contribution.title
        for contribution in contributions
        if any(keyword.lower() in lowered_snippet for keyword in contribution.keywords[:3])
    ]
    evidence_parts: list[str] = []
    if matched_tags:
        evidence_parts.append(f"matched semantic tags {', '.join(matched_tags[:3])}")
    if matched_contributions:
        evidence_parts.append(f"aligned with {', '.join(matched_contributions[:2])}")
    evidence_parts.append(rationale)
    return "; ".join(evidence_parts)


def anchor_kind_from_opcode(opcode: str) -> str:
    if opcode == "insert":
        return "addition"
    if opcode == "delete":
        return "deletion"
    return "modification"


def stable_patch_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


def build_file_code_anchors(
    relative_path: str,
    base_relative_path: str | None,
    base_content: str | None,
    target_content: str,
    semantic_tags: list[str],
    contributions: list[PaperContribution],
    rationale: str,
) -> list[DiffCodeAnchor]:
    context_radius = 3
    max_lines = 24
    base_lines = (base_content or "").splitlines()
    target_lines = target_content.splitlines()
    matcher = difflib.SequenceMatcher(a=base_lines, b=target_lines)
    anchors: list[DiffCodeAnchor] = []
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            continue
        if opcode == "delete":
            continue
        target_start = max(j1 - context_radius, 0)
        target_end = min(j2 + context_radius, len(target_lines))
        base_start = max(i1 - context_radius, 0)
        base_window_end = i2 if i2 > i1 else i1
        base_end = min(base_window_end + context_radius, len(base_lines))
        target_window = target_lines[target_start:target_end][:max_lines]
        base_window = base_lines[base_start:base_end][:max_lines]
        snippet = "\n".join(target_window).strip()
        original_snippet = "\n".join(base_window).strip() or None
        if not snippet:
            continue
        if original_snippet and snippet == original_snippet:
            continue
        start_line = target_start + 1
        end_line = target_start + len(target_window)
        original_start_line = base_start + 1 if original_snippet else None
        original_end_line = base_start + len(base_window) if original_snippet else None
        anchors.append(
            DiffCodeAnchor(
                patch_id=stable_patch_id(
                    relative_path,
                    base_relative_path or "",
                    str(start_line),
                    str(end_line),
                    str(original_start_line or 0),
                    str(original_end_line or 0),
                    snippet,
                    original_snippet or "",
                    opcode,
                ),
                file_path=relative_path,
                original_file_path=(base_relative_path or relative_path) if original_snippet else None,
                start_line=start_line,
                end_line=end_line,
                original_start_line=original_start_line,
                original_end_line=original_end_line,
                snippet=snippet,
                original_snippet=original_snippet,
                reason=infer_anchor_reason(snippet, semantic_tags, contributions, rationale),
                anchor_kind=anchor_kind_from_opcode(opcode),
            )
        )
        if len(anchors) >= 3:
            break
    if anchors:
        return anchors
    fallback_lines = target_lines[: min(len(target_lines), 8)]
    if not fallback_lines:
        return []
    return [
        DiffCodeAnchor(
            patch_id=stable_patch_id(
                relative_path,
                base_relative_path or "",
                "1",
                str(len(fallback_lines)),
                "1" if base_lines else "0",
                str(min(len(base_lines), len(fallback_lines)) if base_lines else 0),
                "\n".join(fallback_lines),
                "\n".join(base_lines[: min(len(base_lines), 8)]).strip() if base_lines else "",
                "context",
            ),
            file_path=relative_path,
            original_file_path=(base_relative_path or relative_path) if base_lines else None,
            start_line=1,
            end_line=len(fallback_lines),
            original_start_line=1 if base_lines else None,
            original_end_line=min(len(base_lines), len(fallback_lines)) if base_lines else None,
            snippet="\n".join(fallback_lines),
            original_snippet="\n".join(base_lines[: min(len(base_lines), 8)]).strip() or None,
            reason=rationale,
            anchor_kind="context",
        )
    ]


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


def extract_import_modules(content: str) -> set[str]:
    return {imported_name.strip() for imported_name in IMPORT_RE.findall(content) if imported_name.strip()}


def extract_base_class_names(content: str) -> set[str]:
    base_class_names: set[str] = set()
    for match in BASE_CLASS_RE.finditer(content):
        for raw_base in match.group("bases").split(","):
            base_name = raw_base.strip().split("[", 1)[0].split(".", 1)[-1]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", base_name):
                base_class_names.add(base_name)
    return base_class_names


def path_signature_tokens(relative_path: str) -> set[str]:
    path = Path(relative_path)
    tokens = {part.lower() for part in path.parts if len(part) >= 3}
    if len(path.stem) >= 3:
        tokens.add(path.stem.lower())
    suffix = path.suffix.lower()
    if len(suffix) > 1:
        tokens.add(suffix)
    return tokens


def import_module_path_candidates(import_modules: set[str]) -> list[str]:
    candidates: list[str] = []
    for import_name in sorted(import_modules):
        module_path = import_name.replace(".", "/")
        candidates.extend([f"{module_path}.py", f"{module_path}/__init__.py"])
    return candidates


def build_base_symbol_index(snapshot: dict[str, str]) -> dict[str, list[str]]:
    symbol_index: dict[str, list[str]] = {}
    for relative_path, content in snapshot.items():
        for symbol_name in SYMBOL_RE.findall(content):
            symbol_index.setdefault(symbol_name, []).append(relative_path)
    return symbol_index


def choose_base_file_match(
    target_relative_path: str,
    target_content: str,
    base_snapshot: dict[str, str],
    semantic_tags: list[str],
) -> tuple[str | None, str | None]:
    if target_relative_path in base_snapshot:
        return target_relative_path, base_snapshot[target_relative_path]

    import_modules = extract_import_modules(target_content)
    import_path_candidates = import_module_path_candidates(import_modules)
    for candidate_path in import_path_candidates:
        candidate_content = base_snapshot.get(candidate_path)
        if candidate_content is not None:
            return candidate_path, candidate_content

    base_symbol_index = build_base_symbol_index(base_snapshot)
    base_class_names = extract_base_class_names(target_content)
    for base_class_name in sorted(base_class_names):
        matched_paths = base_symbol_index.get(base_class_name, [])
        if len(matched_paths) == 1:
            matched_path = matched_paths[0]
            return matched_path, base_snapshot.get(matched_path)

    target_tokens = path_signature_tokens(target_relative_path)
    target_tokens.update(token.lower() for token in semantic_tags)
    target_tokens.update(part.lower() for part in extract_local_import_targets(target_content))
    symbol_tokens = {symbol.lower() for symbol in SYMBOL_RE.findall(target_content)}
    target_suffix = Path(target_relative_path).suffix.lower()

    best_match_path: str | None = None
    best_match_score = 0
    for base_relative_path, base_content in base_snapshot.items():
        if target_suffix and Path(base_relative_path).suffix.lower() != target_suffix:
            continue
        base_tokens = path_signature_tokens(base_relative_path)
        score = 0
        if Path(base_relative_path).stem.lower() == Path(target_relative_path).stem.lower():
            score += 6
        score += 2 * len(target_tokens & base_tokens)
        base_content_lower = base_content.lower()
        score += sum(3 for token in semantic_tags if token and token.lower() in base_content_lower)
        score += sum(2 for token in extract_local_import_targets(target_content) if token in base_content_lower)
        score += sum(1 for token in symbol_tokens if token in base_content_lower)
        if score > best_match_score:
            best_match_score = score
            best_match_path = base_relative_path

    if best_match_path is None or best_match_score < 5:
        return None, None
    return best_match_path, base_snapshot.get(best_match_path)


def extract_signature_queries(snapshot: dict[str, str]) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    for relative_path, content in snapshot.items():
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("def "):
                name = stripped.removeprefix("def ").split("(", 1)[0].strip()
            elif stripped.startswith("class "):
                name = stripped.removeprefix("class ").split("(", 1)[0].split(":", 1)[0].strip()
            else:
                continue
            lowered_name = name.lower()
            if len(lowered_name) < 8 or lowered_name in GENERIC_SYMBOL_NAMES:
                continue
            queries.append((f'"{name}" language:Python', f"{relative_path}:{name}"))
    return list(dict.fromkeys(queries))[:4]


def metadata_url_confidence(relative_path: str) -> float:
    lowered = relative_path.lower()
    if lowered == ".git/config":
        return 0.95
    if lowered in {"citation.cff", "citation.bib"}:
        return 0.9
    if lowered in {"pyproject.toml", "setup.py"}:
        return 0.86
    return 0.82


@dataclass(frozen=True)
class HeuristicPaperParser:
    llm_client: LLMClient | None = None

    def parse(
        self,
        request: AnalysisRequest,
        paper_document: PaperDocument,
        *,
        progress: StageProgressCallback | None = None,
    ) -> ParseOutput:
        case_slug = detect_case_slug(request)
        warnings: list[str] = []
        heuristic_contributions = infer_document_contributions(case_slug or "", paper_document)
        if progress is not None:
            progress(JobStage.PAPER_PARSE, 0.1, "Classifying paper sections and extraction lanes.")
        if self.llm_client is not None:
            try:
                if progress is not None:
                    progress(JobStage.PAPER_PARSE, 0.35, "Requesting structured contributions from the LLM parser.")
                llm_contributions = self.llm_client.extract_contributions(paper_document)
                if llm_contributions:
                    merged_contributions = (
                        merge_contribution_sets(llm_contributions, heuristic_contributions)
                        if heuristic_contributions
                        else llm_contributions
                    )
                    llm_warnings = [*warnings, *parser_gap_warnings(paper_document, merged_contributions)]
                    if progress is not None:
                        progress(
                            JobStage.PAPER_PARSE,
                            1.0,
                            f"Structured {len(merged_contributions)} contributions with the LLM parser.",
                        )
                    return ParseOutput(
                        contributions=merged_contributions,
                        mode=ProcessorMode.LLM,
                        warnings=llm_warnings,
                    )
                warnings.append("Paper parser received an empty llm response and fell back.")
            except Exception:
                warnings.append("Paper parser fell back from llm to heuristic extraction.")
        if progress is not None:
            progress(JobStage.PAPER_PARSE, 0.7, "Running heuristic contribution extraction.")
        if heuristic_contributions:
            if progress is not None:
                progress(
                    JobStage.PAPER_PARSE,
                    1.0,
                    f"Extracted {len(heuristic_contributions)} contributions via heuristic parsing.",
                )
            return ParseOutput(
                contributions=heuristic_contributions,
                mode=ProcessorMode.HEURISTIC,
                warnings=[*warnings, *parser_gap_warnings(paper_document, heuristic_contributions)],
            )
        if case_slug is None:
            if progress is not None:
                progress(JobStage.PAPER_PARSE, 1.0, "No structured contributions could be extracted from this paper.")
            return ParseOutput(
                contributions=[],
                mode=ProcessorMode.HEURISTIC,
                warnings=[
                    *warnings,
                    *parser_gap_warnings(paper_document, []),
                    "Paper parser could not extract structured contributions and had no matching golden fixture.",
                ],
            )
        fixture = load_golden_case(case_slug)
        if progress is not None:
            progress(JobStage.PAPER_PARSE, 1.0, "Heuristic parser failed; falling back to fixture contributions.")
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


def format_candidate_preview(preview: CandidateDiffPreview) -> str:
    return (
        "Lineage preview found "
        f"{preview.comparable_anchor_count} comparable hunks across {preview.comparable_file_count} overlapping files; "
        f"{preview.changed_file_count} changed files total ({preview.modified_file_count} modified, "
        f"{preview.new_file_count} new-only)."
    )


def preview_repo_candidate_diff(
    request: AnalysisRequest,
    candidate: BaseRepoCandidate,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
    contributions: list[PaperContribution],
) -> tuple[CandidateDiffPreview | None, str | None]:
    if repo_mirror is None or settings is None:
        return None, None

    try:
        target_root = repo_mirror.prepare(request.repo_url)
        base_root = repo_mirror.prepare(candidate.repo_url)
        target_snapshot = load_repo_snapshot(target_root, settings)
        base_snapshot = load_repo_snapshot(base_root, settings)
    except (RepoAccessError, KeyError) as exc:
        return None, f"Repo tracer skipped lineage preview for {candidate.repo_url}: {exc}"

    comparable_anchor_total = 0
    comparable_file_total = 0
    raw_anchor_total = 0
    changed_file_total = 0
    modified_file_total = 0
    new_file_total = 0
    for relative_path, content in target_snapshot.items():
        base_relative_path, base_content = choose_base_file_match(
            relative_path,
            content,
            base_snapshot,
            extract_semantic_tags(relative_path, content, contributions),
        )
        if base_content == content:
            continue
        changed_file_total += 1
        if base_relative_path is None:
            new_file_total += 1
        else:
            modified_file_total += 1
        anchors = build_file_code_anchors(
            relative_path,
            base_relative_path,
            base_content,
            content,
            extract_semantic_tags(relative_path, content, contributions),
            contributions,
            "lineage preview",
        )
        raw_anchor_total += len(anchors)
        comparable_anchors = [anchor for anchor in anchors if is_comparable_code_anchor(anchor)]
        if comparable_anchors:
            comparable_file_total += 1
            comparable_anchor_total += len(comparable_anchors)

    return (
        CandidateDiffPreview(
            repo_url=candidate.repo_url,
            comparable_anchor_count=comparable_anchor_total,
            comparable_file_count=comparable_file_total,
            raw_anchor_count=raw_anchor_total,
            changed_file_count=changed_file_total,
            modified_file_count=modified_file_total,
            new_file_count=new_file_total,
        ),
        None,
    )


def rerank_repo_candidates_with_preview(
    request: AnalysisRequest,
    candidates: list[BaseRepoCandidate],
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
    contributions: list[PaperContribution],
) -> tuple[list[BaseRepoCandidate], dict[str, CandidateDiffPreview], list[str]]:
    if repo_mirror is None or settings is None or not candidates:
        return candidates, {}, []

    preview_warnings: list[str] = []
    preview_by_repo_url: dict[str, CandidateDiffPreview] = {}
    top_k = max(min(getattr(settings, "repo_lineage_preview_top_k", 4), len(candidates)), 0)
    preview_candidates = candidates[:top_k]
    preview_candidates.extend(candidate for candidate in candidates if candidate.strategy == "llm_reasoning")
    seen_repo_urls: set[str] = set()
    for candidate in preview_candidates:
        if candidate.repo_url in seen_repo_urls:
            continue
        seen_repo_urls.add(candidate.repo_url)
        preview, warning = preview_repo_candidate_diff(
            request,
            candidate,
            repo_mirror,
            settings,
            contributions,
        )
        if warning:
            preview_warnings.append(warning)
        if preview is None:
            continue
        preview_by_repo_url[candidate.repo_url] = preview

    def candidate_sort_key(candidate: BaseRepoCandidate) -> tuple[int, int, int, int, int, int, int, int, float, str]:
        preview = preview_by_repo_url.get(candidate.repo_url)
        explicit_base_selected = int(candidate.strategy == "readme_base_declaration")
        if preview is None:
            return (
                0,
                explicit_base_selected,
                0,
                0,
                0,
                0,
                0,
                STRATEGY_PRIORITY.get(candidate.strategy, 0),
                candidate.confidence,
                candidate.repo_url,
            )
        comparable_selected = int(preview.comparable_anchor_count > 0)
        return (
            comparable_selected,
            explicit_base_selected,
            *preview.score,
            STRATEGY_PRIORITY.get(candidate.strategy, 0),
            candidate.confidence,
            candidate.repo_url,
        )

    reranked = sorted(candidates, key=candidate_sort_key, reverse=True)
    updated_candidates: list[BaseRepoCandidate] = []
    for candidate in reranked:
        preview = preview_by_repo_url.get(candidate.repo_url)
        if preview is None:
            updated_candidates.append(candidate)
            continue
        updated_candidates.append(
            candidate.model_copy(update={"evidence": f"{candidate.evidence} {format_candidate_preview(preview)}"})
        )
    return updated_candidates, preview_by_repo_url, preview_warnings


def unique_repo_urls(repo_urls: list[str]) -> list[str]:
    return list(dict.fromkeys(repo_urls))


def has_strong_local_ancestry_signal(candidates: list[BaseRepoCandidate]) -> bool:
    return any(
        candidate.strategy in LOCAL_SIGNAL_STRATEGIES and candidate.confidence >= 0.82 for candidate in candidates
    )


def build_generic_result_summary(
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    selected_base_repo: BaseRepoCandidate,
) -> str:
    if contributions:
        return (
            f"{paper_document.title} traces to {selected_base_repo.repo_url} with "
            f"{len(contributions)} extracted contribution hypotheses."
        )
    return (
        f"{paper_document.title} traces to {selected_base_repo.repo_url} with no structured contribution summary yet."
    )


def github_request_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "PaperTrace",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"token {settings.github_token}"
    return headers


def infer_target_repo_from_cases(
    request: AnalysisRequest,
    paper_document: PaperDocument,
) -> str | None:
    haystack = " ".join(
        [
            request.paper_source,
            paper_document.source_ref,
            paper_document.title,
        ]
    ).lower()
    for golden_case in default_case_examples():
        if any(alias in haystack for alias in golden_case.aliases):
            return normalize_repo_url(golden_case.repo_url)
    return None


def infer_target_repo_from_paper_mentions(
    request: AnalysisRequest,
    paper_document: PaperDocument,
) -> str | None:
    haystack = "\n".join(
        [
            request.paper_source,
            paper_document.source_ref,
            paper_document.title,
            paper_document.abstract,
            paper_document.text,
            *[section.text for section in paper_document.sections],
        ]
    )
    repo_urls = extract_github_repo_urls(haystack)
    return repo_urls[0] if repo_urls else None


def infer_target_repo_from_llm(
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    llm_client: LLMClient | None,
) -> tuple[str | None, list[str]]:
    if llm_client is None or not hasattr(llm_client, "extract_target_repos"):
        return None, []
    try:
        candidates = llm_client.extract_target_repos(paper_document, contributions)
    except Exception:
        return None, ["Target repo resolution skipped llm extraction after an llm error."]
    if not candidates:
        return None, []
    selected_candidate = candidates[0]
    return (
        selected_candidate.repo_url,
        [f"LLM target-repo extraction selected {selected_candidate.repo_url}: {selected_candidate.evidence}"],
    )


def infer_target_repo_from_remote_search(
    request: AnalysisRequest,
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    settings: Settings | None,
) -> tuple[str | None, list[str]]:
    if settings is None:
        return None, []

    placeholder_request = AnalysisRequest(
        paper_source=request.paper_source,
        repo_url="https://github.com/papertrace/unknown-target",
    )
    warnings: list[str] = []
    candidates: list[BaseRepoCandidate] = []
    citation_candidates, citation_warnings = build_citation_graph_candidates(
        placeholder_request,
        paper_document,
        settings,
    )
    warnings.extend(citation_warnings)
    candidates.extend(citation_candidates)
    author_candidates, author_warnings = build_author_graph_candidates(
        placeholder_request,
        paper_document,
        contributions,
        settings,
    )
    warnings.extend(author_warnings)
    candidates.extend(author_candidates)
    topic_candidates, topic_warnings = build_temporal_topic_candidates(
        placeholder_request,
        paper_document,
        contributions,
        settings,
    )
    warnings.extend(topic_warnings)
    candidates.extend(topic_candidates)
    deduped = dedupe_repo_candidates(candidates)
    return (deduped[0].repo_url if deduped else None), warnings


def resolve_target_repo_url(
    request: AnalysisRequest,
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    settings: Settings | None,
    llm_client: LLMClient | None = None,
) -> tuple[str, list[str]]:
    if request.repo_url.strip():
        return normalize_repo_url(request.repo_url), []

    warnings: list[str] = []
    paper_repo_url = infer_target_repo_from_paper_mentions(request, paper_document)
    if paper_repo_url is not None:
        warnings.append(f"Resolved target repository from GitHub URL mentioned in the paper: {paper_repo_url}.")
        return paper_repo_url, warnings

    llm_repo_url, llm_warnings = infer_target_repo_from_llm(
        paper_document,
        contributions,
        llm_client,
    )
    warnings.extend(llm_warnings)
    if llm_repo_url is not None:
        warnings.append(f"Resolved target repository from llm paper analysis: {llm_repo_url}.")
        return llm_repo_url, warnings

    case_repo_url = infer_target_repo_from_cases(request, paper_document)
    if case_repo_url is not None:
        warnings.append(f"Resolved target repository from known paper case: {case_repo_url}.")
        return case_repo_url, warnings

    remote_repo_url, remote_warnings = infer_target_repo_from_remote_search(
        request,
        paper_document,
        contributions,
        settings,
    )
    warnings.extend(remote_warnings)
    if remote_repo_url is not None:
        warnings.append(f"Resolved target repository from remote paper-to-repo search: {remote_repo_url}.")
        return remote_repo_url, warnings

    raise ValueError(
        "Could not infer a GitHub repository from the paper source. "
        "Provide repo_url explicitly or use a paper with a discoverable implementation repository."
    )


def inferred_paper_timestamp(request: AnalysisRequest) -> datetime | None:
    matched = re.search(r"(\d{2})(\d{2})\.\d{4,5}", request.paper_source)
    if matched is None:
        return None
    year = 2000 + int(matched.group(1))
    month = int(matched.group(2))
    if month < 1 or month > 12:
        return None
    return datetime(year=year, month=month, day=1, tzinfo=UTC)


def known_upstream_repo_urls() -> list[str]:
    return unique_repo_urls(list(KNOWN_UPSTREAM_ALIAS_MAP.values()))


def text_contains_alias(haystack: str, alias: str) -> bool:
    if "/" in alias:
        return alias in haystack
    return re.search(rf"\b{re.escape(alias)}\b", haystack) is not None


def readme_declares_base_relationship(readme_haystack: str, aliases: tuple[str, ...]) -> bool:
    declaration_markers = (
        "based on",
        "built on",
        "builds on",
        "as a base",
        "as the base",
        "extends",
        "derived from",
    )
    for snippet in readme_haystack.splitlines():
        normalized_snippet = snippet.strip().lower()
        if not normalized_snippet:
            continue
        if not any(alias in normalized_snippet for alias in aliases):
            continue
        if any(marker in normalized_snippet for marker in declaration_markers):
            return True
    return False


def build_paper_mention_candidates(
    paper_document: PaperDocument,
    *,
    exclude_repo_urls: tuple[str, ...] = (),
) -> list[BaseRepoCandidate]:
    haystack = paper_document.text.lower()
    candidates: list[BaseRepoCandidate] = []
    excluded = {repo_url.lower() for repo_url in exclude_repo_urls}

    for repo_url in extract_github_repo_urls(paper_document.text):
        if repo_url.lower() in excluded:
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


def build_temporal_topic_queries(
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
) -> list[str]:
    query_seeds = [paper_document.title, *(contribution.title for contribution in contributions[:4])]
    if paper_document.abstract.strip():
        query_seeds.append(paper_document.abstract)

    phrases: list[str] = []
    for seed in query_seeds:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+-]{1,}", seed)
            if token.lower() not in TOPIC_STOPWORDS and (len(token) >= 3 or any(char.isdigit() for char in token))
        ]
        if len(tokens) < 2:
            continue
        phrases.append(" ".join(tokens[:4]))
    return dedupe_preserving_order(phrases)[:4]


def extract_author_surnames(authors: list[str]) -> list[str]:
    surnames: list[str] = []
    for author in authors:
        parts = [part.strip(" ,.") for part in author.split() if part.strip(" ,.")]
        if not parts:
            continue
        surname = parts[-1].lower()
        if len(surname) < 2 or surname in AUTHOR_STOPWORDS:
            continue
        surnames.append(surname)
    return dedupe_preserving_order(surnames)[:4]


def build_citation_graph_queries(paper_document: PaperDocument, request: AnalysisRequest) -> list[str]:
    queries: list[str] = []
    arxiv_id = extract_arxiv_id(request.paper_source)
    if arxiv_id is not None:
        queries.append(f"arxiv {arxiv_id}")
        queries.append(arxiv_id)
    title_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+-]{2,}", paper_document.title)
        if token.lower() not in TOPIC_STOPWORDS
    ]
    if len(title_tokens) >= 2:
        queries.append(" ".join(title_tokens[:5]))
    return dedupe_preserving_order(queries)[:4]


def build_author_graph_queries(
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
) -> list[str]:
    surnames = extract_author_surnames(paper_document.authors)
    if not surnames:
        return []
    topic_queries = build_temporal_topic_queries(paper_document, contributions) or [paper_document.title]
    queries: list[str] = []
    for surname in surnames:
        for topic_query in topic_queries[:2]:
            queries.append(f"{surname} {topic_query}")
    return dedupe_preserving_order(queries)[:4]


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
        aliases = repo_aliases(repo_url)
        explicit_base_match = readme_declares_base_relationship(readme_haystack, aliases)
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="readme_base_declaration" if explicit_base_match else "readme_declaration",
                confidence=0.99 if explicit_base_match else (0.88 if declaration_match else 0.82),
                evidence=f"Repository README includes a direct GitHub upstream reference to {repo_url}.",
            )
        )

    for candidate in paper_candidates:
        aliases = repo_aliases(candidate.repo_url)
        explicit_base_match = readme_declares_base_relationship(readme_haystack, aliases)
        if candidate.repo_url.lower() in readme_haystack or any(alias in readme_haystack for alias in aliases):
            evidence = (
                f"Repository README declares an explicit upstream base relationship with {candidate.repo_url}."
                if explicit_base_match
                else (
                    f"Repository README declares an upstream relationship with {candidate.repo_url}."
                    if declaration_match
                    else f"Repository README references {candidate.repo_url}."
                )
            )
            confidence = (
                max(candidate.confidence, 0.99) if explicit_base_match else min(candidate.confidence + 0.02, 0.98)
            )
            candidates.append(
                BaseRepoCandidate(
                    repo_url=candidate.repo_url,
                    strategy="readme_base_declaration" if explicit_base_match else "readme_declaration",
                    confidence=confidence,
                    evidence=evidence,
                )
            )

    derived_readme_targets = [repo_url for repo_url in candidate_repo_urls if repo_url != request.repo_url]
    for repo_url in derived_readme_targets:
        aliases = repo_aliases(repo_url)
        explicit_base_match = readme_declares_base_relationship(readme_haystack, aliases)
        if repo_url.lower() not in readme_haystack and not any(alias in readme_haystack for alias in aliases):
            continue
        evidence = (
            f"Repository README declares {aliases[0]} as an explicit upstream base."
            if explicit_base_match
            else (
                f"Repository README references the {aliases[0]} codebase in an upstream declaration."
                if declaration_match
                else f"Repository README references the {aliases[0]} codebase."
            )
        )
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="readme_base_declaration" if explicit_base_match else "readme_declaration",
                confidence=0.99 if explicit_base_match else (0.8 if repo_url != request.repo_url else 0.76),
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
        weighted_hits = len(import_hits) + 2 * len(base_hits) + len(dir_hits)
        candidates.append(
            BaseRepoCandidate(
                repo_url=signature.repo_url,
                strategy="framework_signature",
                confidence=round(min(0.7 + 0.04 * weighted_hits, 0.97), 2),
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


def build_metadata_url_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None:
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped metadata-url analysis: {exc}"]

    supporting_files = load_repo_supporting_files(target_root)
    metadata_files = {"pyproject.toml", "setup.py", "citation.cff", "citation.bib", ".git/config"}
    candidates: list[BaseRepoCandidate] = []
    for relative_path, content in supporting_files.items():
        if relative_path.lower() not in metadata_files:
            continue
        for repo_url in extract_github_repo_urls(content):
            if repo_url == request.repo_url:
                continue
            candidates.append(
                BaseRepoCandidate(
                    repo_url=repo_url,
                    strategy="metadata_url",
                    confidence=metadata_url_confidence(relative_path),
                    evidence=f"Repository metadata file {relative_path} references {repo_url}.",
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


def build_github_code_search_candidates(
    request: AnalysisRequest,
    repo_mirror: RepoMirror | None,
    settings: Settings | None,
    client: httpx.Client | None = None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if repo_mirror is None or settings is None:
        return [], []
    if settings.github_token is None and settings.github_api_base_url == "https://api.github.com":
        return [], []

    try:
        target_root = repo_mirror.prepare(request.repo_url)
        target_snapshot = load_repo_snapshot(target_root, settings)
    except (RepoAccessError, KeyError) as exc:
        return [], [f"Repo tracer skipped GitHub code search: {exc}"]

    signature_queries = extract_signature_queries(target_snapshot)
    if not signature_queries:
        return [], []

    close_client = client is None
    http_client = client or httpx.Client(timeout=settings.github_timeout_seconds)
    headers = github_request_headers(settings)

    repo_evidence: dict[str, list[str]] = {}
    warnings: list[str] = []
    try:
        endpoint = f"{settings.github_api_base_url.rstrip('/')}/search/code"
        for query, query_label in signature_queries:
            try:
                response = http_client.get(
                    endpoint,
                    headers=headers,
                    params={"q": query, "per_page": 5},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"Repo tracer skipped code search query {query_label}: {exc}")
                continue

            for item in payload.get("items", []):
                repository = item.get("repository") or {}
                html_url = repository.get("html_url")
                if not isinstance(html_url, str):
                    continue
                try:
                    repo_url = normalize_repo_url(html_url)
                except ValueError:
                    continue
                if repo_url == request.repo_url:
                    continue
                repo_evidence.setdefault(repo_url, []).append(query_label)
    finally:
        if close_client:
            http_client.close()

    candidates = [
        BaseRepoCandidate(
            repo_url=repo_url,
            strategy="github_code_search",
            confidence=round(min(0.68 + 0.06 * len(matches), 0.9), 2),
            evidence=(f"GitHub code search matched target signatures {', '.join(list(dict.fromkeys(matches))[:3])}."),
        )
        for repo_url, matches in repo_evidence.items()
    ]
    return dedupe_repo_candidates(candidates), warnings


def build_temporal_topic_candidates(
    request: AnalysisRequest,
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    settings: Settings | None,
    client: httpx.Client | None = None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if settings is None:
        return [], []
    if settings.github_token is None and settings.github_api_base_url == "https://api.github.com":
        return [], []

    queries = build_temporal_topic_queries(paper_document, contributions)
    if not queries:
        return [], []

    close_client = client is None
    http_client = client or httpx.Client(timeout=settings.github_timeout_seconds)
    headers = github_request_headers(settings)

    paper_timestamp = inferred_paper_timestamp(request)
    repo_matches: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    try:
        endpoint = f"{settings.github_api_base_url.rstrip('/')}/search/repositories"
        for phrase in queries:
            try:
                response = http_client.get(
                    endpoint,
                    headers=headers,
                    params={
                        "q": f'"{phrase}" in:name,description,readme language:Python',
                        "per_page": 5,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"Repo tracer skipped temporal topic query {phrase}: {exc}")
                continue

            for item in payload.get("items", []):
                html_url = item.get("html_url")
                if not isinstance(html_url, str):
                    continue
                try:
                    repo_url = normalize_repo_url(html_url)
                except ValueError:
                    continue
                if repo_url == request.repo_url:
                    continue
                entry = repo_matches.setdefault(repo_url, {"phrases": [], "created_at": None})
                entry["phrases"] = dedupe_preserving_order([*cast(list[str], entry["phrases"]), phrase])
                created_at = item.get("created_at")
                if isinstance(created_at, str):
                    try:
                        entry["created_at"] = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except ValueError:
                        pass
    finally:
        if close_client:
            http_client.close()

    candidates: list[BaseRepoCandidate] = []
    for repo_url, match_data in repo_matches.items():
        phrases = cast(list[str], match_data["phrases"])
        created_at = cast(datetime | None, match_data["created_at"])
        predates_paper = paper_timestamp is not None and created_at is not None and created_at <= paper_timestamp
        confidence = min(0.62 + 0.05 * len(phrases) + (0.08 if predates_paper else 0.0), 0.9)
        evidence = (
            f"GitHub repository search matched paper topics {', '.join(phrases[:3])} and the repo predates the paper."
            if predates_paper
            else f"GitHub repository search matched paper topics {', '.join(phrases[:3])}."
        )
        candidates.append(
            BaseRepoCandidate(
                repo_url=repo_url,
                strategy="temporal_topic_search",
                confidence=round(confidence, 2),
                evidence=evidence,
            )
        )
    return dedupe_repo_candidates(candidates), warnings


def build_citation_graph_candidates(
    request: AnalysisRequest,
    paper_document: PaperDocument,
    settings: Settings | None,
    client: httpx.Client | None = None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if settings is None:
        return [], []
    if settings.github_token is None and settings.github_api_base_url == "https://api.github.com":
        return [], []

    queries = build_citation_graph_queries(paper_document, request)
    if not queries:
        return [], []

    close_client = client is None
    http_client = client or httpx.Client(timeout=settings.github_timeout_seconds)
    headers = github_request_headers(settings)

    repo_evidence: dict[str, list[str]] = {}
    warnings: list[str] = []
    try:
        endpoint = f"{settings.github_api_base_url.rstrip('/')}/search/repositories"
        for query in queries:
            try:
                response = http_client.get(
                    endpoint,
                    headers=headers,
                    params={"q": f'"{query}" in:readme,description language:Python', "per_page": 5},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"Repo tracer skipped citation graph query {query}: {exc}")
                continue

            for item in payload.get("items", []):
                html_url = item.get("html_url")
                if not isinstance(html_url, str):
                    continue
                try:
                    repo_url = normalize_repo_url(html_url)
                except ValueError:
                    continue
                if repo_url == request.repo_url:
                    continue
                repo_evidence.setdefault(repo_url, []).append(query)
    finally:
        if close_client:
            http_client.close()

    candidates = [
        BaseRepoCandidate(
            repo_url=repo_url,
            strategy="citation_graph",
            confidence=round(min(0.66 + 0.05 * len(matches), 0.9), 2),
            evidence=(
                "GitHub repository search matched paper citation signals "
                f"{', '.join(list(dict.fromkeys(matches))[:3])}."
            ),
        )
        for repo_url, matches in repo_evidence.items()
    ]
    return dedupe_repo_candidates(candidates), warnings


def build_author_graph_candidates(
    request: AnalysisRequest,
    paper_document: PaperDocument,
    contributions: list[PaperContribution],
    settings: Settings | None,
    client: httpx.Client | None = None,
) -> tuple[list[BaseRepoCandidate], list[str]]:
    if settings is None:
        return [], []
    if settings.github_token is None and settings.github_api_base_url == "https://api.github.com":
        return [], []

    queries = build_author_graph_queries(paper_document, contributions)
    if not queries:
        return [], []

    surnames = set(extract_author_surnames(paper_document.authors))
    close_client = client is None
    http_client = client or httpx.Client(timeout=settings.github_timeout_seconds)
    headers = github_request_headers(settings)

    repo_matches: dict[str, tuple[str, str | None]] = {}
    warnings: list[str] = []
    try:
        endpoint = f"{settings.github_api_base_url.rstrip('/')}/search/repositories"
        for query in queries:
            try:
                response = http_client.get(
                    endpoint,
                    headers=headers,
                    params={"q": f'"{query}" in:readme,description language:Python', "per_page": 5},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"Repo tracer skipped author graph query {query}: {exc}")
                continue

            for item in payload.get("items", []):
                html_url = item.get("html_url")
                if not isinstance(html_url, str):
                    continue
                try:
                    repo_url = normalize_repo_url(html_url)
                except ValueError:
                    continue
                if repo_url == request.repo_url:
                    continue
                owner_login = str((item.get("owner") or {}).get("login") or "").lower()
                description = str(item.get("description") or "")
                if surnames and not any(
                    surname in owner_login or surname in description.lower() for surname in surnames
                ):
                    continue
                repo_matches[repo_url] = (query, owner_login or None)
    finally:
        if close_client:
            http_client.close()

    candidates = [
        BaseRepoCandidate(
            repo_url=repo_url,
            strategy="author_graph",
            confidence=0.72,
            evidence=(
                "GitHub repository search linked paper-author surnames "
                f"to repo owner {owner_login} via query '{query}'."
                if owner_login
                else f"GitHub repository search matched paper-author surnames via query '{query}'."
            ),
        )
        for repo_url, (query, owner_login) in repo_matches.items()
    ]
    return dedupe_repo_candidates(candidates), warnings


def changed_file_link_reasons(left: ChangedFile, right: ChangedFile) -> list[str]:
    if left.change_type != right.change_type:
        return []

    reasons: list[str] = []
    shared_tags = sorted(set(left.semantic_tags) & set(right.semantic_tags))
    shared_imports = sorted(
        (set(left.imports) & set(right.imports))
        | (set(left.imports) & {right.stem})
        | ({left.stem} & set(right.imports))
    )
    if shared_tags:
        reasons.append(f"shared semantic tags: {', '.join(shared_tags[:3])}")
    if shared_imports:
        reasons.append(f"shared local symbols: {', '.join(shared_imports[:3])}")
    if left.label == right.label:
        reasons.append(f"shared contribution label: {left.label}")
    if left.parent_dir == right.parent_dir and left.parent_dir:
        reasons.append(f"shared parent dir: {left.parent_dir}")
    return reasons


def build_changed_file_components(changed_files: list[ChangedFile]) -> list[tuple[list[ChangedFile], list[str]]]:
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(changed_files))}
    edge_reasons: dict[frozenset[int], list[str]] = {}
    for left_index, left_file in enumerate(changed_files):
        for right_index in range(left_index + 1, len(changed_files)):
            right_file = changed_files[right_index]
            reasons = changed_file_link_reasons(left_file, right_file)
            if not reasons:
                continue
            adjacency[left_index].add(right_index)
            adjacency[right_index].add(left_index)
            edge_reasons[frozenset({left_index, right_index})] = reasons

    components: list[tuple[list[ChangedFile], list[str]]] = []
    visited: set[int] = set()
    for start_index in range(len(changed_files)):
        if start_index in visited:
            continue
        stack = [start_index]
        component_indices: list[int] = []
        component_reasons: list[str] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component_indices.append(current)
            for neighbor in adjacency[current]:
                component_reasons.extend(edge_reasons.get(frozenset({current, neighbor}), []))
                if neighbor not in visited:
                    stack.append(neighbor)
        components.append(
            ([changed_files[index] for index in sorted(component_indices)], dedupe_preserving_order(component_reasons))
        )
    return components


def select_component_label(files: list[ChangedFile]) -> str:
    ranked_labels = Counter(file.label for file in files)
    return sorted(
        ranked_labels,
        key=lambda label: (
            ranked_labels[label],
            label not in {"New implementation modules", "Core implementation changes"},
            label,
        ),
        reverse=True,
    )[0]


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
    github_client: httpx.Client | None = None
    llm_client: LLMClient | None = None

    def trace(
        self,
        request: AnalysisRequest,
        paper_document: PaperDocument,
        contributions: list[PaperContribution],
        *,
        progress: StageProgressCallback | None = None,
    ) -> TraceOutput:
        case_slug = detect_case_slug(request)
        golden = load_golden_case(case_slug) if case_slug is not None else None
        if progress is not None:
            progress(JobStage.REPO_FETCH, 0.1, "Fetching repository metadata and upstream hints.")
        metadata_output = self.repo_metadata_provider.fetch(request)
        warnings = list(metadata_output.warnings)
        if progress is not None:
            progress(JobStage.REPO_FETCH, 1.0, "Repository metadata loaded; ancestry tracing is ready.")
            progress(JobStage.ANCESTRY_TRACE, 0.1, "Scanning paper mentions and repository archaeology.")

        candidates: list[BaseRepoCandidate] = []
        if golden is not None and golden.selected_base_repo.repo_url == request.repo_url:
            candidates.append(
                BaseRepoCandidate(
                    repo_url=request.repo_url,
                    strategy="paper_mention",
                    confidence=0.96,
                    evidence="Paper lineage resolves to the submitted implementation repository for this golden case.",
                )
            )
        if metadata_output.fork_parent:
            candidates.append(
                BaseRepoCandidate(
                    repo_url=metadata_output.fork_parent,
                    strategy="github_fork",
                    confidence=0.99,
                    evidence="Repository metadata exposes an upstream fork parent.",
                )
            )

        paper_candidates = build_paper_mention_candidates(
            paper_document,
            exclude_repo_urls=(request.repo_url,),
        )
        candidates.extend(paper_candidates)
        if progress is not None:
            progress(
                JobStage.ANCESTRY_TRACE,
                0.35,
                f"Collected {len(candidates)} ancestry candidates after paper-mention expansion.",
            )
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
        metadata_url_candidates, metadata_url_warnings = build_metadata_url_candidates(
            request,
            self.repo_mirror,
        )
        warnings.extend(metadata_url_warnings)
        candidates.extend(metadata_url_candidates)
        dependency_candidates, dependency_warnings = build_dependency_archaeology_candidates(
            request,
            self.repo_mirror,
        )
        warnings.extend(dependency_warnings)
        candidates.extend(dependency_candidates)
        citation_graph_candidates: list[BaseRepoCandidate] = []
        author_graph_candidates: list[BaseRepoCandidate] = []
        github_code_search_candidates: list[BaseRepoCandidate] = []
        temporal_topic_candidates: list[BaseRepoCandidate] = []
        if not has_strong_local_ancestry_signal(candidates):
            citation_graph_candidates, citation_graph_warnings = build_citation_graph_candidates(
                request,
                paper_document,
                self.settings,
                client=self.github_client,
            )
            warnings.extend(citation_graph_warnings)
            candidates.extend(citation_graph_candidates)
            author_graph_candidates, author_graph_warnings = build_author_graph_candidates(
                request,
                paper_document,
                contributions,
                self.settings,
                client=self.github_client,
            )
            warnings.extend(author_graph_warnings)
            candidates.extend(author_graph_candidates)
            github_code_search_candidates, github_code_search_warnings = build_github_code_search_candidates(
                request,
                self.repo_mirror,
                self.settings,
                client=self.github_client,
            )
            warnings.extend(github_code_search_warnings)
            candidates.extend(github_code_search_candidates)
            temporal_topic_candidates, temporal_topic_warnings = build_temporal_topic_candidates(
                request,
                paper_document,
                contributions,
                self.settings,
                client=self.github_client,
            )
            warnings.extend(temporal_topic_warnings)
            candidates.extend(temporal_topic_candidates)
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
                *[candidate.repo_url for candidate in metadata_url_candidates],
                *[candidate.repo_url for candidate in dependency_candidates],
                *[candidate.repo_url for candidate in citation_graph_candidates],
                *[candidate.repo_url for candidate in author_graph_candidates],
                *[candidate.repo_url for candidate in github_code_search_candidates],
                *[candidate.repo_url for candidate in temporal_topic_candidates],
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
        if self.llm_client is not None:
            try:
                llm_candidates = self.llm_client.suggest_base_repos(
                    request_repo_url=request.repo_url,
                    paper_document=paper_document,
                    readme_text=metadata_output.readme_text,
                    notes=metadata_output.notes,
                    existing_candidates=dedupe_repo_candidates(candidates),
                )
                candidates.extend(llm_candidates)
            except Exception:
                warnings.append("Repo tracer skipped llm ancestry reasoning after heuristic candidate generation.")
        if progress is not None:
            progress(
                JobStage.ANCESTRY_TRACE,
                0.75,
                f"Expanded ancestry evidence to {len(candidates)} raw candidate hypotheses.",
            )

        if not candidates and golden is not None:
            candidates.extend(
                BaseRepoCandidate(
                    repo_url=candidate.repo_url,
                    strategy="fallback",
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                )
                for candidate in golden.base_repo_candidates
            )
        if not candidates:
            candidates.append(
                BaseRepoCandidate(
                    repo_url=request.repo_url,
                    strategy="fallback",
                    confidence=0.35,
                    evidence="No ancestry signal outranked the submitted implementation repository.",
                )
            )

        deduped = dedupe_repo_candidates(candidates)
        deduped, preview_by_repo_url, preview_warnings = rerank_repo_candidates_with_preview(
            request,
            deduped,
            self.repo_mirror,
            self.settings,
            contributions,
        )
        warnings.extend(preview_warnings)
        if self.llm_client is not None and hasattr(self.llm_client, "select_base_repo"):
            candidate_pool = [candidate for candidate in deduped if candidate.repo_url != request.repo_url][:8]
            if candidate_pool:
                try:
                    llm_selected = self.llm_client.select_base_repo(
                        request_repo_url=request.repo_url,
                        paper_document=paper_document,
                        contributions=contributions,
                        readme_text=metadata_output.readme_text,
                        notes=metadata_output.notes,
                        existing_candidates=candidate_pool,
                    )
                except Exception:
                    warnings.append("Repo tracer skipped llm base-repo selection after heuristic ranking.")
                else:
                    if llm_selected is not None:
                        selected_match = next(
                            (candidate for candidate in deduped if candidate.repo_url == llm_selected.repo_url),
                            None,
                        )
                        if selected_match is not None:
                            promoted_candidate = selected_match.model_copy(
                                update={
                                    "strategy": "llm_reasoning",
                                    "confidence": max(selected_match.confidence, llm_selected.confidence),
                                    "evidence": llm_selected.evidence,
                                }
                            )
                            deduped = [
                                promoted_candidate,
                                *[
                                    candidate
                                    for candidate in deduped
                                    if candidate.repo_url != promoted_candidate.repo_url
                                ],
                            ]
                            warnings.append(
                                f"Repo tracer promoted {promoted_candidate.repo_url} via llm base-repo selection."
                            )
        if golden is not None and golden.selected_base_repo.repo_url == request.repo_url:
            self_candidate = next((candidate for candidate in deduped if candidate.repo_url == request.repo_url), None)
            if self_candidate is not None:
                deduped = [
                    self_candidate,
                    *[candidate for candidate in deduped if candidate.repo_url != request.repo_url],
                ]
        selected_preview = preview_by_repo_url.get(deduped[0].repo_url) if deduped else None
        if (
            deduped
            and selected_preview is not None
            and selected_preview.comparable_anchor_count == 0
            and self.llm_client is not None
        ):
            preview_diagnostics = "\n".join(
                f"- {candidate.repo_url}: {format_candidate_preview(preview_by_repo_url[candidate.repo_url])}"
                for candidate in deduped
                if candidate.repo_url in preview_by_repo_url
            )
            try:
                llm_rescue_candidates = self.llm_client.suggest_base_repos(
                    request_repo_url=request.repo_url,
                    paper_document=paper_document,
                    readme_text=metadata_output.readme_text,
                    notes=(
                        f"{metadata_output.notes}\n"
                        "Lineage preview diagnostics for current top ancestry candidates:\n"
                        f"{preview_diagnostics}"
                    ),
                    existing_candidates=deduped[:8],
                )
                if llm_rescue_candidates:
                    deduped = dedupe_repo_candidates([*deduped, *llm_rescue_candidates])
                    deduped, preview_by_repo_url, preview_warnings = rerank_repo_candidates_with_preview(
                        request,
                        deduped,
                        self.repo_mirror,
                        self.settings,
                        contributions,
                    )
                    warnings.extend(preview_warnings)
                    warnings.append(
                        "Repo tracer invoked llm ancestry rescue because the initial selected "
                        "base repo produced no comparable hunks."
                    )
            except Exception:
                warnings.append("Repo tracer skipped llm ancestry rescue after zero-comparable lineage preview.")
        selected_preview = preview_by_repo_url.get(deduped[0].repo_url) if deduped else None
        if selected_preview is not None and selected_preview.comparable_anchor_count == 0:
            warnings.append(
                f"Selected base repo {deduped[0].repo_url} produced no comparable hunks during lineage preview; "
                "evidence review will be limited to weak or addition-only matches."
            )
        if progress is not None:
            progress(
                JobStage.ANCESTRY_TRACE,
                1.0,
                f"Ranked {len(deduped)} base repo candidates; selected {deduped[0].repo_url}.",
            )
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
        *,
        progress: StageProgressCallback | None = None,
    ) -> DiffOutput:
        del selected_base_repo, contributions
        case_slug = detect_case_slug(request)
        if case_slug is None:
            if progress is not None:
                progress(JobStage.DIFF_ANALYZE, 1.0, "No golden diff fixture matched; returning an empty diff result.")
            return DiffOutput(
                diff_clusters=[],
                mode=ProcessorMode.FIXTURE,
                warnings=["Diff analyzer had no matching golden fixture and returned no diff clusters."],
            )
        fixture = load_golden_case(case_slug)
        if progress is not None:
            progress(JobStage.DIFF_ANALYZE, 1.0, "Returned fixture-backed diff clusters.")
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
        *,
        progress: StageProgressCallback | None = None,
    ) -> DiffOutput:
        try:
            if progress is not None:
                progress(JobStage.DIFF_ANALYZE, 0.1, "Preparing shallow clones for base and target repositories.")
            base_root = self.repo_mirror.prepare(selected_base_repo.repo_url)
            target_root = self.repo_mirror.prepare(request.repo_url)
            base_snapshot = load_repo_snapshot(base_root, self.settings)
            target_snapshot = load_repo_snapshot(target_root, self.settings)
            if progress is not None:
                progress(JobStage.DIFF_ANALYZE, 0.4, "Loaded repository snapshots and started change discovery.")
        except RepoAccessError as exc:
            if progress is not None:
                progress(JobStage.DIFF_ANALYZE, 1.0, "Repo diff failed; returning an empty live diff result.")
            return DiffOutput(
                diff_clusters=[],
                mode=ProcessorMode.HEURISTIC,
                warnings=[
                    "Diff analyzer could not prepare live repositories and returned no diff clusters.",
                    str(exc),
                ],
            )

        changed_files: list[ChangedFile] = []
        for relative_path, content in target_snapshot.items():
            semantic_tags = extract_semantic_tags(relative_path, content, contributions)
            base_relative_path, base_content = choose_base_file_match(
                relative_path,
                content,
                base_snapshot,
                semantic_tags,
            )
            if base_content == content:
                continue
            change_type, rationale = classify_change_type(
                relative_path,
                content,
                is_new_file=base_relative_path is None,
            )
            if base_relative_path is not None and base_relative_path != relative_path:
                rationale = f"{rationale}; aligned against upstream file {base_relative_path}"
            label = select_cluster_label(relative_path, content, contributions, change_type)
            changed_files.append(
                ChangedFile(
                    relative_path=relative_path,
                    base_relative_path=base_relative_path,
                    base_content=base_content,
                    content=content,
                    change_type=change_type,
                    label=label,
                    rationale=rationale,
                    semantic_tags=semantic_tags,
                    imports=extract_local_import_targets(content),
                    parent_dir=Path(relative_path).parent.as_posix().lower(),
                    stem=Path(relative_path).stem.lower(),
                )
            )

        if not changed_files:
            if progress is not None:
                progress(JobStage.DIFF_ANALYZE, 1.0, "No meaningful live diffs found after repository filtering.")
            return DiffOutput(
                diff_clusters=[],
                mode=ProcessorMode.HEURISTIC,
                warnings=[
                    "Diff analyzer found no meaningful tracked-file changes after filtering.",
                ],
            )

        cluster_states: list[ClusterState] = []
        for component_files, component_link_reasons in build_changed_file_components(changed_files):
            lead_file = component_files[0]
            cluster_states.append(
                ClusterState(
                    change_type=lead_file.change_type,
                    label=select_component_label(component_files),
                    files=sorted(file.relative_path for file in component_files),
                    semantic_tags=dedupe_preserving_order(
                        [tag for file in component_files for tag in file.semantic_tags]
                    ),
                    imports=dedupe_preserving_order(
                        [import_name for file in component_files for import_name in file.imports]
                    ),
                    stems=dedupe_preserving_order([file.stem for file in component_files]),
                    parent_dir=lead_file.parent_dir,
                    rationales=dedupe_preserving_order([file.rationale for file in component_files]),
                    link_reasons=component_link_reasons,
                )
            )
        if progress is not None:
            progress(
                JobStage.DIFF_ANALYZE,
                0.75,
                f"Grouped {len(changed_files)} changed files into {len(cluster_states)} semantic components.",
            )

        diff_clusters = []
        for index, cluster_state in enumerate(cluster_states, start=1):
            files = cluster_state.files
            semantic_tags = list(cluster_state.semantic_tags)
            rationale = "; ".join(cluster_state.rationales[:2])
            summary = summarize_cluster(
                cluster_state.label,
                files,
                cluster_state.change_type,
                rationale,
            )
            if cluster_state.link_reasons:
                summary = f"{summary} Linked by {'; '.join(cluster_state.link_reasons[:2])}."
            if semantic_tags:
                summary = f"{summary} Semantic tags: {', '.join(semantic_tags[:4])}."
            component_changed_files = [file for file in changed_files if file.relative_path in set(files)]
            code_anchors = dedupe_code_anchors(
                [
                    anchor
                    for changed_file in component_changed_files
                    for anchor in build_file_code_anchors(
                        changed_file.relative_path,
                        changed_file.base_relative_path,
                        changed_file.base_content,
                        changed_file.content,
                        changed_file.semantic_tags,
                        contributions,
                        changed_file.rationale,
                    )
                ]
            )[:6]
            diff_clusters.append(
                DiffCluster(
                    id=f"D{index}",
                    patch_id=stable_patch_id(
                        cluster_state.label,
                        cluster_state.change_type,
                        *files,
                        *(anchor.patch_id or "" for anchor in code_anchors),
                    ),
                    label=cluster_state.label,
                    change_type=cluster_state.change_type,
                    files=files,
                    summary=summary,
                    code_anchors=code_anchors,
                    semantic_tags=semantic_tags,
                )
            )

        for diff_cluster in diff_clusters:
            diff_cluster.related_cluster_ids = [
                other.id
                for other in diff_clusters
                if other.id != diff_cluster.id and set(diff_cluster.semantic_tags) & set(other.semantic_tags)
            ]
        if progress is not None:
            progress(JobStage.DIFF_ANALYZE, 1.0, f"Built {len(diff_clusters)} diff clusters with code evidence.")
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
        *,
        progress: StageProgressCallback | None = None,
    ) -> MappingOutput:
        warnings: list[str] = []
        contribution_by_id = {contribution.id: contribution for contribution in contributions}
        diff_cluster_by_id = {diff_cluster.id: diff_cluster for diff_cluster in diff_clusters}
        if progress is not None:
            progress(JobStage.CONTRIBUTION_MAP, 0.1, "Preparing contribution-to-diff alignment.")
        if progress is not None:
            progress(JobStage.CONTRIBUTION_MAP, 0.45, "Running heuristic contribution mapping.")
        heuristic_mappings = infer_mappings(contributions, diff_clusters)
        normalized_heuristic_mappings: list[ContributionMapping] = []
        weak_heuristic_mappings: list[ContributionMapping] = []
        for mapping in heuristic_mappings:
            diff_cluster = diff_cluster_by_id.get(mapping.diff_cluster_id)
            if diff_cluster is None or not is_weak_mapping(mapping, diff_cluster):
                normalized_heuristic_mappings.append(mapping)
                continue
            if not diff_cluster.code_anchors:
                warnings.append(
                    f"Contribution mapper dropped weak mapping {mapping.diff_cluster_id}->{mapping.contribution_id} "
                    "because the diff cluster exposed no code anchors."
                )
                continue
            if diff_cluster.code_anchors and not any(
                anchor.file_path in set(diff_cluster.files) for anchor in diff_cluster.code_anchors
            ):
                warnings.append(
                    f"Contribution mapper dropped weak mapping {mapping.diff_cluster_id}->{mapping.contribution_id} "
                    "because its code anchors no longer matched the diff cluster file set."
                )
                continue
            weak_mapping = mapping.model_copy(
                update={
                    "coverage_type": CoverageType.MISSING,
                    "completeness": "missing",
                    "implementation_coverage": min(mapping.implementation_coverage, 0.1),
                    "evidence": (
                        f"{mapping.evidence} "
                        "This match is currently weak because no source-comparable hunks or "
                        "strongly grounded anchors were found."
                    ),
                }
            )
            normalized_heuristic_mappings.append(weak_mapping)
            weak_heuristic_mappings.append(weak_mapping)
        mappings = normalized_heuristic_mappings
        mode = ProcessorMode.HEURISTIC
        if weak_heuristic_mappings:
            warnings.append(
                f"Contribution mapper marked {len(weak_heuristic_mappings)} mapping(s) as weak because they lacked "
                "comparable hunks and strong anchor grounding."
            )

        if self.llm_client is not None and (
            not heuristic_mappings or len(weak_heuristic_mappings) == len(heuristic_mappings)
        ):
            try:
                if progress is not None:
                    progress(JobStage.CONTRIBUTION_MAP, 0.72, "Requesting LLM contribution mapping review.")
                llm_mappings = self.llm_client.map_contributions(contributions, diff_clusters)
                if llm_mappings:
                    heuristic_by_pair = {
                        (mapping.diff_cluster_id, mapping.contribution_id): mapping for mapping in heuristic_mappings
                    }
                    enriched_llm_mappings: list[ContributionMapping] = []
                    for llm_mapping in llm_mappings:
                        contribution = contribution_by_id.get(llm_mapping.contribution_id)
                        diff_cluster = diff_cluster_by_id.get(llm_mapping.diff_cluster_id)
                        if contribution is None or diff_cluster is None:
                            continue
                        heuristic_mapping = heuristic_by_pair.get(
                            (llm_mapping.diff_cluster_id, llm_mapping.contribution_id)
                        )
                        supported_steps, missing_steps = trace_contribution_steps(contribution, diff_cluster)
                        matched_anchors, fidelity_notes, snippet_fidelity, formula_fidelity = (
                            trace_contribution_anchors(
                                contribution,
                                diff_cluster,
                            )
                        )
                        enriched_llm_mappings.append(
                            llm_mapping.model_copy(
                                update={
                                    "implementation_coverage": (
                                        max(
                                            llm_mapping.implementation_coverage,
                                            heuristic_mapping.implementation_coverage,
                                        )
                                        if heuristic_mapping is not None
                                        else llm_mapping.implementation_coverage
                                    ),
                                    "snippet_fidelity": (
                                        max(llm_mapping.snippet_fidelity, snippet_fidelity)
                                        if llm_mapping.snippet_fidelity
                                        else snippet_fidelity
                                    ),
                                    "formula_fidelity": (
                                        max(llm_mapping.formula_fidelity, formula_fidelity)
                                        if llm_mapping.formula_fidelity
                                        else formula_fidelity
                                    ),
                                    "coverage_type": (
                                        heuristic_mapping.coverage_type
                                        if heuristic_mapping is not None
                                        else llm_mapping.coverage_type
                                    ),
                                    "missing_aspects": (
                                        heuristic_mapping.missing_aspects
                                        if heuristic_mapping is not None
                                        else (
                                            [f"untraced implementation steps: {', '.join(missing_steps[:2])}"]
                                            if missing_steps
                                            else []
                                        )
                                    ),
                                    "engineering_divergences": (
                                        heuristic_mapping.engineering_divergences
                                        if heuristic_mapping is not None
                                        else []
                                    ),
                                    "fidelity_notes": (
                                        heuristic_mapping.fidelity_notes
                                        if heuristic_mapping is not None
                                        else fidelity_notes
                                    ),
                                    "matched_anchor_patch_ids": [
                                        anchor.patch_id for anchor in matched_anchors if anchor.patch_id
                                    ],
                                    "learning_entry_point": select_learning_entry_point(contribution, diff_cluster),
                                    "reading_order": order_cluster_files_for_review(contribution, diff_cluster),
                                    "confidence": max(
                                        llm_mapping.confidence,
                                        heuristic_mapping.confidence if heuristic_mapping is not None else 0.0,
                                    ),
                                    "evidence": (
                                        f"{llm_mapping.evidence} Grounding: {heuristic_mapping.evidence}"
                                        if heuristic_mapping is not None
                                        else llm_mapping.evidence
                                    ),
                                }
                            )
                        )
                    weak_llm_mappings = [
                        mapping
                        for mapping in enriched_llm_mappings
                        if (cluster := diff_cluster_by_id.get(mapping.diff_cluster_id)) is not None
                        and is_weak_mapping(mapping, cluster)
                    ]
                    if enriched_llm_mappings and len(weak_llm_mappings) < len(enriched_llm_mappings):
                        mappings = enriched_llm_mappings
                        mode = ProcessorMode.LLM
                    elif enriched_llm_mappings and len(weak_llm_mappings) <= len(weak_heuristic_mappings):
                        mappings = enriched_llm_mappings
                        mode = ProcessorMode.LLM
                    elif not heuristic_mappings and enriched_llm_mappings:
                        mappings = enriched_llm_mappings
                        mode = ProcessorMode.LLM
                    else:
                        warnings.append(
                            "Contribution mapper kept heuristic output because llm review did not improve grounding."
                        )
                else:
                    warnings.append("Contribution mapper received an empty llm response and fell back.")
            except Exception:
                warnings.append("Contribution mapper fell back from llm review to heuristic matching.")

        unmatched_contribution_ids, unmatched_diff_cluster_ids = collect_unmatched_ids(
            contributions,
            diff_clusters,
            mappings,
        )
        if not mappings:
            warnings.append("Contribution mapper found no confident heuristic matches.")
        if progress is not None:
            progress(
                JobStage.CONTRIBUTION_MAP,
                1.0,
                (
                    f"Mapped {len(mappings)} contribution links; "
                    f"{len(unmatched_contribution_ids)} contributions remain unmatched."
                ),
            )
        return MappingOutput(
            mappings=mappings,
            unmatched_contribution_ids=unmatched_contribution_ids,
            unmatched_diff_cluster_ids=unmatched_diff_cluster_ids,
            mode=mode,
            warnings=warnings,
        )


@dataclass(frozen=True)
class AnalysisService:
    paper_source_fetcher: PaperSourceFetcher
    paper_parser: PaperParser
    repo_tracer: RepoTracer
    diff_analyzer: DiffAnalyzer
    contribution_mapper: ContributionMapper
    llm_client: LLMClient | None = None

    def analyze(
        self,
        request: AnalysisRequest,
        *,
        progress: StageProgressCallback | None = None,
    ) -> AnalysisResult:
        case_slug = detect_case_slug(request)
        fixture = load_golden_case(case_slug) if case_slug is not None else None
        if progress is not None:
            progress(JobStage.PAPER_FETCH, 0.0, "Starting paper fetch.")
        fetch_output = self.paper_source_fetcher.fetch(request, progress=progress)
        if progress is not None:
            progress(
                JobStage.PAPER_FETCH,
                1.0,
                f"Loaded {fetch_output.paper_document.source_kind} paper source {fetch_output.paper_document.title}.",
            )
            progress(JobStage.PAPER_PARSE, 0.0, "Starting structured paper parsing.")
        parse_output = self.paper_parser.parse(request, fetch_output.paper_document, progress=progress)
        if progress is not None:
            progress(
                JobStage.PAPER_PARSE,
                1.0,
                f"Paper parsing completed with {len(parse_output.contributions)} contributions.",
            )
            progress(JobStage.REPO_FETCH, 0.0, "Resolving target repository from the paper source.")
        resolved_repo_url, repo_resolution_warnings = resolve_target_repo_url(
            request,
            fetch_output.paper_document,
            parse_output.contributions,
            get_settings(),
            self.llm_client,
        )
        request.repo_url = resolved_repo_url
        if progress is not None:
            progress(JobStage.REPO_FETCH, 0.05, f"Resolved target repository {resolved_repo_url}.")
        trace_output = self.repo_tracer.trace(
            request,
            fetch_output.paper_document,
            parse_output.contributions,
            progress=progress,
        )
        diff_output = self.diff_analyzer.analyze(
            request,
            trace_output.selected_base_repo,
            parse_output.contributions,
            progress=progress,
        )
        mapping_output = self.contribution_mapper.map(
            request,
            parse_output.contributions,
            diff_output.diff_clusters,
            progress=progress,
        )
        stage_warnings = dedupe_preserving_order(
            [
                *fetch_output.warnings,
                *parse_output.warnings,
                *repo_resolution_warnings,
                *trace_output.warnings,
                *diff_output.warnings,
                *mapping_output.warnings,
            ]
        )
        warnings = dedupe_preserving_order([*(fixture.warnings if fixture is not None else []), *stage_warnings])
        result_case_slug = fixture.case_slug if fixture is not None else GENERIC_CASE_SLUG
        result_summary = (
            fixture.summary
            if fixture is not None
            else build_generic_result_summary(
                fetch_output.paper_document,
                parse_output.contributions,
                trace_output.selected_base_repo,
            )
        )
        return AnalysisResult(
            case_slug=result_case_slug,
            summary=result_summary,
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
    repo_tracer_settings: Settings | None = settings if settings.use_live_repo_trace() else None
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
            settings=repo_tracer_settings,
            llm_client=llm_client,
        ),
        diff_analyzer=diff_analyzer,
        contribution_mapper=FixtureContributionMapper(llm_client=llm_client),
        llm_client=llm_client,
    )
