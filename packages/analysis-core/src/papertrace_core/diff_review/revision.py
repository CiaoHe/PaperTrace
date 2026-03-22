from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from types import ModuleType

from papertrace_core.diff_review.common import sha256_text, stable_digest
from papertrace_core.diff_review.file_mapper import list_reviewable_files
from papertrace_core.settings import Settings


def resolve_repo_revision(repo_root: Path, settings: Settings) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.repo_clone_timeout_seconds,
        )
        revision = completed.stdout.strip()
        if revision:
            return revision
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    file_entries = []
    for relative_path, path in sorted(list_reviewable_files(repo_root, settings).items()):
        file_entries.append(
            {
                "relative_path": relative_path,
                "content_sha256": sha256_text(path.read_text(encoding="utf-8", errors="ignore")),
            }
        )
    return stable_digest(file_entries, length=16)


def module_source_digest(module_object: ModuleType) -> str:
    return sha256_text(inspect.getsource(module_object), length=8)
