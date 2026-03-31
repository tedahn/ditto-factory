# Agent Types Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Agent Types tab to the Agents page with resolution diagnostics, giving the developer full visibility into agent type selection.

**Architecture:** Backend adds `resolution_diagnostics` JSON column to jobs (plus persists existing `agent_type`/`skills_injected` fields that were missing from DB), extends the resolver to capture decision rationale, and adds a summary API endpoint. Frontend adds a tabbed view to `/agents` with card grid and inline detail expansion.

**Tech Stack:** Python/FastAPI, SQLite/Postgres, Next.js, React Query, shadcn/ui Tabs

---

### Task 1: Migration — Persist Missing Job Fields + Resolution Diagnostics

**Files:**
- Create: `controller/migrations/005_job_resolution_diagnostics.sql`
- Modify: `controller/src/controller/state/sqlite.py:44-53` (inline schema)
- Modify: `controller/src/controller/state/sqlite.py:172-182` (create_job INSERT)
- Modify: `controller/src/controller/state/sqlite.py:184-192` (_row_to_job SELECT)
- Modify: `controller/src/controller/state/postgres.py:126-132` (create_job INSERT)
- Modify: `controller/src/controller/state/postgres.py:134-146` (get_job SELECT)

- [ ] **Step 1: Create migration file**

Create `controller/migrations/005_job_resolution_diagnostics.sql`:

```sql
-- Add fields that exist on the Job model but were never persisted
ALTER TABLE jobs ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'general';
ALTER TABLE jobs ADD COLUMN skills_injected TEXT NOT NULL DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN resolution_diagnostics TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_agent_type ON jobs(agent_type);
```

- [ ] **Step 2: Update SQLite inline schema**

In `controller/src/controller/state/sqlite.py`, update the `CREATE TABLE IF NOT EXISTS jobs` block (line 44) to include the new columns:

```python
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    k8s_job_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    task_context TEXT NOT NULL DEFAULT '{}',
                    result TEXT,
                    agent_type TEXT NOT NULL DEFAULT 'general',
                    skills_injected TEXT NOT NULL DEFAULT '[]',
                    resolution_diagnostics TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)
```

Also add the index after the table creation:

```python
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_agent_type ON jobs(agent_type)
            """)
```

- [ ] **Step 3: Update SQLite create_job INSERT**

In `controller/src/controller/state/sqlite.py`, update `create_job` (line 172) to persist all fields:

```python
    async def create_job(self, job: Job) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO jobs (id, thread_id, k8s_job_name, status, task_context,
                                  agent_type, skills_injected, resolution_diagnostics, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.thread_id, job.k8s_job_name, job.status.value,
                json.dumps(job.task_context),
                job.agent_type,
                json.dumps(job.skills_injected),
                json.dumps(job.resolution_diagnostics) if job.resolution_diagnostics else None,
                job.started_at.isoformat() if job.started_at else None,
            ))
            await db.commit()
```

- [ ] **Step 4: Update SQLite _row_to_job to read new columns**

In `controller/src/controller/state/sqlite.py`, update `_row_to_job` (line 184) to include the new fields:

```python
    def _row_to_job(self, row: aiosqlite.Row) -> Job:
        return Job(
            id=row["id"],
            thread_id=row["thread_id"],
            k8s_job_name=row["k8s_job_name"],
            status=JobStatus(row["status"]),
            task_context=json.loads(row["task_context"]) if row["task_context"] else {},
            result=json.loads(row["result"]) if row["result"] else None,
            agent_type=row["agent_type"] if "agent_type" in row.keys() else "general",
            skills_injected=json.loads(row["skills_injected"]) if "skills_injected" in row.keys() and row["skills_injected"] else [],
            resolution_diagnostics=json.loads(row["resolution_diagnostics"]) if "resolution_diagnostics" in row.keys() and row["resolution_diagnostics"] else None,
            started_at=self._parse_dt(row["started_at"]),
            completed_at=self._parse_dt(row["completed_at"]),
        )
```

- [ ] **Step 5: Update Postgres create_job INSERT**

In `controller/src/controller/state/postgres.py`, update `create_job` (line 126):

```python
    async def create_job(self, job: Job) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO jobs (id, thread_id, k8s_job_name, status, task_context,
                                  agent_type, skills_injected, resolution_diagnostics, started_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """, job.id, job.thread_id, job.k8s_job_name, job.status.value,
                json.dumps(job.task_context),
                job.agent_type,
                json.dumps(job.skills_injected),
                json.dumps(job.resolution_diagnostics) if job.resolution_diagnostics else None,
                job.started_at)
```

- [ ] **Step 6: Update Postgres get_job to read new columns**

In `controller/src/controller/state/postgres.py`, update `get_job` (line 134) to include new fields in the Job constructor:

```python
    async def get_job(self, job_id: str) -> Job | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            if not row:
                return None
            return Job(
                id=row["id"], thread_id=row["thread_id"],
                k8s_job_name=row["k8s_job_name"],
                status=JobStatus(row["status"]),
                task_context=json.loads(row["task_context"]) if row["task_context"] else {},
                result=json.loads(row["result"]) if row["result"] else None,
                agent_type=row.get("agent_type", "general"),
                skills_injected=json.loads(row["skills_injected"]) if row.get("skills_injected") else [],
                resolution_diagnostics=json.loads(row["resolution_diagnostics"]) if row.get("resolution_diagnostics") else None,
                started_at=row["started_at"], completed_at=row["completed_at"],
            )
```

- [ ] **Step 7: Update Job model with resolution_diagnostics field**

In `controller/src/controller/models.py`, add `resolution_diagnostics` to the `Job` dataclass (after line 105):

```python
@dataclass
class Job:
    id: str
    thread_id: str
    k8s_job_name: str
    status: JobStatus = JobStatus.PENDING
    task_context: dict = field(default_factory=dict)
    result: dict | None = None
    agent_type: str = "general"
    skills_injected: list[str] = field(default_factory=list)
    resolution_diagnostics: dict | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

- [ ] **Step 8: Commit**

```bash
git add controller/migrations/005_job_resolution_diagnostics.sql controller/src/controller/state/sqlite.py controller/src/controller/state/postgres.py controller/src/controller/models.py
git commit -m "feat: persist agent_type, skills_injected, and resolution_diagnostics on jobs"
```

---

### Task 2: Resolver — Capture Resolution Diagnostics

**Files:**
- Modify: `controller/src/controller/skills/models.py:132-135` (ResolvedAgent)
- Modify: `controller/src/controller/skills/resolver.py` (resolve + _find_best_match)

- [ ] **Step 1: Extend ResolvedAgent with diagnostics field**

In `controller/src/controller/skills/models.py`, update the `ResolvedAgent` dataclass (line 132):

```python
@dataclass
class ResolvedAgent:
    image: str
    agent_type: str = "general"
    diagnostics: dict = field(default_factory=dict)
```

- [ ] **Step 2: Update _find_best_match to return all candidates**

In `controller/src/controller/skills/resolver.py`, replace the `_find_best_match` method to return candidates alongside the best match. Change the return type and accumulate candidate info:

```python
    async def _find_best_match(
        self, required_caps: set[str]
    ) -> tuple[AgentType | None, list[dict]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM agent_types") as cur:
                rows = await cur.fetchall()

        best: AgentType | None = None
        best_extra = float("inf")
        candidates: list[dict] = []

        for row in rows:
            caps = set(json.loads(row["capabilities"] or "[]"))
            covers = required_caps.issubset(caps)
            coverage = len(required_caps & caps)
            candidates.append({
                "name": row["name"],
                "capabilities": list(caps),
                "coverage": coverage,
                "covers_all": covers,
            })
            if covers:
                extra = len(caps - required_caps)
                if extra < best_extra:
                    best = AgentType(
                        id=row["id"],
                        name=row["name"],
                        image=row["image"],
                        description=row["description"],
                        capabilities=list(caps),
                        resource_profile=json.loads(
                            row["resource_profile"] or "{}"
                        ),
                        is_default=bool(row["is_default"]),
                    )
                    best_extra = extra

        return best, candidates
```

- [ ] **Step 3: Update resolve() to build diagnostics dict**

In `controller/src/controller/skills/resolver.py`, update the `resolve` method:

```python
    async def resolve(self, skills: list[Skill], default_image: str) -> ResolvedAgent:
        required_caps: set[str] = set()
        for skill in skills:
            required_caps.update(skill.requires or [])

        if not required_caps:
            return ResolvedAgent(
                image=default_image,
                agent_type="general",
                diagnostics={
                    "required_capabilities": [],
                    "candidates_considered": [],
                    "selected": "general",
                    "reason": "default_fallback",
                },
            )

        best, candidates = await self._find_best_match(required_caps)
        if best is None:
            logger.warning(
                "No agent type covers requirements %s, using default", required_caps
            )
            return ResolvedAgent(
                image=default_image,
                agent_type="general",
                diagnostics={
                    "required_capabilities": sorted(required_caps),
                    "candidates_considered": candidates,
                    "selected": "general",
                    "reason": "default_fallback",
                },
            )

        return ResolvedAgent(
            image=best.image,
            agent_type=best.name,
            diagnostics={
                "required_capabilities": sorted(required_caps),
                "candidates_considered": candidates,
                "selected": best.name,
                "reason": "best_match",
            },
        )
```

- [ ] **Step 4: Commit**

```bash
git add controller/src/controller/skills/models.py controller/src/controller/skills/resolver.py
git commit -m "feat: capture resolution diagnostics in AgentTypeResolver"
```

---

### Task 3: Orchestrator — Persist Diagnostics on Job Creation

**Files:**
- Modify: `controller/src/controller/orchestrator.py` (where `resolved` is used and where the Job is created)

- [ ] **Step 1: Capture diagnostics from resolver and pass to Job**

In `controller/src/controller/orchestrator.py`, the resolver is called in three places (lines 232, 248, 321) and the Job is created around line 430. Find the variable that holds the resolved result and thread the diagnostics through.

After each `resolved = await self._resolver.resolve(...)` call, capture the diagnostics. Then when creating the Job (around line 440), add the diagnostics:

Where the job is created (around line 430), change:

```python
        job = Job(
            id=job_id,
            thread_id=thread_id,
            k8s_job_name=job_name,
            status=JobStatus.PENDING,
            task_context={
                ...
            },
            agent_type=getattr(classification, 'agent_type', 'general') if classification else 'general',
            skills_injected=skill_names,
            resolution_diagnostics=resolved.diagnostics if resolved else None,
            started_at=datetime.now(timezone.utc),
        )
```

This requires ensuring a `resolved` variable is available at job creation time. Currently the variable `resolved` is set inside try/except blocks. Initialize `resolved = None` before the try blocks and reference it at job creation.

- [ ] **Step 2: Initialize resolved variable before try blocks**

At the top of the orchestration method (before the classification try block), add:

```python
        resolved: ResolvedAgent | None = None
```

Make sure the import is present at the top of the file:

```python
from controller.skills.models import ResolvedAgent
```

- [ ] **Step 3: Handle override case diagnostics**

Where `agent_type_override` is handled (around line 243), build override diagnostics:

```python
            classification = ClassificationResult(
                skills=[],
                agent_type=task_request.agent_type_override,
            )
            if self._resolver:
                try:
                    resolved = await self._resolver.resolve(
                        skills=[],
                        default_image=self._settings.agent_image,
                    )
                    agent_image = resolved.image
                    # Override the diagnostics to reflect the manual override
                    resolved = ResolvedAgent(
                        image=agent_image,
                        agent_type=task_request.agent_type_override,
                        diagnostics={
                            "required_capabilities": [],
                            "candidates_considered": [],
                            "selected": task_request.agent_type_override,
                            "reason": "override",
                        },
                    )
                except Exception:
                    logger.exception("Resolver failed for override")
```

- [ ] **Step 4: Commit**

```bash
git add controller/src/controller/orchestrator.py
git commit -m "feat: persist resolution diagnostics on job creation"
```

---

### Task 4: Summary API Endpoint

**Files:**
- Modify: `controller/src/controller/skills/api.py` (add endpoint + response models + dependency)

- [ ] **Step 1: Add state backend dependency to skills api.py**

In `controller/src/controller/skills/api.py`, add a `get_state_backend` dependency (near the existing `get_skill_registry` at line 141):

```python
def get_state_backend():
    """Provide the state backend -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")
```

- [ ] **Step 2: Add response models for the summary endpoint**

In `controller/src/controller/skills/api.py`, add Pydantic models near the existing response models:

```python
class CandidateInfoResponse(BaseModel):
    name: str
    capabilities: list[str]
    coverage: int
    covers_all: bool = False

class ResolutionEventResponse(BaseModel):
    thread_id: str
    timestamp: str | None
    required_capabilities: list[str]
    candidates_considered: list[CandidateInfoResponse]
    selected: str
    reason: str

class AgentTypeSummaryResponse(BaseModel):
    id: str
    name: str
    image: str
    description: str | None = None
    capabilities: list[str] = []
    is_default: bool = False
    created_at: str | None = None
    job_count: int = 0
    recent_resolutions: list[ResolutionEventResponse] = []
    mapped_skills: list[str] = []
```

- [ ] **Step 3: Add the summary endpoint**

In `controller/src/controller/skills/api.py`, after the existing `create_agent_type` endpoint:

```python
@router.get("/agents/types/summary", response_model=list[AgentTypeSummaryResponse])
async def agent_types_summary(
    registry=Depends(get_skill_registry),
    state=Depends(get_state_backend),
):
    """List agent types with usage stats and recent resolution events."""
    agent_types = await registry.list_agent_types()
    all_skills = await registry.list_all()

    results = []
    for at in agent_types:
        at_caps = set(getattr(at, "capabilities", []))

        # Find skills whose requires overlap with this type's capabilities
        mapped = [
            s.slug for s in all_skills
            if s.requires and at_caps and set(s.requires) & at_caps
        ]

        # Count jobs and get recent resolutions
        job_count = 0
        recent_resolutions = []
        try:
            jobs = await state.list_jobs_by_agent_type(at.name, limit=20)
            job_count = await state.count_jobs_by_agent_type(at.name)
            for job in jobs:
                if job.resolution_diagnostics:
                    recent_resolutions.append(ResolutionEventResponse(
                        thread_id=job.thread_id,
                        timestamp=job.started_at.isoformat() if job.started_at else None,
                        required_capabilities=job.resolution_diagnostics.get("required_capabilities", []),
                        candidates_considered=[
                            CandidateInfoResponse(**c)
                            for c in job.resolution_diagnostics.get("candidates_considered", [])
                        ],
                        selected=job.resolution_diagnostics.get("selected", ""),
                        reason=job.resolution_diagnostics.get("reason", ""),
                    ))
        except Exception:
            logger.exception("Failed to fetch job stats for agent type %s", at.name)

        results.append(AgentTypeSummaryResponse(
            id=at.id,
            name=at.name,
            image=at.image,
            description=getattr(at, "description", None),
            capabilities=getattr(at, "capabilities", []),
            is_default=getattr(at, "is_default", False),
            created_at=str(getattr(at, "created_at", "")) if hasattr(at, "created_at") else None,
            job_count=job_count,
            recent_resolutions=recent_resolutions,
            mapped_skills=mapped,
        ))

    return results
```

- [ ] **Step 4: Add state query methods to SQLite backend**

In `controller/src/controller/state/sqlite.py`, add two methods:

```python
    async def list_jobs_by_agent_type(self, agent_type: str, limit: int = 20) -> list[Job]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE agent_type = ? ORDER BY started_at DESC LIMIT ?",
                (agent_type, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def count_jobs_by_agent_type(self, agent_type: str) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM jobs WHERE agent_type = ?",
                (agent_type,),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0
```

- [ ] **Step 5: Add state query methods to Postgres backend**

In `controller/src/controller/state/postgres.py`, add the same two methods:

```python
    async def list_jobs_by_agent_type(self, agent_type: str, limit: int = 20) -> list[Job]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM jobs WHERE agent_type = $1 ORDER BY started_at DESC LIMIT $2",
                agent_type, limit,
            )
        return [Job(
            id=row["id"], thread_id=row["thread_id"],
            k8s_job_name=row["k8s_job_name"],
            status=JobStatus(row["status"]),
            task_context=json.loads(row["task_context"]) if row["task_context"] else {},
            result=json.loads(row["result"]) if row["result"] else None,
            agent_type=row.get("agent_type", "general"),
            skills_injected=json.loads(row["skills_injected"]) if row.get("skills_injected") else [],
            resolution_diagnostics=json.loads(row["resolution_diagnostics"]) if row.get("resolution_diagnostics") else None,
            started_at=row["started_at"], completed_at=row["completed_at"],
        ) for row in rows]

    async def count_jobs_by_agent_type(self, agent_type: str) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE agent_type = $1",
                agent_type,
            )
        return row or 0
```

- [ ] **Step 6: Wire up state backend dependency in main.py**

In `controller/src/controller/main.py`, where the skills router is mounted (around line 550), add the state backend override:

```python
        try:
            from controller.skills.api import router as skills_router, get_skill_registry, get_performance_tracker, get_state_backend
            app.dependency_overrides[get_skill_registry] = lambda: skill_registry
            app.dependency_overrides[get_performance_tracker] = lambda: tracker
            app.dependency_overrides[get_state_backend] = lambda: app.state.db
            app.include_router(skills_router)
            logger.info("Skills API router mounted")
        except Exception:
```

- [ ] **Step 7: Commit**

```bash
git add controller/src/controller/skills/api.py controller/src/controller/state/sqlite.py controller/src/controller/state/postgres.py controller/src/controller/main.py
git commit -m "feat: add GET /agents/types/summary endpoint with job stats"
```

---

### Task 5: Frontend — Install Tabs Component + Add Types

**Files:**
- Create: `web/src/components/ui/tabs.tsx` (shadcn component)
- Modify: `web/src/lib/types.ts` (add new types)

- [ ] **Step 1: Install shadcn tabs component**

Run from `web/` directory:

```bash
cd /Users/tedahn/Documents/codebase/ditto-factory/web && npx shadcn@latest add tabs
```

This creates `web/src/components/ui/tabs.tsx`.

- [ ] **Step 2: Add new types to types.ts**

In `web/src/lib/types.ts`, add at the end of the enums section (after `ResultType`):

```ts
export enum ResolutionReason {
  BEST_MATCH = "best_match",
  DEFAULT_FALLBACK = "default_fallback",
  OVERRIDE = "override",
}
```

Then add the interfaces at the end of the file:

```ts
// ---- Agent Types ----

export interface CandidateInfo {
  name: string;
  capabilities: string[];
  coverage: number;
  covers_all: boolean;
}

export interface ResolutionEvent {
  thread_id: string;
  timestamp: string | null;
  required_capabilities: string[];
  candidates_considered: CandidateInfo[];
  selected: string;
  reason: ResolutionReason;
}

export interface AgentTypeSummary {
  id: string;
  name: string;
  image: string;
  description: string | null;
  capabilities: string[];
  is_default: boolean;
  created_at: string | null;
  job_count: number;
  recent_resolutions: ResolutionEvent[];
  mapped_skills: string[];
}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ui/tabs.tsx web/src/lib/types.ts
git commit -m "feat: add shadcn Tabs component and agent type TS types"
```

---

### Task 6: Frontend — Hook + Agent Type Card Component

**Files:**
- Modify: `web/src/lib/hooks.ts` (add useAgentTypes hook)
- Create: `web/src/components/agents/agent-type-card.tsx`

- [ ] **Step 1: Add query key and hook**

In `web/src/lib/hooks.ts`, add to the `queryKeys` object (after `toolkits` entries):

```ts
  agentTypeSummary: ["agent-types-summary"] as const,
```

Add the import for `AgentTypeSummary` to the type imports at the top of the file.

Then add the hook at the end of the file (or in the agents section):

```ts
export function useAgentTypes() {
  return useQuery({
    queryKey: queryKeys.agentTypeSummary,
    queryFn: () => apiGet<AgentTypeSummary[]>("/api/v1/agents/types/summary"),
    refetchInterval: 30_000,
  });
}
```

- [ ] **Step 2: Create AgentTypeCard component**

Create `web/src/components/agents/agent-type-card.tsx`:

```tsx
"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgentTypeSummary } from "@/lib/types";

interface AgentTypeCardProps {
  agentType: AgentTypeSummary;
  isExpanded: boolean;
  onToggle: () => void;
}

export function AgentTypeCard({ agentType, isExpanded, onToggle }: AgentTypeCardProps) {
  return (
    <Card
      className={`cursor-pointer transition-colors hover:bg-accent/50 ${isExpanded ? "ring-2 ring-primary" : ""}`}
      onClick={onToggle}
    >
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-sm">{agentType.name}</h3>
          <div className="flex gap-1.5">
            {agentType.is_default && (
              <Badge variant="secondary" className="text-xs">Default</Badge>
            )}
            <Badge variant="outline" className="text-xs font-mono">
              {agentType.job_count} jobs
            </Badge>
          </div>
        </div>

        <p className="text-xs text-muted-foreground font-mono truncate">
          {agentType.image}
        </p>

        {agentType.capabilities.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {agentType.capabilities.map((cap) => (
              <Badge key={cap} variant="outline" className="text-xs">
                {cap}
              </Badge>
            ))}
          </div>
        )}

        {agentType.capabilities.length === 0 && (
          <p className="text-xs text-muted-foreground italic">No capabilities defined</p>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/hooks.ts web/src/components/agents/agent-type-card.tsx
git commit -m "feat: add useAgentTypes hook and AgentTypeCard component"
```

---

### Task 7: Frontend — Agent Type Detail (Inline Expand)

**Files:**
- Create: `web/src/components/agents/agent-type-detail.tsx`

- [ ] **Step 1: Create the detail component**

Create `web/src/components/agents/agent-type-detail.tsx`:

```tsx
"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AgentTypeSummary } from "@/lib/types";

interface AgentTypeDetailProps {
  agentType: AgentTypeSummary;
}

export function AgentTypeDetail({ agentType }: AgentTypeDetailProps) {
  return (
    <Card className="col-span-full">
      <CardContent className="p-5 space-y-5">
        {/* Header info */}
        <div className="grid grid-cols-2 gap-4 text-sm">
          <InfoRow label="Name" value={agentType.name} />
          <InfoRow label="Image" value={agentType.image} mono />
          <InfoRow label="Default" value={agentType.is_default ? "Yes" : "No"} />
          <InfoRow label="Total Jobs" value={String(agentType.job_count)} />
          {agentType.description && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Description: </span>
              <span>{agentType.description}</span>
            </div>
          )}
        </div>

        {/* Capabilities */}
        <div>
          <h4 className="text-sm font-medium mb-2">Capabilities</h4>
          {agentType.capabilities.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {agentType.capabilities.map((cap) => (
                <Badge key={cap} variant="outline">{cap}</Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">None defined</p>
          )}
        </div>

        {/* Mapped Skills */}
        <div>
          <h4 className="text-sm font-medium mb-2">Mapped Skills</h4>
          {agentType.mapped_skills.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {agentType.mapped_skills.map((slug) => (
                <Link key={slug} href={`/skills?slug=${slug}`}>
                  <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
                    {slug}
                  </Badge>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">No skills map to this type</p>
          )}
        </div>

        {/* Resolution History */}
        <div>
          <h4 className="text-sm font-medium mb-2">Recent Resolutions</h4>
          {agentType.recent_resolutions.length > 0 ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Thread</TableHead>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Required Capabilities</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agentType.recent_resolutions.map((event, i) => (
                    <TableRow key={`${event.thread_id}-${i}`}>
                      <TableCell>
                        <Link
                          href={`/agents/${event.thread_id}`}
                          className="text-primary hover:underline font-mono text-xs"
                        >
                          {event.thread_id.slice(0, 8)}...
                        </Link>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {event.timestamp ? new Date(event.timestamp).toLocaleString() : "--"}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {event.required_capabilities.map((cap) => (
                            <Badge key={cap} variant="outline" className="text-xs">
                              {cap}
                            </Badge>
                          ))}
                          {event.required_capabilities.length === 0 && (
                            <span className="text-xs text-muted-foreground">none</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={event.reason === "best_match" ? "default" : "secondary"}
                          className="text-xs"
                        >
                          {event.reason}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">No resolution events recorded yet</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={mono ? "font-mono text-xs" : ""}>{value}</span>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/components/agents/agent-type-detail.tsx
git commit -m "feat: add AgentTypeDetail inline expansion component"
```

---

### Task 8: Frontend — Agent Types Tab + Page Integration

**Files:**
- Create: `web/src/components/agents/agent-types-tab.tsx`
- Modify: `web/src/app/agents/page.tsx`

- [ ] **Step 1: Create AgentTypesTab component**

Create `web/src/components/agents/agent-types-tab.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useAgentTypes } from "@/lib/hooks";
import { AgentTypeCard } from "./agent-type-card";
import { AgentTypeDetail } from "./agent-type-detail";

export function AgentTypesTab() {
  const { data: agentTypes, isLoading, isError } = useAgentTypes();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading agent types...</p>;
  }

  if (isError) {
    return <p className="text-sm text-red-400">Failed to load agent types.</p>;
  }

  if (!agentTypes || agentTypes.length === 0) {
    return <p className="text-sm text-muted-foreground">No agent types registered.</p>;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {agentTypes.map((at) => (
        <AgentTypeCard
          key={at.id}
          agentType={at}
          isExpanded={expandedId === at.id}
          onToggle={() => setExpandedId(expandedId === at.id ? null : at.id)}
        />
      ))}
      {expandedId && agentTypes.find((at) => at.id === expandedId) && (
        <AgentTypeDetail
          agentType={agentTypes.find((at) => at.id === expandedId)!}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update agents page with tabs**

Replace the contents of `web/src/app/agents/page.tsx`:

```tsx
"use client";

import { useSearchParams } from "next/navigation";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AgentList } from "@/components/agents/agent-list";
import { AgentTypesTab } from "@/components/agents/agent-types-tab";
import { useThreads, useDashboardSummary } from "@/lib/hooks";

export default function AgentsPage() {
  const searchParams = useSearchParams();
  const tab = searchParams.get("tab") || "threads";
  const { data: threads, isLoading, isError } = useThreads();
  const { data: summary } = useDashboardSummary();

  const activeThreads = (threads || []).filter(
    (t) => t.status === "running" || t.status === "queued",
  );

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div>
          <h1 className="text-lg font-semibold text-foreground">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Monitor active agents and their real-time status
          </p>
        </div>

        <Tabs defaultValue={tab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="threads">Threads</TabsTrigger>
            <TabsTrigger value="types">Agent Types</TabsTrigger>
          </TabsList>

          <TabsContent value="threads" className="space-y-6">
            {/* Summary stats */}
            {summary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard label="Active" value={summary.active_count} accent="emerald" />
                <StatCard label="Completed (24h)" value={summary.completed_24h} accent="blue" />
                <StatCard label="Failed (24h)" value={summary.failed_24h} accent="red" />
                <StatCard
                  label="Avg Duration"
                  value={formatDuration(summary.avg_duration_seconds)}
                  accent="amber"
                />
              </div>
            )}

            {/* Agent cards */}
            <AgentList threads={activeThreads} isLoading={isLoading} isError={isError} />

            {/* All threads (including inactive) */}
            {threads && threads.length > activeThreads.length && (
              <div className="space-y-3">
                <h2 className="text-sm font-medium text-muted-foreground">
                  Recent Idle Threads
                </h2>
                <AgentList
                  threads={threads.filter((t) => t.status === "idle").slice(0, 6)}
                  isLoading={false}
                  isError={false}
                />
              </div>
            )}
          </TabsContent>

          <TabsContent value="types">
            <AgentTypesTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent: string;
}) {
  const colorMap: Record<string, string> = {
    emerald: "text-emerald-400",
    blue: "text-blue-400",
    red: "text-red-400",
    amber: "text-amber-400",
  };

  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={`text-2xl font-semibold font-mono mt-1 ${colorMap[accent] || "text-foreground"}`}>
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function formatDuration(seconds: number): string {
  if (seconds === 0) return "--";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}
```

- [ ] **Step 3: Run dev server and verify**

```bash
cd /Users/tedahn/Documents/codebase/ditto-factory/web && npm run build
```

Expected: No build errors. The `/agents` page renders with two tabs. The "Agent Types" tab fetches from the API and shows cards.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/agents/agent-types-tab.tsx web/src/app/agents/page.tsx
git commit -m "feat: add Agent Types tab to agents page with inline detail"
```
