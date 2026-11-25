"""Background job tracking for the API server, persisted to SQLite.

Experiment and benchmark-sweep runs are launched via FastAPI
`BackgroundTasks` and may take anywhere from milliseconds (MockProvider)
to much longer (a real model provider) - the frontend polls a job's
status rather than blocking an HTTP request on it.

Persisted in the same SQLite file as `ExperimentStore` (a separate table),
so a completed job's status and experiment_ids survive an API server
restart - only genuinely in-flight jobs (status "pending"/"running" at the
moment of a restart) are lost, because the Python task actually running
them is gone; there is nothing to resume. That is a correct, unavoidable
consequence of a restart, not a persistence gap.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "running", "completed", "failed"]
JobType = Literal["experiment", "benchmark_sweep"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    experiment_ids_json TEXT NOT NULL,
    error TEXT
);
"""


class JobRecord(BaseModel):
    job_id: str
    job_type: JobType
    status: JobStatus = "pending"
    created_at: str
    completed_at: Optional[str] = None
    experiment_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None


def _row_to_record(row: tuple) -> JobRecord:
    job_id, job_type, status, created_at, completed_at, experiment_ids_json, error = row
    return JobRecord(
        job_id=job_id, job_type=job_type, status=status, created_at=created_at,
        completed_at=completed_at, experiment_ids=json.loads(experiment_ids_json), error=error,
    )


class JobRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def create(self, job_type: JobType) -> JobRecord:
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            job_type=job_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save(job)
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

    def _save(self, job: JobRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, job_type, status, created_at, completed_at, experiment_ids_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    experiment_ids_json=excluded.experiment_ids_json,
                    error=excluded.error
                """,
                (
                    job.job_id, job.job_type, job.status, job.created_at,
                    job.completed_at, json.dumps(job.experiment_ids), job.error,
                ),
            )
            self._conn.commit()

    def _update(self, job_id: str, **fields) -> None:
        job = self.get(job_id)
        if job is None:
            return
        self._save(job.model_copy(update=fields))

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, job_type, status, created_at, completed_at, experiment_ids_json, error "
                "FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list(self) -> list[JobRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, job_type, status, created_at, completed_at, experiment_ids_json, error "
                "FROM jobs ORDER BY created_at"
            ).fetchall()
        return [_row_to_record(row) for row in rows]
