"""Tests for scheduling watchdog."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.models import (
    SwarmGroup, SwarmAgent, SwarmStatus, AgentStatus,
)
from controller.swarm.watchdog import SchedulingWatchdog


class TestSchedulingWatchdog:
    @pytest.fixture
    def mock_deps(self):
        core_api = MagicMock()
        state = AsyncMock()
        streams = AsyncMock()
        return core_api, state, streams

    @pytest.mark.asyncio
    async def test_skips_non_pending_agents(self, mock_deps):
        core_api, state, streams = mock_deps
        watchdog = SchedulingWatchdog(
            core_api=core_api, state=state,
            redis_streams=streams, namespace="default",
            grace_seconds=120,
        )
        group = SwarmGroup(id="grp-1", thread_id="t1")
        agents = [
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.ACTIVE, k8s_job_name="df-a1"),
        ]
        state.list_swarm_agents = AsyncMock(return_value=agents)

        await watchdog.check_group(group)

        # Should not check K8s — agent is already active
        core_api.list_namespaced_pod.assert_not_called()

    @pytest.mark.asyncio
    async def test_detects_failed_scheduling(self, mock_deps):
        core_api, state, streams = mock_deps
        watchdog = SchedulingWatchdog(
            core_api=core_api, state=state,
            redis_streams=streams, namespace="default",
            grace_seconds=0,  # No grace period for testing
        )
        group = SwarmGroup(id="grp-1", thread_id="t1")
        agents = [
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.PENDING, k8s_job_name="df-a1"),
            SwarmAgent(id="a2", group_id="grp-1", role="aggregator",
                       agent_type="general", task_assignment="aggregate",
                       status=AgentStatus.ACTIVE, k8s_job_name="df-a2"),
        ]
        state.list_swarm_agents = AsyncMock(return_value=agents)

        # Mock K8s: pod with FailedScheduling condition
        mock_pod = MagicMock()
        mock_condition = MagicMock()
        mock_condition.type = "PodScheduled"
        mock_condition.status = "False"
        mock_condition.reason = "Unschedulable"
        mock_pod.status.conditions = [mock_condition]
        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        core_api.list_namespaced_pod = MagicMock(return_value=mock_pod_list)

        await watchdog.check_group(group)

        # Should mark agent as failed and publish control message
        state.update_swarm_agent.assert_called_once()
        streams.send_control.assert_called_once()

    @pytest.mark.asyncio
    async def test_respects_grace_period(self, mock_deps):
        core_api, state, streams = mock_deps
        watchdog = SchedulingWatchdog(
            core_api=core_api, state=state,
            redis_streams=streams, namespace="default",
            grace_seconds=9999,  # Very long grace period
        )
        group = SwarmGroup(id="grp-1", thread_id="t1")
        agents = [
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.PENDING, k8s_job_name="df-a1"),
        ]
        state.list_swarm_agents = AsyncMock(return_value=agents)

        mock_pod = MagicMock()
        mock_condition = MagicMock()
        mock_condition.type = "PodScheduled"
        mock_condition.status = "False"
        mock_condition.reason = "Unschedulable"
        mock_pod.status.conditions = [mock_condition]
        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        core_api.list_namespaced_pod = MagicMock(return_value=mock_pod_list)

        await watchdog.check_group(group)

        # Grace period not expired — should NOT mark failed
        state.update_swarm_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_no_pods_found(self, mock_deps):
        core_api, state, streams = mock_deps
        watchdog = SchedulingWatchdog(
            core_api=core_api, state=state,
            redis_streams=streams, namespace="default",
            grace_seconds=0,
        )
        group = SwarmGroup(id="grp-1", thread_id="t1")
        agents = [
            SwarmAgent(id="a1", group_id="grp-1", role="researcher",
                       agent_type="general", task_assignment="search",
                       status=AgentStatus.PENDING, k8s_job_name="df-a1"),
        ]
        state.list_swarm_agents = AsyncMock(return_value=agents)

        mock_pod_list = MagicMock()
        mock_pod_list.items = []
        core_api.list_namespaced_pod = MagicMock(return_value=mock_pod_list)

        await watchdog.check_group(group)

        # No pods = no conclusion yet
        state.update_swarm_agent.assert_not_called()
