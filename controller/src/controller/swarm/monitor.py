# controller/src/controller/swarm/monitor.py
"""Swarm Monitor — heartbeat detection and PEL garbage collection."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from controller.models import SwarmGroup, AgentStatus
from controller.state.protocol import StateBackend
from controller.swarm.redis_streams import SwarmRedisStreams

logger = logging.getLogger(__name__)


class SwarmMonitor:
    """Monitors active swarms for agent health and stream maintenance."""

    def __init__(
        self,
        state: StateBackend,
        redis_streams: SwarmRedisStreams,
        heartbeat_timeout: int = 90,
    ):
        self._state = state
        self._streams = redis_streams
        self._heartbeat_timeout = heartbeat_timeout

    async def check_heartbeats(self, group: SwarmGroup) -> None:
        """Check all active agents for stale heartbeats.

        Agents with last_seen older than heartbeat_timeout are marked LOST.
        """
        registry = await self._streams.get_agent_registry(group.id)
        agents = await self._state.list_swarm_agents(group.id)
        now = datetime.now(timezone.utc)

        for agent in agents:
            if agent.status != AgentStatus.ACTIVE:
                continue

            entry = registry.get(agent.id)
            if not entry:
                continue

            last_seen_str = entry.get("last_seen", "")
            if not last_seen_str:
                continue

            try:
                last_seen = datetime.fromisoformat(last_seen_str)
                elapsed = (now - last_seen).total_seconds()
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid last_seen for agent %s: %s", agent.id, last_seen_str
                )
                continue

            if elapsed > self._heartbeat_timeout:
                logger.warning(
                    "Agent %s heartbeat stale (%.0fs > %ds), marking as LOST",
                    agent.id,
                    elapsed,
                    self._heartbeat_timeout,
                )
                await self._state.update_swarm_agent(
                    group.id,
                    agent.id,
                    AgentStatus.LOST,
                    result_summary={
                        "error": "heartbeat_timeout",
                        "last_seen": last_seen_str,
                    },
                )
                await self._streams.update_agent_status(
                    group.id, agent.id, "lost"
                )
