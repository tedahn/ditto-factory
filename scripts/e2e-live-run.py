#!/usr/bin/env python3
"""
E2E live run: exercises the full Ditto Factory pipeline against a real GitHub repo.

Prerequisites:
  - kind cluster running (scripts/e2e-setup.sh)
  - GH_TOKEN env var set (or gh auth token)
  - ANTHROPIC_API_KEY env var set (dummy value OK for mock agent)

Usage:
  source .venv/bin/activate
  export GH_TOKEN=$(gh auth token)
  python scripts/e2e-live-run.py
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid

# Add controller src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "controller", "src"))

from controller.config import Settings
from controller.models import TaskRequest, Thread, ThreadStatus, AgentResult
from controller.state.redis_state import RedisState
from controller.state.sqlite import SQLiteBackend
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline
from kubernetes import client as k8s, config as k8s_config
from redis.asyncio import Redis
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("e2e-live")

# ── Configuration ──
SANDBOX_OWNER = "tedahn"
SANDBOX_REPO = "ditto-factory-test-sandbox"
K8S_NAMESPACE = "e2e-ditto-test"
REDIS_HOST_URL = "redis://localhost:16379"
REDIS_CLUSTER_URL = "redis://redis.e2e-ditto-test.svc.cluster.local:6379"


class MockIntegration:
    """Captures report_result calls instead of posting to Slack/GitHub."""
    def __init__(self):
        self.reported_results = []

    async def report_result(self, thread, result):
        self.reported_results.append((thread, result))
        logger.info("📋 Result reported — PR: %s, commits: %d, exit_code: %d",
                     result.pr_url or "none", result.commit_count, result.exit_code)


class RealGitHubClient:
    """Minimal GitHub client that creates real PRs using a token."""
    def __init__(self, token: str):
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30,
        )

    async def create_pr(self, owner, repo, branch, title, body, base="main"):
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        response = await self._client.post(url, json={
            "title": title,
            "body": body,
            "head": branch,
            "base": base,
        })
        response.raise_for_status()
        pr_url = response.json().get("html_url", "")
        logger.info("✅ PR created: %s", pr_url)
        return pr_url

    async def close(self):
        await self._client.aclose()


async def run():
    # ── 1. Resolve tokens ──
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        try:
            gh_token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
        except Exception:
            logger.error("No GH_TOKEN env var and 'gh auth token' failed. Set GH_TOKEN first.")
            sys.exit(1)

    logger.info("🔑 GitHub token resolved (%s...)", gh_token[:8])

    # ── 2. Connect to Redis (host-side via NodePort) ──
    redis = Redis.from_url(REDIS_HOST_URL, decode_responses=True)
    try:
        await redis.ping()
        logger.info("🟢 Redis connected at %s", REDIS_HOST_URL)
    except Exception as e:
        logger.error("❌ Cannot connect to Redis at %s: %s", REDIS_HOST_URL, e)
        logger.error("   Run scripts/e2e-setup.sh first to create the kind cluster.")
        sys.exit(1)

    redis_state = RedisState(redis)

    # ── 3. Set up K8s client ──
    k8s_config.load_kube_config(context="kind-ditto-e2e")
    batch_api = k8s.BatchV1Api()
    core_api = k8s.CoreV1Api()

    # ── 4. Set up state backend (SQLite file) ──
    import tempfile
    db_file = os.path.join(tempfile.gettempdir(), f"ditto-e2e-{uuid.uuid4().hex[:8]}.db")
    state = await SQLiteBackend.create(db_file)
    logger.info("📂 SQLite DB at %s", db_file)

    # ── 5. Build settings ──
    settings = Settings(
        agent_image="mock-agent:latest",
        image_pull_policy="Never",
        redis_url=REDIS_CLUSTER_URL,  # This is what pods see
        max_job_duration_seconds=120,
        auto_open_pr=True,
        retry_on_empty_result=False,
        anthropic_api_key="dummy-for-mock",
        agent_cpu_request="100m",
        agent_memory_request="128Mi",
        agent_cpu_limit="500m",
        agent_memory_limit="256Mi",
    )

    # ── 6. Create components ──
    spawner = JobSpawner(settings, batch_api, namespace=K8S_NAMESPACE)
    monitor = JobMonitor(redis_state, batch_api, namespace=K8S_NAMESPACE)
    mock_integration = MockIntegration()
    github_client = RealGitHubClient(gh_token)

    # ── 7. Simulate the pipeline ──
    thread_id = uuid.uuid4().hex
    branch = f"df/{thread_id[:8]}/{uuid.uuid4().hex[:8]}"

    logger.info("🚀 Starting E2E run")
    logger.info("   Thread ID: %s", thread_id)
    logger.info("   Branch: %s", branch)
    logger.info("   Target: %s/%s", SANDBOX_OWNER, SANDBOX_REPO)

    # Create thread
    from datetime import datetime, timezone
    thread = Thread(
        id=thread_id,
        source="github",
        source_ref=f"{SANDBOX_OWNER}/{SANDBOX_REPO}#e2e",
        repo_owner=SANDBOX_OWNER,
        repo_name=SANDBOX_REPO,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await state.upsert_thread(thread)
    await state.update_thread_status(thread_id, ThreadStatus.RUNNING)

    # Push task context to Redis (what the orchestrator normally does)
    task_context = {
        "task": "E2E test: add a test file to verify the pipeline works",
        "system_prompt": "You are a test agent. Create a file and commit it.",
        "repo_url": f"https://github.com/{SANDBOX_OWNER}/{SANDBOX_REPO}.git",
        "branch": branch,
    }
    await redis_state.push_task(thread_id, task_context)
    logger.info("📝 Task pushed to Redis")

    # Spawn K8s Job (passes real GH token so mock agent can push)
    job_name = spawner.spawn(
        thread_id=thread_id,
        github_token=gh_token,
        redis_url=REDIS_CLUSTER_URL,
    )
    logger.info("🏗️  K8s Job spawned: %s", job_name)

    # ── 8. Monitor for result ──
    logger.info("⏳ Waiting for agent result (timeout: 120s)...")
    result = await monitor.wait_for_result(thread_id, timeout=120, poll_interval=3.0)

    if result is None:
        # Try to get pod logs for debugging
        logger.error("❌ Timeout — no result from agent")
        try:
            pods = core_api.list_namespaced_pod(
                namespace=K8S_NAMESPACE,
                label_selector=f"df/thread={thread_id[:8]}"
            )
            for pod in pods.items:
                logger.info("Pod %s status: %s", pod.metadata.name, pod.status.phase)
                try:
                    logs = core_api.read_namespaced_pod_log(
                        name=pod.metadata.name,
                        namespace=K8S_NAMESPACE,
                        tail_lines=30,
                    )
                    logger.info("Pod logs:\n%s", logs)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Could not fetch pod info: %s", e)
        await github_client.close()
        await redis.aclose()
        sys.exit(1)

    logger.info("📦 Agent result received:")
    logger.info("   Branch: %s", result.branch)
    logger.info("   Exit code: %d", result.exit_code)
    logger.info("   Commits: %d", result.commit_count)

    # ── 9. Run safety pipeline ──
    pipeline = SafetyPipeline(
        settings=settings,
        state_backend=state,
        redis_state=redis_state,
        integration=mock_integration,
        spawner=None,  # No retry spawning in this test
        github_client=github_client,
    )

    logger.info("🔒 Running safety pipeline...")
    await pipeline.process(thread, result)

    # ── 10. Verify outcomes ──
    success = True

    if result.commit_count > 0 and result.pr_url:
        logger.info("✅ PR created: %s", result.pr_url)
    elif result.commit_count > 0:
        logger.warning("⚠️  Commits made but no PR URL — check safety pipeline logs")
        success = False
    else:
        logger.warning("⚠️  No commits made")
        success = False

    if mock_integration.reported_results:
        logger.info("✅ Result reported to integration")
    else:
        logger.warning("⚠️  No result reported to integration")
        success = False

    # Check thread status
    updated_thread = await state.get_thread(thread_id)
    if updated_thread and updated_thread.status == ThreadStatus.IDLE:
        logger.info("✅ Thread reset to IDLE")
    else:
        logger.warning("⚠️  Thread not reset to IDLE")
        success = False

    # ── Cleanup ──
    await github_client.close()
    await redis.aclose()

    if success:
        logger.info("")
        logger.info("🎉 E2E test PASSED — full pipeline verified end-to-end!")
        logger.info("   Check the PR at: %s", result.pr_url)
    else:
        logger.error("")
        logger.error("💥 E2E test had issues — check warnings above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
