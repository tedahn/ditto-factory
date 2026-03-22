"""Tests for tracing data models."""

from __future__ import annotations

from datetime import datetime, timezone

from controller.tracing.models import (
    TraceEventType,
    TraceSpan,
    generate_span_id,
    generate_trace_id,
)


def test_generate_trace_id_format():
    tid = generate_trace_id()
    assert len(tid) == 32
    int(tid, 16)  # must be valid hex


def test_generate_trace_id_uniqueness():
    ids = {generate_trace_id() for _ in range(100)}
    assert len(ids) == 100


def test_generate_span_id_format():
    sid = generate_span_id()
    assert len(sid) == 16
    int(sid, 16)  # must be valid hex


def test_generate_span_id_uniqueness():
    ids = {generate_span_id() for _ in range(100)}
    assert len(ids) == 100


def test_trace_event_type_values():
    assert TraceEventType.TASK_RECEIVED.value == "task_received"
    assert TraceEventType.ERROR.value == "error"
    assert TraceEventType.TOOL_INVOKED.value == "tool_invoked"


def test_trace_span_creation():
    now = datetime.now(timezone.utc)
    span = TraceSpan(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id=None,
        operation_name=TraceEventType.TASK_RECEIVED,
        started_at=now,
        input_summary="test input",
        thread_id="thread-1",
        job_id="job-1",
        agent_name="test-agent",
        metadata={"key": "value"},
    )
    assert span.span_id == "a" * 16
    assert span.trace_id == "b" * 32
    assert span.parent_span_id is None
    assert span.status == "ok"
    assert span.ended_at is None
    assert span.duration_ms is None
    assert span.metadata == {"key": "value"}


def test_trace_span_creation_all_fields():
    now = datetime.now(timezone.utc)
    span = TraceSpan(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id="c" * 16,
        operation_name=TraceEventType.TOOL_INVOKED,
        started_at=now,
        tool_name="bash",
        tool_args={"cmd": "ls"},
        tool_result="file.txt",
        model="claude-3",
        tokens_input=100,
        tokens_output=200,
        cost_usd=0.01,
    )
    assert span.tool_name == "bash"
    assert span.tool_args == {"cmd": "ls"}
    assert span.model == "claude-3"
    assert span.tokens_input == 100
    assert span.cost_usd == 0.01


def test_trace_span_complete():
    now = datetime.now(timezone.utc)
    span = TraceSpan(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id=None,
        operation_name=TraceEventType.AGENT_SPAWNED,
        started_at=now,
    )
    span.complete(output_summary="done", status="ok")
    assert span.ended_at is not None
    assert span.duration_ms is not None
    assert span.duration_ms >= 0
    assert span.output_summary == "done"
    assert span.status == "ok"


def test_trace_span_complete_preserves_output():
    now = datetime.now(timezone.utc)
    span = TraceSpan(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id=None,
        operation_name=TraceEventType.AGENT_COMPLETED,
        started_at=now,
        output_summary="existing",
    )
    span.complete()
    assert span.output_summary == "existing"


def test_trace_span_complete_error_status():
    now = datetime.now(timezone.utc)
    span = TraceSpan(
        span_id="a" * 16,
        trace_id="b" * 32,
        parent_span_id=None,
        operation_name=TraceEventType.ERROR,
        started_at=now,
    )
    span.complete(output_summary="failed", status="error")
    assert span.status == "error"
    assert span.output_summary == "failed"
