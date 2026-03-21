# Skill Hotloading System -- Design Specification

**Status**: Proposed
**Date**: 2026-03-21
**Authors**: Ted Ahn, Software Architect Agent
**Approach**: Approach A (primary) + cherry-picks from B (subagent spawning) and C (MCP gateway)

---

## 1. Executive Summary

Ditto Factory currently runs every task on a single Docker image with a hardcoded toolchain and no mechanism to vary agent capabilities based on the task at hand. A CSS review task gets the same environment as a database migration task.

This spec describes a **Skill Hotloading System** that enables per-task capability injection through a three-layer model:

1. **Agent Types** (Docker images) -- coarse-grained capability boundaries (e.g., `general`, `frontend`, `backend`)
2. **Skills** (injected per-task) -- fine-grained instructions written as SKILL.md files, selected by the controller and injected via Redis before the agent starts
3. **Subagents** (future) -- controller-mediated child agents that a running agent can spawn for specialized subtasks

The key architectural decision is **controller-side selection + injection**: the controller classifies each task, selects relevant skills from a registry, resolves the appropriate agent type, and injects everything into the Redis payload before the agent pod starts. The agent receives pre-selected skills and does not discover or fetch them itself.

This approach keeps agents stateless, enables centralized performance tracking, and avoids adding new infrastructure (MCP servers, init containers) in the MVP phase.

---

## 2. Requirements

### 2.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Per-task skill injection: select 0-10 skills based on task description and inject as SKILL.md files | P0 |
| FR-2 | Skill CRUD API: create, read, update, delete, version, and rollback skills | P0 |
| FR-3 | Tag-based skill matching: match skills by language, domain, and explicit tags | P0 |
| FR-4 | Semantic search: embed task descriptions and skill content, use vector similarity for matching | P1 |
| FR-5 | Skill versioning: every update creates a new version; rollback to any previous version | P0 |
| FR-6 | Agent type resolution: determine Docker image from skill requirements (e.g., skills requiring `browser` resolve to `frontend` image) | P1 |
| FR-7 | Performance tracking: record which skills were injected per task and correlate with outcomes | P1 |
| FR-8 | Default skills: some skills are always injected regardless of classification | P0 |
| FR-9 | Skill search API: search skills by natural language query with similarity scores | P1 |
| FR-10 | Subagent spawning: running agents can request specialized child agents via MCP tool (future) | P2 |

### 2.2 Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1 | Classification latency (tag-based) | < 50ms |
| NFR-2 | Classification latency (semantic search) | < 500ms |
| NFR-3 | Graceful degradation | If embedding service is unavailable, fall back to tag-based matching |
| NFR-4 | Redis payload size | < 500KB per task (10 skills at 5KB each = 50KB, well within limits) |
| NFR-5 | Incremental rollout | Feature-flagged; disabled by default; no impact on existing tasks when off |
| NFR-6 | Skill budget | Total injected skill content < 16K characters (Claude Code's effective limit) |
| NFR-7 | Zero downtime | Skill registry changes do not require controller restart |

---

## 3. Architecture

### 3.1 Three-Layer Capability Model

```
+------------------------------------------------------------------+
|                        LAYER 3: SUBAGENTS (Future)                |
|  Running agent spawns child agents via message-queue MCP tool     |
|  Controller mediates: classifies, spawns, tracks, returns result  |
+------------------------------------------------------------------+
        |
+------------------------------------------------------------------+
|                     LAYER 2: SKILLS (This Spec)                   |
|  Per-task SKILL.md files injected via Redis payload               |
|  Selected by controller using tag-based or semantic matching      |
|  Written to .claude/skills/ by entrypoint.sh before claude starts |
+------------------------------------------------------------------+
        |
+------------------------------------------------------------------+
|                    LAYER 1: AGENT TYPES (This Spec)               |
|  Docker images with pre-installed toolchains                      |
|  general | frontend (+ Playwright) | backend (+ DB clients)      |
|  Resolved from skill requirements (e.g., requires: ["browser"])   |
+------------------------------------------------------------------+
```

### 3.2 System Architecture

```
                         +-----------------+
                         |  Webhook Source  |
                         | (Slack/GitHub/   |
                         |  Linear)         |
                         +--------+--------+
                                  |
                                  v
+-----------------------------Controller----------------------------------+
|                                                                         |
|  +-------------+    +----------------+    +-----------------+           |
|  | Orchestrator |--->| Task Classifier |--->| Skill Registry  |          |
|  | (_spawn_job) |    | (classify)     |    | (CRUD + search) |          |
|  +------+------+    +-------+--------+    +---------+-------+          |
|         |                   |                       |                   |
|         |            +------v--------+    +---------v-------+          |
|         |            | Agent Type    |    | Skill Injector   |          |
|         |            | Resolver      |    | (Redis payload)  |          |
|         |            +------+--------+    +---------+-------+          |
|         |                   |                       |                   |
|         |            +------v--------+              |                   |
|         |            | Performance   |              |                   |
|         |            | Tracker       |              |                   |
|         |            +---------------+              |                   |
|         |                                           |                   |
|         +-------------------------------------------+                   |
|                         |                                               |
+-----------+-------------+-----------------------------------------------+
            |             |
            v             v
      +-----------+  +---------+
      | Redis     |  | K8s API |
      | (task     |  | (spawn  |
      |  payload) |  |  job)   |
      +-----------+  +----+----+
                          |
                          v
                    +-----+------+
                    | Agent Pod  |
                    | entrypoint |
                    | reads task |
                    | from Redis |
                    | writes     |
                    | skills to  |
                    | .claude/   |
                    | skills/    |
                    +------------+
```

### 3.3 Data Flow (Sequence)

```
Slack/GitHub/Linear          Controller                  Postgres            Redis           K8s
       |                        |                           |                  |               |
       |  webhook               |                           |                  |               |
       +----------------------->|                           |                  |               |
       |                        |                           |                  |               |
       |                        |  1. create thread/task    |                  |               |
       |                        |-------------------------->|                  |               |
       |                        |                           |                  |               |
       |                        |  2. classify task         |                  |               |
       |                        |     (generate embedding,  |                  |               |
       |                        |      pgvector search)     |                  |               |
       |                        |-------------------------->|                  |               |
       |                        |<----- top 5 skills -------|                  |               |
       |                        |     + agent_type          |                  |               |
       |                        |                           |                  |               |
       |                        |  3. resolve agent_type    |                  |               |
       |                        |     to Docker image       |                  |               |
       |                        |     (orchestrator calls   |                  |               |
       |                        |      AgentTypeResolver)   |                  |               |
       |                        |                           |                  |               |
       |                        |  4. build system prompt   |                  |               |
       |                        |     + skill content       |                  |               |
       |                        |                           |                  |               |
       |                        |  5. push task + skills    |                  |               |
       |                        |     to Redis              |                  |               |
       |                        |------------------------------------------>|               |
       |                        |                           |                  |               |
       |                        |  6. spawn K8s job         |                  |               |
       |                        |     (agent_image from     |                  |               |
       |                        |      resolver)            |                  |               |
       |                        |--------------------------------------------------------------->|
       |                        |                           |                  |               |
       |                        |  7. record injection      |                  |               |
       |                        |     (skill_usage)         |                  |               |
       |                        |-------------------------->|                  |               |
       |                        |                           |                  |               |
       |                        |                           |          Agent Pod Starts        |
       |                        |                           |                  |<--------------|
       |                        |                           |                  |               |
       |                        |                           |  8. fetch task   |               |
       |                        |                           |     + skills     |               |
       |                        |                           |  (entrypoint.sh) |               |
       |                        |                           |                  |               |
       |                        |                           |  9. write skills |               |
       |                        |                           |     to .claude/  |               |
       |                        |                           |     skills/*.md  |               |
       |                        |                           |                  |               |
       |                        |                           |  10. run claude  |               |
       |                        |                           |      with skills |               |
       |                        |                           |      loaded      |               |
       |                        |                           |                  |               |
       |                        |  11. job completes        |                  |               |
       |                        |<---------------------------------------------------------------|
       |                        |                           |                  |               |
       |                        |  12. record outcome       |                  |               |
       |                        |      (performance tracker)|                  |               |
       |                        |-------------------------->|                  |               |
```

---

## 4. Detailed Design

### 4.1 Module Structure

The skill hotloading system is deployed as a set of Python modules within the controller (modular monolith). Clean module boundaries make future extraction to a separate service straightforward if needed.

```
controller/
  src/controller/
    skills/
      __init__.py
      registry.py        # SkillRegistry class (CRUD + search)
      classifier.py      # TaskClassifier (embedding generation + matching)
      injector.py        # SkillInjector (formats skills for Redis payload)
      resolver.py        # AgentTypeResolver (skill tags -> Docker image)
      tracker.py         # PerformanceTracker (post-task metrics)
      models.py          # Pydantic models for Skill, SkillVersion, etc.
```

### 4.2 Skill Registry

**Responsibility**: Store, version, search, and serve skills.

The registry is Postgres-backed using `pgvector` for embedding storage and similarity search. It lives within the controller process to avoid network hops.

```python
class SkillRegistry:
    """CRUD and search operations for the skill registry."""

    async def create(self, skill: SkillCreate) -> Skill:
        """Create a new skill with initial version. Generates embedding."""

    async def update(self, slug: str, update: SkillUpdate) -> Skill:
        """Update skill content. Creates a new version, re-generates embedding."""

    async def get(self, slug: str) -> Skill | None:
        """Get skill by slug. Returns active version."""

    async def list(self, filters: SkillFilters) -> list[Skill]:
        """List skills with optional filters (language, domain, org)."""

    async def search_by_tags(
        self, language: list[str], domain: list[str], limit: int = 10
    ) -> list[Skill]:
        """Tag-based matching. Used as fallback when embeddings unavailable."""

    async def search_by_embedding(
        self, task_embedding: list[float], filters: SkillFilters, limit: int = 20
    ) -> list[ScoredSkill]:
        """Vector similarity search using pgvector cosine distance."""

    async def delete(self, slug: str) -> None:
        """Soft-delete: set is_active=false."""

    async def rollback(self, slug: str, version: int) -> Skill:
        """Restore a previous version as the current active content."""

    async def get_defaults(self) -> list[Skill]:
        """Return all skills marked is_default=true."""
```

### 4.3 Task Classifier

**Responsibility**: Given a `TaskRequest`, produce a ranked list of relevant skills.

**Algorithm**:
1. Generate an embedding for `task_request.task` using Voyage-3
2. Query pgvector for top-K similar skills (cosine similarity, K=20)
3. Apply hard filters: repo language, required agent type, org restrictions
4. Apply soft ranking boost from performance metrics (success rate, usage frequency)
5. Merge with default skills (always included)
6. Return top N skills (configurable, default 5, max 10)
7. Enforce total content budget (< 16K characters)

**Fallback**: If embedding service is unavailable, fall back to tag-based matching (exact match on `language` and `domain` tags from TaskRequest metadata). The system degrades gracefully rather than failing.

```python
class TaskClassifier:
    """Classify tasks and select relevant skills."""

    def __init__(
        self,
        registry: SkillRegistry,
        embedding_provider: EmbeddingProvider,
        settings: Settings,
    ):
        self._registry = registry
        self._embedder = embedding_provider
        self._settings = settings

    async def classify(
        self,
        task: str,
        language: list[str] | None = None,
        domain: list[str] | None = None,
    ) -> ClassificationResult:
        """
        Classify a task and return matched skills + resolved agent type.

        Falls back to tag-based matching if embedding generation fails.
        Enforces skill budget (max chars) and count limits.
        """
        try:
            embedding = await self._embedder.embed(task)
            candidates = await self._registry.search_by_embedding(
                task_embedding=embedding,
                filters=SkillFilters(language=language, domain=domain),
                limit=20,
            )
        except EmbeddingError:
            logger.warning("Embedding service unavailable, falling back to tags")
            candidates = await self._registry.search_by_tags(
                language=language or [],
                domain=domain or [],
                limit=self._settings.skill_max_per_task,
            )

        # Apply performance boost
        ranked = self._apply_performance_boost(candidates)

        # Merge defaults
        defaults = await self._registry.get_defaults()
        selected = self._merge_and_budget(ranked, defaults)

        # Resolve agent type
        agent_type = self._resolve_agent_type(selected)

        return ClassificationResult(
            skills=selected,
            agent_type=agent_type,
            task_embedding=embedding if embedding else None,
        )
```

**Embedding provider**: Voyage-3 via API. Rationale: Ditto Factory already depends on Anthropic; Voyage is Anthropic's recommended embedding provider with strong code understanding. Adding OpenAI as a dependency for ada-002 creates a second vendor relationship with no meaningful benefit.

### 4.4 Skill Injector

**Responsibility**: Format matched skills for inclusion in the Redis task payload.

**Mechanism**: Skills are included as a JSON array in the `task:{thread_id}` Redis value. The entrypoint script reads this array and writes each skill as a `.md` file in `.claude/skills/` before invoking Claude Code.

**Why Redis payload over init container**: Skill files are typically 1-5KB each; 10 skills = 10-50KB, well within Redis's 512MB value limit. An init container would add 2-5s latency and K8s complexity for no benefit at this scale. We migrate to init containers only if skill payloads grow large (e.g., skills with embedded code templates exceeding 100KB total).

```python
class SkillInjector:
    """Format skills for Redis payload injection."""

    def format_for_redis(self, skills: list[Skill]) -> list[dict]:
        """
        Convert skills to the Redis payload format.

        Returns:
            [{"name": "debug-react", "content": "# Debug React Components\n..."}]
        """
        return [
            {"name": skill.slug, "content": skill.content}
            for skill in skills
        ]

    def validate_budget(self, skills: list[Skill], max_chars: int = 16000) -> list[Skill]:
        """
        Enforce total character budget. Drops lowest-priority skills
        if total content exceeds the budget.
        """
        total = 0
        accepted = []
        for skill in skills:
            if total + len(skill.content) > max_chars:
                logger.warning(
                    "Skill budget exceeded, dropping %s (%d chars)",
                    skill.slug, len(skill.content)
                )
                continue
            total += len(skill.content)
            accepted.append(skill)
        return accepted
```

### 4.5 Agent Type Resolver

**Responsibility**: Given a set of matched skills, determine which Docker image to use for the agent pod.

**Algorithm**:
1. Collect all `requires` tags from matched skills (e.g., `["browser"]`, `["python-3.12"]`)
2. Look up agent types whose `capabilities` superset the required tags
3. If multiple matches, prefer the one with the fewest extra capabilities (least-privilege)
4. If no match, fall back to default agent type

```python
class AgentTypeResolver:
    """Resolve skill requirements to a Docker image."""

    async def resolve(
        self,
        skills: list[Skill],
        default_image: str,
    ) -> ResolvedAgent:
        """
        Determine the agent type (Docker image) from skill requirements.

        Returns the default image if no special capabilities are required.
        """
        required_caps = set()
        for skill in skills:
            required_caps.update(skill.requires or [])

        if not required_caps:
            return ResolvedAgent(image=default_image, agent_type="general")

        # Query agent_types table for best match
        agent_type = await self._find_best_match(required_caps)
        if agent_type is None:
            logger.warning(
                "No agent type satisfies requirements %s, using default",
                required_caps,
            )
            return ResolvedAgent(image=default_image, agent_type="general")

        return ResolvedAgent(
            image=agent_type.image,
            agent_type=agent_type.name,
        )
```

### 4.6 Performance Tracker

**Responsibility**: After task completion, record which skills were injected and whether the task succeeded. This data feeds back into the classifier's ranking to boost high-performing skills.

```python
class PerformanceTracker:
    """Track skill injection outcomes for feedback loop."""

    async def record_injection(
        self,
        skills: list[Skill],
        thread_id: str,
        job_id: str,
        task_request: TaskRequest,
        task_embedding: list[float] | None = None,
    ) -> None:
        """Record that skills were injected for a task (at spawn time)."""

    async def record_outcome(
        self,
        thread_id: str,
        job_id: str,
        result: AgentResult,
    ) -> None:
        """Record task outcome (at completion time). Updates skill_usage rows."""

    async def get_skill_metrics(self, skill_id: str) -> SkillMetrics:
        """
        Compute aggregate metrics for a skill:
        - usage_count, success_rate, avg_commits, pr_creation_rate
        - trend (last 7 days vs previous 7 days)
        """

    def compute_boost(self, skill_id: str, base_similarity: float) -> float:
        """
        Apply performance-based ranking boost.
        Skills with high success rates get up to 10% similarity boost.
        Skills with low success rates get up to 10% penalty.
        """
```

---

## 5. Integration Points

### 5.1 Changes to `orchestrator.py`

The `_spawn_job` method gains three new steps between prompt building and Redis push:

```python
# File: controller/src/controller/orchestrator.py
# Method: _spawn_job

async def _spawn_job(self, thread, task_request, is_retry=False, retry_count=0):
    # ... existing prompt building (unchanged) ...
    integration = self._registry.get(task_request.source)
    system_prompt = build_system_prompt(
        repo_owner=thread.repo_owner,
        repo_name=thread.repo_name,
        task=task_request.task,
        claude_md=claude_md,
        conversation=conversation_strs if conversation_strs else None,
        is_retry=is_retry,
    )

    # === NEW: Skill classification and injection ===

    # Step 1: Classify task and select skills
    if self._settings.skill_registry_enabled:
        try:
            classification = await self._classifier.classify(
                task=task_request.task,
                language=self._detect_language(thread),
                domain=task_request.source_ref.get("labels", []),
            )
            matched_skills = classification.skills
            # Step 1b: Resolve agent_type to a Docker image
            resolved = await self._resolver.resolve(
                skills=matched_skills,
                default_image=self._settings.agent_image,
            )
            agent_image = resolved.image
        except Exception:
            logger.exception(
                "Skill classification failed, falling back to no-skills behavior"
            )
            matched_skills = []
            agent_image = self._settings.agent_image
    else:
        matched_skills = []
        agent_image = self._settings.agent_image

    # Step 2: Format skills for Redis payload
    skills_payload = self._injector.format_for_redis(matched_skills)

    # === END NEW ===

    # Store conversation (unchanged)
    await self._state.append_conversation(thread_id, {
        "role": "user",
        "content": task_request.task,
        "source": task_request.source,
    })

    # Create branch name (unchanged)
    short_id = thread_id[:8]
    branch = f"df/{short_id}/{uuid.uuid4().hex[:8]}"

    # Push task to Redis (MODIFIED: includes skills)
    await self._redis.push_task(thread_id, {
        "task": task_request.task,
        "system_prompt": system_prompt,
        "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
        "branch": branch,
        "skills": skills_payload,                    # <-- NEW FIELD
    })

    # SPAWN: Create K8s Job (MODIFIED: pass agent_image)
    job_name = self._spawner.spawn(
        thread_id=thread_id,
        github_token="",
        redis_url=self._settings.redis_url,
        agent_image=agent_image,                     # <-- NEW PARAM
    )

    # Track job in state (unchanged)
    job = Job(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        k8s_job_name=job_name,
        status=JobStatus.RUNNING,
        task_context={"task": task_request.task, "branch": branch},
        started_at=datetime.now(timezone.utc),
    )
    await self._state.create_job(job)

    # === NEW: Record skill injection for tracking ===
    if matched_skills:
        await self._tracker.record_injection(
            skills=matched_skills,
            thread_id=thread_id,
            job_id=job.id,
            task_request=task_request,
            task_embedding=classification.task_embedding,
        )
    # === END NEW ===

    await self._state.update_thread(thread_id, ...)
```

**New constructor dependencies** (shown with full signature, consistent with existing DI pattern):
```python
def __init__(
    self,
    settings: Settings,
    state: StateBackend,
    redis: RedisClient,
    spawner: Spawner,
    registry: IntegrationRegistry,
    # NEW: Skill hotloading dependencies
    classifier: TaskClassifier,
    injector: SkillInjector,
    resolver: AgentTypeResolver,
    tracker: PerformanceTracker,
):
    # ... existing assignments ...
    self._classifier = classifier              # NEW
    self._injector = injector                  # NEW
    self._resolver = resolver                  # NEW
    self._tracker = tracker                    # NEW
```

**New helper method** (added to Orchestrator):
```python
def _detect_language(self, thread: Thread) -> list[str] | None:
    """
    Detect the primary language(s) for a thread's repository.

    Heuristic: Inspects thread metadata for language hints. Falls back
    to None (no language filter) if detection is not possible.
    This is a new method that must be implemented.
    """
    # Check thread metadata for repo language (set by webhook source)
    lang = thread.metadata.get("language") if thread.metadata else None
    if lang:
        return [lang.lower()] if isinstance(lang, str) else lang
    return None
```

### 5.2 Changes to `spawner.py`

The `build_job_spec` method accepts an optional `agent_image` parameter:

```python
# File: controller/src/controller/jobs/spawner.py

def build_job_spec(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_image: str | None = None,        # <-- NEW PARAM
) -> k8s.V1Job:
    image = agent_image or self._settings.agent_image  # <-- CHANGED

    # ... rest of method unchanged, but uses `image` variable
    # instead of `self._settings.agent_image`

def spawn(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_image: str | None = None,        # <-- NEW PARAM
) -> str:
    spec = self.build_job_spec(
        thread_id, github_token, redis_url,
        agent_image=agent_image,            # <-- PASS THROUGH
    )
    # ... rest unchanged
```

### 5.3 Changes to `entrypoint.sh`

After cloning the repo and before running Claude, inject skills from the Redis payload:

```bash
# File: images/agent/entrypoint.sh
# Insert AFTER: git clone ... "$WORKSPACE" && cd "$WORKSPACE"
# Insert BEFORE: claude "${CLAUDE_ARGS[@]}"

# === NEW: Inject skills from task payload ===
SKILLS_JSON=$(echo "$TASK_JSON" | jq -r '.skills // empty')
if [ -n "$SKILLS_JSON" ] && [ "$SKILLS_JSON" != "null" ]; then
    mkdir -p .claude/skills
    while read -r skill; do
        # Sanitize skill name: strip path separators and allow only safe chars
        SKILL_NAME=$(echo "$skill" | jq -r '.name' | tr -cd 'a-zA-Z0-9_-')
        if [ -z "$SKILL_NAME" ]; then
            echo "WARNING: Skipping skill with empty/invalid name"
            continue
        fi
        SKILL_CONTENT=$(echo "$skill" | jq -r '.content')
        echo "$SKILL_CONTENT" > ".claude/skills/${SKILL_NAME}.md"
    done < <(echo "$SKILLS_JSON" | jq -c '.[]')
    SKILL_COUNT=$(echo "$SKILLS_JSON" | jq length)
    echo "Injected ${SKILL_COUNT} skills into .claude/skills/"
else
    echo "No skills to inject"
fi
# === END NEW ===
```

### 5.4 Changes to `config.py`

New settings for the skill registry:

```python
# File: controller/src/controller/config.py

class Settings(BaseSettings):
    # ... existing fields ...

    # Skill Registry (NEW)
    skill_registry_enabled: bool = False           # Feature flag
    skill_embedding_provider: str = "voyage"       # "voyage" | "none" (tag-only)
    skill_embedding_model: str = "voyage-3"
    skill_max_per_task: int = 5                    # Max skills injected per task
    skill_min_similarity: float = 0.5              # Minimum cosine similarity threshold
    skill_max_total_chars: int = 16000             # Character budget for all skills
    voyage_api_key: str = ""                       # Voyage API key for embeddings

    model_config = {"env_prefix": "DF_"}
```

### 5.5 Changes to `models.py`

New fields on the Job dataclass:

```python
# File: controller/src/controller/models.py

@dataclass
class Job:
    id: str
    thread_id: str
    k8s_job_name: str
    status: JobStatus = JobStatus.PENDING
    task_context: dict = field(default_factory=dict)
    result: dict | None = None
    agent_type: str = "general"              # NEW: which agent type was used
    skills_injected: list[str] = field(default_factory=list)  # NEW: skill slugs
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

### 5.6 Changes to `handle_job_completion` (Performance Tracking)

After processing the agent result, record the outcome for performance tracking:

```python
# In orchestrator.py or safety.py, after result is processed:

if self._settings.skill_registry_enabled:
    await self._tracker.record_outcome(
        thread_id=thread_id,
        job_id=job.id,
        result=agent_result,
    )
```

---

## 6. Data Model

### 6.1 Full SQL Schema

```sql
-- Enable pgvector extension (requires Postgres 15+)
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Skills table: stores the current active version of each skill
-- ============================================================
CREATE TABLE skills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    slug            VARCHAR(128) NOT NULL UNIQUE,
    description     TEXT NOT NULL,
    content         TEXT NOT NULL,

    -- Classification metadata
    language        VARCHAR(32)[],                  -- e.g., ["python", "typescript"]
    domain          VARCHAR(64)[],                  -- e.g., ["testing", "frontend"]
    requires        VARCHAR(64)[],                  -- agent capabilities needed, e.g., ["browser"]
    tags            VARCHAR(64)[],                  -- freeform tags for search

    -- Embedding (1024-dim for Voyage-3)
    embedding       vector(1024),

    -- Scoping
    org_id          VARCHAR(128),                   -- NULL = global skill
    repo_pattern    VARCHAR(256),                   -- glob pattern, e.g., "myorg/frontend-*"

    -- Metadata
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      VARCHAR(128) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_default      BOOLEAN NOT NULL DEFAULT false,

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vector similarity search index
-- Using HNSW instead of IVFFlat: HNSW works well on small datasets and does not
-- require pre-existing rows for good recall (IVFFlat needs ~1000+ rows to tune lists).
CREATE INDEX idx_skills_embedding ON skills
    USING hnsw (embedding vector_cosine_ops);

-- Filter indexes
CREATE INDEX idx_skills_language ON skills USING gin (language);
CREATE INDEX idx_skills_domain ON skills USING gin (domain);
CREATE INDEX idx_skills_org ON skills (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_skills_active ON skills (is_active) WHERE is_active = true;
CREATE INDEX idx_skills_default ON skills (is_default) WHERE is_default = true;


-- ============================================================
-- Skill versions: full history of every skill revision
-- ============================================================
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


-- ============================================================
-- Skill usage: per-injection tracking for performance feedback
-- ============================================================
CREATE TABLE skill_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        UUID NOT NULL REFERENCES skills(id),
    thread_id       VARCHAR(128) NOT NULL,
    job_id          VARCHAR(128) NOT NULL,

    -- Context
    task_embedding  vector(1024),
    task_source     VARCHAR(32) NOT NULL,
    repo_owner      VARCHAR(128),
    repo_name       VARCHAR(128),

    -- Outcome (populated on completion)
    was_selected    BOOLEAN NOT NULL DEFAULT true,
    exit_code       INTEGER,
    commit_count    INTEGER,
    pr_created      BOOLEAN DEFAULT false,

    -- Timing
    injected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_usage_skill ON skill_usage (skill_id);
CREATE INDEX idx_usage_thread ON skill_usage (thread_id);
CREATE INDEX idx_usage_outcome ON skill_usage (skill_id, exit_code)
    WHERE completed_at IS NOT NULL;


-- ============================================================
-- Agent types: Docker images with capability declarations
-- ============================================================
CREATE TABLE agent_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    image           VARCHAR(256) NOT NULL,
    description     TEXT,
    capabilities    VARCHAR(64)[] NOT NULL,
    resource_profile JSONB NOT NULL DEFAULT '{}',
    is_default      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed default agent type
INSERT INTO agent_types (name, image, capabilities, is_default)
VALUES ('general', 'ditto-factory-agent:latest', '{}', true);


-- ============================================================
-- Add columns to the jobs table.
-- NOTE: The current codebase uses dataclass-based models (models.py) with a
-- pluggable state backend. If the state backend is Postgres-backed (i.e., the
-- jobs table already exists), apply these ALTER statements. If jobs are stored
-- differently (e.g., Redis-only), these columns should be added when the jobs
-- table is first created in Postgres. The CREATE TABLE for jobs is defined by
-- the state backend implementation, not this migration.
-- ============================================================
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS agent_type VARCHAR(128) DEFAULT 'general';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS skills_injected TEXT[] DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS classification_confidence REAL;
```

---

## 7. API Reference

### 7.1 Skill CRUD

#### Create Skill

```
POST /api/v1/skills

Request:
{
    "name": "Debug React Components",
    "slug": "debug-react",
    "description": "Systematic approach to debugging React rendering issues",
    "content": "# Debug React Components\n\nWhen debugging React...",
    "language": ["typescript", "javascript"],
    "domain": ["frontend", "debugging"],
    "requires": [],
    "tags": ["react", "debugging", "components"],
    "org_id": null,
    "is_default": false,
    "created_by": "ted@example.com"
}

Response: 201 Created
{
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "slug": "debug-react",
    "version": 1,
    "created_at": "2026-03-21T10:00:00Z"
}
```

#### List Skills

```
GET /api/v1/skills?language=typescript&domain=frontend&page=1&per_page=20

Response: 200 OK
{
    "skills": [ ... ],
    "total": 42,
    "page": 1,
    "per_page": 20
}
```

#### Get Skill

```
GET /api/v1/skills/{slug}

Response: 200 OK
{
    "id": "550e8400-...",
    "name": "Debug React Components",
    "slug": "debug-react",
    "description": "...",
    "content": "# Debug React Components\n...",
    "language": ["typescript", "javascript"],
    "domain": ["frontend", "debugging"],
    "requires": [],
    "tags": ["react", "debugging"],
    "version": 3,
    "is_active": true,
    "is_default": false,
    "created_at": "2026-03-21T10:00:00Z",
    "updated_at": "2026-03-22T14:30:00Z"
}
```

#### Update Skill

```
PUT /api/v1/skills/{slug}

Request:
{
    "content": "# Debug React Components (v2)\n\nUpdated approach...",
    "description": "Updated debugging approach with React 19 patterns",
    "changelog": "Added React 19 server component debugging steps",
    "updated_by": "ted@example.com"
}

Response: 200 OK
{
    "slug": "debug-react",
    "version": 4,
    "previous_version": 3
}
```

#### Delete Skill (Soft)

```
DELETE /api/v1/skills/{slug}

Response: 204 No Content
```

#### List Versions

```
GET /api/v1/skills/{slug}/versions

Response: 200 OK
{
    "versions": [
        {"version": 3, "changelog": "Added React 19 patterns", "created_at": "..."},
        {"version": 2, "changelog": "Fixed hook debugging section", "created_at": "..."},
        {"version": 1, "changelog": null, "created_at": "..."}
    ]
}
```

#### Rollback

```
POST /api/v1/skills/{slug}/rollback

Request:
{
    "target_version": 2
}

Response: 200 OK
{
    "slug": "debug-react",
    "version": 5,
    "restored_from": 2
}
```

### 7.2 Skill Search

```
POST /api/v1/skills/search

Request:
{
    "query": "fix react component rendering bug",
    "language": ["typescript", "javascript"],
    "domain": ["frontend"],
    "limit": 10,
    "min_similarity": 0.5
}

Response: 200 OK
{
    "skills": [
        {
            "slug": "debug-react",
            "name": "Debug React Components",
            "similarity": 0.87,
            "success_rate": 0.92,
            "usage_count": 156
        },
        {
            "slug": "typescript-testing",
            "name": "TypeScript Testing Patterns",
            "similarity": 0.71,
            "success_rate": 0.85,
            "usage_count": 89
        }
    ]
}
```

### 7.3 Internal: Task Classification

```
POST /api/v1/internal/classify

Request:
{
    "task_request": {
        "task": "Fix the broken login form validation on the signup page",
        "source": "github",
        "repo_owner": "myorg",
        "repo_name": "web-app"
    },
    "max_skills": 5
}

Response: 200 OK
{
    "skills": [
        {
            "slug": "debug-react",
            "name": "Debug React Components",
            "content": "# Debug React Components\n...",
            "similarity": 0.87
        }
    ],
    "agent_type": "general",
    "agent_image": "ditto-factory-agent:latest",
    "classification_confidence": 0.87
}
```

### 7.4 Metrics

```
GET /api/v1/skills/{slug}/metrics

Response: 200 OK
{
    "skill_slug": "debug-react",
    "usage_count": 156,
    "success_rate": 0.92,
    "avg_commits": 2.3,
    "pr_creation_rate": 0.78,
    "trend": {
        "last_7_days": {"usage": 12, "success_rate": 0.91},
        "previous_7_days": {"usage": 8, "success_rate": 0.87}
    }
}
```

```
POST /api/v1/skills/usage

Request:
{
    "thread_id": "abc12345...",
    "job_id": "def67890...",
    "skills": ["debug-react", "typescript-testing"],
    "exit_code": 0,
    "commit_count": 3,
    "pr_created": true
}

Response: 204 No Content
```

### 7.5 Agent Types

```
GET /api/v1/agent-types

Response: 200 OK
{
    "agent_types": [
        {
            "name": "general",
            "image": "ditto-factory-agent:latest",
            "capabilities": [],
            "is_default": true
        },
        {
            "name": "frontend",
            "image": "ditto-factory-agent-frontend:latest",
            "capabilities": ["browser", "playwright"],
            "is_default": false
        }
    ]
}
```

```
POST /api/v1/agent-types

Request:
{
    "name": "frontend",
    "image": "ditto-factory-agent-frontend:latest",
    "description": "Agent with browser automation capabilities",
    "capabilities": ["browser", "playwright"],
    "resource_profile": {
        "cpu_request": "1",
        "memory_request": "4Gi",
        "cpu_limit": "4",
        "memory_limit": "16Gi"
    }
}

Response: 201 Created
```

---

## 8. Phased Rollout

### Phase 1: MVP (Tag-Based Matching + Injection Pipeline) -- ~8 days

**Goal**: Ship skill injection end-to-end with manual skill curation and simple tag matching.

| Component | Effort | Details |
|-----------|--------|---------|
| Data model + migrations | 1-2 days | `skills`, `skill_versions`, `agent_types` tables (no pgvector yet) |
| Skill Registry (CRUD) | 2-3 days | `registry.py` with create/read/update/delete/list/rollback |
| Tag-based classifier | 1 day | Match by `language` + `domain` arrays, no embeddings |
| Skill Injector + entrypoint changes | 1-2 days | Redis payload format + bash injection script |
| Orchestrator integration | 1-2 days | Wire classifier + injector into `_spawn_job` |
| API endpoints | 1-2 days | Skill CRUD + search-by-tags endpoints |
| Feature flag | 0.5 days | `DF_SKILL_REGISTRY_ENABLED=false` default |

**What you get**: Skills can be created via API, matched by language/domain tags, and injected into agent pods. No ML, no embeddings, no external API dependencies. Pure Postgres.

**What you give up**: No semantic understanding of task-skill relevance. Matching is only as good as the tags you assign.

### Phase 2: Intelligence (Semantic Search + Agent Type Resolution) -- ~8 days

**Goal**: Replace tag-based matching with embedding-powered semantic search. Add agent type resolution.

| Component | Effort | Details |
|-----------|--------|---------|
| pgvector setup | 1 day | Enable extension, add embedding columns, create HNSW index |
| Embedding pipeline | 2-3 days | Voyage-3 integration, embed on skill create/update, embed task at classify time |
| Semantic classifier | 2-3 days | Vector similarity search with hard filters + soft ranking |
| Agent type resolver | 1 day | Resolve `requires` tags to Docker images |
| Spawner changes | 0.5 days | Accept `agent_image` parameter |
| Testing + tuning | 1-2 days | Similarity threshold tuning, fallback verification |

**Dependencies**: pgvector extension in Postgres 15+, Voyage API key.

### Phase 3: Learning (Performance Tracking + Feedback Loop) -- ~6 days

**Goal**: Track outcomes and use them to improve skill selection over time.

| Component | Effort | Details |
|-----------|--------|---------|
| `skill_usage` table + tracking | 1-2 days | Record injections at spawn, outcomes at completion |
| Performance boost algorithm | 1-2 days | Success rate weighting in classifier ranking |
| Metrics API | 1 day | Per-skill usage stats, trends, success rates |
| Dashboard/observability | 1-2 days | Grafana or custom metrics endpoints |

### Phase 4: Subagent Spawning (Future -- cherry-picked from Approach B)

**Goal**: Allow running agents to spawn specialized child agents for subtasks.

| Component | Effort | Details |
|-----------|--------|---------|
| MCP server extension | 1 week | Add `spawn_subagent` tool to message-queue server |
| Controller handler | 1 week | Listen for spawn requests, enforce limits, track parent-child |
| Result forwarding | 1 week | Route child results back to parent via Redis |
| Timeout + cancellation | 0.5 weeks | Cancel children when parent completes/fails |
| Integration tests | 0.5 weeks | Multi-agent end-to-end scenarios |

**Subagent protocol**: The agent's existing `message-queue` MCP server gains a `spawn_subagent` tool. When invoked, it publishes a spawn request to Redis. The controller listens, classifies the subtask, spawns a child job, and routes the result back to the parent agent via Redis.

**Limits**:

| Setting | Default | Rationale |
|---------|---------|-----------|
| `max_subagents_per_task` | 3 | Prevent runaway cost |
| `subagent_timeout_seconds` | 600 | 10 min max for child tasks |
| `subagent_inherit_branch` | true | Children work on same branch as parent |
| `subagent_depth_limit` | 1 | Subagents cannot spawn sub-subagents |

### Phase 5: Remote MCP Gateway (Future -- cherry-picked from Approach C)

**Goal**: Centralize tool access through a shared MCP gateway, reducing the need for specialized Docker images.

| Component | Effort | Details |
|-----------|--------|---------|
| MCP Gateway service | 3-4 weeks | SSE/streamable-HTTP proxy with per-session tool scoping |
| Tool backends | 1-2 weeks | Postgres, file-analysis, and other remote MCP servers |
| Security model | 1 week | JWT session tokens, NetworkPolicy, Redis-backed scopes |
| Agent mcp.json injection | 0.5 weeks | Generate per-task mcp.json pointing to gateway |

**Why defer**: The gateway is a non-trivial piece of infrastructure (concurrent SSE connections, MCP protocol proxying, session lifecycle management). Phases 1-3 deliver significant value without it. The gateway becomes worthwhile when the number of agent types exceeds 4-5 and image management becomes a burden.

---

## 9. Trade-offs and Risks

### 9.1 What We Gain

| Benefit | Details |
|---------|---------|
| **Per-task specialization** | Agents receive only relevant instructions, reducing noise and improving focus |
| **Centralized visibility** | Controller knows exactly what each agent gets; enables performance tracking |
| **Graceful degradation** | Tag-based fallback when embeddings fail; feature flag for full disable |
| **Incremental delivery** | Phase 1 delivers value in ~8 days with zero ML dependencies |
| **Clean boundaries** | Modular monolith structure makes future extraction to a separate service straightforward |

### 9.2 What We Give Up

| Cost | Details |
|------|---------|
| **Simplicity** | Today's system has zero skill selection logic. We are adding a registry, embeddings, and classification -- three new failure points |
| **Determinism** | Embedding-based matching is fuzzy. The same task might match different skills on different runs |
| **Agent autonomy** | Skills are chosen *for* the agent, not *by* the agent. If the classifier picks wrong, the agent has no recourse |
| **Operational cost** | pgvector requires Postgres 15+ with the extension. Voyage API costs ~$0.0001/embedding |
| **Entrypoint complexity** | The bash entrypoint gains more logic; may warrant a Python rewrite in the future |

### 9.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Wrong skill selection** degrades agent performance | Medium | High | Feature flag to disable; tag-based fallback; performance tracking identifies bad skills |
| **Redis payload size** exceeds limits with many/large skills | Low | Medium | Character budget enforcement (16K default); migrate to init container if needed |
| **pgvector performance** degrades with many skills | Low | Low | HNSW index handles 10K+ vectors with good recall even on small datasets; we expect < 1000 skills initially |
| **Voyage API availability** blocks task processing | Medium | Medium | Tag-based fallback; cache embeddings; embed at write time, not read time |
| **Skill quality** is inconsistent | High | Medium | Metrics dashboard surfaces underperforming skills; review process for new skills |
| **Agent type proliferation** creates image management burden | Low (initially) | Medium | Start with 1-2 types; MCP gateway (Phase 5) reduces need for many types |

---

## 10. Open Questions

1. **Skill authoring UX**: Who writes skills? Is the API + CLI sufficient for v1, or do we need a web UI?

2. **Skill scope**: Should skills be per-org, per-repo, or global? The data model supports all three, but the UX implications differ.

3. **Embedding refresh**: When a skill's content is updated, do we re-embed immediately (synchronous) or in a background job (asynchronous)?

4. **Agent-side feedback**: Can the agent report back which skills it actually *used* vs which were injected but ignored? Claude Code does not currently expose skill activation telemetry.

5. **Entrypoint rewrite**: The bash entrypoint is getting complex. Should we rewrite it in Python as part of this work, or defer?

6. **Classifier override**: Should webhook payloads support an explicit `skills` or `agent_type` field to bypass the classifier? (Recommended: yes, for power users and testing.)

7. **Skill pack vs individual skills**: Should we support grouping skills into packs (Approach B's concept) for common task patterns?

8. **Cross-agent-type migration**: If a task starts on `general` but the agent discovers it needs `frontend` tools, should it be able to request a restart on a different image?

---

## 11. Architectural Decision Records

### ADR-001: Controller-Side Skill Selection

**Status**: Accepted

**Context**: Skills (SKILL.md files) need to be matched to tasks. Two approaches are possible:
- **Controller-side**: The controller classifies the task, selects skills, and injects them before the agent starts
- **Agent-side**: The agent discovers and fetches skills at runtime via an MCP server

**Decision**: Controller-side selection. The controller classifies each task, selects relevant skills from the registry, and includes them in the Redis payload. The agent receives pre-selected skills and does not discover or fetch them itself.

**Consequences**:
- *Easier*: Agents start faster (skills pre-loaded). No new MCP server needed in the agent image. Controller has full visibility into what each agent receives. Performance tracking is straightforward.
- *Harder*: Controller takes on classification responsibility. Wrong classification wastes an entire agent run. No mid-task skill discovery. Requires embedding infrastructure (deferred to Phase 2).

---

### ADR-002: Redis Payload over Init Container

**Status**: Accepted

**Context**: Skill content needs to reach the agent's filesystem before Claude Code starts. Two mechanisms are available:
- **Redis payload**: Include skill content in the `task:{thread_id}` Redis value; entrypoint reads and writes to disk
- **Init container**: A K8s init container fetches skills from the registry API and writes to a shared volume

**Decision**: Redis payload for Phase 1-3. Skill files are typically 1-5KB each; 10 skills at 5KB = 50KB, well within Redis's value limits. The entrypoint already reads the task payload from Redis, so adding a `skills` array is a minimal change.

**Consequences**:
- *Easier*: No new K8s infrastructure (init containers, shared volumes). Single source of truth for the task payload. Simple entrypoint logic.
- *Harder*: Redis value size grows (but stays well under limits). Skills must be serialized as JSON strings. No way to stream large skills. If skills grow to include binary assets, we will need to migrate to init containers.

---

### ADR-003: Modular Monolith over Separate Service

**Status**: Accepted

**Context**: The skill registry could be deployed as:
- **A module within the controller** (modular monolith)
- **A separate microservice** with its own database and API

**Decision**: Modular monolith. The skill registry is a Python module (`controller/skills/`) within the controller codebase. It shares the controller's Postgres database and is deployed as part of the controller process.

**Consequences**:
- *Easier*: No network hops for skill lookups. Single deployment. Shared database simplifies transactions. Small team operates one service instead of two.
- *Harder*: Cannot scale the registry independently from the controller. Cannot use a different language/runtime. If the registry has a bug, it could affect the controller. Module boundaries must be enforced by convention (not network isolation).
- *Reversible*: Clean module boundaries (`controller/skills/` package with its own models and interfaces) make extraction to a separate service straightforward when multi-team access or independent scaling becomes necessary.
