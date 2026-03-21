"""Tests for MCP Gateway wiring into the orchestrator."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.orchestrator import Orchestrator
from controller.config import Settings
from controller.models import TaskRequest, Thread, ThreadStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = dict(
        redis_url="redis://localhost:6379",
        database_url="sqlite:///test.db",
        agent_image="test-agent:latest",
        gateway_enabled=False,
        gateway_url="",
        gateway_default_tools=[],
        skill_registry_enabled=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_thread(thread_id: str = "thread-abc") -> Thread:
    from datetime import datetime, timezone
    return Thread(
        id=thread_id,
        source="cli",
        source_ref={},
        repo_owner="test-owner",
        repo_name="test-repo",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_task_request(thread_id: str = "thread-abc") -> TaskRequest:
    return TaskRequest(
        thread_id=thread_id,
        source="cli",
        source_ref={},
        task="Fix the bug",
        repo_owner="test-owner",
        repo_name="test-repo",
    )


def _make_gateway_manager():
    """Create a mock GatewayManager with all async methods."""
    gw = AsyncMock()
    gw.scope_from_skills = AsyncMock(return_value=["db-query"])
    gw.set_scope = AsyncMock()
    gw.clear_scope = AsyncMock()
    gw.get_gateway_mcp_config = MagicMock(return_value={
        "gateway": {
            "url": "http://gw:3001/sse?thread_id=thread-abc",
            "transport": "sse",
        }
    })
    return gw


def _make_orchestrator(settings, gateway_manager=None):
    """Create an Orchestrator with mocked dependencies."""
    state = AsyncMock()
    state.get_conversation = AsyncMock(return_value=[])
    state.append_conversation = AsyncMock()
    state.create_job = AsyncMock()
    state.update_thread_status = AsyncMock()

    redis_state = AsyncMock()
    redis_state.push_task = AsyncMock()

    registry = MagicMock()
    registry.get = MagicMock(return_value=MagicMock())

    spawner = MagicMock()
    spawner.spawn = MagicMock(return_value="job-123")

    monitor = AsyncMock()

    return Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        gateway_manager=gateway_manager,
    ), state, redis_state


# ---------------------------------------------------------------------------
# Tests: _spawn_job gateway wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_scope_set_on_spawn():
    """When skills have gw: tags, gateway scope is set in Redis."""
    settings = _make_settings(
        gateway_enabled=True,
        gateway_url="http://gw:3001",
        skill_registry_enabled=True,
    )
    gw = _make_gateway_manager()
    orch, state, redis_state = _make_orchestrator(settings, gateway_manager=gw)

    # Simulate skill classification by injecting matched_skills via classifier
    skill = SimpleNamespace(name="sql-skill", tags=["gw:db-query"])
    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=SimpleNamespace(
        skills=[skill], agent_type="general",
    ))
    orch._classifier = classifier
    orch._settings = Settings(**{
        **settings.model_dump(),
        "skill_registry_enabled": True,
    })

    thread = _make_thread()
    task_req = _make_task_request()

    await orch._spawn_job(thread, task_req)

    gw.scope_from_skills.assert_awaited_once()
    gw.set_scope.assert_awaited_once()
    gw.get_gateway_mcp_config.assert_called_once_with("thread-abc")


@pytest.mark.asyncio
async def test_gateway_mcp_in_payload():
    """Gateway MCP config appears in the Redis task payload."""
    settings = _make_settings(
        gateway_enabled=True,
        gateway_url="http://gw:3001",
        gateway_default_tools=["health"],
    )
    gw = _make_gateway_manager()
    gw.scope_from_skills = AsyncMock(return_value=[])
    orch, state, redis_state = _make_orchestrator(settings, gateway_manager=gw)

    thread = _make_thread()
    task_req = _make_task_request()

    await orch._spawn_job(thread, task_req)

    # push_task should have been called with gateway_mcp in payload
    call_args = redis_state.push_task.call_args
    payload = call_args[0][1]
    assert "gateway_mcp" in payload
    assert payload["gateway_mcp"]["gateway"]["transport"] == "sse"


@pytest.mark.asyncio
async def test_gateway_scope_cleared_on_completion():
    """Gateway scope is cleared when a job completes."""
    settings = _make_settings(
        gateway_enabled=True,
        gateway_url="http://gw:3001",
    )
    gw = _make_gateway_manager()
    orch, state, redis_state = _make_orchestrator(settings, gateway_manager=gw)

    # Set up state for handle_job_completion
    thread = _make_thread()
    state.get_thread = AsyncMock(return_value=thread)

    active_job = SimpleNamespace(id="job-1", k8s_job_name="k8s-job-1")
    state.get_active_job_for_thread = AsyncMock(return_value=active_job)
    state.update_job_status = AsyncMock()

    result = SimpleNamespace(
        branch="df/abc/123",
        exit_code=0,
        commit_count=1,
        pr_url="https://github.com/test/pr/1",
        stderr="",
    )
    orch._monitor.wait_for_result = AsyncMock(return_value=result)

    # Mock the integration and pipeline
    integration = MagicMock()
    orch._registry.get = MagicMock(return_value=integration)

    with patch("controller.orchestrator.SafetyPipeline") as MockPipeline:
        mock_pipeline = AsyncMock()
        MockPipeline.return_value = mock_pipeline

        await orch.handle_job_completion("thread-abc")

    gw.clear_scope.assert_awaited_once_with("thread-abc")


@pytest.mark.asyncio
async def test_gateway_disabled_no_scope():
    """When gateway_enabled=False, no gateway methods are called."""
    settings = _make_settings(gateway_enabled=False)
    gw = _make_gateway_manager()
    orch, state, redis_state = _make_orchestrator(settings, gateway_manager=gw)

    thread = _make_thread()
    task_req = _make_task_request()

    await orch._spawn_job(thread, task_req)

    gw.scope_from_skills.assert_not_awaited()
    gw.set_scope.assert_not_awaited()
    gw.get_gateway_mcp_config.assert_not_called()

    # gateway_mcp should be empty dict
    payload = redis_state.push_task.call_args[0][1]
    assert payload["gateway_mcp"] == {}


@pytest.mark.asyncio
async def test_gateway_failure_doesnt_block_spawn():
    """If gateway operations fail, the job still spawns."""
    settings = _make_settings(
        gateway_enabled=True,
        gateway_url="http://gw:3001",
        gateway_default_tools=["health"],
    )
    gw = _make_gateway_manager()
    gw.set_scope = AsyncMock(side_effect=RuntimeError("Redis down"))
    orch, state, redis_state = _make_orchestrator(settings, gateway_manager=gw)

    thread = _make_thread()
    task_req = _make_task_request()

    # Should NOT raise
    await orch._spawn_job(thread, task_req)

    # Job should still be spawned
    orch._spawner.spawn.assert_called_once()

    # gateway_mcp should be empty dict due to error
    payload = redis_state.push_task.call_args[0][1]
    assert payload["gateway_mcp"] == {}
