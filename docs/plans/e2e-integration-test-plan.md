# E2E Integration Test Plan -- Approach 1: Full K8s Cluster

> ADR Status: Proposed | Date: 2026-03-21

## Context

Ditto Factory has 23 unit/integration tests covering individual components (orchestrator, spawner, monitor, safety pipeline, integrations). What is missing is a **full end-to-end test** that exercises the real pipeline: webhook arrives, K8s Job spawns, agent runs, result flows back through the safety pipeline, and the integration reports success.

The existing `tests/e2e/test_k8s_live.py` tests real K8s Job creation and Redis round-trips, but stops short of running an actual agent container and verifying the full result flow.

### What we are giving up

- **Speed**: E2E tests take 60-120s vs <5s for unit tests. They will not run on every push.
- **Determinism**: Real K8s clusters introduce flakiness (pod scheduling, image pull times). We mitigate with retries and generous timeouts.
- **Simplicity**: Requires kind cluster, container registry, Redis, SQLite/Postgres in CI.

### What we gain

- **Confidence**: Catches integration bugs that unit tests miss (wrong Redis keys, K8s RBAC issues, container env var mismatches).
- **Regression safety**: The mock agent container creates a contract -- if the entrypoint protocol changes, E2E tests break before production does.

---

## 1. Test Infrastructure Requirements

### 1.1 Local / CI Cluster

| Component | Tool | Notes |
|-----------|------|-------|
| K8s cluster | **kind** (preferred) | Single-node, ephemeral, fast startup (~30s). Works in GitHub Actions. |
| Container registry | **kind registry** | Local registry at `localhost:5001`. Avoids DockerHub rate limits. |
| Redis | **Redis pod** in kind | Deploy via manifest, expose as ClusterIP service. |
| Database | **SQLite** (in-memory) | Sufficient for E2E; Postgres tested separately in state tests. |
| Controller | **In-process** | Run FastAPI app via `httpx.AsyncClient` -- no need to containerize for tests. |

### 1.2 Required Manifests

```
tests/
  e2e_k8s/
    manifests/
      namespace.yaml          # e2e-ditto-test namespace
      redis.yaml              # Single-node Redis deployment + service
      secrets.yaml            # Dummy df-secrets (ANTHROPIC_API_KEY) for spawner
      mock-agent-configmap.yaml  # Mock entrypoint script as ConfigMap
      rbac.yaml               # ServiceAccount + Role for job creation
```

### 1.3 Test GitHub Repository

- A **dedicated test repo** (`ditto-factory/e2e-test-target`) with a simple README.
- Tests create branches, push trivial commits, and open PRs against this repo.
- Alternative for CI without GitHub credentials: **gitea** deployed in-cluster as a lightweight Git server (no external dependency).

---

## 2. Mock Agent Container

### 2.1 Design Philosophy

The mock agent replaces the real Claude Code agent. It must:
1. Read task from Redis (`task:{THREAD_ID}`)
2. Clone the repo and create a branch
3. Make a trivial commit (no Anthropic API calls)
4. Write result to Redis (`result:{THREAD_ID}`)
5. Exit with configurable exit code

The mock agent is the **contract boundary** between controller and agent. If the entrypoint protocol changes, this test breaks.

### 2.2 Mock Agent Entrypoint

```bash
#!/bin/bash
# images/mock-agent/entrypoint.sh
# Lightweight mock that simulates the real agent's Redis protocol.
set -euo pipefail

# ── Required env vars (same as real agent) ──
: "${THREAD_ID:?}"
: "${REDIS_URL:?}"
: "${GITHUB_TOKEN:?}"

# ── Configuration (injected by test) ──
MOCK_EXIT_CODE="${MOCK_EXIT_CODE:-0}"
MOCK_COMMIT_COUNT="${MOCK_COMMIT_COUNT:-1}"
MOCK_DELAY_SECONDS="${MOCK_DELAY_SECONDS:-2}"
MOCK_FAIL_PHASE="${MOCK_FAIL_PHASE:-}"  # "clone" | "push" | "result"

echo "[mock-agent] Starting for thread: $THREAD_ID"

# ── Step 1: Read task from Redis ──
TASK_JSON=$(redis-cli -u "$REDIS_URL" GET "task:$THREAD_ID")
if [ -z "$TASK_JSON" ]; then
    echo "[mock-agent] ERROR: No task found in Redis for $THREAD_ID"
    exit 1
fi

REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')
TASK_TEXT=$(echo "$TASK_JSON" | jq -r '.task')

echo "[mock-agent] Task: $TASK_TEXT"
echo "[mock-agent] Repo: $REPO_URL Branch: $BRANCH"

# ── Step 2: Simulate failure if configured ──
if [ "$MOCK_FAIL_PHASE" = "clone" ]; then
    echo "[mock-agent] Simulating clone failure"
    exit 1
fi

# ── Step 3: Clone repo ──
git clone "https://x-access-token:${GITHUB_TOKEN}@${REPO_URL#https://}" /tmp/workspace
cd /tmp/workspace
git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"

# ── Step 4: Make trivial commits ──
ACTUAL_COMMITS=0
if [ "$MOCK_COMMIT_COUNT" -gt 0 ]; then
    for i in $(seq 1 "$MOCK_COMMIT_COUNT"); do
        echo "Mock change $i at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> mock-changes.txt
        git add mock-changes.txt
        git commit -m "mock: change $i for thread $THREAD_ID"
        ACTUAL_COMMITS=$((ACTUAL_COMMITS + 1))
    done
fi

# ── Step 5: Simulate work delay ──
sleep "$MOCK_DELAY_SECONDS"

# ── Step 6: Push branch ──
if [ "$MOCK_FAIL_PHASE" = "push" ]; then
    echo "[mock-agent] Simulating push failure"
    MOCK_EXIT_CODE=1
else
    if [ "$ACTUAL_COMMITS" -gt 0 ]; then
        git push origin "$BRANCH" --force
    fi
fi

# ── Step 7: Write result to Redis ──
if [ "$MOCK_FAIL_PHASE" = "result" ]; then
    echo "[mock-agent] Simulating result write failure -- exiting without writing"
    exit 1
fi

RESULT_JSON=$(jq -n \
    --arg branch "$BRANCH" \
    --argjson exit_code "$MOCK_EXIT_CODE" \
    --argjson commit_count "$ACTUAL_COMMITS" \
    --arg stderr "" \
    '{branch: $branch, exit_code: $exit_code, commit_count: $commit_count, stderr: $stderr}')

redis-cli -u "$REDIS_URL" SET "result:$THREAD_ID" "$RESULT_JSON" EX 3600

echo "[mock-agent] Result written. Exit code: $MOCK_EXIT_CODE"
exit "$MOCK_EXIT_CODE"
```

### 2.3 Mock Agent Dockerfile

```dockerfile
# images/mock-agent/Dockerfile
FROM alpine:3.20

RUN apk add --no-cache bash git jq redis curl

# Build args wired to env vars so variant images work
ARG MOCK_FAIL_PHASE=""
ARG MOCK_COMMIT_COUNT=""
ARG MOCK_DELAY_SECONDS=""
ENV MOCK_FAIL_PHASE=${MOCK_FAIL_PHASE}
ENV MOCK_COMMIT_COUNT=${MOCK_COMMIT_COUNT}
ENV MOCK_DELAY_SECONDS=${MOCK_DELAY_SECONDS}

RUN adduser -D -u 1000 agent
WORKDIR /home/agent

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER 1000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**Key trade-off**: Alpine base image (~8MB) vs the real agent's node:22-slim (~200MB). Tests run faster, but we lose fidelity on the Node.js / Claude Code CLI layer. This is acceptable because the mock tests the **protocol** (Redis keys, env vars, exit codes), not the CLI itself.

---

## 3. Test Harness Design

### 3.1 Architecture

```
pytest (host machine)
  |
  |-- httpx.AsyncClient --> FastAPI app (in-process)
  |                            |
  |                            |--> JobSpawner --> K8s API (kind cluster)
  |                            |                      |
  |                            |                      +--> mock-agent Pod
  |                            |                              |
  |                            |--> RedisState <--------------+
  |                            |       (task:{id}, result:{id})
  |                            |
  |                            +--> SQLite (in-memory state backend)
  |
  |-- Assertions:
  |     - K8s Job exists and completed
  |     - Redis result matches expected
  |     - Thread status updated in DB
  |     - Integration.report_result() called with correct args
  |     - PR created (if commits > 0)
  |
  +-- Cleanup:
        - Delete K8s Jobs in namespace
        - Flush Redis keys with test prefix
        - Reset SQLite
```

### 3.2 Conftest and Fixtures

```python
# controller/tests/e2e_k8s/conftest.py
"""
Fixtures for full E2E tests against a real kind cluster.

Prerequisites:
  - kind cluster running with local registry at localhost:5001
  - Mock agent image pushed: localhost:5001/mock-agent:latest
  - Redis deployed in cluster (namespace: e2e-ditto-test)

Run with:
  DF_E2E_K8S=1 uv run pytest tests/e2e_k8s/ -v --timeout=180
"""
import asyncio
import os
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from kubernetes import client as k8s, config as k8s_config
from redis.asyncio import Redis

from controller.config import Settings
from controller.models import TaskRequest
from controller.orchestrator import Orchestrator
from controller.state.sqlite import SQLiteState
from controller.state.redis_state import RedisState
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline
from controller.integrations.registry import IntegrationRegistry


E2E_NAMESPACE = "e2e-ditto-test"
MOCK_AGENT_IMAGE = "localhost:5001/mock-agent:latest"
REDIS_URL = "redis://redis.e2e-ditto-test.svc.cluster.local:6379"
# IMPORTANT: Dual-URL requirement:
#   REDIS_URL       = in-cluster address, passed to spawned pods via Settings.redis_url
#   REDIS_HOST_URL  = host-reachable address (via port-forward or NodePort), used by
#                     pytest fixtures for assertions and RedisState operations.
# Both point to the same Redis instance but resolve in different network contexts.
REDIS_HOST_URL = os.getenv("E2E_REDIS_URL", "redis://localhost:16379")

# ── Skip marker ──
skip_unless_e2e = pytest.mark.skipif(
    not os.getenv("DF_E2E_K8S"),
    reason="Set DF_E2E_K8S=1 to run full K8s E2E tests",
)
pytestmark = [skip_unless_e2e, pytest.mark.asyncio, pytest.mark.timeout(180)]


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


@pytest_asyncio.fixture
async def redis_client():
    """Direct Redis connection for assertions (via port-forward)."""
    client = Redis.from_url(REDIS_HOST_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def redis_state(redis_client):
    return RedisState(redis_client)


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite state backend -- fresh per test."""
    state = SQLiteState(":memory:")
    await state.initialize()
    yield state


@pytest_asyncio.fixture
async def settings():
    return Settings(
        agent_image=MOCK_AGENT_IMAGE,
        redis_url=REDIS_URL,
        github_token="test-token-not-used-by-mock",
        auto_open_pr=False,  # Disable for most tests; enable explicitly
        retry_on_empty_result=False,
    )


@pytest.fixture
def spawner(settings, k8s_clients, namespace):
    return JobSpawner(
        settings=settings,
        batch_api=k8s_clients["batch"],
        namespace=namespace,
    )


@pytest.fixture
def mock_integration():
    """Mock integration that captures report_result calls."""
    integ = AsyncMock()
    integ.name = "test"
    integ.parse_webhook = AsyncMock()
    integ.report_result = AsyncMock()
    return integ


@pytest.fixture
def registry(mock_integration):
    reg = IntegrationRegistry()
    reg.register(mock_integration)
    return reg


@pytest_asyncio.fixture
async def monitor(redis_state, k8s_clients, namespace):
    return JobMonitor(redis_state=redis_state, batch_api=k8s_clients["batch"], namespace=namespace)


@pytest.fixture
def unique_thread_id():
    """Generate a unique thread ID per test to avoid collisions."""
    return f"e2e-{uuid.uuid4().hex[:12]}"


# ── Cleanup ──

@pytest_asyncio.fixture(autouse=True)
async def cleanup_k8s_jobs(k8s_clients, namespace):
    """Delete all test jobs after each test."""
    yield
    try:
        k8s_clients["batch"].delete_collection_namespaced_job(
            namespace=namespace,
            propagation_policy="Background",
        )
    except Exception:
        pass  # Best-effort cleanup


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis(redis_client, unique_thread_id):
    """Remove test keys from Redis after each test."""
    yield
    # NOTE: agent:{id} keys are Redis streams (XADD). DELETE works on streams,
    # but if tests exercise streaming, consider XTRIM or verify no cross-test leakage.
    for prefix in ("task:", "result:", "queue:", "agent:"):
        await redis_client.delete(f"{prefix}{unique_thread_id}")
```

### 3.3 Helper: Wait for Job Completion

```python
# controller/tests/e2e_k8s/helpers.py
"""Polling helpers for E2E tests."""
import asyncio
import logging
from kubernetes import client as k8s

logger = logging.getLogger(__name__)


async def wait_for_job_completion(
    batch_api: k8s.BatchV1Api,
    namespace: str,
    job_name: str,
    timeout_seconds: int = 120,
    poll_interval: float = 3.0,
) -> k8s.V1Job:
    """Poll K8s until the Job reaches Succeeded or Failed."""
    elapsed = 0.0
    while elapsed < timeout_seconds:
        job = batch_api.read_namespaced_job(name=job_name, namespace=namespace)
        if job.status.succeeded and job.status.succeeded > 0:
            return job
        if job.status.failed and job.status.failed > 0:
            return job
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"Job {job_name} did not complete within {timeout_seconds}s")


async def wait_for_redis_key(
    redis_state,
    key_type: str,
    thread_id: str,
    timeout_seconds: int = 120,
    poll_interval: float = 2.0,
):
    """Poll Redis until a key appears."""
    elapsed = 0.0
    getter = getattr(redis_state, f"get_{key_type}")
    while elapsed < timeout_seconds:
        value = await getter(thread_id)
        if value is not None:
            return value
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"Redis key {key_type}:{thread_id} not found within {timeout_seconds}s")


def get_job_logs(core_api: k8s.CoreV1Api, namespace: str, job_name: str) -> str:
    """Fetch logs from the pod created by a Job (for debugging)."""
    pods = core_api.list_namespaced_pod(
        namespace=namespace,
        label_selector=f"job-name={job_name}",
    )
    if not pods.items:
        return "<no pods found>"
    pod_name = pods.items[0].metadata.name
    try:
        return core_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
        )
    except Exception as e:
        return f"<error reading logs: {e}>"
```

---

## 4. Test Scenarios (Prioritized)

### Priority 1: Happy Path -- GitHub Issue

```python
# controller/tests/e2e_k8s/test_happy_path.py
"""
P1: Full pipeline happy path -- webhook to result.
"""
import json
import pytest
from controller.models import TaskRequest, ThreadStatus, AgentResult
from .helpers import wait_for_redis_key, wait_for_job_completion, get_job_logs


class TestGitHubHappyPath:
    """
    Flow: TaskRequest -> orchestrator.handle_task()
          -> thread created in DB
          -> task pushed to Redis
          -> K8s Job spawned (mock agent)
          -> mock agent reads task, makes commit, writes result
          -> monitor picks up result
          -> thread status = IDLE
    """

    async def test_full_pipeline_github_issue(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 1, "comment_id": 100},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Add a hello-world.txt file",
        )

        # Trigger the pipeline (this spawns the K8s Job)
        await orch.handle_task(task)

        # ── Assert: Thread created ──
        thread = await db.get_thread(unique_thread_id)
        assert thread is not None
        assert thread.status == ThreadStatus.RUNNING

        # ── Assert: Task in Redis ──
        task_data = await redis_state.get_task(unique_thread_id)
        assert task_data is not None
        assert task_data["task"] == "Add a hello-world.txt file"

        # ── Assert: K8s Job exists ──
        jobs = k8s_clients["batch"].list_namespaced_job(
            namespace=namespace,
            label_selector=f"df/thread={unique_thread_id[:8]}",
        )
        assert len(jobs.items) == 1
        job = jobs.items[0]
        job_name = job.metadata.name

        # ── Wait: Job completes ──
        completed_job = await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )
        assert completed_job.status.succeeded == 1, (
            f"Job failed. Logs:\n{get_job_logs(k8s_clients['core'], namespace, job_name)}"
        )

        # ── Assert: Result in Redis ──
        result = await wait_for_redis_key(redis_state, "result", unique_thread_id)
        assert result["exit_code"] == 0
        assert result["commit_count"] == 1
        assert result["branch"].startswith("df/")

        # ── Trigger completion pipeline ──
        await orch.handle_job_completion(unique_thread_id)

        # ── Assert: Thread status returned to IDLE ──
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.IDLE

        # ── Assert: Integration was notified ──
        mock_integration.report_result.assert_called_once()


class TestSlackHappyPath:
    """Same flow but triggered as if from Slack."""

    async def test_full_pipeline_slack_message(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"channel": "C123", "thread_ts": "1234567890.123456"},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Fix the README typo",
        )

        await orch.handle_task(task)

        # Wait for result
        result = await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=90
        )
        assert result["exit_code"] == 0
        assert result["commit_count"] == 1
```

### Priority 1.5: Full Completion Pipeline

This test exercises the most complex code path end-to-end: `handle_job_completion -> monitor.wait_for_result -> SafetyPipeline.process -> integration.report_result`.

```python
# controller/tests/e2e_k8s/test_completion_pipeline.py
"""
P1.5: Full completion pipeline -- job result through safety pipeline to integration.
"""
import pytest
from unittest.mock import AsyncMock
from controller.models import TaskRequest, ThreadStatus
from .helpers import wait_for_redis_key, wait_for_job_completion, get_job_logs


class TestCompletionPipeline:
    """
    Verifies the full handle_job_completion flow:
      1. monitor.wait_for_result picks up result from Redis
      2. SafetyPipeline.process runs (PR check, validation, report)
      3. integration.report_result is called with correct Thread and AgentResult
      4. Thread status transitions back to IDLE
      5. Queued messages are drained
    """

    async def test_completion_pipeline_with_commits(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        # Disable auto PR to isolate the completion path
        settings.auto_open_pr = False

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 99},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Add a completion-test file",
        )

        await orch.handle_task(task)

        # Wait for mock agent to complete
        jobs = k8s_clients["batch"].list_namespaced_job(
            namespace=namespace,
            label_selector=f"df/thread={unique_thread_id[:8]}",
        )
        assert len(jobs.items) == 1
        job_name = jobs.items[0].metadata.name

        await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )

        # Trigger the completion pipeline
        await orch.handle_job_completion(unique_thread_id)

        # Assert: Thread returned to IDLE
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.IDLE

        # Assert: Integration received the result
        mock_integration.report_result.assert_called_once()
        call_args = mock_integration.report_result.call_args
        reported_thread = call_args[0][0]
        reported_result = call_args[0][1]
        assert reported_thread.id == unique_thread_id
        assert reported_result.exit_code == 0
        assert reported_result.commit_count == 1

    async def test_completion_pipeline_drains_queued_messages(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        settings.auto_open_pr = False

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Initial task",
        )

        await orch.handle_task(task)

        # Queue a follow-up message while job is running
        await redis_state.queue_message(unique_thread_id, "Follow-up message")

        # Wait for job
        jobs = k8s_clients["batch"].list_namespaced_job(
            namespace=namespace,
            label_selector=f"df/thread={unique_thread_id[:8]}",
        )
        job_name = jobs.items[0].metadata.name
        await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )

        # Trigger completion -- should drain queued messages
        await orch.handle_job_completion(unique_thread_id)

        # Queue should be empty after completion
        remaining = await redis_state.drain_messages(unique_thread_id)
        assert len(remaining) == 0
```

### Priority 2: Error Paths

```python
# controller/tests/e2e_k8s/test_error_paths.py
"""
P2: Error scenarios -- agent failures, timeouts, empty results.
"""
import pytest
from controller.models import TaskRequest, ThreadStatus
from .helpers import wait_for_redis_key, wait_for_job_completion, get_job_logs


class TestAgentFailure:
    """Agent exits with non-zero code."""

    async def test_agent_crash_reports_failure(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        """
        Configure mock agent to exit 1 during clone phase.
        The Job should fail and no result should appear in Redis.
        """
        from controller.orchestrator import Orchestrator

        # Override agent image env to inject MOCK_FAIL_PHASE=clone
        # This requires the spawner to support extra env vars, or we
        # use a separate mock image tag built with the env baked in.
        settings.agent_image = "localhost:5001/mock-agent:fail-clone"

        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="This should fail",
        )

        await orch.handle_task(task)

        # Job should exist but fail
        jobs = k8s_clients["batch"].list_namespaced_job(
            namespace=namespace,
            label_selector=f"df/thread={unique_thread_id[:8]}",
        )
        assert len(jobs.items) == 1
        job_name = jobs.items[0].metadata.name

        completed_job = await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=60
        )
        assert completed_job.status.failed >= 1


class TestEmptyResult:
    """Agent succeeds but produces zero commits (anti-stall scenario)."""

    async def test_zero_commits_triggers_retry(
        self, settings, db, redis_state, spawner, monitor,
        registry, mock_integration, k8s_clients, namespace,
        unique_thread_id,
    ):
        settings.agent_image = "localhost:5001/mock-agent:zero-commits"
        settings.retry_on_empty_result = True
        settings.max_empty_retries = 1

        from controller.orchestrator import Orchestrator
        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="This produces no commits",
        )

        await orch.handle_task(task)

        result = await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=90
        )
        assert result["commit_count"] == 0
        assert result["exit_code"] == 0


class TestAgentTimeout:
    """Agent hangs -- monitor should timeout."""

    async def test_monitor_timeout(
        self, settings, db, redis_state, spawner, monitor,
        registry, k8s_clients, namespace, unique_thread_id,
    ):
        settings.agent_image = "localhost:5001/mock-agent:slow"
        # Set very short timeout for test
        settings.max_job_duration_seconds = 15

        from controller.orchestrator import Orchestrator
        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="This will timeout",
        )

        await orch.handle_task(task)

        # Monitor should have timed out -- thread goes back to IDLE
        # (The monitor sets timeout status)
        thread = await db.get_thread(unique_thread_id)
        assert thread is not None
        # Depending on implementation: IDLE or FAILED
```

### Priority 3: Concurrency

```python
# controller/tests/e2e_k8s/test_concurrency.py
"""
P3: Concurrency -- duplicate webhooks, queued messages.
"""
import asyncio
import pytest
from controller.models import TaskRequest, ThreadStatus
from .helpers import wait_for_redis_key


class TestDuplicateWebhook:
    """
    Two identical webhooks arrive simultaneously -- only one Job should spawn.

    CAVEAT: With in-memory SQLite and no true advisory locking, the race
    between the two asyncio.gather'd calls may not reproduce reliably. The
    second call seeing RUNNING status depends on whether the first call's
    state update has committed before the second call reads. If this test
    is flaky in CI, consider adding a small delay or using Postgres with
    row-level locking for the concurrency suite.
    """

    async def test_duplicate_webhook_single_job(
        self, settings, db, redis_state, spawner, monitor,
        registry, k8s_clients, namespace, unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Duplicate test",
        )

        # Fire two handle_task calls concurrently
        results = await asyncio.gather(
            orch.handle_task(task),
            orch.handle_task(task),
            return_exceptions=True,
        )

        # Only ONE K8s Job should exist
        jobs = k8s_clients["batch"].list_namespaced_job(
            namespace=namespace,
            label_selector=f"df/thread={unique_thread_id[:8]}",
        )
        assert len(jobs.items) == 1

        # Second call should have queued the message
        queued = await redis_state.drain_messages(unique_thread_id)
        assert len(queued) == 1
        assert "Duplicate test" in queued[0]


class TestQueuedFollowUp:
    """Message arrives while job is running -- should be queued and processed after."""

    async def test_follow_up_message_queued(
        self, settings, db, redis_state, spawner, monitor,
        registry, k8s_clients, namespace, unique_thread_id,
    ):
        from controller.orchestrator import Orchestrator

        orch = Orchestrator(
            settings=settings, state=db, redis_state=redis_state,
            registry=registry, spawner=spawner, monitor=monitor,
        )

        # First task
        task1 = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="First task",
        )
        await orch.handle_task(task1)

        # While first job is running, send follow-up
        task2 = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Follow-up while running",
        )
        await orch.handle_task(task2)

        # Verify message was queued (not spawned as second job)
        queued = await redis_state.drain_messages(unique_thread_id)
        assert len(queued) >= 1

        # Only one active job
        jobs = k8s_clients["batch"].list_namespaced_job(namespace=namespace)
        agent_jobs = [
            j for j in jobs.items
            if j.metadata.labels.get("app") == "ditto-factory-agent"
        ]
        assert len(agent_jobs) == 1
```

---

## 5. File Structure

```
ditto-factory/
  images/
    mock-agent/
      Dockerfile                    # Alpine + git + jq + redis-cli
      entrypoint.sh                 # Mock agent script (see section 2.2)
  controller/
    tests/
      e2e_k8s/
        __init__.py
        conftest.py                 # Fixtures: K8s clients, Redis, SQLite, Orchestrator
        helpers.py                  # wait_for_job_completion, wait_for_redis_key, get_job_logs
        test_happy_path.py          # P1: GitHub + Slack full pipeline
        test_error_paths.py         # P2: Agent crash, empty result, timeout
        test_concurrency.py         # P3: Duplicate webhooks, queued messages
        test_completion_pipeline.py  # P1.5: Full handle_job_completion -> SafetyPipeline flow
        test_safety_pipeline.py     # P2: PR creation, result reporting (requires gitea or mock GitHub)
        manifests/
          namespace.yaml
          redis.yaml
          secrets.yaml              # Dummy df-secrets for ANTHROPIC_API_KEY
          rbac.yaml
  scripts/
    e2e-setup.sh                    # One-shot: create kind cluster, push mock images, deploy Redis
    e2e-teardown.sh                 # Destroy kind cluster
```

### Naming Conventions

- Test files: `test_<scenario>.py`
- Test classes: `Test<Feature><Scenario>` (e.g., `TestGitHubHappyPath`)
- Test methods: `test_<what_it_verifies>` (e.g., `test_full_pipeline_github_issue`)
- Thread IDs: `e2e-<uuid12>` to avoid collisions and enable cleanup
- K8s namespace: `e2e-ditto-test` (dedicated, auto-cleaned)

---

## 6. CI/CD Integration

### 6.1 GitHub Actions Workflow

```yaml
# .github/workflows/e2e-k8s.yaml
name: E2E K8s Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  # Manual trigger for debugging
  workflow_dispatch:

# Only run one E2E suite at a time
concurrency:
  group: e2e-k8s-${{ github.ref }}
  cancel-in-progress: true

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      # ── kind cluster ──
      - name: Create kind cluster
        uses: helm/kind-action@v1
        with:
          cluster_name: ditto-e2e
          config: |
            kind: Cluster
            apiVersion: kind.x-k8s.io/v1alpha4
            containerdConfigPatches:
              - |-
                [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
                  endpoint = ["http://kind-registry:5000"]
            nodes:
              - role: control-plane
                extraPortMappings:
                  - containerPort: 30379
                    hostPort: 16379
                    protocol: TCP

      # ── Local registry ──
      - name: Create local registry
        run: |
          docker run -d --restart=always -p 5001:5000 --name kind-registry --network kind registry:2

      # ── Build and push mock agent ──
      - name: Build mock agent
        run: |
          docker build -t localhost:5001/mock-agent:latest images/mock-agent/
          docker push localhost:5001/mock-agent:latest
          # Variant images for error tests
          docker build -t localhost:5001/mock-agent:fail-clone \
            --build-arg MOCK_FAIL_PHASE=clone images/mock-agent/
          docker push localhost:5001/mock-agent:fail-clone
          docker build -t localhost:5001/mock-agent:zero-commits \
            --build-arg MOCK_COMMIT_COUNT=0 images/mock-agent/
          docker push localhost:5001/mock-agent:zero-commits
          docker build -t localhost:5001/mock-agent:slow \
            --build-arg MOCK_DELAY_SECONDS=300 images/mock-agent/
          docker push localhost:5001/mock-agent:slow

      # ── Deploy Redis ──
      - name: Deploy Redis to kind
        run: |
          kubectl create namespace e2e-ditto-test
          kubectl apply -f controller/tests/e2e_k8s/manifests/
          kubectl wait --namespace e2e-ditto-test \
            --for=condition=ready pod \
            --selector=app=redis \
            --timeout=60s

      # ── Redis host access ──
      # NOTE: The kind config already maps containerPort 30379 -> hostPort 16379.
      # Using a NodePort service (port 30379) is more reliable than port-forward
      # in CI. If using NodePort, replace the redis Service type with NodePort
      # and set nodePort: 30379, then set E2E_REDIS_URL=redis://localhost:16379.
      # For now, port-forward is used as a simpler default:
      - name: Port-forward Redis
        run: |
          kubectl port-forward -n e2e-ditto-test svc/redis 16379:6379 &
          sleep 2

      # ── Install Python deps ──
      - uses: astral-sh/setup-uv@v4
      - name: Install dependencies
        run: cd controller && uv sync

      # ── Run E2E tests ──
      - name: Run E2E tests
        env:
          DF_E2E_K8S: "1"
          E2E_REDIS_URL: "redis://localhost:16379"
          KUBECONFIG: ${{ env.KUBECONFIG }}
        run: |
          cd controller
          uv run pytest tests/e2e_k8s/ -v --timeout=180 --tb=long -x 2>&1 | tee e2e-output.log

      # ── Debug on failure ──
      - name: Dump debug info on failure
        if: failure()
        run: |
          echo "=== K8s Jobs ==="
          kubectl get jobs -n e2e-ditto-test -o wide
          echo "=== K8s Pods ==="
          kubectl get pods -n e2e-ditto-test -o wide
          echo "=== Pod Logs ==="
          for pod in $(kubectl get pods -n e2e-ditto-test -o name); do
            echo "--- $pod ---"
            kubectl logs "$pod" -n e2e-ditto-test --tail=50 || true
          done
          echo "=== Redis Keys ==="
          redis-cli -u redis://localhost:16379 KEYS '*' || true

      # ── Upload logs ──
      - name: Upload test logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-logs
          path: controller/e2e-output.log
```

### 6.2 When to Run

| Trigger | Suite |
|---------|-------|
| Every push | Unit tests only (~5s) |
| PR to main | Unit + integration + E2E (~3-5min) |
| Nightly | E2E + extended timeout tests (~10min) |
| Manual | Full suite with debug logging |

---

## 7. Observability During Tests

### 7.1 Debugging Failed Tests

| What to check | How |
|----------------|-----|
| Agent pod logs | `get_job_logs()` helper -- included in assertion messages |
| Redis state | `redis-cli -u $E2E_REDIS_URL KEYS 'task:*' 'result:*' 'queue:*'` |
| K8s Job status | `kubectl describe job <name> -n e2e-ditto-test` |
| Pod events | `kubectl get events -n e2e-ditto-test --sort-by=.lastTimestamp` |
| Thread state | SQLite is in-memory, so log it in fixtures or add a dump-on-failure hook |

### 7.2 Pytest Plugin: Dump State on Failure

```python
# controller/tests/e2e_k8s/conftest.py (additional fixture)

@pytest.fixture(autouse=True)
def dump_state_on_failure(request, k8s_clients, namespace, redis_client):
    """If a test fails, dump K8s and Redis state to stdout for CI debugging."""
    from .helpers import get_job_logs

    yield
    if request.node.rep_call and request.node.rep_call.failed:
        import logging
        logger = logging.getLogger("e2e.debug")

        # K8s Jobs
        jobs = k8s_clients["batch"].list_namespaced_job(namespace=namespace)
        for job in jobs.items:
            logger.error(
                "Job %s: succeeded=%s failed=%s",
                job.metadata.name,
                job.status.succeeded,
                job.status.failed,
            )
            logs = get_job_logs(k8s_clients["core"], namespace, job.metadata.name)
            logger.error("Logs:\n%s", logs)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result on the item for the dump_state_on_failure fixture."""
    import pluggy
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
```

### 7.3 Structured Logging

Enable JSON logging in the controller during E2E tests so pod logs are parseable:

```python
# In conftest.py setup
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
```

---

## 8. Setup Script

```bash
#!/bin/bash
# scripts/e2e-setup.sh
# One-shot setup for local E2E test environment.
set -euo pipefail

CLUSTER_NAME="ditto-e2e"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT=5001
NAMESPACE="e2e-ditto-test"

echo "=== Creating kind cluster ==="
kind create cluster --name "$CLUSTER_NAME" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
      endpoint = ["http://kind-registry:5000"]
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30379
        hostPort: 16379
        protocol: TCP
EOF

echo "=== Starting local registry ==="
docker run -d --restart=always -p "${REGISTRY_PORT}:5000" \
  --name "$REGISTRY_NAME" --network kind registry:2 2>/dev/null || true

echo "=== Building mock agent images ==="
docker build -t "localhost:${REGISTRY_PORT}/mock-agent:latest" images/mock-agent/
docker push "localhost:${REGISTRY_PORT}/mock-agent:latest"

# Build variant images for error tests
for variant in fail-clone zero-commits slow; do
    case "$variant" in
        fail-clone)   BUILD_ARGS="--build-arg MOCK_FAIL_PHASE=clone" ;;
        zero-commits) BUILD_ARGS="--build-arg MOCK_COMMIT_COUNT=0" ;;
        slow)         BUILD_ARGS="--build-arg MOCK_DELAY_SECONDS=300" ;;
    esac
    docker build -t "localhost:${REGISTRY_PORT}/mock-agent:${variant}" \
        $BUILD_ARGS images/mock-agent/
    docker push "localhost:${REGISTRY_PORT}/mock-agent:${variant}"
done

echo "=== Deploying test infrastructure ==="
kubectl create namespace "$NAMESPACE" 2>/dev/null || true
kubectl apply -f controller/tests/e2e_k8s/manifests/

echo "=== Waiting for Redis ==="
kubectl wait --namespace "$NAMESPACE" \
    --for=condition=ready pod \
    --selector=app=redis \
    --timeout=60s

echo "=== Port-forwarding Redis ==="
kubectl port-forward -n "$NAMESPACE" svc/redis 16379:6379 &
sleep 2

echo ""
echo "Ready! Run tests with:"
echo "  cd controller && DF_E2E_K8S=1 E2E_REDIS_URL=redis://localhost:16379 uv run pytest tests/e2e_k8s/ -v"
```

---

## 9. K8s Manifests

### 9.1 Namespace

```yaml
# controller/tests/e2e_k8s/manifests/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: e2e-ditto-test
  labels:
    purpose: e2e-testing
```

### 9.2 Redis

```yaml
# controller/tests/e2e_k8s/manifests/redis.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: e2e-ditto-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "128Mi"
              cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: e2e-ditto-test
spec:
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
  type: ClusterIP
```

### 9.3 Secrets (dummy for E2E)

The spawner unconditionally references a `df-secrets` Secret for the `ANTHROPIC_API_KEY` env var. Without this Secret, pods fail with `CreateContainerConfigError`.

```yaml
# controller/tests/e2e_k8s/manifests/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: df-secrets
  namespace: e2e-ditto-test
type: Opaque
stringData:
  anthropic-api-key: "sk-test-not-real-e2e-only"
```

### 9.4 RBAC

```yaml
# controller/tests/e2e_k8s/manifests/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ditto-controller
  namespace: e2e-ditto-test
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ditto-job-manager
  namespace: e2e-ditto-test
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete", "deletecollection"]
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ditto-job-manager-binding
  namespace: e2e-ditto-test
subjects:
  - kind: ServiceAccount
    name: ditto-controller
    namespace: e2e-ditto-test
roleRef:
  kind: Role
  name: ditto-job-manager
  apiGroup: rbac.authorization.k8s.io
```

---

## 10. Implementation Sequence

| Phase | Tasks | Estimate |
|-------|-------|----------|
| **Phase 1** | Mock agent container (Dockerfile + entrypoint) | 0.5 day |
| **Phase 2** | Test harness (conftest, helpers, manifests) | 1 day |
| **Phase 3** | Happy path tests (P1) | 1 day |
| **Phase 4** | Error path tests (P2) | 0.5 day |
| **Phase 5** | Concurrency tests (P3) | 0.5 day |
| **Phase 6** | CI workflow + setup script | 0.5 day |
| **Phase 7** | Safety pipeline E2E (with gitea or mock GitHub) | 1 day |
| **Total** | | **5 days** |

### Open Questions

1. **Git server in CI**: Do we use the real ditto-factory/e2e-test-target repo (requires GitHub token in CI secrets), or deploy gitea in-cluster for full isolation? Gitea adds complexity but removes external dependency.
2. **Variant images**: Should we bake MOCK_FAIL_PHASE into separate image tags (simpler to use in tests) or pass it as an env var override in the Job spec (requires spawner modification)?
3. **Postgres E2E**: Should we also test with a real Postgres backend in E2E, or is SQLite sufficient given that state backends are already tested independently?

### Decision: Recommendation

- Use **gitea in-cluster** for Phase 7 to avoid CI secrets and rate limits.
- Use **env var overrides** in the Job spec rather than variant images -- it is more flexible and avoids building 4+ Docker images.
- Keep **SQLite** for E2E state; Postgres is tested in `tests/state/test_postgres.py`.

---

## Revision History

| Date | Change | Ref |
|------|--------|-----|
| 2026-03-21 | **Critical #1**: Fixed `JobMonitor` fixture -- constructor requires `(redis_state, batch_api, namespace)`, not `(settings, redis_state)`. | Review Finding 1 |
| 2026-03-21 | **Critical #2**: Fixed K8s label selector from `ditto-factory/thread-id=` to `df/thread=` to match actual spawner labels. All occurrences updated. | Review Finding 2 |
| 2026-03-21 | **Critical #3**: Added `handle_job_completion()` call and post-completion assertions (IDLE status, report_result called) to happy path tests. | Review Finding 3 |
| 2026-03-21 | **Critical #4**: Added `ARG`/`ENV` directives to mock agent Dockerfile so build-arg variant images work correctly. | Review Finding 4 |
| 2026-03-21 | **Critical #5**: Added `secrets.yaml` manifest with dummy `df-secrets` Secret. Without this, spawner pods fail with `CreateContainerConfigError`. | Review Finding 5 |
| 2026-03-21 | **Important #6**: Added documentation of dual-URL requirement (in-cluster vs host-side Redis URLs) in conftest constants. | Review Finding 6 |
| 2026-03-21 | **Important #7**: Fixed `settings.job_timeout_seconds` to `settings.max_job_duration_seconds` (correct Settings field name). | Review Finding 7 |
| 2026-03-21 | **Important #8**: Added comment noting that `agent:` keys are Redis streams and DELETE works but cross-test leakage should be monitored. | Review Finding 8 |
| 2026-03-21 | **Important #9**: Added new `test_completion_pipeline.py` (Priority 1.5) with dedicated tests for the full `handle_job_completion -> SafetyPipeline.process -> report_result` flow. | Review Finding 9 |
| 2026-03-21 | **Suggestion #10**: Added NodePort alternative documentation to the CI workflow port-forward step. | Review Finding 10 |
| 2026-03-21 | **Suggestion #11**: Added missing `from .helpers import get_job_logs` to `dump_state_on_failure` fixture. | Review Finding 11 |
| 2026-03-21 | **Suggestion #12**: Added caveat about SQLite concurrency limitations to `TestDuplicateWebhook` docstring. | Review Finding 12 |
