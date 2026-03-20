import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.orchestrator import Orchestrator
from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, ThreadStatus, JobStatus

@pytest.fixture
def settings():
    return Settings(anthropic_api_key="test")

@pytest.fixture
def state():
    mock = AsyncMock()
    mock.get_thread = AsyncMock(return_value=None)
    mock.get_active_job_for_thread = AsyncMock(return_value=None)
    mock.try_acquire_lock = AsyncMock(return_value=True)
    mock.release_lock = AsyncMock()
    mock.get_conversation = AsyncMock(return_value=[])
    return mock

@pytest.fixture
def redis_state():
    return AsyncMock()

@pytest.fixture
def registry():
    mock = MagicMock()
    mock.get = MagicMock(return_value=AsyncMock())
    return mock

@pytest.fixture
def spawner():
    mock = MagicMock()
    mock.spawn = MagicMock(return_value="df-abc123-99999")
    return mock

@pytest.fixture
def monitor():
    return AsyncMock()

@pytest.fixture
def orchestrator(settings, state, redis_state, registry, spawner, monitor):
    return Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
    )

@pytest.fixture
def task_request():
    return TaskRequest(
        thread_id="abc123",
        source="slack",
        source_ref={"channel": "C1", "thread_ts": "123.456"},
        repo_owner="org",
        repo_name="repo",
        task="fix the login bug",
    )

async def test_new_task_creates_thread_and_spawns(orchestrator, state, task_request):
    await orchestrator.handle_task(task_request)
    state.upsert_thread.assert_called_once()
    state.create_job.assert_called_once()
    state.update_thread_status.assert_called_once()

async def test_active_job_queues_message(orchestrator, state, redis_state, task_request):
    state.get_thread = AsyncMock(return_value=Thread(
        id="abc123", source="slack", source_ref={}, repo_owner="org", repo_name="repo",
        status=ThreadStatus.RUNNING,
    ))
    state.get_active_job_for_thread = AsyncMock(return_value=Job(
        id="j1", thread_id="abc123", k8s_job_name="df-abc-123", status=JobStatus.RUNNING,
    ))
    await orchestrator.handle_task(task_request)
    redis_state.queue_message.assert_called_once_with("abc123", "fix the login bug")
    state.create_job.assert_not_called()

async def test_lock_failure_queues_message(orchestrator, state, redis_state, task_request):
    state.try_acquire_lock = AsyncMock(return_value=False)
    await orchestrator.handle_task(task_request)
    redis_state.queue_message.assert_called_once()
    state.create_job.assert_not_called()

async def test_lock_released_after_spawn(orchestrator, state, task_request):
    await orchestrator.handle_task(task_request)
    state.release_lock.assert_called_once_with("abc123")

async def test_lock_released_on_error(orchestrator, state, redis_state, task_request):
    orchestrator._spawner.spawn = MagicMock(side_effect=RuntimeError("k8s error"))
    with pytest.raises(RuntimeError):
        await orchestrator.handle_task(task_request)
    state.release_lock.assert_called_once_with("abc123")
