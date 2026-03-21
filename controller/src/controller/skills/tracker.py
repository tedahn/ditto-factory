"""Track skill usage and outcomes for performance analytics."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import aiosqlite

from controller.skills.models import SkillMetrics

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

    # ------------------------------------------------------------------
    # Phase 3: Metrics & Learning Loop
    # ------------------------------------------------------------------

    async def get_skill_metrics(self, skill_slug: str) -> SkillMetrics | None:
        """Compute aggregate metrics for a skill from skill_usage table."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN su.exit_code = 0 THEN 1 ELSE 0 END) AS successes,
                       AVG(su.commit_count) AS avg_commits,
                       SUM(CASE WHEN su.pr_created THEN 1 ELSE 0 END) AS prs
                   FROM skill_usage su
                   JOIN skills s ON su.skill_id = s.id
                   WHERE s.slug = ? AND su.completed_at IS NOT NULL""",
                (skill_slug,),
            )
            if not row or row[0][0] == 0:
                return None
            r = row[0]
            total = r[0]
            successes = r[1] or 0
            avg_commits = r[2] or 0.0
            prs = r[3] or 0
            return SkillMetrics(
                skill_slug=skill_slug,
                usage_count=total,
                success_rate=successes / total if total else 0.0,
                avg_commits=float(avg_commits),
                pr_creation_rate=prs / total if total else 0.0,
            )

    async def get_all_metrics(self) -> dict[str, SkillMetrics]:
        """Get metrics for all skills. Returns dict keyed by skill slug."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                """SELECT
                       s.slug,
                       COUNT(*) AS total,
                       SUM(CASE WHEN su.exit_code = 0 THEN 1 ELSE 0 END) AS successes,
                       AVG(su.commit_count) AS avg_commits,
                       SUM(CASE WHEN su.pr_created THEN 1 ELSE 0 END) AS prs
                   FROM skill_usage su
                   JOIN skills s ON su.skill_id = s.id
                   WHERE su.completed_at IS NOT NULL
                   GROUP BY s.slug""",
            )
            result: dict[str, SkillMetrics] = {}
            for r in rows:
                slug = r[0]
                total = r[1]
                successes = r[2] or 0
                avg_commits = r[3] or 0.0
                prs = r[4] or 0
                result[slug] = SkillMetrics(
                    skill_slug=slug,
                    usage_count=total,
                    success_rate=successes / total if total else 0.0,
                    avg_commits=float(avg_commits),
                    pr_creation_rate=prs / total if total else 0.0,
                )
            return result

    async def compute_boost(self, skill_id: str, base_score: float) -> float:
        """Apply performance-based ranking boost/penalty.

        - Skills with >80% success rate: up to +10% boost
        - Skills with <40% success rate: up to -10% penalty
        - Skills with <10 usage events: no boost (insufficient data)

        Uses linear interpolation between -0.1 and +0.1 based on success rate.
        Returns adjusted score.
        """
        async with aiosqlite.connect(self._db_path) as db:
            row = await db.execute_fetchall(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) AS successes
                   FROM skill_usage
                   WHERE skill_id = ? AND completed_at IS NOT NULL""",
                (skill_id,),
            )
            if not row or row[0][0] < 10:
                return base_score

            total = row[0][0]
            successes = row[0][1] or 0
            success_rate = successes / total

            # Linear interpolation outside the 40-80% neutral zone
            if success_rate > 0.8:
                # Boost: linearly from 0 at 80% to +0.1 at 100%
                boost = ((success_rate - 0.8) / 0.2) * 0.1
            elif success_rate < 0.4:
                # Penalty: linearly from 0 at 40% to -0.1 at 0%
                boost = -((0.4 - success_rate) / 0.4) * 0.1
            else:
                boost = 0.0

            return base_score + boost

    async def get_trend(self, skill_slug: str, days: int = 7) -> dict:
        """Get usage trend: last N days vs previous N days.

        Returns: {"current": {"usage": int, "success_rate": float},
                  "previous": {"usage": int, "success_rate": float}}
        """
        now = datetime.now(timezone.utc)
        current_start = (now - timedelta(days=days)).isoformat()
        previous_start = (now - timedelta(days=days * 2)).isoformat()
        now_iso = now.isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            # Current period
            cur = await db.execute_fetchall(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN su.exit_code = 0 THEN 1 ELSE 0 END) AS successes
                   FROM skill_usage su
                   JOIN skills s ON su.skill_id = s.id
                   WHERE s.slug = ? AND su.completed_at IS NOT NULL
                     AND su.injected_at >= ? AND su.injected_at < ?""",
                (skill_slug, current_start, now_iso),
            )
            cur_total = cur[0][0] if cur else 0
            cur_successes = (cur[0][1] or 0) if cur else 0

            # Previous period
            prev = await db.execute_fetchall(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN su.exit_code = 0 THEN 1 ELSE 0 END) AS successes
                   FROM skill_usage su
                   JOIN skills s ON su.skill_id = s.id
                   WHERE s.slug = ? AND su.completed_at IS NOT NULL
                     AND su.injected_at >= ? AND su.injected_at < ?""",
                (skill_slug, previous_start, current_start),
            )
            prev_total = prev[0][0] if prev else 0
            prev_successes = (prev[0][1] or 0) if prev else 0

        return {
            "current": {
                "usage": cur_total,
                "success_rate": cur_successes / cur_total if cur_total else 0.0,
            },
            "previous": {
                "usage": prev_total,
                "success_rate": prev_successes / prev_total if prev_total else 0.0,
            },
        }
