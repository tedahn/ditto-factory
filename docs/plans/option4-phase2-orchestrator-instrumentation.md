# Option 4 (B Enhanced) -- Phase 2: Orchestrator-Side Instrumentation

## Status
Proposed

## Context

Phase 1 provides the data model: `TraceSpan`, `TraceStore`, `TraceContext` with `trace_span()` context manager, and event types. Phase 2 instruments every step of the orchestrator flow so that each task execution produces a complete trace tree from webhook receipt through job completion.

The orchestrator (`controller/src/controller/orchestrator.py`) is the single most important instrumentation target because it makes every decision: which skills to inject, which agent image to use, what goes into the Redis payload, and when to retry.

### Assumed Phase 1 API

```python
# controller/src/controller/tracing/models.py
@dataclass
class TraceSpan:
    span_id: str                    # uuid hex, unique per span
    trace_id: str                   # uuid hex, shared across entire task lifecycle
    parent_span_id: str | None      # links to parent operation
    operation_name: str             # e.g. "task.classify"
    event_type: EventType           # enum: TASK_RECEIVED, TASK_CLASSIFIED, etc.
    status: SpanStatus              # OK, ERROR, TIMEOUT
    start_time: datetime
    end_time: datetime | None
    duration_ms: float | None
    input_summary: dict             # what went into this step
    output_summary: dict            # what came out
    reasoning: str | None           # why this decision was made
    metadata: dict                  # arbitrary k/v (token counts, scores, etc.)
    error_message: str | None

class EventType(str, Enum):
    TASK_RECEIVED = "task.received"
    TASK_CLASSIFIED = "task.classified"
    SKILLS_INJECTED = "skills.injected"
    AGENT_RESOLVED = "agent.resolved"
    REDIS_PUSHED = "redis.pushed"
    AGENT_SPAWNED = "agent.spawned"
    AGENT_COMPLETED = "agent.completed"
    SAFETY_PROCESSED = "safety.processed"
    PERFORMANCE_RECORDED = "performance.recorded"

# controller/src/controller/tracing/context.py
class TraceContext:
    """Uses contextvars to propagate trace_id + current span_id."""

    @staticmethod
    @contextmanager
    def trace_span(
        operation_name: str,
        event_type: EventType,
        parent_span_id: str | None = None,
        trace_id: str | None = None,
        input_summary: dict | None = None,
    ) -> Generator[TraceSpan, None, None]:
        """Creates span, sets it as current, yields, finalizes on exit."""
        ...

# controller/src/controller/tracing/store.py
class TraceStore:
    async def insert_span(self, span: TraceSpan) -> None: ...
    async def insert_spans_batch(self, spans: list[TraceSpan]) -> None: ...
    async def get_trace(self, trace_id: str) -> list[TraceSpan]: ...
```

---

## 1. Instrumentation Points Map

### 1.1 Task Received (`TASK_RECEIVED`)

**Location**: `Orchestrator.handle_task()`, first line after method entry.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"thread_id": ..., "source": ..., "repo": "owner/name", "task_length": len(task), "has_images": bool, "conversation_depth": len(conversation)}` |
| output_summary | `{"action": "spawn" \| "queue" \| "lock_failed", "thread_existed": bool}` |
| reasoning | `None` (no decision yet) |
| metadata | `{"source_ref_type": type, "is_retry": bool}` |

**Example span**:
```json
{
  "span_id": "a1b2c3d4",
  "trace_id": "f0e1d2c3",
  "parent_span_id": null,
  "operation_name": "task.receive",
  "event_type": "task.received",
  "status": "OK",
  "start_time": "2026-03-21T10:00:00Z",
  "end_time": "2026-03-21T10:00:00.002Z",
  "duration_ms": 2.1,
  "input_summary": {
    "thread_id": "slack-C123-1234567890",
    "source": "slack",
    "repo": "acme/webapp",
    "task_length": 342,
    "has_images": false,
    "conversation_depth": 0
  },
  "output_summary": {
    "action": "spawn",
    "thread_existed": false
  },
  "metadata": {
    "source_ref_type": "slack_message",
    "is_retry": false
  }
}
```

### 1.2 Task Classification (`TASK_CLASSIFIED`)

**Location**: Inside `Orchestrator._spawn_job()`, wrapping the `self._classifier.classify()` call.

This is the **most critical** instrumentation point. Currently there is zero record of why skills were selected.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"task_text": task[:200], "language_hint": [...], "domain_hint": [...], "embedding_provider": "voyage-3" \| None}` |
| output_summary | `{"matched_skills": [{"slug": ..., "score": ...}], "agent_type": ..., "method": "semantic" \| "tag_fallback", "skill_count": int}` |
| reasoning | `"Semantic search returned 5 candidates above threshold 0.5; tag fallback not needed; performance boost applied to 2 skills"` |
| metadata | `{"threshold_applied": 0.5, "candidates_before_threshold": int, "candidates_after_threshold": int, "boost_applied": {"skill-slug": 0.05, ...}, "embedding_cached": bool, "fallback_triggered": bool, "embedding_dim": 1024}` |

**Example span**:
```json
{
  "span_id": "b2c3d4e5",
  "trace_id": "f0e1d2c3",
  "parent_span_id": "a1b2c3d4",
  "operation_name": "task.classify",
  "event_type": "task.classified",
  "status": "OK",
  "start_time": "2026-03-21T10:00:00.003Z",
  "end_time": "2026-03-21T10:00:00.450Z",
  "duration_ms": 447.2,
  "input_summary": {
    "task_text": "Fix the CSS grid layout on the dashboard page...",
    "language_hint": ["typescript"],
    "domain_hint": ["frontend"],
    "embedding_provider": "voyage-3"
  },
  "output_summary": {
    "matched_skills": [
      {"slug": "frontend-css-review", "score": 0.87},
      {"slug": "react-best-practices", "score": 0.72}
    ],
    "agent_type": "frontend",
    "method": "semantic",
    "skill_count": 2
  },
  "reasoning": "Semantic search: 12 candidates, 5 above threshold 0.5, budget trimmed to 2. Boost +0.03 applied to frontend-css-review (92% success rate over 47 uses).",
  "metadata": {
    "threshold_applied": 0.5,
    "candidates_before_threshold": 12,
    "candidates_after_threshold": 5,
    "candidates_after_budget": 2,
    "boost_applied": {"frontend-css-review": 0.03},
    "embedding_cached": true,
    "fallback_triggered": false,
    "embedding_dim": 1024
  }
}
```

### 1.3 Skill Injection (`SKILLS_INJECTED`)

**Location**: Inside `_spawn_job()`, wrapping `self._injector.format_for_redis()` and `validate_budget()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"skill_count": int, "total_chars_before_budget": int, "budget_limit": 16000}` |
| output_summary | `{"accepted_skills": [...slugs], "dropped_skills": [...slugs], "total_chars_after_budget": int, "payload_size_bytes": int}` |
| reasoning | `"2 of 4 skills dropped due to 16K char budget"` or `"All 3 skills fit within budget"` |
| metadata | `{"per_skill_chars": {"slug": char_count, ...}}` |

**Example span**:
```json
{
  "span_id": "c3d4e5f6",
  "trace_id": "f0e1d2c3",
  "parent_span_id": "a1b2c3d4",
  "operation_name": "skills.inject",
  "event_type": "skills.injected",
  "status": "OK",
  "duration_ms": 1.2,
  "input_summary": {
    "skill_count": 3,
    "total_chars_before_budget": 14200,
    "budget_limit": 16000
  },
  "output_summary": {
    "accepted_skills": ["frontend-css-review", "react-best-practices"],
    "dropped_skills": ["general-code-quality"],
    "total_chars_after_budget": 9800,
    "payload_size_bytes": 10240
  },
  "metadata": {
    "per_skill_chars": {
      "frontend-css-review": 5200,
      "react-best-practices": 4600,
      "general-code-quality": 6800
    }
  }
}
```

### 1.4 Agent Resolution (`AGENT_RESOLVED`)

**Location**: Inside `_spawn_job()`, wrapping `self._resolver.resolve()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"required_capabilities": [...], "skill_domains": [...], "default_image": "..."}` |
| output_summary | `{"agent_type": "frontend", "image": "ghcr.io/...:frontend-v1.2", "match_method": "capability_subset"}` |
| reasoning | `"Skills require ['browser', 'css-tools']; agent type 'frontend' covers all with 1 extra capability"` |
| metadata | `{"candidates_evaluated": int, "best_extra_caps": int}` |

### 1.5 Redis Push (`REDIS_PUSHED`)

**Location**: Inside `_spawn_job()`, wrapping `self._redis.push_task()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"thread_id": ..., "payload_keys": ["task", "system_prompt", "repo_url", "branch", "skills", "trace_id", "parent_span_id"]}` |
| output_summary | `{"redis_key": "task:{thread_id}", "payload_size_bytes": int, "ttl_seconds": 3600}` |
| reasoning | `None` |
| metadata | `{"skill_count_in_payload": int, "branch": "df/a1b2c3d4/e5f6a7b8", "trace_context_propagated": true}` |

### 1.6 K8s Job Spawn (`AGENT_SPAWNED`)

**Location**: Inside `_spawn_job()`, wrapping `self._spawner.spawn()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"thread_id": ..., "agent_image": "...", "namespace": "default"}` |
| output_summary | `{"job_name": "df-a1b2c3d4-1234567890", "job_id": "..."}` |
| reasoning | `None` |
| metadata | `{"resource_requests": {"cpu": "500m", "memory": "1Gi"}, "extra_env_keys": [...]}` |

### 1.7 Job Monitoring (`AGENT_COMPLETED`)

**Location**: Inside `Orchestrator.handle_job_completion()`, wrapping `self._monitor.wait_for_result()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"thread_id": ..., "timeout": 1800, "poll_interval": 5.0}` |
| output_summary | `{"exit_code": 0, "commit_count": 3, "has_pr": true, "branch": "df/..."}` |
| reasoning | `None` |
| metadata | `{"poll_count": 12, "wait_duration_ms": 60000, "stderr_length": 0}` |

### 1.8 Safety Pipeline (`SAFETY_PROCESSED`)

**Location**: Inside `handle_job_completion()`, wrapping `pipeline.process()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"exit_code": 0, "commit_count": 3, "has_pr": false, "auto_open_pr": true}` |
| output_summary | `{"pr_created": true, "pr_url": "...", "retried": false, "reported": true}` |
| reasoning | `"Commits present, no PR, auto_open_pr enabled -> created PR"` or `"Zero commits, exit_code=0, retry 1/3 triggered"` |
| metadata | `{"retry_count": 0, "queued_messages_drained": 0}` |

### 1.9 Performance Recording (`PERFORMANCE_RECORDED`)

**Location**: Inside `handle_job_completion()`, wrapping `self._tracker.record_outcome()`.

**Captures**:
| Field | Value |
|-------|-------|
| input_summary | `{"thread_id": ..., "job_id": ..., "exit_code": 0, "commit_count": 3}` |
| output_summary | `{"skills_updated": ["frontend-css-review", "react-best-practices"]}` |
| reasoning | `None` |
| metadata | `{"pr_created": true}` |

---

## 2. Cross-Process Trace Propagation

### 2.1 Problem

The orchestrator runs in the controller pod. The agent runs in a separate K8s Job pod. They communicate via Redis. The trace must span both processes under one `trace_id`.

### 2.2 Solution: Embed Trace Context in Redis Payload

Add `trace_id` and `parent_span_id` to the existing Redis task JSON. The agent pod reads these on startup and creates child spans under the same trace.

**Current Redis payload** (from `_spawn_job()`):
```python
await self._redis.push_task(thread_id, {
    "task": task_request.task,
    "system_prompt": system_prompt,
    "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
    "branch": branch,
    "skills": skills_payload,
})
```

**New Redis payload**:
```python
await self._redis.push_task(thread_id, {
    "task": task_request.task,
    "system_prompt": system_prompt,
    "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
    "branch": branch,
    "skills": skills_payload,
    # Trace context propagation (Phase 2)
    "trace_id": current_trace_id,
    "parent_span_id": current_span_id,
})
```

### 2.3 Agent-Side Reading (Phase 3 scope, documented here for interface contract)

The agent entrypoint reads `trace_id` and `parent_span_id` from the task JSON:

```python
task_data = await redis_state.get_task(thread_id)
trace_id = task_data.get("trace_id")       # May be None (pre-Phase-2 tasks)
parent_span_id = task_data.get("parent_span_id")
```

If present, the agent creates all its spans under this `trace_id` with `parent_span_id` pointing to the controller's `AGENT_SPAWNED` span. If absent (backward compat), the agent generates its own `trace_id`.

### 2.4 Format

Both fields are UUID hex strings (32 chars, no dashes), matching OTel's 128-bit trace ID and 64-bit span ID conventions but stored as full 128-bit UUIDs for simplicity:

```
trace_id:       "f0e1d2c3b4a596877869504132104050"
parent_span_id: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
```

---

## 3. Classification Decision Logging (Deep Dive)

This is the most important instrumentation point. The classifier currently returns a `ClassificationResult` with skills but discards all intermediate decision data.

### 3.1 What to Capture

| Data Point | Source | Why It Matters |
|-----------|--------|----------------|
| All candidate skills with scores | `search_by_embedding()` returns `list[ScoredSkill]` | Debug "why was skill X not selected?" |
| Pre-boost scores vs post-boost scores | `compute_boost()` in classifier loop | Understand performance feedback loop impact |
| Threshold applied | `settings.skill_min_similarity` | Tune threshold over time |
| Method used | Whether embeddings worked or tag fallback fired | Monitor embedding service reliability |
| Embedding cache hit | `self._cache.get(task)` | Monitor cache effectiveness |
| Budget trimming | `_enforce_budget()` output vs input | Know if valuable skills were dropped |
| Fallback reason | Exception from embedding provider | Debug outages |

### 3.2 Required Change to TaskClassifier

The classifier needs to return richer data. Two options:

**Option A: Enrich ClassificationResult** (recommended)

Add optional diagnostic fields to `ClassificationResult`:

```python
# controller/src/controller/skills/models.py

@dataclass
class ClassificationDiagnostics:
    """Diagnostic data from the classification process. Not used for execution."""
    all_candidates: list[ScoredSkill]          # Full list before threshold
    method: str                                 # "semantic" | "tag_fallback"
    threshold_applied: float
    boosts_applied: dict[str, float]            # skill_id -> boost delta
    embedding_cached: bool
    fallback_reason: str | None                 # Exception message if semantic failed
    budget_input_chars: int                     # Total chars before budget trim
    budget_output_chars: int                    # Total chars after budget trim
    dropped_skills: list[str]                   # Slugs dropped by budget

@dataclass
class ClassificationResult:
    skills: list[Skill]
    agent_type: str = "general"
    task_embedding: list[float] | None = None
    diagnostics: ClassificationDiagnostics | None = None  # NEW
```

**Option B: Separate diagnostic method** -- rejected because it would require re-running classification.

### 3.3 Classifier Code Changes

**File**: `controller/src/controller/skills/classifier.py`

The `classify()` method needs to accumulate diagnostics as it runs. Here is the before/after for the core logic:

**BEFORE** (current `classify()` method, lines ~45-95):
```python
async def classify(
    self,
    task: str,
    language: list[str] | None = None,
    domain: list[str] | None = None,
) -> ClassificationResult:
    matched_skills: list[Skill] = []
    task_embedding: list[float] | None = None
    filters = SkillFilters(language=language, domain=domain)

    # Phase 2: Try semantic search first
    if self._embedder:
        try:
            task_embedding = self._cache.get(task)
            if task_embedding is None:
                task_embedding = await self._embedder.embed(task)
                self._cache.put(task, task_embedding)

            scored = await self._registry.search_by_embedding(
                task_embedding=task_embedding,
                filters=filters,
                limit=20,
            )
            if self._tracker:
                for scored_skill in scored:
                    scored_skill.score = await self._tracker.compute_boost(
                        scored_skill.skill.id, scored_skill.score
                    )

            min_sim = getattr(self._settings, "skill_min_similarity", 0.5)
            matched_skills = [s.skill for s in scored if s.score >= min_sim]
        except EmbeddingError:
            logger.warning("Embedding failed, falling back to tag search")
            matched_skills = await self._registry.search_by_tags(
                language=language, domain=domain
            )
    else:
        matched_skills = await self._registry.search_by_tags(
            language=language, domain=domain
        )
    # ... rest of method
```

**AFTER** (with diagnostics collection):
```python
async def classify(
    self,
    task: str,
    language: list[str] | None = None,
    domain: list[str] | None = None,
) -> ClassificationResult:
    matched_skills: list[Skill] = []
    task_embedding: list[float] | None = None
    filters = SkillFilters(language=language, domain=domain)

    # Diagnostics accumulator
    method = "tag_fallback"
    all_candidates: list[ScoredSkill] = []
    boosts_applied: dict[str, float] = {}
    embedding_cached = False
    fallback_reason: str | None = None
    threshold = getattr(self._settings, "skill_min_similarity", 0.5)

    # Phase 2: Try semantic search first
    if self._embedder:
        try:
            cached = self._cache.get(task)
            embedding_cached = cached is not None
            if cached is not None:
                task_embedding = cached
            else:
                task_embedding = await self._embedder.embed(task)
                self._cache.put(task, task_embedding)

            scored = await self._registry.search_by_embedding(
                task_embedding=task_embedding,
                filters=filters,
                limit=20,
            )
            all_candidates = [
                ScoredSkill(skill=s.skill, score=s.score) for s in scored
            ]  # snapshot pre-boost scores

            if self._tracker:
                for scored_skill in scored:
                    pre_boost = scored_skill.score
                    scored_skill.score = await self._tracker.compute_boost(
                        scored_skill.skill.id, scored_skill.score
                    )
                    if scored_skill.score != pre_boost:
                        boosts_applied[scored_skill.skill.id] = round(
                            scored_skill.score - pre_boost, 4
                        )

            matched_skills = [s.skill for s in scored if s.score >= threshold]
            method = "semantic"
        except EmbeddingError as exc:
            logger.warning("Embedding failed, falling back to tag search")
            fallback_reason = str(exc)
            matched_skills = await self._registry.search_by_tags(
                language=language, domain=domain
            )
            method = "tag_fallback"
    else:
        matched_skills = await self._registry.search_by_tags(
            language=language, domain=domain
        )
        method = "tag_fallback"

    # Merge defaults and enforce budget
    defaults = await self._registry.get_defaults(filters)
    merged = self._merge_and_deduplicate(defaults, matched_skills)
    budget_limit = getattr(self._settings, "skill_budget_chars", 16000)
    budget_input_chars = sum(len(s.content) for s in merged)
    budgeted = self._enforce_budget(merged, budget_limit)
    budget_output_chars = sum(len(s.content) for s in budgeted)
    dropped = [s.slug for s in merged if s not in budgeted]

    agent_type = self._resolve_agent_type_from_skills(budgeted)

    diagnostics = ClassificationDiagnostics(
        all_candidates=all_candidates,
        method=method,
        threshold_applied=threshold,
        boosts_applied=boosts_applied,
        embedding_cached=embedding_cached,
        fallback_reason=fallback_reason,
        budget_input_chars=budget_input_chars,
        budget_output_chars=budget_output_chars,
        dropped_skills=dropped,
    )

    return ClassificationResult(
        skills=budgeted,
        agent_type=agent_type,
        task_embedding=task_embedding,
        diagnostics=diagnostics,
    )
```

---

## 4. Decorator Pattern

### 4.1 `@traced` Decorator

A decorator that automatically creates a span, captures timing, handles errors, and persists the span.

**File**: `controller/src/controller/tracing/decorator.py`

```python
"""Tracing decorator for automatic span creation."""

from __future__ import annotations

import functools
import logging
import time
import uuid
from typing import Any, Callable

from controller.tracing.context import TraceContext
from controller.tracing.models import EventType, SpanStatus, TraceSpan
from controller.tracing.store import TraceStore

logger = logging.getLogger(__name__)

# Module-level store reference, set during app startup
_trace_store: TraceStore | None = None


def set_trace_store(store: TraceStore) -> None:
    """Called once at app startup to wire the store."""
    global _trace_store
    _trace_store = store


def traced(
    operation_name: str,
    event_type: EventType,
    capture_input: Callable[..., dict] | None = None,
    capture_output: Callable[[Any], dict] | None = None,
) -> Callable:
    """Decorator that wraps a function in a trace span.

    Args:
        operation_name: Name for the span (e.g. "task.classify").
        event_type: The EventType enum value.
        capture_input: Optional callable that extracts input_summary from *args/**kwargs.
        capture_output: Optional callable that extracts output_summary from the return value.

    Works with both sync and async functions.
    """

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                span_id = uuid.uuid4().hex
                trace_id = TraceContext.get_trace_id() or uuid.uuid4().hex
                parent_span_id = TraceContext.get_current_span_id()

                input_summary = {}
                if capture_input:
                    try:
                        input_summary = capture_input(*args, **kwargs)
                    except Exception:
                        logger.debug("Failed to capture input for %s", operation_name)

                span = TraceSpan(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                    operation_name=operation_name,
                    event_type=event_type,
                    status=SpanStatus.OK,
                    start_time=datetime.now(timezone.utc),
                    end_time=None,
                    duration_ms=None,
                    input_summary=input_summary,
                    output_summary={},
                    reasoning=None,
                    metadata={},
                    error_message=None,
                )

                start = time.monotonic()
                token = TraceContext.set_current_span_id(span_id)

                try:
                    result = await func(*args, **kwargs)

                    if capture_output:
                        try:
                            span.output_summary = capture_output(result)
                        except Exception:
                            logger.debug("Failed to capture output for %s", operation_name)

                    return result

                except Exception as exc:
                    span.status = SpanStatus.ERROR
                    span.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
                    raise

                finally:
                    span.end_time = datetime.now(timezone.utc)
                    span.duration_ms = (time.monotonic() - start) * 1000
                    TraceContext.reset_current_span_id(token)

                    if _trace_store:
                        try:
                            await _trace_store.insert_span(span)
                        except Exception:
                            logger.warning(
                                "Failed to persist trace span %s",
                                span_id,
                                exc_info=True,
                            )

            return async_wrapper

        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Same pattern but synchronous; queue span for async persistence
                span_id = uuid.uuid4().hex
                trace_id = TraceContext.get_trace_id() or uuid.uuid4().hex
                parent_span_id = TraceContext.get_current_span_id()

                input_summary = {}
                if capture_input:
                    try:
                        input_summary = capture_input(*args, **kwargs)
                    except Exception:
                        pass

                span = TraceSpan(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                    operation_name=operation_name,
                    event_type=event_type,
                    status=SpanStatus.OK,
                    start_time=datetime.now(timezone.utc),
                    end_time=None,
                    duration_ms=None,
                    input_summary=input_summary,
                    output_summary={},
                    reasoning=None,
                    metadata={},
                    error_message=None,
                )

                start = time.monotonic()
                token = TraceContext.set_current_span_id(span_id)

                try:
                    result = func(*args, **kwargs)
                    if capture_output:
                        try:
                            span.output_summary = capture_output(result)
                        except Exception:
                            pass
                    return result
                except Exception as exc:
                    span.status = SpanStatus.ERROR
                    span.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
                    raise
                finally:
                    span.end_time = datetime.now(timezone.utc)
                    span.duration_ms = (time.monotonic() - start) * 1000
                    TraceContext.reset_current_span_id(token)
                    # For sync functions, buffer span for later async flush
                    TraceContext.buffer_span(span)

            return sync_wrapper

    return decorator


import asyncio
from datetime import datetime, timezone
```

### 4.2 Interaction with `TraceContext`

The `TraceContext` uses `contextvars` to maintain a stack:

```python
# controller/src/controller/tracing/context.py
import contextvars
from contextvars import Token

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_span_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
_span_buffer: contextvars.ContextVar[list] = contextvars.ContextVar("span_buffer", default=[])


class TraceContext:
    @staticmethod
    def get_trace_id() -> str | None:
        return _trace_id_var.get()

    @staticmethod
    def set_trace_id(trace_id: str) -> Token:
        return _trace_id_var.set(trace_id)

    @staticmethod
    def get_current_span_id() -> str | None:
        return _span_id_var.get()

    @staticmethod
    def set_current_span_id(span_id: str) -> Token:
        return _span_id_var.set(span_id)

    @staticmethod
    def reset_current_span_id(token: Token) -> None:
        _span_id_var.reset(token)

    @staticmethod
    def buffer_span(span) -> None:
        buf = _span_buffer.get()
        buf.append(span)

    @staticmethod
    def flush_buffer() -> list:
        buf = _span_buffer.get()
        _span_buffer.set([])
        return buf
```

### 4.3 Usage Examples

The decorator is best for leaf functions. For the orchestrator methods where we need to set `reasoning` and `metadata` dynamically, we use the context manager directly (see Section 5).

```python
# Example: on JobSpawner.spawn() -- a leaf function
@traced(
    "agent.spawn",
    EventType.AGENT_SPAWNED,
    capture_input=lambda self, thread_id, **kw: {"thread_id": thread_id, "agent_image": kw.get("agent_image")},
    capture_output=lambda job_name: {"job_name": job_name},
)
def spawn(self, thread_id, github_token, redis_url, agent_image=None, extra_env=None):
    ...
```

---

## 5. Code Changes Per File

### 5.1 `controller/src/controller/orchestrator.py`

**Imports to add**:
```python
import time
import uuid as uuid_mod
from controller.tracing.context import TraceContext
from controller.tracing.models import EventType, SpanStatus, TraceSpan
from controller.tracing.store import TraceStore
```

**Constructor change** -- add `trace_store` parameter:

```python
# BEFORE
class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        state: StateBackend,
        redis_state: RedisState,
        registry: IntegrationRegistry,
        spawner: JobSpawner,
        monitor: JobMonitor,
        github_client=None,
        classifier: TaskClassifier | None = None,
        injector: SkillInjector | None = None,
        resolver: AgentTypeResolver | None = None,
        tracker: PerformanceTracker | None = None,
    ):
```

```python
# AFTER
class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        state: StateBackend,
        redis_state: RedisState,
        registry: IntegrationRegistry,
        spawner: JobSpawner,
        monitor: JobMonitor,
        github_client=None,
        classifier: TaskClassifier | None = None,
        injector: SkillInjector | None = None,
        resolver: AgentTypeResolver | None = None,
        tracker: PerformanceTracker | None = None,
        trace_store: TraceStore | None = None,
    ):
        # ... existing assignments ...
        self._trace_store = trace_store
```

**`handle_task()` method** -- wrap with root span:

```python
# BEFORE
async def handle_task(self, task_request: TaskRequest) -> None:
    thread_id = task_request.thread_id
    logger.info("Handling task for thread %s from %s", thread_id, task_request.source)

    # RESOLVE: Get or create thread
    thread = await self._state.get_thread(thread_id)
    # ... rest of method
```

```python
# AFTER
async def handle_task(self, task_request: TaskRequest) -> None:
    thread_id = task_request.thread_id
    logger.info("Handling task for thread %s from %s", thread_id, task_request.source)

    # Initialize trace context for this task
    trace_id = uuid_mod.uuid4().hex
    trace_id_token = TraceContext.set_trace_id(trace_id)
    root_span_id = uuid_mod.uuid4().hex
    span_token = TraceContext.set_current_span_id(root_span_id)

    root_span = TraceSpan(
        span_id=root_span_id,
        trace_id=trace_id,
        parent_span_id=None,
        operation_name="task.receive",
        event_type=EventType.TASK_RECEIVED,
        status=SpanStatus.OK,
        start_time=datetime.now(timezone.utc),
        end_time=None,
        duration_ms=None,
        input_summary={
            "thread_id": thread_id,
            "source": task_request.source,
            "repo": f"{task_request.repo_owner}/{task_request.repo_name}",
            "task_length": len(task_request.task),
            "has_images": bool(task_request.images),
            "conversation_depth": len(task_request.conversation),
        },
        output_summary={},
        reasoning=None,
        metadata={"source_ref_type": task_request.source},
        error_message=None,
    )
    start = time.monotonic()

    try:
        # RESOLVE: Get or create thread
        thread = await self._state.get_thread(thread_id)
        if thread is None:
            thread = Thread(
                id=thread_id,
                source=task_request.source,
                source_ref=task_request.source_ref,
                repo_owner=task_request.repo_owner,
                repo_name=task_request.repo_name,
                status=ThreadStatus.IDLE,
            )
            await self._state.upsert_thread(thread)
            root_span.output_summary["thread_existed"] = False
        else:
            root_span.output_summary["thread_existed"] = True

        # CHECK: Is there an active job?
        active_job = await self._state.get_active_job_for_thread(thread_id)
        if active_job is not None:
            logger.info("Thread %s has active job %s, queuing message", thread_id, active_job.k8s_job_name)
            await self._redis.queue_message(thread_id, task_request.task)
            root_span.output_summary["action"] = "queue"
            return

        # LOCK
        if not await self._state.try_acquire_lock(thread_id):
            logger.info("Thread %s is locked, queuing message", thread_id)
            await self._redis.queue_message(thread_id, task_request.task)
            root_span.output_summary["action"] = "lock_failed"
            return

        try:
            root_span.output_summary["action"] = "spawn"
            await self._spawn_job(thread, task_request)
        finally:
            await self._state.release_lock(thread_id)

    except Exception as exc:
        root_span.status = SpanStatus.ERROR
        root_span.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
        raise

    finally:
        root_span.end_time = datetime.now(timezone.utc)
        root_span.duration_ms = (time.monotonic() - start) * 1000
        await self._emit_span(root_span)
        TraceContext.reset_current_span_id(span_token)
        _trace_id_var.reset(trace_id_token)  # imported from tracing.context
```

**`_spawn_job()` method** -- instrument each step:

```python
# AFTER (full replacement with instrumentation)
async def _spawn_job(
    self,
    thread: Thread,
    task_request: TaskRequest,
    is_retry: bool = False,
    retry_count: int = 0,
) -> None:
    thread_id = thread.id
    trace_id = TraceContext.get_trace_id()
    parent_span_id = TraceContext.get_current_span_id()

    # PREPARE: Build system prompt
    integration = self._registry.get(task_request.source)
    claude_md = ""
    conversation_history = await self._state.get_conversation(thread_id)
    conversation_strs = [
        entry["content"] for entry in conversation_history
        if isinstance(entry, dict) and "content" in entry
    ]

    system_prompt = build_system_prompt(
        repo_owner=thread.repo_owner,
        repo_name=thread.repo_name,
        task=task_request.task,
        claude_md=claude_md,
        conversation=conversation_strs if conversation_strs else None,
        is_retry=is_retry,
    )

    await self._state.append_conversation(thread_id, {
        "role": "user",
        "content": task_request.task,
        "source": task_request.source,
    })

    short_id = thread_id[:8]
    branch = f"df/{short_id}/{uuid.uuid4().hex[:8]}"

    # === STEP 1: Classification ===
    matched_skills = []
    agent_image = self._settings.agent_image
    classification = None

    if self._settings.skill_registry_enabled and self._classifier:
        classify_span = self._make_span(
            "task.classify", EventType.TASK_CLASSIFIED, trace_id, parent_span_id,
            input_summary={
                "task_text": task_request.task[:200],
                "language_hint": self._detect_language(thread),
                "domain_hint": task_request.source_ref.get("labels", []),
                "embedding_provider": type(self._classifier._embedder).__name__
                    if self._classifier._embedder else None,
            },
        )
        classify_start = time.monotonic()

        try:
            classification = await self._classifier.classify(
                task=task_request.task,
                language=self._detect_language(thread),
                domain=task_request.source_ref.get("labels", []),
            )
            matched_skills = classification.skills

            # Build rich output from diagnostics
            diag = classification.diagnostics
            classify_span.output_summary = {
                "matched_skills": [
                    {"slug": s.slug, "score": round(sc.score, 4)}
                    for s in matched_skills
                    for sc in (diag.all_candidates if diag else [])
                    if sc.skill.slug == s.slug
                ] if diag else [{"slug": s.slug} for s in matched_skills],
                "agent_type": classification.agent_type,
                "method": diag.method if diag else "unknown",
                "skill_count": len(matched_skills),
            }

            if diag:
                classify_span.reasoning = (
                    f"{diag.method.title()} search: "
                    f"{len(diag.all_candidates)} candidates, "
                    f"{len(matched_skills)} above threshold {diag.threshold_applied}, "
                    f"budget trimmed to {len(classification.skills)}."
                )
                if diag.boosts_applied:
                    slug_boosts = {}
                    for skill in matched_skills:
                        if skill.id in diag.boosts_applied:
                            slug_boosts[skill.slug] = diag.boosts_applied[skill.id]
                    if slug_boosts:
                        classify_span.reasoning += (
                            f" Boosts applied: {slug_boosts}."
                        )

                classify_span.metadata = {
                    "threshold_applied": diag.threshold_applied,
                    "candidates_before_threshold": len(diag.all_candidates),
                    "candidates_after_threshold": len([
                        c for c in diag.all_candidates
                        if c.score >= diag.threshold_applied
                    ]),
                    "candidates_after_budget": len(classification.skills),
                    "boost_applied": diag.boosts_applied,
                    "embedding_cached": diag.embedding_cached,
                    "fallback_triggered": diag.method == "tag_fallback",
                    "fallback_reason": diag.fallback_reason,
                }

            # === STEP 1b: Agent resolution ===
            if self._resolver:
                resolve_span = self._make_span(
                    "agent.resolve", EventType.AGENT_RESOLVED, trace_id, parent_span_id,
                    input_summary={
                        "required_capabilities": list({
                            cap for s in matched_skills for cap in (s.requires or [])
                        }),
                        "skill_domains": list({d for s in matched_skills for d in s.domain}),
                        "default_image": self._settings.agent_image,
                    },
                )
                resolve_start = time.monotonic()
                try:
                    resolved = await self._resolver.resolve(
                        skills=matched_skills,
                        default_image=self._settings.agent_image,
                    )
                    agent_image = resolved.image
                    resolve_span.output_summary = {
                        "agent_type": resolved.agent_type,
                        "image": resolved.image,
                    }
                except Exception as exc:
                    resolve_span.status = SpanStatus.ERROR
                    resolve_span.error_message = str(exc)
                    raise
                finally:
                    resolve_span.duration_ms = (time.monotonic() - resolve_start) * 1000
                    resolve_span.end_time = datetime.now(timezone.utc)
                    await self._emit_span(resolve_span)

        except Exception:
            classify_span.status = SpanStatus.ERROR
            classify_span.error_message = "Skill classification failed, using defaults"
            logger.exception("Skill classification failed, using defaults")
            matched_skills = []
            agent_image = self._settings.agent_image
        finally:
            classify_span.duration_ms = (time.monotonic() - classify_start) * 1000
            classify_span.end_time = datetime.now(timezone.utc)
            await self._emit_span(classify_span)

    # === STEP 2: Skill injection ===
    skills_payload = []
    if matched_skills and self._injector:
        inject_span = self._make_span(
            "skills.inject", EventType.SKILLS_INJECTED, trace_id, parent_span_id,
            input_summary={
                "skill_count": len(matched_skills),
                "total_chars_before_budget": sum(len(s.content) for s in matched_skills),
                "budget_limit": 16000,
            },
        )
        inject_start = time.monotonic()
        try:
            skills_payload = self._injector.format_for_redis(matched_skills)
            inject_span.output_summary = {
                "accepted_skills": [s.slug for s in matched_skills],
                "payload_size_bytes": len(str(skills_payload)),
            }
            inject_span.metadata = {
                "per_skill_chars": {s.slug: len(s.content) for s in matched_skills},
            }
        except Exception as exc:
            inject_span.status = SpanStatus.ERROR
            inject_span.error_message = str(exc)
        finally:
            inject_span.duration_ms = (time.monotonic() - inject_start) * 1000
            inject_span.end_time = datetime.now(timezone.utc)
            await self._emit_span(inject_span)

    # === STEP 3: Redis push (with trace context propagation) ===
    current_span_id = TraceContext.get_current_span_id()
    redis_payload = {
        "task": task_request.task,
        "system_prompt": system_prompt,
        "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
        "branch": branch,
        "skills": skills_payload,
        # Trace context propagation
        "trace_id": trace_id,
        "parent_span_id": current_span_id,
    }
    payload_size = len(str(redis_payload))

    redis_span = self._make_span(
        "redis.push", EventType.REDIS_PUSHED, trace_id, parent_span_id,
        input_summary={
            "thread_id": thread_id,
            "payload_keys": list(redis_payload.keys()),
        },
    )
    redis_start = time.monotonic()
    try:
        await self._redis.push_task(thread_id, redis_payload)
        redis_span.output_summary = {
            "redis_key": f"task:{thread_id}",
            "payload_size_bytes": payload_size,
            "ttl_seconds": 3600,
        }
        redis_span.metadata = {
            "skill_count_in_payload": len(skills_payload),
            "branch": branch,
            "trace_context_propagated": True,
        }
    except Exception as exc:
        redis_span.status = SpanStatus.ERROR
        redis_span.error_message = str(exc)
        raise
    finally:
        redis_span.duration_ms = (time.monotonic() - redis_start) * 1000
        redis_span.end_time = datetime.now(timezone.utc)
        await self._emit_span(redis_span)

    # === STEP 4: K8s job spawn ===
    spawn_span = self._make_span(
        "agent.spawn", EventType.AGENT_SPAWNED, trace_id, parent_span_id,
        input_summary={
            "thread_id": thread_id,
            "agent_image": agent_image,
            "namespace": "default",
        },
    )
    spawn_start = time.monotonic()
    try:
        job_name = self._spawner.spawn(
            thread_id=thread_id,
            github_token="",
            redis_url=self._settings.redis_url,
            agent_image=agent_image,
        )
        spawn_span.output_summary = {"job_name": job_name}
    except Exception as exc:
        spawn_span.status = SpanStatus.ERROR
        spawn_span.error_message = str(exc)
        raise
    finally:
        spawn_span.duration_ms = (time.monotonic() - spawn_start) * 1000
        spawn_span.end_time = datetime.now(timezone.utc)
        await self._emit_span(spawn_span)

    # Track job in state
    skill_names = [s.name if hasattr(s, 'name') else str(s) for s in matched_skills]
    job = Job(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        k8s_job_name=job_name,
        status=JobStatus.RUNNING,
        task_context={
            "task": task_request.task,
            "branch": branch,
            "trace_id": trace_id,  # Store trace_id for later correlation
        },
        agent_type=getattr(classification, 'agent_type', 'general') if classification else 'general',
        skills_injected=skill_names,
        started_at=datetime.now(timezone.utc),
    )
    await self._state.create_job(job)

    # Record skill injection for performance tracking
    if self._settings.skill_registry_enabled and self._tracker and matched_skills:
        try:
            await self._tracker.record_injection(
                skills=matched_skills,
                thread_id=thread_id,
                job_id=job.id,
                task_request=task_request,
            )
        except Exception:
            logger.exception("Failed to record skill injection")

    logger.info("Spawned job %s for thread %s (skills=%d)", job_name, thread_id, len(matched_skills))
```

**`handle_job_completion()` method** -- instrument monitoring and safety:

```python
# AFTER
async def handle_job_completion(self, thread_id: str) -> None:
    thread = await self._state.get_thread(thread_id)
    if thread is None:
        logger.error("Thread %s not found for job completion", thread_id)
        return

    # Recover trace context from job record
    active_job = await self._state.get_active_job_for_thread(thread_id)
    trace_id = None
    if active_job and active_job.task_context:
        trace_id = active_job.task_context.get("trace_id")
    if not trace_id:
        trace_id = uuid_mod.uuid4().hex

    trace_id_token = TraceContext.set_trace_id(trace_id)

    try:
        # === Monitor span ===
        monitor_span = self._make_span(
            "agent.complete", EventType.AGENT_COMPLETED, trace_id, None,
            input_summary={"thread_id": thread_id, "timeout": 60, "poll_interval": 1.0},
        )
        monitor_start = time.monotonic()
        poll_count = 0

        try:
            result = await self._monitor.wait_for_result(thread_id, timeout=60, poll_interval=1.0)
            poll_count = int((time.monotonic() - monitor_start) / 1.0) + 1

            if result is None:
                monitor_span.status = SpanStatus.TIMEOUT
                monitor_span.error_message = "No result within timeout"
                logger.error("No result found for thread %s", thread_id)
                return

            monitor_span.output_summary = {
                "exit_code": result.exit_code,
                "commit_count": result.commit_count,
                "has_pr": result.pr_url is not None,
                "branch": result.branch,
            }
            monitor_span.metadata = {
                "poll_count": poll_count,
                "wait_duration_ms": (time.monotonic() - monitor_start) * 1000,
                "stderr_length": len(result.stderr),
            }
        except Exception as exc:
            monitor_span.status = SpanStatus.ERROR
            monitor_span.error_message = str(exc)
            raise
        finally:
            monitor_span.duration_ms = (time.monotonic() - monitor_start) * 1000
            monitor_span.end_time = datetime.now(timezone.utc)
            await self._emit_span(monitor_span)

        # Persist result to Job
        if active_job:
            status = JobStatus.COMPLETED if result.exit_code == 0 else JobStatus.FAILED
            result_dict = {
                "branch": result.branch,
                "exit_code": result.exit_code,
                "commit_count": result.commit_count,
                "pr_url": result.pr_url,
                "stderr": result.stderr,
            }
            await self._state.update_job_status(active_job.id, status, result=result_dict)

        integration = self._registry.get(thread.source)
        if integration is None:
            logger.error("No integration found for source %s", thread.source)
            return

        # === Safety pipeline span ===
        safety_span = self._make_span(
            "safety.process", EventType.SAFETY_PROCESSED, trace_id, None,
            input_summary={
                "exit_code": result.exit_code,
                "commit_count": result.commit_count,
                "has_pr": result.pr_url is not None,
                "auto_open_pr": self._settings.auto_open_pr,
            },
        )
        safety_start = time.monotonic()
        try:
            pipeline = SafetyPipeline(
                settings=self._settings,
                state_backend=self._state,
                redis_state=self._redis,
                integration=integration,
                spawner=self._spawn_job,
                github_client=self._github_client,
            )
            await pipeline.process(thread, result)

            safety_span.output_summary = {
                "pr_created": result.pr_url is not None,
                "pr_url": result.pr_url,
                "reported": True,
            }
            safety_span.reasoning = self._describe_safety_decision(result)
        except Exception as exc:
            safety_span.status = SpanStatus.ERROR
            safety_span.error_message = str(exc)
            raise
        finally:
            safety_span.duration_ms = (time.monotonic() - safety_start) * 1000
            safety_span.end_time = datetime.now(timezone.utc)
            await self._emit_span(safety_span)

        # === Performance recording span ===
        if self._settings.skill_registry_enabled and self._tracker and active_job:
            perf_span = self._make_span(
                "performance.record", EventType.PERFORMANCE_RECORDED, trace_id, None,
                input_summary={
                    "thread_id": thread_id,
                    "job_id": active_job.id,
                    "exit_code": result.exit_code,
                    "commit_count": result.commit_count,
                },
            )
            perf_start = time.monotonic()
            try:
                await self._tracker.record_outcome(
                    thread_id=thread_id,
                    job_id=active_job.id,
                    result=result,
                )
                perf_span.output_summary = {
                    "skills_updated": active_job.skills_injected,
                }
                perf_span.metadata = {"pr_created": result.pr_url is not None}
            except Exception as exc:
                perf_span.status = SpanStatus.ERROR
                perf_span.error_message = str(exc)
                logger.exception("Failed to record skill outcome")
            finally:
                perf_span.duration_ms = (time.monotonic() - perf_start) * 1000
                perf_span.end_time = datetime.now(timezone.utc)
                await self._emit_span(perf_span)

    finally:
        _trace_id_var.reset(trace_id_token)
```

**New helper methods on Orchestrator**:

```python
def _make_span(
    self,
    operation_name: str,
    event_type: EventType,
    trace_id: str | None,
    parent_span_id: str | None,
    input_summary: dict | None = None,
) -> TraceSpan:
    """Factory for creating a new TraceSpan with defaults."""
    return TraceSpan(
        span_id=uuid_mod.uuid4().hex,
        trace_id=trace_id or uuid_mod.uuid4().hex,
        parent_span_id=parent_span_id,
        operation_name=operation_name,
        event_type=event_type,
        status=SpanStatus.OK,
        start_time=datetime.now(timezone.utc),
        end_time=None,
        duration_ms=None,
        input_summary=input_summary or {},
        output_summary={},
        reasoning=None,
        metadata={},
        error_message=None,
    )

async def _emit_span(self, span: TraceSpan) -> None:
    """Persist a span to the trace store. Never raises."""
    if not self._trace_store:
        return
    try:
        await self._trace_store.insert_span(span)
    except Exception:
        logger.warning(
            "Failed to persist trace span %s for trace %s",
            span.span_id,
            span.trace_id,
            exc_info=True,
        )

@staticmethod
def _describe_safety_decision(result: AgentResult) -> str:
    """Generate human-readable reasoning for safety pipeline action."""
    if result.commit_count > 0 and not result.pr_url:
        return "Commits present, no PR exists, auto_open_pr may create one"
    if result.commit_count == 0 and result.exit_code == 0:
        return "Zero commits with success exit code, anti-stall retry may trigger"
    if result.exit_code != 0:
        return f"Agent failed with exit_code={result.exit_code}"
    return "Normal completion with PR"
```

### 5.2 `controller/src/controller/skills/models.py`

**Add ClassificationDiagnostics** (after existing `ClassificationResult`):

```python
# BEFORE
@dataclass
class ClassificationResult:
    skills: list[Skill]
    agent_type: str = "general"
    task_embedding: list[float] | None = None
```

```python
# AFTER
@dataclass
class ClassificationDiagnostics:
    """Diagnostic data from the classification process."""
    all_candidates: list[ScoredSkill]
    method: str                         # "semantic" | "tag_fallback"
    threshold_applied: float
    boosts_applied: dict[str, float]    # skill_id -> boost delta
    embedding_cached: bool
    fallback_reason: str | None
    budget_input_chars: int
    budget_output_chars: int
    dropped_skills: list[str]


@dataclass
class ClassificationResult:
    skills: list[Skill]
    agent_type: str = "general"
    task_embedding: list[float] | None = None
    diagnostics: ClassificationDiagnostics | None = None
```

### 5.3 `controller/src/controller/skills/classifier.py`

Full replacement of `classify()` method as shown in Section 3.3 above.

### 5.4 `controller/src/controller/main.py`

**Add TraceStore initialization** in the lifespan function:

```python
# BEFORE (in lifespan, after skill registry init)
app.state.orchestrator = Orchestrator(
    settings=settings,
    state=app.state.db,
    redis_state=app.state.redis_state,
    registry=registry,
    spawner=spawner,
    monitor=monitor,
    classifier=classifier,
    injector=injector,
    resolver=resolver,
    tracker=tracker,
)
```

```python
# AFTER
# Initialize trace store
trace_store = None
if settings.tracing_enabled:
    try:
        from controller.tracing.store import TraceStore
        trace_db_path = settings.trace_db_path or "traces.db"
        trace_store = await TraceStore.create(trace_db_path)
        logger.info("Trace store initialized at %s", trace_db_path)
    except Exception:
        logger.exception("Failed to initialize trace store, continuing without tracing")

app.state.orchestrator = Orchestrator(
    settings=settings,
    state=app.state.db,
    redis_state=app.state.redis_state,
    registry=registry,
    spawner=spawner,
    monitor=monitor,
    classifier=classifier,
    injector=injector,
    resolver=resolver,
    tracker=tracker,
    trace_store=trace_store,
)
```

### 5.5 `controller/src/controller/config.py`

**Add tracing settings**:

```python
# Add to Settings class
tracing_enabled: bool = False
trace_db_path: str = "traces.db"
```

### 5.6 New Files

| File | Purpose |
|------|---------|
| `controller/src/controller/tracing/__init__.py` | Package init |
| `controller/src/controller/tracing/models.py` | `TraceSpan`, `EventType`, `SpanStatus` (Phase 1) |
| `controller/src/controller/tracing/store.py` | `TraceStore` with SQLite (Phase 1) |
| `controller/src/controller/tracing/context.py` | `TraceContext` with contextvars (Phase 1) |
| `controller/src/controller/tracing/decorator.py` | `@traced` decorator (Phase 2) |

---

## 6. Error Handling

### 6.1 Core Principle

**Tracing must NEVER block the orchestrator.** If `TraceStore.insert_span()` fails, the task must still execute normally.

### 6.2 Implementation

Every call to `_emit_span()` is wrapped in try/except:

```python
async def _emit_span(self, span: TraceSpan) -> None:
    """Persist a span to the trace store. Never raises."""
    if not self._trace_store:
        return
    try:
        await self._trace_store.insert_span(span)
    except Exception:
        logger.warning(
            "Failed to persist trace span %s for trace %s",
            span.span_id,
            span.trace_id,
            exc_info=True,
        )
```

### 6.3 Failure Scenarios

| Scenario | Behavior |
|----------|----------|
| `TraceStore` is None (tracing disabled) | `_emit_span()` returns immediately |
| SQLite write fails (disk full, corruption) | Warning logged, span lost, task continues |
| `_make_span()` raises (bug in input capture) | Outer try/except in `_spawn_job` catches it, task continues |
| `ClassificationDiagnostics` raises | Classification still returns result; diagnostics is None |
| Trace context propagation fails | Agent creates its own trace_id (independent trace) |

### 6.4 Monitoring Tracing Health

Add a counter metric (or structured log) when spans fail to persist. If the failure rate exceeds a threshold, alert:

```python
# In _emit_span, on failure:
logger.warning(
    "trace_span_persist_failed",
    extra={
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "operation": span.operation_name,
    },
)
```

---

## 7. Test Plan

### 7.1 Integration Test: Full Orchestrator Flow Produces Expected Trace

**File**: `controller/tests/test_orchestrator_tracing.py`

```python
"""Integration test: orchestrator flow produces complete trace tree."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from controller.models import TaskRequest, AgentResult
from controller.orchestrator import Orchestrator
from controller.tracing.models import EventType
from controller.tracing.store import TraceStore


@pytest.fixture
async def trace_store(tmp_path):
    store = await TraceStore.create(str(tmp_path / "test_traces.db"))
    yield store


@pytest.fixture
def orchestrator_with_tracing(trace_store, mock_state, mock_redis, mock_registry,
                               mock_spawner, mock_monitor, mock_classifier,
                               mock_injector, mock_resolver, mock_settings):
    return Orchestrator(
        settings=mock_settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=mock_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        classifier=mock_classifier,
        injector=mock_injector,
        resolver=mock_resolver,
        trace_store=trace_store,
    )


@pytest.mark.asyncio
async def test_spawn_job_produces_complete_trace(orchestrator_with_tracing, trace_store):
    """A full _spawn_job cycle should produce spans for every step."""
    task = TaskRequest(
        thread_id="test-thread-001",
        source="slack",
        source_ref={"channel": "C123"},
        repo_owner="acme",
        repo_name="webapp",
        task="Fix the login button CSS",
    )

    await orchestrator_with_tracing.handle_task(task)

    # Retrieve all spans -- should share one trace_id
    # (We need to query by thread to find the trace_id first)
    all_spans = await trace_store.get_all_spans()  # test helper
    assert len(all_spans) >= 5  # receive, classify, inject, redis, spawn

    trace_ids = {s.trace_id for s in all_spans}
    assert len(trace_ids) == 1, "All spans should share one trace_id"

    event_types = {s.event_type for s in all_spans}
    expected = {
        EventType.TASK_RECEIVED,
        EventType.TASK_CLASSIFIED,
        EventType.SKILLS_INJECTED,
        EventType.REDIS_PUSHED,
        EventType.AGENT_SPAWNED,
    }
    assert expected.issubset(event_types)

    # Verify parent-child relationships
    root = [s for s in all_spans if s.parent_span_id is None]
    assert len(root) == 1
    assert root[0].event_type == EventType.TASK_RECEIVED

    # Verify classification span has diagnostics
    classify_spans = [s for s in all_spans if s.event_type == EventType.TASK_CLASSIFIED]
    assert len(classify_spans) == 1
    cs = classify_spans[0]
    assert "method" in cs.output_summary
    assert cs.reasoning is not None
    assert "threshold_applied" in cs.metadata


@pytest.mark.asyncio
async def test_trace_context_propagated_to_redis(orchestrator_with_tracing, mock_redis):
    """The Redis payload should include trace_id and parent_span_id."""
    task = TaskRequest(
        thread_id="test-thread-002",
        source="slack",
        source_ref={},
        repo_owner="acme",
        repo_name="webapp",
        task="Add dark mode",
    )

    await orchestrator_with_tracing.handle_task(task)

    # Inspect what was pushed to Redis
    push_call = mock_redis.push_task.call_args
    payload = push_call[0][1]  # second positional arg
    assert "trace_id" in payload
    assert "parent_span_id" in payload
    assert len(payload["trace_id"]) == 32  # hex UUID
```

### 7.2 Unit Tests: Each Instrumentation Point

**File**: `controller/tests/test_trace_spans.py`

```python
"""Unit tests for individual trace span emission."""

import pytest
from controller.tracing.models import EventType, SpanStatus, TraceSpan


class TestClassificationSpan:
    def test_classification_span_captures_all_candidates(self):
        """Verify classification span includes pre-threshold candidates."""
        # Arrange: mock classifier with 10 candidates, 3 above threshold
        # Act: run classification
        # Assert: span.metadata.candidates_before_threshold == 10
        pass

    def test_classification_span_records_boost_deltas(self):
        """Verify boost amounts are recorded per skill."""
        pass

    def test_classification_fallback_records_reason(self):
        """When embedding fails, span records fallback_reason."""
        pass

    def test_classification_without_embedder_uses_tag_method(self):
        """Without embedding provider, method should be 'tag_fallback'."""
        pass


class TestSkillInjectionSpan:
    def test_injection_span_records_per_skill_chars(self):
        """Each skill's character count is in metadata."""
        pass

    def test_injection_span_records_dropped_skills(self):
        """Skills dropped by budget appear in output_summary.dropped_skills."""
        pass


class TestRedisSpan:
    def test_redis_span_records_payload_size(self):
        pass

    def test_redis_span_confirms_trace_propagation(self):
        """metadata.trace_context_propagated should be True."""
        pass


class TestSpawnSpan:
    def test_spawn_span_records_job_name(self):
        pass

    def test_spawn_error_records_exception(self):
        """K8s API failure should set status=ERROR."""
        pass
```

### 7.3 Failure Tests: Tracing Failure Does Not Break Orchestrator

**File**: `controller/tests/test_tracing_resilience.py`

```python
"""Verify tracing failures never block the orchestrator."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_trace_store_failure_does_not_block_spawn(
    orchestrator_with_tracing, trace_store
):
    """If TraceStore.insert_span raises, the job should still spawn."""
    trace_store.insert_span = AsyncMock(side_effect=Exception("DB write failed"))

    task = TaskRequest(
        thread_id="test-thread-003",
        source="slack",
        source_ref={},
        repo_owner="acme",
        repo_name="webapp",
        task="Fix bug",
    )

    # Should NOT raise
    await orchestrator_with_tracing.handle_task(task)

    # Job should still have been spawned
    assert orchestrator_with_tracing._spawner.spawn.called


@pytest.mark.asyncio
async def test_no_trace_store_runs_normally(mock_settings, mock_state, mock_redis,
                                             mock_registry, mock_spawner, mock_monitor):
    """Orchestrator with trace_store=None should work identically to before."""
    orch = Orchestrator(
        settings=mock_settings,
        state=mock_state,
        redis_state=mock_redis,
        registry=mock_registry,
        spawner=mock_spawner,
        monitor=mock_monitor,
        trace_store=None,  # No tracing
    )

    task = TaskRequest(
        thread_id="test-thread-004",
        source="slack",
        source_ref={},
        repo_owner="acme",
        repo_name="webapp",
        task="Fix bug",
    )

    await orch.handle_task(task)
    assert mock_spawner.spawn.called


@pytest.mark.asyncio
async def test_diagnostics_failure_does_not_break_classification(
    mock_classifier,
):
    """If diagnostics collection raises, classify() should still return skills."""
    # Arrange: mock that causes diagnostics creation to fail
    # but skills are still returned
    # Act + Assert: classification succeeds with diagnostics=None
    pass
```

### 7.4 Test for `@traced` Decorator

**File**: `controller/tests/test_traced_decorator.py`

```python
"""Tests for the @traced decorator."""

import pytest
from controller.tracing.decorator import traced, set_trace_store
from controller.tracing.models import EventType, SpanStatus
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_traced_async_captures_timing():
    store = AsyncMock()
    set_trace_store(store)

    @traced("test.op", EventType.TASK_RECEIVED)
    async def my_func(x):
        return x * 2

    result = await my_func(5)
    assert result == 10
    assert store.insert_span.called
    span = store.insert_span.call_args[0][0]
    assert span.operation_name == "test.op"
    assert span.duration_ms > 0
    assert span.status == SpanStatus.OK


@pytest.mark.asyncio
async def test_traced_async_captures_error():
    store = AsyncMock()
    set_trace_store(store)

    @traced("test.fail", EventType.TASK_RECEIVED)
    async def failing_func():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await failing_func()

    span = store.insert_span.call_args[0][0]
    assert span.status == SpanStatus.ERROR
    assert "ValueError: boom" in span.error_message


@pytest.mark.asyncio
async def test_traced_store_failure_does_not_propagate():
    store = AsyncMock()
    store.insert_span.side_effect = Exception("DB down")
    set_trace_store(store)

    @traced("test.resilient", EventType.TASK_RECEIVED)
    async def resilient_func():
        return 42

    result = await resilient_func()
    assert result == 42  # Function still works
```

---

## 8. Trace Tree Visualization

A complete task execution produces this span tree:

```
task.receive (TASK_RECEIVED)                    [root, ~2ms]
  |
  +-- task.classify (TASK_CLASSIFIED)           [~450ms, embedding + search]
  |     |
  |     +-- agent.resolve (AGENT_RESOLVED)      [~5ms, DB lookup]
  |
  +-- skills.inject (SKILLS_INJECTED)           [~1ms, format + serialize]
  |
  +-- redis.push (REDIS_PUSHED)                 [~3ms, Redis SET]
  |
  +-- agent.spawn (AGENT_SPAWNED)               [~200ms, K8s API]

--- agent pod runs (traced in Phase 3) ---

agent.complete (AGENT_COMPLETED)                [~60s, polling]
  |
  +-- safety.process (SAFETY_PROCESSED)         [~500ms, PR creation + reporting]
  |
  +-- performance.record (PERFORMANCE_RECORDED) [~5ms, SQLite write]
```

---

## 9. Migration and Rollout

### 9.1 Feature Flag

All tracing is gated behind `settings.tracing_enabled` (default: `False`). The `_emit_span()` method returns immediately when `self._trace_store` is None.

### 9.2 Rollout Plan

1. **Deploy with tracing_enabled=False** -- zero behavior change, validates no import errors
2. **Enable on staging** -- verify trace DB grows, inspect span quality
3. **Enable on production** -- monitor SQLite write latency, disk usage
4. **Build dashboard** -- query trace DB for classification accuracy over time

### 9.3 Backward Compatibility

- `ClassificationResult.diagnostics` defaults to `None`, so existing callers are unaffected
- `Orchestrator.__init__` accepts `trace_store=None` (optional kwarg at the end)
- Redis payload gains two new keys (`trace_id`, `parent_span_id`) which agents ignore until Phase 3
- No existing tests need modification

---

## 10. Open Questions

1. **Should we store task_embedding in the trace span?** It is 1024 floats (~8KB). Useful for debugging but expensive to store per trace. Recommendation: store only if a `trace_debug` flag is set.

2. **Should the `@traced` decorator be used on SafetyPipeline.process() directly?** The current plan instruments it from the orchestrator. If SafetyPipeline gains more internal steps, the decorator approach is cleaner.

3. **Batched writes vs per-span writes?** Phase 1 TraceStore should support both. For Phase 2, per-span writes are simpler and the volume is low (~8 spans per task). Batch if latency becomes an issue.

4. **Trace retention policy?** Not in scope for Phase 2, but the store should support `DELETE FROM trace_spans WHERE start_time < ?` for a future cleanup job.
