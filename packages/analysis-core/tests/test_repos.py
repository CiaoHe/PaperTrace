from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from papertrace_core.interfaces import RepoMetadataOutput
from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    DiffChangeType,
    PaperContribution,
    PaperDocument,
    PaperSection,
    PaperSourceKind,
)
from papertrace_core.repos import RepoAccessError
from papertrace_core.services import LiveRepoDiffAnalyzer, StrategyDrivenRepoTracer
from papertrace_core.settings import Settings


class StaticRepoMirror:
    def __init__(self, mapping: Mapping[str, Path]) -> None:
        self.mapping = dict(mapping)

    def prepare(self, repo_url: str) -> Path:
        try:
            return self.mapping[repo_url]
        except KeyError as exc:
            raise RepoAccessError(f"Missing repository mapping for {repo_url}") from exc


class EmptyRepoMetadataProvider:
    def fetch(self, _: AnalysisRequest) -> RepoMetadataOutput:
        return RepoMetadataOutput(
            fork_parent=None,
            readme_text="",
            notes="",
            warnings=[],
        )


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


def test_repo_tracer_uses_live_code_fingerprint_candidates(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-tracer"
    triton_repo = tmp_path / "triton"
    transformers_repo = tmp_path / "transformers"
    trl_repo = tmp_path / "trl"
    init_git_repo(
        target_repo,
        {
            "src/kernel.py": (
                "import triton\n\n"
                "def launch_kernel(block_size, num_warps):\n"
                "    return triton.jit(block_size + num_warps)\n"
            ),
            "src/runtime.py": "def launch(num_warps):\n    return num_warps\n",
        },
    )
    init_git_repo(
        triton_repo,
        {
            "src/triton_kernel.py": (
                "import triton\n\n"
                "def launch_kernel(block_size, num_warps):\n"
                "    return triton.jit(block_size)\n"
            ),
        },
    )
    init_git_repo(
        transformers_repo,
        {
            "src/modeling.py": "class TransformerModel:\n    pass\n",
        },
    )
    init_git_repo(
        trl_repo,
        {
            "src/trainer.py": "def train_preference_model():\n    return 'trl'\n",
        },
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror(
            {
                "https://github.com/example/target": target_repo,
                "https://github.com/openai/triton": triton_repo,
                "https://github.com/huggingface/transformers": transformers_repo,
                "https://github.com/huggingface/trl": trl_repo,
            }
        ),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/example/target",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/2205.14135",
            title="Flash Attention",
            abstract="",
            sections=[PaperSection(heading="Abstract", text="Kernel launch optimization")],
            text="Kernel launch optimization for attention.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.strategy == "code_fingerprint"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/openai/triton"
    assert any(candidate.strategy == "code_fingerprint" for candidate in trace_output.candidates)
