"""REST API endpoints for trace inspection.

  GET /api/traces              -- list recent traces
  GET /api/traces/{trace_id}   -- get full trace detail
  GET /api/traces/{trace_id}/report  -- rendered Markdown report
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from controller.tracing.renderer import TraceReportRenderer
from controller.tracing.store import TraceStore


# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------

class TraceListItem(BaseModel):
    trace_id: str
    thread_id: str
    started_at: str
    span_count: int
    status: str
    duration_ms: float | None = None


class TraceDetailResponse(BaseModel):
    trace_id: str
    spans: list[dict[str, Any]]
    report: str | None = None


class TraceReportResponse(BaseModel):
    trace_id: str
    view: str
    report: str


# ---------------------------------------------------------------------------
# Dependency injection helper (overridden in main.py / tests)
# ---------------------------------------------------------------------------

def get_trace_store() -> TraceStore:
    """Provide the trace store -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("", response_model=list[TraceListItem])
async def list_traces(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    store: TraceStore = Depends(get_trace_store),
) -> list[TraceListItem]:
    """List recent traces with summary info."""
    traces = await store.list_traces(limit=limit, offset=offset)
    return [
        TraceListItem(
            trace_id=t["trace_id"],
            thread_id=t["thread_id"],
            started_at=t["started_at"],
            span_count=t["span_count"],
            status=t["status"],
        )
        for t in traces
    ]


@router.get("/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(
    trace_id: str,
    store: TraceStore = Depends(get_trace_store),
) -> TraceDetailResponse:
    """Get full trace detail with all spans."""
    spans = await store.get_trace(trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found")

    span_dicts = [_span_to_dict(s) for s in spans]
    return TraceDetailResponse(trace_id=trace_id, spans=span_dicts)


@router.get("/{trace_id}/report", response_model=TraceReportResponse)
async def get_trace_report(
    trace_id: str,
    view: str = Query("hierarchical", pattern="^(hierarchical|timeline|decision)$"),
    store: TraceStore = Depends(get_trace_store),
) -> TraceReportResponse:
    """Return rendered Markdown report for a trace."""
    spans = await store.get_trace(trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found")

    renderer = TraceReportRenderer()
    if view == "timeline":
        report = renderer.render_timeline(spans)
    elif view == "decision":
        report = renderer.render_decision_summary(spans)
    else:
        report = renderer.render_hierarchical(spans)

    return TraceReportResponse(trace_id=trace_id, view=view, report=report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _span_to_dict(span: Any) -> dict[str, Any]:
    """Convert a TraceSpan to a JSON-safe dictionary."""
    d = asdict(span)
    # Convert datetime objects to ISO strings
    for key in ("started_at", "ended_at"):
        val = d.get(key)
        if val is not None and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    # Convert enum to string
    op = d.get("operation_name")
    if op is not None and hasattr(op, "value"):
        d["operation_name"] = op.value
    return d
