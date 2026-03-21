from __future__ import annotations

import logging

from fastapi import Request

from controller.models import AgentResult, TaskRequest, Thread

logger = logging.getLogger(__name__)


class CLIIntegration:
    """Integration for direct CLI/API access.

    Results are retrieved via GET /api/tasks/{thread_id} rather than
    being pushed to an external service, so report_result is a no-op.
    """

    name: str = "cli"

    async def parse_webhook(self, request: Request) -> TaskRequest | None:
        raise NotImplementedError("CLI integration does not use webhooks")

    async def fetch_context(self, thread: Thread) -> str:
        return ""

    async def report_result(self, thread: Thread, result: AgentResult) -> None:
        logger.info(
            "CLI result for thread %s: exit_code=%s, branch=%s",
            thread.id,
            result.exit_code,
            result.branch,
        )

    async def acknowledge(self, request: Request) -> None:
        pass
