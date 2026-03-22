from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from papertrace_core.diff_review.common import language_for_path, path_tokens
from papertrace_core.diff_review.models import ReviewDiffType, ReviewMatchType
from papertrace_core.settings import Settings


@dataclass(frozen=True)
class FilePair:
    source_path: str | None
    current_path: str | None
    diff_type: ReviewDiffType
    match_type: ReviewMatchType
    similarity: float
    language: str

    @property
    def comparable(self) -> bool:
        return self.diff_type == ReviewDiffType.MODIFIED and self.match_type in {
            ReviewMatchType.EXACT_PATH,
            ReviewMatchType.CONTENT_MOVED,
        }


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
    return path.suffix.lower() in settings.repo_analysis_extensions


def list_reviewable_files(repo_root: Path, settings: Settings) -> dict[str, Path]:
    tracked_files: list[str] = []
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.repo_clone_timeout_seconds,
        )
        tracked_files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.SubprocessError):
        tracked_files = [path.relative_to(repo_root).as_posix() for path in repo_root.rglob("*") if path.is_file()]
    result: dict[str, Path] = {}
    for relative_path in tracked_files:
        if not should_include_repo_file(relative_path, settings):
            continue
        file_path = repo_root / relative_path
        if not file_path.is_file():
            continue
        if file_path.stat().st_size > settings.repo_max_file_size_bytes:
            continue
        result[relative_path] = file_path
        if len(result) >= settings.repo_max_files:
            break
    return result


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


@dataclass(frozen=True)
class MatchCandidate:
    path: str
    similarity: float


class FileMapper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def map_repositories(self, source_root: Path, current_root: Path) -> list[FilePair]:
        source_files = list_reviewable_files(source_root, self.settings)
        current_files = list_reviewable_files(current_root, self.settings)
        source_hashes = {relative_path: file_hash(path) for relative_path, path in source_files.items()}
        current_hashes = {relative_path: file_hash(path) for relative_path, path in current_files.items()}
        used_source_paths: set[str] = set()
        results: list[FilePair] = []

        for current_path in sorted(current_files):
            if current_path in source_files:
                if current_hashes[current_path] == source_hashes[current_path]:
                    used_source_paths.add(current_path)
                    continue
                used_source_paths.add(current_path)
                results.append(
                    FilePair(
                        source_path=current_path,
                        current_path=current_path,
                        diff_type=ReviewDiffType.MODIFIED,
                        match_type=ReviewMatchType.EXACT_PATH,
                        similarity=1.0,
                        language=language_for_path(current_path),
                    )
                )
                continue

            exact_hash_match = next(
                (
                    source_path
                    for source_path, source_hash in source_hashes.items()
                    if source_hash == current_hashes[current_path] and source_path not in used_source_paths
                ),
                None,
            )
            if exact_hash_match is not None:
                used_source_paths.add(exact_hash_match)
                results.append(
                    FilePair(
                        source_path=exact_hash_match,
                        current_path=current_path,
                        diff_type=ReviewDiffType.MODIFIED,
                        match_type=ReviewMatchType.CONTENT_MOVED,
                        similarity=1.0,
                        language=language_for_path(current_path),
                    )
                )
                continue

            candidates = self._rank_candidates(
                current_path,
                current_files[current_path],
                source_files,
                used_source_paths,
            )
            best = candidates[0] if candidates else None
            second = candidates[1] if len(candidates) > 1 else None
            if best is not None and best.similarity >= 0.55:
                if second is not None and second.similarity >= 0.55 and (best.similarity - second.similarity) < 0.08:
                    results.append(
                        FilePair(
                            source_path=None,
                            current_path=current_path,
                            diff_type=ReviewDiffType.ADDED,
                            match_type=ReviewMatchType.AMBIGUOUS,
                            similarity=best.similarity,
                            language=language_for_path(current_path),
                        )
                    )
                    continue
                used_source_paths.add(best.path)
                results.append(
                    FilePair(
                        source_path=best.path,
                        current_path=current_path,
                        diff_type=ReviewDiffType.MODIFIED,
                        match_type=ReviewMatchType.CONTENT_MOVED,
                        similarity=best.similarity,
                        language=language_for_path(current_path),
                    )
                )
                continue
            if best is not None and best.similarity >= 0.40:
                results.append(
                    FilePair(
                        source_path=best.path,
                        current_path=current_path,
                        diff_type=ReviewDiffType.ADDED,
                        match_type=ReviewMatchType.LOW_CONFIDENCE,
                        similarity=best.similarity,
                        language=language_for_path(current_path),
                    )
                )
                continue

            results.append(
                FilePair(
                    source_path=None,
                    current_path=current_path,
                    diff_type=ReviewDiffType.ADDED,
                    match_type=ReviewMatchType.ADDED,
                    similarity=0.0,
                    language=language_for_path(current_path),
                )
            )

        for source_path in sorted(set(source_files) - used_source_paths):
            results.append(
                FilePair(
                    source_path=source_path,
                    current_path=None,
                    diff_type=ReviewDiffType.DELETED,
                    match_type=ReviewMatchType.DELETED,
                    similarity=0.0,
                    language=language_for_path(source_path),
                )
            )
        return results

    def _rank_candidates(
        self,
        current_path: str,
        current_file: Path,
        source_files: dict[str, Path],
        used_source_paths: set[str],
    ) -> list[MatchCandidate]:
        current_text = read_text(current_file)
        current_tokens = path_tokens(current_path)
        ranked: list[MatchCandidate] = []
        for source_path, source_file in source_files.items():
            if source_path in used_source_paths:
                continue
            if Path(source_path).suffix.lower() != Path(current_path).suffix.lower():
                continue
            source_text = read_text(source_file)
            content_ratio = SequenceMatcher(a=source_text, b=current_text).ratio()
            token_overlap = len(current_tokens & path_tokens(source_path))
            stem_bonus = 0.12 if Path(source_path).stem == Path(current_path).stem else 0.0
            similarity = min(1.0, content_ratio + min(token_overlap * 0.05, 0.20) + stem_bonus)
            ranked.append(MatchCandidate(path=source_path, similarity=similarity))
        return sorted(ranked, key=lambda item: (item.similarity, item.path), reverse=True)
