"""
Full K8s integration test.
Runs INSIDE the controller pod to test: orchestrator → K8s Job spawn → Redis result.
"""
import asyncio
import json
import sys
sys.path.insert(0, "/app/src")

from controller.config import Settings
from controller.models import TaskRequest, ThreadStatus, JobStatus
from controller.state.sqlite import SQLiteBackend
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.orchestrator import Orchestrator
from redis.asyncio import Redis


async def main():
    settings = Settings()
    print(f"[1] Settings loaded: redis={settings.redis_url}, agent_image={settings.agent_image}")

    # Connect to Redis
    redis_client = Redis.from_url(settings.redis_url)
    redis_state = RedisState(redis_client)
    await redis_client.ping()
    print("[2] Redis connected ✓")

    # Initialize SQLite backend
    db = await SQLiteBackend.create("sqlite:////tmp/test_flow.db")
    print("[3] SQLite backend initialized ✓")

    # Initialize K8s client
    from kubernetes import client as k8s, config as k8s_config
    k8s_config.load_incluster_config()
    batch_api = k8s.BatchV1Api()
    print("[4] K8s client initialized (in-cluster) ✓")

    # Create spawner and monitor
    spawner = JobSpawner(settings=settings, batch_api=batch_api, namespace="aal")
    monitor = JobMonitor(redis_state=redis_state, batch_api=batch_api, namespace="aal")
    registry = IntegrationRegistry()

    # Create orchestrator
    orchestrator = Orchestrator(
        settings=settings,
        state=db,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
    )
    print("[5] Orchestrator created ✓")

    # Create a test task
    task = TaskRequest(
        thread_id="k8s-test-001",
        source="test",
        source_ref={"test": True},
        repo_owner="tedahn",
        repo_name="ditto-factory",
        task="echo 'Hello from K8s agent test'",
    )

    # Handle the task (this should spawn a K8s Job)
    print("[6] Submitting task to orchestrator...")
    try:
        await orchestrator.handle_task(task)
        print("[7] Task handled ✓")
    except Exception as e:
        print(f"[7] Task handling error (expected if no github token): {e}")

    # Check thread was created
    thread = await db.get_thread("k8s-test-001")
    if thread:
        print(f"[8] Thread created: id={thread.id}, status={thread.status.value} ✓")
    else:
        print("[8] Thread NOT found ✗")
        return

    # Check if K8s Job was created
    try:
        jobs = batch_api.list_namespaced_job(namespace="aal")
        aal_jobs = [j for j in jobs.items if j.metadata.name.startswith("df-")]
        if aal_jobs:
            job = aal_jobs[0]
            print(f"[9] K8s Job spawned: name={job.metadata.name}, status={job.status} ✓")
        else:
            print("[9] No AAL K8s Jobs found (may have failed to create)")
    except Exception as e:
        print(f"[9] Error listing jobs: {e}")

    # Check Redis has the task
    task_data = await redis_state.get_task("k8s-test-001")
    if task_data:
        print(f"[10] Task in Redis: branch={task_data.get('branch', 'N/A')} ✓")
    else:
        print("[10] Task NOT in Redis (may have expired)")

    # Test message queuing (send a follow-up while job is "running")
    task2 = TaskRequest(
        thread_id="k8s-test-001",
        source="test",
        source_ref={"test": True},
        repo_owner="tedahn",
        repo_name="ditto-factory",
        task="also update the README",
    )
    await orchestrator.handle_task(task2)
    queued = await redis_client.lrange("queue:k8s-test-001", 0, -1)
    if queued:
        print(f"[11] Follow-up message queued ({len(queued)} messages) ✓")
    else:
        print("[11] Follow-up message NOT queued")

    # Cleanup
    print("\n--- Cleaning up K8s Jobs ---")
    for j in aal_jobs if 'aal_jobs' in dir() else []:
        try:
            batch_api.delete_namespaced_job(name=j.metadata.name, namespace="aal", propagation_policy="Foreground")
            print(f"  Deleted job: {j.metadata.name}")
        except Exception:
            pass

    await redis_client.aclose()
    print("\n=== Full flow test complete ===")


if __name__ == "__main__":
    asyncio.run(main())
