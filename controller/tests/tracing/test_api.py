"""Tests for tracing REST API endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from controller.tracing.api import get_trace_store, router
from controller.tracing.models import TraceEventType, TraceSpan, generate_span_id, generate_trace_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)
_TRACE_ID = "a" * 32


def _make_span(
    trace_id: str = _TRACE_ID,
    parent_span_id: str | None = None,
    operation_name: TraceEventType = TraceEventType.TASK_RECEIVED,
    started_at: datetime | None = None,
    duration_ms: float = 100.0,
    status: str = "ok",
    input_summary: str = "",
    output_summary: str = "",
    thread_id: str = "thread-1",
) -> TraceSpan:
    start = started_at or _BASE_TIME
    return TraceSpan(
        span_id=generate_span_id(),
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        operation_name=operation_name,
        started_at=start,
        ended_at=start + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        input_summary=input_summary,
        output_summary=output_summary,
        status=status,
        thread_id=thread_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.list_traces = AsyncMock(return_value=[
        {
            "trace_id": _TRACE_ID,
            "thread_id": "thread-1",
            "started_at": _BASE_TIME.isoformat(),
            "span_count": 3,
            "status": "ok",
        }
    ])
    store.get_trace = AsyncMock(return_value=[
        _make_span(input_summary="fix bug"),
        _make_span(
            operation_name=TraceEventType.TASK_CLASSIFIED,
            started_at=_BASE_TIME + timedelta(milliseconds=10),
            output_summary="classified",
        ),
    ])
    return store


@pytest.fixture
def client(mock_store):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_trace_store] = lambda: mock_store
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListTraces:
    def test_returns_list(self, client, mock_store):
        resp = client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trace_id"] == _TRACE_ID
        assert data[0]["span_count"] == 3
        mock_store.list_traces.assert_called_once_with(limit=50, offset=0)

    def test_custom_limit_offset(self, client, mock_store):
        client.get("/api/traces?limit=10&offset=5")
        mock_store.list_traces.assert_called_once_with(limit=10, offset=5)

    def test_empty_list(self, client, mock_store):
        mock_store.list_traces.return_value = []
        resp = client.get("/api/traces")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetTrace:
    def test_returns_spans(self, client):
        resp = client.get(f"/api/traces/{_TRACE_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == _TRACE_ID
        assert len(data["spans"]) == 2

    def test_not_found(self, client, mock_store):
        mock_store.get_trace.return_value = []
        resp = client.get("/api/traces/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


class TestGetTraceReport:
    def test_hierarchical_default(self, client):
        resp = client.get(f"/api/traces/{_TRACE_ID}/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["view"] == "hierarchical"
        assert "TASK_RECEIVED" in data["report"]

    def test_timeline_view(self, client):
        resp = client.get(f"/api/traces/{_TRACE_ID}/report?view=timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["view"] == "timeline"
        assert "| Time |" in data["report"]

    def test_decision_view(self, client):
        resp = client.get(f"/api/traces/{_TRACE_ID}/report?view=decision")
        assert resp.status_code == 200
        data = resp.json()
        assert data["view"] == "decision"
        assert "Decision Summary" in data["report"]

    def test_not_found(self, client, mock_store):
        mock_store.get_trace.return_value = []
        resp = client.get("/api/traces/nonexistent/report")
        assert resp.status_code == 404

    def test_report_contains_markdown(self, client):
        resp = client.get(f"/api/traces/{_TRACE_ID}/report?view=hierarchical")
        report = resp.json()["report"]
        # Should contain markdown heading
        assert report.startswith("#")
