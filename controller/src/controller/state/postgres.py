"""PostgreSQL StateBackend implementation using asyncpg."""
from __future__ import annotations
import json
import asyncpg
from datetime import datetime, timezone
from controller.models import Thread, Job, ThreadStatus, JobStatus, Artifact, ResultType


class PostgresBackend:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str) -> PostgresBackend:
        pool = await asyncpg.create_pool(dsn)
        backend = cls(pool)
        await backend._init_schema()
        return backend

    async def _init_schema(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_ref JSONB NOT NULL DEFAULT '{}',
                    repo_owner TEXT NOT NULL,
                    repo_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    current_job_name TEXT,
                    conversation_history JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    k8s_job_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    task_context JSONB NOT NULL DEFAULT '{}',
                    result JSONB,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    result_type TEXT NOT NULL,
                    location TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id
                ON task_artifacts(task_id);
            """)

    async def get_thread(self, thread_id: str) -> Thread | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM threads WHERE id = $1", thread_id)
            if not row:
                return None
            return Thread(
                id=row["id"], source=row["source"],
                source_ref=json.loads(row["source_ref"]),
                repo_owner=row["repo_owner"], repo_name=row["repo_name"],
                status=ThreadStatus(row["status"]),
                current_job_name=row["current_job_name"],
                conversation_history=json.loads(row["conversation_history"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
            )

    async def upsert_thread(self, thread: Thread) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO threads (id, source, source_ref, repo_owner, repo_name, status, current_job_name, conversation_history, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    current_job_name = EXCLUDED.current_job_name,
                    conversation_history = EXCLUDED.conversation_history,
                    updated_at = $9
            """, thread.id, thread.source, json.dumps(thread.source_ref),
                thread.repo_owner, thread.repo_name, thread.status.value,
                thread.current_job_name, json.dumps(thread.conversation_history), now)

    async def update_thread_status(self, thread_id: str, status: ThreadStatus, job_name: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            if job_name is not None:
                await conn.execute(
                    "UPDATE threads SET status=$2, current_job_name=$3, updated_at=$4 WHERE id=$1",
                    thread_id, status.value, job_name, now)
            else:
                await conn.execute(
                    "UPDATE threads SET status=$2, updated_at=$3 WHERE id=$1",
                    thread_id, status.value, now)

    async def create_job(self, job: Job) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO jobs (id, thread_id, k8s_job_name, status, task_context, started_at)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, job.id, job.thread_id, job.k8s_job_name, job.status.value,
                json.dumps(job.task_context), job.started_at)

    async def get_job(self, job_id: str) -> Job | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            if not row:
                return None
            return Job(
                id=row["id"], thread_id=row["thread_id"],
                k8s_job_name=row["k8s_job_name"],
                status=JobStatus(row["status"]),
                task_context=json.loads(row["task_context"]) if row["task_context"] else {},
                result=json.loads(row["result"]) if row["result"] else None,
                started_at=row["started_at"], completed_at=row["completed_at"],
            )

    async def get_active_job_for_thread(self, thread_id: str) -> Job | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM jobs WHERE thread_id = $1 AND status IN ('pending', 'running') LIMIT 1",
                thread_id)
            if not row:
                return None
            return Job(
                id=row["id"], thread_id=row["thread_id"],
                k8s_job_name=row["k8s_job_name"],
                status=JobStatus(row["status"]),
                task_context=json.loads(row["task_context"]) if row["task_context"] else {},
                result=json.loads(row["result"]) if row["result"] else None,
                started_at=row["started_at"], completed_at=row["completed_at"],
            )

    async def update_job_status(self, job_id: str, status: JobStatus, result: dict | None = None) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            if result is not None:
                await conn.execute(
                    "UPDATE jobs SET status=$2, result=$3, completed_at=$4 WHERE id=$1",
                    job_id, status.value, json.dumps(result), now)
            else:
                await conn.execute(
                    "UPDATE jobs SET status=$2 WHERE id=$1",
                    job_id, status.value)

    async def append_conversation(self, thread_id: str, message: dict) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE threads
                SET conversation_history = conversation_history || $2::jsonb,
                    updated_at = NOW()
                WHERE id = $1
            """, thread_id, json.dumps([message]))

    async def get_conversation(self, thread_id: str, limit: int = 50) -> list[dict]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT conversation_history FROM threads WHERE id = $1", thread_id)
            if not row:
                return []
            history = json.loads(row["conversation_history"])
            return history[-limit:]

    async def try_acquire_lock(self, thread_id: str) -> bool:
        lock_id = int.from_bytes(thread_id[:8].encode(), "big") & 0x7FFFFFFFFFFFFFFF
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT pg_try_advisory_lock($1)", lock_id)
            return row[0]

    async def release_lock(self, thread_id: str) -> None:
        lock_id = int.from_bytes(thread_id[:8].encode(), "big") & 0x7FFFFFFFFFFFFFFF
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)

    async def create_artifact(self, task_id: str, artifact: Artifact) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO task_artifacts (id, task_id, result_type, location, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
            """, artifact.id, task_id, artifact.result_type.value,
                artifact.location, json.dumps(artifact.metadata))

    async def get_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM task_artifacts WHERE task_id = $1 ORDER BY created_at",
                task_id)
            return [
                Artifact(
                    id=row["id"],
                    result_type=ResultType(row["result_type"]),
                    location=row["location"],
                    # asyncpg auto-deserializes JSONB to dict, no json.loads needed
                    metadata=row["metadata"] if row["metadata"] else {},
                )
                for row in rows
            ]
