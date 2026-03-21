# Approach D: Langfuse-Integrated Observability

## Status
Proposed — 2026-03-21

---

## 1. Architecture Overview

### The Argument

Ditto Factory currently has **zero visibility into agent reasoning**. We know *what* happened (exit code, commit count) but not *why*. The system is a black box between "task pushed to Redis" and "agent result received."

Building custom tracing infrastructure is a trap. You start with "just a spans table" and end up maintaining a query engine, a UI, retention policies, and a cost tracker. Langfuse already solves all of this. It is purpose-built for LLM observability, open-source, self-hostable, and has first-class Anthropic SDK support.

**The thesis**: Use Langfuse as the tracing backbone. Invest zero engineering time in storage, indexing, or visualization. Invest all engineering time in *instrumentation* (the hard part) and *report generation* (the unique part).

### Integration Diagram

```
                    ┌──────────────────────────────────────────────┐
                    │              Controller Process              │
                    │                                              │
                    │  Webhook ─► Orchestrator ─► TaskClassifier   │
                    │       │          │              │            │
                    │       │    @observe()      @observe()        │
                    │       │          │              │            │
                    │       │          ▼              ▼            │
                    │       │    SkillInjector   AgentTypeResolver │
                    │       │     @observe()       @observe()      │
                    │       │          │              │            │
                    │       │          ▼              ▼            │
                    │       │    JobSpawner ──► Redis (task push)  │
                    │       │     @observe()    trace_id in payload│
                    │       │                                      │
                    │       │    JobMonitor ◄── Redis (result poll)│
                    │       │     @observe()                       │
                    │       │          │                            │
                    │       │          ▼                            │
                    │       │    PerformanceTracker                 │
                    │       │     @observe()                       │
                    │       │          │                            │
                    │       │          ▼                            │
                    │       │    SafetyPipeline                    │
                    │       │     @observe()                       │
                    │       │          │                            │
                    │       └──────────┼────────────────────────────┘
                    │                  │                            │
                    │                  ▼                            │
                    │         Langfuse SDK ─────────────────────────┼──► Langfuse Server
                    │         (flush async)                        │    (self-hosted)
                    └──────────────────────────────────────────────┘
                                                                          │
                    ┌──────────────────────────────────────────────┐       │
                    │              Agent Pod (K8s)                 │       │
                    │                                              │       │
                    │  entrypoint.sh ─► claude -p (subprocess)     │       │
                    │       │              │                        │       │
                    │       │         stdout/stderr captured        │       │
                    │       │              │                        │       │
                    │       │         agent_tracer.py               │       │
                    │       │         (wraps claude output,         │       │
                    │       │          parses tool calls,           │       │
                    │       │          sends spans to Langfuse)     │───────┘
                    │       │              │                        │
                    │       │         Redis result push             │
                    │       │         (includes trace_id)           │
                    │       │                                       │
                    └──────────────────────────────────────────────┘

                    ┌──────────────────────────────────────────────┐
                    │          Report Generator (cron/API)         │
                    │                                              │
                    │  Queries Langfuse API ─► Renders Markdown    │
                    │  (GET /api/public/traces)                    │
                    │  (GET /api/public/observations)              │
                    └──────────────────────────────────────────────┘

                    ┌──────────────────────────────────────────────┐
                    │          Langfuse Server (self-hosted)       │
                    │                                              │
                    │  Docker Compose:                             │
                    │    - langfuse-web (Next.js UI)               │
                    │    - langfuse-worker (async processing)      │
                    │    - PostgreSQL 15 (trace storage)           │
                    │    - ClickHouse (analytics, optional)        │
                    │    - Redis (queue, can share existing)       │
                    └──────────────────────────────────────────────┘
```

---

## 2. Trace Model

Langfuse provides a three-level hierarchy: **Trace → Span → Generation**. Here is how it maps to the Ditto Factory flow.

### Mapping

| Ditto Factory Concept | Langfuse Primitive | Name Convention | Metadata |
|---|---|---|---|
| Full job lifecycle (webhook → report) | **Trace** | `job:{thread_id}` | `thread_id`, `repo`, `source`, `task` |
| Orchestrator `_spawn_job` | **Span** | `orchestrate` | `retry_count`, `is_retry` |
| TaskClassifier `classify()` | **Span** | `classify_task` | `language`, `domain`, `matched_count` |
| Embedding API call (Voyage-3) | **Generation** | `embed_task` | `model=voyage-3`, `input_tokens`, `cost` |
| SkillInjector `format_for_redis()` | **Span** | `inject_skills` | `skill_slugs[]`, `total_skills` |
| AgentTypeResolver `resolve()` | **Span** | `resolve_agent_type` | `resolved_image`, `required_tools` |
| JobSpawner `spawn()` | **Span** | `spawn_k8s_job` | `k8s_job_name`, `image`, `namespace` |
| Agent execution (entire pod) | **Span** | `agent_execution` | `duration_s`, `exit_code` |
| Each Claude API call inside agent | **Generation** | `claude_call:{n}` | `model`, `input_tokens`, `output_tokens`, `cost` |
| Each tool use inside agent | **Span** | `tool:{tool_name}` | `tool_input`, `tool_output` (truncated) |
| JobMonitor `wait_for_result()` | **Span** | `monitor_result` | `poll_count`, `timeout_s` |
| PerformanceTracker `record_outcome()` | **Span** | `record_metrics` | `exit_code`, `commit_count`, `pr_created` |
| SafetyPipeline checks | **Span** | `safety_check` | `passed`, `violations[]` |
| Report generation | **Span** | `generate_report` | `report_format`, `word_count` |

### Trace Lifecycle

```
Trace "job:abc123" created at webhook receipt
  ├── Span "orchestrate" (controller process)
  │   ├── Span "classify_task"
  │   │   └── Generation "embed_task" (Voyage-3 API call)
  │   ├── Span "inject_skills"
  │   ├── Span "resolve_agent_type"
  │   └── Span "spawn_k8s_job"
  │
  ├── Span "agent_execution" (agent pod, linked via trace_id)
  │   ├── Generation "claude_call:1" (initial prompt)
  │   │   └── Span "tool:bash" (tool use)
  │   ├── Generation "claude_call:2" (follow-up)
  │   │   └── Span "tool:edit" (tool use)
  │   └── Generation "claude_call:3" (final)
  │
  ├── Span "monitor_result"
  ├── Span "record_metrics"
  ├── Span "safety_check"
  └── Span "generate_report"
```

### Key Insight: Trace Continuity

A single Langfuse trace spans the entire job lifecycle, even across process boundaries (controller → agent pod). This is the feature that makes Langfuse compelling: you see the full story in one view, not fragments scattered across logs.

---

## 3. Instrumentation Strategy

### 3.1 Controller-Side: `@observe` Decorator

Langfuse's `@observe` decorator is the primary instrumentation mechanism. It automatically captures function entry/exit, duration, and return values with zero boilerplate.

**Where to add decorators:**

| File | Function | Decorator |
|---|---|---|
| `orchestrator.py` | `_spawn_job()` | `@observe(name="orchestrate")` |
| `orchestrator.py` | `handle_job_completion()` | `@observe(name="handle_completion")` |
| `skills/classifier.py` | `classify()` | `@observe(name="classify_task")` |
| `skills/injector.py` | `format_for_redis()` | `@observe(name="inject_skills")` |
| `skills/resolver.py` | `resolve()` | `@observe(name="resolve_agent_type")` |
| `skills/performance.py` | `record_injection()` | `@observe(name="record_injection")` |
| `skills/performance.py` | `record_outcome()` | `@observe(name="record_metrics")` |
| `jobs/spawner.py` | `spawn()` | `@observe(name="spawn_k8s_job")` |
| `jobs/monitor.py` | `wait_for_result()` | `@observe(name="monitor_result")` |

### 3.2 Agent-Side: Wrapper Script

The agent pod runs `claude -p` as a subprocess. We cannot add `@observe` decorators inside Claude's binary. Instead, we use a **wrapper script** that:

1. Receives the `trace_id` from Redis task payload
2. Initializes a Langfuse client with that trace_id
3. Captures `claude -p` stdout/stderr in real-time
4. Parses structured output (tool calls, reasoning) and creates spans/generations
5. Flushes to Langfuse before writing the result to Redis

### 3.3 Manual Span Creation

For cases where `@observe` is insufficient (e.g., adding metadata mid-function), use manual span creation:

```python
from langfuse.decorators import langfuse_context

@observe(name="orchestrate")
async def _spawn_job(self, thread, task_request, is_retry=False, retry_count=0):
    # Add metadata to the current trace
    langfuse_context.update_current_trace(
        name=f"job:{thread.id}",
        metadata={
            "repo": f"{thread.repo_owner}/{thread.repo_name}",
            "source": task_request.source,
            "is_retry": is_retry,
            "retry_count": retry_count,
        },
        tags=["ditto-factory", task_request.source],
    )

    # ... existing orchestration logic ...
```

---

## 4. Cross-Process Tracing

This is the hardest problem: the controller creates the trace, but the agent pod (a separate K8s job) must continue it. Langfuse supports this via explicit trace ID propagation.

### 4.1 Protocol

```
Controller                          Redis                           Agent Pod
    │                                 │                                │
    │  1. Create Langfuse trace       │                                │
    │     trace_id = uuid4()          │                                │
    │                                 │                                │
    │  2. Push task to Redis ─────────►                                │
    │     payload includes:           │                                │
    │     {                           │                                │
    │       "task": "...",            │                                │
    │       "system_prompt": "...",   │                                │
    │       "langfuse_trace_id": id,  │  3. Pop task from Redis ◄──────│
    │       "skills": [...]           │     extract trace_id           │
    │     }                           │                                │
    │                                 │                                │
    │                                 │  4. Init Langfuse client       │
    │                                 │     with existing trace_id     │
    │                                 │                                │
    │                                 │  5. Create span                │
    │                                 │     "agent_execution"          │
    │                                 │     under that trace           │
    │                                 │                                │
    │                                 │  6. Run claude -p              │
    │                                 │     Parse output, create       │
    │                                 │     child spans/generations    │
    │                                 │                                │
    │                                 │  7. Flush Langfuse             │
    │                                 │                                │
    │                                 │  8. Push result to Redis ──────►
    │  9. Poll result from Redis ◄────│     (unchanged format)         │
    │                                 │                                │
    │  10. Continue trace             │                                │
    │      (monitor, safety, report)  │                                │
```

### 4.2 Code: Controller Side (Trace Creation + Redis Propagation)

```python
# controller/src/controller/orchestrator.py

from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context
import uuid

class Orchestrator:
    def __init__(self, ..., langfuse: Langfuse):
        self._langfuse = langfuse

    @observe(name="orchestrate")
    async def _spawn_job(self, thread, task_request, is_retry=False, retry_count=0):
        # Create a trace ID that will be shared with the agent pod
        trace_id = uuid.uuid4().hex

        # Update the current observation's trace to use our explicit ID
        langfuse_context.update_current_trace(
            id=trace_id,
            name=f"job:{thread.id[:8]}",
            user_id=thread.repo_owner,
            metadata={
                "thread_id": thread.id,
                "repo": f"{thread.repo_owner}/{thread.repo_name}",
                "source": task_request.source,
                "task_preview": task_request.task[:200],
            },
            tags=["ditto-factory", task_request.source, thread.repo_name],
        )

        # ... classification, injection, resolution (all @observe'd) ...

        # Push task to Redis — NOW INCLUDES trace_id
        await self._redis.push_task(thread.id, {
            "task": task_request.task,
            "system_prompt": system_prompt,
            "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
            "branch": branch,
            "skills": skills_payload,
            "langfuse_trace_id": trace_id,       # <-- NEW: cross-process link
        })

        # Spawn K8s job (unchanged)
        job_name = self._spawner.spawn(...)

        # Record skill injection (unchanged)
        if matched_skills:
            await self._tracker.record_injection(...)
```

### 4.3 Code: Agent Side (Trace Continuation)

```python
# agent/agent_tracer.py — runs inside the agent pod

import json
import subprocess
import sys
from langfuse import Langfuse

def main():
    # 1. Read task from Redis (already done by entrypoint.sh, passed as env/file)
    task_payload = json.loads(os.environ["TASK_PAYLOAD"])
    trace_id = task_payload.get("langfuse_trace_id")

    # 2. Initialize Langfuse client, pointing at the SAME trace
    langfuse = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ["LANGFUSE_HOST"],  # e.g., http://langfuse.internal:3000
    )

    # 3. Create a span under the existing trace
    trace = langfuse.trace(id=trace_id)
    agent_span = trace.span(
        name="agent_execution",
        metadata={
            "pod_name": os.environ.get("HOSTNAME", "unknown"),
            "image": os.environ.get("AGENT_IMAGE", "unknown"),
        },
    )

    # 4. Run claude -p and capture output
    proc = subprocess.Popen(
        ["claude", "-p", task_payload["system_prompt"]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout_lines = []
    for line in proc.stdout:
        stdout_lines.append(line)
        # Parse tool calls and create child spans
        parsed = parse_claude_output_line(line)
        if parsed and parsed["type"] == "tool_use":
            agent_span.span(
                name=f"tool:{parsed['tool_name']}",
                input=parsed.get("input", {}),
                output=parsed.get("output", {}),
            )
        elif parsed and parsed["type"] == "generation":
            agent_span.generation(
                name=f"claude_call:{parsed['turn']}",
                model=parsed.get("model", "claude-sonnet-4-20250514"),
                input=parsed.get("input"),
                output=parsed.get("output"),
                usage={
                    "input": parsed.get("input_tokens", 0),
                    "output": parsed.get("output_tokens", 0),
                },
            )

    proc.wait()

    # 5. End span with result metadata
    agent_span.end(
        metadata={
            "exit_code": proc.returncode,
            "stderr_preview": proc.stderr.read()[:500],
        },
    )

    # 6. Flush before exiting
    langfuse.flush()

    # 7. Push result to Redis (existing behavior, unchanged)
    # ...
```

### 4.4 Environment Variables for Agent Pods

The `spawner.py` must inject Langfuse credentials into agent pods:

```python
# Added to JobSpawner.build_job_spec()

env_vars = [
    # ... existing env vars ...
    k8s.V1EnvVar(
        name="LANGFUSE_PUBLIC_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(
                name="ditto-factory-secrets",
                key="langfuse-public-key",
            ),
        ),
    ),
    k8s.V1EnvVar(
        name="LANGFUSE_SECRET_KEY",
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(
                name="ditto-factory-secrets",
                key="langfuse-secret-key",
            ),
        ),
    ),
    k8s.V1EnvVar(
        name="LANGFUSE_HOST",
        value=settings.langfuse_host,  # e.g., "http://langfuse.internal:3000"
    ),
]
```

---

## 5. Infrastructure

### 5.1 Self-Hosted Deployment

Langfuse is deployed via Docker Compose alongside the existing K8s infrastructure. It does NOT run inside K8s (to avoid circular dependency — we are tracing K8s jobs).

```yaml
# docker-compose.langfuse.yml

version: "3.9"

services:
  langfuse-web:
    image: langfuse/langfuse:2
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgresql://langfuse:${LANGFUSE_DB_PASSWORD}@langfuse-db:5432/langfuse
      NEXTAUTH_SECRET: ${LANGFUSE_AUTH_SECRET}
      NEXTAUTH_URL: http://langfuse.internal:3000
      SALT: ${LANGFUSE_SALT}
      LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES: "true"
    depends_on:
      langfuse-db:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - langfuse-net
      - ditto-net  # shared network with controller

  langfuse-db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ${LANGFUSE_DB_PASSWORD}
      POSTGRES_DB: langfuse
    volumes:
      - langfuse-pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped
    networks:
      - langfuse-net

volumes:
  langfuse-pg-data:

networks:
  langfuse-net:
    driver: bridge
  ditto-net:
    external: true
```

### 5.2 Resource Requirements

| Component | CPU | Memory | Disk | Notes |
|---|---|---|---|---|
| langfuse-web | 0.5 vCPU | 512 MB | — | Next.js app + API |
| langfuse-db | 0.5 vCPU | 1 GB | 10 GB (grows) | Dedicated Postgres for traces |
| **Total** | **1 vCPU** | **1.5 GB** | **10 GB** | Modest for self-hosted |

For comparison: building custom tracing with Postgres + a dashboard would require similar resources, but you'd also need engineering time to build the query layer, UI, and retention policies.

### 5.3 Networking

- Controller pods reach Langfuse via internal DNS: `langfuse.internal:3000`
- Agent pods reach Langfuse via the same internal DNS (must be routable from K8s pod network)
- If Langfuse runs outside K8s, expose via NodePort or ingress with internal-only access
- Langfuse SDK uses async HTTP flush — no blocking on trace writes

### 5.4 Data Retention

Langfuse supports configurable retention. Recommended policy:

| Data | Retention | Rationale |
|---|---|---|
| Traces (metadata) | 90 days | Sufficient for trend analysis |
| Generations (full I/O) | 30 days | Large; contains full prompts/responses |
| Scores/evaluations | Indefinite | Small; used for long-term skill metrics |

---

## 6. Report Generation

Langfuse's web UI is excellent for ad-hoc exploration. But engineers reviewing agent work need a **portable, self-contained document** — a Markdown report they can read in a PR comment or Slack thread.

### 6.1 Architecture

```
                    ┌─────────────────────────────┐
                    │      Report Generator       │
                    │                              │
                    │  Trigger: POST /api/report   │
                    │           or cron (daily)    │
                    │                              │
                    │  1. Query Langfuse API       │
                    │     GET /api/public/traces   │
                    │     GET /api/public/          │
                    │         observations         │
                    │                              │
                    │  2. Build trace timeline     │
                    │                              │
                    │  3. Render Jinja2 template   │
                    │     → Markdown document      │
                    │                              │
                    │  4. Post to GitHub PR /      │
                    │     Slack / store in S3      │
                    └─────────────────────────────┘
```

### 6.2 Code: Report Generator

```python
# controller/src/controller/reports/generator.py

from langfuse import Langfuse
from jinja2 import Template
from datetime import datetime, timedelta

REPORT_TEMPLATE = """
# Agent Activity Report: {{ trace.name }}

**Generated**: {{ now }}
**Trace ID**: {{ trace.id }}
**Duration**: {{ duration_s }}s
**Status**: {{ "SUCCESS" if exit_code == 0 else "FAILURE" }}

## Task
{{ task_preview }}

## Timeline

| Step | Duration | Status | Details |
|------|----------|--------|---------|
{% for span in spans -%}
| {{ span.name }} | {{ span.duration_ms }}ms | {{ span.status }} | {{ span.detail }} |
{% endfor %}

## Agent Activity

### Claude Calls: {{ generation_count }}
| Turn | Input Tokens | Output Tokens | Cost | Tools Used |
|------|-------------|---------------|------|------------|
{% for gen in generations -%}
| {{ gen.turn }} | {{ gen.input_tokens }} | {{ gen.output_tokens }} | ${{ gen.cost }} | {{ gen.tools }} |
{% endfor %}

### Total Cost: ${{ total_cost }}

## Skills Injected
{% for skill in skills -%}
- `{{ skill }}`
{% endfor %}

## Key Decisions
{% for decision in decisions -%}
{{ loop.index }}. **{{ decision.tool }}**: {{ decision.summary }}
{% endfor %}

---
*View full trace: [Langfuse UI]({{ langfuse_url }}/trace/{{ trace.id }})*
"""


class ReportGenerator:
    def __init__(self, langfuse: Langfuse, langfuse_url: str):
        self._langfuse = langfuse
        self._langfuse_url = langfuse_url
        self._template = Template(REPORT_TEMPLATE)

    async def generate(self, trace_id: str) -> str:
        """Generate a Markdown report for a single trace."""
        # Fetch trace and all its observations
        trace = self._langfuse.fetch_trace(trace_id)
        observations = self._langfuse.fetch_observations(
            trace_id=trace_id,
        )

        # Separate spans and generations
        spans = [o for o in observations.data if o.type == "SPAN"]
        generations = [o for o in observations.data if o.type == "GENERATION"]

        # Calculate costs
        total_cost = sum(g.calculated_total_cost or 0 for g in generations)

        # Extract decisions from tool-use spans
        decisions = self._extract_decisions(spans)

        # Extract skills from trace metadata
        skills = trace.data.metadata.get("skills_injected", [])

        # Render
        return self._template.render(
            trace=trace.data,
            now=datetime.utcnow().isoformat(),
            duration_s=self._calc_duration(trace.data),
            exit_code=trace.data.metadata.get("exit_code", -1),
            task_preview=trace.data.metadata.get("task_preview", "N/A"),
            spans=self._format_spans(spans),
            generations=self._format_generations(generations),
            generation_count=len(generations),
            total_cost=f"{total_cost:.4f}",
            skills=skills,
            decisions=decisions,
            langfuse_url=self._langfuse_url,
        )

    def _extract_decisions(self, spans):
        """Extract tool-use spans that represent agent decisions."""
        decisions = []
        for s in spans:
            if s.name.startswith("tool:"):
                tool_name = s.name.replace("tool:", "")
                summary = str(s.output)[:150] if s.output else "N/A"
                decisions.append({"tool": tool_name, "summary": summary})
        return decisions

    def _format_spans(self, spans):
        """Format spans for the timeline table."""
        formatted = []
        for s in sorted(spans, key=lambda x: x.start_time or datetime.min):
            duration_ms = 0
            if s.start_time and s.end_time:
                duration_ms = int((s.end_time - s.start_time).total_seconds() * 1000)
            formatted.append({
                "name": s.name,
                "duration_ms": duration_ms,
                "status": s.level or "DEFAULT",
                "detail": str(s.metadata or {}).get("detail", "")[:80],
            })
        return formatted

    def _format_generations(self, generations):
        """Format generations for the Claude calls table."""
        formatted = []
        for i, g in enumerate(sorted(generations, key=lambda x: x.start_time or datetime.min)):
            tools = []
            # Look for child tool spans (would need parent_observation_id matching)
            formatted.append({
                "turn": i + 1,
                "input_tokens": g.usage.input if g.usage else 0,
                "output_tokens": g.usage.output if g.usage else 0,
                "cost": f"{g.calculated_total_cost or 0:.4f}",
                "tools": ", ".join(tools) or "none",
            })
        return formatted

    def _calc_duration(self, trace):
        """Calculate trace duration in seconds."""
        if trace.metadata and "duration_s" in trace.metadata:
            return trace.metadata["duration_s"]
        return "N/A"
```

### 6.3 API Endpoint

```python
# controller/src/controller/api/routes.py

@router.post("/api/v1/reports/{thread_id}")
async def generate_report(thread_id: str, report_gen: ReportGenerator = Depends()):
    # Look up trace_id from job record
    job = await state.get_latest_job(thread_id)
    if not job or not job.langfuse_trace_id:
        raise HTTPException(404, "No trace found for this thread")

    markdown = await report_gen.generate(job.langfuse_trace_id)
    return {"markdown": markdown, "trace_url": f"{LANGFUSE_URL}/trace/{job.langfuse_trace_id}"}
```

---

## 7. Bonus Features (Batteries Included)

These are features you get essentially for free with Langfuse. Building them from scratch would take weeks each.

### 7.1 Cost Tracking

Langfuse automatically calculates costs for Claude API calls when you log generations with model and usage info. The web UI shows:
- Cost per trace (per job)
- Cost trends over time
- Cost breakdown by model
- Cost per user/repo

**Zero custom code required** — just log generations with the `model` and `usage` fields.

### 7.2 Prompt Management

Langfuse has a built-in prompt management system. Instead of hardcoding `build_system_prompt()`, you could version prompts in Langfuse and fetch them at runtime:

```python
from langfuse import Langfuse

langfuse = Langfuse()

# Fetch the latest production version of the system prompt
prompt = langfuse.get_prompt("ditto-system-prompt", label="production")
system_prompt = prompt.compile(
    repo_owner=thread.repo_owner,
    repo_name=thread.repo_name,
    task=task_request.task,
)
```

This enables A/B testing prompts, rolling back bad prompt versions, and tracking which prompt version produced which results — all without code changes.

**Recommendation**: Do NOT adopt this in Phase 1. It adds coupling to Langfuse for a critical path (prompt serving). Adopt it in Phase 3 when Langfuse has proven stable.

### 7.3 Evaluations and Scoring

Langfuse supports attaching **scores** to traces. Use this to close the feedback loop:

```python
# After job completion, score the trace
langfuse.score(
    trace_id=trace_id,
    name="job_success",
    value=1 if result.exit_code == 0 else 0,
)
langfuse.score(
    trace_id=trace_id,
    name="commit_count",
    value=result.commit_count,
)
langfuse.score(
    trace_id=trace_id,
    name="pr_created",
    value=1 if result.pr_url else 0,
)
```

This enables filtering traces by outcome in the UI: "Show me all failed jobs where the agent used the `debug-react` skill."

### 7.4 Dataset Creation from Traces

Langfuse lets you create **datasets** from production traces. This is powerful for:
- Building regression test suites from real tasks
- Creating evaluation benchmarks
- Fine-tuning prompt strategies based on successful traces

```python
# Create a dataset item from a successful trace
langfuse.create_dataset_item(
    dataset_name="successful-tasks",
    input={"task": task_text, "skills": skill_slugs},
    expected_output={"exit_code": 0, "commit_count": 3},
    metadata={"trace_id": trace_id, "source": "production"},
)
```

---

## 8. Pros and Cons

### Pros

| Advantage | Detail |
|---|---|
| **Fastest time-to-value** | `@observe` decorator + Langfuse server = tracing in days, not weeks |
| **Purpose-built for LLMs** | Trace/span/generation hierarchy designed for AI workloads; not repurposed from generic APM |
| **Web UI included** | No need to build dashboards. Trace exploration, filtering, cost analysis out of the box |
| **Cost tracking for free** | Automatic token counting and cost calculation for Claude/Voyage calls |
| **Self-hostable** | Full data sovereignty. No data leaves your infrastructure |
| **Active open-source** | 20k+ GitHub stars, weekly releases, responsive maintainers |
| **Anthropic SDK integration** | First-class support for Claude; `@observe` automatically captures Claude API calls |
| **Evaluation framework** | Built-in scoring, datasets, and evaluation runs — no custom metrics infrastructure |
| **Prompt versioning** | Optional prompt management with rollback and A/B testing |

### Cons

| Disadvantage | Detail | Mitigation |
|---|---|---|
| **Another service to run** | Langfuse = web app + Postgres + optional ClickHouse | Docker Compose; minimal ops. ~1.5 GB RAM total |
| **Vendor coupling (even self-hosted)** | API surface, SDK patterns, data model are all Langfuse-specific | Wrap Langfuse client in a thin adapter; keep decorator usage to boundary functions only |
| **Agent-side tracing is custom** | `claude -p` is a black box; parsing its output into spans is fragile | Start with coarse-grained agent spans; refine parsing incrementally |
| **Cross-process trace linking is manual** | Must explicitly pass `trace_id` through Redis | Well-defined protocol (Section 4); one field addition |
| **No real-time streaming** | Langfuse ingestion is async (batched); traces may lag 5-30s behind reality | Acceptable for post-hoc analysis; use Redis/logs for real-time monitoring |
| **Postgres storage grows** | Full prompt/response capture = significant storage per trace | Retention policies (Section 5.4); truncate large inputs/outputs |
| **SDK version churn** | Langfuse SDK has breaking changes between major versions | Pin SDK version; update deliberately |
| **Report generation is still custom** | Langfuse provides data, but Markdown reports require custom code | ~2 days of effort; well-scoped problem (Section 6) |

### Compared to Alternatives

| Aspect | Custom Postgres Tracing | OpenTelemetry + Jaeger | Langfuse (this approach) |
|---|---|---|---|
| Time to basic tracing | 2-3 weeks | 1-2 weeks | 2-3 days |
| LLM-specific features | Must build everything | Generic; no LLM awareness | Built-in (cost, tokens, prompts) |
| UI/exploration | Must build (or use Grafana) | Jaeger UI (designed for microservices) | Purpose-built LLM trace UI |
| Self-hostable | Yes (it's your code) | Yes | Yes |
| Operational overhead | Low (it's just Postgres) | Medium (Jaeger + collector + storage) | Low-Medium (web + Postgres) |
| Vendor lock-in risk | None | Low (CNCF standard) | Medium (Langfuse-specific API) |
| Agent-side capture | Custom either way | Custom either way | Custom either way |

---

## 9. Implementation Effort

### Phase 1: Foundation (3-4 days)

| Task | Effort | Details |
|---|---|---|
| Deploy self-hosted Langfuse | 0.5 days | Docker Compose, DNS, secrets |
| Add `langfuse` SDK to controller deps | 0.5 hours | `pip install langfuse` |
| Initialize Langfuse client in app startup | 0.5 days | Settings, DI, health check |
| Add `@observe` to orchestrator + classifier | 1 day | 5-8 decorators, metadata attachment |
| Add `langfuse_trace_id` to Redis payload | 0.5 days | One field in push_task, one in entrypoint |
| Verify traces appear in Langfuse UI | 0.5 days | End-to-end smoke test |

**Deliverable**: Controller-side traces visible in Langfuse UI. No agent-side tracing yet.

### Phase 2: Agent Tracing (4-5 days)

| Task | Effort | Details |
|---|---|---|
| Build `agent_tracer.py` wrapper | 2 days | Subprocess management, output parsing |
| Inject Langfuse env vars into pods | 0.5 days | Secret mounting, spawner changes |
| Parse `claude -p` output into spans | 1-2 days | Fragile; start coarse, refine later |
| Verify cross-process trace linking | 0.5 days | One trace spans controller + agent |
| Add scores on job completion | 0.5 days | exit_code, commit_count, pr_created |

**Deliverable**: Full end-to-end traces. Agent tool calls visible as child spans.

### Phase 3: Reports and Polish (3-4 days)

| Task | Effort | Details |
|---|---|---|
| Build ReportGenerator | 1.5 days | Langfuse API queries, Jinja2 template |
| Add `/api/v1/reports/{thread_id}` endpoint | 0.5 days | FastAPI route |
| Add daily report cron (optional) | 0.5 days | Summarize all traces from past 24h |
| Prompt management evaluation | 0.5 days | Decide whether to adopt Langfuse prompts |
| Data retention configuration | 0.5 days | Set up trace/generation TTLs |
| Documentation | 0.5 days | Runbook for Langfuse ops, trace guide |

**Deliverable**: Markdown reports generated from traces. Engineers can review agent activity.

### Total: 10-13 days

For comparison:
- Custom Postgres tracing (approach with no external service): ~15-20 days, and you still don't have a UI
- OpenTelemetry + Jaeger: ~12-15 days, and the UI isn't designed for LLM traces

---

## 10. Code Examples

### Example 1: Instrumented Orchestrator

```python
# controller/src/controller/orchestrator.py

from langfuse.decorators import observe, langfuse_context
import uuid

class Orchestrator:
    """Main orchestrator with Langfuse tracing."""

    @observe(name="orchestrate")
    async def _spawn_job(self, thread, task_request, is_retry=False, retry_count=0):
        trace_id = uuid.uuid4().hex

        langfuse_context.update_current_trace(
            id=trace_id,
            name=f"job:{thread.id[:8]}",
            user_id=thread.repo_owner,
            session_id=thread.id,  # groups retries under same session
            metadata={
                "thread_id": thread.id,
                "repo": f"{thread.repo_owner}/{thread.repo_name}",
                "source": task_request.source,
                "task_preview": task_request.task[:200],
                "is_retry": is_retry,
                "retry_count": retry_count,
            },
            tags=["ditto-factory", task_request.source],
        )

        # Classification (auto-traced via @observe on classifier.classify)
        if self._settings.skill_registry_enabled:
            classification = await self._classifier.classify(
                task=task_request.task,
                language=self._detect_language(thread),
                domain=task_request.source_ref.get("labels", []),
            )
            matched_skills = classification.skills
            resolved = await self._resolver.resolve(
                skills=matched_skills,
                default_image=self._settings.agent_image,
            )
            agent_image = resolved.image
        else:
            matched_skills = []
            agent_image = self._settings.agent_image

        # Inject skills
        skills_payload = self._injector.format_for_redis(matched_skills)

        # Push to Redis with trace_id
        await self._redis.push_task(thread.id, {
            "task": task_request.task,
            "system_prompt": system_prompt,
            "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
            "branch": branch,
            "skills": skills_payload,
            "langfuse_trace_id": trace_id,
        })

        job_name = self._spawner.spawn(
            thread_id=thread.id,
            github_token="",
            redis_url=self._settings.redis_url,
            agent_image=agent_image,
        )

        # Update trace with job metadata
        langfuse_context.update_current_observation(
            metadata={
                "k8s_job_name": job_name,
                "agent_image": agent_image,
                "skills_injected": [s.slug for s in matched_skills],
            },
        )

        return job_name
```

### Example 2: Instrumented Task Classifier

```python
# controller/src/controller/skills/classifier.py

from langfuse.decorators import observe, langfuse_context
from dataclasses import dataclass

@dataclass
class ClassificationResult:
    skills: list  # List[Skill]
    task_embedding: list[float] | None
    confidence_scores: dict[str, float]

class TaskClassifier:
    """Classifies tasks and selects relevant skills."""

    @observe(name="classify_task")
    async def classify(
        self,
        task: str,
        language: list[str] | None = None,
        domain: list[str] | None = None,
    ) -> ClassificationResult:
        # Step 1: Generate task embedding
        embedding = await self._embed(task)

        # Step 2: Search skills by similarity
        candidates = await self._registry.search_by_embedding(
            embedding=embedding,
            language=language,
            domain=domain,
            min_similarity=self._settings.skill_min_similarity,
            max_results=20,
        )

        # Step 3: Apply performance boost
        scored = []
        for skill, similarity in candidates:
            boosted = await self._tracker.compute_boost(skill.id, similarity)
            scored.append((skill, boosted))

        # Step 4: Select top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        selected = scored[:self._settings.max_skills]

        # Log classification details to Langfuse
        langfuse_context.update_current_observation(
            metadata={
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "language_filter": language,
                "domain_filter": domain,
                "confidence_scores": {
                    s.slug: round(score, 4) for s, score in selected
                },
            },
        )

        return ClassificationResult(
            skills=[s for s, _ in selected],
            task_embedding=embedding,
            confidence_scores={s.slug: score for s, score in selected},
        )

    @observe(name="embed_task", as_type="generation")
    async def _embed(self, text: str) -> list[float]:
        """Call Voyage-3 to embed the task description."""
        result = await self._embedding_provider.embed(text)

        # Langfuse automatically captures this as a "generation" with cost
        langfuse_context.update_current_observation(
            model="voyage-3",
            usage={"input": len(text.split())},  # approximate tokens
        )

        return result
```

### Example 3: Job Completion with Scoring

```python
# controller/src/controller/orchestrator.py

from langfuse import Langfuse

class Orchestrator:

    @observe(name="handle_completion")
    async def handle_job_completion(self, thread_id: str, job: Job):
        """Process completed agent job and record outcomes."""

        # Fetch result from Redis
        result = await self._monitor.wait_for_result(thread_id, timeout=0)
        if result is None:
            return

        # Record performance metrics (existing)
        if self._settings.skill_registry_enabled:
            await self._tracker.record_outcome(
                thread_id=thread_id,
                job_id=job.id,
                result=result,
            )

        # Score the trace in Langfuse
        trace_id = job.task_context.get("langfuse_trace_id")
        if trace_id:
            # Numeric scores for quantitative analysis
            self._langfuse.score(
                trace_id=trace_id,
                name="exit_code",
                value=result.exit_code,
                comment="0 = success, non-zero = failure",
            )
            self._langfuse.score(
                trace_id=trace_id,
                name="commit_count",
                value=result.commit_count,
            )
            self._langfuse.score(
                trace_id=trace_id,
                name="success",
                value=1.0 if result.exit_code == 0 else 0.0,
                data_type="NUMERIC",
            )

            # Categorical score for quick filtering
            if result.exit_code == 0 and result.commit_count > 0:
                outcome = "productive"  # succeeded and made changes
            elif result.exit_code == 0:
                outcome = "no-op"  # succeeded but no commits
            else:
                outcome = "failure"

            self._langfuse.score(
                trace_id=trace_id,
                name="outcome_category",
                value=outcome,
                data_type="CATEGORICAL",
            )

        # Update job state
        job.status = JobStatus.COMPLETED if result.exit_code == 0 else JobStatus.FAILED
        job.result = {
            "branch": result.branch,
            "exit_code": result.exit_code,
            "commit_count": result.commit_count,
        }
        await self._state.update_job(job)

        # Run safety pipeline
        await self._safety.check(thread_id, result)

        # Generate report (if configured)
        if self._settings.auto_report and trace_id:
            report = await self._report_gen.generate(trace_id)
            await self._post_report(thread_id, report)
```

---

## ADR-004: Langfuse as Tracing Backend over Custom Implementation

### Status
Proposed

### Context
Ditto Factory needs end-to-end observability for agent jobs: reasoning capture, tool call tracing, cost tracking, and report generation. Three options were considered:

1. **Custom Postgres tracing** — full control, no external dependencies, but 15-20 days to build and no UI
2. **OpenTelemetry + Jaeger** — industry standard, but designed for microservice tracing, not LLM workloads
3. **Langfuse (self-hosted)** — purpose-built for LLM observability, open-source, batteries included

### Decision
Use self-hosted Langfuse as the primary tracing backend. Instrument the controller with `@observe` decorators and propagate trace IDs through Redis to agent pods. Build a custom report generator that queries the Langfuse API.

### Consequences
- **Easier**: Tracing, cost tracking, prompt management, evaluation scoring, and trace exploration are all provided out of the box. Estimated 10-13 days vs 15-20 days for custom.
- **Harder**: Another service to operate (Docker Compose). SDK version upgrades require attention. Agent-side tracing is still custom regardless of backend choice. Medium vendor coupling to Langfuse's API surface.
- **Reversible**: The `@observe` decorator is lightweight and can be replaced with OpenTelemetry spans if Langfuse proves insufficient. The trace ID propagation protocol through Redis is backend-agnostic.
