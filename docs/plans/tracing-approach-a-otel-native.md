# Approach A: OpenTelemetry-Native Tracing for Ditto Factory

**Status**: Proposed
**Author**: Software Architect
**Date**: 2026-03-21

---

## 1. Architecture Overview

OpenTelemetry (OTel) becomes the **single tracing backbone** for all decision-making, agent execution, and result processing in Ditto Factory. Every request — from webhook receipt through agent completion and report generation — is a single distributed trace composed of nested spans.

### Why OTel-Native?

The OTel GenAI semantic conventions (v0.29+) define first-class span kinds for AI agent systems: `invoke_agent`, `execute_tool`, and `chat`. Ditto Factory's pipeline maps almost 1:1 onto these conventions:

| Pipeline Stage | OTel Span Kind | GenAI Convention |
|----------------|----------------|------------------|
| Webhook received | HTTP server span | Standard ASGI instrumentation |
| Task classification | `execute_tool` | Custom tool: `classify_task` |
| Skill injection | `execute_tool` | Custom tool: `inject_skills` |
| Agent type resolution | `execute_tool` | Custom tool: `resolve_agent_type` |
| K8s job spawn | `execute_tool` | Custom tool: `spawn_job` |
| Agent execution | `invoke_agent` | GenAI agent span |
| Claude API call | `chat` | GenAI LLM span |
| Result polling | Internal span | Custom |
| Safety pipeline | `execute_tool` | Custom tool: `safety_check` |
| Report generation | Internal span | Custom |

### System Diagram: Span Flow

```
                                    Trace: ditto-{thread_id}
                                    ════════════════════════

   Controller Process                                           Agent Pod (K8s)
  ┌─────────────────────────────────────────────┐         ┌──────────────────────────────┐
  │                                             │         │                              │
  │  [HTTP] POST /webhook/{source}              │         │  [invoke_agent] agent.run    │
  │  ├── [INTERNAL] orchestrate                 │         │  ├── [chat] claude.chat      │
  │  │   ├── [TOOL] classify_task               │         │  │   ├── input/output tokens │
  │  │   │   ├── gen.ai.semantic_search         │         │  │   └── thinking content    │
  │  │   │   └── gen.ai.tag_match               │         │  ├── [TOOL] git_commit      │
  │  │   ├── [TOOL] inject_skills               │         │  ├── [TOOL] file_edit       │
  │  │   │   └── skill slugs as attributes      │         │  ├── [chat] claude.chat (2) │
  │  │   ├── [TOOL] resolve_agent_type          │         │  └── [TOOL] pr_create       │
  │  │   ├── [TOOL] spawn_job                   │         │                              │
  │  │   │   └── k8s.job.name attribute         │         └──────────────────────────────┘
  │  │   └── [INTERNAL] await_result            │                       │
  │  │       └── poll loop (not individual spans)│                      │
  │  ├── [TOOL] safety_check                    │◄──── traceparent via Redis ────┘
  │  ├── [TOOL] performance_record              │
  │  └── [TOOL] report_result                   │
  │                                             │
  └─────────────────────────────────────────────┘
                    │
                    ▼
           OTel Collector (OTLP/gRPC)
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
     Tempo/Jaeger        Report Generator
     (trace storage)     (queries traces,
                          renders Markdown)
```

### Key Architectural Decision

Traces flow **through Redis** via W3C `traceparent` propagation. The controller serializes the trace context into the Redis task payload. The agent pod deserializes it and continues the same trace. This gives us a single trace spanning both processes — the defining advantage of OTel-native over log-based approaches.

---

## 2. Trace Data Model

### 2.1 Span Hierarchy

Every webhook request produces a trace with this nesting structure:

```
root: HTTP POST /webhook/{source}
├── orchestrate (thread_id, source, repo)
│   ├── classify_task
│   │   ├── embed_task (if semantic search enabled)
│   │   ├── search_skills (vector similarity scores)
│   │   └── tag_fallback (if embedding fails)
│   ├── inject_skills
│   ├── resolve_agent_type
│   ├── push_task_to_redis
│   ├── spawn_k8s_job
│   └── await_result (long-running, may be hours)
├── agent.run  ← (propagated from Redis, child of orchestrate)
│   ├── claude.chat (1..N LLM calls)
│   │   ├── tool_call: Read
│   │   ├── tool_call: Edit
│   │   └── tool_call: Bash
│   └── claude.chat (continued turns)
├── safety_check
├── performance_record
└── report_result
```

### 2.2 Span Attributes

All spans carry a base attribute set. Specialized spans carry additional attributes per the GenAI semantic conventions.

#### Base Attributes (all spans)

| Attribute | Type | Example |
|-----------|------|---------|
| `ditto.thread.id` | string | `"abc12345"` |
| `ditto.job.id` | string | `"def67890"` |
| `ditto.repo.owner` | string | `"myorg"` |
| `ditto.repo.name` | string | `"web-app"` |
| `ditto.source` | string | `"github"` |

#### Task Classification Span

| Attribute | Type | Example |
|-----------|------|---------|
| `ditto.classification.method` | string | `"semantic"` or `"tag_fallback"` |
| `ditto.classification.confidence` | float | `0.87` |
| `ditto.classification.skills_matched` | int | `3` |
| `ditto.classification.skills` | string[] | `["debug-react", "typescript-testing"]` |
| `ditto.classification.agent_type` | string | `"frontend"` |
| `ditto.classification.search_duration_ms` | int | `42` |

#### Skill Injection Span

| Attribute | Type | Example |
|-----------|------|---------|
| `ditto.skills.injected` | string[] | `["debug-react", "typescript-testing"]` |
| `ditto.skills.defaults_included` | string[] | `["code-review-checklist"]` |
| `ditto.skills.total_size_bytes` | int | `12480` |

#### Agent Execution Span (GenAI `invoke_agent`)

| Attribute | Type | Example |
|-----------|------|---------|
| `gen_ai.agent.name` | string | `"ditto-agent"` |
| `gen_ai.agent.id` | string | `"pod-abc123"` |
| `gen_ai.system` | string | `"anthropic"` |
| `ditto.agent.image` | string | `"ditto-factory-agent:frontend-v2"` |
| `ditto.agent.exit_code` | int | `0` |
| `ditto.agent.commit_count` | int | `3` |
| `ditto.agent.branch` | string | `"df/abc12345/fe3d9a1b"` |

#### LLM Chat Span (GenAI `chat`)

| Attribute | Type | Example |
|-----------|------|---------|
| `gen_ai.system` | string | `"anthropic"` |
| `gen_ai.request.model` | string | `"claude-sonnet-4-20250514"` |
| `gen_ai.usage.input_tokens` | int | `15230` |
| `gen_ai.usage.output_tokens` | int | `4821` |
| `gen_ai.response.finish_reason` | string | `"end_turn"` |

#### Tool Call Span (GenAI `execute_tool`)

| Attribute | Type | Example |
|-----------|------|---------|
| `gen_ai.tool.name` | string | `"Edit"` |
| `gen_ai.tool.call_id` | string | `"toolu_abc123"` |
| `ditto.tool.file_path` | string | `"src/components/LoginForm.tsx"` |
| `ditto.tool.success` | bool | `true` |

### 2.3 Span Events

For data too large for attributes (Claude's reasoning, full prompts), we use **span events**:

| Event Name | Attached To | Payload |
|------------|-------------|---------|
| `gen_ai.content.prompt` | `chat` span | System prompt (truncated to 4KB) |
| `gen_ai.content.completion` | `chat` span | Response text (truncated to 4KB) |
| `ditto.agent.thinking` | `chat` span | Extended thinking content (truncated to 8KB) |
| `ditto.agent.stderr` | `invoke_agent` span | Agent stderr output |
| `ditto.safety.report` | `safety_check` span | Safety pipeline output |

> **Design note**: Span events are the right place for large text, not attributes. Attributes are for indexed, searchable, filterable fields. Events are for contextual detail that enriches a specific span. This distinction matters for storage cost and query performance.

---

## 3. Instrumentation Strategy

Instrumentation is added at six points in the pipeline. Each point uses the `opentelemetry-api` library directly — no auto-instrumentation magic that obscures what is being traced.

### 3.1 Instrumentation Points

| # | Component | File | What to Trace |
|---|-----------|------|---------------|
| 1 | FastAPI webhook handler | `main.py` | HTTP request lifecycle (ASGI auto-instrumentation) |
| 2 | Orchestrator | `orchestrator.py` | `_spawn_job` orchestration, all sub-decisions |
| 3 | Task Classifier | `task_classifier.py` | Semantic search, tag matching, confidence scores |
| 4 | Skill Injector | `skill_injector.py` | Which skills selected, payload size |
| 5 | Job Spawner | `spawner.py` | K8s job creation, traceparent injection into Redis |
| 6 | Agent Pod | `entrypoint.py` | Agent lifecycle, Claude calls, tool invocations |

### 3.2 Dependency Setup

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
tracing = [
    "opentelemetry-api>=1.29",
    "opentelemetry-sdk>=1.29",
    "opentelemetry-exporter-otlp>=1.29",
    "opentelemetry-instrumentation-fastapi>=0.50b0",
    "opentelemetry-instrumentation-redis>=0.50b0",
    "opentelemetry-instrumentation-httpx>=0.50b0",
    "opentelemetry-semantic-conventions>=0.50b0",
]
```

### 3.3 Tracer Initialization Module

```python
# controller/src/controller/tracing.py
"""
OpenTelemetry initialization for Ditto Factory.

This module is imported once at startup (in main.py lifespan).
All other modules import the tracer from here.
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource


def init_tracing(service_name: str = "ditto-controller", endpoint: str | None = None) -> None:
    """Initialize OTel tracing. Call once at app startup."""
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "0.1.0",
    })

    provider = TracerProvider(resource=resource)

    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)


def get_tracer(module_name: str) -> trace.Tracer:
    """Get a tracer for the given module. Thin wrapper for consistency."""
    return trace.get_tracer(module_name, "0.1.0")
```

---

## 4. Code Examples: Three Instrumentation Points

### 4.1 Orchestrator — The Decision Hub

This is the most critical instrumentation point. The orchestrator makes all routing decisions, and today those decisions are invisible.

```python
# controller/src/controller/orchestrator.py
from __future__ import annotations

import uuid
from opentelemetry import trace, context
from opentelemetry.trace import StatusCode
from opentelemetry.trace.propagation import TraceContextTextMapPropagator

from controller.tracing import get_tracer
from controller.models import TaskRequest, Job, JobStatus

tracer = get_tracer(__name__)
propagator = TraceContextTextMapPropagator()


class Orchestrator:
    async def _spawn_job(
        self,
        thread,
        task_request: TaskRequest,
        is_retry: bool = False,
        retry_count: int = 0,
    ):
        with tracer.start_as_current_span(
            "orchestrate",
            attributes={
                "ditto.thread.id": task_request.thread_id,
                "ditto.repo.owner": task_request.repo_owner,
                "ditto.repo.name": task_request.repo_name,
                "ditto.source": task_request.source,
                "ditto.is_retry": is_retry,
                "ditto.retry_count": retry_count,
            },
        ) as orchestrate_span:

            # --- Step 1: Classify task and select skills ---
            matched_skills = []
            agent_image = self._settings.agent_image

            if self._settings.skill_registry_enabled:
                with tracer.start_as_current_span(
                    "classify_task",
                    attributes={"ditto.classification.method": "semantic"},
                ) as classify_span:
                    try:
                        classification = await self._classifier.classify(
                            task=task_request.task,
                            language=self._detect_language(thread),
                            domain=task_request.source_ref.get("labels", []),
                        )
                        matched_skills = classification.skills

                        # Record decision details as span attributes
                        classify_span.set_attribute(
                            "ditto.classification.confidence",
                            classification.confidence,
                        )
                        classify_span.set_attribute(
                            "ditto.classification.skills",
                            [s.slug for s in matched_skills],
                        )
                        classify_span.set_attribute(
                            "ditto.classification.skills_matched",
                            len(matched_skills),
                        )

                        # Resolve agent type
                        with tracer.start_as_current_span(
                            "resolve_agent_type"
                        ) as resolve_span:
                            resolved = await self._resolver.resolve(
                                skills=matched_skills,
                                default_image=self._settings.agent_image,
                            )
                            agent_image = resolved.image
                            resolve_span.set_attribute(
                                "ditto.agent.image", agent_image
                            )
                            resolve_span.set_attribute(
                                "ditto.classification.agent_type",
                                resolved.agent_type,
                            )

                    except Exception as e:
                        classify_span.set_status(StatusCode.ERROR, str(e))
                        classify_span.record_exception(e)
                        # Graceful degradation: continue without skills
                        matched_skills = []
                        agent_image = self._settings.agent_image

            # --- Step 2: Inject skills into payload ---
            with tracer.start_as_current_span("inject_skills") as inject_span:
                skills_payload = self._injector.format_for_redis(matched_skills)
                inject_span.set_attribute(
                    "ditto.skills.injected",
                    [s.slug for s in matched_skills],
                )
                inject_span.set_attribute(
                    "ditto.skills.total_size_bytes",
                    sum(len(s.content.encode()) for s in matched_skills),
                )

            # --- Step 3: Build prompt (existing logic, unchanged) ---
            system_prompt = build_system_prompt(
                repo_owner=thread.repo_owner,
                repo_name=thread.repo_name,
                task=task_request.task,
            )

            # --- Step 4: Push to Redis WITH trace context ---
            with tracer.start_as_current_span("push_task_to_redis") as redis_span:
                # Propagate trace context through Redis payload
                carrier: dict[str, str] = {}
                propagator.inject(carrier)

                branch = f"df/{task_request.thread_id[:8]}/{uuid.uuid4().hex[:8]}"

                await self._redis.push_task(task_request.thread_id, {
                    "task": task_request.task,
                    "system_prompt": system_prompt,
                    "repo_url": (
                        f"https://github.com/{thread.repo_owner}"
                        f"/{thread.repo_name}.git"
                    ),
                    "branch": branch,
                    "skills": skills_payload,
                    "traceparent": carrier.get("traceparent", ""),  # <-- KEY
                    "tracestate": carrier.get("tracestate", ""),
                })
                redis_span.set_attribute("ditto.agent.branch", branch)

            # --- Step 5: Spawn K8s job ---
            with tracer.start_as_current_span("spawn_k8s_job") as spawn_span:
                job_name = self._spawner.spawn(
                    thread_id=task_request.thread_id,
                    github_token="",
                    redis_url=self._settings.redis_url,
                    agent_image=agent_image,
                )
                spawn_span.set_attribute("k8s.job.name", job_name)
                orchestrate_span.set_attribute("k8s.job.name", job_name)
```

### 4.2 Task Classifier — Capturing Search Decisions

The classifier is where the system decides which skills are relevant. Today this is a black box. With tracing, every similarity score and ranking decision is visible.

```python
# controller/src/controller/skills/task_classifier.py
from __future__ import annotations

from opentelemetry.trace import StatusCode
from controller.tracing import get_tracer

tracer = get_tracer(__name__)


class TaskClassifier:
    async def classify(
        self,
        task: str,
        language: list[str] | None = None,
        domain: list[str] | None = None,
        max_skills: int = 5,
    ) -> ClassificationResult:
        """Classify a task and return matching skills with confidence scores."""

        with tracer.start_as_current_span(
            "semantic_search",
            attributes={
                "ditto.search.max_skills": max_skills,
                "ditto.search.language_filter": language or [],
                "ditto.search.domain_filter": domain or [],
            },
        ) as search_span:

            # Step 1: Generate embedding for the task description
            with tracer.start_as_current_span("embed_task") as embed_span:
                task_embedding = await self._embedding_provider.embed(task)
                embed_span.set_attribute(
                    "ditto.embedding.dimensions", len(task_embedding)
                )

            # Step 2: Vector similarity search
            with tracer.start_as_current_span("vector_search") as vs_span:
                scored_skills = await self._registry.search_by_embedding(
                    task_embedding=task_embedding,
                    filters=SkillFilters(language=language, domain=domain),
                    limit=max_skills * 2,  # Over-fetch for re-ranking
                )
                vs_span.set_attribute(
                    "ditto.search.candidates_returned", len(scored_skills)
                )

                # Record each candidate's score as a span event
                # (too detailed for attributes, perfect for events)
                for skill in scored_skills:
                    vs_span.add_event(
                        "skill_candidate",
                        attributes={
                            "skill.slug": skill.slug,
                            "skill.similarity": skill.similarity,
                            "skill.boosted_score": self._tracker.compute_boost(
                                skill.id, skill.similarity
                            ),
                        },
                    )

            # Step 3: Apply performance boost and re-rank
            with tracer.start_as_current_span("rerank_with_performance") as rerank_span:
                boosted = []
                for skill in scored_skills:
                    boosted_score = self._tracker.compute_boost(
                        skill.id, skill.similarity
                    )
                    boosted.append((skill, boosted_score))

                boosted.sort(key=lambda x: x[1], reverse=True)
                final_skills = [s for s, _ in boosted[:max_skills]]

                rerank_span.set_attribute(
                    "ditto.rerank.selected",
                    [s.slug for s in final_skills],
                )
                rerank_span.set_attribute(
                    "ditto.rerank.top_score",
                    boosted[0][1] if boosted else 0.0,
                )

            # Step 4: Include default skills
            with tracer.start_as_current_span("include_defaults") as defaults_span:
                defaults = await self._registry.get_defaults()
                defaults_span.set_attribute(
                    "ditto.defaults.count", len(defaults)
                )

                # Merge defaults (avoid duplicates)
                final_slugs = {s.slug for s in final_skills}
                for d in defaults:
                    if d.slug not in final_slugs:
                        final_skills.append(d)

            search_span.set_attribute(
                "ditto.classification.final_count", len(final_skills)
            )

            return ClassificationResult(
                skills=final_skills,
                confidence=boosted[0][1] if boosted else 0.0,
            )
```

### 4.3 Agent Pod — Capturing Claude's Reasoning

This is the hardest and most valuable instrumentation point. The agent pod runs `claude -p` as a subprocess. We need to capture Claude's tool calls, reasoning, and outcomes.

```python
# agent/src/agent/traced_entrypoint.py
"""
Traced agent entrypoint. Reads task from Redis, runs Claude CLI,
captures output as OTel spans.

This replaces the raw `claude -p` subprocess call with a traced wrapper
that parses Claude's streaming output into structured spans.
"""
from __future__ import annotations

import json
import subprocess
import sys
from opentelemetry import trace, context
from opentelemetry.trace.propagation import TraceContextTextMapPropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource


def init_agent_tracing(otel_endpoint: str) -> trace.Tracer:
    """Initialize tracing in the agent pod."""
    resource = Resource.create({
        "service.name": "ditto-agent",
        "service.version": "0.1.0",
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otel_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(__name__)


def restore_trace_context(task_payload: dict) -> context.Context:
    """
    Restore the trace context that was propagated through Redis.

    The controller serialized traceparent/tracestate into the Redis payload.
    We deserialize it here to continue the same distributed trace.
    """
    propagator = TraceContextTextMapPropagator()
    carrier = {
        "traceparent": task_payload.get("traceparent", ""),
        "tracestate": task_payload.get("tracestate", ""),
    }
    return propagator.extract(carrier)


def run_agent_with_tracing(task_payload: dict, otel_endpoint: str) -> int:
    """
    Run the Claude agent with full OTel tracing.

    Strategy: Use `claude --output-format stream-json -p` to get structured
    streaming output. Parse each JSON event into OTel spans in real-time.

    Claude's stream-json output emits events like:
    - {"type": "assistant", "message": {...}}  -> chat span
    - {"type": "tool_use", "name": "Edit", ...} -> tool span
    - {"type": "result", "exit_code": 0, ...}  -> final attributes
    """
    tracer = init_agent_tracing(otel_endpoint)

    # Restore distributed trace context from Redis payload
    parent_ctx = restore_trace_context(task_payload)

    with tracer.start_as_current_span(
        "agent.run",
        context=parent_ctx,
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "gen_ai.agent.name": "ditto-agent",
            "gen_ai.system": "anthropic",
            "ditto.agent.image": task_payload.get("agent_image", "unknown"),
            "ditto.thread.id": task_payload.get("thread_id", "unknown"),
            "ditto.agent.branch": task_payload.get("branch", "unknown"),
        },
    ) as agent_span:

        # Build the claude command
        prompt = task_payload["task"]
        system_prompt = task_payload["system_prompt"]

        cmd = [
            "claude",
            "--output-format", "stream-json",
            "--system-prompt", system_prompt,
            "-p", prompt,
        ]

        # Stream and parse Claude's output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        current_chat_span = None
        tool_spans: dict[str, trace.Span] = {}
        total_input_tokens = 0
        total_output_tokens = 0

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "assistant":
                # New LLM turn — start a chat span
                if current_chat_span:
                    current_chat_span.end()

                current_chat_span = tracer.start_span(
                    "claude.chat",
                    attributes={
                        "gen_ai.system": "anthropic",
                        "gen_ai.request.model": event.get("model", "unknown"),
                    },
                )

                # Capture thinking content as span event
                thinking = event.get("message", {}).get("thinking", "")
                if thinking:
                    current_chat_span.add_event(
                        "ditto.agent.thinking",
                        attributes={
                            "content": thinking[:8192],  # Truncate
                        },
                    )

            elif event_type == "tool_use":
                tool_name = event.get("name", "unknown")
                tool_id = event.get("id", "unknown")

                tool_span = tracer.start_span(
                    f"tool.{tool_name}",
                    attributes={
                        "gen_ai.tool.name": tool_name,
                        "gen_ai.tool.call_id": tool_id,
                    },
                )
                tool_spans[tool_id] = tool_span

            elif event_type == "tool_result":
                tool_id = event.get("tool_use_id", "")
                if tool_id in tool_spans:
                    span = tool_spans.pop(tool_id)
                    is_error = event.get("is_error", False)
                    span.set_attribute("ditto.tool.success", not is_error)
                    if is_error:
                        span.set_status(
                            trace.StatusCode.ERROR,
                            event.get("content", "")[:1024],
                        )
                    span.end()

            elif event_type == "usage":
                # Token usage update
                total_input_tokens += event.get("input_tokens", 0)
                total_output_tokens += event.get("output_tokens", 0)

            elif event_type == "result":
                # Final result
                exit_code = event.get("exit_code", 1)
                agent_span.set_attribute("ditto.agent.exit_code", exit_code)

        # Clean up any open spans
        if current_chat_span:
            current_chat_span.set_attribute(
                "gen_ai.usage.input_tokens", total_input_tokens
            )
            current_chat_span.set_attribute(
                "gen_ai.usage.output_tokens", total_output_tokens
            )
            current_chat_span.end()

        for span in tool_spans.values():
            span.end()

        # Wait for process and capture stderr
        _, stderr = process.communicate()
        exit_code = process.returncode

        if stderr:
            agent_span.add_event(
                "ditto.agent.stderr",
                attributes={"content": stderr[:4096]},
            )

        agent_span.set_attribute("ditto.agent.exit_code", exit_code)

        if exit_code != 0:
            agent_span.set_status(
                trace.StatusCode.ERROR,
                f"Agent exited with code {exit_code}",
            )

        return exit_code
```

---

## 5. Cross-Process Propagation

The hardest problem in this architecture: the controller and agent pod are separate processes communicating through Redis. OTel's context propagation assumes HTTP headers or gRPC metadata. We need to bridge the gap.

### 5.1 Strategy: W3C Traceparent in Redis Payload

```
Controller                    Redis                     Agent Pod
    │                           │                           │
    │  traceparent:             │                           │
    │  00-{trace_id}-           │                           │
    │  {span_id}-01             │                           │
    │                           │                           │
    │  HSET task:{thread_id}    │                           │
    │  { ...,                   │                           │
    │    traceparent: "00-...", │                           │
    │    tracestate: "" }       │                           │
    │ ─────────────────────────▶│                           │
    │                           │                           │
    │                           │   HGET task:{thread_id}   │
    │                           │◀──────────────────────────│
    │                           │                           │
    │                           │   Extract traceparent,    │
    │                           │   create child span with  │
    │                           │   same trace_id           │
    │                           │──────────────────────────▶│
```

### 5.2 Controller Side (Injection)

```python
# In orchestrator._spawn_job, before pushing to Redis:
from opentelemetry.trace.propagation import TraceContextTextMapPropagator

propagator = TraceContextTextMapPropagator()
carrier: dict[str, str] = {}
propagator.inject(carrier)  # Serializes current span context

redis_payload = {
    "task": task_request.task,
    "system_prompt": system_prompt,
    "skills": skills_payload,
    "traceparent": carrier.get("traceparent", ""),
    "tracestate": carrier.get("tracestate", ""),
}
```

### 5.3 Agent Side (Extraction)

```python
# In agent entrypoint, after reading task from Redis:
from opentelemetry.trace.propagation import TraceContextTextMapPropagator

propagator = TraceContextTextMapPropagator()
carrier = {
    "traceparent": task_payload["traceparent"],
    "tracestate": task_payload.get("tracestate", ""),
}
parent_ctx = propagator.extract(carrier)

# Start agent span as child of the controller's span
with tracer.start_as_current_span("agent.run", context=parent_ctx):
    ...
```

### 5.4 Why This Works

The W3C `traceparent` header format is: `00-{trace_id}-{parent_span_id}-{trace_flags}`. By serializing this into the Redis payload, we preserve the full trace lineage. The agent pod's `agent.run` span becomes a child of the controller's `spawn_k8s_job` span, creating a single unified trace.

### 5.5 Edge Cases

| Scenario | Handling |
|----------|----------|
| Missing traceparent in payload | Agent creates a new root trace (graceful degradation) |
| Redis payload TTL expires | Trace is orphaned but still queryable by trace_id in controller spans |
| Multiple retries of same task | Each retry gets a new trace; linked via `ditto.thread.id` attribute |
| Agent crashes before flushing spans | Controller-side spans still show spawn + await_result timeout |

---

## 6. Storage and Backend

### 6.1 Recommended Stack: Grafana Tempo + Grafana

**Why Tempo over Jaeger:**

| Criterion | Tempo | Jaeger | Phoenix (Arize) |
|-----------|-------|--------|-----------------|
| Storage cost | Object storage (S3/GCS), cheapest | Elasticsearch/Cassandra, expensive | Hosted SaaS or local SQLite |
| Trace retention | Weeks/months affordably | Days/weeks (storage grows fast) | Limited in local mode |
| GenAI conventions | Supports via attributes | Supports via attributes | Native GenAI support |
| Query language | TraceQL (powerful) | Basic tag search | SQL-like |
| Operational burden | Low (single binary + object store) | Medium (multiple components) | Low (SaaS) or Medium (self-hosted) |
| K8s native | Yes (Helm chart) | Yes (operator) | Partial |
| Report generation | API + TraceQL | API | API |

**Alternative worth considering**: Arize Phoenix for its GenAI-native UI. Phoenix understands LLM spans natively and can show token usage, latency distributions, and evaluation metrics out of the box. The tradeoff is less mature trace storage for long retention.

### 6.2 Deployment Architecture

```
Agent Pods ──┐
             │ OTLP/gRPC
Controller ──┼──────────▶ OTel Collector ──┬──▶ Tempo (traces)
             │                             ├──▶ Prometheus (metrics from spans)
             │                             └──▶ Loki (logs, correlated by trace_id)
             │
             └── Grafana (dashboards, trace viewer, report queries)
```

### 6.3 OTel Collector Configuration

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"

processors:
  batch:
    timeout: 5s
    send_batch_size: 1024

  # Filter out noisy internal spans to control storage costs
  filter:
    error_mode: ignore
    traces:
      span:
        - 'attributes["ditto.internal"] == true'

  # Add K8s metadata to spans automatically
  k8sattributes:
    extract:
      metadata:
        - k8s.pod.name
        - k8s.namespace.name
        - k8s.node.name

exporters:
  otlp/tempo:
    endpoint: "tempo.monitoring.svc.cluster.local:4317"
    tls:
      insecure: true

  prometheus:
    endpoint: "0.0.0.0:8889"
    # Generate metrics from span data (RED metrics)
    resource_to_telemetry_conversion:
      enabled: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch, k8sattributes]
      exporters: [otlp/tempo]
```

### 6.4 Trace Retention Policy

| Data Category | Retention | Rationale |
|---------------|-----------|-----------|
| Full traces (all spans + events) | 7 days | Debugging and active review |
| Sampled traces (1 in 10) | 30 days | Trend analysis, performance regression detection |
| Span metrics (aggregated) | 90 days | Dashboard data: success rates, latency percentiles |
| Exported reports (Markdown) | Indefinite | Git-committed review artifacts |

---

## 7. Report Generation

This is where OTel-native tracing pays its biggest dividend. A single TraceQL query retrieves the entire decision chain for a job, and a Python script renders it into a readable Markdown report.

### 7.1 Report Structure

```markdown
# Agent Report: df/abc12345/fe3d9a1b

## Summary
| Field | Value |
|-------|-------|
| Thread | abc12345 |
| Source | GitHub Issue #42 |
| Repo | myorg/web-app |
| Status | Completed (exit_code: 0) |
| Duration | 4m 32s |
| Commits | 3 |
| Branch | df/abc12345/fe3d9a1b |

## Skills Injected
| Skill | Similarity | Boosted Score | Method |
|-------|-----------|---------------|--------|
| debug-react | 0.87 | 0.91 | semantic |
| typescript-testing | 0.72 | 0.75 | semantic |
| code-review-checklist | - | - | default |

**Classification confidence**: 0.87
**Agent type resolved**: frontend (image: ditto-factory-agent:frontend-v2)

## Agent Activity Timeline
| Time | Action | Details |
|------|--------|---------|
| 00:00 | Started | Pod ditto-abc123 launched |
| 00:03 | LLM Call #1 | 15,230 input / 4,821 output tokens |
| 00:03 | Thinking | "The login form validation issue is in LoginForm.tsx..." |
| 00:15 | Tool: Read | src/components/LoginForm.tsx |
| 00:16 | Tool: Read | src/utils/validation.ts |
| 00:22 | Tool: Edit | src/components/LoginForm.tsx (success) |
| 00:25 | Tool: Bash | npm test (success) |
| 00:30 | LLM Call #2 | 8,102 input / 2,340 output tokens |
| 00:35 | Tool: Edit | src/utils/validation.ts (success) |
| 00:38 | Tool: Bash | git commit (success) |
| 04:32 | Completed | 3 commits, exit_code 0 |

## Token Usage
| Metric | Value |
|--------|-------|
| Total input tokens | 23,332 |
| Total output tokens | 7,161 |
| LLM calls | 2 |
| Tool invocations | 5 |

## Decision Trace
Why were these skills selected?
1. Task "Fix the broken login form validation" embedded to vector [0.12, -0.34, ...]
2. Semantic search returned 8 candidates from pgvector
3. Top candidate: debug-react (similarity: 0.87, boosted: 0.91 via 92% success rate)
4. Performance boost applied: +4.6% from historical 92% success rate
5. Default skill code-review-checklist added unconditionally
6. Agent type resolved to "frontend" based on debug-react requiring browser capability
```

### 7.2 Report Generator Implementation

```python
# controller/src/controller/reports/trace_report.py
"""
Generate Markdown reports from OTel traces stored in Tempo.

Queries Tempo's HTTP API for a complete trace by trace_id,
then renders the span tree as a readable report.
"""
from __future__ import annotations

import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class TraceReport:
    thread_id: str
    trace_id: str
    markdown: str
    generated_at: datetime


class TraceReportGenerator:
    """Query Tempo and render traces as Markdown reports."""

    def __init__(self, tempo_url: str = "http://tempo.monitoring.svc.cluster.local:3200"):
        self._tempo_url = tempo_url

    async def generate(self, trace_id: str) -> TraceReport:
        """
        Fetch a complete trace from Tempo and render it as Markdown.

        Steps:
        1. GET /api/traces/{trace_id} from Tempo
        2. Parse the OTLP JSON response into a span tree
        3. Walk the tree and extract decision attributes
        4. Render as structured Markdown
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._tempo_url}/api/traces/{trace_id}",
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            trace_data = resp.json()

        spans = self._parse_spans(trace_data)
        tree = self._build_span_tree(spans)
        markdown = self._render_markdown(tree)

        thread_id = self._extract_attribute(tree.root, "ditto.thread.id")

        return TraceReport(
            thread_id=thread_id,
            trace_id=trace_id,
            markdown=markdown,
            generated_at=datetime.utcnow(),
        )

    def _parse_spans(self, trace_data: dict) -> list[SpanInfo]:
        """Parse Tempo's response into SpanInfo objects."""
        spans = []
        for batch in trace_data.get("batches", []):
            resource_attrs = self._attrs_to_dict(
                batch.get("resource", {}).get("attributes", [])
            )
            service_name = resource_attrs.get("service.name", "unknown")

            for scope_spans in batch.get("scopeSpans", []):
                for span in scope_spans.get("spans", []):
                    spans.append(SpanInfo(
                        span_id=span["spanId"],
                        parent_span_id=span.get("parentSpanId", ""),
                        name=span["name"],
                        start_time=int(span["startTimeUnixNano"]),
                        end_time=int(span["endTimeUnixNano"]),
                        attributes=self._attrs_to_dict(
                            span.get("attributes", [])
                        ),
                        events=[
                            EventInfo(
                                name=e["name"],
                                attributes=self._attrs_to_dict(
                                    e.get("attributes", [])
                                ),
                            )
                            for e in span.get("events", [])
                        ],
                        service_name=service_name,
                        status=span.get("status", {}).get("code", "UNSET"),
                    ))
        return spans

    def _render_markdown(self, tree: SpanTree) -> str:
        """Render the span tree as a Markdown report."""
        root = tree.root
        attrs = root.attributes

        # Extract key fields
        thread_id = attrs.get("ditto.thread.id", "unknown")
        source = attrs.get("ditto.source", "unknown")
        repo = f"{attrs.get('ditto.repo.owner', '?')}/{attrs.get('ditto.repo.name', '?')}"

        # Find agent span for results
        agent_span = tree.find_span("agent.run")
        exit_code = agent_span.attributes.get("ditto.agent.exit_code", "?") if agent_span else "?"
        branch = agent_span.attributes.get("ditto.agent.branch", "?") if agent_span else "?"

        duration_ns = root.end_time - root.start_time
        duration = timedelta(microseconds=duration_ns // 1000)

        lines = [
            f"# Agent Report: {branch}",
            "",
            "## Summary",
            "| Field | Value |",
            "|-------|-------|",
            f"| Thread | {thread_id} |",
            f"| Source | {source} |",
            f"| Repo | {repo} |",
            f"| Status | {'Completed' if exit_code == 0 else 'Failed'} (exit_code: {exit_code}) |",
            f"| Duration | {duration} |",
            f"| Branch | {branch} |",
            "",
        ]

        # Skills section
        classify_span = tree.find_span("classify_task")
        if classify_span:
            skills = classify_span.attributes.get("ditto.classification.skills", [])
            confidence = classify_span.attributes.get("ditto.classification.confidence", "?")

            lines.extend([
                "## Skills Injected",
                "| Skill | Confidence |",
                "|-------|-----------|",
            ])
            for skill in skills:
                lines.append(f"| {skill} | {confidence} |")
            lines.append("")

        # Timeline from tool spans
        tool_spans = tree.find_spans_by_prefix("tool.")
        if tool_spans:
            lines.extend([
                "## Agent Activity Timeline",
                "| Offset | Action | Details |",
                "|--------|--------|---------|",
            ])
            base_time = root.start_time
            for ts in sorted(tool_spans, key=lambda s: s.start_time):
                offset_s = (ts.start_time - base_time) / 1e9
                tool_name = ts.attributes.get("gen_ai.tool.name", ts.name)
                success = ts.attributes.get("ditto.tool.success", True)
                status_str = "success" if success else "FAILED"
                lines.append(
                    f"| {offset_s:.1f}s | {tool_name} | {status_str} |"
                )
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _attrs_to_dict(attrs: list[dict]) -> dict:
        """Convert OTel attribute list to a flat dict."""
        result = {}
        for attr in attrs:
            key = attr["key"]
            value = attr.get("value", {})
            if "stringValue" in value:
                result[key] = value["stringValue"]
            elif "intValue" in value:
                result[key] = int(value["intValue"])
            elif "doubleValue" in value:
                result[key] = float(value["doubleValue"])
            elif "boolValue" in value:
                result[key] = bool(value["boolValue"])
            elif "arrayValue" in value:
                result[key] = [
                    v.get("stringValue", str(v))
                    for v in value["arrayValue"].get("values", [])
                ]
        return result
```

### 7.3 Report Trigger Points

Reports can be generated at three points:

| Trigger | Mechanism | Use Case |
|---------|-----------|----------|
| Job completion | `handle_job_completion` calls `TraceReportGenerator.generate(trace_id)` | Automatic report on every completed job |
| API endpoint | `GET /api/v1/reports/{trace_id}` | On-demand report for debugging |
| CLI command | `ditto report <thread_id>` | Engineer pulls up a report locally |

### 7.4 Trace-to-Report Query Flow

```
1. Job completes → handle_job_completion has trace_id from span context
2. Query Tempo: GET /api/traces/{trace_id}
3. Parse span tree (controller spans + agent spans, unified by traceparent)
4. Render Markdown using span attributes and events
5. Post report to integration (GitHub PR comment, Slack thread, Linear comment)
6. Optionally commit report to repo as .ditto/reports/{job_id}.md
```

---

## 8. Pros and Cons

### What This Approach Does Well

| Advantage | Details |
|-----------|---------|
| **Unified trace** | Single trace_id spans webhook → agent → report. No log correlation needed. |
| **Standard ecosystem** | OTel is the industry standard. Grafana, Datadog, Honeycomb all speak OTLP. No vendor lock-in. |
| **GenAI conventions** | The `invoke_agent` / `execute_tool` / `chat` span types are purpose-built for our use case. |
| **Structured decisions** | Span attributes like `ditto.classification.confidence` are queryable, alertable, and dashboardable. |
| **Zero custom storage** | Traces go to Tempo (or any OTLP backend). No new database schema for tracing data. |
| **Automatic correlation** | Parent-child span relationships automatically connect classifier decisions to agent outcomes. |
| **Ecosystem tooling** | Grafana trace viewer, TraceQL queries, span-to-metrics pipelines — all free. |

### What This Approach Does Poorly

| Disadvantage | Details | Mitigation |
|--------------|---------|------------|
| **Cross-process propagation is fragile** | traceparent through Redis is unconventional. If the agent pod does not extract it correctly, the trace breaks into two disconnected halves. | Fallback: link by `ditto.thread.id` attribute. Both halves are still queryable. |
| **Agent instrumentation is complex** | Parsing Claude's streaming JSON output into spans requires careful state management. Edge cases (crashes, partial output, timeouts) are tricky. | Start with basic spans (start/end/exit_code). Add tool-level detail incrementally. |
| **Large text in spans is awkward** | OTel span events are not designed for 10KB+ text blobs. Storing full prompts, reasoning, and code diffs in span events strains the backend. | Truncate aggressively (4-8KB per event). Store full text in object storage, reference by span_id. |
| **Operational overhead** | OTel Collector, Tempo, Grafana — three new services to deploy and maintain in K8s. | Use Grafana Cloud or a managed OTel backend to offload operations. Alternatively, start with Jaeger all-in-one for dev/staging. |
| **Trace retention cost** | Agent traces can be large (dozens of tool call spans, each with events). At scale, storage costs grow. | Tail-based sampling: only store traces where `exit_code != 0` or duration > threshold. Sample successful traces at 10%. |
| **Report generation coupling** | Report quality depends on Tempo query API availability. If Tempo is down, reports fail. | Cache generated reports. Generate eagerly at job completion, not lazily at view time. |
| **No native full-text search** | TraceQL can filter by attribute values but cannot full-text search span event content. Finding "which agent edited LoginForm.tsx" requires attribute indexing, not event content search. | Index key fields (file paths, tool names) as span attributes, not just events. |

### Honest Assessment

OTel-native tracing is the right approach if you value **ecosystem compatibility** and **structured queryability** over simplicity. The cross-process propagation through Redis is the main risk — it works, but it is not how OTel was designed to be used, and debugging broken traces will be frustrating.

The alternative — a custom event log stored in Postgres — would be simpler to implement and query, but would not give you the trace viewer, span-to-metrics pipeline, or ecosystem compatibility. You would be building a worse version of what OTel already provides.

My recommendation: **adopt this approach, but phase it so you get value before solving the hard problems.**

---

## 9. Implementation Effort — Phased Rollout

### Phase 1: Controller-Side Tracing (3-4 days)

**Goal**: Trace all decisions in the controller. No agent-side instrumentation yet.

| Task | Effort | Details |
|------|--------|---------|
| Add OTel dependencies | 0.5 days | `opentelemetry-api`, `opentelemetry-sdk`, OTLP exporter |
| Create `tracing.py` module | 0.5 days | Tracer initialization, helper functions |
| Instrument `orchestrator._spawn_job` | 1 day | Orchestration span with all sub-decision spans |
| Instrument `task_classifier.classify` | 0.5 days | Semantic search, tag matching, confidence scores |
| Instrument `skill_injector` | 0.5 days | Skill selection and payload construction |
| Deploy Jaeger all-in-one for dev | 0.5 days | Single container, in-memory storage |
| Verify traces in Jaeger UI | 0.5 days | End-to-end test: webhook → trace visible |

**Outcome**: You can see WHY skills were selected for every task. Biggest immediate value.

### Phase 2: Cross-Process Propagation (2-3 days)

**Goal**: Unified traces spanning controller and agent pod.

| Task | Effort | Details |
|------|--------|---------|
| Add traceparent to Redis payload | 0.5 days | Controller-side injection |
| Agent entrypoint: extract traceparent | 0.5 days | Restore context, start child span |
| Basic agent span (start/end/exit_code) | 0.5 days | No tool-level detail yet |
| OTel Collector deployment | 0.5 days | Helm chart, OTLP receiver, Tempo exporter |
| Tempo deployment | 0.5 days | Helm chart, S3/GCS backend |
| Verify cross-process trace | 0.5 days | Single trace showing both controller + agent spans |

**Outcome**: Unified trace across process boundary. Core distributed tracing working.

### Phase 3: Agent Detail Instrumentation (4-5 days)

**Goal**: Capture Claude's tool calls, token usage, and reasoning in spans.

| Task | Effort | Details |
|------|--------|---------|
| Parse Claude stream-json output | 2 days | State machine for tool_use/tool_result/assistant events |
| Create per-tool spans | 1 day | `tool.Edit`, `tool.Read`, `tool.Bash` with attributes |
| Capture token usage | 0.5 days | Aggregate across turns, set on chat spans |
| Capture thinking content | 0.5 days | Truncated span events for extended thinking |
| Edge case handling | 1 day | Crashes, timeouts, partial output, OOM |

**Outcome**: Full visibility into agent behavior. Every tool call traceable.

### Phase 4: Report Generation (3-4 days)

**Goal**: Automatic Markdown reports from traces.

| Task | Effort | Details |
|------|--------|---------|
| `TraceReportGenerator` implementation | 1.5 days | Tempo query, span tree parsing, Markdown rendering |
| Report API endpoint | 0.5 days | `GET /api/v1/reports/{trace_id}` |
| Auto-generate on job completion | 0.5 days | Hook into `handle_job_completion` |
| Post report to integration | 0.5 days | GitHub PR comment, Slack thread |
| Report template refinement | 0.5 days | Iterate on format based on engineer feedback |

**Outcome**: Engineers get readable reports for every agent run, automatically.

### Phase 5: Dashboards and Alerting (2-3 days)

**Goal**: Operational visibility and proactive alerts.

| Task | Effort | Details |
|------|--------|---------|
| Grafana dashboards | 1 day | Success rate, latency, token usage, skill popularity |
| Span-to-metrics pipeline | 0.5 days | OTel Collector `spanmetrics` connector |
| Alerts | 0.5 days | High failure rate, classification confidence dropping |
| Tail-based sampling | 0.5 days | Only retain 100% of failed traces, sample successful |

**Outcome**: Operational maturity. Proactive detection of regressions.

### Total Estimate: 14-19 days

Phases 1-2 deliver the core value (decision tracing + cross-process correlation) in ~6 days. Phases 3-4 add depth (agent detail + reports) in ~8 days. Phase 5 is operational polish.

---

## 10. ADR: OpenTelemetry as Tracing Backbone

### ADR-004: OpenTelemetry-Native Tracing over Custom Event Log

**Status**: Proposed

**Context**: Ditto Factory has no observability into agent decision-making. We cannot answer "why were these skills selected?" or "what did the agent do?" without SSH-ing into logs. We need structured tracing across the webhook-to-report lifecycle, including cross-process correlation between the controller and agent pods.

**Options considered**:
1. **Custom event log in Postgres** — Simple INSERT-based event logging with a query API
2. **OpenTelemetry-native tracing** — Use OTel spans with GenAI semantic conventions
3. **Hybrid** — OTel for transport, custom storage for query

**Decision**: Option 2, OpenTelemetry-native tracing.

**Rationale**:
- OTel GenAI semantic conventions (`invoke_agent`, `execute_tool`, `chat`) map directly to our pipeline stages
- Distributed trace context propagation solves the controller→Redis→agent correlation problem
- Ecosystem tooling (Grafana, TraceQL, span-to-metrics) eliminates the need for custom dashboards
- OTLP export means we can switch backends (Tempo, Jaeger, Datadog, Honeycomb) without code changes
- The Python SDK is mature and well-maintained

**Consequences**:
- *Easier*: Querying decision chains, correlating across processes, building dashboards, switching backends
- *Harder*: Large text storage (prompts, reasoning), cross-process propagation through Redis (unconventional), operational overhead of Collector + backend
- *Risk*: If Redis propagation breaks, traces split into disconnected halves. Mitigated by `ditto.thread.id` attribute linking.
