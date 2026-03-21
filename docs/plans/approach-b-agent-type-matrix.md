# Approach B: Agent Type Matrix with Composable Skill Packs

## Status: Proposed
**Date:** 2026-03-21
**Author:** Software Architect Agent

---

## 1. Problem Statement

Ditto Factory currently runs every task on a single Docker image (`ditto-factory-agent`) with a hardcoded toolchain: Node.js 22, git, python3, jq, redis-tools, and one MCP server (message-queue). The spawner always uses `self._settings.agent_image` — there is no mechanism to vary the image, MCP servers, or injected instructions based on the task.

This creates three scaling problems:

1. **Bloated image** — Adding every possible tool (browser automation, database clients, security scanners) to one image inflates build time, image size, and attack surface.
2. **No capability specialization** — A CSS review task gets the same environment as a database migration task.
3. **Skill limit** — Claude Code's SKILL.md system caps around ~42 skills. We need a way to inject only relevant instructions per-task without hitting this ceiling.

### What we are NOT solving here

- Runtime plugin hot-swap into a running container (out of scope — containers are ephemeral)
- Multi-model agent routing (all agents use Claude Code)

---

## 2. Architecture Overview

```
                                 ┌──────────────────────┐
                                 │   Agent Type Registry │ ← YAML/DB definitions
                                 │   (general, frontend, │
                                 │    backend, data,     │
                                 │    security, infra)   │
                                 └──────────┬───────────┘
                                            │
Webhook ──► Controller ──► Classifier ──────┤
                               │            │
                               ▼            ▼
                          ┌─────────┐  ┌──────────────┐
                          │ Skill   │  │ Agent Type   │
                          │ Pack DB │  │ → Docker img │
                          └────┬────┘  │ → base mcp   │
                               │       │ → resources  │
                               │       └──────┬───────┘
                               │              │
                               ▼              ▼
                         ┌────────────────────────┐
                         │  Entrypoint.sh          │
                         │  1. Fetch task from Redis│
                         │  2. Merge mcp.json       │
                         │  3. Inject skill pack    │
                         │  4. Run claude -p        │
                         └────────────────────────┘
```

Three layers compose to define an agent's runtime capabilities:

| Layer | What it controls | How it's selected | Mutability |
|-------|-----------------|-------------------|------------|
| **Agent Type** | Docker image (installed binaries, MCP servers, base mcp.json) | Classifier at dispatch time | Immutable per-task (baked into image) |
| **Skill Pack** | Text-based instructions + optional MCP server declarations | Classifier + override | Dynamic (fetched from DB at container start) |
| **Subagent** | Additional agent types spawned by primary agent via message-queue MCP | Agent's own decision at runtime | Dynamic (controller-mediated) |

---

## 3. Agent Type Definitions

Each agent type is a named Docker image with a specific toolchain pre-installed.

### 3.1 Type Registry Schema

```yaml
# agent-types.yaml (or DB table: agent_types)
agent_types:
  general:
    image: "ditto-factory-agent:latest"
    description: "Default agent with git, Node.js, Python"
    base_mcp_servers:
      - message-queue
    installed_tools:
      - git
      - node (22)
      - python3
      - jq
    resources:
      cpu_request: "500m"
      memory_request: "2Gi"
      cpu_limit: "2"
      memory_limit: "8Gi"

  frontend:
    image: "ditto-factory-agent-frontend:latest"
    description: "Frontend specialist with browser automation"
    base_mcp_servers:
      - message-queue
      - playwright-mcp
    installed_tools:
      - git
      - node (22)
      - python3
      - playwright
      - chromium (headless)
      - lighthouse-cli
    resources:
      cpu_request: "1"
      memory_request: "4Gi"
      cpu_limit: "4"
      memory_limit: "12Gi"

  backend:
    image: "ditto-factory-agent-backend:latest"
    description: "Backend specialist with database clients and API testing"
    base_mcp_servers:
      - message-queue
      - postgres-mcp
    installed_tools:
      - git
      - node (22)
      - python3
      - psql
      - redis-cli
      - httpie
    resources:
      cpu_request: "500m"
      memory_request: "2Gi"
      cpu_limit: "2"
      memory_limit: "8Gi"

  data:
    image: "ditto-factory-agent-data:latest"
    description: "Data engineering with pandas, dbt, SQL tooling"
    base_mcp_servers:
      - message-queue
      - postgres-mcp
    installed_tools:
      - git
      - python3
      - pandas
      - dbt-core
      - psql
      - duckdb
    resources:
      cpu_request: "1"
      memory_request: "4Gi"
      cpu_limit: "4"
      memory_limit: "16Gi"

  security:
    image: "ditto-factory-agent-security:latest"
    description: "Security auditing with SAST/DAST tools"
    base_mcp_servers:
      - message-queue
    installed_tools:
      - git
      - node (22)
      - python3
      - semgrep
      - trivy
      - gitleaks
    resources:
      cpu_request: "1"
      memory_request: "4Gi"
      cpu_limit: "4"
      memory_limit: "8Gi"

  infra:
    image: "ditto-factory-agent-infra:latest"
    description: "Infrastructure/DevOps with Terraform, Helm, kubectl"
    base_mcp_servers:
      - message-queue
      - kubernetes-mcp
    installed_tools:
      - git
      - node (22)
      - python3
      - terraform
      - helm
      - kubectl
      - aws-cli
    resources:
      cpu_request: "500m"
      memory_request: "2Gi"
      cpu_limit: "2"
      memory_limit: "8Gi"
```

### 3.2 Dockerfile Strategy

Each agent type extends a shared base image:

```dockerfile
# images/agent-base/Dockerfile
FROM node:22-slim
RUN apt-get update && apt-get install -y \
    git build-essential python3 python3-pip curl jq redis-tools \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
RUN useradd -m -u 1000 agent
WORKDIR /home/agent
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER 1000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

```dockerfile
# images/agent-frontend/Dockerfile
FROM ditto-factory-agent-base:latest
USER root
RUN npx playwright install --with-deps chromium
RUN npm install -g @anthropic-ai/mcp-server-playwright
COPY mcp.json /etc/df/mcp-base.json
USER 1000
```

Key decision: the entrypoint is shared across all types. Only the installed binaries and base `mcp.json` differ.

### 3.3 Agent Type DB Model

```python
# New model in controller/src/controller/models.py

@dataclass
class AgentType:
    name: str                          # e.g., "frontend"
    image: str                         # e.g., "ditto-factory-agent-frontend:latest"
    description: str
    base_mcp_servers: list[str]        # MCP servers baked into the image
    installed_tools: list[str]         # For classifier reference
    resource_requests: dict[str, str]  # cpu, memory
    resource_limits: dict[str, str]
    is_default: bool = False           # "general" is default
```

---

## 4. Skill Pack Structure

Skill packs are curated bundles of related text instructions. They are NOT individual SKILL.md files — they are domain-coherent instruction sets stored in a database table and injected into the system prompt at runtime.

### 4.1 Skill Pack Schema

```python
@dataclass
class SkillPack:
    id: str
    name: str                          # e.g., "frontend-review"
    description: str
    domain: str                        # e.g., "frontend", "backend", "security"
    compatible_agent_types: list[str]  # Which agent types can use this
    instructions: str                  # The actual text injected into system prompt
    required_mcp_servers: list[str]    # MCP servers this pack needs (beyond base)
    priority: int                      # Ordering when multiple packs are combined
    max_combinable: int                # Max packs to combine with (prevents prompt bloat)
    version: str
    created_at: datetime
    updated_at: datetime
```

### 4.2 Example Skill Packs

```yaml
skill_packs:
  - name: "frontend-review"
    domain: "frontend"
    compatible_agent_types: ["general", "frontend"]
    required_mcp_servers: []
    priority: 10
    max_combinable: 3
    instructions: |
      ## Frontend Code Review Guidelines

      When reviewing frontend code:
      1. Check for accessibility (WCAG 2.1 AA compliance)
         - All images must have alt text
         - Form inputs must have associated labels
         - Color contrast must meet 4.5:1 ratio
      2. Verify responsive design breakpoints
      3. Check for CSS specificity issues
      4. Ensure components follow single-responsibility principle
      5. Validate prop types / TypeScript interfaces
      6. Check for unnecessary re-renders (React.memo, useMemo usage)

      When suggesting changes, always provide before/after code snippets.

  - name: "api-design"
    domain: "backend"
    compatible_agent_types: ["general", "backend"]
    required_mcp_servers: []
    priority: 10
    max_combinable: 3
    instructions: |
      ## API Design Standards

      When designing or reviewing APIs:
      1. Follow REST conventions (plural nouns, HTTP verbs, status codes)
      2. Version APIs in URL path (/api/v1/...)
      3. Use consistent error response format: {"error": {"code": "...", "message": "..."}}
      4. Pagination: cursor-based for large collections, offset for small
      5. Rate limiting headers: X-RateLimit-Limit, X-RateLimit-Remaining
      6. Always validate request bodies against schemas

  - name: "security-audit"
    domain: "security"
    compatible_agent_types: ["general", "security"]
    required_mcp_servers: []
    priority: 5
    max_combinable: 2
    instructions: |
      ## Security Audit Checklist

      Run these checks on every code change:
      1. No hardcoded secrets (API keys, passwords, tokens)
      2. SQL queries use parameterized statements
      3. User input is sanitized before rendering (XSS prevention)
      4. Authentication checks on all protected endpoints
      5. CORS configuration is restrictive, not wildcard
      6. Dependencies checked against known vulnerability databases

      Use `semgrep` if available for automated SAST scanning.
      Use `gitleaks` if available to scan for leaked secrets.

  - name: "database-migration"
    domain: "data"
    compatible_agent_types: ["general", "backend", "data"]
    required_mcp_servers: ["postgres-mcp"]
    priority: 10
    max_combinable: 2
    instructions: |
      ## Database Migration Standards

      When writing migrations:
      1. Always provide both up and down migrations
      2. Never drop columns in production — add new, migrate data, then deprecate
      3. Add indexes concurrently (CREATE INDEX CONCURRENTLY)
      4. Test migrations against a copy of production schema
      5. Keep migrations idempotent where possible
      6. Document breaking changes in migration comments
```

### 4.3 Composition Rules

1. **Maximum pack count**: No more than 3 skill packs per task (prevents prompt bloat).
2. **Compatibility check**: A skill pack can only be used with its declared `compatible_agent_types`.
3. **Priority ordering**: Lower priority number = injected first (closer to system prompt start).
4. **MCP server merge**: If a skill pack declares `required_mcp_servers`, those must be available in the selected agent type's image OR the entrypoint must be able to install them at runtime (with a startup time penalty).
5. **No conflicting instructions**: If two packs have contradictory guidance, the lower-priority one wins. The classifier should avoid selecting conflicting packs.

---

## 5. Task-to-Agent Classifier

### 5.1 Classification Flow

```
Task arrives
    │
    ▼
┌──────────────────┐
│ Rule-based pass   │ ← Fast, deterministic
│ 1. Repo language  │
│ 2. File patterns  │
│ 3. Keyword match  │
└────────┬─────────┘
         │
    confidence > 0.8?
    ┌────┴────┐
   YES       NO
    │         │
    ▼         ▼
  Return   ┌─────────────┐
  result   │ LLM fallback │ ← Slower, handles ambiguous tasks
           │ (Claude Haiku)│
           └──────┬──────┘
                  │
                  ▼
             Return result
```

### 5.2 Rule-Based Classifier

```python
# New file: controller/src/controller/classifier.py

@dataclass
class ClassificationResult:
    agent_type: str              # e.g., "frontend"
    skill_packs: list[str]       # e.g., ["frontend-review", "security-audit"]
    confidence: float            # 0.0 to 1.0
    reasoning: str               # Why this classification was chosen

class TaskClassifier:
    """Determines agent type and skill packs for a task."""

    # Keyword → agent type mapping (ordered by specificity)
    KEYWORD_RULES: list[tuple[list[str], str, list[str]]] = [
        # (keywords, agent_type, default_skill_packs)
        (["playwright", "browser", "screenshot", "visual"], "frontend", ["frontend-review"]),
        (["css", "tailwind", "responsive", "component", "react", "vue", "svelte"], "frontend", ["frontend-review"]),
        (["accessibility", "a11y", "wcag", "aria"], "frontend", ["frontend-review"]),
        (["migration", "schema", "database", "sql", "postgres"], "backend", ["database-migration"]),
        (["api", "endpoint", "rest", "graphql"], "backend", ["api-design"]),
        (["terraform", "helm", "kubernetes", "k8s", "docker", "ci/cd"], "infra", []),
        (["semgrep", "vulnerability", "cve", "security", "audit"], "security", ["security-audit"]),
        (["pandas", "dbt", "etl", "pipeline", "data"], "data", []),
    ]

    # File extension → agent type mapping
    EXTENSION_RULES: dict[str, str] = {
        ".tsx": "frontend",
        ".jsx": "frontend",
        ".vue": "frontend",
        ".svelte": "frontend",
        ".css": "frontend",
        ".scss": "frontend",
        ".tf": "infra",
        ".hcl": "infra",
        ".sql": "backend",
    }

    def classify_by_rules(
        self,
        task_text: str,
        repo_languages: list[str] | None = None,
        changed_files: list[str] | None = None,
    ) -> ClassificationResult | None:
        """Fast rule-based classification. Returns None if confidence is low."""
        task_lower = task_text.lower()
        matches: dict[str, int] = {}
        pack_candidates: dict[str, list[str]] = {}

        # Keyword matching
        for keywords, agent_type, packs in self.KEYWORD_RULES:
            score = sum(1 for kw in keywords if kw in task_lower)
            if score > 0:
                matches[agent_type] = matches.get(agent_type, 0) + score
                pack_candidates.setdefault(agent_type, []).extend(packs)

        # File extension matching
        if changed_files:
            for f in changed_files:
                ext = "." + f.rsplit(".", 1)[-1] if "." in f else ""
                if ext in self.EXTENSION_RULES:
                    at = self.EXTENSION_RULES[ext]
                    matches[at] = matches.get(at, 0) + 2  # Weight file evidence higher

        if not matches:
            return None

        best_type = max(matches, key=matches.get)
        total_signals = sum(matches.values())
        confidence = matches[best_type] / total_signals if total_signals > 0 else 0

        if confidence < 0.6:
            return None  # Fall through to LLM

        packs = list(dict.fromkeys(pack_candidates.get(best_type, [])))[:3]

        return ClassificationResult(
            agent_type=best_type,
            skill_packs=packs,
            confidence=confidence,
            reasoning=f"Rule-based: {matches[best_type]} keyword/file signals for '{best_type}'",
        )

    async def classify_by_llm(
        self,
        task_text: str,
        available_types: list[str],
        available_packs: list[str],
    ) -> ClassificationResult:
        """LLM fallback for ambiguous tasks. Uses Claude Haiku for speed/cost."""
        # Implementation: call Anthropic API with a structured prompt
        # asking for agent_type + skill_packs selection
        # Returns ClassificationResult with confidence and reasoning
        ...

    async def classify(
        self,
        task_text: str,
        repo_languages: list[str] | None = None,
        changed_files: list[str] | None = None,
    ) -> ClassificationResult:
        """Main entry point. Tries rules first, falls back to LLM."""
        result = self.classify_by_rules(task_text, repo_languages, changed_files)
        if result is not None:
            return result

        # LLM fallback
        return await self.classify_by_llm(
            task_text,
            available_types=["general", "frontend", "backend", "data", "security", "infra"],
            available_packs=["frontend-review", "api-design", "security-audit", "database-migration"],
        )
```

---

## 6. Dynamic mcp.json Composition

### 6.1 How It Works

Each agent type image ships with a base `mcp.json` at `/etc/df/mcp-base.json`. At container startup, `entrypoint.sh` merges additional MCP server declarations from the skill pack configuration.

The merge process:

```
/etc/df/mcp-base.json          ← Baked into Docker image
    +
skill_pack.required_mcp_servers ← Fetched from Redis at startup
    =
/etc/df/mcp.json               ← Final config passed to claude --mcp-config
```

### 6.2 MCP Server Catalog

Each MCP server that can be dynamically added is pre-installed in the relevant agent type image. The entrypoint does NOT install MCP servers at runtime — it only merges configuration.

```json
// MCP server catalog (stored alongside agent type registry)
{
  "mcp_servers": {
    "message-queue": {
      "command": "node",
      "args": ["/opt/mcp/message-queue-server.js"],
      "env": { "REDIS_URL": "${REDIS_URL}", "THREAD_ID": "${THREAD_ID}" },
      "installed_in": ["general", "frontend", "backend", "data", "security", "infra"]
    },
    "playwright-mcp": {
      "command": "node",
      "args": ["/opt/mcp/playwright-server.js"],
      "env": {},
      "installed_in": ["frontend"]
    },
    "postgres-mcp": {
      "command": "node",
      "args": ["/opt/mcp/postgres-server.js"],
      "env": { "DATABASE_URL": "${DATABASE_URL}" },
      "installed_in": ["backend", "data"]
    },
    "kubernetes-mcp": {
      "command": "node",
      "args": ["/opt/mcp/kubernetes-server.js"],
      "env": {},
      "installed_in": ["infra"]
    }
  }
}
```

### 6.3 Merge Script (part of entrypoint.sh)

```bash
merge_mcp_config() {
    local base_config="/etc/df/mcp-base.json"
    local extra_servers_json="$1"  # JSON string from Redis
    local output="/etc/df/mcp.json"

    if [ -z "$extra_servers_json" ] || [ "$extra_servers_json" = "null" ]; then
        cp "$base_config" "$output"
        return
    fi

    # Merge using jq: base config + extra MCP servers
    jq -s '.[0] * {"mcpServers": (.[0].mcpServers + .[1])}' \
        "$base_config" <(echo "$extra_servers_json") > "$output"
}
```

---

## 7. Modified Entrypoint Flow

### 7.1 Current Flow (today)

```
1. Validate env vars (THREAD_ID, REDIS_URL, GITHUB_TOKEN, ANTHROPIC_API_KEY)
2. Fetch task JSON from Redis (task, system_prompt, repo_url, branch)
3. Clone repo
4. Checkout branch
5. Run claude -p with --mcp-config /etc/df/mcp.json
6. Count commits, push branch
7. Publish result to Redis
```

### 7.2 New Flow (Approach B)

```
1.  Validate env vars (THREAD_ID, REDIS_URL, GITHUB_TOKEN, ANTHROPIC_API_KEY)
2.  Fetch task JSON from Redis
    ├── task, system_prompt, repo_url, branch       (existing)
    ├── skill_packs: [{name, instructions, priority}]  (NEW)
    └── extra_mcp_servers: {server_name: config}       (NEW)
3.  Merge MCP config: /etc/df/mcp-base.json + extra_mcp_servers → /etc/df/mcp.json   (NEW)
4.  Compose system prompt: base system_prompt + skill pack instructions (sorted by priority)  (NEW)
5.  Clone repo
6.  Checkout branch
7.  Run claude -p with composed system prompt and merged mcp.json
8.  Count commits, push branch
9.  Publish result to Redis (include agent_type in result metadata)  (MODIFIED)
```

### 7.3 New entrypoint.sh Additions

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- Existing env validation ---
: "${THREAD_ID:?THREAD_ID is required}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required}"

REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)

# --- Fetch task (expanded schema) ---
TASK_JSON=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "task:$THREAD_ID")
REPO_URL=$(echo "$TASK_JSON" | jq -r '.repo_url')
BRANCH=$(echo "$TASK_JSON" | jq -r '.branch')
TASK=$(echo "$TASK_JSON" | jq -r '.task')
BASE_SYSTEM_PROMPT=$(echo "$TASK_JSON" | jq -r '.system_prompt // empty')

# --- NEW: Extract skill pack instructions ---
SKILL_INSTRUCTIONS=$(echo "$TASK_JSON" | jq -r '
    [.skill_packs // [] | sort_by(.priority) | .[].instructions] | join("\n\n---\n\n")
')

# Compose full system prompt: base + skill packs
if [ -n "$SKILL_INSTRUCTIONS" ] && [ "$SKILL_INSTRUCTIONS" != "null" ]; then
    SYSTEM_PROMPT="${BASE_SYSTEM_PROMPT}

## Skill Pack Instructions

${SKILL_INSTRUCTIONS}"
else
    SYSTEM_PROMPT="$BASE_SYSTEM_PROMPT"
fi

# --- NEW: Merge MCP config ---
EXTRA_MCP=$(echo "$TASK_JSON" | jq -r '.extra_mcp_servers // empty')
if [ -n "$EXTRA_MCP" ] && [ "$EXTRA_MCP" != "null" ]; then
    jq -s '.[0] * {"mcpServers": (.[0].mcpServers + .[1])}' \
        /etc/df/mcp-base.json <(echo "$EXTRA_MCP") > /tmp/mcp.json
    MCP_CONFIG="/tmp/mcp.json"
else
    MCP_CONFIG="/etc/df/mcp-base.json"
fi

# --- Clone, checkout (unchanged) ---
# ...

# --- Run Claude with composed config ---
CLAUDE_ARGS=(-p "$TASK" --allowedTools '*' --mcp-config "$MCP_CONFIG")
if [ -n "${SYSTEM_PROMPT:-}" ]; then
    CLAUDE_ARGS+=(--system-prompt "$SYSTEM_PROMPT")
fi

claude "${CLAUDE_ARGS[@]}"

# --- Publish result (add agent_type metadata) ---
AGENT_TYPE=$(echo "$TASK_JSON" | jq -r '.agent_type // "general"')
# Include agent_type in result JSON for observability
```

---

## 8. Modified Spawner

### 8.1 Current spawner.spawn() Signature

```python
def spawn(self, thread_id: str, github_token: str, redis_url: str) -> str:
```

Always uses `self._settings.agent_image` for the container image.

### 8.2 New spawner.spawn() Signature

```python
def spawn(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_type: str = "general",       # NEW
) -> str:
```

### 8.3 Changes to build_job_spec()

```python
def build_job_spec(
    self,
    thread_id: str,
    github_token: str,
    redis_url: str,
    agent_type: str = "general",
) -> k8s.V1Job:
    # Look up agent type from registry
    agent_type_def = self._agent_type_registry.get(agent_type)
    if agent_type_def is None:
        raise ValueError(f"Unknown agent type: {agent_type}")

    short_id = self._sanitize_label(thread_id[:8])
    ts = int(time.time())
    job_name = f"df-{agent_type[:4]}-{short_id}-{ts}"  # Include type in job name

    container = k8s.V1Container(
        name="agent",
        image=agent_type_def.image,                          # CHANGED: type-specific image
        image_pull_policy=self._settings.image_pull_policy,
        env=[
            k8s.V1EnvVar(name="THREAD_ID", value=thread_id),
            k8s.V1EnvVar(name="REDIS_URL", value=redis_url),
            k8s.V1EnvVar(name="GITHUB_TOKEN", value=github_token),
            k8s.V1EnvVar(name="AGENT_TYPE", value=agent_type),  # NEW
            k8s.V1EnvVar(
                name="ANTHROPIC_API_KEY",
                value_from=k8s.V1EnvVarSource(
                    secret_key_ref=k8s.V1SecretKeySelector(
                        name="df-secrets", key="anthropic-api-key"
                    )
                ),
            ),
        ],
        resources=k8s.V1ResourceRequirements(
            requests=agent_type_def.resource_requests,        # CHANGED: type-specific resources
            limits=agent_type_def.resource_limits,
        ),
        security_context=k8s.V1SecurityContext(
            run_as_non_root=True,
            run_as_user=1000,
            allow_privilege_escalation=False,
            capabilities=k8s.V1Capabilities(drop=["ALL"]),
        ),
    )

    return k8s.V1Job(
        metadata=k8s.V1ObjectMeta(
            name=job_name,
            labels={
                "app": "ditto-factory-agent",
                "df/thread": short_id,
                "df/agent-type": agent_type,                  # NEW: label for filtering
            },
        ),
        spec=k8s.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=300,
            active_deadline_seconds=self._settings.max_job_duration_seconds,
            template=k8s.V1PodTemplateSpec(
                metadata=k8s.V1ObjectMeta(
                    labels={
                        "app": "ditto-factory-agent",
                        "df/thread": short_id,
                        "df/agent-type": agent_type,
                    }
                ),
                spec=k8s.V1PodSpec(
                    containers=[container],
                    restart_policy="Never",
                ),
            ),
        ),
    )
```

---

## 9. Modified Orchestrator

### 9.1 Changes to _spawn_job()

```python
async def _spawn_job(
    self,
    thread: Thread,
    task_request: TaskRequest,
    is_retry: bool = False,
    retry_count: int = 0,
) -> None:
    thread_id = thread.id

    # PREPARE: Build system prompt (unchanged)
    integration = self._registry.get(task_request.source)
    system_prompt = build_system_prompt(...)

    # NEW: Classify task to determine agent type + skill packs
    classification = await self._classifier.classify(
        task_text=task_request.task,
        repo_languages=None,  # TODO: fetch from GitHub API
        changed_files=None,   # TODO: fetch from webhook payload
    )

    # NEW: Fetch skill pack definitions from DB
    skill_packs = await self._state.get_skill_packs(classification.skill_packs)

    # NEW: Build extra MCP server config from skill packs
    extra_mcp_servers = {}
    for pack in skill_packs:
        for server_name in pack.required_mcp_servers:
            server_config = self._mcp_catalog.get(server_name)
            if server_config:
                extra_mcp_servers[server_name] = server_config

    # Create branch name
    short_id = thread_id[:8]
    branch = f"df/{short_id}/{uuid.uuid4().hex[:8]}"

    # Push task to Redis (expanded schema)
    await self._redis.push_task(thread_id, {
        "task": task_request.task,
        "system_prompt": system_prompt,
        "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
        "branch": branch,
        "agent_type": classification.agent_type,                    # NEW
        "skill_packs": [                                            # NEW
            {"name": p.name, "instructions": p.instructions, "priority": p.priority}
            for p in skill_packs
        ],
        "extra_mcp_servers": extra_mcp_servers,                     # NEW
    })

    # SPAWN: Create K8s Job with agent type
    job_name = self._spawner.spawn(
        thread_id=thread_id,
        github_token="",
        redis_url=self._settings.redis_url,
        agent_type=classification.agent_type,                       # NEW
    )

    # Track job (include classification metadata)
    job = Job(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        k8s_job_name=job_name,
        status=JobStatus.RUNNING,
        task_context={
            "task": task_request.task,
            "branch": branch,
            "agent_type": classification.agent_type,                # NEW
            "skill_packs": classification.skill_packs,              # NEW
            "classification_confidence": classification.confidence, # NEW
        },
        started_at=datetime.now(timezone.utc),
    )
    await self._state.create_job(job)
```

---

## 10. Subagent Spawning (Controller-Mediated)

### 10.1 How It Works Today

The agent has a `message-queue` MCP server that communicates with Redis. Currently this is used for receiving follow-up messages from the controller.

### 10.2 Subagent Protocol

We extend the message-queue MCP server with a `spawn_subagent` tool:

```
Agent                    Redis                    Controller
  │                        │                          │
  │─── spawn_subagent ────►│                          │
  │    {type: "frontend",  │                          │
  │     task: "...",       │                          │
  │     parent_thread: X}  │                          │
  │                        │──── pubsub notify ──────►│
  │                        │                          │
  │                        │     Classifier runs      │
  │                        │     Spawner creates Job  │
  │                        │◄──── child thread ID ────│
  │                        │                          │
  │◄── result ─────────────│      (child completes)   │
  │                        │                          │
```

### 10.3 MCP Server Extension

```javascript
// Addition to message-queue-server.js

server.addTool({
  name: "spawn_subagent",
  description: "Spawn a specialized sub-agent to handle part of this task",
  parameters: {
    type: "object",
    properties: {
      agent_type: {
        type: "string",
        enum: ["general", "frontend", "backend", "data", "security", "infra"],
        description: "The type of agent to spawn"
      },
      task: {
        type: "string",
        description: "The task for the sub-agent to execute"
      },
      wait_for_result: {
        type: "boolean",
        default: true,
        description: "Whether to block until the sub-agent completes"
      }
    },
    required: ["agent_type", "task"]
  },
  handler: async ({ agent_type, task, wait_for_result }) => {
    const requestId = crypto.randomUUID();

    // Publish spawn request
    await redis.publish("df:subagent:request", JSON.stringify({
      request_id: requestId,
      parent_thread_id: process.env.THREAD_ID,
      agent_type,
      task,
      repo_url: /* inherit from parent */,
      branch: /* inherit from parent */,
    }));

    if (!wait_for_result) {
      return { request_id: requestId, status: "spawned" };
    }

    // Block until child result arrives
    const result = await waitForResult(requestId, timeout=600);
    return result;
  }
});
```

### 10.4 Controller-Side Handler

```python
# New method in Orchestrator

async def handle_subagent_request(self, request: dict) -> None:
    """Handle a subagent spawn request from an active agent."""
    parent_thread_id = request["parent_thread_id"]
    parent_thread = await self._state.get_thread(parent_thread_id)

    if parent_thread is None:
        logger.error("Parent thread %s not found", parent_thread_id)
        return

    # Enforce limits
    active_children = await self._state.count_child_jobs(parent_thread_id)
    if active_children >= self._settings.max_subagents_per_task:
        await self._redis.publish_subagent_error(
            request["request_id"],
            f"Max subagents ({self._settings.max_subagents_per_task}) reached"
        )
        return

    # Create child thread
    child_thread_id = f"{parent_thread_id}:sub:{request['request_id'][:8]}"

    child_task = TaskRequest(
        thread_id=child_thread_id,
        task=request["task"],
        source=parent_thread.source,
        repo_owner=parent_thread.repo_owner,
        repo_name=parent_thread.repo_name,
    )

    # Classify with explicit agent type override
    classification = ClassificationResult(
        agent_type=request.get("agent_type", "general"),
        skill_packs=[],
        confidence=1.0,
        reasoning="Explicit subagent request from parent agent",
    )

    # Spawn using the requested agent type
    await self._spawn_job_with_classification(
        parent_thread, child_task, classification,
        parent_thread_id=parent_thread_id,
    )
```

### 10.5 Subagent Limits

| Setting | Default | Rationale |
|---------|---------|-----------|
| `max_subagents_per_task` | 3 | Prevent runaway cost |
| `subagent_timeout_seconds` | 600 | 10 min max for child tasks |
| `subagent_inherit_branch` | true | Children work on same branch as parent |
| `subagent_depth_limit` | 1 | Subagents cannot spawn sub-subagents |

---

## 11. Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DISPATCH TIME                                │
│                                                                     │
│  Webhook ──► Orchestrator ──► Classifier ──► agent_type + packs     │
│                    │                              │                  │
│                    ▼                              ▼                  │
│              Redis SET task:THREAD_ID       Spawner.spawn(           │
│              {                                agent_type=...)        │
│                task: "...",                       │                  │
│                system_prompt: "...",              ▼                  │
│                agent_type: "frontend",      K8s Job created         │
│                skill_packs: [...],          with type-specific      │
│                extra_mcp_servers: {...}     Docker image             │
│              }                                                      │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        CONTAINER STARTUP                            │
│                                                                     │
│  entrypoint.sh:                                                     │
│    1. GET task:THREAD_ID from Redis                                 │
│    2. Merge mcp-base.json + extra_mcp_servers → mcp.json           │
│    3. Compose system_prompt + skill_pack instructions               │
│    4. Clone repo, checkout branch                                   │
│    5. claude -p --mcp-config /tmp/mcp.json --system-prompt "..."    │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        RUNTIME (OPTIONAL)                           │
│                                                                     │
│  Agent ──► spawn_subagent(type="security", task="audit deps")       │
│    │                                                                │
│    ▼                                                                │
│  message-queue MCP ──► Redis pubsub ──► Controller                  │
│                                           │                         │
│                                           ▼                         │
│                                     Spawner.spawn(agent_type=...)   │
│                                           │                         │
│                                           ▼                         │
│                                     Child K8s Job                   │
│                                           │                         │
│                                           ▼                         │
│  Agent ◄── result ◄── Redis ◄── Child completes                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 12. Helm Chart Changes

### 12.1 values.yaml Additions

```yaml
# Agent type registry
agentTypes:
  general:
    image:
      repository: ditto-factory-agent
      tag: "latest"
    resources:
      requests: { cpu: "500m", memory: "2Gi" }
      limits: { cpu: "2", memory: "8Gi" }
  frontend:
    image:
      repository: ditto-factory-agent-frontend
      tag: "latest"
    resources:
      requests: { cpu: "1", memory: "4Gi" }
      limits: { cpu: "4", memory: "12Gi" }
  backend:
    image:
      repository: ditto-factory-agent-backend
      tag: "latest"
    resources:
      requests: { cpu: "500m", memory: "2Gi" }
      limits: { cpu: "2", memory: "8Gi" }
  # ... data, security, infra

# Classifier settings
classifier:
  llmFallbackEnabled: true
  llmModel: "claude-haiku"
  defaultAgentType: "general"

# Subagent limits
subagents:
  maxPerTask: 3
  timeoutSeconds: 600
  depthLimit: 1
```

### 12.2 ConfigMap for Agent Type Registry

The controller needs the agent type definitions. These can be injected via a ConfigMap mounted into the controller pod, or stored in the database and managed via an admin API.

---

## 13. Database Schema Changes

```sql
-- New table: agent_types
CREATE TABLE agent_types (
    name            TEXT PRIMARY KEY,
    image           TEXT NOT NULL,
    description     TEXT,
    base_mcp_servers JSONB DEFAULT '[]',
    installed_tools  JSONB DEFAULT '[]',
    resource_requests JSONB DEFAULT '{}',
    resource_limits   JSONB DEFAULT '{}',
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- New table: skill_packs
CREATE TABLE skill_packs (
    id              TEXT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT,
    domain          TEXT NOT NULL,
    compatible_agent_types JSONB DEFAULT '[]',
    instructions    TEXT NOT NULL,
    required_mcp_servers JSONB DEFAULT '[]',
    priority        INTEGER DEFAULT 10,
    max_combinable  INTEGER DEFAULT 3,
    version         TEXT DEFAULT '1.0.0',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Add agent_type to jobs table
ALTER TABLE jobs ADD COLUMN agent_type TEXT DEFAULT 'general';
ALTER TABLE jobs ADD COLUMN parent_thread_id TEXT REFERENCES threads(id);
ALTER TABLE jobs ADD COLUMN classification_confidence REAL;
```

---

## 14. Trade-off Analysis

### 14.1 What's Good

| Benefit | Why it matters |
|---------|---------------|
| **Right-sized containers** | Frontend tasks get browser tools; security tasks get scanners. No bloat. |
| **Clear capability boundaries** | Agent types are explicit — you know exactly what tools are available. |
| **Skill packs are reversible** | Text instructions can be updated in the DB without rebuilding images. |
| **Subagent spawning enables composition** | A "general" agent can delegate to a "security" agent without needing security tools installed. |
| **Classification is observable** | Every job records which agent type and skill packs were selected, enabling iterative improvement. |
| **Resource optimization** | Frontend agents (browser = heavy) get more memory. General agents stay lean. |

### 14.2 What's Risky

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Image proliferation** | Medium | Start with 2-3 types (general + 1 specialist). Add types only when there's evidence the general agent underperforms. |
| **Classifier accuracy** | High | Log all classifications with confidence scores. Review misclassifications weekly. Allow manual override via task metadata. |
| **Skill pack prompt bloat** | Medium | Enforce max 3 packs per task. Monitor total system prompt length. Each pack should be under 500 words. |
| **Subagent cost runaway** | High | Hard limit of 3 subagents per task. Budget alerts. Subagents cannot spawn sub-subagents (depth=1). |
| **Branch conflicts from subagents** | High | All subagents work on the same branch. The parent agent must coordinate. Consider file-level locking or sequential execution. |
| **MCP server compatibility** | Medium | Validate that extra_mcp_servers are actually installed in the selected agent type's image. Fail fast with clear error if not. |

### 14.3 What's Complex

| Complexity | Impact | Simplification option |
|-----------|--------|----------------------|
| **Multi-image CI/CD pipeline** | Must build, test, and push 6+ Docker images on every release. | Use multi-stage builds from a monorepo. Only rebuild changed images. |
| **Classifier maintenance** | Rules drift as the product evolves. LLM fallback adds latency and cost. | Start rule-only. Add LLM fallback in phase 2 after collecting misclassification data. |
| **Subagent coordination** | Parent must wait for child, handle failures, merge results. | Phase 3. Start without subagent support. |
| **Skill pack versioning** | Instructions evolve. Old tasks may reference deprecated packs. | Use semantic versioning. Never delete packs — deprecate them. |

---

## 15. Implementation Phases and Effort Estimates

### Phase 1: Agent Type Registry + Modified Spawner
**Effort: Medium (2-3 weeks)**

| Component | Size | Description |
|-----------|------|-------------|
| Agent type data model | S | Add `AgentType` to models.py, DB migration |
| Agent type registry | S | YAML or DB-backed registry with lookup |
| Modified `spawner.py` | S | Accept `agent_type` param, look up image + resources |
| Modified `orchestrator.py` | S | Pass agent type through to spawner |
| Base + 1 specialist Dockerfile | M | Create shared base image, one specialist (e.g., frontend) |
| CI/CD for multi-image builds | M | GitHub Actions workflow to build all agent images |
| Helm chart changes | S | `agentTypes` in values.yaml, ConfigMap |

### Phase 2: Skill Packs + Classifier
**Effort: Medium (2-3 weeks)**

| Component | Size | Description |
|-----------|------|-------------|
| Skill pack data model + DB | S | `SkillPack` model, migration, CRUD |
| Seed skill packs | M | Write 4-6 initial skill packs with good instructions |
| Rule-based classifier | M | `TaskClassifier` with keyword + file extension rules |
| Modified entrypoint.sh | S | Merge MCP config, compose system prompt |
| Modified orchestrator | M | Integrate classifier, fetch packs, push expanded task JSON |
| Admin API for skill packs | S | CRUD endpoints for managing packs |
| Observability | S | Log classification results, add metrics |

### Phase 3: Subagent Spawning
**Effort: Large (3-4 weeks)**

| Component | Size | Description |
|-----------|------|-------------|
| message-queue MCP extension | M | Add `spawn_subagent` tool |
| Controller pubsub handler | M | Listen for spawn requests, enforce limits |
| Child thread/job tracking | M | Parent-child relationship in DB |
| Result forwarding | M | Route child results back to parent via Redis |
| Timeout + cancellation | M | Cancel children when parent completes/fails |
| Integration tests | L | Test multi-agent scenarios end-to-end |

### Phase 4: LLM Classifier Fallback + Refinement
**Effort: Small (1 week)**

| Component | Size | Description |
|-----------|------|-------------|
| LLM classifier | S | Call Claude Haiku with structured prompt |
| A/B comparison | S | Compare rule vs LLM classification accuracy |
| Feedback loop | M | Allow users to correct misclassifications |

---

## 16. ADR: Agent Type as Primary Capability Mechanism

### ADR-001: Use Docker images as the primary capability boundary

**Status:** Proposed

**Context:** Claude Code agents need different tools for different tasks. MCP servers and CLI tools must be pre-installed (can't be hot-swapped into a running container). The current system has one image for all tasks.

**Decision:** Define named agent types as distinct Docker images with pre-installed toolchains. The controller selects the agent type at dispatch time and spawns a K8s Job with the corresponding image.

**Consequences:**
- Easier: Adding new capabilities (just build a new image or extend an existing one)
- Easier: Resource optimization (heavy images get more CPU/memory)
- Harder: CI/CD pipeline complexity (multiple images to build/test/push)
- Harder: Debugging (need to know which agent type was used)
- Trade-off: Image pull latency for rarely-used types (mitigate with pre-pull DaemonSet)

### ADR-002: Skill packs over individual skills

**Status:** Proposed

**Context:** Individual SKILL.md files don't scale past ~42. Text-based instructions are the most flexible injection mechanism but risk prompt bloat.

**Decision:** Bundle related instructions into "skill packs" stored in a database. Limit to 3 packs per task. Packs are domain-coherent and version-controlled.

**Consequences:**
- Easier: Domain experts write coherent instruction sets, not scattered tips
- Easier: Update instructions without rebuilding Docker images
- Harder: Deciding pack boundaries (too broad = irrelevant instructions, too narrow = too many packs)
- Trade-off: System prompt length increases (mitigate with strict word limits per pack)

### ADR-003: Controller-mediated subagent spawning

**Status:** Proposed

**Context:** An agent may need capabilities from a different agent type (e.g., a general agent needs a security audit). The agent can't spawn K8s Jobs directly (no K8s API access).

**Decision:** The agent requests subagent spawning via the message-queue MCP server. The controller receives the request, enforces limits, and spawns a child K8s Job.

**Consequences:**
- Easier: Security (agents never have K8s API access)
- Easier: Cost control (controller enforces limits)
- Harder: Latency (round-trip through Redis + controller + K8s Job startup)
- Harder: Coordination (parent must handle child failures gracefully)
- Trade-off: Complexity vs. capability (defer to Phase 3)

---

## 17. Open Questions

1. **Image pre-pulling**: Should we use a DaemonSet to pre-pull all agent type images on every node? This reduces cold-start latency but wastes disk space.

2. **Classifier override**: Should webhook payloads support an explicit `agent_type` field to bypass the classifier? (Recommended: yes, for power users and testing.)

3. **Skill pack authoring**: Who writes skill packs? Engineering teams? A central platform team? Should there be a review process?

4. **Cross-agent-type migrations**: If a task starts on `general` but the agent realizes it needs `frontend` tools, should it be able to request a restart on a different image?

5. **Shared workspace**: Should subagents share a workspace volume, or each clone independently? Shared volumes add complexity but avoid merge conflicts.

6. **Cost attribution**: How do we attribute Anthropic API costs to the parent task vs. subagent tasks?
