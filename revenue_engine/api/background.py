"""
Background work for operations too slow for a request.

One worker thread, so processing jobs and imports run one at a time. That
keeps writes serialized (SQLite, and a consistent member table while matching)
and matches the single-replica deployment. Revenue jobs persist their status
in revenue_job; spreadsheet imports are tracked in memory and are lost on
restart (clients see 404 on the task and can retry).
"""

import logging
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from core.errors import CoreError
from core.jobs import run_job
from engine.db import get_connection
from models.enums import TaskKind
from models.members import LedgerImportResult, MemberImportResult
from models.misc import Task

log = logging.getLogger("api.background")

_MAX_TASKS_KEPT = 200

ImportResult = MemberImportResult | LedgerImportResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundRunner:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="background")
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._lock = threading.Lock()

    def submit_job(self, job_id: int, manual_payment_id: str | None) -> None:
        self._executor.submit(self._run_job, job_id, manual_payment_id)

    def _run_job(self, job_id: int, manual_payment_id: str | None) -> None:
        conn = get_connection()
        try:
            job = run_job(conn, job_id, manual_payment_id)
            log.info("job finished job_id=%s status=%s", job_id, job.status)
        except Exception:
            # run_job has already recorded the failure on the job row.
            log.exception("job failed job_id=%s", job_id)
        finally:
            conn.close()

    def submit_import(self, kind: TaskKind, work: Callable[[object], ImportResult]) -> Task:
        task = Task(id=uuid.uuid4().hex, kind=kind, status="queued", created_at=_now(),
                    finished_at=None, error=None, member_import=None, ledger_import=None)
        self._put(task)
        self._executor.submit(self._run_import, task.id, work)
        return task

    def _run_import(self, task_id: str, work: Callable[[object], ImportResult]) -> None:
        self._update(task_id, status="running")
        conn = get_connection()
        try:
            result = work(conn)
            field = "member_import" if isinstance(result, MemberImportResult) else "ledger_import"
            self._update(task_id, status="succeeded", finished_at=_now(), **{field: result})
        except CoreError as e:
            self._update(task_id, status="failed", finished_at=_now(), error=e.message)
        except Exception:
            log.exception("import failed task_id=%s", task_id)
            self._update(task_id, status="failed", finished_at=_now(), error="The import failed unexpectedly.")
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _put(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.id] = task
            while len(self._tasks) > _MAX_TASKS_KEPT:
                self._tasks.popitem(last=False)

    def _update(self, task_id: str, **changes) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id] = self._tasks[task_id].model_copy(update=changes)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
