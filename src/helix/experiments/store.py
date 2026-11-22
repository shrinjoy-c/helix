"""SQLite-backed persistence for `ExperimentRecord`s.

Phase-0.1 scope: a single `experiments` table storing each record as JSON
alongside a few indexed columns for querying. Later phases (agent
orchestration event logs, benchmark aggregation) build on top of this
without changing the schema's core contract: every experiment is
reproducible from `dataset_config` alone.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from helix.config.models import ExperimentRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    runtime_seconds REAL NOT NULL,
    record_json TEXT NOT NULL
);
"""


class ExperimentStore:
    """Thread-safe: a single connection guarded by a lock, so one store
    instance can be shared across the API server's background-task
    threads without each caller needing to know that detail."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def save(self, record: ExperimentRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO experiments
                    (experiment_id, dataset_id, pipeline_name, status, created_at, runtime_seconds, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    dataset_id=excluded.dataset_id,
                    pipeline_name=excluded.pipeline_name,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    runtime_seconds=excluded.runtime_seconds,
                    record_json=excluded.record_json
                """,
                (
                    record.experiment_id,
                    record.dataset_id,
                    record.pipeline_name,
                    record.status,
                    record.created_at,
                    record.runtime_seconds,
                    record.model_dump_json(),
                ),
            )
            self._conn.commit()

    def load(self, experiment_id: str) -> ExperimentRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            return None
        return ExperimentRecord.model_validate_json(row[0])

    def list_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT experiment_id FROM experiments ORDER BY created_at").fetchall()
        return [r[0] for r in rows]

    def list_by_dataset(self, dataset_id: str) -> list[ExperimentRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_json FROM experiments WHERE dataset_id = ? ORDER BY created_at", (dataset_id,)
            ).fetchall()
        return [ExperimentRecord.model_validate_json(r[0]) for r in rows]

    def list_all(self) -> list[ExperimentRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT record_json FROM experiments ORDER BY created_at").fetchall()
        return [ExperimentRecord.model_validate_json(r[0]) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ExperimentStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
