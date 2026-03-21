"""Contract 3: Orchestrator -> State Backend.

Abstract contract test suite run against every StateBackend implementation.
Verifies CRUD operations on Thread and Job via the StateBackend protocol.
"""
import pytest
from datetime import datetime, timezone
from controller.models import Thread, Job, ThreadStatus, JobStatus


class StateBackendContractSuite:
    """
    Abstract contract test suite. Run against EVERY StateBackend implementation.
    Subclass and provide a `backend` fixture.
    """

    async def test_get_nonexistent_thread_returns_none(self, backend):
        result = await backend.get_thread("does-not-exist")
        assert result is None

    async def test_upsert_then_get_thread(self, backend):
        thread = Thread(
            id="t1", source="github", source_ref={"number": 1},
            repo_owner="org", repo_name="repo",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await backend.upsert_thread(thread)
        retrieved = await backend.get_thread("t1")
        assert retrieved is not None
        assert retrieved.id == "t1"
        assert retrieved.source == "github"
        assert retrieved.repo_owner == "org"

    async def test_upsert_is_idempotent(self, backend):
        thread = Thread(id="t2", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.upsert_thread(thread)  # Should not raise
        retrieved = await backend.get_thread("t2")
        assert retrieved is not None

    async def test_update_thread_status(self, backend):
        thread = Thread(id="t3", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.update_thread_status("t3", ThreadStatus.RUNNING, job_name="df-job-1")
        retrieved = await backend.get_thread("t3")
        assert retrieved.status == ThreadStatus.RUNNING
        assert retrieved.current_job_name == "df-job-1"

    async def test_create_job_and_get_active(self, backend):
        thread = Thread(id="t4", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j1", thread_id="t4", k8s_job_name="df-test-1", status=JobStatus.RUNNING)
        await backend.create_job(job)
        active = await backend.get_active_job_for_thread("t4")
        assert active is not None
        assert active.id == "j1"

    async def test_no_active_job_for_completed(self, backend):
        thread = Thread(id="t5", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j2", thread_id="t5", k8s_job_name="df-test-2", status=JobStatus.COMPLETED)
        await backend.create_job(job)
        active = await backend.get_active_job_for_thread("t5")
        assert active is None

    async def test_get_job(self, backend):
        """Contract: get_job returns Job by ID or None."""
        thread = Thread(id="t5a", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j3", thread_id="t5a", k8s_job_name="df-test-3", status=JobStatus.RUNNING)
        await backend.create_job(job)
        retrieved = await backend.get_job("j3")
        assert retrieved is not None
        assert retrieved.id == "j3"
        assert await backend.get_job("nonexistent") is None

    async def test_update_job_status(self, backend):
        """Contract: update_job_status transitions job status and stores result."""
        thread = Thread(id="t5b", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j4", thread_id="t5b", k8s_job_name="df-test-4", status=JobStatus.RUNNING)
        await backend.create_job(job)
        await backend.update_job_status("j4", JobStatus.COMPLETED, result={"exit_code": 0})
        updated = await backend.get_job("j4")
        assert updated.status == JobStatus.COMPLETED
        # Job should no longer appear as active
        assert await backend.get_active_job_for_thread("t5b") is None

    async def test_lock_acquire_release(self, backend):
        assert await backend.try_acquire_lock("t6") is True
        assert await backend.try_acquire_lock("t6") is False  # Already locked
        await backend.release_lock("t6")
        assert await backend.try_acquire_lock("t6") is True  # Re-acquirable

    async def test_release_unlocked_is_noop(self, backend):
        await backend.release_lock("never-locked")  # Should not raise

    async def test_conversation_append_and_retrieve(self, backend):
        thread = Thread(id="t7", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.append_conversation("t7", {"role": "user", "content": "hello"})
        await backend.append_conversation("t7", {"role": "assistant", "content": "hi"})
        convo = await backend.get_conversation("t7", limit=50)
        assert len(convo) == 2
        assert convo[0]["role"] == "user"
        assert convo[1]["role"] == "assistant"

    async def test_conversation_limit(self, backend):
        thread = Thread(id="t8", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        for i in range(10):
            await backend.append_conversation("t8", {"role": "user", "content": f"msg-{i}"})
        convo = await backend.get_conversation("t8", limit=3)
        assert len(convo) == 3


class TestSQLiteContract(StateBackendContractSuite):
    @pytest.fixture
    async def backend(self, tmp_path):
        from controller.state.sqlite import SQLiteBackend

        return await SQLiteBackend.create(f"sqlite:///{tmp_path / 'test.db'}")
