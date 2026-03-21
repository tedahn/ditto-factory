---
name: ditto
description: Submit tasks to Ditto Factory agents, check status, and retrieve results. Use when user wants to dispatch coding work to a remote agent, check on running agents, or get agent results.
---

# Ditto Factory Skill

You are helping the user interact with Ditto Factory, a K8s-native coding agent platform. Users speak in natural language — your job is to detect their intent and call the right API.

## Intent Detection

Classify the user's message into one of these actions:

| Intent | Trigger phrases | Action |
|:--|:--|:--|
| **Submit** | "fix the login bug", "add dark mode", "refactor auth", any task description | Submit a new task |
| **Status** | "what's happening?", "is it done?", "status", "check on my agent" | Check task status |
| **Result** | "show me the result", "what did it do?", "what changed?" | Get completed result |
| **List** | "what threads are running?", "list agents", "show all tasks" | List recent threads |

If the intent is ambiguous, ask the user: "Did you want me to submit that as a task, or were you asking about something else?"

## Configuration

Before making any API call, verify these environment variables:

- **`DITTO_CONTROLLER_URL`** (required) — Base URL of the controller (e.g., `http://localhost:8000`). If not set, tell the user: "Set `DITTO_CONTROLLER_URL` to your controller's base URL (e.g., `export DITTO_CONTROLLER_URL=http://localhost:8000`)."
- **`DITTO_API_KEY`** (optional) — Bearer token for authentication. If set, include it as an `Authorization: Bearer <key>` header.

Check these by reading `process.env.DITTO_CONTROLLER_URL` and `process.env.DITTO_API_KEY` inside your `ctx_execute` calls.

## Repo Resolution

Before submitting a task, resolve the current repository:

1. Run via Bash: `git remote get-url origin`
2. Parse owner and repo name from the URL:
   - HTTPS: `https://github.com/owner/repo.git` → owner=`owner`, repo=`repo`
   - SSH: `git@github.com:owner/repo.git` → owner=`owner`, repo=`repo`
3. Strip the `.git` suffix if present.
4. If the command fails (not a git repo or no remote), ask the user: "I can't detect the repo from git. What's the repo owner and name? (e.g., `acme/my-project`)"

## Actions

### Submit a Task

When the user describes work to be done, submit it to Ditto Factory.

Use `ctx_execute` with `language: "javascript"`:

```javascript
const url = process.env.DITTO_CONTROLLER_URL;
const key = process.env.DITTO_API_KEY || '';
const headers = { 'Content-Type': 'application/json' };
if (key) headers['Authorization'] = `Bearer ${key}`;
const resp = await fetch(`${url}/api/tasks`, {
  method: 'POST',
  headers,
  body: JSON.stringify({ task: '<TASK>', repo_owner: '<OWNER>', repo_name: '<REPO>' })
});
const data = await resp.json();
console.log(JSON.stringify(data, null, 2));
```

Replace `<TASK>` with the user's task description, `<OWNER>` and `<REPO>` with the resolved values.

**After submitting:**

1. Show the `thread_id` to the user clearly.
2. Ask: "Want me to wait for results or check back later?"
3. If the user wants to wait, poll the status endpoint with exponential backoff:
   - Start at 10 seconds, double each time (10s, 20s, 40s), cap at 60 seconds.
   - Timeout after 30 minutes total.
   - Show brief status updates during polling (e.g., "Still running... (2 min elapsed)").
   - When status is `completed` or `failed`, stop polling and show the result.

**Remember the `thread_id`** in conversation context for follow-up queries.

### Check Status / Get Result

When the user asks about status or results, fetch the task details.

If the user doesn't specify a thread_id, use the most recently tracked one from this conversation. If there is no tracked thread_id, ask: "Which thread? Give me the thread ID or I can list recent ones."

Use `ctx_execute` with `language: "javascript"`:

```javascript
const url = process.env.DITTO_CONTROLLER_URL;
const key = process.env.DITTO_API_KEY || '';
const headers = {};
if (key) headers['Authorization'] = `Bearer ${key}`;
const resp = await fetch(`${url}/api/tasks/<THREAD_ID>`, { headers });
const data = await resp.json();
console.log(JSON.stringify(data, null, 2));
```

Replace `<THREAD_ID>` with the actual thread ID.

**Format the response:**

- **Status**: Show the current status prominently (queued, running, completed, failed).
- **If completed**: Show the branch name, commit count, and PR URL (if available).
- **If failed**: Show the exit code and stderr output. Suggest: "Check the agent logs for more details."
- **If running**: Show elapsed time and any available progress info.

### List Threads

When the user wants to see all recent threads.

Use `ctx_execute` with `language: "javascript"`:

```javascript
const url = process.env.DITTO_CONTROLLER_URL;
const key = process.env.DITTO_API_KEY || '';
const headers = {};
if (key) headers['Authorization'] = `Bearer ${key}`;
const resp = await fetch(`${url}/api/threads`, { headers });
const data = await resp.json();
console.log(JSON.stringify(data, null, 2));
```

**Format as a table:**

| Thread ID | Source | Repo | Status | Created |
|:--|:--|:--|:--|:--|

- Truncate thread IDs to the first 8 characters for readability (show full ID only if the user asks).
- Sort by most recent first.

## Error Handling

| Scenario | Response |
|:--|:--|
| `DITTO_CONTROLLER_URL` not set | "Set `DITTO_CONTROLLER_URL` to your controller's base URL." |
| Controller unreachable (fetch throws) | "Can't reach the controller at `{url}`. Is it running?" |
| 401 or 403 response | "Auth failed. Check your `DITTO_API_KEY`." |
| Agent job fails (exit_code != 0) | Show stderr content. Suggest checking logs. |
| No git remote in current directory | Ask user to specify repo as `owner/repo`. |
| Ambiguous user intent | Ask what they meant before acting. |

## Thread Tracking

Remember the most recent `thread_id` from any submit or status call within this conversation. When the user says things like "is it done?" or "show me the result" without specifying a thread, use the remembered ID automatically. If multiple threads have been used, ask which one they mean.

## Tone

Be conversational and concise. Examples:

- "Submitted! Thread `a1b2c3d4`. Want me to wait for it or check back later?"
- "Still running (3 min in). I'll check again in 40 seconds."
- "Done! The agent pushed 4 commits to branch `fix/login-bug` and opened PR #42."
- "That thread failed with exit code 1. Here's what went wrong: ..."
