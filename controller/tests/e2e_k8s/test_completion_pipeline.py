"""
P1.5: Full completion pipeline -- job result through safety pipeline to integration.

Tests the handle_job_completion flow:
  1. monitor.wait_for_result picks up result from Redis
  2. SafetyPipeline.process runs (PR check, validation, report)
  3. integration.report_result is called with correct Thread and AgentResult
  4. Thread status transitions back to IDLE
  5. Queued messages are drained
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from controller.models import TaskRequest, ThreadStatus, AgentResult
from controller.orchestrator import Orchestrator
from .helpers import (
    wait_for_redis_key,
    wait_for_job_completion,
    get_job_logs,
    get_jobs_for_thread,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(180)]


class TestCompletionPipeline:
    """
    Verifies the full handle_job_completion flow end-to-end.
    """

    async def test_completion_pipeline_with_commits(
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
        """Result with commits flows through safety pipeline and reports to integration."""
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
            source_ref={"issue_number": 42},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Add a feature file",
        )

        await orch.handle_task(task)

        # Wait for mock agent to finish
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1
        job_name = jobs[0].metadata.name

        await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )

        # Verify result in Redis before completion
        result_data = await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=30
        )
        assert result_data["exit_code"] == 0
        assert result_data["commit_count"] == 1

        # Now trigger the completion pipeline
        await orch.handle_job_completion(unique_thread_id)

        # Verify: thread status is IDLE
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.IDLE

        # Verify: integration.report_result called with correct data
        mock_integration.report_result.assert_called_once()
        call_args = mock_integration.report_result.call_args
        reported_thread = call_args[0][0]
        reported_result = call_args[0][1]
        assert reported_thread.id == unique_thread_id
        assert reported_result.exit_code == 0
        assert reported_result.commit_count == 1
        assert isinstance(reported_result.branch, str)
        assert len(reported_result.branch) > 0

        # Verify: queued messages are drained
        remaining = await redis_state.drain_messages(unique_thread_id)
        assert len(remaining) == 0

        cleanup_k8s_jobs(unique_thread_id)

    async def test_completion_drains_queued_messages(
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
        """Messages queued while agent is running are drained after completion."""
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
            task="Initial task",
        )

        await orch.handle_task(task)

        # Queue follow-up messages while agent is running
        await redis_state.queue_message(unique_thread_id, "also fix tests")
        await redis_state.queue_message(unique_thread_id, "and update docs")

        # Wait for agent completion
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1
        job_name = jobs[0].metadata.name

        await wait_for_job_completion(
            k8s_clients["batch"], namespace, job_name, timeout_seconds=90
        )
        await wait_for_redis_key(
            redis_state, "result", unique_thread_id, timeout_seconds=30
        )

        # Trigger completion -- should drain queued messages
        await orch.handle_job_completion(unique_thread_id)

        # Queue should be empty after completion
        remaining = await redis_state.drain_messages(unique_thread_id)
        assert len(remaining) == 0

        cleanup_k8s_jobs(unique_thread_id)

    async def test_completion_missing_thread_returns_early(
        self,
        settings,
        db,
        redis_state,
        spawner,
        monitor,
        registry,
    ):
        """handle_job_completion with unknown thread_id returns without error."""
        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        # Should not raise
        await orch.handle_job_completion("nonexistent-thread-id")

    async def test_completion_no_result_returns_early(
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
        """If monitor times out (no result in Redis), completion returns early."""
        settings.auto_open_pr = False

        # Create a thread manually without spawning a real job
        from controller.models import Thread
        from datetime import datetime, timezone

        thread = Thread(
            id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 1},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            status=ThreadStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await db.upsert_thread(thread)

        # Use a monitor with very short timeout so we don't wait long
        from controller.jobs.monitor import JobMonitor

        fast_monitor = JobMonitor(
            redis_state=redis_state,
            batch_api=k8s_clients["batch"],
            namespace=namespace,
        )

        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=fast_monitor,
        )

        # No result in Redis -- monitor.wait_for_result will timeout
        # The orchestrator calls wait_for_result(thread_id, timeout=60, poll_interval=1.0)
        # For this test, there's no result, so it should return None and the orchestrator
        # should return early without calling report_result.
        await orch.handle_job_completion(unique_thread_id)

        # report_result should NOT have been called
        mock_integration.report_result.assert_not_called()

        # Thread should still be in RUNNING (early return means no status change)
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.RUNNING
