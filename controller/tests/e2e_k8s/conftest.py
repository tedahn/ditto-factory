"""
Fixtures for full E2E tests against a real kind cluster.

Prerequisites:
  - kind cluster running with local registry at localhost:5001
  - Mock agent image pushed: localhost:5001/mock-agent:latest
  - Redis deployed in cluster (namespace: e2e-ditto-test)

Run with:
  DF_E2E_K8S=1 uv run pytest tests/e2e_k8s/ -v --timeout=180
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from kubernetes import client as k8s, config as k8s_config
from redis.asyncio import Redis

from controller.config import Settings
from controller.models import TaskRequest
from controller.orchestrator import Orchestrator
from controller.state.sqlite import SQLiteBackend
from controller.state.redis_state import RedisState
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.integrations.registry import IntegrationRegistry
from .helpers import cleanup_thread_redis_keys, get_job_logs

logger = logging.getLogger(__name__)

E2E_NAMESPACE = "e2e-ditto-test"
MOCK_AGENT_IMAGE = "localhost:5001/mock-agent:latest"
# In-cluster Redis URL (passed to spawned pods via Settings.redis_url)
REDIS_URL = "redis://redis.e2e-ditto-test.svc.cluster.local:6379"
# Host-reachable Redis URL (via kind NodePort or port-forward), used by
# pytest fixtures for assertions and RedisState operations.
REDIS_HOST_URL = os.getenv("E2E_REDIS_URL", "redis://localhost:16379")

# ── Skip marker ──
skip_unless_e2e = pytest.mark.skipif(
    not os.getenv("DF_E2E_K8S"),
    reason="Set DF_E2E_K8S=1 to run full K8s E2E tests",
)
pytestmark = [skip_unless_e2e, pytest.mark.asyncio, pytest.mark.timeout(180)]


# ── Session-scoped fixtures ──


@pytest.fixture(scope="session")
def k8s_clients():
    """Load kubeconfig (kind) and return API clients."""
    k8s_config.load_kube_config(context="kind-ditto-e2e")
    return {
        "core": k8s.CoreV1Api(),
        "batch": k8s.BatchV1Api(),
    }


@pytest.fixture(scope="session")
def namespace():
    return E2E_NAMESPACE


# ── Per-test fixtures ──


@pytest_asyncio.fixture
async def redis_client():
    """Direct Redis connection (host-side) for assertions."""
    client = Redis.from_url(REDIS_HOST_URL, decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def redis_state(redis_client):
    """RedisState instance backed by host-side Redis."""
    return RedisState(redis_client)


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite state backend, fresh per test."""
    backend = await SQLiteBackend.create("sqlite:///")
    return backend


@pytest.fixture
def settings():
    """Controller settings configured for E2E."""
    return Settings(
        anthropic_api_key="sk-test-not-real-e2e-only",
        redis_url=REDIS_URL,
        agent_image=MOCK_AGENT_IMAGE,
        auto_open_pr=False,
        max_job_duration_seconds=120,
        job_ttl_seconds=60,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )


@pytest.fixture
def spawner(settings, k8s_clients, namespace):
    """Real JobSpawner that creates K8s Jobs in the test namespace."""
    return JobSpawner(
        settings=settings,
        batch_api=k8s_clients["batch"],
        namespace=namespace,
    )


@pytest.fixture
def monitor(redis_state, k8s_clients, namespace):
    """Real JobMonitor backed by test Redis and K8s."""
    return JobMonitor(
        redis_state=redis_state,
        batch_api=k8s_clients["batch"],
        namespace=namespace,
    )


@pytest.fixture
def mock_integration():
    """Mock integration that records report_result calls."""
    integration = AsyncMock()
    integration.name = "test"
    integration.parse_webhook = AsyncMock(return_value=None)
    integration.fetch_context = AsyncMock(return_value="")
    integration.report_result = AsyncMock()
    integration.acknowledge = AsyncMock()
    return integration


@pytest.fixture
def registry(mock_integration):
    """IntegrationRegistry with a single mock 'test' integration."""
    reg = IntegrationRegistry()
    reg.register(mock_integration)
    return reg


@pytest.fixture
def unique_thread_id():
    """Unique thread ID per test to avoid cross-test interference."""
    return uuid.uuid4().hex


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis(redis_state, unique_thread_id):
    """Clean up Redis keys for the test thread after each test."""
    yield
    await cleanup_thread_redis_keys(redis_state, unique_thread_id)


@pytest.fixture
def cleanup_k8s_jobs(k8s_clients, namespace):
    """Factory fixture: returns a function to delete jobs for a thread."""
    deleted_jobs = []

    def _cleanup(thread_id: str):
        from .helpers import get_jobs_for_thread

        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, thread_id)
        for job in jobs:
            try:
                k8s_clients["batch"].delete_namespaced_job(
                    name=job.metadata.name,
                    namespace=namespace,
                    body=k8s.V1DeleteOptions(propagation_policy="Background"),
                )
                deleted_jobs.append(job.metadata.name)
            except k8s.ApiException:
                pass

    yield _cleanup

    # Also run cleanup at teardown for safety
    for job_name in deleted_jobs:
        try:
            k8s_clients["batch"].delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                body=k8s.V1DeleteOptions(propagation_policy="Background"),
            )
        except k8s.ApiException:
            pass


# ── Debug / Observability ──


@pytest.fixture(autouse=True)
def dump_state_on_failure(request, k8s_clients, namespace, redis_client):
    """If a test fails, dump K8s and Redis state to stdout for CI debugging."""
    yield
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        _logger = logging.getLogger("e2e.debug")

        # K8s Jobs
        try:
            jobs = k8s_clients["batch"].list_namespaced_job(namespace=namespace)
            for job in jobs.items:
                _logger.error(
                    "Job %s: succeeded=%s failed=%s",
                    job.metadata.name,
                    job.status.succeeded,
                    job.status.failed,
                )
                logs = get_job_logs(k8s_clients["core"], namespace, job.metadata.name)
                _logger.error("Logs:\n%s", logs)
        except Exception as e:
            _logger.error("Failed to dump K8s state: %s", e)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result on the item for the dump_state_on_failure fixture."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
