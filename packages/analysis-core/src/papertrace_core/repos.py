from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from papertrace_core.settings import Settings


class RepoAccessError(RuntimeError):
    pass


def repo_cache_key(repo_url: str) -> str:
    return hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ShallowGitRepoMirror:
    settings: Settings

    def prepare(self, repo_url: str) -> Path:
        root = self.settings.local_data_dir / "repos" / repo_cache_key(repo_url)
        checkout_dir = root / "checkout"
        checkout_dir.parent.mkdir(parents=True, exist_ok=True)

        if (checkout_dir / ".git").exists():
            return checkout_dir

        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)

        command = [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            repo_url,
            str(checkout_dir),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.settings.repo_clone_timeout_seconds,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            if checkout_dir.exists():
                shutil.rmtree(checkout_dir, ignore_errors=True)
            raise RepoAccessError(f"Failed to clone repository {repo_url}: {exc}") from exc

        return checkout_dir
