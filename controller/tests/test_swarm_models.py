"""Tests for swarm-related data models."""
from controller.models import (
    SwarmStatus, AgentStatus, ResourceProfile,
    SwarmAgent, SwarmGroup, SwarmMessage, ROLE_PROFILES,
)


class TestSwarmStatusEnum:
    def test_all_statuses_exist(self):
        assert SwarmStatus.PENDING == "pending"
        assert SwarmStatus.ACTIVE == "active"
        assert SwarmStatus.COMPLETING == "completing"
        assert SwarmStatus.COMPLETED == "completed"
        assert SwarmStatus.FAILED == "failed"


class TestAgentStatusEnum:
    def test_all_statuses_exist(self):
        assert AgentStatus.PENDING == "pending"
        assert AgentStatus.ACTIVE == "active"
        assert AgentStatus.COMPLETED == "completed"
        assert AgentStatus.FAILED == "failed"
        assert AgentStatus.LOST == "lost"


class TestResourceProfile:
    def test_researcher_profile(self):
        p = ROLE_PROFILES["researcher"]
        assert p.cpu_request == "100m"
        assert p.memory_request == "256Mi"

    def test_coder_profile(self):
        p = ROLE_PROFILES["coder"]
        assert p.cpu_request == "500m"
        assert p.memory_request == "1Gi"

    def test_default_profile_exists(self):
        assert "default" in ROLE_PROFILES

    def test_aggregator_profile(self):
        p = ROLE_PROFILES["aggregator"]
        assert p.cpu_request == "250m"


class TestSwarmAgent:
    def test_defaults(self):
        a = SwarmAgent(
            id="agent-1", group_id="grp-1",
            role="researcher", agent_type="general",
            task_assignment="search google",
        )
        assert a.status == AgentStatus.PENDING
        assert a.k8s_job_name is None
        assert a.result_summary == {}
        assert a.resource_profile is None


class TestSwarmGroup:
    def test_defaults(self):
        g = SwarmGroup(id="grp-1", thread_id="t1")
        assert g.status == SwarmStatus.PENDING
        assert g.completion_strategy == "all_complete"
        assert g.agents == []
        assert g.config == {}


class TestSwarmMessage:
    def test_creation(self):
        m = SwarmMessage(
            id="msg-1", group_id="grp-1",
            sender_id="agent-1", recipient_id=None,
            message_type="status", correlation_id=None,
            payload={"state": "searching"},
            timestamp="2026-03-25T10:00:00Z",
            signature="abc123",
        )
        assert m.sender_id == "agent-1"
        assert m.recipient_id is None
        assert m.signature == "abc123"

    def test_broadcast_is_none_recipient(self):
        m = SwarmMessage(
            id="msg-2", group_id="grp-1",
            sender_id="agent-1", recipient_id=None,
            message_type="data", correlation_id=None,
            payload={}, timestamp="2026-03-25T10:00:00Z",
            signature="",
        )
        assert m.recipient_id is None
