"""
Tests for the REST API endpoints:
  POST /api/tasks        — submit a task
  GET  /api/tasks/{id}   — get task status/result
  GET  /api/threads      — list threads

Uses SQLiteBackend for state, mocks the orchestrator to avoid K8s deps.
"""
from __future__ import annotations

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from controller.config import Settings
from controller.models import Thread, Job, ThreadStatus, JobStatus

try:
    from controller.state.sqlite import SQLiteBackend

    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

pytestmark = [
    pytest.mark.skipif(not HAS_SQLITE, reason="aiosqlite not installed"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_settings():
    """Minimal settings for API tests (no integrations needed)."""
    return Settings(
        anthropic_api_key="sk-test-api",
        slack_enabled=False,
        linear_enabled=False,
        github_enabled=False,
    )


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / f"api_{uuid.uuid4().hex[:8]}.db")
    backend = await SQLiteBackend.create(f"sqlite:///{path}")
    return backend


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.handle_task = AsyncMock()
    return orch


@pytest.fixture
def app(api_settings, db, mock_orchestrator):
    """Create a fresh FastAPI app with API routes, wired to real DB + mock orchestrator."""
    from fastapi import FastAPI
    from controller.api import router as api_router, get_db, get_orchestrator, get_settings

    test_app = FastAPI()
    test_app.include_router(api_router)

    # Override dependency injection
    test_app.dependency_overrides[get_db] = lambda: db
    test_app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    test_app.dependency_overrides[get_settings] = lambda: api_settings

    return test_app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: seed data in the state backend
# ---------------------------------------------------------------------------

async def _seed_thread(
    db,
    thread_id: str,
    *,
    status: ThreadStatus = ThreadStatus.IDLE,
    source: str = "api",
) -> Thread:
    thread = Thread(
        id=thread_id,
        source=source,
        source_ref={},
        repo_owner="testorg",
        repo_name="testrepo",
        status=status,
    )
    await db.upsert_thread(thread)
    return thread


async def _seed_job(
    db,
    thread_id: str,
    *,
    status: JobStatus = JobStatus.PENDING,
    result: dict | None = None,
) -> Job:
    job = Job(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        k8s_job_name=f"df-{thread_id[:8]}",
        status=status,
        task_context={"task": "test task"},
        result=result,
    )
    await db.create_job(job)
    return job


# ===========================================================================
# Tests
# ===========================================================================


class TestSubmitTask:
    """POST /api/tasks"""

    def test_submit_task(self, client, mock_orchestrator):
        """Valid body returns 200 with thread_id and status='submitted'."""
        body = {
            "repo_owner": "testorg",
            "repo_name": "testrepo",
            "task": "fix the login bug",
        }
        response = client.post("/api/tasks", json=body)

        assert response.status_code == 200
        data = response.json()
        assert "thread_id" in data
        assert data["status"] == "submitted"
        # Orchestrator should have been called
        mock_orchestrator.handle_task.assert_called_once()

    def test_submit_task_missing_fields(self, client):
        """Missing required fields returns 422 validation error."""
        # Missing 'task' field
        body = {
            "repo_owner": "testorg",
            "repo_name": "testrepo",
        }
        response = client.post("/api/tasks", json=body)
        assert response.status_code == 422

    def test_submit_task_empty_body(self, client):
        """Empty body returns 422."""
        response = client.post("/api/tasks", json={})
        assert response.status_code == 422


class TestGetTask:
    """GET /api/tasks/{thread_id}"""

    def test_get_task_not_found(self, client):
        """Non-existent thread_id returns 404."""
        random_id = uuid.uuid4().hex
        response = client.get(f"/api/tasks/{random_id}")
        assert response.status_code == 404
        assert "not found" in response.json().get("detail", "").lower()

    async def test_get_task_running(self, client, db):
        """Thread with RUNNING status returns status='running'."""
        thread_id = f"running-{uuid.uuid4().hex[:8]}"
        await _seed_thread(db, thread_id, status=ThreadStatus.RUNNING)
        await _seed_job(db, thread_id, status=JobStatus.RUNNING)

        response = client.get(f"/api/tasks/{thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == thread_id
        assert data["status"] == "running"

    async def test_get_task_completed(self, client, db):
        """Completed job returns status='completed' with result fields."""
        thread_id = f"done-{uuid.uuid4().hex[:8]}"
        result_data = {
            "branch": "df/fix/abc123",
            "exit_code": 0,
            "commit_count": 3,
            "pr_url": "https://github.com/testorg/testrepo/pull/1",
        }
        await _seed_thread(db, thread_id, status=ThreadStatus.IDLE)
        job = await _seed_job(
            db, thread_id, status=JobStatus.COMPLETED, result=result_data
        )
        # Mark job as completed with result in DB
        await db.update_job_status(job.id, JobStatus.COMPLETED, result=result_data)

        response = client.get(f"/api/tasks/{thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["thread_id"] == thread_id
        assert data["status"] == "completed"
        assert data["result"] is not None
        assert data["result"]["branch"] == "df/fix/abc123"
        assert data["result"]["pr_url"] == "https://github.com/testorg/testrepo/pull/1"

    async def test_get_task_failed(self, client, db):
        """Failed job returns status='failed'."""
        thread_id = f"fail-{uuid.uuid4().hex[:8]}"
        await _seed_thread(db, thread_id, status=ThreadStatus.IDLE)
        await _seed_job(db, thread_id, status=JobStatus.FAILED)

        response = client.get(f"/api/tasks/{thread_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"


class TestListThreads:
    """GET /api/threads"""

    def test_list_threads_empty(self, client):
        """No threads returns empty list."""
        response = client.get("/api/threads")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_list_threads(self, client, db):
        """Created threads are returned in the list."""
        ids = [f"list-{i}-{uuid.uuid4().hex[:6]}" for i in range(3)]
        for tid in ids:
            await _seed_thread(db, tid)

        response = client.get("/api/threads")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 3
        returned_ids = {t["id"] for t in data}
        for tid in ids:
            assert tid in returned_ids


class TestAuth:
    """Authentication via DF_API_KEY / Bearer token."""

    def test_auth_required_no_header(self):
        """When DF_API_KEY is set, requests without auth header get 401."""
        settings_with_key = Settings(
            anthropic_api_key="sk-test",
            api_key="secret-api-key-123",
            slack_enabled=False,
            linear_enabled=False,
            github_enabled=False,
        )
        from fastapi import FastAPI
        from controller.api import router as api_router, get_db, get_orchestrator, get_settings

        test_app = FastAPI()
        test_app.include_router(api_router)

        # Wire up with keyed settings
        mock_db = AsyncMock()
        mock_db.get_thread = AsyncMock(return_value=None)
        mock_orch = AsyncMock()

        test_app.dependency_overrides[get_db] = lambda: mock_db
        test_app.dependency_overrides[get_orchestrator] = lambda: mock_orch
        test_app.dependency_overrides[get_settings] = lambda: settings_with_key

        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get("/api/threads")
        assert response.status_code == 401

    def test_auth_valid_bearer(self):
        """When DF_API_KEY is set, correct Bearer token passes auth."""
        settings_with_key = Settings(
            anthropic_api_key="sk-test",
            api_key="secret-api-key-123",
            slack_enabled=False,
            linear_enabled=False,
            github_enabled=False,
        )
        from fastapi import FastAPI
        from controller.api import router as api_router, get_db, get_orchestrator, get_settings

        test_app = FastAPI()
        test_app.include_router(api_router)

        mock_db = AsyncMock()
        # list_threads returns empty list
        mock_db.list_threads = AsyncMock(return_value=[])
        mock_orch = AsyncMock()

        test_app.dependency_overrides[get_db] = lambda: mock_db
        test_app.dependency_overrides[get_orchestrator] = lambda: mock_orch
        test_app.dependency_overrides[get_settings] = lambda: settings_with_key

        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/api/threads",
            headers={"Authorization": "Bearer secret-api-key-123"},
        )
        assert response.status_code == 200

    def test_auth_invalid_bearer(self):
        """When DF_API_KEY is set, wrong Bearer token gets 401."""
        settings_with_key = Settings(
            anthropic_api_key="sk-test",
            api_key="secret-api-key-123",
            slack_enabled=False,
            linear_enabled=False,
            github_enabled=False,
        )
        from fastapi import FastAPI
        from controller.api import router as api_router, get_db, get_orchestrator, get_settings

        test_app = FastAPI()
        test_app.include_router(api_router)

        mock_db = AsyncMock()
        mock_orch = AsyncMock()

        test_app.dependency_overrides[get_db] = lambda: mock_db
        test_app.dependency_overrides[get_orchestrator] = lambda: mock_orch
        test_app.dependency_overrides[get_settings] = lambda: settings_with_key

        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/api/threads",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_auth_skip_when_no_key(self, client):
        """When DF_API_KEY is not set, requests without auth header pass (open mode)."""
        # The default fixture has no api_key set
        response = client.get("/api/threads")
        assert response.status_code == 200
