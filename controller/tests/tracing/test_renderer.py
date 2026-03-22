"""Tests for TraceReportRenderer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from controller.tracing.models import TraceEventType, TraceSpan, generate_span_id, generate_trace_id
from controller.tracing.renderer import TraceReportRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)


def _make_span(
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    operation_name: TraceEventType = TraceEventType.TASK_RECEIVED,
    thread_id: str = "thread-abc123",
    status: str = "ok",
    started_at: datetime | None = None,
    duration_ms: float | None = 100.0,
    input_summary: str = "",
    output_summary: str = "",
    reasoning: str = "",
    agent_name: str | None = None,
    tool_name: str | None = None,
    model: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    cost_usd: float | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> TraceSpan:
    start = started_at or _BASE_TIME
    end = start + timedelta(milliseconds=duration_ms) if duration_ms is not None else None
    return TraceSpan(
        span_id=generate_span_id(),
        trace_id=trace_id or generate_trace_id(),
        parent_span_id=parent_span_id,
        operation_name=operation_name,
        started_at=start,
        ended_at=end,
        duration_ms=duration_ms,
        input_summary=input_summary,
        output_summary=output_summary,
        reasoning=reasoning,
        tool_name=tool_name,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        status=status,
        error_type=error_type,
        error_message=error_message,
        thread_id=thread_id,
        agent_name=agent_name,
        metadata=metadata,
    )


def _make_hierarchy() -> list[TraceSpan]:
    """Create a realistic 4-span hierarchy."""
    tid = generate_trace_id()
    root = _make_span(
        trace_id=tid,
        operation_name=TraceEventType.TASK_RECEIVED,
        started_at=_BASE_TIME,
        duration_ms=0.0,
        input_summary="fix the login bug on mobile",
    )
    classify = _make_span(
        trace_id=tid,
        parent_span_id=root.span_id,
        operation_name=TraceEventType.TASK_CLASSIFIED,
        started_at=_BASE_TIME + timedelta(milliseconds=10),
        duration_ms=180.0,
        output_summary="mobile_auth_sdk",
        reasoning="Matched mobile auth pattern",
        metadata={"scores": {"mobile_auth_sdk": 0.87, "session_replay": 0.72}},
    )
    inject = _make_span(
        trace_id=tid,
        parent_span_id=root.span_id,
        operation_name=TraceEventType.SKILLS_INJECTED,
        started_at=_BASE_TIME + timedelta(milliseconds=200),
        duration_ms=45.0,
        output_summary="2 skills injected",
    )
    complete = _make_span(
        trace_id=tid,
        parent_span_id=root.span_id,
        operation_name=TraceEventType.AGENT_COMPLETED,
        started_at=_BASE_TIME + timedelta(milliseconds=300),
        duration_ms=272000.0,
        agent_name="coder-v1",
        output_summary="PR #42 created",
    )
    return [root, classify, inject, complete]


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_none(self):
        assert TraceReportRenderer._format_duration(None) == "--"

    def test_milliseconds(self):
        assert TraceReportRenderer._format_duration(50.0) == "50ms"

    def test_zero(self):
        assert TraceReportRenderer._format_duration(0.0) == "0ms"

    def test_seconds_exact(self):
        assert TraceReportRenderer._format_duration(5000.0) == "5s"

    def test_seconds_fractional(self):
        assert TraceReportRenderer._format_duration(1200.0) == "1.2s"

    def test_minutes(self):
        assert TraceReportRenderer._format_duration(272000.0) == "4m 32s"

    def test_exact_minutes(self):
        assert TraceReportRenderer._format_duration(120000.0) == "2m"

    def test_large_ms(self):
        assert TraceReportRenderer._format_duration(999.0) == "999ms"


# ---------------------------------------------------------------------------
# _format_relative_time
# ---------------------------------------------------------------------------

class TestFormatRelativeTime:
    def test_zero(self):
        result = TraceReportRenderer._format_relative_time(_BASE_TIME, _BASE_TIME)
        assert result == "00:00.000"

    def test_offset(self):
        later = _BASE_TIME + timedelta(milliseconds=180)
        result = TraceReportRenderer._format_relative_time(later, _BASE_TIME)
        assert result == "00:00.180"

    def test_minutes(self):
        later = _BASE_TIME + timedelta(minutes=2, seconds=30)
        result = TraceReportRenderer._format_relative_time(later, _BASE_TIME)
        assert result == "02:30.000"


# ---------------------------------------------------------------------------
# _build_tree
# ---------------------------------------------------------------------------

class TestBuildTree:
    def test_single_root(self):
        spans = [_make_span()]
        result = TraceReportRenderer._build_tree(spans)
        assert len(result) == 1
        assert result[0][1] == 0  # depth 0

    def test_parent_child(self):
        spans = _make_hierarchy()
        result = TraceReportRenderer._build_tree(spans)
        assert len(result) == 4
        # Root is depth 0
        assert result[0][1] == 0
        # Children are depth 1
        assert result[1][1] == 1
        assert result[2][1] == 1
        assert result[3][1] == 1

    def test_empty_list(self):
        result = TraceReportRenderer._build_tree([])
        assert result == []

    def test_orphaned_spans(self):
        """Spans with parent_span_id pointing to non-existent parents."""
        span = _make_span(parent_span_id="nonexistent")
        result = TraceReportRenderer._build_tree([span])
        # Orphans should still appear at depth 0 via fallback
        assert len(result) == 1


# ---------------------------------------------------------------------------
# render_hierarchical
# ---------------------------------------------------------------------------

class TestRenderHierarchical:
    def test_empty(self):
        renderer = TraceReportRenderer()
        result = renderer.render_hierarchical([])
        assert "No spans recorded" in result

    def test_single_span(self):
        renderer = TraceReportRenderer()
        span = _make_span(input_summary="test task")
        result = renderer.render_hierarchical([span])
        assert "TASK_RECEIVED" in result
        assert "test task" in result
        assert "Spans:** 1" in result

    def test_hierarchy(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_hierarchical(spans)
        assert "TASK_RECEIVED" in result
        assert "TASK_CLASSIFIED" in result
        assert "SKILLS_INJECTED" in result
        assert "AGENT_COMPLETED" in result
        assert "thread-abc123" in result
        assert "SUCCESS" in result

    def test_custom_title(self):
        renderer = TraceReportRenderer()
        spans = [_make_span()]
        result = renderer.render_hierarchical(spans, title="My Report")
        assert "# My Report" in result

    def test_error_status(self):
        renderer = TraceReportRenderer()
        span = _make_span(
            status="error",
            error_type="RuntimeError",
            error_message="something broke",
        )
        result = renderer.render_hierarchical([span])
        assert "ERROR" in result
        assert "RuntimeError" in result
        assert "something broke" in result

    def test_model_and_cost(self):
        renderer = TraceReportRenderer()
        span = _make_span(
            model="claude-3.5-sonnet",
            tokens_input=1000,
            tokens_output=500,
            cost_usd=0.0123,
        )
        result = renderer.render_hierarchical([span])
        assert "claude-3.5-sonnet" in result
        assert "1000" in result
        assert "$0.0123" in result


# ---------------------------------------------------------------------------
# render_timeline
# ---------------------------------------------------------------------------

class TestRenderTimeline:
    def test_empty(self):
        renderer = TraceReportRenderer()
        result = renderer.render_timeline([])
        assert "No spans recorded" in result

    def test_sorted_table(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_timeline(spans)
        assert "| Time |" in result
        assert "TASK_RECEIVED" in result
        # Verify it contains table rows
        lines = result.strip().split("\n")
        table_lines = [l for l in lines if l.startswith("|") and "Time" not in l and "---" not in l]
        assert len(table_lines) == 4

    def test_relative_times(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_timeline(spans)
        assert "00:00.000" in result  # first span at time 0


# ---------------------------------------------------------------------------
# render_decision_summary
# ---------------------------------------------------------------------------

class TestRenderDecisionSummary:
    def test_empty(self):
        renderer = TraceReportRenderer()
        result = renderer.render_decision_summary([])
        assert "No spans recorded" in result

    def test_classification(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_decision_summary(spans)
        assert "## Classification" in result
        assert "mobile_auth_sdk" in result

    def test_agent_outcome(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_decision_summary(spans)
        assert "## Agent Outcomes" in result
        assert "coder-v1" in result
        assert "PR #42 created" in result

    def test_skills_selected(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_decision_summary(spans)
        assert "## Skills Selected" in result
        assert "2 skills injected" in result

    def test_error_section(self):
        renderer = TraceReportRenderer()
        span = _make_span(
            operation_name=TraceEventType.ERROR,
            status="error",
            error_type="TimeoutError",
            error_message="Agent timed out after 30m",
        )
        result = renderer.render_decision_summary([span])
        assert "## Errors" in result
        assert "TimeoutError" in result

    def test_reasoning_trail(self):
        renderer = TraceReportRenderer()
        span = _make_span(
            operation_name=TraceEventType.TASK_CLASSIFIED,
            reasoning="High confidence match on mobile SDK",
        )
        result = renderer.render_decision_summary([span])
        assert "## Reasoning Trail" in result
        assert "High confidence match" in result

    def test_scores_in_metadata(self):
        renderer = TraceReportRenderer()
        spans = _make_hierarchy()
        result = renderer.render_decision_summary(spans)
        assert "0.87" in result
        assert "0.72" in result
