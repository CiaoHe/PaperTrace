from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
TEST_DB_PATH = Path(".local/test-review-api.db").resolve()
TEST_ARTIFACT_PATH = Path(".local/test-review-cache").resolve()
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH}"
os.environ["REVIEW_ARTIFACT_BASE_DIR"] = str(TEST_ARTIFACT_PATH)
os.environ["ENABLE_LIVE_BY_DEFAULT"] = "false"

from papertrace_api.main import app  # noqa: E402
from papertrace_core.models import (  # noqa: E402
    AnalysisRequest,
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    DiffChangeType,
    DiffCluster,
    JobStage,
    JobStatus,
    PaperContribution,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.settings import get_settings  # noqa: E402
from papertrace_core.storage import (  # noqa: E402
    create_job,
    get_review_artifact_dir,
    get_review_manifest,
    init_db,
    reset_storage_state,
    update_job_status,
)
from papertrace_worker.tasks import refine_review_links  # noqa: E402

get_settings.cache_clear()


def init_git_repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "papertrace@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "PaperTrace"], check=True)
    for relative_path, content in files.items():
        file_path = root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True, text=True)


@pytest.fixture(autouse=True)
def reset_review_db() -> Generator[None, None, None]:
    get_settings.cache_clear()
    reset_storage_state()
    os.environ["LLM_BASE_URL"] = ""
    os.environ["LLM_MODEL"] = ""
    os.environ["LLM_API_KEY"] = ""
    TEST_DB_PATH.unlink(missing_ok=True)
    if TEST_ARTIFACT_PATH.exists():
        import shutil

        shutil.rmtree(TEST_ARTIFACT_PATH, ignore_errors=True)
    yield
    get_settings.cache_clear()
    reset_storage_state()
    TEST_DB_PATH.unlink(missing_ok=True)
    if TEST_ARTIFACT_PATH.exists():
        import shutil

        shutil.rmtree(TEST_ARTIFACT_PATH, ignore_errors=True)


def make_result(source_repo: str) -> AnalysisResult:
    contribution = PaperContribution(
        id="C1",
        title="Adaptive decoder",
        section="Method",
        keywords=["decoder"],
        impl_hints=["Adaptive decoding stabilizes generation."],
    )
    return AnalysisResult(
        case_slug="custom",
        summary="Review artifact test result",
        selected_base_repo=BaseRepoCandidate(
            repo_url=source_repo,
            strategy="test",
            confidence=0.9,
            evidence="test source repo",
        ),
        base_repo_candidates=[
            BaseRepoCandidate(
                repo_url=source_repo,
                strategy="test",
                confidence=0.9,
                evidence="test source repo",
            )
        ],
        contributions=[contribution],
        diff_clusters=[
            DiffCluster(
                id="D1",
                label="Decoder change",
                change_type=DiffChangeType.MODIFIED_CORE,
                files=["src/model.py"],
                summary="decoder changed",
            )
        ],
        mappings=[],
        unmatched_contribution_ids=["C1"],
        unmatched_diff_cluster_ids=["D1"],
        metadata=AnalysisRuntimeMetadata(
            paper_source_kind=PaperSourceKind.TEXT_REFERENCE,
            paper_fetch_mode=ProcessorMode.HEURISTIC,
            parser_mode=ProcessorMode.HEURISTIC,
            repo_tracer_mode=ProcessorMode.HEURISTIC,
            diff_analyzer_mode=ProcessorMode.HEURISTIC,
            contribution_mapper_mode=ProcessorMode.HEURISTIC,
            selected_repo_strategy="test",
            fallback_notes=[],
        ),
        warnings=[],
    )


def configure_review_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def seed_succeeded_job(source_root: Path, current_root: Path, paper_source: str) -> str:
    request = AnalysisRequest(paper_source=paper_source, repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )
    return job.id


def test_review_endpoint_builds_and_serves_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/analyses/{job.id}/review")

    assert response.status_code == 200
    body = response.json()["review"]
    assert body["source_repo"] == str(source_root)
    assert body["current_repo"] == str(current_root)
    assert body["review_queue"]
    assert body["refinement_status"] == "disabled"
    file_id = body["review_queue"][0]["file_id"]

    with TestClient(app) as client:
        file_response = client.get(f"/api/v1/analyses/{job.id}/review/files/{file_id}")

    assert file_response.status_code == 200
    file_body = file_response.json()["file"]
    assert file_body["raw_unified_diff"]
    assert file_body["hunks"]
    assert "return x + 1" in file_body["source_content"]
    assert "return x + 2" in file_body["current_content"]


def test_review_large_file_fallback_serves_prebuilt_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_review_env(
        monkeypatch,
        REVIEW_LARGE_FILE_LINE_THRESHOLD="1",
        REVIEW_LARGE_FILE_DIFF_BYTES_THRESHOLD="1",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n    return x + 3\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-large", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )

    with TestClient(app) as client:
        review_response = client.get(f"/api/v1/analyses/{job.id}/review")
        assert review_response.status_code == 200
        file_id = review_response.json()["review"]["secondary_buckets"]["large_files"]["files"][0]["file_id"]

        file_response = client.get(f"/api/v1/analyses/{job.id}/review/files/{file_id}")
        assert file_response.status_code == 200
        file_body = file_response.json()["file"]
        assert file_body["fallback_mode"] == "diff2html_prebuilt"
        assert file_body["fallback_html_path"] == f"/api/v1/analyses/{job.id}/review/files/{file_id}/rendered"
        assert file_body["semantic_status"] == "large_file"

        rendered_response = client.get(file_body["fallback_html_path"])

    assert rendered_response.status_code == 200
    assert "d2h-wrapper" in rendered_response.text


def test_review_large_file_fallback_degrades_when_node_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_review_env(
        monkeypatch,
        REVIEW_NODE_BINARY="node-does-not-exist",
        REVIEW_LARGE_FILE_LINE_THRESHOLD="1",
        REVIEW_LARGE_FILE_DIFF_BYTES_THRESHOLD="1",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n    return x + 3\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-large-no-node", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )

    with TestClient(app) as client:
        review_response = client.get(f"/api/v1/analyses/{job.id}/review")
        assert review_response.status_code == 200
        file_id = review_response.json()["review"]["secondary_buckets"]["large_files"]["files"][0]["file_id"]

        file_response = client.get(f"/api/v1/analyses/{job.id}/review/files/{file_id}")
        assert file_response.status_code == 200
        file_body = file_response.json()["file"]
        assert file_body["fallback_mode"] == "raw_diff_only"
        assert file_body["fallback_html_path"] is None

        rendered_response = client.get(f"/api/v1/analyses/{job.id}/review/files/{file_id}/rendered")

    assert rendered_response.status_code == 404


def test_review_build_queues_refinement_when_llm_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_review_env(
        monkeypatch,
        LLM_BASE_URL="http://example.test/v1",
        LLM_MODEL="fake-model",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-queued", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )

    queued: list[str] = []

    def fake_delay(job_id: str) -> dict[str, str]:
        queued.append(job_id)
        return {"job_id": job_id}

    monkeypatch.setattr("papertrace_worker.tasks.refine_review_links.delay", fake_delay)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/analyses/{job.id}/review")

    assert response.status_code == 200
    assert response.json()["review"]["refinement_status"] == "queued"
    assert queued == [job.id]


def test_refinement_task_marks_running_and_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_review_env(
        monkeypatch,
        LLM_BASE_URL="http://example.test/v1",
        LLM_MODEL="fake-model",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-ready", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )
    monkeypatch.setattr("papertrace_worker.tasks.refine_review_links.delay", lambda *_args, **_kwargs: None)
    with TestClient(app) as client:
        build_response = client.get(f"/api/v1/analyses/{job.id}/review")
    assert build_response.status_code == 200
    assert build_response.json()["review"]["refinement_status"] == "queued"

    observed_running: list[str] = []

    def fake_request_refinement_decisions(**_kwargs: Any) -> list[dict[str, str]]:
        manifest = get_review_manifest(job.id)
        assert manifest is not None
        observed_running.append(manifest.refinement_status.value)
        return [{"hunk_id": _kwargs["candidates"][0].hunk_id, "verdict": "supports", "reason": "supported"}]

    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement.build_llm_client",
        lambda _settings: SimpleNamespace(model="fake-model", client=None),
    )
    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement._request_refinement_decisions",
        fake_request_refinement_decisions,
    )

    result = refine_review_links.run(job.id)
    manifest = get_review_manifest(job.id)

    assert observed_running
    assert set(observed_running) == {"running"}
    assert result["status"] == "ready"
    assert manifest is not None
    assert manifest.refinement_status.value == "ready"


def test_refinement_task_marks_failed_when_provider_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_review_env(
        monkeypatch,
        LLM_BASE_URL="http://example.test/v1",
        LLM_MODEL="fake-model",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-failed", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )
    monkeypatch.setattr("papertrace_worker.tasks.refine_review_links.delay", lambda *_args, **_kwargs: None)
    with TestClient(app) as client:
        build_response = client.get(f"/api/v1/analyses/{job.id}/review")
    assert build_response.status_code == 200

    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement.build_llm_client",
        lambda _settings: SimpleNamespace(model="fake-model", client=None),
    )
    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement._request_refinement_decisions",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limit")),
    )

    result = refine_review_links.run(job.id)
    manifest = get_review_manifest(job.id)

    assert result["status"] == "failed"
    assert manifest is not None
    assert manifest.refinement_status.value == "failed"


def test_review_rebuild_resets_failed_refinement_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_review_env(
        monkeypatch,
        LLM_BASE_URL="http://example.test/v1",
        LLM_MODEL="fake-model",
    )
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    request = AnalysisRequest(paper_source="paper://review-rebuild", repo_url=str(current_root))
    job = create_job(request)
    update_job_status(
        job.id,
        status=JobStatus.SUCCEEDED,
        stage=JobStage.PERSIST_RESULT,
        stage_progress=1.0,
        stage_detail="Analysis result persisted.",
        summary="ready",
        result=make_result(str(source_root)),
        repo_url=str(current_root),
    )
    monkeypatch.setattr("papertrace_worker.tasks.refine_review_links.delay", lambda *_args, **_kwargs: None)
    with TestClient(app) as client:
        build_response = client.get(f"/api/v1/analyses/{job.id}/review")
    assert build_response.status_code == 200

    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement.build_llm_client",
        lambda _settings: SimpleNamespace(model="fake-model", client=None),
    )
    monkeypatch.setattr(
        "papertrace_core.diff_review.refinement._request_refinement_decisions",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limit")),
    )
    refine_review_links.run(job.id)

    queued: list[str] = []
    monkeypatch.setattr(
        "papertrace_worker.tasks.build_review_artifact.delay",
        lambda job_id: queued.append(job_id),
    )
    with TestClient(app) as client:
        rebuild_response = client.post(f"/api/v1/analyses/{job.id}/review/rebuild")

    assert rebuild_response.status_code == 202
    assert rebuild_response.json()["build_status"] == "pending"
    assert rebuild_response.json()["refinement_status"] == "queued"
    assert queued == [job.id]


def test_review_endpoint_returns_202_when_build_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    job_id = seed_succeeded_job(source_root, current_root, "paper://review-lock")

    @contextmanager
    def held_lock(*_args: Any, **_kwargs: Any) -> Generator[bool, None, None]:
        yield False

    monkeypatch.setattr("papertrace_core.diff_review.builder.review_build_lock", held_lock)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/analyses/{job_id}/review")

    assert response.status_code == 202
    assert response.json()["build_status"] == "building"
    assert response.json()["build_phase"] == "file_mapping"


def test_review_cache_hit_reuses_existing_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    first_job_id = seed_succeeded_job(source_root, current_root, "paper://review-cache-hit")
    second_job_id = seed_succeeded_job(source_root, current_root, "paper://review-cache-hit")

    with TestClient(app) as client:
        first_response = client.get(f"/api/v1/analyses/{first_job_id}/review")

    assert first_response.status_code == 200
    first_cache_key = first_response.json()["review"]["cache_key"]

    monkeypatch.setattr(
        "papertrace_core.diff_review.builder._build_manifest",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit should not rebuild manifest")),
    )

    with TestClient(app) as client:
        second_response = client.get(f"/api/v1/analyses/{second_job_id}/review")

    assert second_response.status_code == 200
    assert second_response.json()["review"]["cache_key"] == first_cache_key


def test_review_endpoint_returns_409_when_build_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    job_id = seed_succeeded_job(source_root, current_root, "paper://review-build-failure")

    monkeypatch.setattr(
        "papertrace_core.diff_review.builder._build_manifest",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic build failure")),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/analyses/{job_id}/review")

    assert response.status_code == 409
    assert "synthetic build failure" in response.json()["build_error"]


def test_review_endpoint_reenqueues_when_ready_manifest_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})

    init_db()
    job_id = seed_succeeded_job(source_root, current_root, "paper://review-stale-manifest")

    with TestClient(app) as client:
        first_response = client.get(f"/api/v1/analyses/{job_id}/review")

    assert first_response.status_code == 200
    artifact_dir = get_review_artifact_dir(job_id)
    assert artifact_dir is not None
    (artifact_dir / "manifest.json").unlink()

    queued: list[str] = []
    monkeypatch.setattr(
        "papertrace_worker.tasks.build_review_artifact.delay",
        lambda queued_job_id: queued.append(queued_job_id),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/analyses/{job_id}/review")

    assert response.status_code == 202
    assert response.json()["build_status"] == "pending"
    assert queued == [job_id]
