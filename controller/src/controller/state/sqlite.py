"""SQLite StateBackend implementation using aiosqlite (for local dev)."""
from __future__ import annotations
import json
import aiosqlite
from datetime import datetime, timezone
from controller.models import Thread, Job, ThreadStatus, JobStatus


class SQLiteBackend:
    def __init__(self, db_path: str):
        self._db_path = db_path

    @classmethod
    async def create(cls, dsn: str) -> SQLiteBackend:
        # Strip sqlite:/// prefix
        if dsn.startswith("sqlite:///"):
            db_path = dsn[len("sqlite:///"):]
        else:
            db_path = dsn
        backend = cls(db_path)
        await backend._init_schema()
        return backend

    async def _init_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL DEFAULT '{}',
                    repo_owner TEXT NOT NULL,
                    repo_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    current_job_name TEXT,
                    conversation_history TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    k8s_job_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    task_context TEXT NOT NULL DEFAULT '{}',
                    result TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS locks (
                    thread_id TEXT PRIMARY KEY
                )
            """)
            await db.commit()

    def _now_str(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_dt(self, val: str | None) -> datetime | None:
        if val is None:
            return None
        return datetime.fromisoformat(val)

    async def get_thread(self, thread_id: str) -> Thread | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return Thread(
                id=row["id"],
                source=row["source"],
                source_ref=json.loads(row["source_ref"]),
                repo_owner=row["repo_owner"],
                repo_name=row["repo_name"],
                status=ThreadStatus(row["status"]),
                current_job_name=row["current_job_name"],
                conversation_history=json.loads(row["conversation_history"]),
                created_at=self._parse_dt(row["created_at"]),
                updated_at=self._parse_dt(row["updated_at"]),
            )

    async def upsert_thread(self, thread: Thread) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO threads
                    (id, source, source_ref, repo_owner, repo_name, status,
                     current_job_name, conversation_history, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    current_job_name = excluded.current_job_name,
                    conversation_history = excluded.conversation_history,
                    updated_at = excluded.updated_at
            """, (
                thread.id, thread.source, json.dumps(thread.source_ref),
                thread.repo_owner, thread.repo_name, thread.status.value,
                thread.current_job_name, json.dumps(thread.conversation_history),
                now, now,
            ))
            await db.commit()

    async def update_thread_status(
        self, thread_id: str, status: ThreadStatus, job_name: str | None = None
    ) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            if job_name is not None:
                await db.execute(
                    "UPDATE threads SET status=?, current_job_name=?, updated_at=? WHERE id=?",
                    (status.value, job_name, now, thread_id),
                )
            else:
                await db.execute(
                    "UPDATE threads SET status=?, updated_at=? WHERE id=?",
                    (status.value, now, thread_id),
                )
            await db.commit()

    async def create_job(self, job: Job) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO jobs (id, thread_id, k8s_job_name, status, task_context, started_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.thread_id, job.k8s_job_name, job.status.value,
                json.dumps(job.task_context),
                job.started_at.isoformat() if job.started_at else None,
            ))
            await db.commit()

    def _row_to_job(self, row: aiosqlite.Row) -> Job:
        return Job(
            id=row["id"],
            thread_id=row["thread_id"],
            k8s_job_name=row["k8s_job_name"],
            status=JobStatus(row["status"]),
            task_context=json.loads(row["task_context"]) if row["task_context"] else {},
            result=json.loads(row["result"]) if row["result"] else None,
            started_at=self._parse_dt(row["started_at"]),
            completed_at=self._parse_dt(row["completed_at"]),
        )

    async def get_job(self, job_id: str) -> Job | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    async def get_active_job_for_thread(self, thread_id: str) -> Job | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE thread_id = ? AND status IN ('pending', 'running') LIMIT 1",
                (thread_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    async def get_latest_job_for_thread(self, thread_id: str) -> Job | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE thread_id = ? ORDER BY started_at DESC LIMIT 1",
                (thread_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    async def update_job_status(
        self, job_id: str, status: JobStatus, result: dict | None = None
    ) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            if result is not None:
                await db.execute(
                    "UPDATE jobs SET status=?, result=?, completed_at=? WHERE id=?",
                    (status.value, json.dumps(result), now, job_id),
                )
            else:
                await db.execute(
                    "UPDATE jobs SET status=? WHERE id=?",
                    (status.value, job_id),
                )
            await db.commit()

    async def append_conversation(self, thread_id: str, message: dict) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT conversation_history FROM threads WHERE id = ?", (thread_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return
            history = json.loads(row["conversation_history"])
            history.append(message)
            await db.execute(
                "UPDATE threads SET conversation_history=?, updated_at=? WHERE id=?",
                (json.dumps(history), self._now_str(), thread_id),
            )
            await db.commit()

    async def get_conversation(self, thread_id: str, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT conversation_history FROM threads WHERE id = ?", (thread_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return []
            history = json.loads(row["conversation_history"])
            return history[-limit:]

    async def list_threads(self) -> list[Thread]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM threads ORDER BY created_at DESC") as cur:
                rows = await cur.fetchall()
            return [
                Thread(
                    id=row["id"],
                    source=row["source"],
                    source_ref=json.loads(row["source_ref"]),
                    repo_owner=row["repo_owner"],
                    repo_name=row["repo_name"],
                    status=ThreadStatus(row["status"]),
                    current_job_name=row["current_job_name"],
                    conversation_history=json.loads(row["conversation_history"]),
                    created_at=self._parse_dt(row["created_at"]),
                    updated_at=self._parse_dt(row["updated_at"]),
                )
                for row in rows
            ]

    async def try_acquire_lock(self, thread_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO locks (thread_id) VALUES (?)", (thread_id,)
                )
                await db.commit()
                return True
            except Exception:
                return False

    async def release_lock(self, thread_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM locks WHERE thread_id = ?", (thread_id,))
            await db.commit()
