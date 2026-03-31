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
    toolkit_slugs: list[str] = field(default_factory=list)    # explicit toolkit selections
    component_slugs: list[str] = field(default_factory=list)   # explicit component selections
    template_slug: str | None = None
    workflow_parameters: dict = field(default_factory=dict)


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
    resolution_diagnostics: dict | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SwarmStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"


@dataclass
class ResourceProfile:
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


ROLE_PROFILES: dict[str, ResourceProfile] = {
    "researcher":  ResourceProfile("100m",  "250m",  "256Mi", "512Mi"),
    "coder":       ResourceProfile("500m",  "1000m", "1Gi",   "2Gi"),
    "aggregator":  ResourceProfile("250m",  "500m",  "512Mi", "1Gi"),
    "planner":     ResourceProfile("100m",  "250m",  "256Mi", "512Mi"),
    "default":     ResourceProfile("250m",  "500m",  "512Mi", "1Gi"),
}


@dataclass
class SwarmAgent:
    id: str
    group_id: str
    role: str
    agent_type: str
    task_assignment: str
    resource_profile: ResourceProfile | None = None
    status: AgentStatus = AgentStatus.PENDING
    k8s_job_name: str | None = None
    result_summary: dict = field(default_factory=dict)


@dataclass
class SwarmGroup:
    id: str
    thread_id: str
    agents: list[SwarmAgent] = field(default_factory=list)
    status: SwarmStatus = SwarmStatus.PENDING
    completion_strategy: str = "all_complete"
    config: dict = field(default_factory=dict)
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class SwarmMessage:
    id: str
    group_id: str
    sender_id: str
    recipient_id: str | None
    message_type: str
    correlation_id: str | None
    payload: dict
    timestamp: str
    signature: str
