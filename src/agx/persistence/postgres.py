"""Postgres persistence for runs and events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import psycopg
from psycopg.rows import dict_row


@dataclass
class RunRecord:
    run_id: str
    project: str
    engine: str
    config_path: str
    requested_path: str
    total_tasks: int
    completed_tasks: int
    completed: bool
    stop_requested: bool
    started_at: float
    updated_at: float


class PostgresRunStore:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._ensure_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.db_url, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agx_runs (
                    run_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    config_path TEXT NOT NULL,
                    requested_path TEXT NOT NULL,
                    total_tasks INTEGER NOT NULL,
                    completed_tasks INTEGER NOT NULL,
                    completed BOOLEAN NOT NULL,
                    stop_requested BOOLEAN NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agx_run_events (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES agx_runs(run_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    event JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS agx_run_events_run_id_idx ON agx_run_events(run_id)"
            )
            conn.commit()

    def create_run(
        self,
        *,
        run_id: str,
        project: str,
        engine: str,
        config_path: str,
        requested_path: str,
        total_tasks: int,
        completed_tasks: int,
        completed: bool,
        stop_requested: bool,
        started_at: float,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agx_runs (
                    run_id, project, engine, config_path, requested_path,
                    total_tasks, completed_tasks, completed, stop_requested,
                    started_at, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    project=excluded.project,
                    engine=excluded.engine,
                    config_path=excluded.config_path,
                    requested_path=excluded.requested_path,
                    total_tasks=excluded.total_tasks,
                    completed_tasks=excluded.completed_tasks,
                    completed=excluded.completed,
                    stop_requested=excluded.stop_requested,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    project,
                    engine,
                    config_path,
                    requested_path,
                    total_tasks,
                    completed_tasks,
                    completed,
                    stop_requested,
                    datetime.fromtimestamp(started_at, tz=timezone.utc),
                    now,
                ),
            )
            conn.commit()

    def update_run(
        self,
        *,
        run_id: str,
        completed_tasks: int,
        completed: bool,
        stop_requested: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agx_runs
                SET completed_tasks=%s,
                    completed=%s,
                    stop_requested=%s,
                    updated_at=%s
                WHERE run_id=%s
                """,
                (completed_tasks, completed, stop_requested, now, run_id),
            )
            conn.commit()

    def append_event(self, run_id: str, seq: int, event: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agx_run_events (run_id, seq, event, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (run_id, seq, json.dumps(event), now),
            )
            conn.commit()

    def list_runs(self, limit: int = 200) -> List[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, project, engine, config_path, requested_path,
                       total_tasks, completed_tasks, completed, stop_requested,
                       started_at, updated_at
                FROM agx_runs
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        records: List[RunRecord] = []
        for row in rows:
            records.append(
                RunRecord(
                    run_id=row["run_id"],
                    project=row["project"],
                    engine=row["engine"],
                    config_path=row["config_path"],
                    requested_path=row["requested_path"],
                    total_tasks=int(row["total_tasks"]),
                    completed_tasks=int(row["completed_tasks"]),
                    completed=bool(row["completed"]),
                    stop_requested=bool(row["stop_requested"]),
                    started_at=row["started_at"].timestamp(),
                    updated_at=row["updated_at"].timestamp(),
                )
            )
        return records

    def list_events(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event
                FROM agx_run_events
                WHERE run_id = %s
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()
        events: List[Dict[str, Any]] = []
        for row in rows:
            event = row["event"]
            if isinstance(event, dict):
                events.append(event)
            else:
                try:
                    events.append(json.loads(event))
                except Exception:
                    continue
        return events
