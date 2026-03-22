# Approach A: Controller-Side Skill Registry with Semantic Search

**Status**: Proposed
**Date**: 2026-03-21
**Author**: Architecture Review

---

## 1. Problem Statement

Ditto Factory agents currently run with identical, image-baked capabilities. Every agent gets the same tools regardless of whether the task is "fix a CSS bug" or "write a database migration." This wastes context budget, increases latency, and prevents enterprise teams from curating domain-specific capabilities.

Claude Code's native SKILL.md system cannot solve this at scale: it caps metadata at ~16K chars (~42 skills), uses LLM-based selection with no embeddings, and silently drops skills that exceed the budget. We need a controller-side system that selects and injects the right skills *before* the agent starts.

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CONTROL PLANE                            │
│                                                                 │
│  ┌──────────┐    ┌───────────────┐    ┌───────────────────┐     │
│  │ Webhook  │───▶│  Orchestrator │───▶│  Task Classifier  │     │
│  │ Ingress  │    │               │    │  (Embedding Match) │     │
│  └──────────┘    │               │◀───│                   │     │
│                  │               │    └───────┬───────────┘     │
│                  │               │            │                 │
│                  │               │    ┌───────▼───────────┐     │
│                  │               │    │  Skill Registry   │     │
│                  │               │◀───│  (Postgres + pgv) │     │
│                  │               │    └───────────────────┘     │
│                  │               │                              │
│                  │  ┌────────────▼──────────┐                   │
│                  │  │   Skill Injector      │                   │
│                  │  │   (writes CLAUDE.md + │                   │
│                  │  │    skills/ to PVC)    │                   │
│                  │  └────────────┬──────────┘                   │
│                  │               │                              │
│                  │  ┌────────────▼──────────┐                   │
│                  │  │   Agent Type Resolver  │                   │
│                  │  │   (selects Docker     │                   │
│                  │  │    image from tags)   │                   │
│                  │  └────────────┬──────────┘                   │
│                  └───────────────┤                              │
│                                  │                              │
└──────────────────────────────────┤──────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      K8s Job (Agent)         │
                    │  ┌─────────────────────┐    │
                    │  │ /workspace/.claude/  │    │
                    │  │   CLAUDE.md (merged) │    │
                    │  │   skills/            │    │
                    │  │     debug-react.md   │    │
                    │  │     write-tests.md   │    │
                    │  └─────────────────────┘    │
                    │  ┌─────────────────────┐    │
                    │  │ Claude Code CLI      │    │
                    │  │ (discovers injected  │    │
                    │  │  skills natively)    │    │
                    │  └─────────────────────┘    │
                    └─────────────────────────────┘
```

### Key Insight: Injection, Not Bypass

Rather than bypassing Claude Code's SKILL.md discovery, we *pre-filter* skills controller-side (using embeddings + metadata) and then inject only the matched skills (typically 3-8) into the workspace. Claude Code's native discovery then finds them normally. This keeps us within the ~42-skill budget with room to spare and preserves Claude Code's built-in activation logic.

## 3. System Components

### 3.1 Skill Registry Service

**Responsibility**: Store, version, search, and serve skills.

The registry is a Postgres-backed service using `pgvector` for embedding storage and similarity search. It is deployed as part of the controller (not a separate microservice) to avoid network hops and operational overhead at this stage.

**Why not a separate service?** Ditto Factory is operated by a small team. A modular monolith approach (registry as a Python module within the controller) keeps deployment simple while maintaining clean boundaries. If multi-team access or independent scaling becomes necessary, the module boundary makes extraction straightforward.

```
controller/
  skills/
    __init__.py
    registry.py        # SkillRegistry class (CRUD + search)
    classifier.py      # TaskClassifier (embedding generation + matching)
    injector.py        # SkillInjector (writes files to workspace)
    resolver.py        # AgentTypeResolver (skill tags → image)
    tracker.py         # PerformanceTracker (post-task metrics)
    models.py          # Pydantic models for Skill, SkillVersion, etc.
```

### 3.2 Task Classifier

**Responsibility**: Given a `TaskRequest`, produce a ranked list of relevant skills.

**Algorithm**:
1. Generate an embedding for `task_request.task` (the natural language task description)
2. Query pgvector for top-K similar skills (cosine similarity, K=20)
3. Apply hard filters: repo language, required agent type, org/team restrictions
4. Apply soft ranking boost from performance metrics (success rate, usage frequency)
5. Return top N skills (configurable, default 5, max 10)

**Embedding provider**: Anthropic's Voyage embeddings (`voyage-3`) via API. Rationale: we already depend on Anthropic for the agent runtime; adding OpenAI as a dependency for ada-002 creates a second vendor relationship with no meaningful benefit. Voyage-3 has strong code understanding.

**Fallback**: If embedding service is unavailable, fall back to tag-based matching (exact match on `language`, `domain` tags from TaskRequest metadata). The system degrades gracefully rather than failing.

### 3.3 Skill Injector

**Responsibility**: Write matched skills into the agent's workspace before Claude Code starts.

**Mechanism**: The injector writes skills as SKILL.md files into the cloned repo's `.claude/skills/` directory. This happens in the entrypoint script, after `git clone` but before `claude` is invoked.

**Two injection strategies** (trade-off):

| Strategy | How | Pro | Con |
|----------|-----|-----|-----|
| **A: Redis payload** | Include skill content in the `task:{thread_id}` Redis value | Simple, no new infra | Redis value size grows; 1MB limit per key |
| **B: Init container** | K8s init container fetches skills from registry API, writes to shared volume | Clean separation | Adds latency (~2-5s), more K8s complexity |

**Recommendation**: Start with Strategy A (Redis payload). Skill files are typically 1-5KB each; 10 skills = 10-50KB, well within Redis limits. Migrate to Strategy B only if skill payloads grow large (e.g., skills with embedded code templates).

### 3.4 Agent Type Resolver

**Responsibility**: Map skill requirements to Docker images.

Skills declare their runtime requirements via tags:

```yaml
# Example skill metadata
requires:
  - browser        # needs Playwright/Puppeteer
  - python-3.12    # needs specific Python version
  - gpu            # needs GPU-enabled node
```

The resolver maintains a mapping:

```python
AGENT_TYPE_MAP = {
    frozenset():                    "ditto-factory-agent:latest",        # default
    frozenset({"browser"}):         "ditto-factory-agent:frontend",      # + Playwright
    frozenset({"python-3.12"}):     "ditto-factory-agent:python312",     # Python 3.12
    frozenset({"browser", "gpu"}):  "ditto-factory-agent:frontend-gpu",  # GPU + browser
}
```

**Resolution algorithm**: Union all `requires` tags from matched skills, find the most specific image that covers all requirements. If no exact match, fall back to the image that covers the most requirements and log a warning.

### 3.5 Performance Tracker

**Responsibility**: After task completion, record which skills were injected and whether the task succeeded.

This feeds back into the classifier's ranking. Skills with high success rates for similar tasks get boosted; skills that are frequently injected but don't improve outcomes get demoted.

## 4. Data Model

### 4.1 Skills Table

```sql
CREATE TABLE skills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    slug            VARCHAR(128) NOT NULL UNIQUE,  -- URL-safe identifier
    description     TEXT NOT NULL,                  -- human-readable, used for embedding
    content         TEXT NOT NULL,                  -- the actual SKILL.md content

    -- Classification
    language        VARCHAR(32)[],                  -- ["python", "typescript"]
    domain          VARCHAR(64)[],                  -- ["testing", "frontend", "database"]
    requires        VARCHAR(64)[],                  -- agent type requirements ["browser"]
    tags            VARCHAR(64)[],                  -- free-form tags for filtering

    -- Embedding
    embedding       vector(1024),                   -- voyage-3 embedding of description+content

    -- Ownership
    org_id          VARCHAR(128),                   -- NULL = global, otherwise org-scoped
    created_by      VARCHAR(128) NOT NULL,

    -- Status
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_default      BOOLEAN NOT NULL DEFAULT false, -- always injected regardless of match

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Similarity search index
CREATE INDEX idx_skills_embedding ON skills
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Filter indexes
CREATE INDEX idx_skills_language ON skills USING gin (language);
CREATE INDEX idx_skills_domain ON skills USING gin (domain);
CREATE INDEX idx_skills_org ON skills (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_skills_active ON skills (is_active) WHERE is_active = true;
```

### 4.2 Skill Versions Table

```sql
CREATE TABLE skill_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL,
    embedding       vector(1024),
    changelog       TEXT,
    created_by      VARCHAR(128) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (skill_id, version)
);
```

### 4.3 Skill Usage Metrics Table

```sql
CREATE TABLE skill_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        UUID NOT NULL REFERENCES skills(id),
    thread_id       VARCHAR(128) NOT NULL,
    job_id          VARCHAR(128) NOT NULL,

    -- Context
    task_embedding   vector(1024),                 -- embedding of the task that triggered this
    task_source      VARCHAR(32) NOT NULL,          -- "github" | "slack" | "linear"
    repo_owner       VARCHAR(128),
    repo_name        VARCHAR(128),

    -- Outcome
    was_selected     BOOLEAN NOT NULL DEFAULT true, -- was this skill in the injection set?
    exit_code        INTEGER,                       -- from AgentResult
    commit_count     INTEGER,                       -- from AgentResult
    pr_created       BOOLEAN DEFAULT false,

    -- Timing
    injected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX idx_usage_skill ON skill_usage (skill_id);
CREATE INDEX idx_usage_thread ON skill_usage (thread_id);
```

### 4.4 Agent Types Table

```sql
CREATE TABLE agent_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    image           VARCHAR(256) NOT NULL,          -- Docker image reference
    capabilities    VARCHAR(64)[] NOT NULL,          -- ["browser", "python-3.12"]
    resource_profile JSONB NOT NULL DEFAULT '{}',   -- CPU/memory overrides
    is_default      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 5. API Surface

### 5.1 Skill CRUD

```
POST   /api/v1/skills                    # Create skill
GET    /api/v1/skills                    # List skills (paginated, filterable)
GET    /api/v1/skills/{slug}             # Get skill by slug
PUT    /api/v1/skills/{slug}             # Update skill (creates new version)
DELETE /api/v1/skills/{slug}             # Soft-delete (set is_active=false)
GET    /api/v1/skills/{slug}/versions    # List versions
GET    /api/v1/skills/{slug}/versions/{v}# Get specific version
POST   /api/v1/skills/{slug}/rollback    # Rollback to previous version
```

### 5.2 Skill Search

```
POST   /api/v1/skills/search
Body: {
    "query": "fix react component rendering bug",
    "language": ["typescript", "javascript"],
    "domain": ["frontend"],
    "limit": 10,
    "min_similarity": 0.5
}
Response: {
    "skills": [
        {
            "slug": "debug-react",
            "name": "Debug React Components",
            "similarity": 0.87,
            "success_rate": 0.92,
            "usage_count": 156
        }
    ]
}
```

### 5.3 Internal: Task Classification

```
POST   /api/v1/internal/classify
Body: {
    "task_request": { ... },  // TaskRequest fields
    "max_skills": 5
}
Response: {
    "skills": [ ... ],          // Matched skill objects with content
    "agent_type": "frontend",   // Resolved agent type
    "agent_image": "ditto-factory-agent:frontend"
}
```

### 5.4 Metrics

```
GET    /api/v1/skills/{slug}/metrics     # Usage stats, success rate, trend
POST   /api/v1/skills/usage              # Record usage (called by completion handler)
```

### 5.5 Agent Types

```
GET    /api/v1/agent-types               # List available agent types
POST   /api/v1/agent-types               # Register new agent type
```

## 6. Integration with Existing System

### 6.1 Changes to `orchestrator.py`

The `_spawn_job` method gains three new steps between prompt building and Redis push:

```python
async def _spawn_job(self, thread, task_request, is_retry=False, retry_count=0):
    # ... existing prompt building ...

    # NEW: Classify task and select skills
    matched_skills = await self._skill_registry.classify(
        task=task_request.task,
        language=self._detect_language(thread),  # from repo metadata
        domain=task_request.source_ref.get("labels", []),
    )

    # NEW: Resolve agent type from skill requirements
    agent_image = self._agent_resolver.resolve(
        skills=matched_skills,
        default=self._settings.agent_image,
    )

    # NEW: Include skills in Redis payload
    await self._redis.push_task(thread_id, {
        "task": task_request.task,
        "system_prompt": system_prompt,
        "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
        "branch": branch,
        "skills": [                          # <-- NEW FIELD
            {"name": s.slug, "content": s.content}
            for s in matched_skills
        ],
    })

    # CHANGED: Pass resolved image to spawner
    job_name = self._spawner.spawn(
        thread_id=thread_id,
        github_token="",
        redis_url=self._settings.redis_url,
        agent_image=agent_image,             # <-- NEW PARAM
    )

    # NEW: Record skill injection for tracking
    await self._skill_tracker.record_injection(
        skills=matched_skills,
        thread_id=thread_id,
        job_id=job.id,
        task_request=task_request,
    )
```

### 6.2 Changes to `spawner.py`

Add optional `agent_image` override parameter:

```python
def spawn(self, thread_id, github_token, redis_url, agent_image=None):
    job = self.build_job_spec(thread_id, github_token, redis_url, agent_image)
    # ...

def build_job_spec(self, thread_id, github_token, redis_url, agent_image=None):
    image = agent_image or self._settings.agent_image  # <-- NEW
    container = k8s.V1Container(
        name="agent",
        image=image,  # <-- CHANGED from self._settings.agent_image
        # ...
    )
```

### 6.3 Changes to `entrypoint.sh`

After cloning the repo and before running Claude, inject skills from the Redis payload:

```bash
# After: git clone ... "$WORKSPACE" && cd "$WORKSPACE"
# Before: claude "${CLAUDE_ARGS[@]}"

# NEW: Inject skills from task payload
SKILLS_JSON=$(echo "$TASK_JSON" | jq -r '.skills // empty')
if [ -n "$SKILLS_JSON" ] && [ "$SKILLS_JSON" != "null" ]; then
    mkdir -p .claude/skills
    echo "$SKILLS_JSON" | jq -c '.[]' | while read -r skill; do
        SKILL_NAME=$(echo "$skill" | jq -r '.name')
        SKILL_CONTENT=$(echo "$skill" | jq -r '.content')
        echo "$SKILL_CONTENT" > ".claude/skills/${SKILL_NAME}.md"
    done
    echo "Injected $(echo "$SKILLS_JSON" | jq length) skills"
fi
```

### 6.4 Changes to `handle_job_completion` (Performance Tracking)

```python
async def handle_job_completion(self, thread_id):
    # ... existing completion logic ...

    # NEW: Record skill performance
    await self._skill_tracker.record_outcome(
        thread_id=thread_id,
        exit_code=result.exit_code,
        commit_count=result.commit_count,
        pr_created=result.pr_url is not None,
    )
```

### 6.5 Changes to `config.py`

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Skill Registry
    skill_embedding_provider: str = "voyage"       # "voyage" | "openai"
    skill_embedding_model: str = "voyage-3"
    skill_max_per_task: int = 5
    skill_min_similarity: float = 0.5
    skill_default_agent_image: str = "ditto-factory-agent:latest"
    voyage_api_key: str = ""
```

## 7. Sequence Diagram: Full Flow

```
Slack/GitHub/Linear          Controller                  Postgres            Redis           K8s
       │                        │                           │                  │               │
       │  webhook               │                           │                  │               │
       ├───────────────────────▶│                           │                  │               │
       │                        │                           │                  │               │
       │                        │  parse webhook            │                  │               │
       │                        │──────┐                    │                  │               │
       │                        │◀─────┘ TaskRequest        │                  │               │
       │                        │                           │                  │               │
       │                        │  1. embed(task.text)      │                  │               │
       │                        │──────────────────────────▶│ (Voyage API)     │               │
       │                        │◀─────────────────────────── vector(1024)     │               │
       │                        │                           │                  │               │
       │                        │  2. SELECT * FROM skills  │                  │               │
       │                        │     ORDER BY embedding    │                  │               │
       │                        │     <=> $task_embedding   │                  │               │
       │                        │     WHERE is_active=true  │                  │               │
       │                        │     LIMIT 20              │                  │               │
       │                        │──────────────────────────▶│                  │               │
       │                        │◀──────────────────────────│                  │               │
       │                        │      ranked skills        │                  │               │
       │                        │                           │                  │               │
       │                        │  3. filter by language,   │                  │               │
       │                        │     domain, org; boost    │                  │               │
       │                        │     by success_rate       │                  │               │
       │                        │──────┐                    │                  │               │
       │                        │◀─────┘ top 5 skills       │                  │               │
       │                        │                           │                  │               │
       │                        │  4. resolve agent_type    │                  │               │
       │                        │     from skill.requires   │                  │               │
       │                        │──────┐                    │                  │               │
       │                        │◀─────┘ "frontend" image   │                  │               │
       │                        │                           │                  │               │
       │                        │  5. push_task(thread_id,  │                  │               │
       │                        │     {task, prompt, skills})│                 │               │
       │                        │─────────────────────────────────────────────▶│               │
       │                        │                           │                  │               │
       │                        │  6. spawn(thread_id,      │                  │               │
       │                        │     agent_image=frontend) │                  │               │
       │                        │────────────────────────────────────────────────────────────▶│
       │                        │                           │                  │               │
       │                        │  7. record_injection()    │                  │               │
       │                        │──────────────────────────▶│                  │               │
       │                        │                           │                  │               │
       │                        │                           │                  │    ┌──────────┤
       │                        │                           │                  │    │ Agent    │
       │                        │                           │                  │    │ starts   │
       │                        │                           │                  │◀───┤ reads    │
       │                        │                           │                  │    │ task:id  │
       │                        │                           │                  │    │          │
       │                        │                           │                  │    │ git clone│
       │                        │                           │                  │    │          │
       │                        │                           │                  │    │ write    │
       │                        │                           │                  │    │ skills/  │
       │                        │                           │                  │    │ to repo  │
       │                        │                           │                  │    │          │
       │                        │                           │                  │    │ claude   │
       │                        │                           │                  │    │ runs w/  │
       │                        │                           │                  │    │ skills   │
       │                        │                           │                  │    │          │
       │                        │                           │                  │◀───┤ push     │
       │                        │                           │                  │    │ result   │
       │                        │                           │                  │    └──────────┤
       │                        │                           │                  │               │
       │                        │  handle_job_completion    │                  │               │
       │                        │◀────────────────────────────────────────────── (monitor poll)│
       │                        │                           │                  │               │
       │                        │  8. record_outcome()      │                  │               │
       │                        │──────────────────────────▶│                  │               │
       │                        │                           │                  │               │
       │  result notification   │                           │                  │               │
       │◀───────────────────────│                           │                  │               │
       │                        │                           │                  │               │
```

## 8. Trade-off Analysis

### What's Good

| Aspect | Benefit |
|--------|---------|
| **Semantic matching** | Skills are matched by meaning, not keywords. "Fix flaky test" matches a "test-debugging" skill even without exact keyword overlap. |
| **Bounded injection** | Only 3-8 skills injected per task. Stays well within Claude Code's ~42-skill discovery budget. |
| **Performance feedback loop** | Success/failure data improves matching over time. Bad skills naturally sink in rankings. |
| **Agent type selection** | Heavy dependencies (Playwright, GPU) are only loaded when skills require them. Faster startup for simple tasks. |
| **Versioning** | Skills can be updated without breaking in-flight agents. Rollback is one API call. |
| **Incremental adoption** | The system is fully additive. If the registry is empty or down, agents run exactly as they do today (no skills, default image). |

### What's Risky

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Embedding quality** | Poor embeddings = wrong skills selected = agent confusion | Start with a curated set of 10-20 skills; manually validate matches before scaling |
| **Cold start** | No usage data = no performance boost signal | Seed with manual relevance scores; remove performance boost until N > 50 usage events per skill |
| **Redis payload size** | 10 skills at 5KB each = 50KB; acceptable but could grow | Monitor `task:*` key sizes; migrate to init container strategy if p95 > 200KB |
| **Embedding API latency** | Voyage API adds 100-300ms to task dispatch | Cache embeddings for identical task strings (TTL 24h); pre-compute for common patterns |
| **Skill conflicts** | Two skills give contradictory instructions | Add `conflicts_with` field to skill metadata; classifier checks for conflicts |
| **pgvector at scale** | IVFFlat index recall degrades with > 10K skills | This is unlikely soon; migrate to HNSW index if skill count exceeds 5K |

### What's Complex

| Complexity | Why It Matters |
|------------|----------------|
| **Embedding pipeline** | New external dependency (Voyage API). Requires API key management, rate limiting, retry logic. |
| **Schema migration** | Three new tables in Postgres. Requires pgvector extension (`CREATE EXTENSION vector`). |
| **Entrypoint changes** | Bash script becomes more complex. JSON parsing of skills array in bash is fragile. Consider rewriting entrypoint in Python. |
| **Testing** | Embedding-based matching is non-deterministic. Need integration tests with fixed embeddings and contract tests for the injection flow. |
| **Observability** | Must track: embedding latency, match quality (were injected skills actually used?), agent type resolution accuracy. |

## 9. What We're Giving Up

1. **Simplicity**: Today's system has zero skill selection logic. Adding a registry, embeddings, and classification introduces three new failure points.
2. **Determinism**: Embedding-based matching is fuzzy. The same task might match different skills on different runs if embeddings or skill content change.
3. **Agent autonomy**: Skills are chosen *for* the agent, not *by* the agent. If the classifier picks wrong, the agent has no recourse (it doesn't know about skills that weren't injected).
4. **Operational cost**: pgvector requires Postgres 15+ with the extension. Voyage API costs ~$0.0001/embedding but adds up at scale.

## 10. Estimated Effort

| Component | Size | Effort | Dependencies |
|-----------|------|--------|--------------|
| Data model + migrations | S | 1-2 days | pgvector extension in Postgres |
| Skill Registry (CRUD) | S | 2-3 days | Data model |
| Task Classifier (embeddings + search) | M | 3-5 days | Voyage API key, pgvector |
| Skill Injector (Redis payload + entrypoint) | S | 1-2 days | Entrypoint.sh changes |
| Agent Type Resolver | S | 1 day | Agent type Docker images must exist |
| Performance Tracker | S | 1-2 days | Data model |
| API endpoints | M | 2-3 days | Skill Registry |
| Orchestrator integration | M | 2-3 days | All above |
| Testing (unit + integration) | M | 3-4 days | All above |
| Observability + dashboards | S | 1-2 days | Metrics endpoint |
| **Total** | | **17-27 days** | |

### Suggested phasing

**Phase 1 (MVP, ~8 days)**: Data model + Skill CRUD + tag-based matching (no embeddings) + Redis injection + entrypoint changes. Ships value immediately with manual skill curation.

**Phase 2 (Intelligence, ~8 days)**: Embedding generation + semantic search + agent type resolution. Replaces tag-based matching with semantic matching.

**Phase 3 (Learning, ~6 days)**: Performance tracking + feedback loop + metrics dashboards. System improves over time.

## 11. Open Questions

1. **Skill authoring UX**: Who writes skills? Do we need a web UI, or is the API + CLI sufficient for v1?
2. **Skill scope**: Should skills be per-org, per-repo, or global? The data model supports all three, but the UX implications differ.
3. **Embedding refresh**: When a skill's content is updated, do we re-embed immediately (sync) or in a background job (async)?
4. **Agent-side feedback**: Can the agent report back which skills it actually *used* vs which were injected but ignored? This requires Claude Code to expose skill activation telemetry, which it currently does not.
5. **Entrypoint rewrite**: The bash entrypoint is getting complex. Should we rewrite it in Python as part of this work, or defer?

## 12. Comparison Hook: Approach B

This document describes Approach A (controller-side, centralized). Approach B (agent-side, on-demand) would instead:
- Give the agent an MCP tool to query the registry at runtime
- Let the agent decide which skills to load based on its own analysis
- Avoid the cold-classification problem but add latency during agent execution
- Require changes to the agent image (new MCP server) rather than the controller

A separate document will detail Approach B for comparison.

---

## ADR-001: Controller-Side Skill Selection Over Agent-Side Discovery

### Status
Proposed

### Context
Claude Code's native SKILL.md discovery does not scale past ~42 skills. We need per-task skill injection. Two approaches exist: controller selects skills before agent starts (Approach A), or agent queries a registry during execution (Approach B).

### Decision
Implement controller-side skill selection with semantic search (Approach A).

### Consequences
- **Easier**: Agents start faster (skills pre-loaded). No new MCP server needed. Controller has full visibility into what each agent gets. Performance tracking is straightforward.
- **Harder**: Controller takes on classification responsibility. Wrong classification wastes an entire agent run. No mid-task skill discovery. Requires embedding infrastructure.
