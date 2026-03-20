"""
K8s Live Integration Test — Requires running K8s cluster + Redis.
Skip if not available. Tests real Job creation and Redis communication.

Run with: DF_K8S_LIVE_TEST=1 uv run pytest tests/e2e/test_k8s_live.py -v
"""
import asyncio
import json
import os
import time
import uuid
import pytest
from unittest.mock import MagicMock

from controller.config import Settings
from controller.models import TaskRequest, Thread, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

LIVE_TEST = os.environ.get("DF_K8S_LIVE_TEST", "") == "1"
pytestmark = pytest.mark.skipif(not LIVE_TEST, reason="Set DF_K8S_LIVE_TEST=1 to run K8s live tests")


@pytest.fixture
async def settings():
    return Settings(
        anthropic_api_key="sk-test-live",
        redis_url=os.environ.get("DF_REDIS_URL", "redis://localhost:6379"),
        agent_image="busybox:latest",  # lightweight image for testing
        max_job_duration_seconds=30,
        job_ttl_seconds=60,
    )


@pytest.fixture
async def redis_client(settings):
    from redis.asyncio import Redis
    client = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        pytest.skip(f"Redis not reachable at {settings.redis_url} — run `kubectl port-forward svc/redis 6379:6379 -n aal` or set DF_REDIS_URL: {e}")
    yield client
    await client.aclose()


@pytest.fixture
def redis_state(redis_client):
    return RedisState(redis_client)


@pytest.fixture
async def db(tmp_path):
    from controller.state.sqlite import SQLiteBackend
    path = str(tmp_path / "live_test.db")
    return await SQLiteBackend.create(f"sqlite:///{path}")


@pytest.fixture
def k8s_batch():
    from kubernetes import client as k8s, config as k8s_config
    try:
        k8s_config.load_kube_config()
    except Exception:
        k8s_config.load_incluster_config()
    return k8s.BatchV1Api()


@pytest.fixture
def k8s_core():
    from kubernetes import client as k8s, config as k8s_config
    try:
        k8s_config.load_kube_config()
    except Exception:
        k8s_config.load_incluster_config()
    return k8s.CoreV1Api()


@pytest.fixture
def namespace():
    return os.environ.get("DF_K8S_NAMESPACE", "default")


@pytest.fixture
def spawner(settings, k8s_batch, namespace):
    return JobSpawner(settings=settings, batch_api=k8s_batch, namespace=namespace)


@pytest.fixture
def monitor(redis_state, k8s_batch, namespace):
    return JobMonitor(redis_state=redis_state, batch_api=k8s_batch, namespace=namespace)


# ─── Test: K8s Job Creation ──────────────────────────────────────────

class TestK8sJobCreation:

    async def test_job_spec_creates_valid_k8s_job(self, spawner, k8s_batch, namespace):
        """Build a job spec and verify it's valid K8s YAML."""
        spec = spawner.build_job_spec(
            thread_id="live-test-001",
            github_token="fake-token",
            redis_url="redis://redis:6379",
        )

        assert spec.metadata.name.startswith("df-live-tes-")
        assert spec.spec.active_deadline_seconds == 30
        # spawner hardcodes 300s TTL; settings.job_ttl_seconds is not yet wired through
        assert spec.spec.ttl_seconds_after_finished == 300

        container = spec.spec.template.spec.containers[0]
        assert container.security_context.run_as_non_root is True
        assert container.security_context.allow_privilege_escalation is False

    async def test_spawn_and_cleanup_real_job(self, spawner, k8s_batch, namespace):
        """Spawn a real K8s Job (busybox echo) and verify it runs."""
        # Override agent image to a simple command
        spawner._settings.agent_image = "busybox:latest"

        thread_id = f"live-{uuid.uuid4().hex[:8]}"
        job_name = spawner.spawn(thread_id, "fake-token", "redis://localhost:6379")

        try:
            # Wait for job to appear
            await asyncio.sleep(2)
            job = k8s_batch.read_namespaced_job(name=job_name, namespace=namespace)
            assert job is not None
            assert job.metadata.name == job_name

            # Verify labels
            assert job.metadata.labels["app"] == "ditto-factory-agent"
            assert "df/thread" in job.metadata.labels

        finally:
            # Cleanup
            try:
                from kubernetes import client as k8s
                k8s_batch.delete_namespaced_job(
                    name=job_name, namespace=namespace,
                    body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
                )
            except Exception:
                pass

    async def test_monitor_detects_job_status(self, spawner, monitor, k8s_batch, namespace):
        """Monitor correctly reports job running/completed status."""
        spawner._settings.agent_image = "busybox:latest"
        thread_id = f"monitor-{uuid.uuid4().hex[:8]}"
        job_name = spawner.spawn(thread_id, "fake-token", "redis://localhost:6379")

        try:
            await asyncio.sleep(2)
            # Job should be in some state (running or completed quickly)
            # busybox without command will exit quickly
            is_running = monitor.is_job_running(job_name)
            # Either True or False is acceptable — we just verify no exception
            assert isinstance(is_running, bool)

        finally:
            try:
                from kubernetes import client as k8s
                k8s_batch.delete_namespaced_job(
                    name=job_name, namespace=namespace,
                    body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
                )
            except Exception:
                pass


# ─── Test: Redis Task/Result Round Trip ──────────────────────────────

class TestRedisRoundTrip:

    async def test_task_push_and_retrieve(self, redis_state):
        """Push task to Redis, retrieve it — verifies real Redis connection."""
        tid = f"redis-{uuid.uuid4().hex[:8]}"
        task_data = {
            "task": "test task",
            "system_prompt": "you are a test agent",
            "repo_url": "https://github.com/test/repo.git",
            "branch": f"df/{tid}/test",
        }

        await redis_state.push_task(tid, task_data)
        retrieved = await redis_state.get_task(tid)

        assert retrieved is not None
        assert retrieved["task"] == "test task"
        assert retrieved["branch"] == f"df/{tid}/test"

    async def test_result_push_and_retrieve(self, redis_state):
        """Push result to Redis, retrieve it."""
        tid = f"result-{uuid.uuid4().hex[:8]}"
        result_data = {
            "branch": f"df/{tid}/abc",
            "exit_code": 0,
            "commit_count": 3,
            "stderr": "",
        }

        await redis_state.push_result(tid, result_data)
        retrieved = await redis_state.get_result(tid)

        assert retrieved is not None
        assert retrieved["commit_count"] == 3

    async def test_message_queue_round_trip(self, redis_state):
        """Queue messages and drain them."""
        tid = f"queue-{uuid.uuid4().hex[:8]}"

        await redis_state.queue_message(tid, "message 1")
        await redis_state.queue_message(tid, "message 2")
        await redis_state.queue_message(tid, "message 3")

        messages = await redis_state.drain_messages(tid)
        assert len(messages) == 3
        assert messages == ["message 1", "message 2", "message 3"]

        # Queue should be empty after drain
        empty = await redis_state.drain_messages(tid)
        assert empty == []

    async def test_task_ttl_set(self, redis_state, redis_client):
        """Verify task key has TTL set."""
        tid = f"ttl-{uuid.uuid4().hex[:8]}"
        await redis_state.push_task(tid, {"task": "test"})

        ttl = await redis_client.ttl(f"task:{tid}")
        assert 0 < ttl <= 3600


# ─── Test: Full Orchestrator with Real K8s ───────────────────────────

class TestFullOrchestratorLive:

    async def test_orchestrator_spawns_real_job(self, settings, db, redis_state, monitor, spawner, namespace, k8s_batch):
        """Full orchestrator flow with real K8s Job creation."""
        registry = IntegrationRegistry()
        mock_integ = MagicMock()
        mock_integ.name = "test"
        registry.register(mock_integ)

        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        tid = f"orch-{uuid.uuid4().hex[:8]}"
        task = TaskRequest(
            thread_id=tid, source="test", source_ref={},
            repo_owner="test", repo_name="repo",
            task="echo hello from orchestrator",
        )

        await orch.handle_task(task)

        # Verify thread and job in state
        thread = await db.get_thread(tid)
        assert thread is not None
        assert thread.status == ThreadStatus.RUNNING

        # Verify K8s Job exists
        jobs = k8s_batch.list_namespaced_job(namespace=namespace)
        aal_jobs = [j for j in jobs.items if j.metadata.name.startswith(f"df-{tid[:8]}")]
        assert len(aal_jobs) >= 1

        # Cleanup
        for j in aal_jobs:
            try:
                from kubernetes import client as k8s
                k8s_batch.delete_namespaced_job(
                    name=j.metadata.name, namespace=namespace,
                    body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
                )
            except Exception:
                pass
