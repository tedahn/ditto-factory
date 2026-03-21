"""Track skill usage and outcomes for performance analytics."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from controller.models import AgentResult, TaskRequest
    from controller.skills.models import Skill

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Records skill injection events and their outcomes."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def record_injection(
        self,
        skills: list[Skill],
        thread_id: str,
        job_id: str,
        task_request: TaskRequest,
        task_embedding: list[float] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            for skill in skills:
                usage_id = uuid.uuid4().hex
                await db.execute(
                    """INSERT INTO skill_usage
                       (id, skill_id, thread_id, job_id, task_source,
                        repo_owner, repo_name, injected_at)
                       VALUES (?,?,?,?,?, ?,?,?)""",
                    (
                        usage_id,
                        skill.id,
                        thread_id,
                        job_id,
                        task_request.source,
                        task_request.repo_owner,
                        task_request.repo_name,
                        now,
                    ),
                )
            await db.commit()

    async def record_outcome(
        self,
        thread_id: str,
        job_id: str,
        result: AgentResult,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE skill_usage SET
                   exit_code = ?, commit_count = ?, pr_created = ?, completed_at = ?
                   WHERE thread_id = ? AND job_id = ?""",
                (
                    result.exit_code,
                    result.commit_count,
                    1 if result.pr_url else 0,
                    now,
                    thread_id,
                    job_id,
                ),
            )
            await db.commit()
