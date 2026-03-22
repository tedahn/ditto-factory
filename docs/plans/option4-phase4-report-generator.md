# Phase 4: Report Generator — Detailed Implementation Plan

**Option 4 (B Enhanced) | Phase 4 of 4**
**Status:** Proposed
**Date:** 2026-03-21

## Context

This is the PRIMARY deliverable of the entire traceability system. Phases 1-3 collect data; Phase 4 makes it useful. The PM's key insight: "Engineers need a review tool, not an SRE observability platform."

### Dependencies (provided by earlier phases)

- **Phase 1:** `trace_events` SQLite table with schema: `(id, trace_id, thread_id, event, component, timestamp, duration_ms, level, data)`. Indexes on `trace_id`, `thread_id`, and `level+timestamp`.
- **Phase 2:** Orchestrator instrumentation emitting events: `orchestrator.task.received`, `classifier.skills.matched`, `injector.skills.formatted`, `spawner.job.created`, `monitor.result.received`.
- **Phase 3:** Agent-side events: `agent.started`, `agent.commit.created`, `agent.completed`, `agent.error`, ingested into the same `trace_events` table via Redis result payload.

### Existing Codebase Integration Points

| Component | File | Relevance |
|-----------|------|-----------|
| FastAPI app | `controller/src/controller/main.py` | Router mounting via `app.include_router()` |
| API router | `controller/src/controller/api.py` | Pattern: `APIRouter`, dependency injection via `Depends()` |
| Models | `controller/src/controller/models.py` | `Job`, `AgentResult`, `Thread`, `JobStatus` dataclasses |
| Orchestrator | `controller/src/controller/orchestrator.py` | `handle_job_completion()` — hook point for auto-report |
| SafetyPipeline | `controller/src/controller/jobs/safety.py` | Called after job completion, before thread IDLE reset |
| SkillRegistry | `controller/src/controller/skills/registry.py` | Pattern for SQLite access with `aiosqlite` |
| Skills API | `controller/src/controller/skills/api.py` | Pattern for conditional router mounting |

---

## 1. Architecture Decision: Programmatic String Builder over Jinja2

### Decision

Use **programmatic string building** (f-strings + helper functions), not Jinja2 templates.

### Rationale

| Criterion | Jinja2 | Programmatic |
|-----------|--------|-------------|
| Dependency | New dependency | Zero new dependencies |
| Testability | Requires template file fixtures | Pure functions, trivially testable |
| Debuggability | Template errors are opaque | Standard Python stack traces |
| Complexity | Whitespace control is painful for Markdown | Direct string manipulation |
| Flexibility | Good for HTML, awkward for Markdown trees | Natural for `├──` tree rendering |
| Team familiarity | Template DSL to learn | Plain Python |

**Trade-off acknowledged:** If we later need HTML/PDF reports, Jinja2 becomes the right choice. The programmatic builder should be structured so a Jinja2 renderer can be swapped in without changing the data layer.

---

## 2. Data Layer: TraceQueryEngine

### File: `controller/src/controller/tracing/query.py`

This module queries `trace_events` and returns structured Python objects. It is the ONLY module that touches SQLite — renderers never see raw SQL.

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite


@dataclass
class TraceEvent:
    """A single trace event, parsed from the trace_events table."""
    id: int
    trace_id: str
    thread_id: str
    event: str
    component: str
    timestamp: datetime
    duration_ms: int | None
    level: str
    data: dict


@dataclass
class TraceSummary:
    """Lightweight summary for list views."""
    trace_id: str
    thread_id: str
    started_at: datetime
    ended_at: datetime | None
    total_duration_ms: int
    event_count: int
    error_count: int
    final_status: str  # "success" | "error" | "timeout" | "in_progress"
    task_preview: str  # First 80 chars of the original task


@dataclass
class TraceTimeline:
    """Complete trace with all events, ready for rendering."""
    trace_id: str
    thread_id: str
    events: list[TraceEvent]
    started_at: datetime
    ended_at: datetime | None
    total_duration_ms: int
    component_summary: dict[str, int]  # component -> event count
    error_count: int

    # Extracted decision data (parsed from event data fields)
    task_text: str | None
    classification: ClassificationDecision | None
    injection: InjectionDecision | None
    agent_type: AgentTypeDecision | None
    outcome: OutcomeDecision | None


@dataclass
class ClassificationDecision:
    """Extracted from classifier.skills.matched event."""
    method: str  # "semantic" | "tag_fallback"
    candidates_evaluated: int
    matches: list[dict]  # [{"slug": "...", "score": 0.87}, ...]
    rejected: list[dict]
    threshold: float
    embedding_cached: bool
    boost_applied: dict | None  # {"slug": score_delta, ...}


@dataclass
class InjectionDecision:
    """Extracted from injector.skills.formatted event."""
    skills_injected: list[str]
    total_size_bytes: int
    budget_bytes: int
    budget_utilization: float  # 0.0 - 1.0


@dataclass
class AgentTypeDecision:
    """Extracted from spawner.job.created event."""
    agent_type: str
    image: str
    reason: str | None


@dataclass
class OutcomeDecision:
    """Extracted from agent.completed and monitor.result.received events."""
    exit_code: int
    commit_count: int
    files_changed: int | None
    insertions: int | None
    deletions: int | None
    pr_url: str | None
    commit_message: str | None
    stderr_preview: str  # First 500 chars
    stderr_full_length: int


class TraceQueryEngine:
    """Queries trace_events SQLite table and returns structured objects.

    This is the ONLY class that touches the database. Renderers consume
    TraceTimeline / TraceSummary objects — never raw rows.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def get_timeline(self, trace_id: str) -> TraceTimeline | None:
        """Fetch all events for a trace and parse into a TraceTimeline."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM trace_events
                   WHERE trace_id = ?
                   ORDER BY timestamp ASC""",
                (trace_id,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return None

        events = []
        component_counts: dict[str, int] = {}
        error_count = 0

        for row in rows:
            evt = TraceEvent(
                id=row["id"],
                trace_id=row["trace_id"],
                thread_id=row["thread_id"],
                event=row["event"],
                component=row["component"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                duration_ms=row["duration_ms"],
                level=row["level"],
                data=json.loads(row["data"]),
            )
            events.append(evt)
            component_counts[evt.component] = component_counts.get(evt.component, 0) + 1
            if evt.level == "ERROR":
                error_count += 1

        started_at = events[0].timestamp
        ended_at = events[-1].timestamp
        total_ms = int((ended_at - started_at).total_seconds() * 1000)

        # Extract decision data from specific events
        classification = self._extract_classification(events)
        injection = self._extract_injection(events)
        agent_type = self._extract_agent_type(events)
        outcome = self._extract_outcome(events)
        task_text = self._extract_task_text(events)

        return TraceTimeline(
            trace_id=rows[0]["trace_id"],
            thread_id=rows[0]["thread_id"],
            events=events,
            started_at=started_at,
            ended_at=ended_at,
            total_duration_ms=total_ms,
            component_summary=component_counts,
            error_count=error_count,
            task_text=task_text,
            classification=classification,
            injection=injection,
            agent_type=agent_type,
            outcome=outcome,
        )

    async def list_traces(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        since: datetime | None = None,
        thread_id: str | None = None,
    ) -> list[TraceSummary]:
        """List recent traces with lightweight summaries.

        Filtering:
          - status: "success" | "error" — filters by presence of ERROR-level events
          - since: only traces started after this datetime
          - thread_id: only traces for this thread
        """
        conditions = []
        params: list = []

        if thread_id:
            conditions.append("thread_id = ?")
            params.append(thread_id)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                trace_id,
                thread_id,
                MIN(timestamp) as started_at,
                MAX(timestamp) as ended_at,
                COUNT(*) as event_count,
                SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) as error_count
            FROM trace_events
            {where_clause}
            GROUP BY trace_id
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            summaries = []
            for row in rows:
                started = datetime.fromisoformat(row["started_at"])
                ended = datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None
                total_ms = int((ended - started).total_seconds() * 1000) if ended else 0
                error_count = row["error_count"]

                # Determine status
                if error_count > 0:
                    final_status = "error"
                elif ended:
                    final_status = "success"
                else:
                    final_status = "in_progress"

                # Post-filter by status if requested
                if status and final_status != status:
                    continue

                # Fetch task preview from orchestrator.task.received event
                task_cursor = await db.execute(
                    """SELECT data FROM trace_events
                       WHERE trace_id = ? AND event = 'orchestrator.task.received'
                       LIMIT 1""",
                    (row["trace_id"],),
                )
                task_row = await task_cursor.fetchone()
                task_preview = ""
                if task_row:
                    task_data = json.loads(task_row["data"])
                    task_preview = task_data.get("task", "")[:80]

                summaries.append(TraceSummary(
                    trace_id=row["trace_id"],
                    thread_id=row["thread_id"],
                    started_at=started,
                    ended_at=ended,
                    total_duration_ms=total_ms,
                    event_count=row["event_count"],
                    error_count=error_count,
                    final_status=final_status,
                    task_preview=task_preview,
                ))

        return summaries

    async def search_traces(
        self,
        *,
        event_pattern: str | None = None,
        component: str | None = None,
        level: str | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[TraceSummary]:
        """Search traces by event pattern, component, or level.

        Returns unique trace summaries matching the criteria.
        """
        conditions = []
        params: list = []

        if event_pattern:
            conditions.append("event LIKE ?")
            params.append(f"%{event_pattern}%")
        if component:
            conditions.append("component = ?")
            params.append(component)
        if level:
            conditions.append("level = ?")
            params.append(level)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT DISTINCT trace_id FROM trace_events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            trace_ids = [row["trace_id"] for row in await cursor.fetchall()]

        # Reuse list_traces logic for summaries
        summaries = []
        for tid in trace_ids:
            result = await self.list_traces(limit=1, thread_id=None)
            # More efficient: batch query
            pass

        # Efficient batch approach:
        return await self._batch_summarize(trace_ids)

    async def _batch_summarize(self, trace_ids: list[str]) -> list[TraceSummary]:
        """Build TraceSummary objects for a list of trace_ids in one query."""
        if not trace_ids:
            return []

        placeholders = ",".join("?" for _ in trace_ids)
        query = f"""
            SELECT
                trace_id,
                thread_id,
                MIN(timestamp) as started_at,
                MAX(timestamp) as ended_at,
                COUNT(*) as event_count,
                SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) as error_count
            FROM trace_events
            WHERE trace_id IN ({placeholders})
            GROUP BY trace_id
            ORDER BY started_at DESC
        """

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, trace_ids)
            rows = await cursor.fetchall()

            summaries = []
            for row in rows:
                started = datetime.fromisoformat(row["started_at"])
                ended = datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None
                total_ms = int((ended - started).total_seconds() * 1000) if ended else 0

                summaries.append(TraceSummary(
                    trace_id=row["trace_id"],
                    thread_id=row["thread_id"],
                    started_at=started,
                    ended_at=ended,
                    total_duration_ms=total_ms,
                    event_count=row["event_count"],
                    error_count=row["error_count"],
                    final_status="error" if row["error_count"] > 0 else "success",
                    task_preview="",  # Skip preview for search results
                ))

        return summaries

    # ── Private extraction helpers ─────────────────────────────────

    def _extract_task_text(self, events: list[TraceEvent]) -> str | None:
        for e in events:
            if e.event == "orchestrator.task.received":
                return e.data.get("task")
        return None

    def _extract_classification(self, events: list[TraceEvent]) -> ClassificationDecision | None:
        for e in events:
            if e.event == "classifier.skills.matched":
                d = e.data
                return ClassificationDecision(
                    method=d.get("method", "unknown"),
                    candidates_evaluated=d.get("total_candidates", 0),
                    matches=d.get("skills_matched", []),
                    rejected=d.get("skills_rejected", []),
                    threshold=d.get("threshold", 0.5),
                    embedding_cached=d.get("embedding_cached", False),
                    boost_applied=d.get("boost_applied"),
                )
        return None

    def _extract_injection(self, events: list[TraceEvent]) -> InjectionDecision | None:
        for e in events:
            if e.event == "injector.skills.formatted":
                d = e.data
                return InjectionDecision(
                    skills_injected=d.get("skills", []),
                    total_size_bytes=d.get("total_size_bytes", 0),
                    budget_bytes=d.get("budget_bytes", 16384),
                    budget_utilization=d.get("budget_utilization", 0.0),
                )
        return None

    def _extract_agent_type(self, events: list[TraceEvent]) -> AgentTypeDecision | None:
        for e in events:
            if e.event == "spawner.job.created":
                d = e.data
                return AgentTypeDecision(
                    agent_type=d.get("agent_type", "general"),
                    image=d.get("image", "unknown"),
                    reason=d.get("reason"),
                )
        return None

    def _extract_outcome(self, events: list[TraceEvent]) -> OutcomeDecision | None:
        for e in events:
            if e.event == "monitor.result.received":
                d = e.data
                stderr = d.get("stderr", "")
                return OutcomeDecision(
                    exit_code=d.get("exit_code", -1),
                    commit_count=d.get("commit_count", 0),
                    files_changed=d.get("files_changed"),
                    insertions=d.get("insertions"),
                    deletions=d.get("deletions"),
                    pr_url=d.get("pr_url"),
                    commit_message=d.get("commit_message"),
                    stderr_preview=stderr[:500],
                    stderr_full_length=len(stderr),
                )
        return None
```

---

## 3. Renderer: TraceReportRenderer

### File: `controller/src/controller/tracing/renderer.py`

Three render methods, one class. Each method takes a `TraceTimeline` and returns a Markdown string.

```python
from __future__ import annotations

from controller.tracing.query import (
    TraceTimeline, TraceEvent, TraceSummary,
    ClassificationDecision, InjectionDecision,
    AgentTypeDecision, OutcomeDecision,
)


class TraceReportRenderer:
    """Renders TraceTimeline objects into Markdown strings.

    Three views:
      - hierarchical: Tree structure showing full execution flow
      - timeline: Chronological table with timestamps and durations
      - decision: Focused on WHY decisions were made
    """

    # ── Public API ────────────────────────────────────────────────

    def render(self, timeline: TraceTimeline, view: str = "hierarchical") -> str:
        """Dispatch to the appropriate view renderer."""
        renderers = {
            "hierarchical": self.render_hierarchical,
            "timeline": self.render_timeline,
            "decision": self.render_decision,
        }
        renderer = renderers.get(view)
        if renderer is None:
            raise ValueError(f"Unknown view: {view!r}. Options: {', '.join(renderers)}")
        return renderer(timeline)

    def render_hierarchical(self, t: TraceTimeline) -> str:
        """Tree structure showing the full execution flow.

        Example output:
            Trace: abc123 | 4m 32s | SUCCESS
            +-- 1. TASK_RECEIVED (0ms) -- "fix the login bug on mobile"
            +-- 2. TASK_CLASSIFIED (180ms)
            |   +-- Method: semantic search (Voyage-3)
            |   +-- Scores: mobile_auth_sdk=0.87, session_replay=0.72
            |   +-- Selected: mobile_auth_sdk, session_replay
            +-- 3. SKILLS_INJECTED (45ms) -- 3.9KB / 16KB budget
            ...
        """
        status = self._status_label(t)
        lines = [
            f"# Trace: {t.trace_id[:12]} | {_fmt_duration(t.total_duration_ms)} | {status}",
            "",
        ]

        if t.task_text:
            lines.append(f"> **Task:** {t.task_text}")
            lines.append("")

        step = 0

        # 1. Task received
        task_evt = self._find_event(t, "orchestrator.task.received")
        if task_evt:
            step += 1
            offset = self._offset_ms(t, task_evt)
            preview = _truncate(t.task_text or "", 60)
            lines.append(f"{step}. **TASK_RECEIVED** ({_fmt_duration(offset)}) -- \"{preview}\"")

        # 2. Classification
        cls_evt = self._find_event(t, "classifier.skills.matched")
        if cls_evt and t.classification:
            step += 1
            c = t.classification
            offset = self._offset_ms(t, cls_evt)
            lines.append(f"{step}. **TASK_CLASSIFIED** ({_fmt_duration(offset)})")
            lines.append(f"    - Method: {c.method}" +
                         (" (cached)" if c.embedding_cached else ""))
            if c.matches:
                scores = ", ".join(f"{m['slug']}={m['score']:.2f}" for m in c.matches[:5])
                lines.append(f"    - Scores: {scores}")
                selected = ", ".join(m["slug"] for m in c.matches)
                lines.append(f"    - Selected: {selected}")
            if c.boost_applied:
                boosts = ", ".join(f"{k} +{v:.2f}" for k, v in c.boost_applied.items())
                lines.append(f"    - Performance boost: {boosts}")

        # 3. Skills injected
        inj_evt = self._find_event(t, "injector.skills.formatted")
        if inj_evt and t.injection:
            step += 1
            inj = t.injection
            offset = self._offset_ms(t, inj_evt)
            size_kb = inj.total_size_bytes / 1024
            budget_kb = inj.budget_bytes / 1024
            lines.append(
                f"{step}. **SKILLS_INJECTED** ({_fmt_duration(offset)}) -- "
                f"{size_kb:.1f}KB / {budget_kb:.0f}KB budget"
            )

        # 4. Agent spawned
        spawn_evt = self._find_event(t, "spawner.job.created")
        if spawn_evt and t.agent_type:
            step += 1
            offset = self._offset_ms(t, spawn_evt)
            lines.append(
                f"{step}. **AGENT_SPAWNED** ({_fmt_duration(offset)}) -- "
                f"{t.agent_type.agent_type}:{t.agent_type.image}"
            )

        # 5. Agent execution (aggregate agent.* events)
        agent_events = [e for e in t.events if e.component == "agent"]
        if agent_events:
            step += 1
            agent_start = agent_events[0]
            agent_end = agent_events[-1]
            agent_duration_ms = int(
                (agent_end.timestamp - agent_start.timestamp).total_seconds() * 1000
            )
            lines.append(f"{step}. **AGENT_EXECUTION** ({_fmt_duration(agent_duration_ms)})")

            # Count commits
            commits = [e for e in agent_events if e.event == "agent.commit.created"]
            if commits:
                lines.append(f"    - Commits: {len(commits)}")
                for c_evt in commits:
                    msg = c_evt.data.get("message", "")[:60]
                    lines.append(f"      - \"{msg}\"")

            # Count errors
            errors = [e for e in agent_events if e.level == "ERROR"]
            if errors:
                lines.append(f"    - Errors: {len(errors)}")
                for err in errors[:3]:
                    lines.append(f"      - {err.data.get('error', err.event)}")

        # 6. Outcome
        if t.outcome:
            step += 1
            o = t.outcome
            status_emoji = "exit_code=" + str(o.exit_code)
            result_parts = [status_emoji]
            if o.pr_url:
                result_parts.append(f"PR {o.pr_url}")
            if o.commit_count:
                result_parts.append(f"{o.commit_count} commit(s)")
            lines.append(f"{step}. **RESULT** -- {', '.join(result_parts)}")
            if o.files_changed is not None:
                lines.append(
                    f"    - Git: {o.files_changed} files changed, "
                    f"+{o.insertions or 0} -{o.deletions or 0}"
                )
            if o.stderr_preview and o.exit_code != 0:
                lines.append(f"    - Stderr (first 500 chars):")
                lines.append(f"    ```")
                lines.append(f"    {o.stderr_preview}")
                lines.append(f"    ```")
                if o.stderr_full_length > 500:
                    lines.append(
                        f"    _(truncated, full output: {o.stderr_full_length} chars)_"
                    )

        lines.append("")
        lines.append(f"---")
        lines.append(f"_Generated at {_now_iso()} | "
                      f"{len(t.events)} events across "
                      f"{len(t.component_summary)} components_")

        return "\n".join(lines)

    def render_timeline(self, t: TraceTimeline) -> str:
        """Chronological table with timestamps and durations.

        Example output:
            | Time      | Duration | Event              | Component  | Details              |
            |-----------|----------|--------------------|------------|----------------------|
            | 00:00.000 | 180ms    | skills.matched     | classifier | 2 skills matched     |
        """
        lines = [
            f"# Timeline: {t.trace_id[:12]} | {_fmt_duration(t.total_duration_ms)} | "
            f"{self._status_label(t)}",
            "",
        ]

        if t.task_text:
            lines.append(f"> **Task:** {t.task_text}")
            lines.append("")

        # Table header
        lines.append("| Time | Duration | Event | Component | Level | Details |")
        lines.append("|------|----------|-------|-----------|-------|---------|")

        for evt in t.events:
            offset = self._offset_ms(t, evt)
            time_str = _fmt_offset(offset)
            dur_str = _fmt_duration(evt.duration_ms) if evt.duration_ms else "-"
            # Extract a short detail string from event data
            detail = self._event_detail(evt)
            # Truncate long details for table readability
            detail = _truncate(detail, 50)
            event_short = evt.event.split(".")[-1] if "." in evt.event else evt.event

            lines.append(
                f"| {time_str} | {dur_str} | {evt.event} | "
                f"{evt.component} | {evt.level} | {detail} |"
            )

        lines.append("")
        lines.append(f"---")
        lines.append(f"_{len(t.events)} events | "
                      f"{t.error_count} errors | "
                      f"Components: {', '.join(sorted(t.component_summary.keys()))}_")

        return "\n".join(lines)

    def render_decision(self, t: TraceTimeline) -> str:
        """Focused view on WHY decisions were made.

        Shows skill selection rationale, agent type resolution,
        performance boosts, and outcome analysis.
        """
        lines = [
            f"# Decision Summary: {t.trace_id[:12]}",
            "",
        ]

        if t.task_text:
            lines.append(f"> **Task:** {t.task_text}")
            lines.append("")

        # ── Skill Selection ────────────────────────────────────────
        lines.append("## Skill Selection")
        lines.append("")

        if t.classification:
            c = t.classification
            lines.append(f"- **Method:** {c.method}" +
                         (" (embedding cached)" if c.embedding_cached else " (embedding computed)"))
            lines.append(f"- **Candidates evaluated:** {c.candidates_evaluated}")
            lines.append(f"- **Threshold:** {c.threshold}")
            lines.append("")

            if c.matches:
                lines.append("**Matched skills:**")
                lines.append("")
                lines.append("| Skill | Score | Status |")
                lines.append("|-------|-------|--------|")
                for m in c.matches:
                    score = m.get("score", 0)
                    bar = _ascii_bar(score, width=10)
                    lines.append(f"| {m['slug']} | {score:.3f} {bar} | SELECTED |")

            if c.rejected:
                lines.append("")
                lines.append("**Rejected skills (below threshold):**")
                lines.append("")
                lines.append("| Skill | Score | Reason |")
                lines.append("|-------|-------|--------|")
                for r in c.rejected[:5]:
                    score = r.get("score", 0)
                    reason = r.get("reason", "below_threshold")
                    bar = _ascii_bar(score, width=10)
                    lines.append(f"| {r['slug']} | {score:.3f} {bar} | {reason} |")
                if len(c.rejected) > 5:
                    lines.append(f"| ... | ... | {len(c.rejected) - 5} more |")

            if c.boost_applied:
                lines.append("")
                lines.append("**Performance boosts applied:**")
                for slug, delta in c.boost_applied.items():
                    lines.append(f"- {slug}: +{delta:.3f} (from historical success rate)")
        else:
            lines.append("_No classification data available._")

        lines.append("")

        # ── Skill Injection ────────────────────────────────────────
        lines.append("## Skill Injection")
        lines.append("")

        if t.injection:
            inj = t.injection
            size_kb = inj.total_size_bytes / 1024
            budget_kb = inj.budget_bytes / 1024
            util_pct = inj.budget_utilization * 100
            lines.append(f"- **Skills injected:** {', '.join(inj.skills_injected)}")
            lines.append(f"- **Total size:** {size_kb:.1f}KB / {budget_kb:.0f}KB "
                         f"({util_pct:.0f}% of budget)")
            bar = _ascii_bar(inj.budget_utilization, width=20)
            lines.append(f"- **Budget utilization:** {bar}")
        else:
            lines.append("_No injection data available._")

        lines.append("")

        # ── Agent Type ─────────────────────────────────────────────
        lines.append("## Agent Type")
        lines.append("")

        if t.agent_type:
            at = t.agent_type
            lines.append(f"- **Resolved type:** {at.agent_type}")
            lines.append(f"- **Image:** {at.image}")
            if at.reason:
                lines.append(f"- **Reason:** {at.reason}")
        else:
            lines.append("_No agent type data available._")

        lines.append("")

        # ── Outcome ────────────────────────────────────────────────
        lines.append("## Outcome")
        lines.append("")

        if t.outcome:
            o = t.outcome
            status = "SUCCESS" if o.exit_code == 0 else f"FAILED (exit {o.exit_code})"
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Commits:** {o.commit_count}")
            if o.files_changed is not None:
                lines.append(f"- **Files changed:** {o.files_changed} "
                             f"(+{o.insertions or 0} -{o.deletions or 0})")
            if o.commit_message:
                lines.append(f"- **Commit message:** \"{o.commit_message}\"")
            if o.pr_url:
                lines.append(f"- **Pull request:** {o.pr_url}")
            if o.exit_code != 0 and o.stderr_preview:
                lines.append("")
                lines.append("**Error output:**")
                lines.append("```")
                lines.append(o.stderr_preview)
                lines.append("```")
                if o.stderr_full_length > 500:
                    lines.append(f"_(truncated, full: {o.stderr_full_length} chars)_")
        else:
            lines.append("_No outcome data available._")

        lines.append("")

        # ── Execution Timing ───────────────────────────────────────
        lines.append("## Timing Breakdown")
        lines.append("")
        lines.append("| Phase | Duration | % of Total |")
        lines.append("|-------|----------|------------|")

        phase_events = {
            "Classification": "classifier.skills.matched",
            "Injection": "injector.skills.formatted",
            "Spawning": "spawner.job.created",
        }
        for phase_name, event_name in phase_events.items():
            evt = self._find_event(t, event_name)
            if evt and evt.duration_ms:
                pct = (evt.duration_ms / t.total_duration_ms * 100) if t.total_duration_ms else 0
                lines.append(
                    f"| {phase_name} | {_fmt_duration(evt.duration_ms)} | {pct:.1f}% |"
                )

        # Agent execution as aggregate
        agent_events = [e for e in t.events if e.component == "agent"]
        if len(agent_events) >= 2:
            agent_ms = int(
                (agent_events[-1].timestamp - agent_events[0].timestamp).total_seconds() * 1000
            )
            pct = (agent_ms / t.total_duration_ms * 100) if t.total_duration_ms else 0
            lines.append(f"| Agent Execution | {_fmt_duration(agent_ms)} | {pct:.1f}% |")

        lines.append(f"| **Total** | **{_fmt_duration(t.total_duration_ms)}** | **100%** |")

        lines.append("")
        lines.append("---")
        lines.append(f"_Generated at {_now_iso()}_")

        return "\n".join(lines)

    # ── Trace List Rendering ──────────────────────────────────────

    def render_trace_list(self, summaries: list[TraceSummary]) -> str:
        """Render a list of trace summaries as a Markdown table."""
        if not summaries:
            return "No traces found."

        lines = [
            "# Recent Traces",
            "",
            "| Trace ID | Thread | Status | Duration | Events | Errors | Task |",
            "|----------|--------|--------|----------|--------|--------|------|",
        ]

        for s in summaries:
            status = s.final_status.upper()
            lines.append(
                f"| {s.trace_id[:12]} | {s.thread_id[:12]} | {status} | "
                f"{_fmt_duration(s.total_duration_ms)} | {s.event_count} | "
                f"{s.error_count} | {_truncate(s.task_preview, 40)} |"
            )

        lines.append("")
        lines.append(f"_{len(summaries)} traces shown_")
        return "\n".join(lines)

    # ── Private helpers ───────────────────────────────────────────

    def _status_label(self, t: TraceTimeline) -> str:
        if t.outcome and t.outcome.exit_code == 0:
            return "SUCCESS"
        elif t.outcome and t.outcome.exit_code != 0:
            return f"FAILED (exit {t.outcome.exit_code})"
        elif t.error_count > 0:
            return "ERROR"
        else:
            return "UNKNOWN"

    def _find_event(self, t: TraceTimeline, event_name: str) -> TraceEvent | None:
        for e in t.events:
            if e.event == event_name:
                return e
        return None

    def _offset_ms(self, t: TraceTimeline, evt: TraceEvent) -> int:
        return int((evt.timestamp - t.started_at).total_seconds() * 1000)

    def _event_detail(self, evt: TraceEvent) -> str:
        """Extract a human-readable detail string from event data."""
        d = evt.data
        if not d:
            return ""

        # Event-specific formatters
        formatters = {
            "orchestrator.task.received": lambda: _truncate(d.get("task", ""), 50),
            "classifier.skills.matched": lambda: (
                f"{len(d.get('skills_matched', []))} skills matched"
            ),
            "injector.skills.formatted": lambda: (
                f"{d.get('total_size_bytes', 0) / 1024:.1f}KB injected"
            ),
            "spawner.job.created": lambda: (
                f"{d.get('agent_type', '?')}:{d.get('image', '?')}"
            ),
            "agent.commit.created": lambda: _truncate(d.get("message", ""), 50),
            "agent.error": lambda: _truncate(d.get("error", ""), 50),
            "monitor.result.received": lambda: (
                f"exit={d.get('exit_code', '?')}, "
                f"{d.get('commit_count', 0)} commits"
            ),
        }

        formatter = formatters.get(evt.event)
        if formatter:
            return formatter()

        # Generic: show first key-value pair
        if d:
            key = next(iter(d))
            val = str(d[key])[:30]
            return f"{key}={val}"
        return ""


# ── Module-level formatting utilities ─────────────────────────────────

def _fmt_duration(ms: int | None) -> str:
    """Format milliseconds as human-readable duration.

    Examples: "0ms", "180ms", "2.1s", "4m 32s", "1h 5m"
    """
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _fmt_offset(ms: int) -> str:
    """Format offset as MM:SS.mmm for timeline view."""
    total_seconds = ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _ascii_bar(value: float, width: int = 10) -> str:
    """Render a value (0.0-1.0) as an ASCII bar chart.

    Example: 0.87 with width=10 -> "[========= ]"
    """
    filled = int(value * width)
    empty = width - filled
    return f"[{'=' * filled}{' ' * empty}]"


def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

---

## 4. CLI Commands

### File: `controller/src/controller/tracing/cli.py`

The CLI uses `argparse` (no new dependencies) and is callable via `python -m controller.tracing`.

```python
"""CLI for trace inspection and report generation.

Usage:
    python -m controller.tracing report <trace_id> [--view hierarchical|timeline|decision] [--output file.md]
    python -m controller.tracing list [--limit 20] [--status success|error] [--thread THREAD_ID]
    python -m controller.tracing search [--event PATTERN] [--component NAME] [--level ERROR] [--since 24h]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controller.tracing.query import TraceQueryEngine
from controller.tracing.renderer import TraceReportRenderer


# ── Terminal color helpers ────────────────────────────────────────

_COLORS = {
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _colorize(text: str, color: str) -> str:
    """Apply ANSI color if stdout is a terminal."""
    if not sys.stdout.isatty():
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def _status_color(status: str) -> str:
    """Color-code a status string for terminal output."""
    if "SUCCESS" in status.upper():
        return _colorize(status, "green")
    elif "FAIL" in status.upper() or "ERROR" in status.upper():
        return _colorize(status, "red")
    else:
        return _colorize(status, "yellow")


# ── Duration parsing ─────────────────────────────────────────────

def _parse_since(since_str: str) -> datetime:
    """Parse a relative duration string like '24h', '7d', '30m' into a datetime.

    Supported suffixes: m (minutes), h (hours), d (days)
    """
    now = datetime.now(timezone.utc)
    if since_str.endswith("m"):
        delta = timedelta(minutes=int(since_str[:-1]))
    elif since_str.endswith("h"):
        delta = timedelta(hours=int(since_str[:-1]))
    elif since_str.endswith("d"):
        delta = timedelta(days=int(since_str[:-1]))
    else:
        raise argparse.ArgumentTypeError(
            f"Invalid duration: {since_str!r}. Use format: 30m, 24h, 7d"
        )
    return now - delta


# ── Subcommand handlers ──────────────────────────────────────────

async def cmd_report(args: argparse.Namespace) -> None:
    """Render a trace report."""
    engine = TraceQueryEngine(args.db_path)
    renderer = TraceReportRenderer()

    timeline = await engine.get_timeline(args.trace_id)
    if timeline is None:
        print(f"Trace {args.trace_id!r} not found.", file=sys.stderr)
        sys.exit(1)

    report = renderer.render(timeline, view=args.view)

    # Apply terminal coloring to status lines
    if sys.stdout.isatty() and not args.output:
        for status_word in ["SUCCESS", "FAILED", "ERROR"]:
            if status_word in report:
                color = "green" if status_word == "SUCCESS" else "red"
                report = report.replace(status_word, _colorize(status_word, color))

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to {output_path}")
    else:
        print(report)


async def cmd_list(args: argparse.Namespace) -> None:
    """List recent traces."""
    engine = TraceQueryEngine(args.db_path)
    renderer = TraceReportRenderer()

    since = _parse_since(args.since) if args.since else None

    summaries = await engine.list_traces(
        limit=args.limit,
        status=args.status,
        since=since,
        thread_id=args.thread,
    )

    if not summaries:
        print("No traces found.")
        return

    report = renderer.render_trace_list(summaries)

    # Colorize status column in terminal
    if sys.stdout.isatty():
        report = report.replace("| SUCCESS |", f"| {_colorize('SUCCESS', 'green')} |")
        report = report.replace("| ERROR |", f"| {_colorize('ERROR', 'red')} |")
        report = report.replace("| FAILED |", f"| {_colorize('FAILED', 'red')} |")

    print(report)


async def cmd_search(args: argparse.Namespace) -> None:
    """Search traces by criteria."""
    engine = TraceQueryEngine(args.db_path)
    renderer = TraceReportRenderer()

    since = _parse_since(args.since) if args.since else None

    summaries = await engine.search_traces(
        event_pattern=args.event,
        component=args.component,
        level=args.level,
        since=since,
        limit=args.limit,
    )

    if not summaries:
        print("No matching traces found.")
        return

    report = renderer.render_trace_list(summaries)
    print(report)


# ── Argument parser ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m controller.tracing",
        description="Ditto Factory trace inspection and report generation",
    )
    parser.add_argument(
        "--db-path",
        default="trace_events.db",
        help="Path to trace_events SQLite database (default: trace_events.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # report
    report_parser = subparsers.add_parser("report", help="Render a trace report")
    report_parser.add_argument("trace_id", help="Trace ID to render")
    report_parser.add_argument(
        "--view",
        choices=["hierarchical", "timeline", "decision"],
        default="hierarchical",
        help="Report view (default: hierarchical)",
    )
    report_parser.add_argument(
        "--output", "-o",
        help="Write report to file instead of stdout",
    )

    # list
    list_parser = subparsers.add_parser("list", help="List recent traces")
    list_parser.add_argument("--limit", type=int, default=20, help="Max traces to show")
    list_parser.add_argument("--status", choices=["success", "error"], help="Filter by status")
    list_parser.add_argument("--since", help="Show traces since (e.g., 24h, 7d, 30m)")
    list_parser.add_argument("--thread", help="Filter by thread ID")

    # search
    search_parser = subparsers.add_parser("search", help="Search traces")
    search_parser.add_argument("--event", help="Event name pattern (substring match)")
    search_parser.add_argument("--component", help="Component name (exact match)")
    search_parser.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    search_parser.add_argument("--since", help="Search since (e.g., 24h, 7d)")
    search_parser.add_argument("--limit", type=int, default=20)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "report": cmd_report,
        "list": cmd_list,
        "search": cmd_search,
    }

    handler = handlers[args.command]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
```

### File: `controller/src/controller/tracing/__main__.py`

```python
"""Allow running as: python -m controller.tracing"""
from controller.tracing.cli import main

main()
```

---

## 5. FastAPI Endpoints

### File: `controller/src/controller/tracing/api.py`

Follows the existing pattern from `controller/skills/api.py` -- a router that gets mounted conditionally.

```python
"""Trace report API endpoints.

Mounted conditionally in main.py when tracing is enabled.

Endpoints:
    GET /api/traces                          -- list recent traces (paginated)
    GET /api/traces/{trace_id}               -- raw trace events as JSON
    GET /api/traces/{trace_id}/report        -- rendered Markdown report
    GET /api/traces/thread/{thread_id}       -- traces for a specific thread
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from controller.tracing.query import TraceQueryEngine, TraceSummary
from controller.tracing.renderer import TraceReportRenderer


# ── Dependency injection ──────────────────────────────────────────

def get_trace_engine() -> TraceQueryEngine:
    """Overridden in main.py via app.dependency_overrides."""
    raise RuntimeError("TraceQueryEngine not configured")


TraceEngineDep = Annotated[TraceQueryEngine, Depends(get_trace_engine)]


# ── Response models ───────────────────────────────────────────────

class TraceSummaryResponse(BaseModel):
    trace_id: str
    thread_id: str
    started_at: str
    ended_at: str | None
    total_duration_ms: int
    event_count: int
    error_count: int
    final_status: str
    task_preview: str


class TraceEventResponse(BaseModel):
    id: int
    trace_id: str
    thread_id: str
    event: str
    component: str
    timestamp: str
    duration_ms: int | None
    level: str
    data: dict


class TraceDetailResponse(BaseModel):
    trace_id: str
    thread_id: str
    started_at: str
    ended_at: str | None
    total_duration_ms: int
    event_count: int
    error_count: int
    events: list[TraceEventResponse]


class TraceListResponse(BaseModel):
    traces: list[TraceSummaryResponse]
    total: int
    limit: int
    offset: int


# ── Router ────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("", response_model=TraceListResponse)
async def list_traces(
    engine: TraceEngineDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, pattern="^(success|error)$"),
    thread_id: str | None = Query(default=None),
):
    """List recent traces with summary information."""
    summaries = await engine.list_traces(
        limit=limit,
        offset=offset,
        status=status,
        thread_id=thread_id,
    )
    return TraceListResponse(
        traces=[
            TraceSummaryResponse(
                trace_id=s.trace_id,
                thread_id=s.thread_id,
                started_at=s.started_at.isoformat(),
                ended_at=s.ended_at.isoformat() if s.ended_at else None,
                total_duration_ms=s.total_duration_ms,
                event_count=s.event_count,
                error_count=s.error_count,
                final_status=s.final_status,
                task_preview=s.task_preview,
            )
            for s in summaries
        ],
        total=len(summaries),  # TODO: add COUNT query for true total
        limit=limit,
        offset=offset,
    )


@router.get("/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(trace_id: str, engine: TraceEngineDep):
    """Get full trace with all events as structured JSON."""
    timeline = await engine.get_timeline(trace_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    return TraceDetailResponse(
        trace_id=timeline.trace_id,
        thread_id=timeline.thread_id,
        started_at=timeline.started_at.isoformat(),
        ended_at=timeline.ended_at.isoformat() if timeline.ended_at else None,
        total_duration_ms=timeline.total_duration_ms,
        event_count=len(timeline.events),
        error_count=timeline.error_count,
        events=[
            TraceEventResponse(
                id=e.id,
                trace_id=e.trace_id,
                thread_id=e.thread_id,
                event=e.event,
                component=e.component,
                timestamp=e.timestamp.isoformat(),
                duration_ms=e.duration_ms,
                level=e.level,
                data=e.data,
            )
            for e in timeline.events
        ],
    )


@router.get("/{trace_id}/report", response_class=PlainTextResponse)
async def get_trace_report(
    trace_id: str,
    engine: TraceEngineDep,
    view: str = Query(
        default="hierarchical",
        pattern="^(hierarchical|timeline|decision)$",
    ),
):
    """Render a Markdown trace report.

    Query params:
        view: "hierarchical" (default), "timeline", or "decision"
    """
    timeline = await engine.get_timeline(trace_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

    renderer = TraceReportRenderer()
    report = renderer.render(timeline, view=view)

    return PlainTextResponse(report, media_type="text/markdown")


@router.get("/thread/{thread_id}")
async def list_traces_for_thread(thread_id: str, engine: TraceEngineDep):
    """List all traces for a specific thread."""
    summaries = await engine.list_traces(thread_id=thread_id, limit=50)
    return {
        "thread_id": thread_id,
        "traces": [
            TraceSummaryResponse(
                trace_id=s.trace_id,
                thread_id=s.thread_id,
                started_at=s.started_at.isoformat(),
                ended_at=s.ended_at.isoformat() if s.ended_at else None,
                total_duration_ms=s.total_duration_ms,
                event_count=s.event_count,
                error_count=s.error_count,
                final_status=s.final_status,
                task_preview=s.task_preview,
            )
            for s in summaries
        ],
        "count": len(summaries),
    }
```

### Wiring into `main.py`

Add to the `lifespan` function, following the existing pattern for skills API:

```python
# In main.py lifespan(), after skills API mounting:

# Mount traces API if tracing is enabled
if settings.trace_db_path:
    try:
        from controller.tracing.api import router as trace_router, get_trace_engine
        from controller.tracing.query import TraceQueryEngine

        trace_engine = TraceQueryEngine(db_path=settings.trace_db_path)
        app.dependency_overrides[get_trace_engine] = lambda: trace_engine
        app.include_router(trace_router)
        logger.info("Traces API router mounted")
    except Exception:
        logger.exception("Failed to mount traces API router")
```

---

## 6. Auto-Report on Job Completion

### Hook Point: `Orchestrator.handle_job_completion()`

After `pipeline.process()` and performance tracking, generate and persist the report.

```python
# In orchestrator.py, at the end of handle_job_completion():

# Auto-generate trace report
if self._settings.trace_db_path and self._settings.trace_auto_report:
    try:
        from controller.tracing.query import TraceQueryEngine
        from controller.tracing.renderer import TraceReportRenderer
        from controller.tracing.persistence import save_trace_report

        engine = TraceQueryEngine(self._settings.trace_db_path)
        # trace_id was set at the start of handle_task()
        trace_id = getattr(self, '_current_trace_id', None)
        if trace_id:
            timeline = await engine.get_timeline(trace_id)
            if timeline:
                renderer = TraceReportRenderer()
                report_md = renderer.render(timeline, view="hierarchical")

                # 1. Save to filesystem
                await save_trace_report(
                    trace_id=trace_id,
                    thread_id=thread_id,
                    report_md=report_md,
                    output_dir=self._settings.trace_report_dir,
                )

                # 2. Attach to Job record
                if active_job:
                    await self._state.update_job_result_extra(
                        active_job.id,
                        {"trace_report": report_md[:5000]},  # Truncate for DB
                    )

                # 3. Include in integration notification
                # The integration.report_result() already happened above,
                # so we send a follow-up with the trace link
                if integration and hasattr(integration, 'send_trace_report'):
                    await integration.send_trace_report(
                        thread, trace_id, report_md
                    )
    except Exception:
        logger.exception("Failed to generate auto-report for trace %s", trace_id)
```

### File: `controller/src/controller/tracing/persistence.py`

```python
"""Persist trace reports to the filesystem."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path


async def save_trace_report(
    *,
    trace_id: str,
    thread_id: str,
    report_md: str,
    output_dir: str = "traces",
) -> Path:
    """Save a trace report as a Markdown file.

    File naming: traces/{date}/{thread_id}/{trace_id}.md
    This groups reports by date and thread for easy browsing.
    """
    now = datetime.now(timezone.utc)
    date_dir = now.strftime("%Y-%m-%d")

    report_dir = Path(output_dir) / date_dir / thread_id[:16]
    report_path = report_dir / f"{trace_id[:16]}.md"

    # Run file I/O in a thread to avoid blocking the event loop
    def _write():
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")

    await asyncio.to_thread(_write)
    return report_path
```

---

## 7. Configuration

### New settings in `controller/src/controller/config.py`

```python
# Add to Settings dataclass:

# Tracing
trace_db_path: str = os.getenv("DITTO_TRACE_DB_PATH", "trace_events.db")
trace_auto_report: bool = os.getenv("DITTO_TRACE_AUTO_REPORT", "true").lower() == "true"
trace_report_dir: str = os.getenv("DITTO_TRACE_REPORT_DIR", "traces")
```

---

## 8. Report Formatting Details

### Stderr Handling

```python
# In OutcomeDecision extraction:
# - Store first 500 chars as stderr_preview
# - Store full length for "see full at..." messaging
# - In hierarchical view: show stderr only on non-zero exit
# - In decision view: always show under "Error output" section

# Template for truncation message:
# _(truncated, full output: 12,847 chars -- see `python -m controller.tracing report {trace_id} --view timeline`)_
```

### Git Diff Display

Git diffs are NOT stored in trace events (they're in the PR). The report links to the PR URL instead:

```markdown
## Outcome
- **Pull request:** https://github.com/org/repo/pull/247
- **Files changed:** 5 (+42 -18)
- **Commit:** "fix: session token refresh timezone offset"
```

If we later want inline diffs, the agent-side instrumentation (Phase 3) would need to capture `git diff --stat` output as a trace event with `event=agent.git.diff_stat`.

### Classification Score Formatting

ASCII bar charts in tables for terminal/Markdown compatibility:

```
| Skill            | Score          | Status   |
|------------------|----------------|----------|
| mobile_auth_sdk  | 0.870 [======= ] | SELECTED |
| session_replay   | 0.720 [=====   ] | SELECTED |
| ui_lint          | 0.650 [====    ] | REJECTED |
```

### Duration Formatting

The `_fmt_duration()` function handles all cases:

| Input (ms) | Output |
|------------|--------|
| 0 | `0ms` |
| 45 | `45ms` |
| 180 | `180ms` |
| 2100 | `2.1s` |
| 272000 | `4m 32s` |
| 3900000 | `1h 5m` |

### Terminal Color Coding

ANSI colors applied ONLY when `sys.stdout.isatty()` returns True:
- SUCCESS / exit_code=0: green (`\033[92m`)
- FAILED / ERROR: red (`\033[91m`)
- IN_PROGRESS / UNKNOWN: yellow (`\033[93m`)
- Trace IDs, timestamps: cyan (`\033[96m`)

When `--output file.md` is used or stdout is piped, colors are omitted.

---

## 9. New Files Summary

| File | Purpose |
|------|---------|
| `controller/src/controller/tracing/__init__.py` | Package init |
| `controller/src/controller/tracing/__main__.py` | `python -m controller.tracing` entry point |
| `controller/src/controller/tracing/query.py` | `TraceQueryEngine` -- data access layer |
| `controller/src/controller/tracing/renderer.py` | `TraceReportRenderer` -- three Markdown views |
| `controller/src/controller/tracing/cli.py` | CLI argument parsing and handlers |
| `controller/src/controller/tracing/api.py` | FastAPI router with 4 endpoints |
| `controller/src/controller/tracing/persistence.py` | Save reports to filesystem |
| `controller/tests/test_trace_renderer.py` | Unit + snapshot tests for renderer |
| `controller/tests/test_trace_query.py` | Unit tests for query engine |
| `controller/tests/test_trace_api.py` | Integration tests for API endpoints |
| `controller/tests/test_trace_cli.py` | CLI command tests |
| `controller/tests/fixtures/trace_golden/` | Golden files for snapshot tests |

---

## 10. Test Plan

### 10.1 Unit Tests: Renderer (`test_trace_renderer.py`)

```python
"""Tests for TraceReportRenderer.

Each test constructs a TraceTimeline with known data and verifies
the rendered Markdown contains expected content.
"""
import pytest
from datetime import datetime, timezone, timedelta

from controller.tracing.query import (
    TraceTimeline, TraceEvent, ClassificationDecision,
    InjectionDecision, AgentTypeDecision, OutcomeDecision,
)
from controller.tracing.renderer import (
    TraceReportRenderer, _fmt_duration, _fmt_offset, _ascii_bar, _truncate,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def sample_events():
    """Build a realistic set of trace events."""
    base = datetime(2026, 3, 21, 14, 30, 0, tzinfo=timezone.utc)
    return [
        TraceEvent(
            id=1, trace_id="abc123", thread_id="th_001",
            event="orchestrator.task.received", component="orchestrator",
            timestamp=base, duration_ms=None, level="INFO",
            data={"task": "fix the login bug on mobile"},
        ),
        TraceEvent(
            id=2, trace_id="abc123", thread_id="th_001",
            event="classifier.skills.matched", component="classifier",
            timestamp=base + timedelta(milliseconds=180), duration_ms=180,
            level="INFO",
            data={
                "method": "semantic",
                "total_candidates": 12,
                "skills_matched": [
                    {"slug": "mobile_auth_sdk", "score": 0.87},
                    {"slug": "session_replay", "score": 0.72},
                ],
                "skills_rejected": [
                    {"slug": "ui_lint", "score": 0.45, "reason": "below_threshold"},
                ],
                "threshold": 0.5,
                "embedding_cached": False,
            },
        ),
        TraceEvent(
            id=3, trace_id="abc123", thread_id="th_001",
            event="injector.skills.formatted", component="injector",
            timestamp=base + timedelta(milliseconds=225), duration_ms=45,
            level="INFO",
            data={
                "skills": ["mobile_auth_sdk", "session_replay"],
                "total_size_bytes": 3993,
                "budget_bytes": 16384,
                "budget_utilization": 0.24,
            },
        ),
        TraceEvent(
            id=4, trace_id="abc123", thread_id="th_001",
            event="spawner.job.created", component="spawner",
            timestamp=base + timedelta(seconds=2, milliseconds=100), duration_ms=2100,
            level="INFO",
            data={"agent_type": "general", "image": "general:latest"},
        ),
        TraceEvent(
            id=5, trace_id="abc123", thread_id="th_001",
            event="agent.started", component="agent",
            timestamp=base + timedelta(seconds=4), duration_ms=None,
            level="INFO", data={},
        ),
        TraceEvent(
            id=6, trace_id="abc123", thread_id="th_001",
            event="agent.commit.created", component="agent",
            timestamp=base + timedelta(minutes=3), duration_ms=None,
            level="INFO",
            data={"message": "fix: session token refresh timezone offset"},
        ),
        TraceEvent(
            id=7, trace_id="abc123", thread_id="th_001",
            event="agent.completed", component="agent",
            timestamp=base + timedelta(minutes=4, seconds=28), duration_ms=None,
            level="INFO", data={},
        ),
        TraceEvent(
            id=8, trace_id="abc123", thread_id="th_001",
            event="monitor.result.received", component="monitor",
            timestamp=base + timedelta(minutes=4, seconds=32), duration_ms=None,
            level="INFO",
            data={
                "exit_code": 0,
                "commit_count": 1,
                "files_changed": 5,
                "insertions": 42,
                "deletions": 18,
                "pr_url": "https://github.com/org/repo/pull/247",
                "commit_message": "fix: session token refresh timezone offset",
                "stderr": "",
            },
        ),
    ]


@pytest.fixture
def sample_timeline(sample_events):
    """Build a TraceTimeline from sample events."""
    base = sample_events[0].timestamp
    end = sample_events[-1].timestamp
    return TraceTimeline(
        trace_id="abc123",
        thread_id="th_001",
        events=sample_events,
        started_at=base,
        ended_at=end,
        total_duration_ms=int((end - base).total_seconds() * 1000),
        component_summary={"orchestrator": 1, "classifier": 1, "injector": 1,
                           "spawner": 1, "agent": 3, "monitor": 1},
        error_count=0,
        task_text="fix the login bug on mobile",
        classification=ClassificationDecision(
            method="semantic",
            candidates_evaluated=12,
            matches=[
                {"slug": "mobile_auth_sdk", "score": 0.87},
                {"slug": "session_replay", "score": 0.72},
            ],
            rejected=[{"slug": "ui_lint", "score": 0.45, "reason": "below_threshold"}],
            threshold=0.5,
            embedding_cached=False,
            boost_applied=None,
        ),
        injection=InjectionDecision(
            skills_injected=["mobile_auth_sdk", "session_replay"],
            total_size_bytes=3993,
            budget_bytes=16384,
            budget_utilization=0.24,
        ),
        agent_type=AgentTypeDecision(
            agent_type="general",
            image="general:latest",
            reason=None,
        ),
        outcome=OutcomeDecision(
            exit_code=0,
            commit_count=1,
            files_changed=5,
            insertions=42,
            deletions=18,
            pr_url="https://github.com/org/repo/pull/247",
            commit_message="fix: session token refresh timezone offset",
            stderr_preview="",
            stderr_full_length=0,
        ),
    )


# ── Formatting utilities ─────────────────────────────────────────

class TestFmtDuration:
    def test_zero(self):
        assert _fmt_duration(0) == "0ms"

    def test_milliseconds(self):
        assert _fmt_duration(180) == "180ms"

    def test_seconds(self):
        assert _fmt_duration(2100) == "2.1s"

    def test_minutes_seconds(self):
        assert _fmt_duration(272000) == "4m 32s"

    def test_hours_minutes(self):
        assert _fmt_duration(3900000) == "1h 5m"

    def test_none(self):
        assert _fmt_duration(None) == "-"


class TestFmtOffset:
    def test_zero(self):
        assert _fmt_offset(0) == "00:00.000"

    def test_sub_second(self):
        assert _fmt_offset(180) == "00:00.180"

    def test_minutes(self):
        assert _fmt_offset(272000) == "04:32.000"


class TestAsciiBar:
    def test_full(self):
        assert _ascii_bar(1.0, width=10) == "[==========]"

    def test_empty(self):
        assert _ascii_bar(0.0, width=10) == "[          ]"

    def test_partial(self):
        assert _ascii_bar(0.5, width=10) == "[=====     ]"


class TestTruncate:
    def test_short_string(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length(self):
        assert _truncate("hello", 5) == "hello"

    def test_truncated(self):
        assert _truncate("hello world", 8) == "hello..."


# ── Hierarchical View ────────────────────────────────────────────

class TestHierarchicalView:
    def test_contains_trace_header(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "abc123" in report
        assert "SUCCESS" in report

    def test_contains_task_text(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "fix the login bug on mobile" in report

    def test_contains_classification_scores(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "mobile_auth_sdk" in report
        assert "0.87" in report

    def test_contains_skills_injected(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "SKILLS_INJECTED" in report
        assert "3.9KB" in report

    def test_contains_agent_spawned(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "AGENT_SPAWNED" in report
        assert "general" in report

    def test_contains_commit_message(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "session token refresh timezone offset" in report

    def test_contains_pr_url(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "pull/247" in report

    def test_contains_result(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "RESULT" in report
        assert "exit_code=0" in report


# ── Timeline View ────────────────────────────────────────────────

class TestTimelineView:
    def test_contains_table_headers(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_timeline(sample_timeline)
        assert "| Time |" in report
        assert "| Duration |" in report

    def test_contains_all_events(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_timeline(sample_timeline)
        assert "orchestrator.task.received" in report
        assert "classifier.skills.matched" in report
        assert "agent.completed" in report

    def test_contains_event_count(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_timeline(sample_timeline)
        assert "8 events" in report


# ── Decision View ────────────────────────────────────────────────

class TestDecisionView:
    def test_contains_skill_selection_section(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "## Skill Selection" in report
        assert "semantic" in report

    def test_contains_matched_skills_table(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "mobile_auth_sdk" in report
        assert "SELECTED" in report

    def test_contains_rejected_skills(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "ui_lint" in report
        assert "below_threshold" in report

    def test_contains_injection_budget(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "Budget utilization" in report

    def test_contains_outcome(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "## Outcome" in report
        assert "SUCCESS" in report

    def test_contains_timing_breakdown(self, sample_timeline):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "## Timing Breakdown" in report
        assert "% of Total" in report


# ── Error Cases ──────────────────────────────────────────────────

class TestErrorCases:
    def test_failed_trace(self, sample_timeline):
        """Trace with non-zero exit code shows FAILED status."""
        sample_timeline.outcome.exit_code = 1
        sample_timeline.outcome.stderr_preview = "Error: module not found"
        sample_timeline.outcome.stderr_full_length = 25
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "FAILED" in report
        assert "module not found" in report

    def test_empty_classification(self, sample_timeline):
        """Trace without classification data renders gracefully."""
        sample_timeline.classification = None
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        assert "No classification data available" in report

    def test_empty_outcome(self, sample_timeline):
        """Trace without outcome data renders gracefully."""
        sample_timeline.outcome = None
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        assert "UNKNOWN" in report

    def test_invalid_view(self, sample_timeline):
        """Unknown view name raises ValueError."""
        renderer = TraceReportRenderer()
        with pytest.raises(ValueError, match="Unknown view"):
            renderer.render(sample_timeline, view="nonexistent")


# ── Trace List ───────────────────────────────────────────────────

class TestTraceList:
    def test_empty_list(self):
        renderer = TraceReportRenderer()
        assert renderer.render_trace_list([]) == "No traces found."

    def test_list_with_traces(self):
        from controller.tracing.query import TraceSummary
        summaries = [
            TraceSummary(
                trace_id="abc123def456",
                thread_id="th_001",
                started_at=datetime(2026, 3, 21, 14, 30, tzinfo=timezone.utc),
                ended_at=datetime(2026, 3, 21, 14, 35, tzinfo=timezone.utc),
                total_duration_ms=300000,
                event_count=8,
                error_count=0,
                final_status="success",
                task_preview="fix the login bug",
            ),
        ]
        renderer = TraceReportRenderer()
        report = renderer.render_trace_list(summaries)
        assert "abc123def456" in report
        assert "SUCCESS" in report
        assert "1 traces shown" in report
```

### 10.2 Unit Tests: Query Engine (`test_trace_query.py`)

```python
"""Tests for TraceQueryEngine.

Uses an in-memory SQLite database with the trace_events schema.
"""
import json
import pytest
from datetime import datetime, timezone, timedelta

import aiosqlite

from controller.tracing.query import TraceQueryEngine


SCHEMA = """
CREATE TABLE trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    event TEXT NOT NULL,
    component TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    level TEXT NOT NULL DEFAULT 'INFO',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_trace_events_trace_id ON trace_events(trace_id);
CREATE INDEX idx_trace_events_thread_id ON trace_events(thread_id);
"""


@pytest.fixture
async def db_path(tmp_path):
    """Create a temporary SQLite database with schema."""
    path = str(tmp_path / "test_traces.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    return path


@pytest.fixture
async def seeded_db(db_path):
    """Insert sample trace events."""
    base = datetime(2026, 3, 21, 14, 30, 0, tzinfo=timezone.utc)
    events = [
        ("trace-1", "th-001", "orchestrator.task.received", "orchestrator",
         base.isoformat(), None, "INFO", json.dumps({"task": "fix login bug"})),
        ("trace-1", "th-001", "classifier.skills.matched", "classifier",
         (base + timedelta(milliseconds=180)).isoformat(), 180, "INFO",
         json.dumps({"method": "semantic", "total_candidates": 12,
                      "skills_matched": [{"slug": "auth", "score": 0.87}],
                      "skills_rejected": [], "threshold": 0.5,
                      "embedding_cached": False})),
        ("trace-1", "th-001", "monitor.result.received", "monitor",
         (base + timedelta(minutes=4)).isoformat(), None, "INFO",
         json.dumps({"exit_code": 0, "commit_count": 1, "stderr": ""})),
        # Second trace with error
        ("trace-2", "th-002", "orchestrator.task.received", "orchestrator",
         (base + timedelta(hours=1)).isoformat(), None, "INFO",
         json.dumps({"task": "add dark mode"})),
        ("trace-2", "th-002", "agent.error", "agent",
         (base + timedelta(hours=1, minutes=2)).isoformat(), None, "ERROR",
         json.dumps({"error": "timeout exceeded"})),
    ]

    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """INSERT INTO trace_events
               (trace_id, thread_id, event, component, timestamp, duration_ms, level, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            events,
        )
        await db.commit()
    return db_path


class TestGetTimeline:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing_trace(self, db_path):
        engine = TraceQueryEngine(db_path)
        result = await engine.get_timeline("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_timeline_with_events(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        timeline = await engine.get_timeline("trace-1")
        assert timeline is not None
        assert timeline.trace_id == "trace-1"
        assert len(timeline.events) == 3
        assert timeline.error_count == 0

    @pytest.mark.asyncio
    async def test_extracts_classification(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        timeline = await engine.get_timeline("trace-1")
        assert timeline.classification is not None
        assert timeline.classification.method == "semantic"
        assert timeline.classification.candidates_evaluated == 12

    @pytest.mark.asyncio
    async def test_extracts_task_text(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        timeline = await engine.get_timeline("trace-1")
        assert timeline.task_text == "fix login bug"

    @pytest.mark.asyncio
    async def test_error_trace(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        timeline = await engine.get_timeline("trace-2")
        assert timeline.error_count == 1


class TestListTraces:
    @pytest.mark.asyncio
    async def test_lists_all_traces(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        summaries = await engine.list_traces()
        assert len(summaries) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status_error(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        summaries = await engine.list_traces(status="error")
        assert len(summaries) == 1
        assert summaries[0].trace_id == "trace-2"

    @pytest.mark.asyncio
    async def test_filter_by_thread(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        summaries = await engine.list_traces(thread_id="th-001")
        assert len(summaries) == 1
        assert summaries[0].thread_id == "th-001"

    @pytest.mark.asyncio
    async def test_limit(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        summaries = await engine.list_traces(limit=1)
        assert len(summaries) == 1

    @pytest.mark.asyncio
    async def test_task_preview(self, seeded_db):
        engine = TraceQueryEngine(seeded_db)
        summaries = await engine.list_traces(thread_id="th-001")
        assert summaries[0].task_preview == "fix login bug"
```

### 10.3 Integration Tests: API (`test_trace_api.py`)

```python
"""Integration tests for trace API endpoints.

Full flow: insert events -> query API -> verify response.
"""
import json
import pytest

import aiosqlite
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from controller.tracing.api import router as trace_router, get_trace_engine
from controller.tracing.query import TraceQueryEngine


SCHEMA = """..."""  # Same as test_trace_query.py


@pytest.fixture
async def app_with_traces(tmp_path):
    """Create a FastAPI app with trace router and seeded database."""
    db_path = str(tmp_path / "test_traces.db")

    # Create schema and seed data
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute(
            """INSERT INTO trace_events
               (trace_id, thread_id, event, component, timestamp, level, data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("trace-1", "th-001", "orchestrator.task.received", "orchestrator",
             "2026-03-21T14:30:00+00:00", "INFO",
             json.dumps({"task": "fix login bug"})),
        )
        await db.commit()

    engine = TraceQueryEngine(db_path)
    app = FastAPI()
    app.dependency_overrides[get_trace_engine] = lambda: engine
    app.include_router(trace_router)
    return app


class TestTraceAPI:
    @pytest.mark.asyncio
    async def test_list_traces(self, app_with_traces):
        transport = ASGITransport(app=app_with_traces)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/traces")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["traces"]) == 1

    @pytest.mark.asyncio
    async def test_get_trace(self, app_with_traces):
        transport = ASGITransport(app=app_with_traces)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/traces/trace-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["trace_id"] == "trace-1"

    @pytest.mark.asyncio
    async def test_get_trace_not_found(self, app_with_traces):
        transport = ASGITransport(app=app_with_traces)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/traces/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_report_hierarchical(self, app_with_traces):
        transport = ASGITransport(app=app_with_traces)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/traces/trace-1/report?view=hierarchical")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
            assert "trace-1" in resp.text

    @pytest.mark.asyncio
    async def test_get_report_invalid_view(self, app_with_traces):
        transport = ASGITransport(app=app_with_traces)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/traces/trace-1/report?view=invalid")
            assert resp.status_code == 422  # Validation error from Query pattern
```

### 10.4 CLI Tests (`test_trace_cli.py`)

```python
"""Tests for trace CLI commands."""
import pytest
from unittest.mock import AsyncMock, patch
from argparse import Namespace

from controller.tracing.cli import build_parser, _parse_since


class TestParser:
    def test_report_command(self):
        parser = build_parser()
        args = parser.parse_args(["report", "abc123"])
        assert args.command == "report"
        assert args.trace_id == "abc123"
        assert args.view == "hierarchical"

    def test_report_with_view(self):
        parser = build_parser()
        args = parser.parse_args(["report", "abc123", "--view", "timeline"])
        assert args.view == "timeline"

    def test_report_with_output(self):
        parser = build_parser()
        args = parser.parse_args(["report", "abc123", "-o", "report.md"])
        assert args.output == "report.md"

    def test_list_command(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--limit", "5", "--status", "error"])
        assert args.command == "list"
        assert args.limit == 5
        assert args.status == "error"

    def test_search_command(self):
        parser = build_parser()
        args = parser.parse_args(["search", "--event", "agent.error", "--since", "24h"])
        assert args.command == "search"
        assert args.event == "agent.error"
        assert args.since == "24h"


class TestParseSince:
    def test_hours(self):
        result = _parse_since("24h")
        # Should be approximately 24 hours ago
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 23.9 < diff.total_seconds() / 3600 < 24.1

    def test_days(self):
        result = _parse_since("7d")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 6.9 < diff.total_seconds() / 86400 < 7.1

    def test_minutes(self):
        result = _parse_since("30m")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 29.5 < diff.total_seconds() / 60 < 30.5

    def test_invalid_suffix(self):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_since("24x")
```

### 10.5 Snapshot Tests

Golden file approach for catching unintentional rendering changes:

```python
# In test_trace_renderer.py, add:

class TestSnapshotReports:
    """Golden file snapshot tests.

    To update golden files, run:
        pytest --update-snapshots
    """
    GOLDEN_DIR = Path(__file__).parent / "fixtures" / "trace_golden"

    def _assert_snapshot(self, content: str, name: str, update: bool = False):
        golden_path = self.GOLDEN_DIR / f"{name}.md"
        if update or not golden_path.exists():
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(content, encoding="utf-8")
            pytest.skip(f"Golden file created: {golden_path}")
        expected = golden_path.read_text(encoding="utf-8")
        # Strip timestamps since they change
        import re
        content_clean = re.sub(r"Generated at \S+", "Generated at TIMESTAMP", content)
        expected_clean = re.sub(r"Generated at \S+", "Generated at TIMESTAMP", expected)
        assert content_clean == expected_clean, (
            f"Report does not match golden file {golden_path}. "
            f"Run with --update-snapshots to update."
        )

    def test_hierarchical_snapshot(self, sample_timeline, request):
        renderer = TraceReportRenderer()
        report = renderer.render_hierarchical(sample_timeline)
        update = request.config.getoption("--update-snapshots", default=False)
        self._assert_snapshot(report, "hierarchical_success", update)

    def test_timeline_snapshot(self, sample_timeline, request):
        renderer = TraceReportRenderer()
        report = renderer.render_timeline(sample_timeline)
        update = request.config.getoption("--update-snapshots", default=False)
        self._assert_snapshot(report, "timeline_success", update)

    def test_decision_snapshot(self, sample_timeline, request):
        renderer = TraceReportRenderer()
        report = renderer.render_decision(sample_timeline)
        update = request.config.getoption("--update-snapshots", default=False)
        self._assert_snapshot(report, "decision_success", update)
```

Add to `conftest.py`:
```python
def pytest_addoption(parser):
    parser.addoption("--update-snapshots", action="store_true", default=False)
```

---

## 11. Implementation Order

| Step | Task | Estimated Effort | Dependencies |
|------|------|-----------------|--------------|
| 1 | Create `tracing/` package with `__init__.py` | 5 min | None |
| 2 | Implement `query.py` (TraceQueryEngine + dataclasses) | 3-4 hours | Phase 1 schema |
| 3 | Implement `renderer.py` (TraceReportRenderer) | 4-5 hours | Step 2 |
| 4 | Write `test_trace_renderer.py` (unit tests) | 2-3 hours | Step 3 |
| 5 | Write `test_trace_query.py` (unit tests with SQLite) | 2 hours | Step 2 |
| 6 | Implement `cli.py` + `__main__.py` | 2 hours | Steps 2, 3 |
| 7 | Write `test_trace_cli.py` | 1 hour | Step 6 |
| 8 | Implement `api.py` (FastAPI router) | 2 hours | Steps 2, 3 |
| 9 | Write `test_trace_api.py` (integration tests) | 2 hours | Step 8 |
| 10 | Implement `persistence.py` + auto-report hook | 2 hours | Steps 2, 3 |
| 11 | Wire into `main.py` + add config settings | 1 hour | Steps 8, 10 |
| 12 | Create golden snapshot files | 1 hour | Step 4 |
| 13 | Manual testing with real trace data | 2 hours | All above |

**Total estimated effort: 3-4 days**

---

## 12. ADR: Programmatic Markdown over Jinja2 Templates

### Status
Proposed

### Context
The report generator needs to produce three Markdown views from trace data. Options are Jinja2 templates or programmatic string building. The team is small (1-2 engineers) and the output is Markdown, not HTML.

### Decision
Use programmatic string building with f-strings and helper functions. Each render method is a pure function taking a `TraceTimeline` and returning a `str`.

### Consequences
- **Easier:** Testing (pure functions), debugging (standard stack traces), dependency management (zero new deps), code navigation (everything in one file).
- **Harder:** Adding HTML/PDF output formats later (would need to add Jinja2 or a different renderer at that point), non-technical contributors editing report templates.
- **Reversibility:** High. The `TraceReportRenderer` class can be replaced with a `Jinja2TraceRenderer` that consumes the same `TraceTimeline` dataclass. The data layer does not change.
