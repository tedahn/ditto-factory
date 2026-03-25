"""Tests for swarm monitor (heartbeat detection + PEL GC)."""
import pytest
from unittest.mock import AsyncMock
from controller.models import SwarmGroup, SwarmAgent, AgentStatus, SwarmStatus
from controller.swarm.monitor import SwarmMonitor


class TestHeartbeatDetection:
    @pytest.mark.asyncio
    async def test_marks_agent_lost_when_heartbeat_stale(self):
        state = AsyncMock()
        streams = AsyncMock()
        # Agent last seen 200 seconds ago (timeout is 90s)
        old_time = "2026-03-25T09:56:00+00:00"
        streams.get_agent_registry = AsyncMock(return_value={
            "a1": {"role": "researcher", "status": "active", "last_seen": old_time},
        })
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE),
        ])

        monitor = SwarmMonitor(state=state, redis_streams=streams, heartbeat_timeout=90)
        group = SwarmGroup(id="grp-1", thread_id="t1", status=SwarmStatus.ACTIVE)

        await monitor.check_heartbeats(group)

        state.update_swarm_agent.assert_called_once()
        call_args = state.update_swarm_agent.call_args
        assert call_args[0][2] == AgentStatus.LOST

    @pytest.mark.asyncio
    async def test_skips_healthy_agents(self):
        state = AsyncMock()
        streams = AsyncMock()
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat()
        streams.get_agent_registry = AsyncMock(return_value={
            "a1": {"role": "researcher", "status": "active", "last_seen": recent},
        })
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE),
        ])

        monitor = SwarmMonitor(state=state, redis_streams=streams, heartbeat_timeout=90)
        group = SwarmGroup(id="grp-1", thread_id="t1", status=SwarmStatus.ACTIVE)

        await monitor.check_heartbeats(group)

        state.update_swarm_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_active_agents(self):
        state = AsyncMock()
        streams = AsyncMock()
        streams.get_agent_registry = AsyncMock(return_value={
            "a1": {"role": "researcher", "status": "completed", "last_seen": ""},
        })
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.COMPLETED),
        ])

        monitor = SwarmMonitor(state=state, redis_streams=streams, heartbeat_timeout=90)
        group = SwarmGroup(id="grp-1", thread_id="t1", status=SwarmStatus.ACTIVE)

        await monitor.check_heartbeats(group)

        state.update_swarm_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_agent_not_in_registry(self):
        state = AsyncMock()
        streams = AsyncMock()
        streams.get_agent_registry = AsyncMock(return_value={})
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE),
        ])

        monitor = SwarmMonitor(state=state, redis_streams=streams, heartbeat_timeout=90)
        group = SwarmGroup(id="grp-1", thread_id="t1", status=SwarmStatus.ACTIVE)

        await monitor.check_heartbeats(group)

        state.update_swarm_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_invalid_last_seen(self):
        state = AsyncMock()
        streams = AsyncMock()
        streams.get_agent_registry = AsyncMock(return_value={
            "a1": {"role": "researcher", "status": "active", "last_seen": "not-a-date"},
        })
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE),
        ])

        monitor = SwarmMonitor(state=state, redis_streams=streams, heartbeat_timeout=90)
        group = SwarmGroup(id="grp-1", thread_id="t1", status=SwarmStatus.ACTIVE)

        await monitor.check_heartbeats(group)

        state.update_swarm_agent.assert_not_called()
