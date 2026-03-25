from __future__ import annotations
import logging
from controller.config import Settings
from controller.models import AgentResult, Thread, ThreadStatus, ResultType
from controller.jobs.validators import get_validator

logger = logging.getLogger(__name__)


class SafetyPipeline:
    def __init__(self, settings, state_backend, redis_state, integration, spawner, github_client):
        self._settings = settings
        self._state = state_backend
        self._redis_state = redis_state
        self._integration = integration
        self._spawner = spawner
        self._github_client = github_client

    async def process(self, thread: Thread, result: AgentResult, retry_count: int = 0) -> None:
        result_type = result.result_type

        if result_type == ResultType.PULL_REQUEST:
            await self._process_pr(thread, result, retry_count)
        elif result_type == ResultType.REPORT:
            await self._process_report(thread, result)
        else:
            logger.warning(
                "Unhandled result type %s for thread %s, falling back to PR path",
                result_type, thread.id,
            )
            await self._process_pr(thread, result, retry_count)

    async def _process_pr(self, thread: Thread, result: AgentResult, retry_count: int) -> None:
        """Original PR-based safety pipeline (preserved behavior)."""
        validator = get_validator(ResultType.PULL_REQUEST)
        outcome = await validator.validate(result, thread)

        if result.commit_count > 0 and not result.pr_url and self._settings.auto_open_pr:
            try:
                pr_url = await self._github_client.create_pr(
                    owner=thread.repo_owner,
                    repo=thread.repo_name,
                    branch=result.branch,
                    title=f"[Ditto Factory] Changes for {thread.id[:8]}",
                    body=f"Automated PR created by Ditto Factory agent.\n\nThread: `{thread.id}`",
                )
                result.pr_url = pr_url
            except Exception:
                logger.exception("Failed to auto-create PR for thread %s", thread.id)

        if not outcome.approved and result.exit_code == 0:
            if self._settings.retry_on_empty_result and retry_count < self._settings.max_empty_retries:
                logger.info("Empty result for thread %s, retrying (attempt %d)", thread.id, retry_count + 1)
                await self._spawner(thread.id, is_retry=True, retry_count=retry_count + 1)
                return
            else:
                result.stderr = result.stderr or "Agent produced no changes after retries."

        await self._integration.report_result(thread, result)

        await self._state.update_thread_status(thread.id, ThreadStatus.IDLE)
        queued = await self._redis_state.drain_messages(thread.id)
        if queued:
            logger.info("Found %d queued messages for thread %s, spawning follow-up", len(queued), thread.id)

    async def _process_report(self, thread: Thread, result: AgentResult) -> None:
        """Report/analysis result pipeline — no PR, no anti-stall retry."""
        validator = get_validator(ResultType.REPORT)
        outcome = await validator.validate(result, thread)

        if not outcome.approved:
            logger.warning("Report validation failed for thread %s: %s", thread.id, outcome.reason)
            result.stderr = result.stderr or outcome.reason or "Report validation failed."

        for artifact in result.artifacts:
            try:
                await self._state.create_artifact(task_id=thread.id, artifact=artifact)
            except Exception:
                logger.exception("Failed to store artifact %s for thread %s", artifact.id, thread.id)

        await self._integration.report_result(thread, result)

        await self._state.update_thread_status(thread.id, ThreadStatus.IDLE)
        queued = await self._redis_state.drain_messages(thread.id)
        if queued:
            logger.info("Found %d queued messages for thread %s, spawning follow-up", len(queued), thread.id)
