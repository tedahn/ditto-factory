"""SQLite StateBackend implementation using aiosqlite (for local dev)."""
from __future__ import annotations
import json
import aiosqlite
from datetime import datetime, timezone
from controller.models import (
    Thread, Job, ThreadStatus, JobStatus, Artifact, ResultType,
    SwarmGroup, SwarmAgent, SwarmStatus, AgentStatus,
)


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
                    agent_type TEXT NOT NULL DEFAULT 'general',
                    skills_injected TEXT NOT NULL DEFAULT '[]',
                    resolution_diagnostics TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_agent_type ON jobs(agent_type)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS locks (
                    thread_id TEXT PRIMARY KEY
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    result_type TEXT NOT NULL,
                    location TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id
                ON task_artifacts(task_id)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS swarm_groups (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    completion_strategy TEXT NOT NULL DEFAULT 'all_complete',
                    config TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS swarm_agents (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL REFERENCES swarm_groups(id),
                    role TEXT NOT NULL,
                    agent_type TEXT NOT NULL DEFAULT 'general',
                    task_assignment TEXT NOT NULL,
                    resource_profile TEXT DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    k8s_job_name TEXT,
                    result_summary TEXT DEFAULT '{}',
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_swarm_agents_group_id
                ON swarm_agents(group_id)
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
                INSERT INTO jobs (id, thread_id, k8s_job_name, status, task_context,
                                  agent_type, skills_injected, resolution_diagnostics, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.thread_id, job.k8s_job_name, job.status.value,
                json.dumps(job.task_context),
                job.agent_type,
                json.dumps(job.skills_injected),
                json.dumps(job.resolution_diagnostics) if job.resolution_diagnostics else None,
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
            agent_type=row["agent_type"] if "agent_type" in row.keys() else "general",
            skills_injected=json.loads(row["skills_injected"]) if "skills_injected" in row.keys() and row["skills_injected"] else [],
            resolution_diagnostics=json.loads(row["resolution_diagnostics"]) if "resolution_diagnostics" in row.keys() and row["resolution_diagnostics"] else None,
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

    async def list_jobs_by_agent_type(self, agent_type: str, limit: int = 20) -> list[Job]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE agent_type = ? ORDER BY started_at DESC LIMIT ?",
                (agent_type, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def count_jobs_by_agent_type(self, agent_type: str) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM jobs WHERE agent_type = ?",
                (agent_type,),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

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

    async def create_artifact(self, task_id: str, artifact: Artifact) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO task_artifacts (id, task_id, result_type, location, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                artifact.id, task_id, artifact.result_type.value,
                artifact.location, json.dumps(artifact.metadata),
                self._now_str(),
            ))
            await db.commit()

    async def get_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ) as cur:
                rows = await cur.fetchall()
            return [
                Artifact(
                    id=row["id"],
                    result_type=ResultType(row["result_type"]),
                    location=row["location"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
                for row in rows
            ]

    # ── Swarm group / agent operations ──────────────────────────────

    async def create_swarm_group(self, group: SwarmGroup) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO swarm_groups (id, thread_id, status, completion_strategy, config, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                group.id, group.thread_id, group.status.value,
                group.completion_strategy, json.dumps(group.config),
                group.created_at.isoformat() if group.created_at else self._now_str(),
            ))
            await db.commit()

    async def get_swarm_group(self, group_id: str) -> SwarmGroup | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM swarm_groups WHERE id = ?", (group_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            return SwarmGroup(
                id=row["id"], thread_id=row["thread_id"],
                status=SwarmStatus(row["status"]),
                completion_strategy=row["completion_strategy"],
                config=json.loads(row["config"]) if row["config"] else {},
                created_at=self._parse_dt(row["created_at"]),
                completed_at=self._parse_dt(row["completed_at"]),
            )

    async def update_swarm_status(self, group_id: str, status: SwarmStatus) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            if status in (SwarmStatus.COMPLETED, SwarmStatus.FAILED):
                await db.execute(
                    "UPDATE swarm_groups SET status=?, completed_at=? WHERE id=?",
                    (status.value, now, group_id))
            else:
                await db.execute(
                    "UPDATE swarm_groups SET status=? WHERE id=?",
                    (status.value, group_id))
            await db.commit()

    async def create_swarm_agent(self, agent: SwarmAgent) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO swarm_agents (id, group_id, role, agent_type, task_assignment, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                agent.id, agent.group_id, agent.role,
                agent.agent_type, agent.task_assignment, agent.status.value,
            ))
            await db.commit()

    async def update_swarm_agent(
        self, group_id: str, agent_id: str, status: AgentStatus,
        result_summary: dict | None = None,
    ) -> None:
        now = self._now_str()
        async with aiosqlite.connect(self._db_path) as db:
            if result_summary is not None:
                await db.execute(
                    "UPDATE swarm_agents SET status=?, result_summary=?, completed_at=? WHERE id=? AND group_id=?",
                    (status.value, json.dumps(result_summary), now, agent_id, group_id))
            else:
                await db.execute(
                    "UPDATE swarm_agents SET status=? WHERE id=? AND group_id=?",
                    (status.value, agent_id, group_id))
            await db.commit()

    async def list_swarm_agents(self, group_id: str) -> list[SwarmAgent]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM swarm_agents WHERE group_id = ?", (group_id,)
            ) as cur:
                rows = await cur.fetchall()
            return [
                SwarmAgent(
                    id=row["id"], group_id=row["group_id"],
                    role=row["role"], agent_type=row["agent_type"],
                    task_assignment=row["task_assignment"],
                    status=AgentStatus(row["status"]),
                    k8s_job_name=row["k8s_job_name"],
                    result_summary=json.loads(row["result_summary"]) if row["result_summary"] else {},
                )
                for row in rows
            ]

    async def list_swarm_groups(self, status_in: list[SwarmStatus] | None = None) -> list[SwarmGroup]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if status_in:
                placeholders = ",".join("?" for _ in status_in)
                values = [s.value for s in status_in]
                query = f"SELECT * FROM swarm_groups WHERE status IN ({placeholders})"
                async with db.execute(query, values) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute("SELECT * FROM swarm_groups") as cur:
                    rows = await cur.fetchall()
            return [
                SwarmGroup(
                    id=row["id"], thread_id=row["thread_id"],
                    status=SwarmStatus(row["status"]),
                    completion_strategy=row["completion_strategy"],
                    config=json.loads(row["config"]) if row["config"] else {},
                    created_at=self._parse_dt(row["created_at"]),
                    completed_at=self._parse_dt(row["completed_at"]),
                )
                for row in rows
            ]
