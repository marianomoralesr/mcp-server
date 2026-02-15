"""
job_manager.py — Sistema de background jobs con asyncio.
In-memory, single-user. Soporta progreso, cancelación y listado.
"""

import asyncio
import time
import uuid
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobInfo(BaseModel):
    id: str
    type: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    progress_detail: str = ""
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    params: dict = Field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None


class JobManager:
    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self._jobs: dict[str, JobInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, bool] = {}

    def create_job(self, job_type: str, params: dict | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._jobs[job_id] = JobInfo(
            id=job_id,
            type=job_type,
            params=params or {},
        )
        self._cancel_flags[job_id] = False
        return job_id

    def start_job(self, job_id: str, coro_factory: Callable[[], Coroutine]) -> None:
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        running = sum(
            1 for j in self._jobs.values()
            if j.status == JobStatus.RUNNING
        )
        if running >= self.max_concurrent:
            raise RuntimeError(f"Max concurrent jobs ({self.max_concurrent}) reached")

        async def _run():
            try:
                job.status = JobStatus.RUNNING
                job.started_at = time.time()
                result = await coro_factory()
                if self._cancel_flags.get(job_id):
                    job.status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.COMPLETED
                    job.result = result
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
            finally:
                job.completed_at = time.time()
                self._tasks.pop(job_id, None)
                self._cancel_flags.pop(job_id, None)

        task = asyncio.create_task(_run())
        self._tasks[job_id] = task

    def update_progress(self, job_id: str, progress: int, detail: str = "") -> None:
        job = self._jobs.get(job_id)
        if job:
            job.progress = min(100, max(0, progress))
            job.progress_detail = detail

    def is_cancelled(self, job_id: str) -> bool:
        return self._cancel_flags.get(job_id, False)

    def complete_job(self, job_id: str, result: dict) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.COMPLETED
            job.result = result
            job.progress = 100
            job.completed_at = time.time()

    def fail_job(self, job_id: str, error: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.FAILED
            job.error = error
            job.completed_at = time.time()

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        self._cancel_flags[job_id] = True
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = JobStatus.CANCELLED
        job.completed_at = time.time()
        return True

    def get_job(self, job_id: str) -> Optional[JobInfo]:
        return self._jobs.get(job_id)

    def list_jobs(self, job_type: str | None = None, limit: int = 20) -> list[JobInfo]:
        jobs = list(self._jobs.values())
        if job_type:
            jobs = [j for j in jobs if j.type == job_type]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]
