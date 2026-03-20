from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from papertrace_core.interfaces import FetchOutput, RepoMetadataOutput
from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    DiffChangeType,
    PaperContribution,
    PaperDocument,
    PaperSection,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.repos import RepoAccessError
from papertrace_core.services import (
    AnalysisService,
    FixtureContributionMapper,
    HeuristicPaperParser,
    LiveRepoDiffAnalyzer,
    StrategyDrivenRepoTracer,
)
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


class StaticPaperSourceFetcher:
    def __init__(self, paper_document: PaperDocument) -> None:
        self.paper_document = paper_document

    def fetch(self, _: AnalysisRequest) -> FetchOutput:
        return FetchOutput(
            paper_document=self.paper_document,
            mode=ProcessorMode.REMOTE_FETCH,
            warnings=[],
        )


def init_git_repo(root: Path, files: Mapping[str, str], commit_message: str = "seed") -> None:
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
        ["git", "-C", str(root), "commit", "-m", commit_message],
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
            "REPO_ANALYSIS_EXCLUDE_DIRS": ["docs", ".github"],
            "REPO_ANALYSIS_EXCLUDE_FILENAMES": ["readme.md", "poetry.lock"],
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
    assert "bucketed as" in result.diff_clusters[0].summary
    assert result.diff_clusters[0].semantic_tags
    assert any(cluster.change_type == DiffChangeType.MODIFIED_LOSS for cluster in result.diff_clusters)
    assert any(cluster.label == "Low-rank adaptation modules" for cluster in result.diff_clusters)


def test_live_repo_diff_analyzer_filters_docs_and_lockfiles(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    base_repo = tmp_path / "base-filtered"
    target_repo = tmp_path / "target-filtered"
    init_git_repo(
        base_repo,
        {
            "src/core.py": "def train():\n    return 'base'\n",
            "README.md": "# Base\n",
        },
    )
    init_git_repo(
        target_repo,
        {
            "src/core.py": "def train():\n    return 'updated training loop'\n",
            "README.md": "# Updated docs\n",
            "docs/guide.md": "documentation only\n",
            "poetry.lock": "lockfile\n",
        },
    )

    analyzer = LiveRepoDiffAnalyzer(
        repo_mirror=StaticRepoMirror(
            {
                "https://github.com/example/base-filtered": base_repo,
                "https://github.com/example/target-filtered": target_repo,
            }
        ),
        settings=repo_settings,
    )
    result = analyzer.analyze(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/example/target-filtered",
        ),
        BaseRepoCandidate(
            repo_url="https://github.com/example/base-filtered",
            strategy="readme_declaration",
            confidence=0.9,
            evidence="test",
        ),
        [
            PaperContribution(
                id="C1",
                title="Training changes",
                section="Section 3",
                keywords=["training", "train"],
                impl_hints=["Update training flow."],
            )
        ],
    )

    assert len(result.diff_clusters) == 1
    assert result.diff_clusters[0].files == ["src/core.py"]


def test_live_repo_diff_analyzer_sets_related_clusters_by_semantic_tags(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    base_repo = tmp_path / "base-semantic"
    target_repo = tmp_path / "target-semantic"
    init_git_repo(base_repo, {"src/core.py": "def base():\n    return 1\n"})
    init_git_repo(
        target_repo,
        {
            "src/attention_kernel.py": "def attention_kernel(qkv):\n    return qkv\n",
            "src/attention_train.py": "def train_attention(attention_kernel):\n    return attention_kernel\n",
        },
    )

    analyzer = LiveRepoDiffAnalyzer(
        repo_mirror=StaticRepoMirror(
            {
                "https://github.com/example/base-semantic": base_repo,
                "https://github.com/example/target-semantic": target_repo,
            }
        ),
        settings=repo_settings,
    )
    result = analyzer.analyze(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/example/target-semantic",
        ),
        BaseRepoCandidate(
            repo_url="https://github.com/example/base-semantic",
            strategy="readme_declaration",
            confidence=0.9,
            evidence="test",
        ),
        [
            PaperContribution(
                id="C1",
                title="Attention kernel",
                section="Method",
                keywords=["attention", "kernel"],
                impl_hints=["Introduce a fused attention kernel."],
            )
        ],
    )

    assert result.diff_clusters
    assert all(cluster.semantic_tags for cluster in result.diff_clusters)
    if len(result.diff_clusters) > 1:
        assert any(cluster.related_cluster_ids for cluster in result.diff_clusters)


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
                "import triton\n\ndef launch_kernel(block_size, num_warps):\n    return triton.jit(block_size)\n"
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

    assert trace_output.selected_base_repo.strategy in {"framework_signature", "code_fingerprint"}
    assert trace_output.selected_base_repo.repo_url == "https://github.com/openai/triton"


def test_analysis_service_can_run_without_fixture_primary_path(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-service"
    base_repo = tmp_path / "base-service"
    init_git_repo(
        target_repo,
        {
            "src/kernel.py": (
                "import triton\n\n"
                "def attention_kernel(block_size, num_warps):\n"
                "    io_aware_attention_kernel = block_size + num_warps\n"
                "    return triton.jit(io_aware_attention_kernel)\n"
            )
        },
    )
    init_git_repo(
        base_repo,
        {
            "src/triton_kernel.py": (
                "import triton\n\ndef attention_kernel(block_size, num_warps):\n    return triton.jit(block_size)\n"
            )
        },
    )

    paper_document = PaperDocument(
        source_kind=PaperSourceKind.ARXIV,
        source_ref="https://arxiv.org/abs/2205.14135",
        title="Flash Attention",
        abstract="IO-aware fused attention kernel with Triton-like execution patterns.",
        sections=[
            PaperSection(
                heading="Abstract",
                text="IO-aware fused attention kernel with Triton-like execution patterns.",
            )
        ],
        text="IO-aware fused attention kernel with Triton-like execution patterns.",
    )
    repo_mapping = {
        "https://github.com/example/target-service": target_repo,
        "https://github.com/openai/triton": base_repo,
    }
    service = AnalysisService(
        paper_source_fetcher=StaticPaperSourceFetcher(paper_document),
        paper_parser=HeuristicPaperParser(),
        repo_tracer=StrategyDrivenRepoTracer(
            repo_metadata_provider=EmptyRepoMetadataProvider(),
            repo_mirror=StaticRepoMirror(repo_mapping),
            settings=repo_settings,
        ),
        diff_analyzer=LiveRepoDiffAnalyzer(
            repo_mirror=StaticRepoMirror(repo_mapping),
            settings=repo_settings,
        ),
        contribution_mapper=FixtureContributionMapper(),
    )
    result = service.analyze(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/example/target-service",
        )
    )

    assert result.metadata.paper_fetch_mode == ProcessorMode.REMOTE_FETCH
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.HEURISTIC
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy in {"paper_mention", "framework_signature", "code_fingerprint"}
    assert result.diff_clusters


def test_repo_tracer_extracts_readme_github_urls_without_seed_aliases() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/9999.00001",
        repo_url="https://github.com/example/project",
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.ARXIV,
        source_ref="https://arxiv.org/abs/9999.00001",
        title="Example Paper",
        abstract="",
        sections=[],
        text="This paper describes a custom system.",
    )

    class ReadmeUrlRepoMetadataProvider:
        def fetch(self, _: AnalysisRequest) -> RepoMetadataOutput:
            return RepoMetadataOutput(
                fork_parent=None,
                readme_text="Built on top of https://github.com/example/upstream-core for the base runtime.",
                notes="",
                warnings=[],
            )

    trace_output = StrategyDrivenRepoTracer(repo_metadata_provider=ReadmeUrlRepoMetadataProvider()).trace(
        request,
        paper_document,
        [],
    )

    assert trace_output.selected_base_repo.strategy == "readme_declaration"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/example/upstream-core"


def test_repo_tracer_extracts_code_reference_candidates_from_imports(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-code-reference"
    init_git_repo(
        target_repo,
        {
            "src/train.py": "from transformers import AutoModel\n\ndef build_model():\n    return AutoModel\n",
        },
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror({"https://github.com/example/target-code-reference": target_repo}),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00002",
            repo_url="https://github.com/example/target-code-reference",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00002",
            title="Target Project",
            abstract="",
            sections=[],
            text="No explicit upstream mention in paper text.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.strategy in {"framework_signature", "code_reference"}
    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"


def test_repo_tracer_extracts_framework_signature_candidates(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-framework-signature"
    init_git_repo(
        target_repo,
        {
            "src/model.py": (
                "from transformers import PreTrainedModel, AutoModelForCausalLM\n\n"
                "class MyModel(PreTrainedModel):\n"
                "    @classmethod\n"
                "    def from_pretrained_checkpoint(cls):\n"
                "        return AutoModelForCausalLM.from_pretrained('gpt2')\n"
            ),
        },
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror({"https://github.com/example/framework-target": target_repo}),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00003",
            repo_url="https://github.com/example/framework-target",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00003",
            title="Framework Signature Test",
            abstract="",
            sections=[],
            text="No explicit upstream mention.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.strategy == "framework_signature"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"
    assert any(candidate.strategy == "framework_signature" for candidate in trace_output.candidates)


def test_repo_tracer_extracts_dependency_archaeology_candidates(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-dependency-archaeology"
    init_git_repo(
        target_repo,
        {
            "src/train.py": "def train():\n    return 'ok'\n",
            "requirements.txt": "trl>=0.8.0\n-e git+https://github.com/huggingface/peft.git@main#egg=peft\n",
            ".gitmodules": (
                '[submodule "third_party/base"]\n'
                "\tpath = third_party/base\n"
                "\turl = https://github.com/openai/triton.git\n"
            ),
        },
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror({"https://github.com/example/dependency-target": target_repo}),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00004",
            repo_url="https://github.com/example/dependency-target",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00004",
            title="Dependency Archaeology Test",
            abstract="",
            sections=[],
            text="No explicit upstream mention.",
        ),
        [],
    )

    dependency_candidates = [
        candidate for candidate in trace_output.candidates if candidate.strategy == "dependency_archaeology"
    ]
    assert dependency_candidates
    assert any(candidate.repo_url == "https://github.com/huggingface/trl" for candidate in dependency_candidates)
    assert any(candidate.repo_url == "https://github.com/huggingface/peft" for candidate in dependency_candidates)
    assert any(candidate.repo_url == "https://github.com/openai/triton" for candidate in dependency_candidates)


def test_repo_tracer_extracts_fossil_candidates_from_first_commit(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-fossil"
    init_git_repo(
        target_repo,
        {
            "src/model.py": "class Model:\n    pass\n",
            "src/train.py": "def train():\n    return 'ok'\n",
        },
        commit_message="Init from https://github.com/facebookresearch/fairseq baseline",
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror({"https://github.com/example/fossil-target": target_repo}),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00005",
            repo_url="https://github.com/example/fossil-target",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00005",
            title="Fossil Test",
            abstract="",
            sections=[],
            text="No explicit upstream mention.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.repo_url == "https://github.com/facebookresearch/fairseq"
    assert trace_output.selected_base_repo.strategy == "fossil_evidence"


def test_repo_tracer_extracts_shape_similarity_candidates(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-shape"
    base_repo = tmp_path / "base-shape"
    distractor_repo = tmp_path / "distractor-shape"
    init_git_repo(
        target_repo,
        {
            "src/model.py": "def build_model():\n    return 'x'\n",
            "src/trainer.py": "def train_model():\n    return 'y'\n",
        },
    )
    init_git_repo(
        base_repo,
        {
            "src/modeling.py": "def build_model():\n    return 'base'\n",
            "src/trainer.py": "def train_model():\n    return 'base'\n",
        },
    )
    init_git_repo(
        distractor_repo,
        {
            "lib/server.js": "export const x = 1;\n",
            "package.json": '{"name": "distractor"}\n',
        },
    )

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror(
            {
                "https://github.com/example/target-shape": target_repo,
                "https://github.com/huggingface/transformers": base_repo,
                "https://github.com/example/distractor-shape": distractor_repo,
            }
        ),
        settings=repo_settings,
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00006",
            repo_url="https://github.com/example/target-shape",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00006",
            title="Shape Similarity Test",
            abstract="",
            sections=[],
            text="No explicit upstream mention.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.strategy == "shape_similarity"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"


def test_repo_tracer_extracts_github_code_search_candidates(
    tmp_path: Path,
    repo_settings: Settings,
) -> None:
    target_repo = tmp_path / "target-code-search"
    init_git_repo(
        target_repo,
        {
            "src/kernel.py": (
                "def fused_attention_router_kernel(block_size, num_warps):\n    return block_size + num_warps\n"
            ),
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/code":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "repository": {"html_url": "https://github.com/example/upstream-kernel"},
                            "path": "src/kernel.py",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    tracer = StrategyDrivenRepoTracer(
        repo_metadata_provider=EmptyRepoMetadataProvider(),
        repo_mirror=StaticRepoMirror({"https://github.com/example/target-code-search": target_repo}),
        settings=repo_settings.model_copy(
            update={
                "github_api_base_url": "https://example.test",
                "github_token": "token",
            }
        ),
        github_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    trace_output = tracer.trace(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/9999.00007",
            repo_url="https://github.com/example/target-code-search",
        ),
        PaperDocument(
            source_kind=PaperSourceKind.ARXIV,
            source_ref="https://arxiv.org/abs/9999.00007",
            title="Code Search Test",
            abstract="",
            sections=[],
            text="No explicit upstream mention.",
        ),
        [],
    )

    assert trace_output.selected_base_repo.strategy == "github_code_search"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/example/upstream-kernel"
