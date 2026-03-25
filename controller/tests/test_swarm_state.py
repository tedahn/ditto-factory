"""Tests for swarm state backend operations."""
import pytest
from controller.models import (
    SwarmGroup, SwarmAgent, SwarmStatus, AgentStatus,
)
from controller.state.sqlite import SQLiteBackend


@pytest.fixture
async def backend(tmp_path):
    return await SQLiteBackend.create(f"sqlite:///{tmp_path}/test.db")


class TestSwarmGroupCRUD:
    async def test_create_and_get(self, backend):
        group = SwarmGroup(id="grp-1", thread_id="t1")
        await backend.create_swarm_group(group)
        got = await backend.get_swarm_group("grp-1")
        assert got is not None
        assert got.thread_id == "t1"
        assert got.status == SwarmStatus.PENDING

    async def test_get_nonexistent_returns_none(self, backend):
        got = await backend.get_swarm_group("nope")
        assert got is None

    async def test_update_status(self, backend):
        group = SwarmGroup(id="grp-2", thread_id="t2")
        await backend.create_swarm_group(group)
        await backend.update_swarm_status("grp-2", SwarmStatus.ACTIVE)
        got = await backend.get_swarm_group("grp-2")
        assert got.status == SwarmStatus.ACTIVE

    async def test_list_by_status(self, backend):
        g1 = SwarmGroup(id="g1", thread_id="t1")
        g2 = SwarmGroup(id="g2", thread_id="t2")
        await backend.create_swarm_group(g1)
        await backend.create_swarm_group(g2)
        await backend.update_swarm_status("g2", SwarmStatus.ACTIVE)
        active = await backend.list_swarm_groups(status_in=[SwarmStatus.ACTIVE])
        assert len(active) == 1
        assert active[0].id == "g2"

    async def test_list_all_groups(self, backend):
        g1 = SwarmGroup(id="g1", thread_id="t1")
        g2 = SwarmGroup(id="g2", thread_id="t2")
        await backend.create_swarm_group(g1)
        await backend.create_swarm_group(g2)
        all_groups = await backend.list_swarm_groups()
        assert len(all_groups) == 2


class TestSwarmAgentCRUD:
    async def test_create_and_list(self, backend):
        group = SwarmGroup(id="grp-3", thread_id="t3")
        await backend.create_swarm_group(group)
        agent = SwarmAgent(
            id="a1", group_id="grp-3", role="researcher",
            agent_type="general", task_assignment="search google",
        )
        await backend.create_swarm_agent(agent)
        agents = await backend.list_swarm_agents("grp-3")
        assert len(agents) == 1
        assert agents[0].role == "researcher"
        assert agents[0].status == AgentStatus.PENDING

    async def test_update_agent_status(self, backend):
        group = SwarmGroup(id="grp-4", thread_id="t4")
        await backend.create_swarm_group(group)
        agent = SwarmAgent(
            id="a2", group_id="grp-4", role="aggregator",
            agent_type="general", task_assignment="aggregate results",
        )
        await backend.create_swarm_agent(agent)
        await backend.update_swarm_agent(
            "grp-4", "a2", AgentStatus.COMPLETED,
            result_summary={"events_found": 42},
        )
        agents = await backend.list_swarm_agents("grp-4")
        assert agents[0].status == AgentStatus.COMPLETED
        assert agents[0].result_summary == {"events_found": 42}

    async def test_multiple_agents_per_group(self, backend):
        group = SwarmGroup(id="grp-5", thread_id="t5")
        await backend.create_swarm_group(group)
        a1 = SwarmAgent(id="a1", group_id="grp-5", role="researcher", agent_type="general", task_assignment="search")
        a2 = SwarmAgent(id="a2", group_id="grp-5", role="aggregator", agent_type="general", task_assignment="aggregate")
        await backend.create_swarm_agent(a1)
        await backend.create_swarm_agent(a2)
        agents = await backend.list_swarm_agents("grp-5")
        assert len(agents) == 2

    async def test_list_agents_empty_group(self, backend):
        group = SwarmGroup(id="grp-6", thread_id="t6")
        await backend.create_swarm_group(group)
        agents = await backend.list_swarm_agents("grp-6")
        assert agents == []
