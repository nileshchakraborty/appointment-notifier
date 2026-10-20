from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .models import Artifact, Message, Task, TaskState, TaskStatus, now_utc_iso

LOGGER = logging.getLogger(__name__)


class A2ATaskStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS a2a_tasks (
                    id TEXT PRIMARY KEY,
                    context_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    status_message TEXT,
                    status_details TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS a2a_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    parts TEXT NOT NULL,
                    message_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES a2a_tasks(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS a2a_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    data TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES a2a_tasks(id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_tasks_state ON a2a_tasks(state)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_tasks_context ON a2a_tasks(context_id, updated_at DESC)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_messages_task ON a2a_messages(task_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_artifacts_task ON a2a_artifacts(task_id)"
            )

    def create_task(
        self,
        task_id: str,
        context_id: str,
        initial_message: Message | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        now = now_utc_iso()
        status = TaskStatus(state=TaskState.SUBMITTED, message="Task submitted", timestamp=now)
        meta_json = json.dumps(metadata or {})
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO a2a_tasks (id, context_id, state, status_message, status_details, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, context_id, status.state.value, status.message, json.dumps(status.details), meta_json, now, now),
            )
            if initial_message:
                self.conn.execute(
                    """
                    INSERT INTO a2a_messages (task_id, role, parts, message_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task_id, initial_message.role, json.dumps(initial_message.parts), initial_message.message_id, initial_message.created_at),
                )
        return Task(
            id=task_id,
            context_id=context_id,
            status=status,
            history=[initial_message] if initial_message else [],
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )

    def update_status(self, task_id: str, state: TaskState, message: str | None = None, details: dict[str, Any] | None = None) -> TaskStatus:
        now = now_utc_iso()
        status = TaskStatus(state=state, message=message, details=details, timestamp=now)
        with self.conn:
            self.conn.execute(
                """
                UPDATE a2a_tasks
                SET state = ?, status_message = ?, status_details = ?, updated_at = ?
                WHERE id = ?
                """,
                (state.value, message, json.dumps(details), now, task_id),
            )
        return status

    def append_message(self, task_id: str, message: Message) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO a2a_messages (task_id, role, parts, message_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, message.role, json.dumps(message.parts), message.message_id, message.created_at),
            )
            self.conn.execute("UPDATE a2a_tasks SET updated_at = ? WHERE id = ?", (now_utc_iso(), task_id))

    def add_artifact(self, task_id: str, artifact: Artifact) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO a2a_artifacts (task_id, artifact_id, name, mime_type, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, artifact.artifact_id, artifact.name, artifact.mime_type, json.dumps(artifact.data), artifact.created_at),
            )
            self.conn.execute("UPDATE a2a_tasks SET updated_at = ? WHERE id = ?", (now_utc_iso(), task_id))

    def get_task(self, task_id: str) -> Task | None:
        row = self.conn.execute(
            """
            SELECT id, context_id, state, status_message, status_details, metadata, created_at, updated_at
            FROM a2a_tasks WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if not row:
            return None

        status_details = json.loads(row[4]) if row[4] else None
        status = TaskStatus(
            state=TaskState.from_str(row[2]),
            message=row[3],
            details=status_details,
            timestamp=row[7],
        )

        msg_rows = self.conn.execute(
            "SELECT role, parts, message_id, created_at FROM a2a_messages WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        history = [
            Message(
                role=r[0],
                parts=json.loads(r[1]) if r[1] else [],
                message_id=r[2],
                created_at=r[3],
            )
            for r in msg_rows
        ]

        art_rows = self.conn.execute(
            "SELECT artifact_id, name, mime_type, data, created_at FROM a2a_artifacts WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        artifacts = [
            Artifact(
                artifact_id=r[0],
                name=r[1],
                mime_type=r[2],
                data=json.loads(r[3]) if r[3] else None,
                created_at=r[4],
            )
            for r in art_rows
        ]

        metadata = json.loads(row[5]) if row[5] else {}
        return Task(
            id=row[0],
            context_id=row[1],
            status=status,
            history=history,
            artifacts=artifacts,
            metadata=metadata,
            created_at=row[6],
            updated_at=row[7],
        )

    def list_tasks(
        self,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 20,
    ) -> list[Task]:
        query = "SELECT id FROM a2a_tasks WHERE 1=1"
        params: list[Any] = []
        if context_id:
            query += " AND context_id = ?"
            params.append(context_id)
        if state:
            query += " AND state = ?"
            params.append(state.value)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        tasks = []
        for r in rows:
            t = self.get_task(r[0])
            if t:
                tasks.append(t)
        return tasks
