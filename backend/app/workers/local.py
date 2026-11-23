"""In-process task execution for single-machine development (WORKER_MODE=local).

Jobs are still persisted in ``processing_jobs``; on startup :func:`recover_jobs` re-queues
anything that was queued/running when the process stopped.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import get_settings
from app.workers.registry import HEAVY_QUEUES, TaskDef, all_periodic, get_task

log = logging.getLogger(__name__)


class LocalExecutor:
    def __init__(self) -> None:
        settings = get_settings()
        self.heavy = ThreadPoolExecutor(max_workers=max(1, settings.local_worker_threads),
                                        thread_name_prefix="cf-heavy")
        self.light = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cf-light")
        self._stop = threading.Event()
        self._periodic_thread: threading.Thread | None = None
        self._timers: set[threading.Timer] = set()
        self._lock = threading.Lock()

    def _run(self, tdef: TaskDef, kwargs: dict[str, Any]) -> None:
        try:
            tdef.fn(**kwargs)
        except Exception:  # task functions handle their own job bookkeeping
            log.exception("Local task %s failed", tdef.name)

    def _pool(self, tdef: TaskDef) -> ThreadPoolExecutor:
        return self.heavy if tdef.queue in HEAVY_QUEUES else self.light

    def submit(self, tdef: TaskDef, kwargs: dict[str, Any], countdown: float | None = None) -> str:
        task_id = uuid.uuid4().hex
        if self._stop.is_set():
            log.warning("Executor stopped; dropping task %s", tdef.name)
            return task_id
        if countdown and countdown > 0:
            timer = threading.Timer(countdown, lambda: self._pool(tdef).submit(self._run, tdef, kwargs))
            timer.daemon = True
            with self._lock:
                self._timers.add(timer)
            timer.start()
        else:
            self._pool(tdef).submit(self._run, tdef, kwargs)
        return task_id

    def start_periodic(self) -> None:
        if self._periodic_thread is not None:
            return
        periodic = all_periodic()
        next_due = {name: time.monotonic() + 5 for name in periodic}

        def loop() -> None:
            while not self._stop.wait(1.0):
                now = time.monotonic()
                for name, pdef in periodic.items():
                    if now >= next_due[name]:
                        next_due[name] = now + pdef.every_seconds
                        try:
                            self.submit(get_task(pdef.task_name), dict(pdef.kwargs))
                        except Exception:
                            log.exception("Failed to submit periodic task %s", name)

        self._periodic_thread = threading.Thread(target=loop, name="cf-periodic", daemon=True)
        self._periodic_thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for t in self._timers:
                t.cancel()
        self.heavy.shutdown(wait=False, cancel_futures=True)
        self.light.shutdown(wait=False, cancel_futures=True)


_executor: LocalExecutor | None = None
_executor_lock = threading.Lock()


def get_local_executor() -> LocalExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = LocalExecutor()
        return _executor


def shutdown_local_executor() -> None:
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown()
            _executor = None
