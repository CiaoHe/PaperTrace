from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    DiffChangeType,
    PaperContribution,
)
from papertrace_core.services import LiveRepoDiffAnalyzer
from papertrace_core.settings import Settings


class StaticRepoMirror:
    def __init__(self, mapping: Mapping[str, Path]) -> None:
        self.mapping = dict(mapping)

    def prepare(self, repo_url: str) -> Path:
        return self.mapping[repo_url]


def init_git_repo(root: Path, files: Mapping[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "papertrace@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "PaperTrace Tests"],
        check=True,
        capture_output=True,
        text=True,
    )
    for relative_path, content in files.items():
        file_path = root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo_settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "LOCAL_DATA_DIR": str(tmp_path / ".local"),
            "ENABLE_LIVE_REPO_ANALYSIS": True,
            "REPO_ANALYSIS_INCLUDE_DIRS": ["src"],
            "REPO_MAX_FILE_SIZE_BYTES": 50_000,
            "REPO_MAX_FILES": 50,
        }
    )


def test_live_repo_diff_analyzer_groups_new_and_modified_files(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    base_repo = tmp_path / "base"
    target_repo = tmp_path / "target"
    init_git_repo(
        base_repo,
        {
            "src/model.py": "class Model:\n    pass\n",
            "src/train.py": "def train():\n    return 'base'\n",
        },
    )
    init_git_repo(
        target_repo,
        {
            "src/model.py": "class Model:\n    pass\n\nclass LoraAdapter:\n    rank = 8\n",
            "src/train.py": "def train():\n    return 'finetune with adapters'\n",
            "src/loss.py": "def preference_loss(logits):\n    return logits.mean()\n",
        },
    )

    analyzer = LiveRepoDiffAnalyzer(
        repo_mirror=StaticRepoMirror(
            {
                "https://github.com/example/base": base_repo,
                "https://github.com/example/target": target_repo,
            }
        ),
        settings=repo_settings,
    )
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/example/target",
    )
    contributions = [
        PaperContribution(
            id="C1",
            title="Low-rank adaptation modules",
            section="Section 3",
            keywords=["adapter", "rank"],
            impl_hints=["Insert trainable low-rank modules."],
        ),
        PaperContribution(
            id="C2",
            title="Preference optimization objective",
            section="Section 4",
            keywords=["preference", "logits"],
            impl_hints=["Optimize a preference-aware objective."],
        ),
    ]

    result = analyzer.analyze(
        request,
        BaseRepoCandidate(
            repo_url="https://github.com/example/base",
            strategy="readme_declaration",
            confidence=0.9,
            evidence="test",
        ),
        contributions,
    )

    assert result.mode.value == "heuristic"
    assert result.warnings == []
    assert len(result.diff_clusters) == 3
    assert result.diff_clusters[0].id == "D1"
    assert any(
        cluster.change_type == DiffChangeType.MODIFIED_LOSS for cluster in result.diff_clusters
    )
    assert any(cluster.label == "Low-rank adaptation modules" for cluster in result.diff_clusters)
