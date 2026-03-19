from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path('.local/test-api.db').resolve()}"

from papertrace_api.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"


def test_examples_endpoint_returns_seed_cases() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/examples")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["examples"]) == 3
    assert payload["examples"][0]["slug"] == "lora"


def test_list_analyses_endpoint_returns_created_jobs() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/analyses",
            json={
                "paper_source": "https://arxiv.org/abs/2106.09685 LoRA",
                "repo_url": "https://github.com/microsoft/LoRA",
            },
        )

        assert create_response.status_code == 202

        list_response = client.get("/api/v1/analyses")
        assert list_response.status_code == 200
        jobs = list_response.json()["jobs"]
        assert len(jobs) >= 1
        assert jobs[0]["repo_url"] == "https://github.com/microsoft/LoRA"


def test_create_analysis_runs_fixture_pipeline() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "paper_source": "https://arxiv.org/abs/2106.09685 LoRA",
                "repo_url": "https://github.com/microsoft/LoRA",
            },
        )

        assert response.status_code == 202
        body = response.json()
        job_id = body["job"]["id"]

        job_response = client.get(f"/api/v1/analyses/{job_id}")
        assert job_response.status_code == 200
        assert job_response.json()["job"]["status"] == "succeeded"

        result_response = client.get(f"/api/v1/analyses/{job_id}/result")
        assert result_response.status_code == 200
        result_body = result_response.json()["result"]
        assert result_body["case_slug"] == "lora"
        assert (
            result_body["selected_base_repo"]["repo_url"]
            == "https://github.com/huggingface/transformers"
        )
        assert result_body["metadata"]["paper_source_kind"] == "arxiv"
        assert result_body["metadata"]["repo_tracer_mode"] == "strategy_chain"


def test_create_analysis_rejects_non_github_repo_url() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "paper_source": "https://arxiv.org/abs/2106.09685 LoRA",
                "repo_url": "https://gitlab.com/example/repo",
            },
        )

    assert response.status_code == 422
