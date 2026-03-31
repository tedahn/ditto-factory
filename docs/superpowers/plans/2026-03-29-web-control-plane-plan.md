# Ditto Factory Web Control Plane — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-03-29-web-control-plane-design.md`

## Task Order

Tasks are ordered by dependency — each builds on the previous.

---

### Task 1: Next.js Project Scaffold + Docker Integration

**What:** Initialize the Next.js project in `web/` with TypeScript, Tailwind, shadcn/ui. Create `images/web/Dockerfile`. Add `web` service to `docker-compose.yaml`.

**Files to create:**
- `web/package.json`, `web/next.config.ts`, `web/tailwind.config.ts`, `web/tsconfig.json`, `web/components.json`
- `web/src/app/layout.tsx` — root layout with sidebar navigation (Dashboard, Tasks, Skills, Workflows, Agents links)
- `web/src/app/page.tsx` — placeholder dashboard page
- `images/web/Dockerfile` — Node 22, install deps, build, serve
- Update `docker-compose.yaml` — add `web` service on port 3000

**Acceptance:** `docker compose up -d` starts the web service, visiting `http://localhost:3000` shows the layout with sidebar and placeholder dashboard.

---

### Task 2: API Proxy + Client Library + Types

**What:** Create the API proxy route and shared API client/types.

**Files to create:**
- `web/src/app/api/proxy/[...path]/route.ts` — proxy all requests to `http://controller:8000`
- `web/src/lib/api.ts` — typed fetch wrapper with error handling
- `web/src/lib/types.ts` — TypeScript types matching backend models (TaskRequest, AgentResult, Thread, Job, Skill, WorkflowTemplate, etc.)
- `web/src/lib/query-provider.tsx` — TanStack Query provider wrapper

**Acceptance:** API proxy correctly forwards requests. Types match the backend models from `controller/src/controller/models.py`.

---

### Task 3: Dashboard Page

**What:** Build the dashboard with health status, stats cards, activity feed, and quick-submit form.

**Files to create/edit:**
- `web/src/app/page.tsx` — dashboard page with data fetching
- `web/src/components/dashboard/stats-cards.tsx` — active agents, completed (24h), failure rate, avg duration
- `web/src/components/dashboard/activity-feed.tsx` — recent task events list
- `web/src/components/dashboard/quick-submit.tsx` — inline task submission form
- `web/src/components/layout/sidebar.tsx` — polished sidebar with nav links and active state
- `web/src/components/layout/header.tsx` — top bar with system health indicator

**Acceptance:** Dashboard renders with health status, stats (mocked if backend endpoint not ready), activity feed from `GET /api/threads`, and working quick-submit form.

---

### Task 4: Tasks Pages (List + Submit + Detail)

**What:** Task list with filters, submission form, and detail view.

**Files to create:**
- `web/src/app/tasks/page.tsx` — task list with table, status filters
- `web/src/app/tasks/new/page.tsx` — task submission form
- `web/src/app/tasks/[threadId]/page.tsx` — task detail with status timeline
- `web/src/components/tasks/task-table.tsx` — sortable, filterable table
- `web/src/components/tasks/task-form.tsx` — form with repo, description, task type, skill overrides
- `web/src/components/tasks/task-detail.tsx` — detail view with job info, artifacts, conversation

**Acceptance:** Can list tasks, submit a new task via the form (hits `POST /api/tasks`), view task detail page with status and results.

---

### Task 5: Skills Pages (CRUD + Search + Versions)

**What:** Full skill management UI.

**Files to create:**
- `web/src/app/skills/page.tsx` — skill list with search
- `web/src/app/skills/new/page.tsx` — create skill form
- `web/src/app/skills/[slug]/edit/page.tsx` — edit skill
- `web/src/components/skills/skill-table.tsx` — table with name, tags, usage
- `web/src/components/skills/skill-form.tsx` — form with markdown editor for content
- `web/src/components/skills/version-history.tsx` — version list with rollback

**Acceptance:** Can create, list, edit, delete, search skills. Version history shows with rollback capability.

---

### Task 6: Workflows Pages (Templates + Executions)

**What:** Workflow template management and execution monitoring.

**Files to create:**
- `web/src/app/workflows/page.tsx` — template list
- `web/src/app/workflows/new/page.tsx` — create template
- `web/src/app/workflows/[slug]/edit/page.tsx` — edit template
- `web/src/app/workflows/[slug]/run/page.tsx` — run workflow form
- `web/src/app/workflows/executions/[id]/page.tsx` — execution detail
- `web/src/components/workflows/template-table.tsx`
- `web/src/components/workflows/template-editor.tsx` — JSON editor for DAG definition
- `web/src/components/workflows/execution-view.tsx` — step progress visualization
- `web/src/components/workflows/run-form.tsx` — parameter form + cost estimate

**Acceptance:** Can create/edit templates with JSON DAG editor, run workflows with parameters, view execution progress step-by-step.

---

### Task 7: Agents Monitoring Page + SSE Integration

**What:** Live agent monitoring with SSE-powered updates.

**Files to create:**
- `web/src/lib/sse.ts` — `useEventSource` React hook
- `web/src/app/agents/page.tsx` — active agents list (live updating)
- `web/src/app/agents/[threadId]/page.tsx` — agent detail with live log stream
- `web/src/components/agents/agent-list.tsx` — live-updating agent cards
- `web/src/components/agents/agent-detail.tsx` — log stream, status, results

**Backend addition:**
- Add SSE endpoint `GET /api/events/{thread_id}` to controller (subscribes to Redis pub/sub)
- Add `GET /api/dashboard` summary endpoint

**Acceptance:** Agent list updates in real-time. Agent detail shows live log stream via SSE. Dashboard summary endpoint returns aggregated stats.
