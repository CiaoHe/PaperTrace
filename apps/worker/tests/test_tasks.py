from __future__ import annotations

import os
from pathlib import Path

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path('.local/test-worker.db').resolve()}"

from papertrace_core.models import AnalysisRequest
from papertrace_core.storage import create_job, get_job_result, get_job_summary, init_db
from papertrace_worker.tasks import enqueue_analysis


def test_enqueue_analysis_persists_result() -> None:
    init_db()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    job = create_job(request)
    job_id = job.id

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

    result = get_job_result(job_id)
    assert result is not None
    assert result.case_slug == "flash-attention"
