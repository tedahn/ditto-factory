# Approach C: Event Sourcing with Redis Streams for Agent Tracing

> **Status**: Proposed
> **Date**: 2026-03-21
> **Author**: Software Architect Agent

---

## 1. Architecture Overview

### The Core Idea

Every meaningful action in the Ditto Factory pipeline — from task receipt to final report — emits an **immutable event** to a Redis Stream. The event log IS the source of truth. All other views (timelines, dashboards, review documents) are **derived** by replaying or consuming the stream.

This is event sourcing applied to agent observability. We are not retrofitting logging onto an existing system — we are making the trace a first-class architectural primitive.

### Why Event Sourcing Fits Here

1. **The problem is temporal.** We need to answer "what happened, in what order, and why?" That is exactly what an ordered, immutable event log gives you.
2. **Redis is already the backbone.** The controller pushes tasks to Redis (`task:{thread_id}`), agent pods write results to Redis (`result:{thread_id}`). Adding Redis Streams is zero new infrastructure.
3. **Multiple consumers, different views.** A timeline view, a decision tree, a Markdown report, a metrics dashboard — these are all projections of the same event stream. CQRS makes this natural.
4. **Replay is a superpower.** Bug in the report generator? Fix the materializer, replay the stream, regenerate. No data loss, no re-running agents.

### Architecture Diagram

```
                            WRITE PATH (Events)
                            ==================

  ┌──────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
  │ Webhook/ │───>│ Orchestrator │───>│ TaskClassi- │───>│ Skill    │
  │ API      │    │              │    │ fier        │    │ Injector │
  └──────────┘    └──────┬───────┘    └──────┬──────┘    └────┬─────┘
                         │                   │                │
                    emit │              emit │           emit │
                         ▼                   ▼                ▼
                  ┌─────────────────────────────────────────────────┐
                  │         Redis Stream: traces:{trace_id}         │
                  │                                                 │
                  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐     │
                  │  │ev-1 │ │ev-2 │ │ev-3 │ │ev-4 │ │ev-5 │ ... │
                  │  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘     │
                  └──────────────────┬──────────────────────────────┘
                                     │
  ┌──────────┐                       │            ┌──────────────┐
  │ Agent    │───emit events────────>│            │ Agent Pod    │
  │ Pod (K8s)│  (via Redis client)   │            │ (stderr/log) │
  └──────────┘                       │            └──────────────┘
                                     │
                            READ PATH (Materialization)
                            ===========================
                                     │
                         ┌───────────┼───────────┐
                         ▼           ▼           ▼
                  ┌────────┐  ┌──────────┐  ┌──────────┐
                  │SQLite  │  │Markdown  │  │Metrics   │
                  │Read    │  │Report    │  │Aggregator│
                  │Model   │  │Generator │  │          │
                  └────────┘  └──────────┘  └──────────┘
                       │           │              │
                       ▼           ▼              ▼
                  Query API   ./reports/     Prometheus/
                  (FastAPI)   {trace}.md     Grafana
```

### Event Flow Per Job

```
TaskReceived ──> SkillsClassified ──> SkillsInjected ──> AgentSpawned
                                                              │
                                                              ▼
                                                    ┌─── Agent Pod ───┐
                                                    │ ReasoningStep   │
                                                    │ ToolInvoked     │
                                                    │ FileModified    │
                                                    │ ErrorEncountered│
                                                    │ ReasoningStep   │
                                                    │ ToolInvoked     │
                                                    └────────┬────────┘
                                                              │
AgentCompleted <── SafetyChecked <── PRCreated <──────────────┘
       │
       ▼
 ReportGenerated
```

---

## 2. Event Schema

### Base Event Envelope

Every event shares this structure:

```python
@dataclass
class TraceEvent:
    """Immutable event in the trace stream."""
    event_id: str           # Redis Stream auto-generated ID (timestamp-seq)
    trace_id: str           # Correlation ID = thread_id (one trace per task)
    event_type: str         # e.g., "TaskReceived", "ToolInvoked"
    timestamp: str          # ISO 8601, from the emitting process
    source: str             # Which component: "orchestrator", "classifier", "agent-pod"
    payload: dict           # Event-type-specific data
    parent_event_id: str | None = None  # For causal chains (optional)
    sequence: int = 0       # Monotonic counter within a trace (set by emitter)
```

### Event Catalog

| Event Type | Source | Payload Fields | When Emitted |
|---|---|---|---|
| `TaskReceived` | orchestrator | `task`, `source`, `source_ref`, `repo_owner`, `repo_name`, `thread_id` | `_spawn_job()` entry |
| `SkillsClassified` | classifier | `matched_skills[]` (slug, similarity, success_rate), `agent_type`, `reasoning`, `method` (tag/semantic/llm) | After `classifier.classify()` |
| `SkillsInjected` | injector | `skill_slugs[]`, `total_bytes`, `injection_method` | After skills appended to system prompt |
| `AgentSpawned` | spawner | `job_name`, `agent_image`, `k8s_namespace`, `branch`, `redis_key` | After K8s job created |
| `AgentStarted` | agent-pod | `pod_name`, `node_name`, `image_sha` | `entrypoint.sh` start |
| `ReasoningStep` | agent-pod | `step_index`, `summary`, `token_count`, `duration_ms` | During `claude -p` execution (parsed from output) |
| `ToolInvoked` | agent-pod | `tool_name`, `tool_input_summary`, `duration_ms`, `success` | During agent execution |
| `FileModified` | agent-pod | `file_path`, `change_type` (create/modify/delete), `lines_changed` | After each file write |
| `CommitCreated` | agent-pod | `commit_sha`, `commit_message`, `files_changed[]` | After each `git commit` |
| `ErrorEncountered` | agent-pod | `error_type`, `message`, `recoverable`, `retry_count` | On any error |
| `AgentCompleted` | agent-pod | `exit_code`, `commit_count`, `total_duration_ms`, `token_usage` | `entrypoint.sh` end |
| `PRCreated` | safety | `pr_url`, `pr_number`, `base_branch`, `head_branch` | After PR creation |
| `SafetyChecked` | safety | `checks_passed[]`, `checks_failed[]`, `auto_approved` | After safety pipeline |
| `ReportGenerated` | materializer | `report_path`, `format`, `event_count` | After report materialization |
| `ResultReported` | orchestrator | `integration` (slack/linear/github), `message_id` | After notifying source |

### Payload Examples

```python
# TaskReceived
{
    "event_type": "TaskReceived",
    "source": "orchestrator",
    "payload": {
        "task": "Fix the login form validation on mobile",
        "source": "slack",
        "source_ref": {"channel": "C123", "ts": "1711036800.001"},
        "repo_owner": "acme",
        "repo_name": "web-app",
        "thread_id": "abc123def456"
    }
}

# SkillsClassified
{
    "event_type": "SkillsClassified",
    "source": "classifier",
    "payload": {
        "matched_skills": [
            {"slug": "debug-react", "similarity": 0.87, "success_rate": 0.92},
            {"slug": "mobile-responsive", "similarity": 0.73, "success_rate": 0.85}
        ],
        "agent_type": "frontend",
        "reasoning": "Task mentions 'login form' and 'mobile' — matched frontend skills",
        "method": "semantic",
        "classification_ms": 142
    }
}

# ToolInvoked (from agent pod)
{
    "event_type": "ToolInvoked",
    "source": "agent-pod",
    "payload": {
        "tool_name": "Read",
        "tool_input_summary": "src/components/LoginForm.tsx (lines 1-50)",
        "duration_ms": 12,
        "success": true
    }
}
```

---

## 3. Instrumentation Strategy

### Principle: Emit at Boundaries, Not Everywhere

We instrument at **component boundaries** — the points where control passes from one module to another. This gives us full coverage without drowning in noise.

### Controller-Side Instrumentation

The controller already has clear boundaries (orchestrator -> classifier -> injector -> spawner). We add a `TraceEmitter` that each component receives via dependency injection.

```python
# controller/src/controller/tracing/emitter.py

import json
import time
import uuid
from dataclasses import asdict
from redis.asyncio import Redis


class TraceEmitter:
    """Emits immutable events to Redis Streams."""

    def __init__(self, redis: Redis):
        self._redis = redis
        self._sequence_counters: dict[str, int] = {}

    def _next_seq(self, trace_id: str) -> int:
        self._sequence_counters.setdefault(trace_id, 0)
        self._sequence_counters[trace_id] += 1
        return self._sequence_counters[trace_id]

    async def emit(
        self,
        trace_id: str,
        event_type: str,
        source: str,
        payload: dict,
        parent_event_id: str | None = None,
    ) -> str:
        """
        Append an event to the trace stream.
        Returns the Redis Stream entry ID.
        """
        stream_key = f"traces:{trace_id}"
        event_data = {
            "trace_id": trace_id,
            "event_type": event_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "source": source,
            "sequence": self._next_seq(trace_id),
            "payload": json.dumps(payload),
        }
        if parent_event_id:
            event_data["parent_event_id"] = parent_event_id

        # XADD returns the auto-generated stream ID (e.g., "1711036800001-0")
        entry_id = await self._redis.xadd(stream_key, event_data)
        return entry_id


# Convenience methods for type safety
class OrchestratorTracer:
    """Typed event emitter for the orchestrator component."""

    def __init__(self, emitter: TraceEmitter):
        self._emitter = emitter

    async def task_received(self, trace_id: str, task_request) -> str:
        return await self._emitter.emit(
            trace_id=trace_id,
            event_type="TaskReceived",
            source="orchestrator",
            payload={
                "task": task_request.task,
                "source": task_request.source,
                "source_ref": task_request.source_ref,
                "repo_owner": task_request.repo_owner,
                "repo_name": task_request.repo_name,
                "thread_id": task_request.thread_id,
            },
        )

    async def agent_spawned(self, trace_id: str, job_name: str, branch: str, agent_image: str = "default") -> str:
        return await self._emitter.emit(
            trace_id=trace_id,
            event_type="AgentSpawned",
            source="orchestrator",
            payload={
                "job_name": job_name,
                "agent_image": agent_image,
                "branch": branch,
            },
        )
```

### Wiring Into the Orchestrator

```python
# controller/src/controller/orchestrator.py — modified _spawn_job()

async def _spawn_job(
    self,
    thread: Thread,
    task_request: TaskRequest,
    is_retry: bool = False,
    retry_count: int = 0,
) -> None:
    thread_id = thread.id
    trace_id = thread_id  # One trace per task dispatch

    # --- TRACE: Task received ---
    await self._tracer.task_received(trace_id, task_request)

    # --- Existing: Build system prompt ---
    system_prompt = build_system_prompt(...)
    claude_md = await self._load_claude_md(...)

    # --- Classification (if enabled) ---
    if self._settings.skill_registry_enabled:
        classification = await self._classifier.classify(task_request)

        # --- TRACE: Skills classified ---
        await self._tracer.skills_classified(trace_id, classification)

        # --- Injection ---
        system_prompt = self._injector.inject(system_prompt, classification.skills)

        # --- TRACE: Skills injected ---
        await self._tracer.skills_injected(
            trace_id,
            skill_slugs=[s.slug for s in classification.skills],
            total_bytes=len(system_prompt),
        )

    # --- Push task to Redis (existing) ---
    branch = f"df/{thread_id[:8]}/{uuid.uuid4().hex[:8]}"
    await self._redis.push_task(thread_id, {
        "task": task_request.task,
        "system_prompt": system_prompt,
        "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
        "branch": branch,
    })

    # --- Spawn K8s Job (existing) ---
    job_name = self._spawner.spawn(
        thread_id=thread_id,
        github_token="",
        redis_url=self._settings.redis_url,
    )

    # --- TRACE: Agent spawned ---
    await self._tracer.agent_spawned(trace_id, job_name, branch)

    # --- Track job (existing) ---
    job = Job(...)
    await self._state.create_job(job)
```

---

## 4. Cross-Process Events: Agent Pod Instrumentation

This is the hardest part. The agent pod runs `claude -p` as a subprocess. We cannot instrument Claude's internals, but we CAN:

1. **Parse Claude's output** in real-time and emit events
2. **Wrap git operations** to capture commits
3. **Emit lifecycle events** from `entrypoint.sh`

### Strategy: Sidecar Emitter Script

The agent pod runs a lightweight Python script alongside `claude -p` that:
- Tails Claude's stderr/stdout for tool invocations and reasoning markers
- Watches the git log for new commits
- Emits events to the Redis Stream

```python
#!/usr/bin/env python3
# agent/trace_emitter.py — runs inside agent pod

import asyncio
import json
import os
import subprocess
import sys
import time
import redis.asyncio as redis


TRACE_ID = os.environ["THREAD_ID"]
REDIS_URL = os.environ["REDIS_URL"]
STREAM_KEY = f"traces:{TRACE_ID}"


async def emit_event(r: redis.Redis, event_type: str, payload: dict, seq: int) -> str:
    """Emit a single event to the trace stream."""
    return await r.xadd(STREAM_KEY, {
        "trace_id": TRACE_ID,
        "event_type": event_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "source": "agent-pod",
        "sequence": seq,
        "payload": json.dumps(payload),
    })


async def run_agent_with_tracing():
    """Run claude -p and emit trace events from its output."""
    r = redis.from_url(REDIS_URL)
    seq = 100  # Agent events start at sequence 100 to avoid controller collision

    # Emit AgentStarted
    await emit_event(r, "AgentStarted", {
        "pod_name": os.environ.get("HOSTNAME", "unknown"),
        "trace_id": TRACE_ID,
    }, seq)
    seq += 1

    # Build claude command
    claude_args = sys.argv[1:]  # Passed from entrypoint.sh
    proc = await asyncio.create_subprocess_exec(
        "claude", *claude_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Parse output lines for tool invocations and reasoning
    step_index = 0
    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").strip()

        # Detect tool invocations (Claude CLI outputs tool use markers)
        if "Tool:" in text or "tool_use" in text:
            tool_name = _parse_tool_name(text)
            await emit_event(r, "ToolInvoked", {
                "tool_name": tool_name,
                "tool_input_summary": text[:200],
                "success": True,
            }, seq)
            seq += 1

        # Detect reasoning/thinking blocks
        elif text.startswith("Thinking:") or "reasoning" in text.lower():
            step_index += 1
            await emit_event(r, "ReasoningStep", {
                "step_index": step_index,
                "summary": text[:500],
            }, seq)
            seq += 1

    # Wait for process to complete
    await proc.wait()

    # Capture git commits made during execution
    commits = _get_new_commits()
    for commit in commits:
        await emit_event(r, "CommitCreated", {
            "commit_sha": commit["sha"],
            "commit_message": commit["message"],
            "files_changed": commit["files"],
        }, seq)
        seq += 1

    # Emit AgentCompleted
    stderr_output = (await proc.stderr.read()).decode("utf-8", errors="replace")
    await emit_event(r, "AgentCompleted", {
        "exit_code": proc.returncode,
        "commit_count": len(commits),
        "stderr": stderr_output[:2000],
    }, seq)

    await r.aclose()
    return proc.returncode


def _parse_tool_name(line: str) -> str:
    """Extract tool name from Claude CLI output."""
    # Claude CLI formats: "Tool: Read /path/to/file" or similar
    if "Tool:" in line:
        return line.split("Tool:")[1].strip().split()[0]
    return "unknown"


def _get_new_commits() -> list[dict]:
    """Get commits created during this agent session."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--format=%H|%s", "origin/main..HEAD"],
            capture_output=True, text=True, cwd=os.environ.get("REPO_DIR", ".")
        )
        commits = []
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                sha, msg = line.split("|", 1)
                # Get files changed in this commit
                files_result = subprocess.run(
                    ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
                    capture_output=True, text=True, cwd=os.environ.get("REPO_DIR", ".")
                )
                commits.append({
                    "sha": sha,
                    "message": msg,
                    "files": files_result.stdout.strip().split("\n"),
                })
        return commits
    except Exception:
        return []


if __name__ == "__main__":
    exit_code = asyncio.run(run_agent_with_tracing())
    sys.exit(exit_code)
```

### Modified entrypoint.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

# ... existing env validation and task fetch ...

# Instead of running claude directly, run through the trace emitter
python3 /opt/ditto/trace_emitter.py \
    -p "$TASK" \
    --allowedTools '*' \
    --mcp-config "$MCP_CONFIG" \
    ${SYSTEM_PROMPT:+--system-prompt "$SYSTEM_PROMPT"}

EXIT_CODE=$?

# ... existing result publishing to Redis ...
# The trace_emitter already emitted AgentCompleted,
# but we still publish to result:{thread_id} for backward compatibility
```

### What We Cannot Capture (Honestly)

- **Token-level reasoning**: Claude's internal chain-of-thought is not exposed unless the CLI provides it. We capture what the CLI outputs, which varies by version.
- **MCP server interactions**: If the agent uses MCP tools, we see the tool invocation but not the MCP server's internal processing.
- **Real-time streaming**: Events are emitted after each line of output. There is no sub-second granularity for long-running tool calls.

This is acceptable. We are building an observability system, not a debugger. The event granularity is sufficient for review documents and performance analysis.

---

## 5. Materialization: From Events to Views

### The Materializer Process

A separate async process (running in the controller) consumes trace streams and builds read models. It uses Redis consumer groups for reliable, at-least-once processing.

```python
# controller/src/controller/tracing/materializer.py

import asyncio
import json
import logging
import time
from pathlib import Path

import aiosqlite
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class TraceMaterializer:
    """
    Consumes trace events from Redis Streams and builds:
    1. SQLite read model (for queries and dashboards)
    2. Markdown reports (for human review)
    """

    CONSUMER_GROUP = "materializer"
    CONSUMER_NAME = "materializer-1"
    BATCH_SIZE = 50
    BLOCK_MS = 5000  # Wait up to 5s for new events

    def __init__(
        self,
        redis: Redis,
        db_path: str = "traces.db",
        reports_dir: str = "./reports",
    ):
        self._redis = redis
        self._db_path = db_path
        self._reports_dir = Path(reports_dir)
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Create SQLite schema for materialized views."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS trace_events (
                    stream_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    parent_event_id TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_events_trace
                    ON trace_events(trace_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_events_type
                    ON trace_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                    ON trace_events(timestamp);

                -- Materialized summary per trace
                CREATE TABLE IF NOT EXISTS trace_summaries (
                    trace_id TEXT PRIMARY KEY,
                    task TEXT,
                    source TEXT,
                    repo TEXT,
                    agent_type TEXT,
                    skills_used TEXT,  -- JSON array
                    status TEXT,       -- "running" | "completed" | "failed"
                    event_count INTEGER DEFAULT 0,
                    commit_count INTEGER DEFAULT 0,
                    tool_invocations INTEGER DEFAULT 0,
                    total_duration_ms INTEGER,
                    started_at TEXT,
                    completed_at TEXT,
                    report_path TEXT
                );

                -- Per-tool metrics (aggregated across traces)
                CREATE TABLE IF NOT EXISTS tool_metrics (
                    tool_name TEXT PRIMARY KEY,
                    invocation_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0,
                    last_used TEXT
                );
            """)

    async def run(self):
        """Main loop: consume events and materialize views."""
        # Ensure consumer group exists for all active streams
        # We discover streams dynamically
        logger.info("TraceMaterializer started, consuming events...")

        while True:
            try:
                # Discover active trace streams
                active_streams = await self._discover_streams()
                if not active_streams:
                    await asyncio.sleep(1)
                    continue

                # Ensure consumer groups exist
                for stream_key in active_streams:
                    await self._ensure_consumer_group(stream_key)

                # Read from all active streams
                streams_dict = {s: ">" for s in active_streams}
                results = await self._redis.xreadgroup(
                    groupname=self.CONSUMER_GROUP,
                    consumername=self.CONSUMER_NAME,
                    streams=streams_dict,
                    count=self.BATCH_SIZE,
                    block=self.BLOCK_MS,
                )

                if not results:
                    continue

                # Process each batch of events
                for stream_key, messages in results:
                    for msg_id, fields in messages:
                        await self._process_event(stream_key, msg_id, fields)
                        # ACK the message
                        await self._redis.xack(
                            stream_key, self.CONSUMER_GROUP, msg_id
                        )

            except Exception as e:
                logger.error(f"Materializer error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _discover_streams(self) -> list[str]:
        """Find all traces:* streams in Redis."""
        cursor = 0
        streams = []
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match="traces:*", count=100
            )
            streams.extend(keys)
            if cursor == 0:
                break
        return streams

    async def _ensure_consumer_group(self, stream_key: str):
        """Create consumer group if it doesn't exist."""
        try:
            await self._redis.xgroup_create(
                stream_key, self.CONSUMER_GROUP, id="0", mkstream=True
            )
        except Exception:
            pass  # Group already exists

    async def _process_event(self, stream_key: str, msg_id: str, fields: dict):
        """Process a single event: store in SQLite + update summaries."""
        trace_id = fields.get("trace_id", "")
        event_type = fields.get("event_type", "")
        payload_str = fields.get("payload", "{}")
        payload = json.loads(payload_str)

        # 1. Store raw event in SQLite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO trace_events
                   (stream_id, trace_id, event_type, timestamp, source, sequence, payload, parent_event_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id, trace_id, event_type,
                    fields.get("timestamp", ""),
                    fields.get("source", ""),
                    int(fields.get("sequence", 0)),
                    payload_str,
                    fields.get("parent_event_id"),
                ),
            )

            # 2. Update materialized summary
            await self._update_summary(db, trace_id, event_type, payload)

            await db.commit()

        # 3. If this is a terminal event, generate the Markdown report
        if event_type in ("AgentCompleted", "ResultReported"):
            await self._generate_report(trace_id)

    async def _update_summary(
        self, db: aiosqlite.Connection, trace_id: str, event_type: str, payload: dict
    ):
        """Update the trace_summaries table based on event type."""

        # Ensure row exists
        await db.execute(
            """INSERT OR IGNORE INTO trace_summaries (trace_id, status, event_count)
               VALUES (?, 'running', 0)""",
            (trace_id,),
        )

        # Increment event count
        await db.execute(
            "UPDATE trace_summaries SET event_count = event_count + 1 WHERE trace_id = ?",
            (trace_id,),
        )

        # Type-specific updates
        if event_type == "TaskReceived":
            await db.execute(
                """UPDATE trace_summaries
                   SET task = ?, source = ?, repo = ?, started_at = ?
                   WHERE trace_id = ?""",
                (
                    payload.get("task", ""),
                    payload.get("source", ""),
                    f"{payload.get('repo_owner', '')}/{payload.get('repo_name', '')}",
                    payload.get("timestamp", ""),
                    trace_id,
                ),
            )

        elif event_type == "SkillsClassified":
            skills = [s["slug"] for s in payload.get("matched_skills", [])]
            await db.execute(
                "UPDATE trace_summaries SET agent_type = ?, skills_used = ? WHERE trace_id = ?",
                (payload.get("agent_type", ""), json.dumps(skills), trace_id),
            )

        elif event_type == "ToolInvoked":
            await db.execute(
                "UPDATE trace_summaries SET tool_invocations = tool_invocations + 1 WHERE trace_id = ?",
                (trace_id,),
            )

        elif event_type == "AgentCompleted":
            await db.execute(
                """UPDATE trace_summaries
                   SET status = ?, commit_count = ?, completed_at = ?,
                       total_duration_ms = ?
                   WHERE trace_id = ?""",
                (
                    "completed" if payload.get("exit_code", 1) == 0 else "failed",
                    payload.get("commit_count", 0),
                    payload.get("timestamp", ""),
                    payload.get("total_duration_ms"),
                    trace_id,
                ),
            )

    async def _generate_report(self, trace_id: str):
        """Generate a Markdown report from all events in a trace."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM trace_events
                   WHERE trace_id = ?
                   ORDER BY sequence ASC""",
                (trace_id,),
            )
            events = await cursor.fetchall()

            cursor = await db.execute(
                "SELECT * FROM trace_summaries WHERE trace_id = ?",
                (trace_id,),
            )
            summary = await cursor.fetchone()

        if not summary or not events:
            return

        report = self._render_markdown(trace_id, summary, events)
        report_path = self._reports_dir / f"{trace_id}.md"
        report_path.write_text(report)

        # Update report path in summary
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE trace_summaries SET report_path = ? WHERE trace_id = ?",
                (str(report_path), trace_id),
            )
            await db.commit()

        logger.info(f"Report generated: {report_path}")

    def _render_markdown(self, trace_id: str, summary, events: list) -> str:
        """Render a trace into a human-readable Markdown document."""
        lines = []
        lines.append(f"# Agent Trace Report: {trace_id[:12]}")
        lines.append("")
        lines.append(f"**Task**: {summary['task']}")
        lines.append(f"**Source**: {summary['source']}")
        lines.append(f"**Repository**: {summary['repo']}")
        lines.append(f"**Agent Type**: {summary['agent_type'] or 'general'}")
        lines.append(f"**Status**: {summary['status']}")
        lines.append(f"**Skills Used**: {summary['skills_used'] or '[]'}")
        lines.append(f"**Duration**: {summary['total_duration_ms'] or '?'}ms")
        lines.append(f"**Commits**: {summary['commit_count']}")
        lines.append(f"**Tool Invocations**: {summary['tool_invocations']}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Timeline")
        lines.append("")

        for event in events:
            payload = json.loads(event["payload"])
            event_type = event["event_type"]
            timestamp = event["timestamp"]
            source = event["source"]

            lines.append(f"### {event['sequence']}. {event_type}")
            lines.append(f"*{timestamp} | {source}*")
            lines.append("")

            # Format based on event type
            if event_type == "TaskReceived":
                lines.append(f"> {payload.get('task', '')}")
            elif event_type == "SkillsClassified":
                skills = payload.get("matched_skills", [])
                lines.append(f"**Method**: {payload.get('method', '?')}")
                lines.append(f"**Agent Type**: {payload.get('agent_type', '?')}")
                for s in skills:
                    lines.append(f"- `{s['slug']}` (similarity: {s.get('similarity', '?')}, success: {s.get('success_rate', '?')})")
                if payload.get("reasoning"):
                    lines.append(f"\n*Reasoning*: {payload['reasoning']}")
            elif event_type == "ToolInvoked":
                lines.append(f"**Tool**: `{payload.get('tool_name', '?')}`")
                lines.append(f"**Input**: {payload.get('tool_input_summary', '')[:200]}")
                lines.append(f"**Duration**: {payload.get('duration_ms', '?')}ms")
            elif event_type == "ReasoningStep":
                lines.append(f"> {payload.get('summary', '')[:500]}")
            elif event_type == "CommitCreated":
                lines.append(f"**Commit**: `{payload.get('commit_sha', '?')[:8]}`")
                lines.append(f"**Message**: {payload.get('commit_message', '')}")
                files = payload.get("files_changed", [])
                if files:
                    lines.append("**Files**:")
                    for f in files[:10]:
                        lines.append(f"  - `{f}`")
            elif event_type == "ErrorEncountered":
                lines.append(f"**Error**: {payload.get('error_type', '?')}")
                lines.append(f"**Message**: {payload.get('message', '')}")
                lines.append(f"**Recoverable**: {payload.get('recoverable', '?')}")
            elif event_type == "AgentCompleted":
                lines.append(f"**Exit Code**: {payload.get('exit_code', '?')}")
                lines.append(f"**Commits**: {payload.get('commit_count', 0)}")
            else:
                # Generic payload dump
                for k, v in payload.items():
                    lines.append(f"- **{k}**: {v}")

            lines.append("")

        lines.append("---")
        lines.append(f"*Generated from {len(events)} events*")
        return "\n".join(lines)
```

### Starting the Materializer

```python
# controller/src/controller/main.py — add to FastAPI lifespan

from controller.tracing.materializer import TraceMaterializer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup ...

    # Start trace materializer as background task
    materializer = TraceMaterializer(
        redis=app.state.redis,
        db_path=settings.traces_db_path,
        reports_dir=settings.reports_dir,
    )
    await materializer.initialize()
    materializer_task = asyncio.create_task(materializer.run())

    yield

    # ... existing shutdown ...
    materializer_task.cancel()
```

---

## 6. Storage Strategy

### Redis Streams (Hot — Write Path)

| Concern | Decision |
|---------|----------|
| **Key format** | `traces:{trace_id}` — one stream per task/thread |
| **Retention** | `MAXLEN ~1000` per stream (cap at 1000 events per trace). Most traces will have 20-100 events |
| **TTL** | Expire streams 7 days after last write: `EXPIRE traces:{trace_id} 604800` |
| **Memory estimate** | ~500 bytes per event * 50 events avg * 100 concurrent traces = ~2.5 MB. Negligible |
| **Consumer groups** | One group: `materializer`. Enables at-least-once delivery |
| **Persistence** | Redis RDB/AOF covers crash recovery. Events are also materialized to SQLite |

### SQLite (Warm — Read Path)

| Table | Purpose | Growth Rate |
|-------|---------|-------------|
| `trace_events` | Full event history, queryable | ~50 rows per trace. Vacuum weekly |
| `trace_summaries` | One row per trace, pre-aggregated | ~1 row per task. Compact |
| `tool_metrics` | Aggregated tool performance | ~50 rows total. Tiny |

SQLite is the right choice here because:
- The controller is a single-process application (no write contention)
- Read queries are simple (filter by trace_id, sort by sequence)
- No need for a separate database server
- Already used for skill metrics (`PerformanceTracker`)

### Markdown Reports (Cold — Human-Readable)

| Concern | Decision |
|---------|----------|
| **Location** | `./reports/{trace_id}.md` (configurable) |
| **Generation** | Automatic on `AgentCompleted` event |
| **Retention** | Keep forever (they are tiny — ~5-20 KB each) |
| **Access** | Served via FastAPI static file route or linked in Slack/Linear responses |

### Example Report Output

```markdown
# Agent Trace Report: abc123def456

**Task**: Fix the login form validation on mobile
**Source**: slack
**Repository**: acme/web-app
**Agent Type**: frontend
**Status**: completed
**Skills Used**: ["debug-react", "mobile-responsive"]
**Duration**: 45200ms
**Commits**: 2
**Tool Invocations**: 14

---

## Timeline

### 1. TaskReceived
*2026-03-21T14:30:00.000Z | orchestrator*

> Fix the login form validation on mobile

### 2. SkillsClassified
*2026-03-21T14:30:00.142Z | classifier*

**Method**: semantic
**Agent Type**: frontend
- `debug-react` (similarity: 0.87, success: 0.92)
- `mobile-responsive` (similarity: 0.73, success: 0.85)

### 3. SkillsInjected
*2026-03-21T14:30:00.145Z | injector*

- **skill_slugs**: ["debug-react", "mobile-responsive"]
- **total_bytes**: 12480

### 4. AgentSpawned
*2026-03-21T14:30:00.800Z | orchestrator*

- **job_name**: df-abc123de-7f3a
- **branch**: df/abc123de/7f3a1b2c

### 5. AgentStarted
*2026-03-21T14:30:05.100Z | agent-pod*

### 6. ToolInvoked
*2026-03-21T14:30:12.300Z | agent-pod*

**Tool**: `Read`
**Input**: src/components/LoginForm.tsx (lines 1-120)
**Duration**: 12ms

...

### 14. CommitCreated
*2026-03-21T14:31:02.000Z | agent-pod*

**Commit**: `a1b2c3d4`
**Message**: fix: add mobile viewport validation to login form
**Files**:
  - `src/components/LoginForm.tsx`
  - `src/styles/login.css`

### 15. AgentCompleted
*2026-03-21T14:31:15.200Z | agent-pod*

**Exit Code**: 0
**Commits**: 2

---
*Generated from 15 events*
```

---

## 7. Pros & Cons

### Why This Approach Wins

| Advantage | Details |
|-----------|---------|
| **Immutable audit trail** | Events cannot be modified after emission. You have a complete, tamper-proof record of every agent run. This matters for compliance, debugging, and trust |
| **Temporal queries for free** | "What was the agent doing at T+30s?" — just filter events by timestamp. "How long did classification take?" — diff two event timestamps. The event log IS the timeline |
| **Replay capability** | Bug in report generation? Fix the materializer code, replay the stream, regenerate all reports. No re-running agents. No data loss |
| **Zero new infrastructure** | Redis Streams are a built-in Redis data structure. You are already running Redis. This adds zero operational burden |
| **Natural CQRS fit** | Write path (emit events) is decoupled from read path (materializer). You can add new read models (Grafana dashboard, Slack summary, PDF report) without touching the write path |
| **Loose coupling** | Components emit events and forget. The materializer is the only consumer. Components never query the trace — they just write to it |
| **Debugging superpower** | When an agent produces a bad PR, you can read the full trace: what skills were injected, what tools were called, what reasoning steps occurred. No more guessing |

### What You Are Giving Up (Be Honest)

| Concern | Severity | Mitigation |
|---------|----------|------------|
| **Eventual consistency** | Medium | Reports are generated after `AgentCompleted`. There is a window (typically <1s) where the event is in Redis but not yet in SQLite. For a tracing system, this is fine — nobody queries traces in real-time |
| **CQRS complexity** | Medium | CQRS introduces a new mental model. Developers must understand that writes go to Redis, reads come from SQLite. This is manageable with good documentation and a thin abstraction layer |
| **Redis memory** | Low | 50 events at 500 bytes = 25 KB per trace. With 7-day TTL and ~100 traces/day, peak Redis memory usage is ~17 MB. Negligible |
| **Agent pod instrumentation** | High | Parsing Claude CLI output is fragile. Output format may change between CLI versions. Mitigation: version-pin the CLI, write defensive parsers, and accept that some events may be missed |
| **Materializer as single point** | Medium | If the materializer crashes, events queue up in Redis (they are persistent). On restart, it catches up from the consumer group's last ACK position. No data loss, just delayed reports |
| **SQLite write throughput** | Low | At ~50 events per trace and traces arriving serially, SQLite can handle this trivially (thousands of writes/sec). This becomes a concern only at >1000 concurrent traces, which is far beyond current scale |
| **No real-time streaming to UI** | Low | The materializer is batch-oriented. If you need a live trace viewer, you would need a WebSocket consumer reading from Redis Streams directly. This is additive — does not require changing the architecture |

### Comparison with Alternatives

| | Structured Logging | Span-Based Tracing (OpenTelemetry) | Event Sourcing (This Approach) |
|---|---|---|---|
| **Temporal ordering** | Unreliable (log timestamps) | Good (span parent-child) | Excellent (stream ordering) |
| **Replay** | No | No | Yes |
| **Cross-process correlation** | Manual (correlation IDs) | Built-in (trace context) | Built-in (trace_id = stream key) |
| **Report generation** | Custom log parsing | Custom span-to-doc | Natural (event replay) |
| **Infrastructure** | ELK/Loki | Jaeger/Tempo + collector | Redis (already have it) |
| **Complexity** | Low | High | Medium |
| **Data model flexibility** | Schema-on-read (pain) | Fixed span schema | Custom events (flexible) |

---

## 8. Implementation Effort

### Phase 1: Controller-Side Tracing (3-4 days)

Build the emitter and wire it into the existing controller components.

| Task | Effort | Details |
|------|--------|---------|
| `TraceEmitter` class + typed emitters | 1 day | Base emitter, `OrchestratorTracer`, `ClassifierTracer` |
| Wire into `orchestrator._spawn_job()` | 0.5 day | Emit `TaskReceived`, `SkillsClassified`, `SkillsInjected`, `AgentSpawned` |
| Wire into `safety.process()` | 0.5 day | Emit `PRCreated`, `SafetyChecked`, `ResultReported` |
| Unit tests for emitter | 0.5 day | Use `fakeredis` to verify event structure |
| Integration test: full trace | 0.5 day | Spawn a mock job, verify stream contains expected events |

**Deliverable**: Every job dispatch produces a Redis Stream with 4-6 controller-side events.

### Phase 2: Materializer + Reports (3-4 days)

Build the consumer that turns events into SQLite rows and Markdown reports.

| Task | Effort | Details |
|------|--------|---------|
| SQLite schema + `TraceMaterializer` | 1 day | Consumer group setup, event processing loop |
| Summary materialization | 1 day | Update `trace_summaries` table per event type |
| Markdown report generator | 1 day | Template rendering from event list |
| Background task integration | 0.5 day | Wire into FastAPI lifespan |
| FastAPI endpoints for traces | 0.5 day | `GET /api/v1/traces`, `GET /api/v1/traces/{id}`, `GET /api/v1/traces/{id}/report` |

**Deliverable**: Every completed job produces a Markdown report. Traces queryable via API.

### Phase 3: Agent Pod Instrumentation (4-5 days)

The hardest phase — instrumenting the agent container.

| Task | Effort | Details |
|------|--------|---------|
| `trace_emitter.py` script | 2 days | Claude output parsing, git commit detection, Redis emission |
| Modified `entrypoint.sh` | 0.5 day | Route through trace emitter |
| Docker image changes | 0.5 day | Include `redis` Python package, `trace_emitter.py` |
| Output parsing robustness | 1 day | Handle Claude CLI format variations, edge cases |
| End-to-end test | 0.5 day | Full trace from task receipt to report generation |

**Deliverable**: Full trace coverage including agent-side tool invocations and reasoning steps.

### Phase 4: Polish + Observability (2-3 days)

| Task | Effort | Details |
|------|--------|---------|
| Prometheus metrics for materializer | 0.5 day | events_processed, materialization_lag, report_generation_time |
| Trace API pagination + filtering | 0.5 day | Filter by status, date range, repo |
| Report linking in Slack/Linear responses | 0.5 day | Include report URL in result messages |
| Redis Stream cleanup job | 0.5 day | Periodic XTRIM + EXPIRE for old traces |
| Documentation | 0.5 day | ADR, runbook, event catalog |

**Total: 12-16 days** (roughly 3 weeks with buffer)

---

## 9. Code Examples

### Complete Working Example: Emit + Consume + Report

```python
# Example: Full lifecycle in a test harness

import asyncio
import json
from redis.asyncio import Redis
from controller.tracing.emitter import TraceEmitter
from controller.tracing.materializer import TraceMaterializer


async def demo():
    redis = Redis.from_url("redis://localhost:6379")
    emitter = TraceEmitter(redis)
    trace_id = "demo-trace-001"

    # --- Simulate controller-side events ---

    await emitter.emit(trace_id, "TaskReceived", "orchestrator", {
        "task": "Add dark mode toggle to settings page",
        "source": "slack",
        "source_ref": {"channel": "C123"},
        "repo_owner": "acme",
        "repo_name": "web-app",
        "thread_id": trace_id,
    })

    await emitter.emit(trace_id, "SkillsClassified", "classifier", {
        "matched_skills": [
            {"slug": "css-theming", "similarity": 0.91, "success_rate": 0.88},
        ],
        "agent_type": "frontend",
        "reasoning": "Dark mode is a CSS theming task",
        "method": "semantic",
        "classification_ms": 98,
    })

    await emitter.emit(trace_id, "SkillsInjected", "injector", {
        "skill_slugs": ["css-theming"],
        "total_bytes": 3200,
        "injection_method": "system_prompt_append",
    })

    await emitter.emit(trace_id, "AgentSpawned", "orchestrator", {
        "job_name": "df-demo-001-a1b2",
        "agent_image": "ditto-factory-agent:frontend",
        "branch": "df/demo001/a1b2c3d4",
    })

    # --- Simulate agent-side events ---

    await emitter.emit(trace_id, "AgentStarted", "agent-pod", {
        "pod_name": "df-demo-001-a1b2-xyz",
    })

    await emitter.emit(trace_id, "ToolInvoked", "agent-pod", {
        "tool_name": "Glob",
        "tool_input_summary": "**/*.css",
        "duration_ms": 8,
        "success": True,
    })

    await emitter.emit(trace_id, "ToolInvoked", "agent-pod", {
        "tool_name": "Read",
        "tool_input_summary": "src/styles/theme.css",
        "duration_ms": 5,
        "success": True,
    })

    await emitter.emit(trace_id, "ReasoningStep", "agent-pod", {
        "step_index": 1,
        "summary": "Found existing theme variables in theme.css. Will add dark mode CSS custom properties and a toggle component.",
    })

    await emitter.emit(trace_id, "ToolInvoked", "agent-pod", {
        "tool_name": "Edit",
        "tool_input_summary": "src/styles/theme.css — add dark mode variables",
        "duration_ms": 12,
        "success": True,
    })

    await emitter.emit(trace_id, "CommitCreated", "agent-pod", {
        "commit_sha": "f4e5d6c7b8a9",
        "commit_message": "feat: add dark mode CSS custom properties",
        "files_changed": ["src/styles/theme.css", "src/components/Settings.tsx"],
    })

    await emitter.emit(trace_id, "AgentCompleted", "agent-pod", {
        "exit_code": 0,
        "commit_count": 1,
        "total_duration_ms": 32000,
    })

    # --- Read the stream back ---
    events = await redis.xrange(f"traces:{trace_id}")
    print(f"\n=== Stream contains {len(events)} events ===\n")
    for entry_id, fields in events:
        print(f"  [{fields[b'sequence'].decode()}] {fields[b'event_type'].decode()}")

    # --- Run materializer to generate report ---
    materializer = TraceMaterializer(redis, db_path="/tmp/demo_traces.db", reports_dir="/tmp/demo_reports")
    await materializer.initialize()

    # Process all events manually (in production, the run() loop does this)
    for entry_id, fields in events:
        decoded = {k.decode(): v.decode() for k, v in fields.items()}
        await materializer._process_event(f"traces:{trace_id}", entry_id, decoded)

    print(f"\n=== Report generated at /tmp/demo_reports/{trace_id}.md ===\n")
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(demo())
```

### Query API Endpoints

```python
# controller/src/controller/api/traces.py

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import aiosqlite

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])


@router.get("")
async def list_traces(
    status: str | None = None,
    repo: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List trace summaries with optional filtering."""
    async with aiosqlite.connect("traces.db") as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM trace_summaries WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if repo:
            query += " AND repo = ?"
            params.append(repo)

        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@router.get("/{trace_id}")
async def get_trace(trace_id: str):
    """Get full event timeline for a trace."""
    async with aiosqlite.connect("traces.db") as db:
        db.row_factory = aiosqlite.Row

        # Get summary
        cursor = await db.execute(
            "SELECT * FROM trace_summaries WHERE trace_id = ?", (trace_id,)
        )
        summary = await cursor.fetchone()
        if not summary:
            raise HTTPException(status_code=404, detail="Trace not found")

        # Get events
        cursor = await db.execute(
            """SELECT * FROM trace_events
               WHERE trace_id = ?
               ORDER BY sequence ASC""",
            (trace_id,),
        )
        events = await cursor.fetchall()

        return {
            "summary": dict(summary),
            "events": [dict(e) for e in events],
        }


@router.get("/{trace_id}/report")
async def get_trace_report(trace_id: str):
    """Download the Markdown report for a trace."""
    async with aiosqlite.connect("traces.db") as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT report_path FROM trace_summaries WHERE trace_id = ?",
            (trace_id,),
        )
        row = await cursor.fetchone()
        if not row or not row["report_path"]:
            raise HTTPException(status_code=404, detail="Report not found")

        return FileResponse(
            row["report_path"],
            media_type="text/markdown",
            filename=f"trace-{trace_id[:12]}.md",
        )
```

---

## ADR-001: Event Sourcing over Structured Logging for Agent Tracing

### Status
Proposed

### Context
We need to capture agent activity (reasoning, tool calls, decisions) and compile it into review documents. The system already uses Redis as its communication backbone. We considered three approaches: structured logging (ELK), span-based tracing (OpenTelemetry), and event sourcing (Redis Streams).

### Decision
Use event sourcing with Redis Streams as the tracing backbone. Every agent action emits an immutable event to a per-trace Redis Stream. A materializer process consumes events and builds SQLite read models and Markdown reports.

### Consequences
- **Easier**: Temporal queries (event ordering is guaranteed). Report regeneration via replay. Zero new infrastructure. Adding new read models without touching the write path. Complete audit trail.
- **Harder**: CQRS mental model for developers. Agent pod instrumentation requires parsing Claude CLI output (fragile). Eventual consistency between write and read paths (acceptable for tracing). Materializer is a new process to monitor.

## ADR-002: Redis Streams over Pub/Sub for Event Transport

### Status
Proposed

### Context
Redis offers both Pub/Sub and Streams for message passing. We need persistence (events must survive restarts), ordering (events must replay in order), and consumer groups (materializer must process at-least-once).

### Decision
Use Redis Streams (`XADD`, `XREADGROUP`, `XACK`). Pub/Sub is fire-and-forget with no persistence and no replay. Streams provide all three requirements natively.

### Consequences
- **Easier**: At-least-once delivery via consumer groups. Natural event replay with `XRANGE`. Built-in backpressure via `BLOCK`.
- **Harder**: Slightly more complex API than Pub/Sub. Need to manage stream trimming (`MAXLEN`, `EXPIRE`). Consumer group state adds operational surface.

## ADR-003: SQLite over PostgreSQL for Materialized Read Models

### Status
Proposed

### Context
The materialized views (trace events, summaries, tool metrics) need a queryable store. The system already uses PostgreSQL for some data and SQLite for skill metrics.

### Decision
Use SQLite for trace materialized views. The controller is single-process, trace queries are simple (filter + sort), and SQLite avoids adding another PostgreSQL schema migration path.

### Consequences
- **Easier**: No migration infrastructure needed. Fast reads for single-trace queries. Co-located with the controller process.
- **Harder**: Cannot scale to multiple controller replicas writing concurrently. Must migrate to PostgreSQL if the controller becomes multi-instance. Limited to ~1000 concurrent traces before write contention matters.
