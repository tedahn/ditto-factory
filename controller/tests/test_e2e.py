"""End-to-end integration test for the full webhook-to-report pipeline."""
import hashlib
import hmac
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, ThreadStatus, JobStatus, AgentResult
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.slack import SlackIntegration
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline


class FakeStateBackend:
    """In-memory state backend for E2E testing."""
    def __init__(self):
        self._threads = {}
        self._jobs = {}
        self._locks = set()

    async def get_thread(self, thread_id):
        return self._threads.get(thread_id)

    async def upsert_thread(self, thread):
        self._threads[thread.id] = thread

    async def update_thread_status(self, thread_id, status, job_name=None):
        t = self._threads[thread_id]
        t.status = status
        if job_name is not None:
            t.current_job_name = job_name

    async def create_job(self, job):
        self._jobs[job.id] = job

    async def get_job(self, job_id):
        return self._jobs.get(job_id)

    async def get_active_job_for_thread(self, thread_id):
        for j in self._jobs.values():
            if j.thread_id == thread_id and j.status in (JobStatus.PENDING, JobStatus.RUNNING):
                return j
        return None

    async def update_job_status(self, job_id, status, result=None):
        j = self._jobs[job_id]
        j.status = status
        if result is not None:
            j.result = result

    async def append_conversation(self, thread_id, message):
        self._threads[thread_id].conversation_history.append(message)

    async def get_conversation(self, thread_id, limit=50):
        t = self._threads.get(thread_id)
        if not t:
            return []
        return t.conversation_history[-limit:]

    async def try_acquire_lock(self, thread_id):
        if thread_id in self._locks:
            return False
        self._locks.add(thread_id)
        return True

    async def release_lock(self, thread_id):
        self._locks.discard(thread_id)


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="test-key",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )


@pytest.fixture
def state():
    return FakeStateBackend()


@pytest.fixture
def redis_state():
    mock = AsyncMock(spec=RedisState)
    mock.push_task = AsyncMock()
    mock.get_result = AsyncMock(return_value=None)
    mock.queue_message = AsyncMock()
    mock.drain_messages = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def registry():
    reg = IntegrationRegistry()
    mock_integration = MagicMock()
    mock_integration.name = "slack"
    mock_integration.report_result = AsyncMock()
    mock_integration.acknowledge = AsyncMock()
    mock_integration.fetch_context = AsyncMock(return_value="")
    reg.register(mock_integration)
    return reg


@pytest.fixture
def spawner():
    mock = MagicMock(spec=JobSpawner)
    mock.spawn = MagicMock(return_value="df-abc123-99999")
    return mock


@pytest.fixture
def monitor():
    return AsyncMock(spec=JobMonitor)


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


async def test_full_pipeline_new_task(orchestrator, state, redis_state, spawner):
    """Test: new task → thread created → task pushed to Redis → job spawned."""
    task = TaskRequest(
        thread_id="e2e-test-1",
        source="slack",
        source_ref={"channel": "C123", "thread_ts": "100.000"},
        repo_owner="org",
        repo_name="repo",
        task="fix the login bug",
    )

    await orchestrator.handle_task(task)

    # Thread was created
    thread = await state.get_thread("e2e-test-1")
    assert thread is not None
    assert thread.source == "slack"
    assert thread.status == ThreadStatus.RUNNING

    # Task was pushed to Redis
    redis_state.push_task.assert_called_once()
    pushed_args = redis_state.push_task.call_args
    assert pushed_args[0][0] == "e2e-test-1"

    # Job was spawned
    spawner.spawn.assert_called_once()

    # Job was tracked in state
    jobs = [j for j in state._jobs.values() if j.thread_id == "e2e-test-1"]
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.RUNNING


async def test_concurrent_task_queued(orchestrator, state, redis_state, spawner):
    """Test: active job exists → message queued instead of spawning."""
    # Create an existing thread with an active job
    thread = Thread(
        id="e2e-test-2", source="slack", source_ref={},
        repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
        current_job_name="df-existing-job",
    )
    await state.upsert_thread(thread)
    job = Job(id="j1", thread_id="e2e-test-2", k8s_job_name="df-existing-job", status=JobStatus.RUNNING)
    await state.create_job(job)

    task = TaskRequest(
        thread_id="e2e-test-2",
        source="slack",
        source_ref={"channel": "C123"},
        repo_owner="org",
        repo_name="repo",
        task="also fix the tests",
    )

    await orchestrator.handle_task(task)

    # Message was queued, not spawned
    redis_state.queue_message.assert_called_once_with("e2e-test-2", "also fix the tests")
    spawner.spawn.assert_not_called()


async def test_safety_pipeline_reports_result(settings, state, redis_state, registry):
    """Test: safety pipeline processes result and reports to integration."""
    thread = Thread(
        id="e2e-test-3", source="slack", source_ref={},
        repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
    )
    await state.upsert_thread(thread)

    result = AgentResult(
        branch="df/e2e-test/abc123",
        exit_code=0,
        commit_count=3,
        pr_url="https://github.com/org/repo/pull/42",
    )

    integration = registry.get("slack")
    pipeline = SafetyPipeline(
        settings=settings,
        state_backend=state,
        redis_state=redis_state,
        integration=integration,
        spawner=AsyncMock(),
        github_client=AsyncMock(),
    )

    await pipeline.process(thread, result)

    # Result was reported to integration
    integration.report_result.assert_called_once_with(thread, result)

    # Thread status was reset to IDLE
    updated_thread = await state.get_thread("e2e-test-3")
    assert updated_thread.status == ThreadStatus.IDLE


async def test_safety_pipeline_retries_empty_result(settings, state, redis_state, registry):
    """Test: empty result triggers retry."""
    thread = Thread(
        id="e2e-test-4", source="slack", source_ref={},
        repo_owner="org", repo_name="repo", status=ThreadStatus.RUNNING,
    )
    await state.upsert_thread(thread)

    result = AgentResult(branch="df/e2e-test/abc123", exit_code=0, commit_count=0)
    mock_spawner = AsyncMock()
    integration = registry.get("slack")

    pipeline = SafetyPipeline(
        settings=settings,
        state_backend=state,
        redis_state=redis_state,
        integration=integration,
        spawner=mock_spawner,
        github_client=AsyncMock(),
    )

    await pipeline.process(thread, result, retry_count=0)

    # Should retry, not report
    mock_spawner.assert_called_once()
    integration.report_result.assert_not_called()
