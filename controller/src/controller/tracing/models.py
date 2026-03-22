"""Trace data models for the Ditto Factory observability system."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TraceEventType(str, Enum):
    TASK_RECEIVED = "task_received"
    TASK_CLASSIFIED = "task_classified"
    SKILLS_INJECTED = "skills_injected"
    AGENT_SPAWNED = "agent_spawned"
    AGENT_STARTED = "agent_started"
    TOOL_INVOKED = "tool_invoked"
    REASONING_STEP = "reasoning_step"
    AGENT_COMPLETED = "agent_completed"
    SAFETY_CHECK = "safety_check"
    ERROR = "error"


def generate_trace_id() -> str:
    """Generate a W3C-compatible 32-char hex trace ID."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate a W3C-compatible 16-char hex span ID."""
    return secrets.token_hex(8)


@dataclass
class TraceSpan:
    span_id: str
    trace_id: str
    parent_span_id: str | None
    operation_name: TraceEventType

    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None

    input_summary: str = ""
    output_summary: str = ""
    reasoning: str = ""

    # Tool invocation fields
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None

    # LLM fields (Langfuse compatibility)
    model: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None

    # Status
    status: str = "ok"
    error_type: str | None = None
    error_message: str | None = None

    # Correlation
    thread_id: str = ""
    job_id: str | None = None

    # Metadata
    agent_name: str | None = None
    metadata: dict | None = None

    def complete(
        self,
        output_summary: str = "",
        status: str = "ok",
    ) -> None:
        """Mark span as completed, setting ended_at and computing duration_ms."""
        self.ended_at = datetime.now(timezone.utc)
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        self.output_summary = output_summary or self.output_summary
        self.status = status
