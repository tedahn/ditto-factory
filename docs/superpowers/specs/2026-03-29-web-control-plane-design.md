# Ditto Factory Web Control Plane — Design Spec

## Overview

A Next.js (App Router, TypeScript) web application providing a full control plane for the Ditto Factory agent platform. Internal team tool, no auth. Runs as a separate Docker service alongside the existing FastAPI controller.

## Architecture

```
Browser → Next.js (port 3000) → FastAPI Controller (port 8000)
                                      ↕
                                 Redis + SQLite/Postgres
```

- Next.js runs in its own container (`web` service in docker-compose)
- API proxy route (`/api/proxy/[...path]`) forwards requests to `http://controller:8000`, avoiding CORS
- SSE via `EventSource` to `GET /api/events/{thread_id}` for real-time updates
- React Query (TanStack Query) for server state management
- shadcn/ui + Tailwind CSS for components

## Pages

### 1. Dashboard (`/`)
- System health indicator (from `GET /health`)
- Stats cards: active agents, completed (24h), failure rate, avg duration
- Recent activity feed (last 20 task events)
- Quick-submit form: repo owner/name, task description, submit button

### 2. Tasks (`/tasks`)
- **List view:** Table — status badge, repo, task summary, created time, duration
- **Filters:** Status (pending/running/completed/failed), repo, date range
- **Submit form** (`/tasks/new`): Repo owner/name, task description, task type dropdown (code_change, analysis, db_mutation, file_output, api_action), optional skill overrides, optional workflow template slug
- **Detail view** (`/tasks/[threadId]`): Full task info, live SSE status, job timeline, result artifacts (PR links, reports), conversation history

### 3. Skills (`/skills`)
- **List view:** Table — name, slug, tags, usage count, last updated
- **Search:** Text input + tag filter
- **Create/Edit** (`/skills/new`, `/skills/[slug]/edit`): Name, slug, description, content (markdown editor with preview), tags, language, domain
- **Version history:** Expandable panel per skill showing versions with rollback button

### 4. Workflows (`/workflows`)
- **Template list:** Table — name, step count, last run status
- **Template editor** (`/workflows/new`, `/workflows/[slug]/edit`): Name, description, JSON editor for DAG definition (with validation), parameter schema editor
- **Execution view** (`/workflows/executions/[id]`): Step-by-step progress — each step shows status, agent, duration. Parallel steps shown as parallel lanes.
- **Run workflow** (`/workflows/[slug]/run`): Select template, fill parameters, estimate cost, execute

### 5. Agents (`/agents`)
- **Active agents:** Live-updating list of running threads/jobs — status, repo, duration, agent type
- **Agent detail** (`/agents/[threadId]`): SSE-powered live log stream, current step, resource info
- **Results:** Links to PRs, reports, file artifacts

## New Backend Endpoints

Two new endpoints on the FastAPI controller:

### `GET /api/events/{thread_id}` (SSE)
- Subscribes to Redis pub/sub channel `thread:{thread_id}:events`
- Streams events: `job_status` (status changes), `log_line` (agent output), `result` (completion)
- Content-Type: `text/event-stream`

### `GET /api/dashboard` (JSON)
- Aggregates: active thread count, completed (24h), failed (24h), avg duration
- Single call replaces N+1 from frontend

## Docker Integration

New `web` service in docker-compose.yaml:

```yaml
web:
  build:
    context: .
    dockerfile: images/web/Dockerfile
  ports: ["3000:3000"]
  environment:
    NEXT_PUBLIC_API_URL: http://controller:8000
  depends_on:
    controller:
      condition: service_started
```

## Tech Stack

- Next.js 15 (App Router)
- TypeScript
- Tailwind CSS + shadcn/ui
- TanStack Query (React Query)
- EventSource API for SSE

## File Structure

```
web/
├── package.json
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── components.json          # shadcn/ui config
├── src/
│   ├── app/
│   │   ├── layout.tsx       # Root layout with sidebar nav
│   │   ├── page.tsx         # Dashboard
│   │   ├── tasks/
│   │   │   ├── page.tsx     # Task list
│   │   │   ├── new/page.tsx # Submit task
│   │   │   └── [threadId]/page.tsx  # Task detail
│   │   ├── skills/
│   │   │   ├── page.tsx     # Skill list
│   │   │   ├── new/page.tsx # Create skill
│   │   │   └── [slug]/edit/page.tsx # Edit skill
│   │   ├── workflows/
│   │   │   ├── page.tsx     # Template list
│   │   │   ├── new/page.tsx # Create template
│   │   │   ├── [slug]/
│   │   │   │   ├── edit/page.tsx # Edit template
│   │   │   │   └── run/page.tsx  # Run workflow
│   │   │   └── executions/
│   │   │       └── [id]/page.tsx # Execution detail
│   │   ├── agents/
│   │   │   ├── page.tsx     # Active agents
│   │   │   └── [threadId]/page.tsx # Agent detail
│   │   └── api/
│   │       └── proxy/
│   │           └── [...path]/route.ts # API proxy
│   ├── lib/
│   │   ├── api.ts           # API client (fetch wrapper)
│   │   ├── sse.ts           # SSE hook (useEventSource)
│   │   └── types.ts         # TypeScript types matching backend models
│   └── components/
│       ├── layout/
│       │   ├── sidebar.tsx
│       │   └── header.tsx
│       ├── dashboard/
│       │   ├── stats-cards.tsx
│       │   └── activity-feed.tsx
│       ├── tasks/
│       │   ├── task-table.tsx
│       │   ├── task-form.tsx
│       │   └── task-detail.tsx
│       ├── skills/
│       │   ├── skill-table.tsx
│       │   ├── skill-form.tsx
│       │   └── version-history.tsx
│       ├── workflows/
│       │   ├── template-table.tsx
│       │   ├── template-editor.tsx
│       │   ├── execution-view.tsx
│       │   └── run-form.tsx
│       └── agents/
│           ├── agent-list.tsx
│           └── agent-detail.tsx
```

## Out of Scope (for now)
- Authentication / authorization
- User accounts / teams
- Notification system (email, Slack)
- Mobile responsiveness (desktop-first)
- Dark mode (can add later with Tailwind)
