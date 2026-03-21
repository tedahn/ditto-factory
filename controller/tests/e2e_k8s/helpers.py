"""Polling helpers for E2E tests."""
from __future__ import annotations

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
    """Poll Redis until a key appears. key_type is 'task' or 'result'."""
    elapsed = 0.0
    getter = getattr(redis_state, f"get_{key_type}")
    while elapsed < timeout_seconds:
        value = await getter(thread_id)
        if value is not None:
            return value
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(
        f"Redis key {key_type}:{thread_id} did not appear within {timeout_seconds}s"
    )


def get_job_logs(
    core_api: k8s.CoreV1Api,
    namespace: str,
    job_name: str,
) -> str:
    """Retrieve logs from the first pod of a K8s Job."""
    try:
        pods = core_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
        )
        if not pods.items:
            return f"<no pods found for job {job_name}>"
        pod_name = pods.items[0].metadata.name
        return core_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
        )
    except Exception as e:
        return f"<error fetching logs for job {job_name}: {e}>"


def get_jobs_for_thread(
    batch_api: k8s.BatchV1Api,
    namespace: str,
    thread_id: str,
) -> list[k8s.V1Job]:
    """List K8s Jobs matching a thread ID label."""
    short_id = thread_id[:8]
    # Sanitize the same way JobSpawner does
    sanitized = "".join(c if c.isalnum() or c in "-_." else "" for c in short_id)
    sanitized = sanitized.strip("-_.") or "unknown"

    jobs = batch_api.list_namespaced_job(
        namespace=namespace,
        label_selector=f"df/thread={sanitized}",
    )
    return jobs.items


async def cleanup_thread_redis_keys(redis_state, thread_id: str) -> None:
    """Remove all Redis keys for a thread (for test isolation)."""
    redis = redis_state._redis
    for prefix in ("task", "result", "queue"):
        await redis.delete(f"{prefix}:{thread_id}")
    # Agent streams use DELETE (works on streams too)
    await redis.delete(f"agent:{thread_id}")
