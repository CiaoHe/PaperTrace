from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path('.local/test-api.db').resolve()}"
os.environ["ENABLE_LIVE_BY_DEFAULT"] = "false"

from papertrace_api.main import app


def build_pdf_bytes(title: str, body: str) -> bytes:
    stream = (
        "<< /Length {length} >>\nstream\nBT\n/F1 16 Tf\n36 96 Td\n({text}) Tj\nET\nendstream".format(
            length=len(body.encode("latin-1")) + 31,
            text=body.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"),
        )
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        stream,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("latin-1"))
        document.extend(payload)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    document.extend(
        ("trailer\n<< /Size {size} /Root 1 0 R /Info << /Title ({title}) >> >>\nstartxref\n{xref}\n%%EOF\n")
        .format(
            size=len(objects) + 1,
            title=title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"),
            xref=xref_offset,
        )
        .encode("latin-1")
    )
    return bytes(document)


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["live_by_default"] is False
    assert payload["live_paper_fetch"] is False
    assert payload["live_repo_trace"] is False
    assert payload["live_repo_analysis"] is False
    assert "pdf_url" in payload["supported_paper_source_kinds"]


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
        assert result_body["selected_base_repo"]["repo_url"] == "https://github.com/huggingface/transformers"
        assert result_body["metadata"]["paper_source_kind"] == "arxiv"
        assert result_body["metadata"]["paper_fetch_mode"] == "fixture"
        assert result_body["metadata"]["repo_tracer_mode"] == "strategy_chain"


def test_create_analysis_accepts_multipart_pdf_upload() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            data={"repo_url": "https://github.com/microsoft/LoRA"},
            files={
                "paper_file": (
                    "lora-upload.pdf",
                    build_pdf_bytes(
                        title="LoRA Upload",
                        body="Abstract low-rank adaptation modules keep the pretrained backbone frozen.",
                    ),
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 202
        body = response.json()
        job_id = body["job"]["id"]
        assert body["job"]["paper_source"].endswith(".pdf")

        result_response = client.get(f"/api/v1/analyses/{job_id}/result")
        assert result_response.status_code == 200
        result_body = result_response.json()["result"]
        assert result_body["metadata"]["paper_source_kind"] == "pdf_file"
        assert result_body["contributions"]


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
