"""In-memory background job tracking for the API server.

Experiment and benchmark-sweep runs are launched via FastAPI
`BackgroundTasks` and may take anywhere from milliseconds (MockProvider)
to much longer (a real model provider) - the frontend polls a job's
status rather than blocking an HTTP request on it. Jobs are not
persisted (a server restart loses in-flight job status, not the
underlying experiment results, which are already safely in
`ExperimentStore` by the time a job is marked completed).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "running", "completed", "failed"]
JobType = Literal["experiment", "benchmark_sweep"]


class JobRecord(BaseModel):
    job_id: str
    job_type: JobType
    status: JobStatus = "pending"
    created_at: str
    completed_at: Optional[str] = None
    experiment_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}

    def create(self, job_type: JobType) -> JobRecord:
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            job_type=job_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def mark_running(self, job_id: str) -> None:
        self._update(job_id, status="running")

    def mark_completed(self, job_id: str, experiment_ids: list[str]) -> None:
        self._update(
            job_id, status="completed", experiment_ids=experiment_ids,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", error=error, completed_at=datetime.now(timezone.utc).isoformat())

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = job.model_copy(update=fields)

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at)
