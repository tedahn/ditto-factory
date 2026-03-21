"""
P1: Full pipeline happy path -- webhook to result.

Tests the complete flow:
  TaskRequest -> orchestrator.handle_task()
  -> thread created in DB
  -> task pushed to Redis
  -> K8s Job spawned (mock agent)
  -> mock agent reads task, makes commits, writes result to Redis
  -> monitor picks up result
  -> handle_job_completion -> SafetyPipeline -> report_result
  -> thread status = IDLE
"""
from __future__ import annotations

import pytest
from controller.models import TaskRequest, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from .helpers import (
    wait_for_redis_key,
    wait_for_job_completion,
    get_job_logs,
    get_jobs_for_thread,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(180)]


class TestGitHubHappyPath:
    """
    Full pipeline: TaskRequest -> spawn -> mock agent -> result -> completion.
    """

    async def test_full_pipeline_github_issue(
        self,
        settings,
        db,
        redis_state,
        spawner,
        monitor,
        registry,
        mock_integration,
        k8s_clients,
        namespace,
        unique_thread_id,
        cleanup_k8s_jobs,
    ):
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

        # Trigger the pipeline (this spawns the K8s Job synchronously)
        await orch.handle_task(task)

        # Verify thread created and in RUNNING state
        thread = await db.get_thread(unique_thread_id)
        assert thread is not None, "Thread should be created in state backend"
        assert thread.status == ThreadStatus.RUNNING
        assert thread.source == "test"
        assert thread.repo_owner == "ditto-factory"

        # Verify task was pushed to Redis
        task_data = await redis_state.get_task(unique_thread_id)
        assert task_data is not None, "Task should be in Redis"
        assert task_data["task"] == "Add a hello-world.txt file"
        assert "branch" in task_data
        assert task_data["repo_url"] == "https://github.com/ditto-factory/e2e-test-target.git"

        # Verify K8s Job was created
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1, f"Expected exactly 1 K8s Job, got {len(jobs)}"
        job_name = jobs[0].metadata.name

        # Wait for mock agent to complete
        completed_job = await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )
        assert completed_job.status.succeeded == 1, (
            f"Job should succeed. Logs:\n{get_job_logs(k8s_clients['core'], namespace, job_name)}"
        )

        # Verify result was written to Redis by mock agent
        result = await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=30
        )
        assert result is not None
        assert result["exit_code"] == 0
        assert result["commit_count"] == 1

        # Trigger completion pipeline
        await orch.handle_job_completion(unique_thread_id)

        # Verify thread status is back to IDLE
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.IDLE

        # Verify integration.report_result was called
        mock_integration.report_result.assert_called_once()
        call_args = mock_integration.report_result.call_args
        reported_thread = call_args[0][0]
        reported_result = call_args[0][1]
        assert reported_thread.id == unique_thread_id
        assert reported_result.exit_code == 0
        assert reported_result.commit_count == 1

        # Cleanup
        cleanup_k8s_jobs(unique_thread_id)

    async def test_full_pipeline_slack_source(
        self,
        settings,
        db,
        redis_state,
        spawner,
        monitor,
        registry,
        mock_integration,
        k8s_clients,
        namespace,
        unique_thread_id,
        cleanup_k8s_jobs,
    ):
        """Same flow but with slack-like source metadata."""
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
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1
        job_name = jobs[0].metadata.name

        await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )

        result = await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=30
        )
        assert result["exit_code"] == 0
        assert result["commit_count"] == 1

        # Trigger completion
        await orch.handle_job_completion(unique_thread_id)

        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.IDLE
        mock_integration.report_result.assert_called_once()

        cleanup_k8s_jobs(unique_thread_id)
