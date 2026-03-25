from __future__ import annotations

import uuid
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


class TaskType(str, Enum):
    CODE_CHANGE = "code_change"
    ANALYSIS = "analysis"
    DB_MUTATION = "db_mutation"
    FILE_OUTPUT = "file_output"
    API_ACTION = "api_action"


class ResultType(str, Enum):
    PULL_REQUEST = "pull_request"
    REPORT = "report"
    DB_ROWS = "db_rows"
    FILE_ARTIFACT = "file_artifact"
    API_RESPONSE = "api_response"


class ReversibilityLevel(str, Enum):
    TRIVIAL = "trivial"
    POSSIBLE = "possible"
    DIFFICULT = "difficult"
    IMPOSSIBLE = "impossible"


@dataclass
class Artifact:
    result_type: ResultType
    location: str
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


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
    skill_overrides: list[str] | None = None  # explicit skill slugs to bypass classifier
    agent_type_override: str | None = None    # explicit agent type to bypass resolver
    task_type: TaskType = TaskType.CODE_CHANGE


@dataclass
class AgentResult:
    branch: str
    exit_code: int
    commit_count: int
    stderr: str = ""
    pr_url: str | None = None
    trace_events: list[dict] = field(default_factory=list)
    result_type: ResultType = ResultType.PULL_REQUEST
    artifacts: list[Artifact] = field(default_factory=list)


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
