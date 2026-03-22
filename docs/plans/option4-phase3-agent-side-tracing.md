# Option 4 (B Enhanced) -- Phase 3: Agent-Side Tracing

## Status
Proposed

## Context

Phase 1 provides `TraceSpan`, `TraceStore`, and `TraceContext` on the controller side.
Phase 2 propagates `trace_id` and `parent_span_id` in the Redis task payload from the orchestrator.
Phase 3 instruments the **agent pod** to emit trace spans back to the controller.

### Key Constraints

| Constraint | Impact |
|---|---|
| Agent image is `node:22-slim` | No Python runtime for structured tracing libraries |
| `claude -p` is a black box | Cannot hook into internal reasoning, tool calls, or token usage |
| Entrypoint is bash | All instrumentation must be bash + `jq` + `redis-cli` |
| `redis-tools` already installed | No Docker image changes needed for Redis access |
| `jq` already installed | JSON construction and parsing available |
| Agent pods are ephemeral | Traces must be written to Redis before pod terminates |

---

## 1. What We CAN Capture (Realistic)

### Pre-execution observables
- **AGENT_STARTED**: timestamp when entrypoint begins, env vars present, thread ID
- **TASK_RECEIVED**: timestamp when Redis task is read, task size in bytes, skills count
- **SKILLS_WRITTEN**: which skill files were injected, their names and byte sizes
- **REPO_CLONED**: clone duration, branch name, whether branch existed or was created
- **MCP_CONFIGURED**: whether gateway MCP config was merged, final config path

### Post-execution observables
- **CLAUDE_COMPLETED**: exit code, wall-clock duration of `claude -p` invocation
- **STDOUT_CAPTURED**: raw stdout from Claude (may contain tool usage markers)
- **STDERR_CAPTURED**: stderr content and size
- **GIT_ACTIVITY**: files changed, insertions, deletions, commit messages, diff stats
- **PUSH_RESULT**: whether `git push` succeeded or failed
- **AGENT_COMPLETED**: total wall-clock duration, final exit code

### Git-derived intelligence
- `git diff --stat HEAD ^origin/main` -- files changed, insertions, deletions
- `git log --oneline HEAD ^origin/main` -- commit messages (intent signal)
- `git diff --name-only HEAD ^origin/main` -- which files were touched (scope signal)
- Number of commits (already captured)

---

## 2. What We CANNOT Capture (Honest)

| Observable | Why Not | Workaround |
|---|---|---|
| Claude's thinking/reasoning | CLI does not expose `<thinking>` blocks | None -- opaque by design |
| Individual tool invocations | No hook mechanism in `claude -p` | Best-effort stdout parsing (Section 5) |
| Token usage (input/output) | Not reported by CLI stdout or stderr | Could query Anthropic usage API post-hoc if needed |
| Why Claude chose approach A over B | Internal to model | Prompt Claude to explain decisions in commit messages |
| MCP tool call details | Handled internally by Claude CLI | Gateway-side logging (separate concern) |
| Memory/CPU usage during execution | Would need sidecar or cAdvisor | K8s metrics server (out of scope) |
| Intermediate progress | Claude runs as single blocking call | Could poll git for new commits during execution (future) |

---

## 3. Instrumentation via Bash + redis-cli

### 3.1 Trace Helper Functions

Add these functions near the top of `entrypoint.sh`, after env var validation:

```bash
# ── Tracing helpers ──────────────────────────────────────────────
# Nanosecond timestamp (falls back to second precision if %N unsupported)
trace_ts() {
    local ns
    ns=$(date +%s%N 2>/dev/null)
    if [ ${#ns} -gt 10 ]; then
        echo "$ns"
    else
        echo "$(date +%s)000000000"
    fi
}

# Generate a simple span ID (16 hex chars from /dev/urandom)
gen_span_id() {
    head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

TRACE_EVENTS="[]"
AGENT_SPAN_ID=$(gen_span_id)
AGENT_START_NS=$(trace_ts)

# Append a trace event to the in-memory array.
# Usage: trace_event "EVENT_TYPE" '{"key":"value"}'
trace_event() {
    local event_type="$1"
    local metadata="${2:-\{\}}"
    local ts
    ts=$(trace_ts)

    TRACE_EVENTS=$(echo "$TRACE_EVENTS" | jq \
        --arg type "$event_type" \
        --arg ts "$ts" \
        --arg span_id "$AGENT_SPAN_ID" \
        --argjson meta "$metadata" \
        '. + [{type: $type, timestamp: $ts, span_id: $span_id, metadata: $meta}]')
}

# Compute duration in milliseconds between two nanosecond timestamps.
duration_ms() {
    local start_ns="$1"
    local end_ns="$2"
    echo $(( (end_ns - start_ns) / 1000000 ))
}
```

### 3.2 Reading Trace Context from Task Payload

Phase 2 adds `trace_id` and `parent_span_id` to the Redis task JSON. The agent reads them:

```bash
# Extract trace context propagated by orchestrator (Phase 2)
TRACE_ID=$(echo "$TASK_JSON" | jq -r '.trace_id // empty')
PARENT_SPAN_ID=$(echo "$TASK_JSON" | jq -r '.parent_span_id // empty')

# If no trace context, generate our own (standalone execution)
if [ -z "$TRACE_ID" ]; then
    TRACE_ID=$(gen_span_id)$(gen_span_id)  # 32 hex chars
    PARENT_SPAN_ID=""
fi
```

### 3.3 Instrumentation Points

Events are emitted at each phase boundary in the entrypoint:

| Phase | Event Type | Metadata |
|---|---|---|
| After env validation | `AGENT_STARTED` | `thread_id`, `trace_id`, `parent_span_id` |
| After Redis GET | `TASK_RECEIVED` | `task_bytes`, `has_skills`, `has_system_prompt`, `has_gateway_mcp` |
| After skill injection | `SKILLS_WRITTEN` | `skill_names[]`, `skill_count`, `total_bytes` |
| After git clone + branch | `REPO_CLONED` | `branch`, `branch_existed`, `clone_duration_ms` |
| After MCP config merge | `MCP_CONFIGURED` | `merged`, `config_path` |
| Before `claude -p` | `CLAUDE_STARTED` | `has_system_prompt`, `task_length` |
| After `claude -p` | `CLAUDE_COMPLETED` | `exit_code`, `duration_ms`, `stdout_bytes`, `stderr_bytes` |
| After git diff --stat | `GIT_ACTIVITY` | `files_changed`, `insertions`, `deletions`, `commits[]` |
| After git push | `PUSH_COMPLETED` | `push_success` |
| Final | `AGENT_COMPLETED` | `exit_code`, `total_duration_ms`, `commit_count` |

### 3.4 Error Resilience

All tracing is wrapped defensively. A tracing failure must never cause the agent to fail:

```bash
# Safe trace wrapper -- swallows errors
safe_trace() {
    trace_event "$@" 2>/dev/null || true
}
```

The `cleanup` trap is updated to emit `AGENT_CRASHED` if `RESULT_PUBLISHED` is not set, and to include whatever trace events were collected up to the crash point.

---

## 4. Enhanced Result Payload

### 4.1 New Result Schema

The Redis result JSON is expanded. The controller must handle both old (no `trace_events`) and new payloads gracefully.

```json
{
  "branch": "df/thread-abc123",
  "exit_code": 0,
  "commit_count": 3,
  "stderr": "",
  "trace": {
    "trace_id": "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8",
    "agent_span_id": "f1e2d3c4b5a69788",
    "parent_span_id": "1234567890abcdef",
    "trace_events": [
      {
        "type": "AGENT_STARTED",
        "timestamp": "1711036800000000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"thread_id": "abc123"}
      },
      {
        "type": "TASK_RECEIVED",
        "timestamp": "1711036800100000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"task_bytes": 1423, "has_skills": true, "skill_count": 2}
      },
      {
        "type": "SKILLS_WRITTEN",
        "timestamp": "1711036800150000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"skill_names": ["refactor-py", "testing-py"], "total_bytes": 4096}
      },
      {
        "type": "REPO_CLONED",
        "timestamp": "1711036805000000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"branch": "df/thread-abc123", "branch_existed": false, "clone_duration_ms": 4850}
      },
      {
        "type": "CLAUDE_STARTED",
        "timestamp": "1711036805200000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"task_length": 1200}
      },
      {
        "type": "CLAUDE_COMPLETED",
        "timestamp": "1711036850200000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"exit_code": 0, "duration_ms": 45000, "stdout_bytes": 8432, "stderr_bytes": 0}
      },
      {
        "type": "GIT_ACTIVITY",
        "timestamp": "1711036850500000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {
          "files_changed": 5,
          "insertions": 42,
          "deletions": 18,
          "commits": ["fix: session token refresh", "test: add refresh token tests", "refactor: extract token module"]
        }
      },
      {
        "type": "AGENT_COMPLETED",
        "timestamp": "1711036851000000000",
        "span_id": "f1e2d3c4b5a69788",
        "metadata": {"exit_code": 0, "total_duration_ms": 51000, "commit_count": 3}
      }
    ]
  },
  "stdout_excerpt": "(first 10KB of claude stdout, truncated)"
}
```

### 4.2 Backward Compatibility

The `trace` key is optional. The controller's `JobMonitor.wait_for_result()` already uses `.get()` with defaults, so old-format results continue to work. The `AgentResult` dataclass gains an optional `trace` field.

---

## 5. Stdout/Stderr Parsing (Best Effort)

### 5.1 What Claude CLI Prints

When `claude -p` runs, its stdout contains the final response text. It may also contain markers from tool use, depending on CLI version and output format. Common patterns observed:

```
# File operations (may appear in verbose/streaming modes)
Read file: src/auth/token.py
Edit file: src/auth/token.py
Write file: src/auth/tests/test_token.py
Bash: npm test

# Or structured JSON output (with --output-format json)
{"type":"tool_use","name":"Read","input":{"file_path":"..."}}
```

### 5.2 Parsing Strategy

```bash
# After claude completes, parse stdout for tool usage hints
parse_claude_stdout() {
    local stdout_file="$1"
    local tool_hints="[]"

    # Pattern 1: "Read file:", "Edit file:", "Write file:", "Bash:" lines
    while IFS= read -r line; do
        tool_hints=$(echo "$tool_hints" | jq \
            --arg line "$line" \
            '. + [$line]')
    done < <(grep -E '^(Read|Edit|Write|Bash|Search|Glob):' "$stdout_file" 2>/dev/null || true)

    echo "$tool_hints"
}
```

### 5.3 Fragility Warning

**This parsing is FRAGILE and OPTIONAL.** It depends on:
- The Claude CLI output format, which is not a stable API
- Whether the CLI is run in streaming vs batch mode
- CLI version changes that may alter output format

The parsing should be:
1. **Behind a flag**: `TRACE_PARSE_STDOUT=1` env var (default: off)
2. **Fail-safe**: if parsing fails, store raw stdout and move on
3. **Size-limited**: only store first 10KB of stdout in the result payload to avoid Redis bloat

### 5.4 Raw Stdout Storage

```bash
# Capture stdout to file for post-hoc analysis
STDOUT_FILE=$(mktemp)
claude "${CLAUDE_ARGS[@]}" >"$STDOUT_FILE" 2>"$STDERR_FILE"

# Store truncated excerpt in result
STDOUT_EXCERPT=$(head -c 10240 "$STDOUT_FILE")
```

**Trade-off**: capturing stdout to a file means we lose real-time streaming to the pod log. This is acceptable because:
- Pod logs are ephemeral anyway (pod terminates after completion)
- The stored excerpt provides more value for debugging than transient logs
- We still log key milestones to pod stdout via `echo` statements

---

## 6. Controller-Side Collection

### 6.1 JobMonitor Changes

`JobMonitor.wait_for_result()` is updated to extract trace events from the result payload and return them alongside the `AgentResult`.

```python
# controller/src/controller/jobs/monitor.py

@dataclass
class AgentResult:
    branch: str
    exit_code: int
    commit_count: int
    stderr: str = ""
    pr_url: str | None = None
    trace: dict | None = None          # NEW: raw trace from agent
    stdout_excerpt: str | None = None  # NEW: truncated stdout

class JobMonitor:
    async def wait_for_result(
        self, thread_id: str, timeout: int = 1800, poll_interval: float = 5.0
    ) -> AgentResult | None:
        elapsed = 0.0
        while elapsed < timeout:
            result_data = await self._redis.get_result(thread_id)
            if result_data is not None:
                return AgentResult(
                    branch=result_data.get("branch", ""),
                    exit_code=int(result_data.get("exit_code", 1)),
                    commit_count=int(result_data.get("commit_count", 0)),
                    stderr=result_data.get("stderr", ""),
                    trace=result_data.get("trace"),              # NEW
                    stdout_excerpt=result_data.get("stdout_excerpt"),  # NEW
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return None
```

### 6.2 Orchestrator Trace Collection

In `handle_job_completion()`, after receiving the result, the orchestrator converts agent trace events into `TraceSpan` objects:

```python
# In orchestrator.handle_job_completion()

async def _collect_agent_traces(self, result: AgentResult, thread_id: str) -> None:
    """Convert agent-side trace events into TraceSpan objects."""
    if not result.trace or not result.trace.get("trace_events"):
        return

    trace_id = result.trace.get("trace_id", "")
    agent_span_id = result.trace.get("agent_span_id", "")
    parent_span_id = result.trace.get("parent_span_id", "")

    # Create a parent AGENT_EXECUTION span that wraps all agent events
    agent_span = TraceSpan(
        trace_id=trace_id,
        span_id=agent_span_id,
        parent_span_id=parent_span_id,  # Links to orchestrator's AGENT_SPAWNED span
        operation="AGENT_EXECUTION",
        thread_id=thread_id,
        metadata={"event_count": len(result.trace["trace_events"])},
    )
    await self._trace_store.insert(agent_span)

    # Each trace event becomes a child span of the agent execution
    for event in result.trace["trace_events"]:
        event_span = TraceSpan(
            trace_id=trace_id,
            span_id=gen_span_id(),  # Generate new span ID for each event
            parent_span_id=agent_span_id,
            operation=event["type"],
            thread_id=thread_id,
            start_time=_ns_to_datetime(event["timestamp"]),
            metadata=event.get("metadata", {}),
        )
        await self._trace_store.insert(event_span)
```

### 6.3 Trace Hierarchy

The resulting span tree for a single task looks like:

```
TASK_RECEIVED (orchestrator)
  └── TASK_CLASSIFIED (orchestrator)
  └── SKILLS_SELECTED (orchestrator)
  └── AGENT_SPAWNED (orchestrator)
       └── AGENT_EXECUTION (from agent result)
            ├── AGENT_STARTED
            ├── TASK_RECEIVED (agent-side)
            ├── SKILLS_WRITTEN
            ├── REPO_CLONED
            ├── CLAUDE_STARTED
            ├── CLAUDE_COMPLETED
            ├── GIT_ACTIVITY
            └── AGENT_COMPLETED
  └── SAFETY_PIPELINE (orchestrator)
  └── RESULT_DELIVERED (orchestrator)
```

---

## 7. Docker Image Changes

### 7.1 No Changes Required

The agent Dockerfile already includes all required packages:

```dockerfile
RUN apt-get update && apt-get install -y \
    git build-essential python3 python3-pip curl jq redis-tools \
    && rm -rf /var/lib/apt/lists/*
```

- `redis-tools` provides `redis-cli` -- already installed
- `jq` -- already installed
- `date`, `head`, `od` -- part of base `node:22-slim` (coreutils)
- `/dev/urandom` -- available in all Linux containers

### 7.2 Mock Agent Updates

The mock agent (`images/mock-agent/entrypoint.sh`) should also emit trace events to allow integration testing without burning Anthropic API credits. The mock agent already has `jq` and `redis` installed via Alpine packages.

Add a simplified version of trace emission to the mock agent that produces the same JSON structure with synthetic timing data.

---

## 8. Modified entrypoint.sh

Below is the complete modified entrypoint with inline comments. Changes are marked with `# [TRACE]` comments.

```bash
#!/usr/bin/env bash
set -euo pipefail

# Required environment variables
: "${THREAD_ID:?THREAD_ID is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required}"

REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)

# ── [TRACE] Tracing helpers ─────────────────────────────────────
trace_ts() {
    local ns
    ns=$(date +%s%N 2>/dev/null)
    if [ ${#ns} -gt 10 ]; then
        echo "$ns"
    else
        echo "$(date +%s)000000000"
    fi
}

gen_span_id() {
    head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

TRACE_EVENTS="[]"
AGENT_SPAN_ID=$(gen_span_id)
AGENT_START_NS=$(trace_ts)

# Append a trace event. Swallows errors to never break the agent.
safe_trace() {
    local event_type="$1"
    local metadata="${2:-\{\}}"
    local ts
    ts=$(trace_ts)

    TRACE_EVENTS=$(echo "$TRACE_EVENTS" | jq \
        --arg type "$event_type" \
        --arg ts "$ts" \
        --arg span_id "$AGENT_SPAN_ID" \
        --argjson meta "$metadata" \
        '. + [{type: $type, timestamp: $ts, span_id: $span_id, metadata: $meta}]' \
        2>/dev/null) || true
}

duration_ms() {
    local start_ns="$1"
    local end_ns="$2"
    echo $(( (end_ns - start_ns) / 1000000 ))
}
# ── [TRACE] End tracing helpers ──────────────────────────────────

# [TRACE] Emit AGENT_STARTED
safe_trace "AGENT_STARTED" "$(jq -n --arg tid "$THREAD_ID" '{thread_id: $tid}')"

cleanup() {
    local exit_code=$?
    echo "Agent exiting with code $exit_code"
    if [ -z "${RESULT_PUBLISHED:-}" ]; then
        # [TRACE] Emit crash event with whatever we have so far
        safe_trace "AGENT_CRASHED" "$(jq -n --argjson ec "$exit_code" '{exit_code: $ec}')"

        # [TRACE] Build trace object for crash result
        local trace_obj
        trace_obj=$(jq -n \
            --arg trace_id "${TRACE_ID:-unknown}" \
            --arg agent_span_id "$AGENT_SPAN_ID" \
            --arg parent_span_id "${PARENT_SPAN_ID:-}" \
            --argjson events "$TRACE_EVENTS" \
            '{trace_id: $trace_id, agent_span_id: $agent_span_id, parent_span_id: $parent_span_id, trace_events: $events}' \
            2>/dev/null) || trace_obj="{}"

        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
            "$(jq -n \
                --arg branch "${BRANCH:-unknown}" \
                --argjson exit_code "$exit_code" \
                --arg stderr "${STDERR:-agent crashed}" \
                --argjson trace "$trace_obj" \
            '{branch: $branch, exit_code: $exit_code, commit_count: 0, stderr: $stderr, trace: $trace}')" EX 3600
    fi
}
trap cleanup EXIT

# Fetch task from Redis
TASK_JSON=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "task:$THREAD_ID")
if [ -z "$TASK_JSON" ] || [ "$TASK_JSON" = "(nil)" ]; then
    echo "No task found for thread $THREAD_ID"
    exit 1
fi

REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')
TASK=$(echo "$TASK_JSON" | jq -r '.task')
SYSTEM_PROMPT=$(echo "$TASK_JSON" | jq -r '.system_prompt // empty')

# [TRACE] Extract trace context from task payload (propagated by Phase 2)
TRACE_ID=$(echo "$TASK_JSON" | jq -r '.trace_id // empty')
PARENT_SPAN_ID=$(echo "$TASK_JSON" | jq -r '.parent_span_id // empty')
if [ -z "$TRACE_ID" ]; then
    TRACE_ID=$(gen_span_id)$(gen_span_id)
    PARENT_SPAN_ID=""
fi

# [TRACE] Emit TASK_RECEIVED
TASK_BYTES=${#TASK}
HAS_SKILLS=$(echo "$TASK_JSON" | jq 'has("skills") and (.skills | length > 0)')
HAS_SYSPROMPT=$([ -n "$SYSTEM_PROMPT" ] && echo "true" || echo "false")
safe_trace "TASK_RECEIVED" "$(jq -n \
    --argjson tb "$TASK_BYTES" \
    --argjson hs "$HAS_SKILLS" \
    --argjson hsp "$HAS_SYSPROMPT" \
    '{task_bytes: $tb, has_skills: $hs, has_system_prompt: $hsp}')"

# Clone and setup
git config --global credential.helper '!f() { echo "password=$GITHUB_TOKEN"; }; f'
git config --global user.name "Ditto Factory"
git config --global user.email "aal@noreply.github.com"

WORKSPACE="/workspace"
CLONE_START=$(trace_ts)
git clone "https://x-access-token:${GITHUB_TOKEN}@${REPO_URL#https://}" "$WORKSPACE"
cd "$WORKSPACE"

# Create or checkout branch
BRANCH_EXISTED="false"
if git ls-remote --heads origin "$BRANCH" | grep -q "$BRANCH"; then
    git checkout "$BRANCH"
    BRANCH_EXISTED="true"
else
    git checkout -b "$BRANCH"
fi
CLONE_END=$(trace_ts)

# [TRACE] Emit REPO_CLONED
safe_trace "REPO_CLONED" "$(jq -n \
    --arg branch "$BRANCH" \
    --argjson existed "$BRANCH_EXISTED" \
    --argjson dur "$(duration_ms "$CLONE_START" "$CLONE_END")" \
    '{branch: $branch, branch_existed: $existed, clone_duration_ms: $dur}')"

# === Inject skills from task payload ===
SKILLS_JSON=$(echo "$TASK_JSON" | jq -r '.skills // empty')
SKILL_NAMES="[]"
SKILL_TOTAL_BYTES=0
if [ -n "$SKILLS_JSON" ] && [ "$SKILLS_JSON" != "null" ]; then
    mkdir -p .claude/skills
    while read -r skill; do
        SKILL_NAME=$(echo "$skill" | jq -r '.name' | tr -cd 'a-zA-Z0-9_-')
        if [ -z "$SKILL_NAME" ]; then
            echo "WARNING: Skipping skill with empty/invalid name"
            continue
        fi
        SKILL_CONTENT=$(echo "$skill" | jq -r '.content')
        echo "$SKILL_CONTENT" > ".claude/skills/${SKILL_NAME}.md"
        # [TRACE] Track skill names and sizes
        SKILL_NAMES=$(echo "$SKILL_NAMES" | jq --arg n "$SKILL_NAME" '. + [$n]' 2>/dev/null) || true
        SKILL_TOTAL_BYTES=$((SKILL_TOTAL_BYTES + ${#SKILL_CONTENT}))
    done < <(echo "$SKILLS_JSON" | jq -c '.[]')
    SKILL_COUNT=$(echo "$SKILLS_JSON" | jq length)
    echo "Injected ${SKILL_COUNT} skills into .claude/skills/"
else
    SKILL_COUNT=0
    echo "No skills to inject"
fi
# === End skill injection ===

# [TRACE] Emit SKILLS_WRITTEN
safe_trace "SKILLS_WRITTEN" "$(jq -n \
    --argjson names "$SKILL_NAMES" \
    --argjson count "$SKILL_COUNT" \
    --argjson bytes "$SKILL_TOTAL_BYTES" \
    '{skill_names: $names, skill_count: $count, total_bytes: $bytes}')"

# === Inject gateway MCP config if provided ===
GATEWAY_MCP=$(echo "$TASK_JSON" | jq -r '.gateway_mcp // empty')
MCP_MERGED="false"
if [ -n "$GATEWAY_MCP" ] && [ "$GATEWAY_MCP" != "null" ]; then
    jq -s '.[0] * {mcpServers: (.[0].mcpServers + .[1])}' \
        /etc/df/mcp.json <(echo "$GATEWAY_MCP") > /tmp/mcp-merged.json
    MCP_CONFIG="/tmp/mcp-merged.json"
    MCP_MERGED="true"
    echo "Injected gateway MCP config"
else
    MCP_CONFIG="/etc/df/mcp.json"
fi
# === End gateway MCP injection ===

# [TRACE] Emit MCP_CONFIGURED
safe_trace "MCP_CONFIGURED" "$(jq -n \
    --argjson merged "$MCP_MERGED" \
    --arg path "$MCP_CONFIG" \
    '{merged: $merged, config_path: $path}')"

# Run Claude Code
CLAUDE_ARGS=(-p "$TASK" --allowedTools '*' --mcp-config "$MCP_CONFIG")
if [ -n "${SYSTEM_PROMPT:-}" ]; then
    CLAUDE_ARGS+=(--system-prompt "$SYSTEM_PROMPT")
fi

# [TRACE] Emit CLAUDE_STARTED
safe_trace "CLAUDE_STARTED" "$(jq -n \
    --argjson tlen "${#TASK}" \
    --argjson hsp "$HAS_SYSPROMPT" \
    '{task_length: $tlen, has_system_prompt: $hsp}')"

STDERR_FILE=$(mktemp)
STDOUT_FILE=$(mktemp)
CLAUDE_START=$(trace_ts)
set +e
claude "${CLAUDE_ARGS[@]}" >"$STDOUT_FILE" 2>"$STDERR_FILE"
EXIT_CODE=$?
set -e
CLAUDE_END=$(trace_ts)

STDERR=$(cat "$STDERR_FILE")
STDOUT_BYTES=$(wc -c < "$STDOUT_FILE")
STDERR_BYTES=$(wc -c < "$STDERR_FILE")

# [TRACE] Emit CLAUDE_COMPLETED
safe_trace "CLAUDE_COMPLETED" "$(jq -n \
    --argjson ec "$EXIT_CODE" \
    --argjson dur "$(duration_ms "$CLAUDE_START" "$CLAUDE_END")" \
    --argjson sob "$STDOUT_BYTES" \
    --argjson seb "$STDERR_BYTES" \
    '{exit_code: $ec, duration_ms: $dur, stdout_bytes: $sob, stderr_bytes: $seb}')"

# Count commits
COMMIT_COUNT=$(git rev-list --count HEAD ^origin/main 2>/dev/null || echo "0")

# [TRACE] Capture git activity
GIT_DIFF_STAT=$(git diff --stat HEAD ^origin/main 2>/dev/null || echo "")
FILES_CHANGED=$(git diff --name-only HEAD ^origin/main 2>/dev/null | wc -l | tr -d ' ')
INSERTIONS=$(echo "$GIT_DIFF_STAT" | tail -1 | grep -oP '\d+ insertion' | grep -oP '\d+' || echo "0")
DELETIONS=$(echo "$GIT_DIFF_STAT" | tail -1 | grep -oP '\d+ deletion' | grep -oP '\d+' || echo "0")
COMMIT_MESSAGES=$(git log --oneline HEAD ^origin/main 2>/dev/null | head -20 | jq -R -s 'split("\n") | map(select(. != ""))' 2>/dev/null || echo "[]")

safe_trace "GIT_ACTIVITY" "$(jq -n \
    --argjson fc "$FILES_CHANGED" \
    --argjson ins "${INSERTIONS:-0}" \
    --argjson del "${DELETIONS:-0}" \
    --argjson commits "$COMMIT_MESSAGES" \
    '{files_changed: $fc, insertions: $ins, deletions: $del, commits: $commits}')"

# Push branch
PUSH_START=$(trace_ts)
PUSH_SUCCESS="true"
git push origin "$BRANCH" --force-with-lease 2>/dev/null || PUSH_SUCCESS="false"
PUSH_END=$(trace_ts)

# [TRACE] Emit PUSH_COMPLETED
safe_trace "PUSH_COMPLETED" "$(jq -n \
    --argjson success "$PUSH_SUCCESS" \
    --argjson dur "$(duration_ms "$PUSH_START" "$PUSH_END")" \
    '{push_success: $success, duration_ms: $dur}')"

# [TRACE] Emit AGENT_COMPLETED
AGENT_END_NS=$(trace_ts)
safe_trace "AGENT_COMPLETED" "$(jq -n \
    --argjson ec "$EXIT_CODE" \
    --argjson dur "$(duration_ms "$AGENT_START_NS" "$AGENT_END_NS")" \
    --argjson cc "$COMMIT_COUNT" \
    '{exit_code: $ec, total_duration_ms: $dur, commit_count: $cc}')"

# [TRACE] Build trace object
TRACE_OBJ=$(jq -n \
    --arg trace_id "$TRACE_ID" \
    --arg agent_span_id "$AGENT_SPAN_ID" \
    --arg parent_span_id "$PARENT_SPAN_ID" \
    --argjson events "$TRACE_EVENTS" \
    '{trace_id: $trace_id, agent_span_id: $agent_span_id, parent_span_id: $parent_span_id, trace_events: $events}')

# [TRACE] Capture stdout excerpt (first 10KB) for post-hoc analysis
STDOUT_EXCERPT=$(head -c 10240 "$STDOUT_FILE" | jq -Rs '.' 2>/dev/null || echo '""')

# Publish result (enhanced with trace data)
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SET "result:$THREAD_ID" \
    "$(jq -n \
        --arg branch "$BRANCH" \
        --argjson exit_code "$EXIT_CODE" \
        --argjson commit_count "$COMMIT_COUNT" \
        --arg stderr "$STDERR" \
        --argjson trace "$TRACE_OBJ" \
        --argjson stdout_excerpt "$STDOUT_EXCERPT" \
    '{branch: $branch, exit_code: $exit_code, commit_count: $commit_count, stderr: $stderr, trace: $trace, stdout_excerpt: $stdout_excerpt}')" EX 3600
RESULT_PUBLISHED=1

echo "Agent completed: branch=$BRANCH exit_code=$EXIT_CODE commits=$COMMIT_COUNT"
```

---

## 9. Test Plan

### 9.1 Unit Testing the Bash Functions (Local, No K8s)

Test the tracing helper functions in isolation using `bats` (Bash Automated Testing System) or plain bash assertions.

```bash
# test_trace_helpers.sh -- run with: bash test_trace_helpers.sh

# Source the helper functions (extract to a separate file for testability)
source ./trace_helpers.sh

# Test trace_ts returns a numeric string
ts=$(trace_ts)
[[ "$ts" =~ ^[0-9]+$ ]] && echo "PASS: trace_ts" || echo "FAIL: trace_ts returned '$ts'"

# Test gen_span_id returns 16 hex chars
sid=$(gen_span_id)
[[ "$sid" =~ ^[0-9a-f]{16}$ ]] && echo "PASS: gen_span_id" || echo "FAIL: gen_span_id returned '$sid'"

# Test duration_ms calculation
start="1000000000000"  # 1 second in ns
end="1002500000000"    # 2.5 seconds later
dur=$(duration_ms "$start" "$end")
[ "$dur" -eq 2500 ] && echo "PASS: duration_ms" || echo "FAIL: duration_ms returned '$dur'"

# Test safe_trace builds valid JSON
TRACE_EVENTS="[]"
AGENT_SPAN_ID="abc123def456"
safe_trace "TEST_EVENT" '{"key":"value"}'
count=$(echo "$TRACE_EVENTS" | jq length)
[ "$count" -eq 1 ] && echo "PASS: safe_trace" || echo "FAIL: event count is '$count'"
type=$(echo "$TRACE_EVENTS" | jq -r '.[0].type')
[ "$type" = "TEST_EVENT" ] && echo "PASS: event type" || echo "FAIL: type is '$type'"
```

### 9.2 Integration Test with Mock Redis (Local Docker)

```bash
#!/usr/bin/env bash
# test_agent_tracing_integration.sh
# Requires: docker, jq

set -euo pipefail

echo "=== Starting Redis ==="
docker run -d --name test-redis -p 6399:6379 redis:7-alpine
sleep 1

# Seed a task with trace context
THREAD_ID="test-trace-$(date +%s)"
TASK_JSON=$(jq -n \
    --arg task "Create a hello.txt file" \
    --arg repo_url "https://github.com/test/repo.git" \
    --arg branch "df/test-branch" \
    --arg trace_id "aaaa1111bbbb2222cccc3333dddd4444" \
    --arg parent_span_id "1234567890abcdef" \
    '{task: $task, repo_url: $repo_url, branch: $branch, trace_id: $trace_id, parent_span_id: $parent_span_id, skills: []}')

redis-cli -h localhost -p 6399 SET "task:$THREAD_ID" "$TASK_JSON"

echo "=== Task seeded for thread $THREAD_ID ==="

# Run a simplified entrypoint that skips claude/git but exercises tracing
# (Use the mock agent image for this)
docker run --rm \
    --network host \
    -e THREAD_ID="$THREAD_ID" \
    -e REDIS_URL="redis://localhost:6399" \
    -e GITHUB_TOKEN="fake" \
    -e ANTHROPIC_API_KEY="fake" \
    ditto-factory/mock-agent:latest

echo "=== Checking result ==="
RESULT=$(redis-cli -h localhost -p 6399 GET "result:$THREAD_ID")

# Validate trace structure
TRACE_ID=$(echo "$RESULT" | jq -r '.trace.trace_id')
PARENT=$(echo "$RESULT" | jq -r '.trace.parent_span_id')
EVENT_COUNT=$(echo "$RESULT" | jq '.trace.trace_events | length')
FIRST_EVENT=$(echo "$RESULT" | jq -r '.trace.trace_events[0].type')
LAST_EVENT=$(echo "$RESULT" | jq -r '.trace.trace_events[-1].type')

echo "Trace ID: $TRACE_ID (expect: aaaa1111bbbb2222cccc3333dddd4444)"
echo "Parent span: $PARENT (expect: 1234567890abcdef)"
echo "Event count: $EVENT_COUNT (expect: >= 4)"
echo "First event: $FIRST_EVENT (expect: AGENT_STARTED)"
echo "Last event: $LAST_EVENT (expect: AGENT_COMPLETED)"

# Assertions
PASS=0; FAIL=0
check() { [ "$1" = "$2" ] && { echo "  PASS: $3"; PASS=$((PASS+1)); } || { echo "  FAIL: $3 (got '$1', want '$2')"; FAIL=$((FAIL+1)); }; }

check "$TRACE_ID" "aaaa1111bbbb2222cccc3333dddd4444" "trace_id propagated"
check "$PARENT" "1234567890abcdef" "parent_span_id propagated"
check "$FIRST_EVENT" "AGENT_STARTED" "first event is AGENT_STARTED"
check "$LAST_EVENT" "AGENT_COMPLETED" "last event is AGENT_COMPLETED"
[ "$EVENT_COUNT" -ge 4 ] && { echo "  PASS: event_count >= 4"; PASS=$((PASS+1)); } || { echo "  FAIL: event_count < 4 ($EVENT_COUNT)"; FAIL=$((FAIL+1)); }

echo ""
echo "Results: $PASS passed, $FAIL failed"

# Cleanup
docker rm -f test-redis
```

### 9.3 Controller-Side Unit Tests (Python)

```python
# controller/tests/jobs/test_trace_collection.py

import pytest
from controller.jobs.monitor import JobMonitor, AgentResult


class TestTraceCollection:
    """Verify the controller correctly parses agent trace events."""

    def test_result_without_trace_is_backward_compatible(self):
        """Old-format results (no trace key) still parse correctly."""
        result_data = {
            "branch": "df/test",
            "exit_code": 0,
            "commit_count": 1,
            "stderr": "",
        }
        result = AgentResult(
            branch=result_data.get("branch", ""),
            exit_code=int(result_data.get("exit_code", 1)),
            commit_count=int(result_data.get("commit_count", 0)),
            stderr=result_data.get("stderr", ""),
            trace=result_data.get("trace"),
        )
        assert result.trace is None
        assert result.exit_code == 0

    def test_result_with_trace_events(self):
        """New-format results with trace events parse correctly."""
        result_data = {
            "branch": "df/test",
            "exit_code": 0,
            "commit_count": 2,
            "stderr": "",
            "trace": {
                "trace_id": "aabb" * 8,
                "agent_span_id": "1122334455667788",
                "parent_span_id": "aabbccddeeff0011",
                "trace_events": [
                    {"type": "AGENT_STARTED", "timestamp": "1000", "span_id": "1122334455667788", "metadata": {}},
                    {"type": "AGENT_COMPLETED", "timestamp": "2000", "span_id": "1122334455667788", "metadata": {"exit_code": 0}},
                ],
            },
        }
        result = AgentResult(
            branch=result_data["branch"],
            exit_code=result_data["exit_code"],
            commit_count=result_data["commit_count"],
            trace=result_data.get("trace"),
        )
        assert result.trace is not None
        assert result.trace["trace_id"] == "aabb" * 8
        assert len(result.trace["trace_events"]) == 2
        assert result.trace["trace_events"][0]["type"] == "AGENT_STARTED"

    def test_trace_parent_span_links_to_orchestrator(self):
        """Agent trace's parent_span_id matches orchestrator's AGENT_SPAWNED span."""
        orchestrator_span_id = "abcdef0123456789"
        trace = {
            "trace_id": "1234" * 8,
            "agent_span_id": "fedcba9876543210",
            "parent_span_id": orchestrator_span_id,
            "trace_events": [],
        }
        assert trace["parent_span_id"] == orchestrator_span_id
```

### 9.4 End-to-End Verification Checklist

| # | Check | How to Verify |
|---|---|---|
| 1 | Trace helpers produce valid JSON | `bash test_trace_helpers.sh` |
| 2 | `trace_id` + `parent_span_id` propagate from task to result | Integration test with mock Redis |
| 3 | All expected events emitted in order | Assert event types in result JSON |
| 4 | Timestamps are monotonically increasing | Parse and compare in integration test |
| 5 | `duration_ms` values are positive and reasonable | Assert > 0 and < timeout |
| 6 | Tracing failure does not crash agent | Kill redis mid-trace, verify agent still completes |
| 7 | Old controller handles new result format | Deploy new agent with old controller -- `trace` key ignored |
| 8 | New controller handles old result format | Deploy old agent with new controller -- `trace` is None |
| 9 | Stdout excerpt truncated at 10KB | Send task that generates > 10KB output, verify truncation |
| 10 | Mock agent produces valid trace structure | Run mock agent integration test |

---

## Appendix A: Migration / Rollout Strategy

### Phase 3a: Ship entrypoint changes (agent-side only)
- Update `entrypoint.sh` with tracing instrumentation
- Update mock agent with matching trace structure
- Result payload gains `trace` key -- controller ignores it (backward compatible)
- **Risk**: zero. Extra JSON key in Redis is harmless.

### Phase 3b: Ship controller collection
- Update `AgentResult` dataclass with optional `trace` and `stdout_excerpt` fields
- Update `JobMonitor.wait_for_result()` to extract trace data
- Add `_collect_agent_traces()` to orchestrator
- **Risk**: low. New fields are optional with None defaults.

### Phase 3c: Ship mock agent updates
- Update mock agent to emit synthetic trace events
- Update integration tests to verify trace structure
- **Risk**: zero. Test infrastructure only.

## Appendix B: Future Enhancements (Out of Scope)

| Enhancement | Value | Complexity |
|---|---|---|
| Stream trace events via Redis XADD during execution | Real-time visibility | Medium -- requires agent to emit mid-flight |
| Parse `--output-format json` from Claude CLI | Structured tool call data | Medium -- depends on CLI stability |
| Query Anthropic usage API for token counts | Cost tracking | Low -- separate HTTP call from controller |
| Git commit polling during Claude execution | Progress tracking | High -- requires background process in bash |
| Sidecar container for structured tracing | OpenTelemetry export | High -- architectural change |
