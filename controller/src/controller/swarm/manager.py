"""SwarmManager — lifecycle management for agent swarms."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from controller.config import Settings
from controller.models import (
    SwarmGroup, SwarmAgent, SwarmStatus, AgentStatus, ROLE_PROFILES,
)
from controller.state.protocol import StateBackend
from controller.swarm.redis_streams import SwarmRedisStreams
from controller.swarm.async_spawner import AsyncJobSpawner
from controller.jobs.spawner import JobSpawner

logger = logging.getLogger(__name__)


@dataclass
class SwarmResult:
    """Result of a completed swarm."""
    group_id: str
    agents: list[SwarmAgent] = field(default_factory=list)
    audit_trail: list[dict] = field(default_factory=list)


class SwarmManager:
    """Manages the full lifecycle of agent swarms."""

    def __init__(
        self,
        settings: Settings,
        state: StateBackend,
        redis_streams: SwarmRedisStreams,
        async_spawner: AsyncJobSpawner,
        spawner: JobSpawner,
    ):
        self._settings = settings
        self._state = state
        self._streams = redis_streams
        self._async_spawner = async_spawner
        self._spawner = spawner

    async def create_swarm(
        self,
        thread_id: str,
        agents: list[SwarmAgent],
        config: dict,
    ) -> SwarmGroup:
        """Create a new swarm group, set up Redis, and spawn agents."""
        group_id = f"swarm-{uuid.uuid4().hex[:12]}"

        # Assign group_id and resource profiles to agents
        for agent in agents:
            agent.group_id = group_id
            if agent.resource_profile is None:
                agent.resource_profile = ROLE_PROFILES.get(
                    agent.role, ROLE_PROFILES["default"]
                )

        group = SwarmGroup(
            id=group_id,
            thread_id=thread_id,
            agents=agents,
            config=config,
            created_at=datetime.now(timezone.utc),
        )

        # 1. Persist to state backend
        await self._state.create_swarm_group(group)
        for agent in agents:
            await self._state.create_swarm_agent(agent)

        # 2. Create Redis streams + consumer groups
        agent_ids = [a.id for a in agents]
        await self._streams.create_group(group_id, agent_ids)

        # 3. Create agent registry (all start as "pending")
        await self._streams.create_agent_registry(group_id, agents)

        # 4. Set TTL on Redis keys
        await self._streams.set_ttl(group_id, self._settings.swarm_stream_ttl_seconds)

        # 5. Spawn K8s Jobs in parallel
        agent_specs = []
        for agent in agents:
            spec = {
                "thread_id": agent.id,
                "github_token": "",
                "redis_url": self._settings.redis_url,
                "agent_image": self._settings.agent_image,
                "extra_env": {
                    "SWARM_GROUP_ID": group_id,
                    "AGENT_ID": agent.id,
                    "AGENT_ROLE": agent.role,
                },
            }
            if agent.resource_profile:
                spec["resource_profile"] = agent.resource_profile
            agent_specs.append(spec)

        job_names = await self._async_spawner.spawn_batch(agent_specs)

        # 6. Map job names to agents
        for agent, job_name in zip(agents, job_names):
            agent.k8s_job_name = job_name

        # 7. Update group status
        group.status = SwarmStatus.ACTIVE
        await self._state.update_swarm_status(group_id, SwarmStatus.ACTIVE)

        logger.info(
            "Created swarm %s with %d agents for thread %s",
            group_id, len(agents), thread_id,
        )
        return group

    async def teardown_swarm(self, group_id: str) -> SwarmResult:
        """Tear down a swarm: read audit trail, clean up Redis, update state."""
        # 1. Read full message stream for audit trail
        audit_trail = await self._streams.read_full_stream(group_id)

        # 2. Collect agent results
        agents = await self._state.list_swarm_agents(group_id)

        # 3. Clean up Redis
        agent_ids = [a.id for a in agents]
        await self._streams.cleanup(group_id, agent_ids)

        # 4. Delete K8s Jobs
        for agent in agents:
            if agent.k8s_job_name:
                try:
                    self._spawner.delete(agent.k8s_job_name)
                except Exception:
                    pass  # Job may already be cleaned by TTL

        # 5. Update group status
        await self._state.update_swarm_status(group_id, SwarmStatus.COMPLETED)

        logger.info(
            "Tore down swarm %s: %d agents, %d audit trail messages",
            group_id, len(agents), len(audit_trail),
        )
        return SwarmResult(
            group_id=group_id,
            agents=agents,
            audit_trail=audit_trail,
        )

    async def check_completion(self, group: SwarmGroup) -> bool:
        """Check if a swarm has completed based on its completion strategy."""
        agents = await self._state.list_swarm_agents(group.id)
        terminal = {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.LOST}

        if group.completion_strategy == "all_complete":
            return all(a.status in terminal for a in agents)
        elif group.completion_strategy == "aggregator_signals":
            # Check if any aggregator has completed
            aggregators = [a for a in agents if a.role == "aggregator"]
            return any(a.status == AgentStatus.COMPLETED for a in aggregators)
        elif group.completion_strategy == "timeout":
            # Timeout is handled externally by the monitor
            return False
        else:
            logger.warning("Unknown completion strategy: %s", group.completion_strategy)
            return all(a.status in terminal for a in agents)

    async def recover_redis_state(self) -> int:
        """Reconstruct Redis streams for active swarms from state backend.

        Called on controller startup to handle Redis restart scenarios.
        Returns count of recovered groups.
        """
        active_groups = await self._state.list_swarm_groups(
            status_in=[SwarmStatus.ACTIVE, SwarmStatus.PENDING]
        )
        recovered = 0
        for group in active_groups:
            try:
                agents = await self._state.list_swarm_agents(group.id)
                agent_ids = [a.id for a in agents]
                await self._streams.create_group(group.id, agent_ids)
                await self._streams.create_agent_registry(group.id, agents)
                await self._streams.set_ttl(
                    group.id, self._settings.swarm_stream_ttl_seconds
                )
                recovered += 1
                logger.info("Recovered Redis state for swarm %s", group.id)
            except Exception:
                logger.exception("Failed to recover swarm %s", group.id)

        if recovered:
            logger.info("Recovered %d/%d active swarms", recovered, len(active_groups))
        return recovered
