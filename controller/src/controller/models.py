from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ThreadStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    QUEUED = "queued"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskRequest:
    thread_id: str
    source: str  # "slack" | "linear" | "github"
    source_ref: dict
    repo_owner: str
    repo_name: str
    task: str
    conversation: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    branch: str
    exit_code: int
    commit_count: int
    stderr: str = ""
    pr_url: str | None = None


@dataclass
class Thread:
    id: str
    source: str
    source_ref: dict
    repo_owner: str
    repo_name: str
    status: ThreadStatus = ThreadStatus.IDLE
    current_job_name: str | None = None
    conversation_history: list[dict] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Job:
    id: str
    thread_id: str
    k8s_job_name: str
    status: JobStatus = JobStatus.PENDING
    task_context: dict = field(default_factory=dict)
    result: dict | None = None
    agent_type: str = "general"
    skills_injected: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
