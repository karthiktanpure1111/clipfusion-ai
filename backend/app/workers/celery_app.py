"""Celery application (WORKER_MODE=celery).

Run (Linux/macOS)::

    celery -A app.workers.celery_app worker -Q analysis,render,publish,default -c 2
    celery -A app.workers.celery_app beat

On Windows use ``--pool=solo`` (or ``--pool=threads``) for the worker.
The beat process only ticks periodic tasks; all schedule/queue state lives in PostgreSQL,
so restarting beat or workers never loses scheduled publications.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.registry import all_periodic, all_tasks

settings = get_settings()

celery_app = Celery("clipfusion", broker=settings.broker_url, backend=settings.result_backend)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,                 # re-deliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # long tasks: don't hoard messages
    task_default_queue="default",
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 6 * 3600},  # long renders/analyses
)


@setup_logging.connect
def _setup_logging(**_kwargs):  # noqa: ANN003
    configure_logging()


for _tdef in all_tasks().values():
    celery_app.task(name=_tdef.name, queue=_tdef.queue)(_tdef.fn)

celery_app.conf.beat_schedule = {
    name: {"task": p.task_name, "schedule": float(p.every_seconds), "kwargs": dict(p.kwargs)}
    for name, p in all_periodic().items()
}
