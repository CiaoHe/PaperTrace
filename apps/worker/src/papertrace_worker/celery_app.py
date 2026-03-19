from __future__ import annotations

from celery import Celery
from papertrace_core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "papertrace_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["papertrace_worker.tasks"],
)
celery_app.conf.task_always_eager = settings.celery_task_always_eager
celery_app.conf.task_eager_propagates = True
