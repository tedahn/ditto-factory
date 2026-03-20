"""Test StateBackend contract using InMemoryBackend."""
from controller.state.protocol import StateBackend
from controller.models import Thread, Job, ThreadStatus, JobStatus


class InMemoryBackend:
    """Minimal in-memory StateBackend for testing the contract."""
    def __init__(self):
        self._threads: dict[str, Thread] = {}
        self._jobs: dict[str, Job] = {}
        self._locks: set[str] = set()

    async def get_thread(self, thread_id):
        return self._threads.get(thread_id)

    async def upsert_thread(self, thread):
        self._threads[thread.id] = thread

    async def update_thread_status(self, thread_id, status, job_name=None):
        t = self._threads[thread_id]
        t.status = status
        if job_name is not None:
            t.current_job_name = job_name

    async def create_job(self, job):
        self._jobs[job.id] = job

    async def get_job(self, job_id):
        return self._jobs.get(job_id)

    async def get_active_job_for_thread(self, thread_id):
        for j in self._jobs.values():
            if j.thread_id == thread_id and j.status in (JobStatus.PENDING, JobStatus.RUNNING):
                return j
        return None

    async def update_job_status(self, job_id, status, result=None):
        j = self._jobs[job_id]
        j.status = status
        if result is not None:
            j.result = result

    async def append_conversation(self, thread_id, message):
        self._threads[thread_id].conversation_history.append(message)

    async def get_conversation(self, thread_id, limit=50):
        return self._threads[thread_id].conversation_history[-limit:]

    async def try_acquire_lock(self, thread_id):
        if thread_id in self._locks:
            return False
        self._locks.add(thread_id)
        return True

    async def release_lock(self, thread_id):
        self._locks.discard(thread_id)


async def test_in_memory_implements_protocol():
    backend = InMemoryBackend()
    assert isinstance(backend, StateBackend)


async def test_thread_crud():
    backend = InMemoryBackend()
    t = Thread(id="t1", source="slack", source_ref={"channel": "C1"}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    got = await backend.get_thread("t1")
    assert got is not None
    assert got.source == "slack"


async def test_job_lifecycle():
    backend = InMemoryBackend()
    t = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    j = Job(id="j1", thread_id="t1", k8s_job_name="agent-t1-123")
    await backend.create_job(j)
    active = await backend.get_active_job_for_thread("t1")
    assert active is not None
    assert active.id == "j1"
    await backend.update_job_status("j1", JobStatus.COMPLETED, result={"branch": "df/t1/123"})
    active = await backend.get_active_job_for_thread("t1")
    assert active is None


async def test_advisory_lock():
    backend = InMemoryBackend()
    assert await backend.try_acquire_lock("t1") is True
    assert await backend.try_acquire_lock("t1") is False
    await backend.release_lock("t1")
    assert await backend.try_acquire_lock("t1") is True


async def test_conversation_limit():
    backend = InMemoryBackend()
    t = Thread(id="t1", source="slack", source_ref={}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    for i in range(100):
        await backend.append_conversation("t1", {"role": "user", "content": f"msg {i}"})
    history = await backend.get_conversation("t1", limit=50)
    assert len(history) == 50
    assert history[0]["content"] == "msg 50"
