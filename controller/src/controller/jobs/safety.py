from __future__ import annotations
import logging
from controller.config import Settings
from controller.models import AgentResult, Thread, ThreadStatus

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
        # Step 1: PARSE (result already parsed by monitor)

        # Step 2: PR CHECK
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

        # Step 3: VALIDATE — anti-stall retry
        if result.commit_count == 0 and result.exit_code == 0:
            if self._settings.retry_on_empty_result and retry_count < self._settings.max_empty_retries:
                logger.info("Empty result for thread %s, retrying (attempt %d)", thread.id, retry_count + 1)
                await self._spawner(thread.id, is_retry=True, retry_count=retry_count + 1)
                return
            else:
                result.stderr = result.stderr or "Agent produced no changes after retries."

        # Step 4: REPORT
        await self._integration.report_result(thread, result)

        # Step 5: CLEANUP
        await self._state.update_thread_status(thread.id, ThreadStatus.IDLE)
        queued = await self._redis_state.drain_messages(thread.id)
        if queued:
            logger.info("Found %d queued messages for thread %s, spawning follow-up", len(queued), thread.id)
