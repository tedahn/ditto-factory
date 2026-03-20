import pytest
from controller.state.sqlite import SQLiteBackend
from controller.models import Thread, Job, ThreadStatus, JobStatus


@pytest.fixture
async def backend(tmp_path):
    db_path = str(tmp_path / "test.db")
    b = await SQLiteBackend.create(f"sqlite:///{db_path}")
    yield b


async def test_thread_roundtrip(backend):
    t = Thread(id="t1", source="slack", source_ref={"ch": "C1"}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    got = await backend.get_thread("t1")
    assert got is not None
    assert got.source == "slack"


async def test_job_lifecycle(backend):
    t = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    j = Job(id="j1", thread_id="t1", k8s_job_name="agent-t1-123")
    await backend.create_job(j)
    active = await backend.get_active_job_for_thread("t1")
    assert active is not None
    await backend.update_job_status("j1", JobStatus.COMPLETED)
    assert await backend.get_active_job_for_thread("t1") is None


async def test_conversation_limit(backend):
    t = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    for i in range(100):
        await backend.append_conversation("t1", {"content": f"msg {i}"})
    history = await backend.get_conversation("t1", limit=50)
    assert len(history) == 50


async def test_advisory_lock(backend):
    assert await backend.try_acquire_lock("t1") is True
    assert await backend.try_acquire_lock("t1") is False
    await backend.release_lock("t1")
    assert await backend.try_acquire_lock("t1") is True
