"""Contract 2: Integration -> Orchestrator.

Verifies that Orchestrator.handle_task produces the correct side effects:
- Creates thread if not exists
- Queues message if job is active
- Acquires lock and spawns job otherwise
- Lock is always released (even on error)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator


class TestOrchestratorHandleTaskContract:

    @pytest.fixture
    def settings(self):
        return Settings(anthropic_api_key="test")

    @pytest.fixture
    def state(self):
        mock = AsyncMock()
        mock.get_thread = AsyncMock(return_value=None)
        mock.upsert_thread = AsyncMock()
        mock.get_active_job_for_thread = AsyncMock(return_value=None)
        mock.try_acquire_lock = AsyncMock(return_value=True)
        mock.release_lock = AsyncMock()
        mock.create_job = AsyncMock()
        mock.update_thread_status = AsyncMock()
        mock.append_conversation = AsyncMock()
        mock.get_conversation = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def redis_state(self):
        mock = AsyncMock()
        mock.push_task = AsyncMock()
        mock.queue_message = AsyncMock()
        return mock

    @pytest.fixture
    def spawner(self):
        mock = MagicMock()
        mock.spawn = MagicMock(return_value="df-abc12345-1234567890")
        return mock

    @pytest.fixture
    def registry(self):
        reg = MagicMock()
        reg.get = MagicMock(return_value=AsyncMock())
        return reg

    @pytest.fixture
    def orchestrator(self, settings, state, redis_state, registry, spawner):
        return Orchestrator(
            settings=settings,
            state=state,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=AsyncMock(),
        )

    @pytest.fixture
    def task_request(self):
        return TaskRequest(
            thread_id="a" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 42},
            repo_owner="testorg",
            repo_name="myrepo",
            task="fix the bug",
        )

    async def test_creates_thread_if_not_exists(self, orchestrator, state, task_request):
        """Contract: new thread_id -> thread is created via upsert_thread."""
        state.get_thread = AsyncMock(return_value=None)
        await orchestrator.handle_task(task_request)
        state.upsert_thread.assert_called_once()
        created = state.upsert_thread.call_args[0][0]
        assert created.id == task_request.thread_id
        assert created.source == "github"

    async def test_uses_existing_thread(self, orchestrator, state, task_request):
        """Contract: existing thread -> no new upsert."""
        existing = Thread(
            id=task_request.thread_id, source="github",
            source_ref={"number": 42}, repo_owner="testorg", repo_name="myrepo",
        )
        state.get_thread = AsyncMock(return_value=existing)
        await orchestrator.handle_task(task_request)
        state.upsert_thread.assert_not_called()

    async def test_queues_message_when_job_active(self, orchestrator, state, redis_state, task_request):
        """Contract: active job exists -> message queued, no new spawn."""
        existing_thread = Thread(
            id=task_request.thread_id, source="github",
            source_ref={}, repo_owner="o", repo_name="r",
            status=ThreadStatus.RUNNING,
        )
        state.get_thread = AsyncMock(return_value=existing_thread)
        active_job = Job(id="j1", thread_id=task_request.thread_id, k8s_job_name="df-test-1",
                         status=JobStatus.RUNNING)
        state.get_active_job_for_thread = AsyncMock(return_value=active_job)

        await orchestrator.handle_task(task_request)

        redis_state.queue_message.assert_called_once_with(task_request.thread_id, task_request.task)

    async def test_queues_message_when_lock_unavailable(self, orchestrator, state, redis_state, task_request):
        """Contract: lock not acquired -> message queued."""
        state.try_acquire_lock = AsyncMock(return_value=False)
        await orchestrator.handle_task(task_request)
        redis_state.queue_message.assert_called_once_with(task_request.thread_id, task_request.task)

    async def test_spawns_job_when_idle(self, orchestrator, state, spawner, task_request):
        """Contract: no active job + lock acquired -> job spawned."""
        await orchestrator.handle_task(task_request)
        spawner.spawn.assert_called_once()
        state.create_job.assert_called_once()
        state.update_thread_status.assert_called_once()

    async def test_lock_released_on_success(self, orchestrator, state, task_request):
        """Contract: lock is released after successful spawn."""
        await orchestrator.handle_task(task_request)
        state.release_lock.assert_called_once_with(task_request.thread_id)

    async def test_lock_released_on_error(self, orchestrator, state, spawner, task_request):
        """Contract: lock is released even when spawn raises."""
        spawner.spawn.side_effect = Exception("K8s API error")
        with pytest.raises(Exception, match="K8s API error"):
            await orchestrator.handle_task(task_request)
        state.release_lock.assert_called_once_with(task_request.thread_id)

    async def test_thread_status_set_to_running(self, orchestrator, state, task_request):
        """Contract: after spawning, thread status is RUNNING."""
        await orchestrator.handle_task(task_request)
        state.update_thread_status.assert_called_once()
        call_args = state.update_thread_status.call_args
        assert call_args[0][0] == task_request.thread_id
        assert call_args[0][1] == ThreadStatus.RUNNING

    async def test_task_pushed_to_redis(self, orchestrator, redis_state, task_request):
        """Contract: task context is written to Redis before spawning."""
        await orchestrator.handle_task(task_request)
        redis_state.push_task.assert_called_once()
        thread_id_arg = redis_state.push_task.call_args[0][0]
        context_arg = redis_state.push_task.call_args[0][1]
        assert thread_id_arg == task_request.thread_id
        assert "task" in context_arg
        assert "system_prompt" in context_arg
        assert "repo_url" in context_arg
        assert "branch" in context_arg

    async def test_conversation_appended(self, orchestrator, state, task_request):
        """Contract: user message is appended to conversation history."""
        await orchestrator.handle_task(task_request)
        state.append_conversation.assert_called_once()
        msg = state.append_conversation.call_args[0][1]
        assert msg["role"] == "user"
        assert msg["content"] == task_request.task
