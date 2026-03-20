"""Integration tests for PostgresBackend — skip if no Postgres available."""
import os
import pytest
from controller.state.postgres import PostgresBackend
from controller.models import Thread, Job, JobStatus

pytestmark = pytest.mark.skipif(
    not os.environ.get("DF_TEST_DATABASE_URL"),
    reason="Set DF_TEST_DATABASE_URL to run Postgres integration tests"
)


@pytest.fixture
async def backend():
    b = await PostgresBackend.create(os.environ["DF_TEST_DATABASE_URL"])
    yield b


async def test_thread_roundtrip(backend):
    t = Thread(id="test-1", source="slack", source_ref={"ch": "C1"}, repo_owner="org", repo_name="repo")
    await backend.upsert_thread(t)
    got = await backend.get_thread("test-1")
    assert got is not None
    assert got.source == "slack"
