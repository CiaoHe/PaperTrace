from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path('.local/test-worker.db').resolve()}"

from papertrace_core.models import (  # noqa: E402
    AnalysisRequest,
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    JobStage,
    JobStatus,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.settings import get_settings  # noqa: E402
from papertrace_core.storage import (  # noqa: E402
    create_job,
    get_job_result,
    get_job_summary,
    init_db,
    reset_storage_state,
    update_job_status,
)
from papertrace_worker.tasks import enqueue_analysis  # noqa: E402


@pytest.fixture(autouse=True)
def reset_worker_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    test_db_path = tmp_path_factory.mktemp("worker-db") / "test.db"
    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{test_db_path}"
    get_settings.cache_clear()
    reset_storage_state()


def make_result(case_slug: str, repo_url: str) -> AnalysisResult:
    return AnalysisResult(
        case_slug=case_slug,
        summary=f"{case_slug} summary",
        selected_base_repo=BaseRepoCandidate(
            repo_url=repo_url,
            strategy="fixture",
            confidence=1.0,
            evidence="test",
        ),
        base_repo_candidates=[],
        contributions=[],
        diff_clusters=[],
        mappings=[],
        unmatched_contribution_ids=[],
        unmatched_diff_cluster_ids=[],
        metadata=AnalysisRuntimeMetadata(
            paper_source_kind=PaperSourceKind.ARXIV,
            paper_fetch_mode=ProcessorMode.HEURISTIC,
            parser_mode=ProcessorMode.HEURISTIC,
            repo_tracer_mode=ProcessorMode.HEURISTIC,
            diff_analyzer_mode=ProcessorMode.HEURISTIC,
            contribution_mapper_mode=ProcessorMode.HEURISTIC,
            selected_repo_strategy="fixture",
            fallback_notes=[],
        ),
        warnings=[],
    )


def test_enqueue_analysis_persists_result(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    job = create_job(request)
    job_id = job.id
    result = make_result("flash-attention", "https://github.com/openai/triton")

    def fake_process_analysis_job(fake_job_id: str, fake_request: AnalysisRequest) -> AnalysisResult:
        update_job_status(
            fake_job_id,
            status=JobStatus.RUNNING,
            stage=JobStage.PAPER_FETCH,
            stage_progress=0.0,
            stage_detail="Worker started analysis execution.",
            repo_url=fake_request.repo_url,
        )
        update_job_status(
            fake_job_id,
            status=JobStatus.SUCCEEDED,
            stage=JobStage.PERSIST_RESULT,
            stage_progress=1.0,
            stage_detail="Analysis result persisted.",
            summary=result.summary,
            result=result,
            repo_url=fake_request.repo_url,
        )
        return result

    monkeypatch.setattr("papertrace_worker.tasks.process_analysis_job", fake_process_analysis_job)
    monkeypatch.setattr("papertrace_worker.tasks.build_review_artifact.delay", lambda *_args, **_kwargs: None)

    payload = enqueue_analysis(job_id, request.model_dump(mode="json"))

    assert payload["job_id"] == job_id
    assert payload["case_slug"] == "flash-attention"

    summary = get_job_summary(job_id)
    assert summary is not None
    assert summary.status.value == "succeeded"
    assert summary.stage is not None
    assert summary.stage_progress == 1.0
    assert summary.stage_detail == "Analysis result persisted."
    assert len(summary.timeline) >= 3

    persisted_result = get_job_result(job_id)
    assert persisted_result is not None
    assert persisted_result.case_slug == "flash-attention"


def test_enqueue_analysis_queues_review_build(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    job = create_job(request)
    queued: list[str] = []
    monkeypatch.setattr(
        "papertrace_worker.tasks.process_analysis_job",
        lambda _job_id, _payload: SimpleNamespace(
            case_slug="lora",
            selected_base_repo=SimpleNamespace(repo_url="https://github.com/huggingface/transformers"),
        ),
    )
    monkeypatch.setattr(
        "papertrace_worker.tasks.get_settings",
        lambda: SimpleNamespace(celery_task_always_eager=True, use_live_repo_analysis=lambda: True),
    )
    monkeypatch.setattr(
        "papertrace_worker.tasks.build_review_artifact.delay",
        lambda queued_job_id: queued.append(queued_job_id),
    )

    enqueue_analysis(job.id, request.model_dump(mode="json"))

    assert queued == [job.id]
