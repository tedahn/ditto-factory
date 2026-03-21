# Approach C: Hybrid Skill Platform with Remote MCP Gateway

## Status
Proposed — 2026-03-21

## Executive Summary

Approach C combines an Enterprise Skill Registry (text-based instruction injection) with a Remote MCP Gateway (centralized tool server) to deliver per-task dynamic capability injection without proliferating agent Docker images. The gateway runs heavy tools (Postgres clients, Playwright, linters) as a shared K8s Deployment, while agents connect via SSE/streamable-HTTP transport. A two-phase classifier (rule-based pre-filter + LLM refinement) selects skills and tool scopes per task.

**Core insight**: Most "agent types" exist only because different tasks need different installed binaries. If those binaries run remotely behind an MCP interface, a single general-purpose agent image handles 90%+ of tasks.

---

## 1. Architecture Overview

```
                         Webhooks (Slack / GitHub / Linear)
                                    │
                                    ▼
                      ┌──────────────────────────┐
                      │    FastAPI Controller     │
                      │    (existing, modified)   │
                      └─────────┬────────────────┘
                                │
                    ┌───────────┼───────────────┐
                    ▼           ▼               ▼
          ┌─────────────┐ ┌──────────┐ ┌──────────────────┐
          │  Two-Phase  │ │  Skill   │ │  Agent Type      │
          │  Classifier │ │ Registry │ │  Resolver        │
          └──────┬──────┘ └────┬─────┘ └────────┬─────────┘
                 │             │                 │
                 └─────────────┼─────────────────┘
                               ▼
                      ┌─────────────────┐
                      │  Orchestrator   │  ← Enhanced _spawn_job()
                      │  (modified)     │
                      └────────┬────────┘
                               │
                    ┌──────────┼──────────────┐
                    ▼                         ▼
          ┌──────────────────┐     ┌─────────────────────┐
          │  Agent Pod (Job) │────▶│   MCP Gateway       │
          │  claude -p       │ SSE │   (K8s Deployment)  │
          │  + mcp.json      │     │   Per-agent scoping │
          │  + injected      │     │   Postgres, Playwright,│
          │    CLAUDE.md     │     │   custom tools...   │
          └──────────────────┘     └──────────┬──────────┘
                                              │
                                    ┌─────────┼─────────┐
                                    ▼         ▼         ▼
                              ┌────────┐ ┌────────┐ ┌────────┐
                              │Postgres│ │Browser │ │Custom  │
                              │Client  │ │Engine  │ │Tools   │
                              └────────┘ └────────┘ └────────┘
```

---

## 2. Remote MCP Gateway

### 2.1 What It Is

A centralized MCP server running as a K8s Deployment (not a sidecar, not in the agent pod). It exposes tools over **SSE transport** (MCP spec `streamable-http`) and dynamically scopes which tools each agent session can access.

### 2.2 Architecture

```
┌─────────────────────────────────────────────────────┐
│                   MCP Gateway Pod                    │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Gateway Router (FastAPI / Express)            │   │
│  │                                                │   │
│  │  GET /sse?session_id=XXX                      │   │
│  │    → Authenticate session                      │   │
│  │    → Load tool scope from Redis                │   │
│  │    → Proxy MCP messages to tool backends       │   │
│  │                                                │   │
│  │  POST /messages?session_id=XXX                 │   │
│  │    → Validate tool call against scope          │   │
│  │    → Route to appropriate tool backend         │   │
│  │    → Return result via SSE stream              │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌────────────┐ ┌────────────┐ ┌────────────────┐   │
│  │ PostgreSQL │ │ Playwright │ │ File Analysis  │   │
│  │ Tool       │ │ Tool       │ │ Tool           │   │
│  │ Backend    │ │ Backend    │ │ Backend        │   │
│  └────────────┘ └────────────┘ └────────────────┘   │
│                                                      │
│  Tool backends are loaded as in-process modules     │
│  or as child MCP servers (stdio transport)           │
└─────────────────────────────────────────────────────┘
```

### 2.3 Per-Agent Tool Scoping

When the orchestrator spawns an agent, it writes a **session scope** to Redis:

```json
{
  "session_id": "df-abc12345-1711000000",
  "thread_id": "sha256:...",
  "allowed_tools": ["postgres_query", "postgres_schema", "file_analyze"],
  "denied_tools": ["postgres_drop", "postgres_alter"],
  "tool_config": {
    "postgres_query": {
      "connection_string": "postgresql://readonly:***@prod-replica:5432/app",
      "max_rows": 1000,
      "timeout_seconds": 30
    }
  },
  "expires_at": "2026-03-21T12:30:00Z"
}
```

The gateway reads this on SSE connection and filters the `tools/list` response to only include allowed tools. Any `tools/call` for a non-allowed tool returns an MCP error.

### 2.4 Agent mcp.json Configuration

The orchestrator generates a per-task `mcp.json` and injects it via ConfigMap or init container:

```json
{
  "mcpServers": {
    "message-queue": {
      "command": "node",
      "args": ["/opt/mcp/message-queue-server.js"],
      "env": { "REDIS_URL": "${REDIS_URL}", "THREAD_ID": "${THREAD_ID}" }
    },
    "gateway": {
      "type": "sse",
      "url": "http://mcp-gateway.ditto-factory.svc.cluster.local:8080/sse",
      "headers": {
        "Authorization": "Bearer ${MCP_SESSION_TOKEN}"
      }
    }
  }
}
```

The `message-queue` server stays local (it needs Redis access for follow-ups). The `gateway` entry is only present when the task requires remote tools.

### 2.5 Gateway Security Model

| Layer | Mechanism |
|-------|-----------|
| **Network** | K8s NetworkPolicy: only pods with label `app: ditto-factory-agent` can reach the gateway |
| **Authentication** | Short-lived JWT (session token) signed by controller, embedded in mcp.json, verified by gateway |
| **Authorization** | Per-session tool scope in Redis (allowlist model) |
| **Data isolation** | Tool configs (DB credentials) are per-session, never shared across agents |
| **Expiry** | Session tokens and Redis scopes expire with `max_job_duration_seconds` |
| **Audit** | All tool calls logged with session_id, thread_id, tool_name, timestamp |

Token structure:
```json
{
  "sub": "df-abc12345-1711000000",
  "thread_id": "sha256:...",
  "iat": 1711000000,
  "exp": 1711001800,
  "iss": "ditto-factory-controller"
}
```

---

## 3. Enterprise Skill Registry

### 3.1 Data Model

```sql
CREATE TABLE skills (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(128) UNIQUE NOT NULL,
    slug        VARCHAR(128) UNIQUE NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,

    -- Content
    content     TEXT NOT NULL,             -- The SKILL.md / CLAUDE.md content
    description TEXT NOT NULL,             -- Human-readable summary

    -- Classification metadata
    tags        TEXT[] NOT NULL DEFAULT '{}',
    languages   TEXT[] NOT NULL DEFAULT '{}',   -- e.g., ['python', 'typescript']
    frameworks  TEXT[] NOT NULL DEFAULT '{}',   -- e.g., ['fastapi', 'react']

    -- Dependency declarations (Skill-as-Code)
    required_tools    TEXT[] NOT NULL DEFAULT '{}',   -- MCP tools needed from gateway
    required_agent    VARCHAR(64) DEFAULT 'general',  -- Agent type: general | browser
    min_context_chars INTEGER DEFAULT 0,              -- Minimum context budget

    -- Embeddings for semantic search
    embedding   VECTOR(1536),             -- OpenAI text-embedding-3-small or similar

    -- Performance tracking
    usage_count     INTEGER DEFAULT 0,
    success_rate    FLOAT DEFAULT 0.0,
    avg_duration_s  FLOAT DEFAULT 0.0,
    last_used_at    TIMESTAMPTZ,

    -- Lifecycle
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    created_by  VARCHAR(128)
);

CREATE INDEX idx_skills_embedding ON skills USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_skills_tags ON skills USING gin (tags);
CREATE INDEX idx_skills_languages ON skills USING gin (languages);
CREATE INDEX idx_skills_active ON skills (is_active) WHERE is_active = TRUE;
```

### 3.2 Dependency Declarations (Skill-as-Code)

Each skill declares what it needs to function:

```yaml
# Example: database-migration skill
name: database-migration
version: 3
description: "Generate and review Alembic/Django migrations"
languages: [python]
frameworks: [sqlalchemy, django]
tags: [database, migration, schema]

dependencies:
  required_tools:
    - postgres_query      # Read current schema
    - postgres_schema     # Introspect tables
  required_agent: general  # No special image needed
  min_context_chars: 8000  # Migration scripts can be verbose

content: |
  # Database Migration Specialist
  You are an expert at generating safe, reversible database migrations...
  [skill instructions here]
```

At **registration time**, the registry validates:
1. All `required_tools` exist in the gateway's tool catalog
2. The `required_agent` type is a known agent type
3. The content fits within Claude Code's SKILL.md budget (16K chars)
4. The skill doesn't conflict with existing skills (duplicate tool requirements)

### 3.3 Skill Registry API

```
POST   /api/skills              — Register a new skill
GET    /api/skills              — List skills (filterable)
GET    /api/skills/{slug}       — Get skill by slug
PUT    /api/skills/{slug}       — Update skill (bumps version)
DELETE /api/skills/{slug}       — Soft-delete (is_active = false)
POST   /api/skills/search       — Semantic search by embedding similarity
POST   /api/skills/classify     — Run two-phase classification for a task
GET    /api/skills/{slug}/stats — Performance metrics
```

---

## 4. Two-Phase Task Classification

### 4.1 Phase 1: Rule-Based Pre-Filter (< 10ms)

Fast, deterministic filtering that narrows ~100 skills to ~5-10 candidates.

```python
class RuleBasedPreFilter:
    """Phase 1: Cheap heuristics to narrow skill candidates."""

    def filter(self, task: TaskRequest, all_skills: list[Skill]) -> list[ScoredSkill]:
        candidates = []

        for skill in all_skills:
            score = 0.0

            # 1. Language match (from repo metadata)
            repo_languages = self._get_repo_languages(task.repo_owner, task.repo_name)
            if skill.languages & repo_languages:
                score += 0.3

            # 2. Keyword match (task text vs skill tags/description)
            keyword_hits = self._keyword_overlap(task.task, skill.tags + [skill.description])
            score += min(keyword_hits * 0.1, 0.3)

            # 3. File pattern match (if task mentions specific files)
            if self._file_pattern_match(task.task, skill.frameworks):
                score += 0.2

            # 4. Historical success (skills that worked for similar repos)
            if skill.success_rate > 0.8 and skill.usage_count > 10:
                score += 0.1

            # 5. Embedding similarity (fast approximate, not LLM)
            if self._embedding_similarity(task.task, skill.embedding) > 0.7:
                score += 0.3

            if score > 0.2:  # Minimum threshold
                candidates.append(ScoredSkill(skill=skill, score=score))

        return sorted(candidates, key=lambda s: s.score, reverse=True)[:10]
```

### 4.2 Phase 2: LLM Refinement (< 2s, ~500 tokens)

Takes the top candidates from Phase 1 and uses a cheap, fast LLM call to make the final selection.

```python
class LLMSkillRefiner:
    """Phase 2: LLM picks the best skill(s) from pre-filtered candidates."""

    SYSTEM_PROMPT = """You are a task classifier for a coding agent platform.
    Given a coding task and a list of candidate skills, select the best skill(s).

    Rules:
    - Select 0-3 skills (0 if none are relevant)
    - Order by relevance
    - Consider the task requirements AND the skill's declared dependencies

    Respond with JSON: {"selected": ["skill-slug-1", "skill-slug-2"], "agent_type": "general|browser", "reasoning": "brief explanation"}"""

    async def refine(
        self,
        task: TaskRequest,
        candidates: list[ScoredSkill]
    ) -> ClassificationResult:
        prompt = self._build_prompt(task, candidates)

        # Use Haiku for speed and cost (this is a simple classification)
        response = await self._llm.complete(
            model="claude-haiku",
            system=self.SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=200,
        )

        result = self._parse_response(response)

        # Resolve dependencies: merge required_tools from all selected skills
        required_tools = set()
        agent_type = "general"
        for slug in result.selected_skills:
            skill = self._skill_map[slug]
            required_tools.update(skill.required_tools)
            if skill.required_agent == "browser":
                agent_type = "browser"

        return ClassificationResult(
            selected_skills=result.selected_skills,
            agent_type=agent_type,
            required_tools=list(required_tools),
            reasoning=result.reasoning,
        )
```

### 4.3 Classification Flow

```
Task arrives
    │
    ▼
Phase 1: Rule-based pre-filter (< 10ms)
    │ ~100 skills → ~5-10 candidates
    ▼
Phase 2: LLM refinement (< 2s)
    │ ~5-10 candidates → 0-3 selected skills
    ▼
Dependency resolution
    │ Merge required_tools, determine agent_type
    ▼
Gateway scope creation
    │ Write allowed_tools to Redis
    ▼
Agent spawn with:
    ├─ Selected skills injected into system prompt
    ├─ mcp.json pointing to gateway (if tools needed)
    └─ Correct agent type image
```

---

## 5. Modified Orchestrator Flow

### 5.1 Changes to `_spawn_job()`

The current `_spawn_job()` method builds a system prompt and spawns a K8s Job with a fixed image. The modified version adds classification, skill injection, gateway scope setup, and agent type selection.

```python
async def _spawn_job(
    self,
    thread: Thread,
    task_request: TaskRequest,
    is_retry: bool = False,
    retry_count: int = 0,
) -> None:
    thread_id = thread.id

    # --- NEW: Two-phase classification ---
    classification = await self._classifier.classify(task_request)
    # Returns: selected_skills, agent_type, required_tools, reasoning

    # --- NEW: Resolve agent image ---
    agent_image = self._resolve_agent_image(classification.agent_type)
    # "general" → ditto-factory-agent:latest
    # "browser" → ditto-factory-agent-browser:latest

    # --- NEW: Set up gateway scope (if tools required) ---
    session_token = None
    if classification.required_tools:
        session_id = f"{thread_id[:16]}-{int(time.time())}"
        session_token = self._create_session_token(session_id, thread_id)

        await self._redis.set_gateway_scope(session_id, {
            "thread_id": thread_id,
            "allowed_tools": classification.required_tools,
            "tool_config": await self._resolve_tool_configs(
                classification.required_tools, task_request
            ),
            "expires_at": (
                datetime.now(timezone.utc) +
                timedelta(seconds=self._settings.max_job_duration_seconds)
            ).isoformat(),
        })

    # --- NEW: Fetch and inject skills into prompt ---
    skill_contents = []
    for slug in classification.selected_skills:
        skill = await self._skill_registry.get_skill(slug)
        if skill:
            skill_contents.append(skill.content)

    # PREPARE: Build system prompt (modified to accept skills)
    system_prompt = build_system_prompt(
        repo_owner=thread.repo_owner,
        repo_name=thread.repo_name,
        task=task_request.task,
        claude_md=claude_md,
        skills=skill_contents,           # NEW
        conversation=conversation_strs,
        is_retry=is_retry,
    )

    # Push task to Redis (unchanged)
    await self._redis.push_task(thread_id, { ... })

    # SPAWN: Create K8s Job (modified)
    job_name = self._spawner.spawn(
        thread_id=thread_id,
        github_token="",
        redis_url=self._settings.redis_url,
        agent_image=agent_image,              # NEW: dynamic image
        session_token=session_token,           # NEW: gateway auth
        gateway_url=self._settings.mcp_gateway_url if session_token else None,  # NEW
    )

    # Track job with classification metadata
    job = Job(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        k8s_job_name=job_name,
        status=JobStatus.RUNNING,
        task_context={
            "task": task_request.task,
            "branch": branch,
            "classification": {                # NEW: track what was selected
                "skills": classification.selected_skills,
                "agent_type": classification.agent_type,
                "tools": classification.required_tools,
                "reasoning": classification.reasoning,
            },
        },
        started_at=datetime.now(timezone.utc),
    )
    await self._state.create_job(job)
```

### 5.2 Changes to `JobSpawner.build_job_spec()`

```python
def build_job_spec(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_image: str | None = None,        # NEW
    session_token: str | None = None,       # NEW
    gateway_url: str | None = None,         # NEW
) -> k8s.V1Job:

    image = agent_image or self._settings.agent_image

    env_vars = [
        k8s.V1EnvVar(name="THREAD_ID", value=thread_id),
        k8s.V1EnvVar(name="REDIS_URL", value=redis_url),
        k8s.V1EnvVar(name="GITHUB_TOKEN", value=github_token),
        k8s.V1EnvVar(name="ANTHROPIC_API_KEY", value_from=...),
    ]

    # NEW: Inject gateway credentials
    if session_token and gateway_url:
        env_vars.extend([
            k8s.V1EnvVar(name="MCP_SESSION_TOKEN", value=session_token),
            k8s.V1EnvVar(name="MCP_GATEWAY_URL", value=gateway_url),
        ])

    container = k8s.V1Container(
        name="agent",
        image=image,  # Dynamic image selection
        env=env_vars,
        ...
    )
```

### 5.3 Changes to `build_system_prompt()`

```python
def build_system_prompt(
    repo_owner: str,
    repo_name: str,
    task: str,
    claude_md: str = "",
    skills: list[str] | None = None,       # NEW
    conversation: list[str] | None = None,
    is_retry: bool = False,
) -> str:
    sections = []

    # ... existing sections ...

    # NEW: Inject selected skills
    if skills:
        for i, skill_content in enumerate(skills):
            sections.append(f"# Skill {i+1}\n{skill_content}")

    # ... rest of existing logic ...
```

---

## 6. Agent Type Matrix (Reduced)

### Before (without gateway): N agent types needed

| Agent Type | Installed Tools | Use Case |
|-----------|----------------|----------|
| general | git, node, python | Basic coding |
| database | + psql, mysql | DB work |
| browser | + playwright, chromium | UI testing |
| infra | + terraform, kubectl | Infrastructure |
| data-science | + jupyter, pandas, scipy | ML/data |
| ... | ... | ... |

### After (with gateway): 2-3 agent types

| Agent Type | Installed Locally | Remote via Gateway | Use Case |
|-----------|-------------------|-------------------|----------|
| **general** | git, node, python, claude-code | postgres, mysql, terraform, data tools | 90% of tasks |
| **browser** | + playwright, chromium | Same as general | UI testing, screenshots |
| **gpu** (future) | + CUDA toolkit | Same as general | ML training |

**Why browser stays local**: Playwright needs a real browser engine with GPU rendering. The latency of proxying every DOM interaction through a network hop makes it impractical. Screenshot/rendering must be co-located with the agent.

**Why general absorbs most tools**: Database queries, API calls, file analysis, linting — these are all request/response patterns that work well over MCP's SSE transport. Latency is < 100ms per tool call, which is negligible compared to LLM inference time.

---

## 7. Security Model

### 7.1 Threat Model

| Threat | Mitigation |
|--------|------------|
| Agent calls unauthorized tool | Allowlist in Redis, gateway enforces |
| Agent accesses another agent's data | Session tokens are per-agent, tool configs (DB creds) are per-session |
| Prompt injection causes tool abuse | Tool-level rate limits, read-only DB connections by default |
| Gateway compromise | Gateway runs with minimal privileges, no direct internet access, audit logging |
| Token theft from agent pod | Short-lived JWTs (match job TTL), network policy restricts who can reach gateway |
| Malicious skill injection | Skills are reviewed before registration, content sanitized with `sanitize_untrusted()` |

### 7.2 Defense in Depth

```
Layer 1: K8s NetworkPolicy
    Only agent pods → gateway
    Only controller → gateway admin API

Layer 2: JWT Authentication
    Controller signs, gateway verifies
    Short-lived (matches job TTL)

Layer 3: Redis Session Scope
    Explicit tool allowlist per agent
    Tool-specific configs (read-only DB, row limits)
    Auto-expires with job

Layer 4: Tool-Level Guards
    postgres_query: read-only connection, max 1000 rows, 30s timeout
    file_analyze: sandboxed, no write access
    Each tool validates its own inputs

Layer 5: Audit Trail
    Every tool call: session_id, thread_id, tool, args, result_size, latency
    Stored in Postgres for post-incident analysis
```

---

## 8. Component Effort Estimates

| Component | Effort | Dependencies | Risk |
|-----------|--------|-------------|------|
| MCP Gateway (core router + SSE) | **L** (3-4 weeks) | MCP SDK, FastAPI/Express | High — new infra component |
| Gateway tool backends (3-5 tools) | **M** (2 weeks) | Per tool: SDK/client library | Medium — each tool is independent |
| Skill Registry (DB + API) | **M** (2 weeks) | Postgres, pgvector | Low — standard CRUD + embeddings |
| Two-Phase Classifier | **M** (2 weeks) | Skill registry, Haiku API | Medium — tuning the pre-filter |
| Orchestrator modifications | **S** (1 week) | All above components | Low — well-understood code |
| Spawner modifications | **S** (3 days) | Orchestrator changes | Low — straightforward |
| Prompt builder modifications | **S** (2 days) | Skill registry | Low — additive change |
| Agent mcp.json templating | **S** (3 days) | Gateway deployment | Low |
| Security (JWT, NetworkPolicy) | **M** (1 week) | Gateway, K8s | Medium — must get right |
| Browser agent type image | **S** (3 days) | Playwright | Low |
| Monitoring + observability | **M** (1 week) | Gateway, Prometheus | Low |
| **Total** | **~10-12 weeks** | | |

---

## 9. Trade-Off Analysis

### 9.1 What Approach C Gets Right

| Benefit | Explanation |
|---------|-------------|
| **Minimal agent types** | 2-3 images instead of N. Dramatically reduces CI/CD and image management burden. |
| **Per-task tool scoping** | Each agent only sees the tools it needs. Better security than fat images with everything installed. |
| **Centralized tool management** | Update a tool once in the gateway, all agents get it. No image rebuilds. |
| **Semantic skill matching** | Two-phase classification is both fast (rule-based) and accurate (LLM). |
| **Skill-as-Code dependencies** | Skills declare what they need, registry validates at registration time. No runtime surprises. |
| **Reversible** | Gateway is additive — agents without gateway config work exactly as they do today. |

### 9.2 What Approach C Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Gateway is a SPOF** | All tool-using agents fail if gateway is down | Run 2+ replicas, health checks, circuit breaker in agent |
| **Latency overhead** | Every tool call adds ~50-100ms network hop | Acceptable for DB/API calls; unacceptable for high-frequency tools (hence browser stays local) |
| **Operational complexity** | New component to deploy, monitor, scale | Offset by fewer agent images to manage |
| **MCP SSE transport maturity** | SSE transport is newer in MCP spec | Fallback to streamable-HTTP; MCP SDK handles reconnection |
| **Cold start for gateway tools** | First tool call may be slow (DB connection pool, etc.) | Pre-warm connections, keep-alive |
| **Classification accuracy** | Wrong skill selection degrades agent performance | Feedback loop: track success_rate per skill, retrain pre-filter weights |
| **Skill budget contention** | Multiple skills may exceed Claude Code's 16K char budget | Classifier respects budget, truncates/prioritizes |

### 9.3 What's Complex (Honestly)

1. **MCP Gateway implementation** — This is a non-trivial piece of infrastructure. It needs to handle concurrent SSE connections, proxy MCP protocol messages, manage tool lifecycle, and enforce security. Estimate 3-4 weeks for a production-quality implementation.

2. **Tool backend authoring** — Each remote tool needs an MCP-compatible wrapper. Some tools (Postgres) have community MCP servers. Others need custom work.

3. **Session lifecycle management** — Gateway scopes must be created before the agent starts and cleaned up after the job ends. Edge cases: job crashes, gateway restarts mid-session, Redis eviction.

---

## 10. Comparison: Approach C vs A vs B

| Dimension | A: Skill Registry Only | B: Agent Types + Skills | C: Hybrid + Gateway |
|-----------|----------------------|------------------------|-------------------|
| Agent images | 1 (fat) | 5-10+ | 2-3 |
| Per-task tool scoping | No | By image selection | By gateway scope |
| New infrastructure | Skill registry | Skill registry + image pipeline | Skill registry + MCP gateway |
| Operational burden | Low | High (many images) | Medium (gateway + few images) |
| Tool update speed | Image rebuild | Image rebuild | Gateway redeploy (minutes) |
| Security granularity | All-or-nothing | Per-image | Per-tool per-task |
| Implementation effort | ~4 weeks | ~6-8 weeks | ~10-12 weeks |
| Best for team size | 1-3 engineers | 3-5 engineers | 3+ engineers |

### Verdict

Approach C is worth the added complexity **if and only if**:

1. You expect **> 5 distinct tool categories** that agents need access to (DB, browser, infra, data, etc.)
2. You need **per-task security scoping** (e.g., agent handling user data should only access read-only DB)
3. You want **fast tool iteration** without rebuilding agent images
4. You have the team capacity to own a new infrastructure component (the gateway)

If you only need 2-3 tool categories and security scoping isn't critical, **Approach B is simpler and sufficient**. Start with B, add the gateway when the agent type matrix becomes painful.

### Recommended Phasing

```
Phase 1 (Weeks 1-4):  Skill Registry + Two-Phase Classifier
                       → Immediate value, no new infra
                       → This is basically Approach A

Phase 2 (Weeks 5-8):  MCP Gateway (core) + 2 tool backends
                       → Prove the gateway pattern works
                       → Start with postgres + file-analysis tools

Phase 3 (Weeks 9-12): Production hardening
                       → Security audit, monitoring, HA
                       → Add more tool backends as needed
                       → Browser agent type if required
```

This phasing means you get value from Phase 1 alone (it's Approach A), and Phase 2-3 are incremental. You can stop after Phase 1 if the gateway proves unnecessary.

---

## Appendix A: MCP Gateway Sequence Diagram

```
Controller                Gateway              Redis           Tool Backend
    │                        │                    │                  │
    │  Create scope          │                    │                  │
    ├───────────────────────────────────────────►│                  │
    │                        │     SET scope      │                  │
    │                        │                    │                  │
    │  Spawn agent           │                    │                  │
    ├─── (K8s Job) ──►       │                    │                  │
    │                        │                    │                  │
    │         Agent connects via SSE              │                  │
    │              ┌─────────┤                    │                  │
    │              │ GET /sse?session_id=XXX      │                  │
    │              │         ├────────────────────►│                  │
    │              │         │    GET scope        │                  │
    │              │         │◄────────────────────│                  │
    │              │         │                    │                  │
    │              │ tools/list (filtered)        │                  │
    │              │◄────────┤                    │                  │
    │              │         │                    │                  │
    │              │ tools/call postgres_query    │                  │
    │              ├─────────►                    │                  │
    │              │         │  Validate scope    │                  │
    │              │         ├─────────────────────────────────────►│
    │              │         │                    │   Execute query  │
    │              │         │◄─────────────────────────────────────│
    │              │  Result │                    │                  │
    │              │◄────────┤                    │                  │
    │              │         │                    │                  │
    │  Job completes         │                    │                  │
    │              │         │    DEL scope        │                  │
    │  Cleanup ──────────────────────────────────►│                  │
    │                        │                    │                  │
```

## Appendix B: Skill Registration Example

```bash
# Register a skill via CLI
curl -X POST https://ditto-factory.internal/api/skills \
  -H "Authorization: Bearer $DF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "FastAPI Expert",
    "slug": "fastapi-expert",
    "description": "Expert at building and modifying FastAPI applications",
    "languages": ["python"],
    "frameworks": ["fastapi", "pydantic", "sqlalchemy"],
    "tags": ["api", "rest", "web", "backend"],
    "required_tools": ["postgres_query"],
    "required_agent": "general",
    "min_context_chars": 4000,
    "content": "# FastAPI Development Expert\n\nYou are an expert at building FastAPI applications...\n\n## Rules\n- Always use Pydantic v2 model_validator...\n- Use async def for all endpoints...\n"
  }'
```

## Appendix C: New Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DF_MCP_GATEWAY_URL` | `""` | MCP Gateway internal URL (empty = disabled) |
| `DF_MCP_GATEWAY_JWT_SECRET` | *(required if gateway)* | Secret for signing session JWTs |
| `DF_SKILL_REGISTRY_ENABLED` | `false` | Enable skill registry + classification |
| `DF_CLASSIFIER_MODEL` | `claude-haiku` | Model for Phase 2 classification |
| `DF_CLASSIFIER_MAX_CANDIDATES` | `10` | Max skills passed to Phase 2 |
| `DF_SKILL_MAX_TOTAL_CHARS` | `12000` | Max total chars for injected skills (under 16K budget) |
| `DF_AGENT_IMAGE_BROWSER` | `ditto-factory-agent-browser:latest` | Browser-capable agent image |
