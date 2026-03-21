"""MCP Gateway scope management.

Sets per-session tool scoping in Redis so the gateway knows which tools
to expose for each agent session.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controller.config import Settings
    from controller.state.redis_state import RedisState

logger = logging.getLogger(__name__)


class GatewayManager:
    """Manages per-session gateway scopes stored in Redis."""

    SCOPE_TTL = 7200  # 2 hours

    def __init__(self, redis_state: RedisState, settings: Settings) -> None:
        self._redis = redis_state
        self._settings = settings
        self._gateway_url: str = getattr(settings, "gateway_url", "")

    # ------------------------------------------------------------------
    # Scope CRUD
    # ------------------------------------------------------------------

    async def set_scope(self, thread_id: str, tools: list[str]) -> None:
        """Set allowed tools for a gateway session."""
        await self._redis._redis.set(
            f"gateway_scope:{thread_id}",
            json.dumps(tools),
            ex=self.SCOPE_TTL,
        )
        logger.info("Set gateway scope for %s: %s", thread_id, tools)

    async def clear_scope(self, thread_id: str) -> None:
        """Remove gateway scope when agent completes."""
        await self._redis._redis.delete(f"gateway_scope:{thread_id}")
        logger.info("Cleared gateway scope for %s", thread_id)

    # ------------------------------------------------------------------
    # Config generation
    # ------------------------------------------------------------------

    def get_gateway_mcp_config(self, thread_id: str) -> dict:
        """Generate mcp.json config entry pointing to the gateway.

        Returns an empty dict when the gateway is not configured, which
        signals to the caller that no gateway injection is needed.
        """
        if not self._gateway_url:
            return {}
        return {
            "gateway": {
                "url": f"{self._gateway_url}/sse?thread_id={thread_id}",
                "transport": "sse",
            }
        }

    # ------------------------------------------------------------------
    # Skill-to-tool mapping
    # ------------------------------------------------------------------

    async def scope_from_skills(self, skills: list) -> list[str]:
        """Derive gateway tool names from skill requirements.

        Skills can declare gateway tool dependencies via tags prefixed
        with ``gw:``.  For example, a skill tagged ``gw:db-query`` will
        cause the ``db-query`` tool to be included in the session scope.

        Returns an empty list when no gateway tools are requested, which
        means the gateway is not needed for this session.
        """
        tools: set[str] = set()
        for skill in skills:
            for tag in getattr(skill, "tags", None) or []:
                if tag.startswith("gw:"):
                    tools.add(tag[3:])  # strip "gw:" prefix
        return sorted(tools)
