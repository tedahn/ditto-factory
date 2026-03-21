"""Contract 13: handle_job_completion Flow.

Verifies the orchestrator completion path: thread lookup, result polling,
integration resolution, and SafetyPipeline construction.
"""
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.models import Thread, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.config import Settings


class TestHandleJobCompletionContract:

    @pytest.fixture
    def settings(self):
        return Settings(anthropic_api_key="test", auto_open_pr=True)

    @pytest.fixture
    def state(self):
        return AsyncMock()

    @pytest.fixture
    def redis_state(self):
        mock = AsyncMock()
        mock.drain_messages = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def registry(self):
        reg = MagicMock()
        integration = AsyncMock()
        integration.report_result = AsyncMock()
        reg.get = MagicMock(return_value=integration)
        return reg

    @pytest.fixture
    def monitor(self):
        return AsyncMock()

    @pytest.fixture
    def orchestrator(self, settings, state, redis_state, registry, monitor):
        return Orchestrator(
            settings=settings, state=state, redis_state=redis_state,
            registry=registry, spawner=MagicMock(), monitor=monitor,
            github_client=AsyncMock(),
        )

    async def test_returns_early_on_missing_thread(self, orchestrator, state):
        """Contract: no crash if thread not found."""
        state.get_thread = AsyncMock(return_value=None)
        await orchestrator.handle_job_completion("nonexistent")
        # Should not raise

    async def test_returns_early_on_missing_result(self, orchestrator, state, monitor):
        """Contract: no crash if monitor times out."""
        state.get_thread = AsyncMock(return_value=Thread(
            id="t1", source="github", source_ref={}, repo_owner="o", repo_name="r",
        ))
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        monitor.wait_for_result = AsyncMock(return_value=None)
        await orchestrator.handle_job_completion("t1")
        # Should not raise, but thread stays in RUNNING (known issue)

    async def test_returns_early_on_missing_integration(self, orchestrator, state, monitor, registry):
        """Contract: no crash if integration not registered."""
        state.get_thread = AsyncMock(return_value=Thread(
            id="t1", source="unknown", source_ref={}, repo_owner="o", repo_name="r",
        ))
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        monitor.wait_for_result = AsyncMock(return_value=AgentResult(
            branch="df/test/x", exit_code=0, commit_count=1,
        ))
        registry.get = MagicMock(return_value=None)
        await orchestrator.handle_job_completion("t1")
        # Should not raise

    async def test_spawner_callable_interface(self, orchestrator):
        """Contract: spawner passed to SafetyPipeline is _spawn_job bound method.
        BUG: safety.py calls spawner(thread.id, is_retry=True, retry_count=N)
        but _spawn_job expects (thread: Thread, task_request: TaskRequest, ...).
        This test documents the interface mismatch."""
        sig = inspect.signature(orchestrator._spawn_job)
        params = list(sig.parameters.keys())
        # _spawn_job expects: thread, task_request, is_retry, retry_count
        # but safety.py calls: spawner(thread.id, is_retry=True, retry_count=N)
        # First param is 'thread' (expects Thread, not str)
        assert params[0] == "thread", "First param should be 'thread' (Thread object)"
        assert params[1] == "task_request", "Second param should be 'task_request'"

    async def test_updates_job_status_on_completion(self, orchestrator, state, monitor, registry):
        """Contract: active job status is updated to COMPLETED/FAILED."""
        from controller.models import Job

        thread = Thread(id="t1", source="github", source_ref={}, repo_owner="o", repo_name="r")
        state.get_thread = AsyncMock(return_value=thread)

        active_job = Job(id="j1", thread_id="t1", k8s_job_name="df-test-1", status=JobStatus.RUNNING)
        state.get_active_job_for_thread = AsyncMock(return_value=active_job)
        state.update_job_status = AsyncMock()

        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1)
        monitor.wait_for_result = AsyncMock(return_value=result)

        # Mock the safety pipeline to avoid side effects
        with patch("controller.orchestrator.SafetyPipeline") as MockPipeline:
            MockPipeline.return_value.process = AsyncMock()
            await orchestrator.handle_job_completion("t1")

        state.update_job_status.assert_called_once()
        call_args = state.update_job_status.call_args
        assert call_args[0][0] == "j1"
        assert call_args[0][1] == JobStatus.COMPLETED

    async def test_failed_result_marks_job_failed(self, orchestrator, state, monitor, registry):
        """Contract: exit_code != 0 -> job status is FAILED."""
        from controller.models import Job

        thread = Thread(id="t1", source="github", source_ref={}, repo_owner="o", repo_name="r")
        state.get_thread = AsyncMock(return_value=thread)

        active_job = Job(id="j1", thread_id="t1", k8s_job_name="df-test-1", status=JobStatus.RUNNING)
        state.get_active_job_for_thread = AsyncMock(return_value=active_job)
        state.update_job_status = AsyncMock()

        result = AgentResult(branch="df/test/x", exit_code=1, commit_count=0, stderr="error")
        monitor.wait_for_result = AsyncMock(return_value=result)

        with patch("controller.orchestrator.SafetyPipeline") as MockPipeline:
            MockPipeline.return_value.process = AsyncMock()
            await orchestrator.handle_job_completion("t1")

        call_args = state.update_job_status.call_args
        assert call_args[0][1] == JobStatus.FAILED
