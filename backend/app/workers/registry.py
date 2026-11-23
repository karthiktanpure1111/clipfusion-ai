"""Task registry shared by the Celery worker and the in-process local executor.

Define tasks once with :func:`task`; dispatch with :func:`enqueue`. Task functions take
JSON-serialisable keyword arguments only (pass UUIDs as strings).

    from app.workers.registry import task, periodic, enqueue

    @task("render_clip", queue="render")
    def render_clip(job_id: str) -> None: ...

    periodic("dispatch_due_publications", every_seconds=60)

Queues: ``analysis`` and ``render`` are CPU heavy; ``publish`` and ``default`` are I/O bound.
"""

from __future__ import annotations

import importlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)

TASK_MODULES = [
    "app.workers.tasks_processing",
    "app.workers.tasks_clips",
    "app.workers.tasks_publishing",
    "app.workers.tasks_maintenance",
]
HEAVY_QUEUES = {"analysis", "render"}


@dataclass(frozen=True)
class TaskDef:
    name: str
    fn: Callable[..., Any]
    queue: str


@dataclass(frozen=True)
class PeriodicDef:
    task_name: str
    every_seconds: int
    kwargs: tuple = ()


_TASKS: dict[str, TaskDef] = {}
_PERIODIC: dict[str, PeriodicDef] = {}
_JOB_TASKS: dict[str, str] = {}  # JobType value -> task name (used by the stale-job reaper)
_loaded = False


def task(name: str, queue: str = "default", job_type: str | None = None) -> Callable:
    """Register ``fn`` as a background task. ``job_type`` links a ProcessingJob type to it."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _TASKS and _TASKS[name].fn is not fn:
            raise RuntimeError(f"Duplicate task name: {name}")
        _TASKS[name] = TaskDef(name=name, fn=fn, queue=queue)
        if job_type:
            _JOB_TASKS[job_type] = name
        return fn

    return deco


def periodic(task_name: str, every_seconds: int, **kwargs: Any) -> None:
    _PERIODIC[task_name] = PeriodicDef(task_name=task_name, every_seconds=every_seconds,
                                       kwargs=tuple(sorted(kwargs.items())))


def load_task_modules() -> None:
    global _loaded
    if _loaded:
        return
    for mod in TASK_MODULES:
        importlib.import_module(mod)
    _loaded = True


def get_task(name: str) -> TaskDef:
    load_task_modules()
    try:
        return _TASKS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown task: {name}") from exc


def all_tasks() -> dict[str, TaskDef]:
    load_task_modules()
    return dict(_TASKS)


def all_periodic() -> dict[str, PeriodicDef]:
    load_task_modules()
    return dict(_PERIODIC)


def task_for_job_type(job_type: str) -> str | None:
    load_task_modules()
    return _JOB_TASKS.get(job_type)


def enqueue(name: str, *, countdown: float | None = None, **kwargs: Any) -> str:
    """Dispatch a registered task. Returns a task id."""
    tdef = get_task(name)
    settings = get_settings()
    if settings.worker_mode == "local":
        from app.workers.local import get_local_executor

        return get_local_executor().submit(tdef, kwargs, countdown=countdown)
    from app.workers.celery_app import celery_app

    result = celery_app.send_task(tdef.name, kwargs=kwargs, queue=tdef.queue,
                                  countdown=countdown if countdown and countdown > 0 else None)
    return result.id or uuid.uuid4().hex
