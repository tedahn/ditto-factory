"""
E2E Concurrency Tests — Queue path, lock contention, follow-up processing.

Uses REAL orchestrator, REAL SQLite backend, fakeredis, and MOCKED K8s spawner.
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

try:
    import fakeredis.aioredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

try:
    from controller.state.sqlite import SQLiteBackend
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

pytestmark = [
    pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed"),
    pytest.mark.skipif(not HAS_SQLITE, reason="aiosqlite not installed"),
]


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test",
        auto_open_pr=False,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / f"e2e_{uuid.uuid4().hex[:8]}.db")
    return await SQLiteBackend.create(f"sqlite:///{path}")


@pytest.fixture
async def fake_redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def redis_state(fake_redis):
    return RedisState(fake_redis)


@pytest.fixture
def mock_k8s():
    batch = MagicMock()
    batch.create_namespaced_job = MagicMock()
    return batch


@pytest.fixture
def spawner(settings, mock_k8s):
    sp = JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")
    # Patch spawn to return a deterministic job name without hitting real K8s
    sp.spawn = MagicMock(return_value="df-mock-job-00001")
    return sp


@pytest.fixture
def monitor(redis_state, mock_k8s):
    return JobMonitor(redis_state=redis_state, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def registry():
    reg = IntegrationRegistry()
    mock_integ = MagicMock()
    mock_integ.name = "test"
    mock_integ.report_result = AsyncMock()
    mock_integ.fetch_context = AsyncMock(return_value="")
    reg.register(mock_integ)
    return reg


@pytest.fixture
def orchestrator(settings, db, redis_state, registry, spawner, monitor):
    return Orchestrator(
        settings=settings,
        state=db,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
    )


def make_task(thread_id, task_text="do something"):
    return TaskRequest(
        thread_id=thread_id,
        source="test",
        source_ref={"test": True},
        repo_owner="org",
        repo_name="repo",
        task=task_text,
    )


# ─── Test: Message Queuing When Job Active ───────────────────────────

class TestMessageQueuing:

    async def test_second_message_queued_not_spawned(
        self, orchestrator, db, redis_state, fake_redis, spawner
    ):
        """Second message for same thread → queued, not spawned."""
        task1 = make_task("queue-test-1", "first task")
        task2 = make_task("queue-test-1", "second task")

        await orchestrator.handle_task(task1)
        assert spawner.spawn.call_count == 1

        await orchestrator.handle_task(task2)
        # Should NOT have spawned a second job — active job blocks it
        assert spawner.spawn.call_count == 1

        # Verify message was queued in Redis
        queued = await fake_redis.lrange("queue:queue-test-1", 0, -1)
        assert len(queued) == 1
        assert b"second task" in queued[0]

    async def test_multiple_messages_queued_in_order(
        self, orchestrator, db, redis_state, fake_redis, spawner
    ):
        """Multiple follow-ups are queued in FIFO order."""
        tid = "queue-order-test"
        await orchestrator.handle_task(make_task(tid, "first"))

        await orchestrator.handle_task(make_task(tid, "second"))
        await orchestrator.handle_task(make_task(tid, "third"))
        await orchestrator.handle_task(make_task(tid, "fourth"))

        queued = await fake_redis.lrange(f"queue:{tid}", 0, -1)
        # first was processed (spawned), second/third/fourth queued
        assert len(queued) == 3
        texts = [m.decode() for m in queued]
        assert texts == ["second", "third", "fourth"]

    async def test_queue_drained_after_job_completes(self, settings, db, redis_state, registry):
        """Safety pipeline drains queued messages after job completion."""
        tid = "drain-test"
        thread = Thread(
            id=tid,
            source="test",
            source_ref={},
            repo_owner="org",
            repo_name="repo",
            status=ThreadStatus.RUNNING,
        )
        await db.upsert_thread(thread)

        # Queue some messages
        await redis_state.queue_message(tid, "follow-up 1")
        await redis_state.queue_message(tid, "follow-up 2")

        # Use commit_count=1 so the pipeline goes straight to REPORT+CLEANUP
        # (commit_count=0 + exit_code=0 would trigger retry path instead)
        result = AgentResult(branch="df/test/abc", exit_code=0, commit_count=1)
        integration = registry.get("test")

        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )
        await pipeline.process(thread, result)

        # Queue should be empty after pipeline drains it
        remaining = await redis_state.drain_messages(tid)
        assert remaining == []


# ─── Test: Lock Contention ───────────────────────────────────────────

class TestLockContention:

    async def test_lock_prevents_concurrent_spawn(
        self, db, redis_state, settings, mock_k8s, registry, monitor
    ):
        """If lock is held externally, request queues instead of spawning."""
        tid = "lock-test"
        mock_spawner = MagicMock(spec=JobSpawner)
        mock_spawner.spawn = MagicMock(return_value="df-lock-test-001")

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=mock_spawner,
            monitor=monitor,
        )

        # Pre-populate the thread so it exists
        thread = Thread(
            id=tid, source="test", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        await db.upsert_thread(thread)

        # Manually acquire lock before task arrives
        assert await db.try_acquire_lock(tid) is True

        task = make_task(tid, "should be queued")
        await orch.handle_task(task)

        # Job should NOT have been spawned (lock was held by another process)
        mock_spawner.spawn.assert_not_called()

        # Clean up
        await db.release_lock(tid)

    async def test_lock_released_after_successful_spawn(
        self, db, redis_state, settings, registry, monitor
    ):
        """Lock is released after a successful spawn (not held permanently)."""
        tid = "lock-release-success"
        mock_k8s = MagicMock()
        mock_k8s.create_namespaced_job = MagicMock()
        sp = JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")
        sp.spawn = MagicMock(return_value="df-success-job-001")

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=sp,
            monitor=monitor,
        )

        task = make_task(tid, "will succeed")
        await orch.handle_task(task)

        # Spawn was called
        sp.spawn.assert_called_once()

        # Lock must have been released (we can re-acquire it)
        assert await db.try_acquire_lock(tid) is True
        await db.release_lock(tid)

    async def test_lock_released_on_spawn_error(
        self, db, redis_state, settings, registry, monitor
    ):
        """Lock is released even if spawner throws."""
        tid = "lock-error-test"
        broken_k8s = MagicMock()
        broken_k8s.create_namespaced_job = MagicMock()
        sp = JobSpawner(settings=settings, batch_api=broken_k8s, namespace="test")
        sp.spawn = MagicMock(side_effect=RuntimeError("k8s down"))

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=sp,
            monitor=monitor,
        )

        task = make_task(tid, "will fail")
        with pytest.raises(RuntimeError, match="k8s down"):
            await orch.handle_task(task)

        # Lock should have been released by the finally block
        assert await db.try_acquire_lock(tid) is True
        await db.release_lock(tid)


# ─── Test: Conversation History Accumulation ─────────────────────────

class TestConversationHistory:

    async def test_conversation_accumulates_across_tasks(self, db, redis_state, settings, registry):
        """Each spawned task appends its prompt to conversation history."""
        tid = "conv-test"
        mock_k8s = MagicMock()
        mock_k8s.create_namespaced_job = MagicMock()
        sp = JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")
        sp.spawn = MagicMock(return_value="df-conv-job-001")
        mon = JobMonitor(
            redis_state=redis_state,
            batch_api=mock_k8s,
            namespace="test",
        )

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=sp,
            monitor=mon,
        )

        # First task spawns a job and records conversation
        await orch.handle_task(make_task(tid, "first question"))

        history = await db.get_conversation(tid)
        assert len(history) == 1
        assert history[0]["content"] == "first question"
        assert history[0]["role"] == "user"

    async def test_conversation_limit_respected(self, db):
        """Conversation retrieval respects the limit parameter."""
        tid = "conv-limit-test"
        thread = Thread(
            id=tid, source="test", source_ref={}, repo_owner="o", repo_name="r"
        )
        await db.upsert_thread(thread)

        for i in range(100):
            await db.append_conversation(tid, {"role": "user", "content": f"msg {i}"})

        history = await db.get_conversation(tid, limit=10)
        assert len(history) == 10
        # get_conversation returns history[-limit:], so last 10 messages
        assert history[0]["content"] == "msg 90"
        assert history[-1]["content"] == "msg 99"

    async def test_no_conversation_stored_when_queued(
        self, db, redis_state, settings, registry, monitor
    ):
        """Messages that are queued (no spawn) do NOT write to conversation history."""
        tid = "conv-queue-test"
        mock_k8s = MagicMock()
        sp = JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")
        sp.spawn = MagicMock(return_value="df-conv-queue-001")

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=sp,
            monitor=monitor,
        )

        # First task: spawned → conversation written
        await orch.handle_task(make_task(tid, "first task"))
        # Second task: queued (active job exists) → no conversation written
        await orch.handle_task(make_task(tid, "queued task"))

        history = await db.get_conversation(tid)
        assert len(history) == 1
        assert history[0]["content"] == "first task"
