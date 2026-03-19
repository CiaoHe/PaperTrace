from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest
from papertrace_core.fixtures import load_paper_fixture
from papertrace_core.llm import build_llm_client
from papertrace_core.models import AnalysisRequest, ProcessorMode
from papertrace_core.paper_sources import ArxivPaperSourceFetcher, paper_document_from_fixture
from papertrace_core.repo_metadata import GitHubRepoMetadataProvider
from papertrace_core.repos import ShallowGitRepoMirror
from papertrace_core.services import (
    AnalysisService,
    FixtureContributionMapper,
    HeuristicPaperParser,
    LiveRepoDiffAnalyzer,
    StrategyDrivenRepoTracer,
    build_default_analysis_service,
)
from papertrace_core.settings import get_settings
from papertrace_core.storage import create_job, get_job_result, get_job_summary, init_db, reset_storage_state

PYTHONPATHS = "apps/api/src:apps/worker/src:packages/analysis-core/src"


def is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def poll_for_status(job_id: str, *, timeout_seconds: float = 120.0) -> str | None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        summary = get_job_summary(job_id)
        if summary is not None and summary.status.value in {"succeeded", "failed"}:
            return summary.status.value
        time.sleep(1.0)
    return None


@pytest.mark.smoke
def test_smoke_llm_extracts_contributions() -> None:
    settings = get_settings()
    llm_client = build_llm_client(settings)
    if llm_client is None:
        pytest.skip("LLM_BASE_URL and LLM_MODEL are required for smoke LLM coverage")
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    contributions = llm_client.extract_contributions(paper_document_from_fixture(request, load_paper_fixture("lora")))

    assert contributions
    assert contributions[0].id
    assert contributions[0].title


@pytest.mark.smoke
def test_smoke_github_repo_metadata_fetch() -> None:
    settings = get_settings()
    provider = GitHubRepoMetadataProvider(settings)

    output = provider.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685 LoRA",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.readme_text
    assert "lora" in output.readme_text.lower()


@pytest.mark.smoke
def test_smoke_arxiv_paper_fetch() -> None:
    settings = get_settings()
    fetcher = ArxivPaperSourceFetcher(settings)

    output = fetcher.fetch(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2106.09685",
            repo_url="https://github.com/microsoft/LoRA",
        )
    )

    assert output.paper_document.title
    assert output.paper_document.abstract


@pytest.mark.smoke
def test_smoke_flash_attention_runs_non_fixture_primary_path() -> None:
    settings = get_settings()
    repo_mirror = ShallowGitRepoMirror(settings)
    service = AnalysisService(
        paper_source_fetcher=ArxivPaperSourceFetcher(settings),
        paper_parser=HeuristicPaperParser(),
        repo_tracer=StrategyDrivenRepoTracer(
            repo_metadata_provider=GitHubRepoMetadataProvider(settings),
        ),
        diff_analyzer=LiveRepoDiffAnalyzer(
            repo_mirror=repo_mirror,
            settings=settings,
        ),
        contribution_mapper=FixtureContributionMapper(),
    )

    result = service.analyze(
        AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/Dao-AILab/flash-attention",
        )
    )

    assert result.metadata.paper_fetch_mode == ProcessorMode.REMOTE_FETCH
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.HEURISTIC
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy != "fallback"
    assert result.diff_clusters


@pytest.mark.smoke
def test_smoke_default_service_prefers_live_path_when_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_LIVE_BY_DEFAULT", "true")
    get_settings.cache_clear()
    try:
        result = build_default_analysis_service().analyze(
            AnalysisRequest(
                paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
                repo_url="https://github.com/Dao-AILab/flash-attention",
            )
        )
    finally:
        get_settings.cache_clear()

    assert result.metadata.paper_fetch_mode == ProcessorMode.REMOTE_FETCH
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy != "fallback"


@pytest.mark.smoke
def test_smoke_postgres_redis_and_non_eager_celery_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not is_port_open("127.0.0.1", 5432):
        pytest.skip("PostgreSQL is not reachable on 127.0.0.1:5432")
    if not is_port_open("127.0.0.1", 6379):
        pytest.skip("Redis is not reachable on 127.0.0.1:6379")

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": PYTHONPATHS,
            "DATABASE_URL": "postgresql+psycopg://papertrace:papertrace@127.0.0.1:5432/papertrace",
            "CELERY_BROKER_URL": "redis://127.0.0.1:6379/14",
            "CELERY_RESULT_BACKEND": "redis://127.0.0.1:6379/15",
            "CELERY_TASK_ALWAYS_EAGER": "false",
            "ENABLE_LIVE_BY_DEFAULT": "true",
        }
    )

    for key, value in env.items():
        if key in {
            "DATABASE_URL",
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "CELERY_TASK_ALWAYS_EAGER",
            "ENABLE_LIVE_BY_DEFAULT",
        }:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_storage_state()

    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "papertrace_worker.celery_app.celery_app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--without-heartbeat",
            "--without-gossip",
            "--without-mingle",
            "--loglevel=warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(4.0)
        if worker.poll() is not None:
            output = worker.stdout.read() if worker.stdout is not None else ""
            pytest.skip(f"Celery worker failed to start:\n{output}")

        init_db()
        request = AnalysisRequest(
            paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
            repo_url="https://github.com/Dao-AILab/flash-attention",
        )
        job = create_job(request)
        payload_json = json.dumps(request.model_dump(mode="json"))
        enqueue_code = (
            "import json; "
            "from papertrace_worker.tasks import enqueue_analysis; "
            f"enqueue_analysis.delay({job.id!r}, json.loads({payload_json!r}))"
        )
        enqueue_result = subprocess.run(
            [sys.executable, "-c", enqueue_code],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if enqueue_result.returncode != 0:
            pytest.skip(f"Failed to enqueue Celery task:\n{enqueue_result.stderr}")

        final_status = poll_for_status(job.id)
        assert final_status == "succeeded"

        summary = get_job_summary(job.id)
        assert summary is not None
        assert summary.stage is not None
        assert summary.status.value == "succeeded"

        result = get_job_result(job.id)
        assert result is not None
        assert result.metadata.paper_fetch_mode == ProcessorMode.REMOTE_FETCH
        assert result.metadata.diff_analyzer_mode == ProcessorMode.HEURISTIC
        assert result.metadata.selected_repo_strategy != "fallback"
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
        get_settings.cache_clear()
        reset_storage_state()
