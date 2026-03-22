# Traceability & Reporting Analysis for Ditto Factory

**Date:** 2026-03-21
**Branch:** feat/skill-hotloading-phase2
**Goal:** Determine the most appropriate way to trace all agent activity (including reasoning) and compile it into readable form for review.

---

## Current State Assessment

### What's Already Captured

| Layer | What's Stored | Where |
|-------|--------------|-------|
| **Job lifecycle** | id, thread_id, status, skills_injected, result, started_at/completed_at | PostgreSQL/SQLite |
| **Thread history** | conversation_history, source, repo_owner/name | PostgreSQL/SQLite |
| **Agent result** | exit_code, commit_count, stderr, branch | Redis → DB |
| **Skill performance** | usage_count, success_rate, avg_commits, pr_creation_rate | SQLite (skill_usage) |

### Critical Gaps

| Gap | Impact |
|-----|--------|
| **No decision tracing** | Can't see *why* skills were selected (no classification scores logged) |
| **No reasoning capture** | Claude's thinking/tool invocations during execution are lost |
| **No intermediate steps** | Only final result persisted — no step-by-step trace |
| **No trace correlation** | No trace_id linking request → classify → inject → execute → report |
| **No report generation** | Engineers can't review a readable execution trace |

---

## Recommended Approach: Structured Trace Spans + Markdown Reports

Inspired by the [Open Agent Harness](https://tedahn.github.io/open-agent-harness-page/) architecture, which advocates for **full session traces** where the engineer sees everything — not just the PR diff, but the original request, gathered context, agent reasoning, and code changes as a single reviewable artifact.

### 1. Trace Data Model

```python
@dataclass
class TraceSpan:
    span_id: str           # unique span identifier
    trace_id: str          # shared across entire request lifecycle
    parent_span_id: str | None  # for hierarchical nesting
    operation_name: str    # "invoke_agent", "execute_tool", "classify_task", "inject_skills"
    agent_name: str | None # "orchestrator", "classifier", "agent-pod"

    started_at: datetime
    ended_at: datetime | None

    input_summary: str     # what went in (truncated for readability)
    output_summary: str    # what came out
    reasoning: str | None  # agent's reasoning/thinking (when available)

    tool_name: str | None  # for tool invocation spans
    tool_args: dict | None
    tool_result: str | None

    model: str | None      # "claude-opus-4-6", etc.
    tokens_input: int | None
    tokens_output: int | None

    status: str            # "ok", "error", "timeout"
    error_type: str | None

    # Correlation
    thread_id: str
    job_id: str | None
```

This aligns with **OpenTelemetry GenAI semantic conventions** (`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.*`) so it can export to OTel-compatible backends later.

### 2. Instrumentation Points (Zero Pollution of Business Logic)

Three integration patterns that don't touch existing orchestrator logic:

#### a) Context Managers — orchestrator-level spans
```python
async with trace_span("classify_task", trace_id=trace_id) as span:
    result = await classifier.classify(task)
    span.output_summary = f"matched {len(result.skills)} skills"
    span.reasoning = f"top_score={result.scores[0]:.3f}, method={result.method}"
```

#### b) Decorators — automatic tool/function tracing
```python
@traced("execute_tool")
async def run_eslint(file_path: str) -> LintResult:
    ...
```

#### c) Claude Client Wrapper — capture every LLM call
```python
class TracedClaudeClient:
    """Wraps Claude API calls to emit trace spans with token counts and reasoning."""
    async def create_message(self, **kwargs):
        with trace_span("chat", model=kwargs.get("model")) as span:
            response = await self.client.messages.create(**kwargs)
            span.tokens_input = response.usage.input_tokens
            span.tokens_output = response.usage.output_tokens
            # Capture thinking blocks if extended thinking enabled
            span.reasoning = extract_thinking(response)
            return response
```

### 3. Cross-Process Propagation (Orchestrator → Agent Pod)

The existing Redis task payload gains two fields:

```python
# In orchestrator, before pushing to Redis:
redis_payload = {
    "task": task_text,
    "system_prompt": system_prompt,
    "skills": formatted_skills,
    "repo_url": repo_url,
    # NEW: trace context propagation
    "trace_id": trace_id,
    "parent_span_id": orchestrator_span.span_id,
}
```

Agent pods reconstruct `TraceContext` from the payload and emit child spans back via **Redis Streams** (`traces:{thread_id}`). The controller collects them on job completion.

### 4. Storage Pipeline

```
Agent Pod → Redis Streams (traces:{thread_id})  [hot path, ephemeral]
     ↓
Controller collects on job completion
     ↓
SQLite trace_spans table                         [warm storage, queryable]
     ↓
Markdown report rendered                         [cold storage, reviewable]
     ↓
(Optional) Export to Langfuse/Phoenix            [visualization UI]
```

This matches the existing architecture: Redis for cross-process communication, SQLite for persistent state.

### 5. Report Compilation: Three Views

#### a) Hierarchical View (default)
Shows the request tree — orchestrator → sub-agents → tool calls:

```markdown
# Execution Trace: thread-abc123
**Request:** "fix the login bug on mobile"
**Duration:** 4m 32s | **Tokens:** 12,430 in / 3,210 out | **Status:** SUCCESS

## 1. classify_task (180ms)
- **Method:** semantic search (Voyage-3)
- **Scores:** mobile_auth_sdk=0.87, session_replay=0.72, ui_lint=0.65
- **Selected:** mobile_auth_sdk, session_replay (threshold: 0.5)
- **Reasoning:** High semantic match on "login" + "mobile" keywords

## 2. inject_skills (45ms)
- **Skills:** mobile_auth_sdk (2.1KB), session_replay (1.8KB)
- **Budget:** 3.9KB / 16KB

## 3. spawn_agent (2.1s)
- **Image:** general:latest
- **K8s Job:** agent-a1b2c3

## 4. agent_execution (4m 28s)
  ### 4.1 gather_context
  - Read MOBILE-4821 from Jira
  - Searched git history for session-related changes

  ### 4.2 tool: file_read (src/auth/sessionManager.ts)
  - **Reasoning:** "The bug report mentions session expiry. Let me check the token refresh logic."

  ### 4.3 tool: file_edit (src/auth/sessionManager.ts)
  - **Change:** Added timezone offset to token.refresh()
  - **Reasoning:** "The refresh was using Date.now() without accounting for timezone offset on iOS 17"

  ### 4.4 tool: run_tests (auth.test.ts)
  - **Result:** 14/14 passing

## 5. result
- **Branch:** df/thread-abc123
- **Commits:** 1
- **PR:** #247
```

#### b) Timeline View
Chronological with timestamps — useful for debugging latency:

```markdown
| Time | Duration | Operation | Details |
|------|----------|-----------|---------|
| 00:00.000 | 180ms | classify_task | 2 skills matched |
| 00:00.180 | 45ms | inject_skills | 3.9KB injected |
| 00:00.225 | 2.1s | spawn_agent | K8s job created |
| 00:02.325 | 12s | gather_context | Jira + git history |
| 00:14.325 | 3s | file_read | sessionManager.ts |
| 00:17.325 | 8s | file_edit | +1 -1 lines |
| 00:25.325 | 45s | run_tests | 14/14 pass |
| ... | ... | ... | ... |
```

#### c) Decision Tree View
For understanding branching reasoning — why the agent chose path A over B:

```markdown
## Decision: Which file to modify?
- Considered: sessionManager.ts, authMiddleware.ts, tokenStore.ts
- Selected: sessionManager.ts
- Reason: "Stack trace in bug report points to refresh() call at line 47"
- Alternatives rejected: "authMiddleware handles routing only, tokenStore is read-only"
```

### 6. Implementation Phases

| Phase | Scope | Effort |
|-------|-------|--------|
| **Phase 1: Foundation** | TraceSpan model, SQLite storage, trace_id propagation in Redis payload, context managers in orchestrator | 1-2 weeks |
| **Phase 2: Agent-side** | Wrap Claude client for token/reasoning capture, emit spans via Redis Streams, tool call tracing | 2-3 weeks |
| **Phase 3: Reports** | Markdown report generator (hierarchical + timeline views), CLI command to view traces | 1-2 weeks |
| **Phase 4: Export** | Optional Langfuse/Phoenix integration for UI visualization | 1 week |

### 7. OSS Visualization Options (Phase 4)

| Tool | Fit | Notes |
|------|-----|-------|
| **Langfuse** | Best | Lightweight, Anthropic-compatible, self-hostable, trace trees |
| **Arize Phoenix** | Good | OTel-native, great UI, heavier setup |
| **W&B Weave** | Decent | Good for ML teams, overkill for pure agent tracing |
| **LangSmith** | Poor fit | LangChain-centric, vendor lock-in |

**Key insight:** None of these tools generate review documents. They provide visualization UIs. The Markdown report generation in Phase 3 is custom regardless — it's the most important piece for engineer review.

---

## Connection to Open Agent Harness Architecture

The [Open Agent Harness](https://tedahn.github.io/open-agent-harness-page/) identifies the same core problem: **"Zero Auditability — No trace of what the agent did or why."**

Their solution maps directly to what we need:

| Harness Concept | Ditto Factory Equivalent |
|----------------|--------------------------|
| **Session Trace** (request + context + reasoning) | TraceSpan hierarchy per thread |
| **Script Run vs Coding Agent Run** (deterministic vs stochastic) | operation_name distinguishes orchestrator steps from agent reasoning |
| **Engineer Review** (sees everything, not just PR diff) | Markdown report with all 3 views |
| **Versioned Workflow Templates** | Skill injection + agent type resolution (already exists) |
| **Narrowed Decision Surface** | Scoped tools + skill budget (already exists at 16KB) |

The key architectural alignment: **code controls, agent executes.** The trace makes this verifiable.

---

## Recommendation

**Start with Phase 1 + Phase 3 together.** The trace model and report generator are the highest-value pieces. Agent-side instrumentation (Phase 2) requires changes to the entrypoint bash script and possibly Claude API integration for reasoning capture — that's the hardest part and can follow.

The most impactful quick win: **log classification scores and skill selection reasoning in the orchestrator.** This is 20 lines of code and immediately answers the most common review question: "why did it pick these skills?"
