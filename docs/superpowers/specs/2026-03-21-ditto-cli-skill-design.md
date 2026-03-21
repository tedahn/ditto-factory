# Ditto CLI Skill + REST API Design

## Problem

Ditto Factory currently only accepts tasks via webhooks (Slack, GitHub, Linear). There's no way to submit tasks, check status, or retrieve results directly from Claude Code or a terminal. This blocks local testing and direct developer interaction.

## Solution

Two components:

1. **REST API endpoints** on the FastAPI controller for task submission, status, and listing
2. **Claude Code skill** (`/ditto`) that provides natural language interface to those endpoints

## REST API Endpoints

Added to the existing FastAPI controller in `controller/src/controller/api.py`, mounted in `main.py`.

### `POST /api/tasks`

Submit a new task to the controller.

**Request body:**
```json
{
  "task": "fix the login bug",
  "repo_owner": "tedahn",
  "repo_name": "myapp"
}
```

**Behavior:**
- Generates a deterministic thread_id from `repo_owner/repo_name + task` (same SHA256 pattern as other integrations)
- Creates a `TaskRequest` with `source="cli"` and empty `source_ref`
- Feeds into `Orchestrator.handle_task()` — same pipeline as Slack/GitHub/Linear
- Returns thread_id immediately

**Response:**
```json
{
  "thread_id": "abc123...",
  "status": "submitted"
}
```

### `GET /api/tasks/{thread_id}`

Get status and result for a thread.

**Response (running):**
```json
{
  "thread_id": "abc123...",
  "status": "running",
  "started_at": "2026-03-21T10:00:00Z"
}
```

**Response (completed):**
```json
{
  "thread_id": "abc123...",
  "status": "completed",
  "result": {
    "branch": "df/abc123/deadbeef",
    "exit_code": 0,
    "commit_count": 3,
    "pr_url": "https://github.com/tedahn/myapp/pull/42",
    "stderr": ""
  }
}
```

### `GET /api/threads`

List recent threads.

**Query params:**
- `limit` (int, default 20) — max threads to return
- `status` (string, optional) — filter by status ("idle", "running", "queued")

**Response:**
```json
{
  "threads": [
    {
      "id": "abc123...",
      "source": "cli",
      "repo_owner": "tedahn",
      "repo_name": "myapp",
      "status": "completed",
      "created_at": "2026-03-21T10:00:00Z",
      "updated_at": "2026-03-21T10:05:00Z"
    }
  ]
}
```

### Authentication

- Controller checks `Authorization: Bearer <key>` header against `DF_API_KEY` setting
- If `DF_API_KEY` is not configured, endpoints are open (local dev mode)
- Add `api_key: str = ""` to the `Settings` class

### Mounting

In `main.py`, import and include the API router:
```python
from controller.api import api_router
app.include_router(api_router)
```

The API router needs access to `app.state.db`, `app.state.redis_state`, and the orchestrator — passed via FastAPI dependency injection or app state.

## Claude Code Skill

File: `controller/skills/ditto/ditto.md`

### Invocation

`/ditto <natural language>` — no rigid subcommands.

### Intent Detection

| User says | Action |
|:--|:--|
| Task description (e.g., "fix the login bug", "add dark mode") | **Submit** — extract task, resolve repo, POST `/api/tasks` |
| "what's happening?", "is it done?", "status" | **Status** — GET `/api/tasks/{thread_id}` for most recent thread |
| "show me the result", "what did it do?" | **Result** — GET `/api/tasks/{thread_id}`, display result fields |
| "what threads are running?", "list agents" | **List** — GET `/api/threads` |

### Repo Resolution

1. Run `git remote get-url origin` in the current working directory
2. Parse `owner/repo` from the remote URL (handles both HTTPS and SSH formats)
3. If not in a git repo or no remote, ask the user to specify

### Configuration

| Env var | Required | Purpose |
|:--|:--|:--|
| `DITTO_CONTROLLER_URL` | Yes | Base URL of the controller (e.g., `https://ditto.example.com`) |
| `DITTO_API_KEY` | No | Bearer token for auth. Omit if controller has no `DF_API_KEY` set |

### Polling After Submit

- After submitting a task, ask user: "Want me to wait for results or check back later?"
- If waiting: poll `GET /api/tasks/{thread_id}` every 10 seconds
- Show brief status updates ("still running... 2m elapsed")
- Timeout after 30 minutes (matches `DF_MAX_JOB_DURATION_SECONDS`)
- User can say "stop waiting" at any time

### Error Handling

| Scenario | Behavior |
|:--|:--|
| Controller unreachable | "Can't reach the controller at {url}. Is it running?" |
| 401/403 | "Auth failed. Check your DITTO_API_KEY." |
| Agent job fails (exit_code != 0) | Show stderr, suggest checking logs |
| No git remote in cwd | Ask user to specify repo |
| Ambiguous intent | Ask user what they meant |

### HTTP Calls

All HTTP calls made via `ctx_execute` (per CLAUDE.md routing rules — no curl/fetch in Bash). Example:

```javascript
const resp = await fetch(`${DITTO_URL}/api/tasks`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${API_KEY}` },
  body: JSON.stringify({ task, repo_owner, repo_name })
});
const data = await resp.json();
console.log(JSON.stringify(data));
```

## What We're NOT Building

- No new `Integration` subclass — "cli" source doesn't need webhook verification or reply-posting
- No CLI binary — the skill covers the interface
- No changes to the agent container, entrypoint, or Redis flow
- No changes to the existing Slack/GitHub/Linear integrations

## Files to Create/Modify

| File | Action |
|:--|:--|
| `controller/src/controller/api.py` | **Create** — REST API router with 3 endpoints + auth middleware |
| `controller/src/controller/main.py` | **Modify** — mount API router, wire up orchestrator |
| `controller/src/controller/config.py` | **Modify** — add `api_key: str = ""` |
| `controller/skills/ditto/ditto.md` | **Create** — Claude Code skill definition |
| `controller/tests/test_api.py` | **Create** — tests for REST endpoints |
