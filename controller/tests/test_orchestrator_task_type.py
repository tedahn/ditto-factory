"""Tests for task_type flowing through orchestrator to Redis and Job."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.models import TaskRequest, TaskType
from controller.config import Settings
from controller.orchestrator import Orchestrator


def _make_settings(**overrides):
    defaults = dict(
        anthropic_api_key="test",
        redis_url="redis://localhost:6379",
        skill_registry_enabled=False,
        gateway_enabled=False,
        tracing_enabled=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestOrchestratorTaskTypePassthrough:
    @pytest.mark.asyncio
    async def test_task_type_included_in_redis_payload(self):
        state = AsyncMock()
        state.get_thread = AsyncMock(return_value=None)
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        state.try_acquire_lock = AsyncMock(return_value=True)
        state.release_lock = AsyncMock()
        state.upsert_thread = AsyncMock()
        state.append_conversation = AsyncMock()
        state.get_conversation = AsyncMock(return_value=[])
        state.create_job = AsyncMock()
        state.update_thread_status = AsyncMock()

        redis_state = AsyncMock()
        redis_state.push_task = AsyncMock()

        spawner = MagicMock()
        spawner.spawn = MagicMock(return_value="df-test-123")

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)

        orch = Orchestrator(
            settings=_make_settings(),
            state=state,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=AsyncMock(),
        )

        tr = TaskRequest(
            thread_id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
            task="analyze error rates",
            task_type=TaskType.ANALYSIS,
        )
        await orch.handle_task(tr)

        redis_state.push_task.assert_called_once()
        payload = redis_state.push_task.call_args[0][1]
        assert payload["task_type"] == "analysis"

    @pytest.mark.asyncio
    async def test_task_type_in_job_context(self):
        state = AsyncMock()
        state.get_thread = AsyncMock(return_value=None)
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        state.try_acquire_lock = AsyncMock(return_value=True)
        state.release_lock = AsyncMock()
        state.upsert_thread = AsyncMock()
        state.append_conversation = AsyncMock()
        state.get_conversation = AsyncMock(return_value=[])
        state.create_job = AsyncMock()
        state.update_thread_status = AsyncMock()

        redis_state = AsyncMock()
        redis_state.push_task = AsyncMock()

        spawner = MagicMock()
        spawner.spawn = MagicMock(return_value="df-test-456")

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)

        orch = Orchestrator(
            settings=_make_settings(),
            state=state,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=AsyncMock(),
        )

        tr = TaskRequest(
            thread_id="t2", source="github", source_ref={},
            repo_owner="org", repo_name="repo",
            task="backfill data",
            task_type=TaskType.DB_MUTATION,
        )
        await orch.handle_task(tr)

        state.create_job.assert_called_once()
        job = state.create_job.call_args[0][0]
        assert job.task_context["task_type"] == "db_mutation"
