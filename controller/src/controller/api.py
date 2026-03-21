"""
REST API endpoints for Ditto Factory.

  POST /api/tasks          — submit a new coding task
  GET  /api/tasks/{id}     — get task status and result
  GET  /api/threads        — list all threads
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from controller.config import Settings
from controller.models import TaskRequest, Thread, ThreadStatus, JobStatus


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class TaskSubmitRequest(BaseModel):
    repo_owner: str
    repo_name: str
    task: str
    source: str = "api"
    conversation: list[str] = []
    images: list[str] = []


class TaskSubmitResponse(BaseModel):
    thread_id: str
    status: str  # "submitted"


class TaskStatusResponse(BaseModel):
    thread_id: str
    status: str  # "pending" | "running" | "completed" | "failed" | "idle"
    result: dict[str, Any] | None = None


class ThreadListItem(BaseModel):
    id: str
    source: str
    repo_owner: str
    repo_name: str
    status: str


# ---------------------------------------------------------------------------
# Dependency injection helpers (overridden in tests)
# ---------------------------------------------------------------------------

def get_db():
    """Provide the state backend — overridden in tests."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_orchestrator():
    """Provide the orchestrator — overridden in tests."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_settings() -> Settings:
    """Provide settings — overridden in tests."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def verify_api_key(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
) -> None:
    """Bearer token auth — skipped when Settings.api_key is empty (open mode)."""
    api_key = getattr(settings, "api_key", "")
    if not api_key:
        return  # open mode

    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


@router.post("/tasks", response_model=TaskSubmitResponse)
async def submit_task(
    body: TaskSubmitRequest,
    db=Depends(get_db),
    orchestrator=Depends(get_orchestrator),
):
    thread_id = uuid.uuid4().hex

    task_request = TaskRequest(
        thread_id=thread_id,
        source=body.source,
        source_ref={"origin": "rest_api"},
        repo_owner=body.repo_owner,
        repo_name=body.repo_name,
        task=body.task,
        conversation=body.conversation,
        images=body.images,
    )

    await orchestrator.handle_task(task_request)

    return TaskSubmitResponse(thread_id=thread_id, status="submitted")


@router.get("/tasks/{thread_id}", response_model=TaskStatusResponse)
async def get_task(thread_id: str, db=Depends(get_db)):
    thread = await db.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Determine effective status from the latest job
    job = await db.get_active_job_for_thread(thread_id)

    if job is None:
        # No active job — check for most recent completed/failed job
        job = await db.get_latest_job_for_thread(thread_id)

    if job is not None:
        status = job.status.value
        result = job.result
    else:
        status = thread.status.value
        result = None

    return TaskStatusResponse(
        thread_id=thread_id,
        status=status,
        result=result,
    )


@router.get("/threads", response_model=list[ThreadListItem])
async def list_threads(db=Depends(get_db)):
    threads = await db.list_threads()
    return [
        ThreadListItem(
            id=t.id,
            source=t.source,
            repo_owner=t.repo_owner,
            repo_name=t.repo_name,
            status=t.status.value,
        )
        for t in threads
    ]
