"""Tests for SwarmManager lifecycle."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.models import (
    SwarmGroup, SwarmAgent, SwarmStatus, AgentStatus, ROLE_PROFILES,
)
from controller.swarm.manager import SwarmManager
from controller.config import Settings


def _make_settings(**overrides):
    defaults = dict(
        anthropic_api_key="test",
        redis_url="redis://localhost",
        agent_image="test-image:latest",
        swarm_enabled=True,
        swarm_stream_ttl_seconds=7200,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestSwarmCreation:
    @pytest.mark.asyncio
    async def test_create_swarm_persists_group(self):
        state = AsyncMock()
        streams = AsyncMock()
        async_spawner = AsyncMock()
        async_spawner.spawn_batch = AsyncMock(return_value=["job-1", "job-2"])
        spawner = MagicMock()

        mgr = SwarmManager(
            settings=_make_settings(),
            state=state,
            redis_streams=streams,
            async_spawner=async_spawner,
            spawner=spawner,
        )

        agents = [
            SwarmAgent(id="a1", group_id="", role="researcher",
                       agent_type="general", task_assignment="search google"),
            SwarmAgent(id="a2", group_id="", role="aggregator",
                       agent_type="general", task_assignment="aggregate"),
        ]

        group = await mgr.create_swarm("thread-1", agents, {})

        assert group.status == SwarmStatus.ACTIVE
        state.create_swarm_group.assert_called_once()
        streams.create_group.assert_called_once()
        streams.create_agent_registry.assert_called_once()
        async_spawner.spawn_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_swarm_assigns_resource_profiles(self):
        state = AsyncMock()
        streams = AsyncMock()
        async_spawner = AsyncMock()
        async_spawner.spawn_batch = AsyncMock(return_value=["job-1"])
        spawner = MagicMock()

        mgr = SwarmManager(
            settings=_make_settings(),
            state=state,
            redis_streams=streams,
            async_spawner=async_spawner,
            spawner=spawner,
        )

        agents = [
            SwarmAgent(id="a1", group_id="", role="researcher",
                       agent_type="general", task_assignment="search"),
        ]
        group = await mgr.create_swarm("thread-2", agents, {})

        # Verify resource profile was assigned
        assert agents[0].resource_profile is not None
        assert agents[0].resource_profile.cpu_request == "100m"


class TestSwarmTeardown:
    @pytest.mark.asyncio
    async def test_teardown_cleans_up(self):
        state = AsyncMock()
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       k8s_job_name="df-a1-123"),
        ])
        streams = AsyncMock()
        streams.read_full_stream = AsyncMock(return_value=[])
        async_spawner = AsyncMock()
        spawner = MagicMock()

        mgr = SwarmManager(
            settings=_make_settings(),
            state=state,
            redis_streams=streams,
            async_spawner=async_spawner,
            spawner=spawner,
        )

        result = await mgr.teardown_swarm("grp-1")

        streams.read_full_stream.assert_called_once_with("grp-1")
        streams.cleanup.assert_called_once()
        state.update_swarm_status.assert_called_with("grp-1", SwarmStatus.COMPLETED)


class TestCompletionDetection:
    @pytest.mark.asyncio
    async def test_all_complete_strategy(self):
        state = AsyncMock()
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.COMPLETED),
            SwarmAgent(id="a2", group_id="grp-1", role="aggregator",
                       agent_type="general", task_assignment="aggregate",
                       status=AgentStatus.COMPLETED),
        ])
        streams = AsyncMock()
        async_spawner = AsyncMock()
        spawner = MagicMock()

        mgr = SwarmManager(
            settings=_make_settings(),
            state=state,
            redis_streams=streams,
            async_spawner=async_spawner,
            spawner=spawner,
        )

        group = SwarmGroup(id="grp-1", thread_id="t1", completion_strategy="all_complete")
        is_complete = await mgr.check_completion(group)
        assert is_complete is True

    @pytest.mark.asyncio
    async def test_not_complete_when_agents_running(self):
        state = AsyncMock()
        state.list_swarm_agents = AsyncMock(return_value=[
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE),
            SwarmAgent(id="a2", group_id="grp-1", role="aggregator",
                       agent_type="general", task_assignment="aggregate",
                       status=AgentStatus.COMPLETED),
        ])
        streams = AsyncMock()
        async_spawner = AsyncMock()
        spawner = MagicMock()

        mgr = SwarmManager(
            settings=_make_settings(),
            state=state,
            redis_streams=streams,
            async_spawner=async_spawner,
            spawner=spawner,
        )

        group = SwarmGroup(id="grp-1", thread_id="t1", completion_strategy="all_complete")
        is_complete = await mgr.check_completion(group)
        assert is_complete is False
