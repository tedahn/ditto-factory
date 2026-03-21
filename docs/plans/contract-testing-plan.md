# Contract-Based Integration Testing Plan

## Approach 2: Contract Testing for Ditto Factory

**Status**: Proposed
**Date**: 2026-03-21
**Scope**: All service boundaries in the controller pipeline

---

## Part 1: Contract Analysis

### Contract 1: Webhook -> Integration Parser

| Aspect | Detail |
|--------|--------|
| **Contract** | Raw HTTP request (headers + JSON body) -> `TaskRequest` dataclass |
| **Provider** | External service (GitHub, Slack, Linear) |
| **Consumer** | `Integration.parse_webhook(request) -> TaskRequest \| None` |
| **Data shape (in)** | Provider-specific: GitHub sends `x-hub-signature-256` header + event JSON; Slack sends `x-slack-signature` + `x-slack-request-timestamp` + URL-encoded body; Linear sends `linear-signature` + JSON body |
| **Data shape (out)** | `TaskRequest(thread_id: str, source: str, source_ref: dict, repo_owner: str, repo_name: str, task: str, conversation: list[str], images: list[str])` |

**Invariants**:
- `thread_id` is deterministic (SHA256 of source + identifiers) and never empty
- `source` is one of `"slack"`, `"github"`, `"linear"`
- `source_ref` contains enough context to report back (e.g., `channel`+`thread_ts` for Slack, `number`+`type` for GitHub)
- `repo_owner` and `repo_name` are non-empty for GitHub/Linear; may need manual resolution for Slack
- `task` is non-empty (messages with no actionable content return `None`)
- Signature verification rejects tampered payloads (returns `None`)

**Failure modes**:
- Malformed JSON body -> parse error, should return `None` not crash
- Missing signature header -> reject (return `None`)
- Invalid signature -> reject (return `None`)
- Bot's own messages -> must be filtered (infinite loop prevention)
- Unknown event type -> return `None`

---

### Contract 2: Integration -> Orchestrator

| Aspect | Detail |
|--------|--------|
| **Contract** | `TaskRequest` -> Thread lifecycle side effects |
| **Provider** | Integration (produces `TaskRequest`) |
| **Consumer** | `Orchestrator.handle_task(task_request: TaskRequest) -> None` |
| **Data shape** | `TaskRequest` as defined above |

**Invariants**:
- Orchestrator must be idempotent for the same `thread_id` when a job is already active (queues instead of spawning)
- Thread is created if it does not exist
- Lock is always released (even on error) -- guaranteed by `try/finally`
- Exactly one of: spawn job, queue message, or reject

**Failure modes**:
- `thread_id` collision across sources -> must not happen (SHA256 includes source prefix)
- State backend unavailable -> unhandled exception propagates to FastAPI error handler
- Lock acquisition fails -> message is queued (correct degradation)

---

### Contract 3: Orchestrator -> State Backend

| Aspect | Detail |
|--------|--------|
| **Contract** | CRUD operations on `Thread` and `Job` via `StateBackend` protocol |
| **Provider** | Orchestrator (calls state methods) |
| **Consumer** | `StateBackend` implementations (SQLite, Postgres) |
| **Data shapes** | `Thread` dataclass (in/out), `Job` dataclass (in/out), `ThreadStatus` enum, `JobStatus` enum |

**Protocol methods and their contracts**:

| Method | Input | Output | Invariant |
|--------|-------|--------|-----------|
| `get_thread(thread_id)` | `str` | `Thread \| None` | Returns `None` for unknown IDs, never raises |
| `upsert_thread(thread)` | `Thread` | `None` | Creates or updates; `thread.id` is the key |
| `update_thread_status(thread_id, status, job_name)` | `str, ThreadStatus, str\|None` | `None` | Thread must exist; status transition must be valid |
| `create_job(job)` | `Job` | `None` | `job.id` must be unique; `job.thread_id` must reference existing thread |
| `get_job(job_id)` | `str` | `Job \| None` | Returns `None` for unknown IDs |
| `get_active_job_for_thread(thread_id)` | `str` | `Job \| None` | Returns job with status PENDING or RUNNING |
| `update_job_status(job_id, status, result)` | `str, JobStatus, dict\|None` | `None` | Job must exist; updates status and optional result dict |
| `try_acquire_lock(thread_id)` | `str` | `bool` | Must be reentrant-safe; returns `False` if already locked |
| `release_lock(thread_id)` | `str` | `None` | Idempotent; releasing unlocked thread is a no-op |
| `append_conversation(thread_id, message)` | `str, dict` | `None` | Appends to ordered list |
| `get_conversation(thread_id, limit)` | `str, int` | `list[dict]` | Returns most recent `limit` messages |

**Known application bug**: `handle_job_completion` never calls `update_job_status` to mark jobs as COMPLETED/FAILED. The job stays in RUNNING status indefinitely. Contract tests should document this expected behavior and flag it as a defect to fix.

**Failure modes**:
- SQLite file lock contention under concurrent access
- Postgres connection pool exhaustion
- Schema drift between implementations (SQLite vs Postgres must honor same contract)

---

### Contract 4: Orchestrator -> JobSpawner

| Aspect | Detail |
|--------|--------|
| **Contract** | Thread context -> K8s Job name |
| **Provider** | Orchestrator (calls `spawner.spawn(...)`) |
| **Consumer** | `JobSpawner.spawn(thread_id, github_token, redis_url) -> str` |
| **Data shape (in)** | `thread_id: str, github_token: str, redis_url: str` |
| **Data shape (out)** | `str` (K8s job name, format: `df-{short_id}-{timestamp}`) |

**Invariants**:
- Job name is valid K8s resource name (alphanumeric + hyphens, max 63 chars)
- Job name contains sanitized `thread_id[:8]` prefix for traceability
- Container env vars include `THREAD_ID`, `REDIS_URL`, `GITHUB_TOKEN`
- Job spec has `backoff_limit=1`, `restart_policy="Never"`, security context with `run_as_non_root=True`
- `active_deadline_seconds` matches `settings.max_job_duration_seconds`

**Failure modes**:
- K8s API unavailable -> `ApiException` propagates
- Job name collision (same thread_id + same timestamp second) -> K8s rejects with 409
- Invalid characters in thread_id -> `_sanitize_label` must handle

---

### Contract 5: JobSpawner -> Redis (Task Context Serialization)

| Aspect | Detail |
|--------|--------|
| **Contract** | Task context dict -> Redis key `task:{thread_id}` |
| **Provider** | Orchestrator (calls `redis_state.push_task(thread_id, context)`) |
| **Consumer** | Agent container (reads `task:{thread_id}` from Redis) |
| **Data shape** | JSON dict: `{"task": str, "system_prompt": str, "repo_url": str, "branch": str}` |

**Invariants**:
- Key format is exactly `task:{thread_id}`
- TTL is 3600 seconds (1 hour)
- JSON is valid and deserializable
- All four fields are present and non-null
- `repo_url` is a valid `https://github.com/{owner}/{repo}.git` URL
- `branch` follows naming convention `df/{source}/{short_hash}`

**Failure modes**:
- Redis unavailable -> connection error propagates
- TTL expires before agent reads -> agent gets `None`, job fails silently
- JSON serialization error (unlikely with dict of strings)
- Agent expects different key schema -> silent data loss

---

### Contract 6: Agent -> Redis (AgentResult Serialization)

| Aspect | Detail |
|--------|--------|
| **Contract** | Agent writes result dict to Redis key `result:{thread_id}` |
| **Provider** | Agent container (external process) |
| **Consumer** | `JobMonitor.wait_for_result()` -> `AgentResult` |
| **Data shape** | JSON dict: `{"branch": str, "exit_code": int, "commit_count": int, "stderr": str}` |

**Invariants**:
- Key format is exactly `result:{thread_id}`
- TTL is 3600 seconds
- `exit_code` is an integer (0 = success)
- `commit_count` is a non-negative integer
- `branch` matches the branch from the task context
- `stderr` may be empty string but must be present

**Failure modes**:
- Agent crashes without writing result -> monitor times out (1800s default)
- Agent writes malformed JSON -> `json.loads` raises, monitor returns `None`
- Agent writes extra/missing fields -> `dict.get()` with defaults handles gracefully
- Type coercion issues: `exit_code` as string "0" vs int 0 -> monitor does `int()` cast

---

### Contract 7: Redis -> JobMonitor (Result Polling)

| Aspect | Detail |
|--------|--------|
| **Contract** | Poll `result:{thread_id}` until data appears or timeout |
| **Provider** | Redis (stores result written by agent) |
| **Consumer** | `JobMonitor.wait_for_result(thread_id, timeout, poll_interval) -> AgentResult \| None` |
| **Data shape (out)** | `AgentResult(branch: str, exit_code: int, commit_count: int, stderr: str, pr_url: str\|None)` |

**Invariants**:
- Returns `None` on timeout (never raises)
- `exit_code` and `commit_count` are cast to `int`
- `pr_url` is NOT populated by monitor (always `None` at this stage; set later by SafetyPipeline)
- Poll interval and timeout are configurable
- Result is consumed but NOT deleted from Redis (TTL handles cleanup)

**Failure modes**:
- Redis connection drops during polling -> unhandled exception
- Result appears then TTL expires between polls -> unlikely but possible race
- Partial JSON write (network split) -> corrupt data

---

### Contract 8: JobMonitor -> SafetyPipeline

| Aspect | Detail |
|--------|--------|
| **Contract** | `AgentResult` + `Thread` -> side effects (PR creation, reporting, cleanup) |
| **Provider** | Orchestrator (passes result from monitor to pipeline) |
| **Consumer** | `SafetyPipeline.process(thread, result, retry_count) -> None` |
| **Data shape (in)** | `Thread` (must have valid `id`, `source`, `source_ref`, `repo_owner`, `repo_name`), `AgentResult` (as above) |

**Invariants**:
- If `commit_count > 0` AND no `pr_url` AND `auto_open_pr=True` -> creates PR
- If `commit_count == 0` AND `exit_code == 0` AND retries available -> re-spawns (anti-stall)
- Always reports result to integration (unless retrying)
- Always resets thread status to `IDLE` after processing (unless retrying)
- Always drains queued messages after completion

**Failure modes**:
- PR creation fails -> exception is caught, logged, result reported without `pr_url`
- Integration reporting fails -> unhandled, thread stays in non-IDLE state
- Retry spawner reference is a bound method -> must match expected signature

---

### Contract 9: SafetyPipeline -> GitHub Client

| Aspect | Detail |
|--------|--------|
| **Contract** | Branch name -> PR URL |
| **Provider** | SafetyPipeline (calls `self._github_client.create_pr(...)`) |
| **Consumer** | `GitHubIntegration.create_pr(owner, repo, branch, title, body, base="main")` (client abstraction, not raw API) |
| **Data shape (in)** | `owner: str, repo: str, branch: str, title: str, body: str, base: str` |
| **Data shape (out)** | `str` (PR URL) |

**Note**: SafetyPipeline currently calls `create_pr(owner=, repo=, branch=)` with only 3 kwargs, but `GitHubIntegration.create_pr` requires `title` and `body` as well. This is a signature mismatch bug in the application.

**Invariants**:
- Branch must exist on remote before PR creation
- PR is created against default branch
- Returns full URL (e.g., `https://github.com/org/repo/pull/99`)

**Failure modes**:
- Branch doesn't exist -> 422 from GitHub
- PR already exists for branch -> 422 from GitHub
- Rate limiting -> 403 from GitHub
- Auth token expired -> 401 from GitHub

---

### Contract 10: SafetyPipeline -> Integration (Result Reporting)

| Aspect | Detail |
|--------|--------|
| **Contract** | `Thread` + `AgentResult` -> notification to source |
| **Provider** | SafetyPipeline (calls `integration.report_result(thread, result)`) |
| **Consumer** | Integration implementation (Slack posts message, GitHub posts comment) |
| **Data shape (in)** | `Thread` (for routing: `source_ref`), `AgentResult` (for content) |

**Invariants**:
- Message format depends on `exit_code`: success shows branch/PR/commits, failure shows stderr
- `source_ref` contains enough routing info (Slack: `channel`+`thread_ts`, GitHub: `number`)
- Method is async and may make HTTP calls

**Failure modes**:
- Integration API unavailable -> HTTP error propagates
- `source_ref` missing required fields -> KeyError
- Rate limiting from Slack/GitHub API

---

### Contract 11: Orchestrator -> Redis Queue (Follow-up Messages)

| Aspect | Detail |
|--------|--------|
| **Contract** | Messages queued during active job, drained after completion |
| **Provider** | Orchestrator (calls `redis_state.queue_message` and `drain_messages`) |
| **Consumer** | Orchestrator (processes drained messages as new tasks) |
| **Data shape** | Queue key: `queue:{thread_id}`, values: raw task strings |
| **Operations** | `RPUSH` to enqueue, `LRANGE+DELETE` (pipeline) to drain atomically |

**Invariants**:
- FIFO ordering preserved (RPUSH + LRANGE 0 -1)
- Drain is atomic (pipeline: read all + delete in one round-trip)
- Drained messages are processed sequentially as follow-up tasks
- Empty queue returns empty list (not `None`)

**Failure modes**:
- Redis unavailable during enqueue -> message lost, no retry
- Drain partial failure -> messages lost (pipeline is not transactional)
- Thread deleted between enqueue and drain -> orphaned messages (cleaned by TTL if set)
- No TTL on queue keys -> potential memory leak for abandoned threads

---

### Contract 12: Integration Protocol Conformance

| Aspect | Detail |
|--------|--------|
| **Contract** | All integration implementations must satisfy the `Integration` protocol |
| **Provider** | `GitHubIntegration`, `SlackIntegration`, `LinearIntegration` |
| **Consumer** | Orchestrator, SafetyPipeline (call protocol methods polymorphically) |

**Protocol methods** (from `controller/integrations/protocol.py`):

| Method | Input | Output | Invariant |
|--------|-------|--------|-----------|
| `parse_webhook(request)` | `Request` | `TaskRequest \| None` | Returns `None` on invalid/filtered input; never raises |
| `fetch_context(thread)` | `Thread` | `str` | Returns empty string on failure; never raises |
| `report_result(thread, result)` | `Thread, AgentResult` | `None` | Must not raise on HTTP errors (log and continue) |
| `acknowledge(request)` | `Request` | `None` | Best-effort; failure is non-fatal |

**Invariants**:
- `name` attribute must be one of `"github"`, `"slack"`, `"linear"`
- All implementations must be `@runtime_checkable` protocol conformant
- `parse_webhook` must verify cryptographic signatures before processing
- `report_result` message format depends on `exit_code` and `commit_count`

**Failure modes**:
- Implementation adds new methods not in protocol -> silent drift
- Implementation changes signature (e.g., adds required params) -> breaks polymorphic calls
- `report_result` raises on HTTP error -> thread stuck in non-IDLE state

---

### Contract 13: Orchestrator.handle_job_completion Flow

| Aspect | Detail |
|--------|--------|
| **Contract** | Job completion notification -> result processing + cleanup |
| **Provider** | External trigger (K8s watch or webhook) |
| **Consumer** | `Orchestrator.handle_job_completion(thread_id: str) -> None` |

**Flow**:
1. Retrieves thread from state backend (`get_thread`)
2. Waits for result via `JobMonitor.wait_for_result` (60s timeout, 1s poll)
3. Resolves integration from `thread.source` via registry
4. Constructs a new `SafetyPipeline` instance per call
5. Passes `self._spawn_job` (bound method) as the spawner callable

**Invariants**:
- Returns early (no crash) if thread not found
- Returns early if no result within timeout
- Returns early if no integration for the thread's source
- SafetyPipeline receives `self._spawn_job` as spawner callable

**Known bug**: `self._spawner(thread.id, is_retry=True, retry_count=...)` in `safety.py:36` calls the spawner with `thread.id` (a string), but `_spawn_job` expects `(thread: Thread, task_request: TaskRequest, ...)`. This is a type mismatch that will cause a runtime error on the retry path. Contract tests must verify the spawner callable interface matches.

**Failure modes**:
- Thread deleted between job start and completion -> early return
- Monitor timeout (agent crashed without writing result) -> early return, thread stays in RUNNING status
- Integration not found (registry misconfigured) -> early return, thread stays in RUNNING status

---

### Contract 14: Redis Stream Events

| Aspect | Detail |
|--------|--------|
| **Contract** | Agent appends real-time status events; consumers read the stream |
| **Provider** | Agent container (calls `append_stream_event`) |
| **Consumer** | Any stream reader (calls `read_stream`) |
| **Data shape** | Stream key: `agent:{thread_id}`, fields: `{"event": str}` |
| **Operations** | `XADD` to append, `XRANGE` to read |

**Invariants**:
- Stream key format is exactly `agent:{thread_id}`
- Each entry has an `event` field with a string value
- `read_stream` with `last_id="0"` returns all events
- Events are ordered by Redis-generated stream IDs
- Stream entries decode bytes to strings transparently

**Failure modes**:
- No TTL on stream keys -> memory leak for abandoned threads (same issue as queue keys)
- Large number of events per thread -> unbounded stream growth
- `last_id` cursor not persisted -> consumer re-reads all events on restart

---

## Part 2: Contract Test Plan

### Recommended Tooling

| Tool | Purpose | Why |
|------|---------|-----|
| **pytest + pytest-asyncio** | Test runner | Already in use, async-native |
| **fakeredis** | Redis contract tests | Already a dev dependency, in-process |
| **pydantic / dataclass validation** | Schema enforcement | Dataclasses are already used; add validation |
| **JSON Schema** | Cross-process contract (Agent <-> Controller) | Agent is a separate process; schema is the lingua franca |
| **hypothesis** | Property-based testing for serialization | Catches edge cases in type coercion |
| **pytest markers** | `@pytest.mark.contract` | Separate contract tests in CI |

**Note on Pact**: Pact was considered but is not recommended for this codebase. Both producer and consumer live in the same repository (monorepo), so Pact's broker-based workflow adds CI complexity without proportional value. JSON Schema validation plus the fixture-based contract test suite above is sufficient. Reconsider Pact only if the agent container moves to a separate repository.

### Test File Organization

```
controller/tests/
  contracts/
    __init__.py
    conftest.py                    # Shared fixtures for contract tests
    test_webhook_contracts.py      # Contract 1: Webhook -> Integration
    test_orchestrator_contracts.py # Contract 2: Integration -> Orchestrator
    test_state_contracts.py        # Contract 3: Orchestrator -> State
    test_spawner_contracts.py      # Contract 4: Orchestrator -> JobSpawner
    test_redis_contracts.py        # Contracts 5,6,7,11: All Redis boundaries
    test_safety_contracts.py       # Contracts 8,9,10: Safety pipeline
    test_integration_protocol_contracts.py  # Contract 12: Integration protocol conformance
    test_job_completion_contracts.py        # Contract 13: handle_job_completion flow
    test_stream_contracts.py               # Contract 14: Redis stream events
    test_negative_contracts.py             # Negative/failure path tests
    test_e2e_contract.py           # Part 3: Full pipeline contract E2E
  schemas/
    task_context.schema.json       # JSON Schema for task:{thread_id}
    agent_result.schema.json       # JSON Schema for result:{thread_id}
```

---

### Contract 1 Tests: Webhook -> Integration Parser

```python
# tests/contracts/test_webhook_contracts.py

import pytest
from dataclasses import fields
from controller.models import TaskRequest

REQUIRED_TASK_REQUEST_FIELDS = {"thread_id", "source", "source_ref", "repo_owner", "repo_name", "task"}
VALID_SOURCES = {"slack", "github", "linear"}


class TaskRequestContractValidator:
    """Reusable validator for any integration's parse_webhook output."""

    @staticmethod
    def validate(task_req: TaskRequest | None, *, allow_none: bool = False):
        if task_req is None:
            assert allow_none, "parse_webhook returned None unexpectedly"
            return

        # All required fields are non-empty strings
        assert isinstance(task_req.thread_id, str) and len(task_req.thread_id) > 0
        assert task_req.source in VALID_SOURCES
        assert isinstance(task_req.source_ref, dict) and len(task_req.source_ref) > 0
        assert isinstance(task_req.task, str) and len(task_req.task) > 0

        # Thread ID is deterministic (SHA256 hex)
        assert len(task_req.thread_id) == 64, f"thread_id should be SHA256 hex, got len={len(task_req.thread_id)}"

        # Conversation and images are lists
        assert isinstance(task_req.conversation, list)
        assert isinstance(task_req.images, list)


class TestGitHubWebhookContract:
    """Verify GitHub webhook payloads produce valid TaskRequests."""

    @pytest.fixture
    def github_integration(self):
        from controller.integrations.github import GitHubIntegration
        return GitHubIntegration(
            webhook_secret="test-secret",
            allowed_orgs=["testorg"],
        )

    async def test_issue_comment_produces_valid_task_request(self, github_integration):
        """Contract: issue_comment event -> TaskRequest with source='github'."""
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Bug", "body": "desc"},
            "comment": {"body": "please fix this", "user": {"login": "human", "type": "User"}},
            "repository": {"full_name": "testorg/myrepo"},
        }
        request = make_signed_request("issue_comment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)

        TaskRequestContractValidator.validate(result)
        assert result.source == "github"
        assert result.repo_owner == "testorg"
        assert result.repo_name == "myrepo"
        assert "number" in result.source_ref

    async def test_bot_message_returns_none(self, github_integration):
        """Contract: bot messages must be filtered to prevent loops."""
        payload = {
            "action": "created",
            "issue": {"number": 1, "title": "T", "body": ""},
            "comment": {"body": "bot response", "user": {"login": "ditto-bot", "type": "Bot"}},
            "repository": {"full_name": "testorg/repo"},
        }
        request = make_signed_request("issue_comment", payload, "test-secret")
        result = await github_integration.parse_webhook(request)
        TaskRequestContractValidator.validate(result, allow_none=True)

    async def test_invalid_signature_returns_none(self, github_integration):
        """Contract: tampered payload must be rejected."""
        payload = {"action": "created", "comment": {"body": "x"}, "repository": {"full_name": "testorg/r"}}
        request = make_signed_request("issue_comment", payload, "wrong-secret")
        result = await github_integration.parse_webhook(request)
        assert result is None

    async def test_thread_id_determinism(self, github_integration):
        """Contract: same input always produces same thread_id."""
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Bug", "body": ""},
            "comment": {"body": "fix it", "user": {"login": "dev", "type": "User"}},
            "repository": {"full_name": "testorg/repo"},
        }
        req1 = make_signed_request("issue_comment", payload, "test-secret")
        req2 = make_signed_request("issue_comment", payload, "test-secret")
        r1 = await github_integration.parse_webhook(req1)
        r2 = await github_integration.parse_webhook(req2)
        assert r1.thread_id == r2.thread_id


# Repeat similar pattern for SlackWebhookContract, LinearWebhookContract
```

Helper for creating signed mock requests:

```python
# tests/contracts/conftest.py

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
import pytest


def make_signed_request(event_type: str, payload: dict, secret: str) -> AsyncMock:
    """Create a mock FastAPI Request with valid GitHub webhook signature."""
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = AsyncMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "x-github-event": event_type,
        "x-hub-signature-256": sig,
    }
    return request


def make_slack_signed_request(payload: dict, secret: str) -> AsyncMock:
    """Create a mock FastAPI Request with valid Slack signature."""
    body_str = f"payload={json.dumps(payload)}"
    body = body_str.encode()
    ts = str(int(time.time()))
    sig_basestring = f"v0:{ts}:{body_str}"
    sig = "v0=" + hmac.new(secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    request = AsyncMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": sig,
    }
    return request


# Shared fixtures
@pytest.fixture
def valid_task_request():
    from controller.models import TaskRequest
    return TaskRequest(
        thread_id="a" * 64,
        source="github",
        source_ref={"type": "issue_comment", "number": 42},
        repo_owner="testorg",
        repo_name="myrepo",
        task="fix the bug",
    )


@pytest.fixture
def valid_agent_result_dict():
    """The canonical shape an agent MUST write to Redis."""
    return {
        "branch": "df/github/abc12345",
        "exit_code": 0,
        "commit_count": 3,
        "stderr": "",
    }


@pytest.fixture
def valid_task_context_dict():
    """The canonical shape the controller writes to Redis for the agent."""
    return {
        "task": "fix the login bug",
        "system_prompt": "You are a coding agent...",
        "repo_url": "https://github.com/testorg/myrepo.git",
        "branch": "df/github/abc12345",
    }
```

---

### Contract 3 Tests: State Backend Protocol Conformance

```python
# tests/contracts/test_state_contracts.py

import pytest
from datetime import datetime, timezone
from controller.models import Thread, Job, ThreadStatus, JobStatus


class StateBackendContractSuite:
    """
    Abstract contract test suite. Run against EVERY StateBackend implementation.
    Subclass and provide a `backend` fixture.
    """

    async def test_get_nonexistent_thread_returns_none(self, backend):
        result = await backend.get_thread("does-not-exist")
        assert result is None

    async def test_upsert_then_get_thread(self, backend):
        thread = Thread(
            id="t1", source="github", source_ref={"number": 1},
            repo_owner="org", repo_name="repo",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await backend.upsert_thread(thread)
        retrieved = await backend.get_thread("t1")
        assert retrieved is not None
        assert retrieved.id == "t1"
        assert retrieved.source == "github"
        assert retrieved.repo_owner == "org"

    async def test_upsert_is_idempotent(self, backend):
        thread = Thread(id="t2", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.upsert_thread(thread)  # Should not raise
        retrieved = await backend.get_thread("t2")
        assert retrieved is not None

    async def test_update_thread_status(self, backend):
        thread = Thread(id="t3", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.update_thread_status("t3", ThreadStatus.RUNNING, job_name="df-job-1")
        retrieved = await backend.get_thread("t3")
        assert retrieved.status == ThreadStatus.RUNNING
        assert retrieved.current_job_name == "df-job-1"

    async def test_create_job_and_get_active(self, backend):
        thread = Thread(id="t4", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j1", thread_id="t4", k8s_job_name="df-test-1", status=JobStatus.RUNNING)
        await backend.create_job(job)
        active = await backend.get_active_job_for_thread("t4")
        assert active is not None
        assert active.id == "j1"

    async def test_no_active_job_for_completed(self, backend):
        thread = Thread(id="t5", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j2", thread_id="t5", k8s_job_name="df-test-2", status=JobStatus.COMPLETED)
        await backend.create_job(job)
        active = await backend.get_active_job_for_thread("t5")
        assert active is None

    async def test_get_job(self, backend):
        """Contract: get_job returns Job by ID or None."""
        thread = Thread(id="t5a", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j3", thread_id="t5a", k8s_job_name="df-test-3", status=JobStatus.RUNNING)
        await backend.create_job(job)
        retrieved = await backend.get_job("j3")
        assert retrieved is not None
        assert retrieved.id == "j3"
        assert await backend.get_job("nonexistent") is None

    async def test_update_job_status(self, backend):
        """Contract: update_job_status transitions job status and stores result."""
        thread = Thread(id="t5b", source="github", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        job = Job(id="j4", thread_id="t5b", k8s_job_name="df-test-4", status=JobStatus.RUNNING)
        await backend.create_job(job)
        await backend.update_job_status("j4", JobStatus.COMPLETED, result={"exit_code": 0})
        updated = await backend.get_job("j4")
        assert updated.status == JobStatus.COMPLETED
        # Job should no longer appear as active
        assert await backend.get_active_job_for_thread("t5b") is None

    async def test_lock_acquire_release(self, backend):
        assert await backend.try_acquire_lock("t6") is True
        assert await backend.try_acquire_lock("t6") is False  # Already locked
        await backend.release_lock("t6")
        assert await backend.try_acquire_lock("t6") is True  # Re-acquirable

    async def test_release_unlocked_is_noop(self, backend):
        await backend.release_lock("never-locked")  # Should not raise

    async def test_conversation_append_and_retrieve(self, backend):
        thread = Thread(id="t7", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        await backend.append_conversation("t7", {"role": "user", "content": "hello"})
        await backend.append_conversation("t7", {"role": "assistant", "content": "hi"})
        convo = await backend.get_conversation("t7", limit=50)
        assert len(convo) == 2
        assert convo[0]["role"] == "user"
        assert convo[1]["role"] == "assistant"

    async def test_conversation_limit(self, backend):
        thread = Thread(id="t8", source="slack", source_ref={}, repo_owner="o", repo_name="r")
        await backend.upsert_thread(thread)
        for i in range(10):
            await backend.append_conversation("t8", {"role": "user", "content": f"msg-{i}"})
        convo = await backend.get_conversation("t8", limit=3)
        assert len(convo) == 3


class TestSQLiteContract(StateBackendContractSuite):
    @pytest.fixture
    async def backend(self, tmp_path):
        from controller.state.sqlite import SQLiteBackend
        return await SQLiteBackend.create(f"sqlite:///{tmp_path / 'test.db'}")


class TestPostgresContract(StateBackendContractSuite):
    @pytest.fixture
    async def backend(self, pg_url):
        """Requires a running Postgres instance. Skip in CI without services."""
        from controller.state.postgres import PostgresBackend
        return await PostgresBackend.create(pg_url)

    pytestmark = pytest.mark.skipif(
        "not config.getoption('--pg-url')",
        reason="Postgres not available",
    )
```

---

### Contract 5/6 Tests: Redis Serialization Contracts (Cross-Process Boundary)

This is the most critical contract because it crosses a process boundary (controller <-> agent container).

```python
# tests/contracts/test_redis_contracts.py

import json
import pytest
import fakeredis.aioredis
from controller.state.redis_state import RedisState, TASK_TTL, RESULT_TTL
from controller.models import AgentResult


TASK_CONTEXT_REQUIRED_KEYS = {"task", "system_prompt", "repo_url", "branch"}
AGENT_RESULT_REQUIRED_KEYS = {"branch", "exit_code", "commit_count", "stderr"}


class TestTaskContextContract:
    """Contract 5: Controller writes task context that agent can read."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_push_task_sets_correct_key(self, redis_state):
        context = {
            "task": "fix bug",
            "system_prompt": "You are an agent",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "df/github/abc123",
        }
        await redis_state.push_task("thread-1", context)
        raw = await redis_state._redis.get("task:thread-1")
        assert raw is not None

        parsed = json.loads(raw)
        assert TASK_CONTEXT_REQUIRED_KEYS.issubset(parsed.keys()), (
            f"Missing keys: {TASK_CONTEXT_REQUIRED_KEYS - parsed.keys()}"
        )

    async def test_task_context_all_values_are_strings(self, redis_state):
        """Agent expects all values to be strings."""
        context = {
            "task": "fix bug",
            "system_prompt": "prompt",
            "repo_url": "https://github.com/o/r.git",
            "branch": "df/test/x",
        }
        await redis_state.push_task("thread-2", context)
        parsed = await redis_state.get_task("thread-2")
        for key in TASK_CONTEXT_REQUIRED_KEYS:
            assert isinstance(parsed[key], str), f"{key} should be str, got {type(parsed[key])}"

    async def test_task_context_ttl(self, redis_state):
        """Task context must have a TTL to prevent unbounded growth."""
        await redis_state.push_task("thread-3", {"task": "x", "system_prompt": "y", "repo_url": "z", "branch": "b"})
        ttl = await redis_state._redis.ttl("task:thread-3")
        assert ttl > 0
        assert ttl <= TASK_TTL

    async def test_repo_url_format(self, redis_state):
        """repo_url must be a valid GitHub clone URL."""
        context = {
            "task": "t", "system_prompt": "s",
            "repo_url": "https://github.com/myorg/myrepo.git",
            "branch": "df/test/b",
        }
        await redis_state.push_task("thread-4", context)
        parsed = await redis_state.get_task("thread-4")
        assert parsed["repo_url"].startswith("https://github.com/")
        assert parsed["repo_url"].endswith(".git")


class TestAgentResultContract:
    """Contract 6: Agent writes result that controller can parse."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_valid_result_roundtrip(self, redis_state):
        """Simulate agent writing, controller reading."""
        agent_output = {
            "branch": "df/github/abc123",
            "exit_code": 0,
            "commit_count": 5,
            "stderr": "",
        }
        await redis_state.push_result("thread-1", agent_output)
        parsed = await redis_state.get_result("thread-1")
        assert AGENT_RESULT_REQUIRED_KEYS.issubset(parsed.keys())

    async def test_result_to_agent_result_model(self, redis_state):
        """Controller must be able to construct AgentResult from dict."""
        agent_output = {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 2,
            "stderr": "warning: something",
        }
        await redis_state.push_result("thread-2", agent_output)
        parsed = await redis_state.get_result("thread-2")

        # This is exactly what JobMonitor does:
        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.branch == "df/test/x"
        assert result.exit_code == 0
        assert result.commit_count == 2

    async def test_result_with_string_numbers(self, redis_state):
        """Agent might write numbers as strings; controller must handle."""
        agent_output = {
            "branch": "df/test/y",
            "exit_code": "0",       # String, not int
            "commit_count": "3",    # String, not int
            "stderr": "",
        }
        await redis_state.push_result("thread-3", agent_output)
        parsed = await redis_state.get_result("thread-3")

        # Must not crash
        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.exit_code == 0
        assert result.commit_count == 3

    async def test_result_with_non_numeric_strings_crashes(self, redis_state):
        """BUG: Agent writes non-numeric exit_code -> int() raises ValueError.
        This test documents the crash path. Fix: wrap int() in try/except."""
        agent_output = {
            "branch": "df/test/crash",
            "exit_code": "abc",       # Non-numeric string
            "commit_count": "",       # Empty string
            "stderr": "",
        }
        await redis_state.push_result("thread-crash", agent_output)
        parsed = await redis_state.get_result("thread-crash")

        with pytest.raises(ValueError):
            AgentResult(
                branch=parsed.get("branch", ""),
                exit_code=int(parsed.get("exit_code", 1)),
                commit_count=int(parsed.get("commit_count", 0)),
                stderr=parsed.get("stderr", ""),
            )

    async def test_result_with_extra_fields_ignored(self, redis_state):
        """Agent may add new fields; controller must not crash."""
        agent_output = {
            "branch": "df/test/z",
            "exit_code": 0,
            "commit_count": 1,
            "stderr": "",
            "new_field": "unexpected",
            "metrics": {"tokens": 1000},
        }
        await redis_state.push_result("thread-4", agent_output)
        parsed = await redis_state.get_result("thread-4")

        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.branch == "df/test/z"

    async def test_missing_optional_field_defaults(self, redis_state):
        """If agent omits stderr, controller defaults gracefully."""
        agent_output = {
            "branch": "df/test/w",
            "exit_code": 1,
            "commit_count": 0,
            # No stderr
        }
        await redis_state.push_result("thread-5", agent_output)
        parsed = await redis_state.get_result("thread-5")

        result = AgentResult(
            branch=parsed.get("branch", ""),
            exit_code=int(parsed.get("exit_code", 1)),
            commit_count=int(parsed.get("commit_count", 0)),
            stderr=parsed.get("stderr", ""),
        )
        assert result.stderr == ""


class TestQueueContract:
    """Contract 11: Message queuing for follow-ups."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_fifo_ordering(self, redis_state):
        await redis_state.queue_message("t1", "first")
        await redis_state.queue_message("t1", "second")
        await redis_state.queue_message("t1", "third")
        messages = await redis_state.drain_messages("t1")
        assert messages == ["first", "second", "third"]

    async def test_drain_empties_queue(self, redis_state):
        await redis_state.queue_message("t2", "msg")
        await redis_state.drain_messages("t2")
        messages = await redis_state.drain_messages("t2")
        assert messages == []

    async def test_drain_empty_queue_returns_empty_list(self, redis_state):
        messages = await redis_state.drain_messages("nonexistent")
        assert messages == []
        assert isinstance(messages, list)

    async def test_queue_isolation_between_threads(self, redis_state):
        await redis_state.queue_message("t3", "for-t3")
        await redis_state.queue_message("t4", "for-t4")
        assert await redis_state.drain_messages("t3") == ["for-t3"]
        assert await redis_state.drain_messages("t4") == ["for-t4"]
```

---

### Contract 4 Tests: JobSpawner

```python
# tests/contracts/test_spawner_contracts.py

import pytest
from unittest.mock import MagicMock
from controller.config import Settings
from controller.jobs.spawner import JobSpawner


class TestJobSpawnerContract:
    @pytest.fixture
    def settings(self):
        return Settings(anthropic_api_key="test", agent_image="ghcr.io/org/agent:latest")

    @pytest.fixture
    def mock_k8s(self):
        batch = MagicMock()
        batch.create_namespaced_job = MagicMock()
        return batch

    @pytest.fixture
    def spawner(self, settings, mock_k8s):
        return JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")

    def test_job_name_format(self, spawner):
        """Contract: job name is df-{short_id}-{timestamp}."""
        spec = spawner.build_job_spec("abc12345" * 8, "token", "redis://localhost")
        name = spec.metadata.name
        assert name.startswith("df-")
        parts = name.split("-")
        assert len(parts) == 3  # df, short_id, timestamp

    def test_job_name_valid_k8s_label(self, spawner):
        """Contract: job name contains only valid K8s characters."""
        weird_id = "thread/with:special!chars@here"
        spec = spawner.build_job_spec(weird_id, "token", "redis://localhost")
        name = spec.metadata.name
        assert all(c.isalnum() or c == "-" for c in name)
        assert len(name) <= 63

    def test_container_env_vars(self, spawner):
        """Contract: agent container has required env vars."""
        spec = spawner.build_job_spec("thread-1", "gh-token", "redis://redis:6379")
        container = spec.spec.template.spec.containers[0]
        env_names = {e.name for e in container.env}
        assert "THREAD_ID" in env_names
        assert "REDIS_URL" in env_names
        assert "GITHUB_TOKEN" in env_names

    def test_security_context(self, spawner):
        """Contract: agent runs as non-root with dropped capabilities."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        sc = spec.spec.template.spec.containers[0].security_context
        assert sc.run_as_non_root is True
        assert sc.allow_privilege_escalation is False

    def test_backoff_limit(self, spawner):
        """Contract: job retries at most once."""
        spec = spawner.build_job_spec("thread-1", "token", "redis://localhost")
        assert spec.spec.backoff_limit == 1

    def test_spawn_returns_job_name(self, spawner, mock_k8s):
        """Contract: spawn() returns the job name string."""
        name = spawner.spawn("thread-1", "token", "redis://localhost")
        assert isinstance(name, str)
        assert name.startswith("df-")
        mock_k8s.create_namespaced_job.assert_called_once()
```

---

### Contract 8/9/10 Tests: Safety Pipeline

```python
# tests/contracts/test_safety_contracts.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.config import Settings
from controller.models import Thread, AgentResult, ThreadStatus


class TestSafetyPipelineContract:

    @pytest.fixture
    def settings(self):
        return Settings(
            anthropic_api_key="test",
            auto_open_pr=True,
            retry_on_empty_result=True,
            max_empty_retries=2,
        )

    @pytest.fixture
    def thread(self):
        return Thread(
            id="t1", source="github",
            source_ref={"type": "issue_comment", "number": 42},
            repo_owner="org", repo_name="repo",
            status=ThreadStatus.RUNNING,
        )

    @pytest.fixture
    def state(self):
        mock = AsyncMock()
        mock.update_thread_status = AsyncMock()
        return mock

    @pytest.fixture
    def redis_state(self):
        mock = AsyncMock()
        mock.drain_messages = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def integration(self):
        mock = AsyncMock()
        mock.report_result = AsyncMock()
        return mock

    @pytest.fixture
    def github_client(self):
        mock = AsyncMock()
        mock.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/99")
        return mock

    @pytest.fixture
    def pipeline(self, settings, state, redis_state, integration, github_client):
        from controller.jobs.safety import SafetyPipeline
        return SafetyPipeline(
            settings=settings,
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

    # --- Contract: PR auto-creation ---

    async def test_creates_pr_when_commits_and_no_pr_url(self, pipeline, thread, github_client, integration):
        """Contract 9: commits > 0 AND no pr_url AND auto_open_pr -> create_pr called."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=3)
        await pipeline.process(thread, result)

        github_client.create_pr.assert_called_once_with(
            owner="org", repo="repo", branch="df/test/x",
        )
        # PR URL should be set on result before reporting
        reported_result = integration.report_result.call_args[0][1]
        assert reported_result.pr_url == "https://github.com/org/repo/pull/99"

    async def test_skips_pr_when_already_has_url(self, pipeline, thread, github_client):
        """Contract: if pr_url already set, don't create another."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=3,
                             pr_url="https://github.com/org/repo/pull/1")
        await pipeline.process(thread, result)
        github_client.create_pr.assert_not_called()

    async def test_skips_pr_when_no_commits(self, pipeline, thread, github_client):
        """Contract: zero commits means nothing to PR."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result)
        github_client.create_pr.assert_not_called()

    # --- Contract: anti-stall retry ---

    async def test_retries_on_empty_result(self, pipeline, thread, integration):
        """Contract 8: commit_count=0, exit_code=0, retries left -> re-spawn."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=0)

        # Should NOT report (retrying instead)
        integration.report_result.assert_not_called()
        pipeline._spawner.assert_called_once()

    async def test_reports_after_max_retries(self, pipeline, thread, integration):
        """Contract: after max retries, report with error message."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=2)  # Already at max

        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert "no changes" in reported.stderr.lower() or len(reported.stderr) > 0

    # --- Contract: always reports and cleans up ---

    async def test_reports_to_integration(self, pipeline, thread, integration):
        """Contract 10: result is always reported to the source integration."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1,
                             pr_url="https://github.com/org/repo/pull/1")
        await pipeline.process(thread, result)
        integration.report_result.assert_called_once_with(thread, result)

    async def test_resets_thread_to_idle(self, pipeline, thread, state):
        """Contract: thread status -> IDLE after processing."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")
        await pipeline.process(thread, result)
        state.update_thread_status.assert_called_with(thread.id, ThreadStatus.IDLE)

    async def test_drains_queued_messages(self, pipeline, thread, redis_state):
        """Contract 11: queued messages are drained after completion."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")
        await pipeline.process(thread, result)
        redis_state.drain_messages.assert_called_once_with(thread.id)

    async def test_pr_creation_failure_doesnt_block_reporting(self, pipeline, thread, github_client, integration):
        """Contract: PR failure is caught; result still reported."""
        github_client.create_pr.side_effect = Exception("GitHub API error")
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=2)
        await pipeline.process(thread, result)

        # Should still report even though PR failed
        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert reported.pr_url is None
```

---

### Contract 12 Tests: Integration Protocol Conformance

```python
# tests/contracts/test_integration_protocol_contracts.py

import pytest
from typing import runtime_checkable
from controller.integrations.protocol import Integration
from controller.integrations.github import GitHubIntegration
from controller.integrations.slack import SlackIntegration
from controller.integrations.linear import LinearIntegration


INTEGRATION_CLASSES = [
    ("github", GitHubIntegration, {"webhook_secret": "test"}),
    ("slack", SlackIntegration, {"signing_secret": "test", "bot_token": "xoxb-test"}),
    ("linear", LinearIntegration, {"webhook_secret": "test", "api_key": "lin_test"}),
]


class TestIntegrationProtocolConformance:
    """Verify all Integration implementations satisfy the protocol."""

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_is_protocol_conformant(self, name, cls, kwargs):
        """Each integration class must satisfy the Integration protocol."""
        instance = cls(**kwargs)
        assert isinstance(instance, Integration), (
            f"{cls.__name__} does not satisfy Integration protocol"
        )

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_has_name_attribute(self, name, cls, kwargs):
        instance = cls(**kwargs)
        assert hasattr(instance, "name")
        assert instance.name == name

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_has_all_protocol_methods(self, name, cls, kwargs):
        """All required methods exist with correct names."""
        instance = cls(**kwargs)
        for method_name in ("parse_webhook", "fetch_context", "report_result", "acknowledge"):
            assert hasattr(instance, method_name), f"{cls.__name__} missing {method_name}"
            assert callable(getattr(instance, method_name))
```

---

### Contract 13 Tests: handle_job_completion Flow

```python
# tests/contracts/test_job_completion_contracts.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.models import Thread, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.config import Settings


class TestHandleJobCompletionContract:

    @pytest.fixture
    def settings(self):
        return Settings(anthropic_api_key="test", auto_open_pr=True)

    @pytest.fixture
    def state(self):
        return AsyncMock()

    @pytest.fixture
    def redis_state(self):
        mock = AsyncMock()
        mock.drain_messages = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def registry(self):
        reg = MagicMock()
        integration = AsyncMock()
        integration.report_result = AsyncMock()
        reg.get = MagicMock(return_value=integration)
        return reg

    @pytest.fixture
    def monitor(self):
        return AsyncMock()

    @pytest.fixture
    def orchestrator(self, settings, state, redis_state, registry, monitor):
        return Orchestrator(
            settings=settings, state=state, redis_state=redis_state,
            registry=registry, spawner=MagicMock(), monitor=monitor,
            github_client=AsyncMock(),
        )

    async def test_returns_early_on_missing_thread(self, orchestrator, state):
        """Contract: no crash if thread not found."""
        state.get_thread = AsyncMock(return_value=None)
        await orchestrator.handle_job_completion("nonexistent")
        # Should not raise

    async def test_returns_early_on_missing_result(self, orchestrator, state, monitor):
        """Contract: no crash if monitor times out."""
        state.get_thread = AsyncMock(return_value=Thread(
            id="t1", source="github", source_ref={}, repo_owner="o", repo_name="r"
        ))
        monitor.wait_for_result = AsyncMock(return_value=None)
        await orchestrator.handle_job_completion("t1")
        # Should not raise, but thread stays in RUNNING (bug)

    async def test_returns_early_on_missing_integration(self, orchestrator, state, monitor, registry):
        """Contract: no crash if integration not registered."""
        state.get_thread = AsyncMock(return_value=Thread(
            id="t1", source="unknown", source_ref={}, repo_owner="o", repo_name="r"
        ))
        monitor.wait_for_result = AsyncMock(return_value=AgentResult(
            branch="df/test/x", exit_code=0, commit_count=1
        ))
        registry.get = MagicMock(return_value=None)
        await orchestrator.handle_job_completion("t1")
        # Should not raise

    async def test_spawner_callable_interface(self, orchestrator):
        """Contract: spawner passed to SafetyPipeline is _spawn_job bound method.
        BUG: safety.py calls spawner(thread.id, is_retry=True, retry_count=N)
        but _spawn_job expects (thread: Thread, task_request: TaskRequest, ...).
        This test documents the interface mismatch."""
        import inspect
        sig = inspect.signature(orchestrator._spawn_job)
        params = list(sig.parameters.keys())
        # _spawn_job expects: self, thread, task_request, is_retry, retry_count
        # but safety.py calls: spawner(thread.id, is_retry=True, retry_count=N)
        # First param after self is 'thread' (expects Thread, not str)
        assert params[0] == "thread", "First param should be 'thread' (Thread object)"
        assert params[1] == "task_request", "Second param should be 'task_request'"
```

---

### Contract 14 Tests: Redis Stream Events

```python
# tests/contracts/test_stream_contracts.py

import pytest
import fakeredis.aioredis
from controller.state.redis_state import RedisState


class TestStreamEventContract:
    """Contract 14: Stream events for real-time agent status."""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_append_and_read_stream_event(self, redis_state):
        """Contract: events written via append_stream_event are readable via read_stream."""
        await redis_state.append_stream_event("t1", "started")
        await redis_state.append_stream_event("t1", "running tool: bash")
        await redis_state.append_stream_event("t1", "completed")

        events = await redis_state.read_stream("t1")
        assert len(events) == 3
        assert events[0][1]["event"] == "started"
        assert events[1][1]["event"] == "running tool: bash"
        assert events[2][1]["event"] == "completed"

    async def test_read_stream_empty(self, redis_state):
        """Contract: reading nonexistent stream returns empty list."""
        events = await redis_state.read_stream("nonexistent")
        assert events == []

    async def test_stream_isolation_between_threads(self, redis_state):
        """Contract: streams are isolated by thread_id."""
        await redis_state.append_stream_event("t1", "event-for-t1")
        await redis_state.append_stream_event("t2", "event-for-t2")
        t1_events = await redis_state.read_stream("t1")
        t2_events = await redis_state.read_stream("t2")
        assert len(t1_events) == 1
        assert len(t2_events) == 1
        assert t1_events[0][1]["event"] == "event-for-t1"
        assert t2_events[0][1]["event"] == "event-for-t2"

    async def test_read_stream_with_cursor(self, redis_state):
        """Contract: read_stream with last_id returns only newer events."""
        await redis_state.append_stream_event("t1", "first")
        events = await redis_state.read_stream("t1")
        first_id = events[0][0]

        await redis_state.append_stream_event("t1", "second")
        newer = await redis_state.read_stream("t1", last_id=first_id)
        # XRANGE with min=last_id is inclusive, so we get first + second
        assert len(newer) == 2

    async def test_stream_event_values_are_strings(self, redis_state):
        """Contract: event values are decoded to strings."""
        await redis_state.append_stream_event("t1", "test-event")
        events = await redis_state.read_stream("t1")
        eid, data = events[0]
        assert isinstance(eid, str)
        assert isinstance(data["event"], str)
```

---

### Negative and Failure Contract Tests

```python
# tests/contracts/test_negative_contracts.py

import json
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock
from controller.state.redis_state import RedisState
from controller.models import AgentResult, Thread, ThreadStatus


class TestStateBackendFailureContracts:
    """What happens when the state backend raises?"""

    async def test_orchestrator_propagates_state_errors(self):
        """Contract: StateBackend exceptions propagate to FastAPI error handler."""
        from controller.orchestrator import Orchestrator
        from controller.models import TaskRequest
        from controller.config import Settings

        state = AsyncMock()
        state.get_thread = AsyncMock(side_effect=Exception("DB connection lost"))

        orch = Orchestrator(
            settings=Settings(anthropic_api_key="test"),
            state=state,
            redis_state=AsyncMock(),
            registry=AsyncMock(),
            spawner=AsyncMock(),
            monitor=AsyncMock(),
        )
        task = TaskRequest(
            thread_id="a" * 64, source="github",
            source_ref={"number": 1}, repo_owner="o", repo_name="r", task="t",
        )
        with pytest.raises(Exception, match="DB connection lost"):
            await orch.handle_task(task)


class TestRedisCorruptionContracts:
    """What happens when Redis returns corrupted data?"""

    @pytest.fixture
    async def redis_state(self):
        redis = fakeredis.aioredis.FakeRedis()
        return RedisState(redis)

    async def test_corrupted_json_in_result(self, redis_state):
        """Contract: corrupted JSON in result key -> json.loads raises."""
        await redis_state._redis.set("result:corrupt", b"not-valid-json{{{")
        with pytest.raises(json.JSONDecodeError):
            await redis_state.get_result("corrupt")

    async def test_corrupted_json_in_task(self, redis_state):
        """Contract: corrupted JSON in task key -> json.loads raises."""
        await redis_state._redis.set("task:corrupt", b"<<<invalid>>>")
        with pytest.raises(json.JSONDecodeError):
            await redis_state.get_task("corrupt")


class TestIntegrationReportFailureContracts:
    """What happens when integration.report_result throws?"""

    async def test_report_failure_leaves_thread_in_non_idle(self):
        """Contract: if report_result raises, thread stays in RUNNING.
        This is an unhandled failure mode -- SafetyPipeline does not
        catch exceptions from integration.report_result."""
        from controller.jobs.safety import SafetyPipeline
        from controller.config import Settings

        integration = AsyncMock()
        integration.report_result = AsyncMock(side_effect=Exception("Slack API down"))
        state = AsyncMock()

        pipeline = SafetyPipeline(
            settings=Settings(anthropic_api_key="test", auto_open_pr=False),
            state_backend=state,
            redis_state=AsyncMock(drain_messages=AsyncMock(return_value=[])),
            integration=integration,
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )
        thread = Thread(id="t1", source="slack", source_ref={"channel": "C1"},
                        repo_owner="o", repo_name="r", status=ThreadStatus.RUNNING)
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")

        with pytest.raises(Exception, match="Slack API down"):
            await pipeline.process(thread, result)

        # Thread status NOT updated to IDLE (update_thread_status never reached)
        state.update_thread_status.assert_not_called()
```

---

### Schema Enforcement: JSON Schema for Cross-Process Contracts

```json
// tests/schemas/task_context.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TaskContext",
  "description": "Data written to Redis task:{thread_id} by controller, read by agent",
  "type": "object",
  "required": ["task", "system_prompt", "repo_url", "branch"],
  "properties": {
    "task": {
      "type": "string",
      "minLength": 1,
      "description": "The user's task description"
    },
    "system_prompt": {
      "type": "string",
      "minLength": 1,
      "description": "System prompt for the agent"
    },
    "repo_url": {
      "type": "string",
      "pattern": "^https://github\\.com/[\\w.-]+/[\\w.-]+\\.git$",
      "description": "GitHub clone URL"
    },
    "branch": {
      "type": "string",
      "pattern": "^df/",
      "description": "Branch name (must start with df/)"
    }
  },
  "additionalProperties": true
}
```

```json
// tests/schemas/agent_result.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AgentResult",
  "description": "Data written to Redis result:{thread_id} by agent, read by controller",
  "type": "object",
  "required": ["branch", "exit_code", "commit_count"],
  "properties": {
    "branch": {
      "type": "string",
      "minLength": 1,
      "description": "Git branch the agent worked on"
    },
    "exit_code": {
      "type": ["integer", "string"],
      "description": "Exit code (0=success). May be string or int."
    },
    "commit_count": {
      "type": ["integer", "string"],
      "description": "Number of commits made. May be string or int."
    },
    "stderr": {
      "type": "string",
      "default": "",
      "description": "Standard error output"
    },
    "pr_url": {
      "type": ["string", "null"],
      "description": "PR URL if agent created one (usually null)"
    }
  },
  "additionalProperties": true
}
```

### Schema Validation in CI

```python
# tests/contracts/test_schema_validation.py

import json
from pathlib import Path
import pytest

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_DIR = Path(__file__).parent.parent / "schemas"

pytestmark = pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")


@pytest.fixture
def task_context_schema():
    return json.loads((SCHEMA_DIR / "task_context.schema.json").read_text())


@pytest.fixture
def agent_result_schema():
    return json.loads((SCHEMA_DIR / "agent_result.schema.json").read_text())


class TestTaskContextSchema:
    def test_valid_context_passes(self, task_context_schema):
        context = {
            "task": "fix the bug",
            "system_prompt": "You are a coding agent",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "df/github/abc123",
        }
        jsonschema.validate(context, task_context_schema)

    def test_missing_task_fails(self, task_context_schema):
        context = {"system_prompt": "x", "repo_url": "https://github.com/o/r.git", "branch": "df/x"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_invalid_repo_url_fails(self, task_context_schema):
        context = {
            "task": "t", "system_prompt": "s",
            "repo_url": "git@github.com:org/repo.git",  # SSH, not HTTPS
            "branch": "df/x",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_extra_fields_allowed(self, task_context_schema):
        context = {
            "task": "t", "system_prompt": "s",
            "repo_url": "https://github.com/o/r.git",
            "branch": "df/x",
            "extra": "allowed",
        }
        jsonschema.validate(context, task_context_schema)  # Should not raise


class TestAgentResultSchema:
    def test_valid_result_passes(self, agent_result_schema):
        result = {"branch": "df/test/x", "exit_code": 0, "commit_count": 3, "stderr": ""}
        jsonschema.validate(result, agent_result_schema)

    def test_string_numbers_pass(self, agent_result_schema):
        result = {"branch": "df/test/x", "exit_code": "0", "commit_count": "3", "stderr": ""}
        jsonschema.validate(result, agent_result_schema)

    def test_missing_branch_fails(self, agent_result_schema):
        result = {"exit_code": 0, "commit_count": 0}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, agent_result_schema)

    def test_extra_fields_allowed(self, agent_result_schema):
        result = {
            "branch": "df/test/x", "exit_code": 0, "commit_count": 0,
            "stderr": "", "tokens_used": 5000,
        }
        jsonschema.validate(result, agent_result_schema)
```

---

## Part 3: Contract-Based E2E Test

This test validates the full pipeline from webhook through to result reporting, with:
- **Real**: Orchestrator, SafetyPipeline, RedisState (fakeredis), SQLiteBackend
- **Stubbed**: K8s JobSpawner (skips pod creation, writes result directly to Redis), GitHub API (asserts PR creation args)
- **Verified**: Every contract boundary is honored end-to-end

```python
# tests/contracts/test_e2e_contract.py

"""
Contract-Based E2E Test
=======================
Tests the full pipeline: webhook -> parse -> orchestrate -> spawn -> result -> safety -> report

Stubs:
  - K8s JobSpawner: Instead of creating a pod, directly writes AgentResult to Redis
  - GitHub API client: Records PR creation calls for assertion

Real:
  - Orchestrator, SafetyPipeline, RedisState (fakeredis), SQLiteBackend
"""

import hashlib
import hmac
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from controller.config import Settings
from controller.models import TaskRequest, Thread, Job, AgentResult, ThreadStatus, JobStatus
from controller.orchestrator import Orchestrator
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.github import GitHubIntegration
from controller.integrations.slack import SlackIntegration
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.jobs.safety import SafetyPipeline

try:
    import fakeredis.aioredis
    from controller.state.sqlite import SQLiteBackend
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="fakeredis or aiosqlite not installed")


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="sk-test-contract-e2e",
        auto_open_pr=True,
        retry_on_empty_result=False,
        github_enabled=True,
        github_webhook_secret="contract-test-secret",
        github_allowed_orgs=["testorg"],
        slack_enabled=True,
        slack_signing_secret="slack-contract-secret",
        slack_bot_token="xoxb-contract",
        slack_bot_user_id="U_BOT",
    )


@pytest.fixture
async def db(tmp_path):
    return await SQLiteBackend.create(f"sqlite:///{tmp_path / 'contract_e2e.db'}")


@pytest.fixture
async def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def redis_state(redis):
    return RedisState(redis)


@pytest.fixture
def mock_k8s():
    batch = MagicMock()
    batch.create_namespaced_job = MagicMock()
    batch.delete_namespaced_job = MagicMock()
    return batch


@pytest.fixture
def spawner(settings, mock_k8s):
    return JobSpawner(settings=settings, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def monitor(redis_state, mock_k8s):
    return JobMonitor(redis_state=redis_state, batch_api=mock_k8s, namespace="test")


@pytest.fixture
def github_client():
    """Stub GitHub client that records calls."""
    client = AsyncMock()
    client.create_pr = AsyncMock(return_value="https://github.com/testorg/myrepo/pull/100")
    return client


@pytest.fixture
def registry(settings):
    reg = IntegrationRegistry()

    github = GitHubIntegration(
        webhook_secret=settings.github_webhook_secret,
        allowed_orgs=settings.github_allowed_orgs,
    )
    # Mock HTTP client on github integration for report_result
    github._client = AsyncMock()
    github._client.post = AsyncMock(return_value=MagicMock(status_code=201))

    slack = SlackIntegration(
        signing_secret=settings.slack_signing_secret,
        bot_token=settings.slack_bot_token,
        bot_user_id=settings.slack_bot_user_id,
    )
    slack._client = AsyncMock()
    slack._client.post = AsyncMock()

    reg.register(github)
    reg.register(slack)
    return reg


@pytest.fixture
def orchestrator(settings, db, redis_state, registry, spawner, monitor, github_client):
    return Orchestrator(
        settings=settings,
        state=db,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        github_client=github_client,
    )


# ── Helpers ──────────────────────────────────────────────────────────

def sign_github_payload(payload: dict, secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


async def simulate_agent_result(redis_state: RedisState, thread_id: str, result: dict):
    """Simulate the agent container writing its result to Redis."""
    await redis_state.push_result(thread_id, result)


# ── Contract Validators ─────────────────────────────────────────────

def assert_task_context_contract(context: dict):
    """Validate the task context written to Redis honors Contract 5."""
    required = {"task", "system_prompt", "repo_url", "branch"}
    assert required.issubset(context.keys()), f"Missing: {required - context.keys()}"
    assert context["repo_url"].startswith("https://github.com/")
    assert context["repo_url"].endswith(".git")
    assert all(isinstance(v, str) for v in context.values()), "All values must be strings"


def assert_agent_result_contract(result: AgentResult):
    """Validate AgentResult honors Contract 7."""
    assert isinstance(result.branch, str) and len(result.branch) > 0
    assert isinstance(result.exit_code, int)
    assert isinstance(result.commit_count, int) and result.commit_count >= 0
    assert isinstance(result.stderr, str)


# ── Tests ────────────────────────────────────────────────────────────

class TestContractE2E:
    """
    Full pipeline contract test:
    1. Parse GitHub webhook (Contract 1)
    2. Orchestrator creates thread + spawns job (Contracts 2, 3, 4)
    3. Task context written to Redis (Contract 5)
    4. Agent writes result to Redis (Contract 6)
    5. Monitor reads result (Contract 7)
    6. SafetyPipeline creates PR + reports (Contracts 8, 9, 10)
    7. Queued messages are drained (Contract 11)
    """

    async def test_github_issue_full_pipeline(
        self, orchestrator, db, redis_state, mock_k8s, settings, github_client, registry,
    ):
        # ── Step 1: Parse webhook (Contract 1) ──
        github = registry.get("github")
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Fix auth", "body": "Auth is broken"},
            "comment": {
                "body": "please fix the authentication flow",
                "user": {"login": "developer", "type": "User"},
            },
            "repository": {"full_name": "testorg/myrepo"},
        }
        body, sig = sign_github_payload(payload, settings.github_webhook_secret)
        mock_request = AsyncMock()
        mock_request.body = AsyncMock(return_value=body)
        mock_request.headers = {
            "x-github-event": "issue_comment",
            "x-hub-signature-256": sig,
        }
        task_req = await github.parse_webhook(mock_request)

        # Contract 1 validation
        assert task_req is not None
        assert task_req.source == "github"
        assert task_req.repo_owner == "testorg"
        assert task_req.repo_name == "myrepo"
        assert len(task_req.thread_id) == 64  # SHA256 hex

        # ── Step 2: Orchestrator handles task (Contracts 2, 3, 4) ──
        await orchestrator.handle_task(task_req)

        # Contract 3: Thread created in state
        thread = await db.get_thread(task_req.thread_id)
        assert thread is not None
        assert thread.source == "github"
        assert thread.status == ThreadStatus.RUNNING

        # Contract 4: K8s Job was created
        mock_k8s.create_namespaced_job.assert_called_once()

        # ── Step 3: Validate task context in Redis (Contract 5) ──
        task_context = await redis_state.get_task(task_req.thread_id)
        assert task_context is not None
        assert_task_context_contract(task_context)
        assert "authentication flow" in task_context["task"]

        # ── Step 4: Simulate agent writing result (Contract 6) ──
        agent_output = {
            "branch": "df/github/abc12345",
            "exit_code": 0,
            "commit_count": 3,
            "stderr": "",
        }
        await simulate_agent_result(redis_state, task_req.thread_id, agent_output)

        # ── Step 5: Monitor reads result (Contract 7) ──
        from controller.jobs.monitor import JobMonitor
        result = await orchestrator._monitor.wait_for_result(
            task_req.thread_id, timeout=5, poll_interval=0.1,
        )
        assert result is not None
        assert_agent_result_contract(result)
        assert result.commit_count == 3
        assert result.exit_code == 0

        # ── Step 6: SafetyPipeline processes result (Contracts 8, 9, 10) ──
        integration = registry.get("github")
        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )
        await pipeline.process(thread, result)

        # Contract 9: PR was created
        github_client.create_pr.assert_called_once_with(
            owner="testorg", repo="myrepo", branch="df/github/abc12345",
        )
        assert result.pr_url == "https://github.com/testorg/myrepo/pull/100"

        # Contract 10: Result reported to integration
        integration._client.post.assert_called()

        # Contract 3: Thread back to IDLE
        thread = await db.get_thread(task_req.thread_id)
        assert thread.status == ThreadStatus.IDLE

    async def test_concurrent_message_queuing(
        self, orchestrator, db, redis_state, mock_k8s,
    ):
        """Contract 11: Second message during active job gets queued, then drained."""
        # First task spawns a job
        task1 = TaskRequest(
            thread_id="a" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 1},
            repo_owner="testorg",
            repo_name="myrepo",
            task="first task",
        )
        await orchestrator.handle_task(task1)

        # Verify job spawned
        thread = await db.get_thread(task1.thread_id)
        assert thread.status == ThreadStatus.RUNNING
        mock_k8s.create_namespaced_job.assert_called_once()

        # Second task should be queued (Contract 11: enqueue)
        task2 = TaskRequest(
            thread_id="a" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 1},
            repo_owner="testorg",
            repo_name="myrepo",
            task="follow-up task",
        )
        await orchestrator.handle_task(task2)

        # Should NOT have spawned a second job
        mock_k8s.create_namespaced_job.assert_called_once()  # Still just once

        # Simulate completion and drain (Contract 11: drain)
        await simulate_agent_result(redis_state, task1.thread_id, {
            "branch": "df/test/x", "exit_code": 0, "commit_count": 1, "stderr": "",
        })

        result = await orchestrator._monitor.wait_for_result(task1.thread_id, timeout=2, poll_interval=0.1)
        assert result is not None

        pipeline = SafetyPipeline(
            settings=orchestrator._settings,
            state_backend=db,
            redis_state=redis_state,
            integration=AsyncMock(),
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )
        await pipeline.process(thread, result)

        # Contract 11: drain verified -- thread is IDLE and queue is empty
        thread = await db.get_thread(task1.thread_id)
        assert thread.status == ThreadStatus.IDLE
        remaining = await redis_state.drain_messages(task1.thread_id)
        assert remaining == []  # Already drained by pipeline

    async def test_failed_agent_result_pipeline(
        self, orchestrator, db, redis_state, mock_k8s, settings, registry,
    ):
        """Contract 8: Failed agent result is reported correctly."""
        task = TaskRequest(
            thread_id="b" * 64,
            source="github",
            source_ref={"type": "issue_comment", "number": 5},
            repo_owner="testorg",
            repo_name="myrepo",
            task="implement feature",
        )
        await orchestrator.handle_task(task)

        # Agent fails
        await simulate_agent_result(redis_state, task.thread_id, {
            "branch": "df/github/fail",
            "exit_code": 1,
            "commit_count": 0,
            "stderr": "Error: could not compile",
        })

        result = await orchestrator._monitor.wait_for_result(task.thread_id, timeout=2, poll_interval=0.1)
        assert result is not None
        assert result.exit_code == 1

        thread = await db.get_thread(task.thread_id)
        integration = AsyncMock()
        github_client = AsyncMock()

        pipeline = SafetyPipeline(
            settings=settings,
            state_backend=db,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )
        await pipeline.process(thread, result)

        # Contract 9: No PR created for failed result
        github_client.create_pr.assert_not_called()

        # Contract 10: Result still reported
        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert reported.exit_code == 1
        assert "could not compile" in reported.stderr
```

---

## CI Integration

### pytest markers

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "contract: Contract boundary tests",
    "e2e_contract: Contract-based end-to-end tests",
]
```

### Makefile targets

```makefile
test-contracts:
	pytest tests/contracts/ -m contract -v

test-contracts-e2e:
	pytest tests/contracts/test_e2e_contract.py -m e2e_contract -v

test-all:
	pytest tests/ -v
```

### CI Pipeline (GitHub Actions)

```yaml
# .github/workflows/contracts.yml
name: Contract Tests
on: [push, pull_request]
jobs:
  contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]" jsonschema
      - run: pytest tests/contracts/ -v --tb=short

  schema-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]" jsonschema
      - run: pytest tests/contracts/test_schema_validation.py -v
```

---

## Summary: Contract Coverage Matrix

| # | Boundary | Test File | Strategy | Critical? |
|---|----------|-----------|----------|-----------|
| 1 | Webhook -> Integration | `test_webhook_contracts.py` | Fixture-based, signed payloads | HIGH |
| 2 | Integration -> Orchestrator | `test_orchestrator_contracts.py` | Mock state, verify side effects | HIGH |
| 3 | Orchestrator -> State | `test_state_contracts.py` | Abstract suite, run per impl | HIGH |
| 4 | Orchestrator -> Spawner | `test_spawner_contracts.py` | Verify K8s spec structure | MEDIUM |
| 5 | Controller -> Redis (task) | `test_redis_contracts.py` | JSON Schema + roundtrip | CRITICAL |
| 6 | Agent -> Redis (result) | `test_redis_contracts.py` | JSON Schema + type coercion + crash paths | CRITICAL |
| 7 | Redis -> Monitor | `test_redis_contracts.py` | Roundtrip + timeout | HIGH |
| 8 | Monitor -> Safety | `test_safety_contracts.py` | Behavior verification | HIGH |
| 9 | Safety -> GitHub Client | `test_safety_contracts.py` | Stub + assert args | MEDIUM |
| 10 | Safety -> Integration | `test_safety_contracts.py` | Stub + assert called | HIGH |
| 11 | Orchestrator -> Redis queue | `test_redis_contracts.py` | FIFO + atomicity | MEDIUM |
| 12 | Integration Protocol | `test_integration_protocol_contracts.py` | Protocol conformance, parametrized | HIGH |
| 13 | handle_job_completion | `test_job_completion_contracts.py` | Early-return paths, spawner interface | HIGH |
| 14 | Redis Stream Events | `test_stream_contracts.py` | Roundtrip, isolation, cursor | MEDIUM |
| -- | Negative/failure paths | `test_negative_contracts.py` | Error propagation, corruption | HIGH |
| E2E | Full pipeline | `test_e2e_contract.py` | All contracts validated | CRITICAL |

Contracts 5 and 6 are the most critical because they cross a **process boundary** (controller <-> agent container). The JSON Schema files serve as the canonical contract definition that both sides must honor.

---

## Revision History

| Date | Change | Reason |
|------|--------|--------|
| 2026-03-21 | Initial plan | -- |
| 2026-03-21 | **Contract 3 table**: Added `get_job(job_id)` and `update_job_status(job_id, status, result)` methods | Missing from protocol table; both exist in `protocol.py` lines 12-14 |
| 2026-03-21 | **Contract 3**: Added note about `update_job_status` never being called | Application bug: jobs stay in RUNNING indefinitely |
| 2026-03-21 | **Contract 9**: Renamed from "GitHub API" to "GitHub Client"; updated provider to reference `self._github_client` abstraction | Plan misdescribed provider as raw API; actual code uses client abstraction |
| 2026-03-21 | **Contract 9**: Documented `create_pr` signature mismatch bug | SafetyPipeline calls with 3 kwargs but `GitHubIntegration.create_pr` requires `title` and `body` |
| 2026-03-21 | **Contract 12 (new)**: Added Integration Protocol conformance contract | Missing protocol-level contract for `parse_webhook`, `fetch_context`, `report_result`, `acknowledge` |
| 2026-03-21 | **Contract 13 (new)**: Added `handle_job_completion` flow contract | Missing analysis of orchestrator completion path and spawner callable interface mismatch bug |
| 2026-03-21 | **Contract 14 (new)**: Added Redis stream events contract (`append_stream_event`/`read_stream`) | Uncovered contract boundary in `redis_state.py:38-52` |
| 2026-03-21 | **Contract 6 tests**: Added `test_result_with_non_numeric_strings_crashes` | Original test only tested valid numeric strings; added test for `int("abc")` crash path |
| 2026-03-21 | **Contract 3 tests**: Added `test_get_job` and `test_update_job_status` | Tests for newly documented protocol methods |
| 2026-03-21 | **New test files**: Added `test_integration_protocol_contracts.py`, `test_job_completion_contracts.py`, `test_stream_contracts.py`, `test_negative_contracts.py` | Coverage for new contracts and failure paths |
| 2026-03-21 | **Negative tests**: Added state backend error propagation, Redis JSON corruption, and `report_result` failure tests | Plan previously covered only happy paths and edge cases |
| 2026-03-21 | **Tooling**: Replaced Pact recommendation with note explaining why fixture-based approach is sufficient | Single-repo monolith does not benefit from Pact's broker-based workflow |
| 2026-03-21 | **Coverage matrix**: Updated to include contracts 12-14 and negative test row | Reflect all additions |
