"""
P3: Concurrency -- duplicate webhooks, queued messages.
"""
from __future__ import annotations

import asyncio
import pytest
from controller.models import TaskRequest, ThreadStatus
from controller.orchestrator import Orchestrator
from .helpers import (
    wait_for_redis_key,
    wait_for_job_completion,
    get_jobs_for_thread,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(180)]


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
        self,
        settings,
        db,
        redis_state,
        spawner,
        monitor,
        registry,
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
            source_ref={"issue_number": 10},
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

        # Neither should raise
        for r in results:
            assert not isinstance(r, Exception), f"handle_task raised: {r}"

        # Only ONE K8s Job should exist for this thread
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1, (
            f"Expected exactly 1 K8s Job for duplicate webhooks, got {len(jobs)}"
        )

        cleanup_k8s_jobs(unique_thread_id)


class TestQueuedMessages:
    """Messages arriving while agent is running get queued in Redis."""

    async def test_message_queued_while_agent_running(
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

        # First task spawns a job
        task1 = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 20},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="First task",
        )
        await orch.handle_task(task1)

        # Verify job is running
        thread = await db.get_thread(unique_thread_id)
        assert thread.status == ThreadStatus.RUNNING

        # Second task should be queued (active job exists)
        task2 = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 20},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="Follow-up task",
        )
        await orch.handle_task(task2)

        # Verify the message was queued in Redis
        queued = await redis_state.drain_messages(unique_thread_id)
        assert len(queued) == 1
        assert queued[0] == "Follow-up task"

        # Only one K8s Job should exist
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1

        cleanup_k8s_jobs(unique_thread_id)

    async def test_multiple_messages_queued(
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
        """Multiple follow-up messages are all queued in order."""
        orch = Orchestrator(
            settings=settings,
            state=db,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=monitor,
        )

        # First task spawns a job
        task = TaskRequest(
            thread_id=unique_thread_id,
            source="test",
            source_ref={"issue_number": 30},
            repo_owner="ditto-factory",
            repo_name="e2e-test-target",
            task="First task",
        )
        await orch.handle_task(task)

        # Queue multiple follow-ups
        for i in range(3):
            follow_up = TaskRequest(
                thread_id=unique_thread_id,
                source="test",
                source_ref={"issue_number": 30},
                repo_owner="ditto-factory",
                repo_name="e2e-test-target",
                task=f"Follow-up {i+1}",
            )
            await orch.handle_task(follow_up)

        # All follow-ups should be queued
        queued = await redis_state.drain_messages(unique_thread_id)
        assert len(queued) == 3
        assert queued[0] == "Follow-up 1"
        assert queued[1] == "Follow-up 2"
        assert queued[2] == "Follow-up 3"

        # Still only one K8s Job
        jobs = get_jobs_for_thread(k8s_clients["batch"], namespace, unique_thread_id)
        assert len(jobs) == 1

        cleanup_k8s_jobs(unique_thread_id)
