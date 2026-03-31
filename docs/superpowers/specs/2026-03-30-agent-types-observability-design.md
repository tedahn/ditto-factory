# Agent Types Observability Page — Design Spec

**Date:** 2026-03-30
**Status:** Approved (revised after review)
**Scope:** Read-only observability for agent types with resolution diagnostics

## Overview

Add an "Agent Types" tab to the existing `/agents` page that surfaces agent type definitions, capability mappings, and resolution history. The goal is to give the developer full transparency into the agent type selection system to inform future design of smart agent selection and dynamic skill matching.

## Prerequisites

### Fix: Persist Job Fields Already on the Model

The `Job` dataclass has `agent_type` and `skills_injected` fields, but the state backends (`sqlite.py`, `postgres.py`) never write them to the database. Before adding `resolution_diagnostics`, fix the existing gap:

- Add `agent_type TEXT NOT NULL DEFAULT 'general'` and `skills_injected TEXT NOT NULL DEFAULT '[]'` columns to the `jobs` table
- Update INSERT/SELECT statements in both `sqlite.py` and `postgres.py`
- Include these fixes in the same migration (005) as the new column

## Backend Changes

### Schema: Resolution Diagnostics on Jobs

Migration 005: Add three columns to the `jobs` table:
- `agent_type TEXT NOT NULL DEFAULT 'general'` (was on model but not persisted)
- `skills_injected TEXT NOT NULL DEFAULT '[]'` (was on model but not persisted)
- `resolution_diagnostics TEXT` (new, JSON, nullable)
- `CREATE INDEX idx_jobs_agent_type ON jobs(agent_type)` (for aggregation queries)

Update INSERT/SELECT in both `sqlite.py` and `postgres.py` to persist all three fields.

The resolver populates `resolution_diagnostics` with a JSON object on every job:

```json
{
  "required_capabilities": ["browser", "python"],
  "candidates_considered": [
    {"name": "general", "capabilities": [], "coverage": 0},
    {"name": "browser-agent", "capabilities": ["browser", "python"], "coverage": 2}
  ],
  "selected": "browser-agent",
  "reason": "best_match"
}
```

Possible `reason` values: `"best_match"`, `"default_fallback"`, `"override"`.

The orchestrator persists this on the job record when it creates the job.

### Resolver Change

`AgentTypeResolver.resolve()` currently returns `ResolvedAgent(image, agent_type)`. Extend `ResolvedAgent` with a `diagnostics: dict = field(default_factory=dict)` field containing the resolution rationale. This is backward-compatible since existing callers only access `.image` and `.agent_type`.

Note: The resolver's `_find_best_match` already computes candidates and coverage internally but discards them. The refactor requires accumulating candidate info during the internal loop and returning it alongside the best match.

### New API Endpoint

`GET /api/v1/agents/types/summary` (nested under `/agents` to match codebase conventions)

Requires a new `StateBackend` dependency injector in `api.py` (the existing endpoints only use `SkillRegistry`, which has no access to the jobs table).

Returns each agent type enriched with:
- `job_count: int` — total jobs that used this type
- `recent_resolutions: list[ResolutionEvent]` — last 20 resolution events from the jobs table, each containing thread_id, timestamp, required_capabilities, candidates_considered, selected, reason
- `mapped_skills: list[str]` — skill slugs whose `requires` capabilities overlap with this type's capabilities

## Frontend Changes

### Navigation

Add a tab bar to the `/agents` page using the shadcn `Tabs` component with `useSearchParams()` from `next/navigation`:
- **"Threads"** tab — existing agent threads list (default, no breaking change)
- **"Agent Types"** tab — new view

URL: `/agents` defaults to Threads; `/agents?tab=types` shows Agent Types.

This is the first tab usage in the codebase. Use shadcn/ui `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` components.

### Agent Types List View

Card grid layout (matching existing `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4` pattern). Each card shows:
- **Name** + "Default" badge if `is_default`
- **Image** — the Docker image tag
- **Capabilities** — rendered as small tag chips
- **Usage count** — number of jobs that used this type

### Agent Type Detail View

Clicking a card opens an inline expanded view (accordion or sheet pattern) — no new route. With 1-3 expected agent types, inline expansion is more practical than navigation.

Contents:
- Full info: name, image, description, capabilities, resource profile, is_default, created_at
- **Resolution history** — table of recent jobs resolved to this type: thread_id (linked), timestamp, required capabilities, reason
- **Mapped skills** — list of skills whose `requires` overlap with this type's capabilities

### New TypeScript Types

```ts
enum ResolutionReason {
  BEST_MATCH = "best_match",
  DEFAULT_FALLBACK = "default_fallback",
  OVERRIDE = "override",
}

interface AgentTypeSummary {
  id: string;
  name: string;
  image: string;
  description: string | null;
  capabilities: string[];
  is_default: boolean;
  created_at: string;
  job_count: number;
  recent_resolutions: ResolutionEvent[];
  mapped_skills: string[];
}

interface ResolutionEvent {
  thread_id: string;
  timestamp: string;
  required_capabilities: string[];
  candidates_considered: CandidateInfo[];
  selected: string;
  reason: ResolutionReason;
}

interface CandidateInfo {
  name: string;
  capabilities: string[];
  coverage: number;
}
```

### New Hook

`useAgentTypes()` — `@tanstack/react-query` hook using `useQuery` with a `queryKeys.agentTypes` entry, fetching `GET /api/v1/agents/types/summary` via `apiGet<AgentTypeSummary[]>()`. Include `refetchInterval: 30_000` to match existing read-only hooks.

### New Components

All in `web/src/components/agents/`:
- `AgentTypesTab` — list/grid view
- `AgentTypeCard` — individual card
- `AgentTypeDetail` — inline expanded detail with resolution history and mapped skills

## Scope Boundaries

**Included:**
- Read-only observability of agent types
- Fix existing unpersisted Job fields (agent_type, skills_injected)
- Resolution diagnostics persisted on job records
- Summary API endpoint with usage stats and skill mappings
- DB index on jobs.agent_type

**Explicitly NOT included:**
- Create/edit/delete agent types from UI (read-only for now)
- Filtering or search (premature with 1-3 agent types expected)
- Changes to the existing Agents threads tab
- Changes to resolution logic itself
