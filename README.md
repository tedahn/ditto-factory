<div align="center">

# Ditto Factory

<img src="assets/banner.jpeg" alt="Ditto Factory — Ditto Replication Factory" width="100%" />

**Kubernetes-native coding agent platform using headless Claude Code**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-ready-326CE5.svg?logo=kubernetes&logoColor=white)](https://kubernetes.io)
[![Claude Code](https://img.shields.io/badge/Runtime-Claude_Code-D97706.svg)](https://docs.anthropic.com/en/docs/claude-code)

Turn Slack messages, GitHub issues, and Linear comments into autonomous coding agents.
Each agent runs as an ephemeral K8s Job with Claude Code as the runtime — no proprietary orchestration, no vendor lock-in.

</div>

---

## Why Ditto Factory?

Internal coding agents at companies like Stripe, Ramp, and Coinbase share a common pattern: they meet engineers where they work, run in isolated sandboxes, and report back with PRs. Ditto Factory implements this pattern with minimal dependencies.

| Concern | Approach |
|:--|:--|
| **Agent Runtime** | Headless Claude Code (`claude -p`) |
| **Orchestration** | FastAPI controller + K8s Jobs |
| **Sandboxes** | Ephemeral Docker containers |
| **State** | PostgreSQL / SQLite + Redis |
| **Deployment** | Helm chart (any K8s cluster) |
| **Paid Dependencies** | Anthropic API only |

---

## How It Works

```mermaid
flowchart TB
    subgraph Sources["Webhook Sources"]
        Slack["Slack"]
        GitHub["GitHub"]
        Linear["Linear"]
        CLI["CLI / REST API"]
    end

    subgraph Controller["FastAPI Controller (K8s Deployment)"]
        Webhook["Webhook Ingress<br/><i>Verify signatures, parse events</i>"]
        Orchestrator["Orchestrator<br/><i>Threads, locks, conversations</i>"]

        subgraph Skills["Skill Hotloading"]
            Classifier["Task Classifier<br/><i>Semantic search + tag fallback</i>"]
            Registry["Skill Registry<br/><i>CRUD, versioning, embeddings</i>"]
            Resolver["Agent Type Resolver<br/><i>Skill requirements → Docker image</i>"]
        end

        subgraph GW["MCP Gateway Manager"]
            GWManager["Gateway Manager<br/><i>Per-session tool scoping</i>"]
        end

        Tracker["Performance Tracker<br/><i>Outcome feedback loop</i>"]
        Spawner["K8s Job Spawner"]
        Safety["Safety Pipeline<br/><i>Auto-PR, anti-stall retry</i>"]
        SubHandler["Subagent Handler<br/><i>Redis pubsub listener</i>"]
    end

    subgraph Data["Data Layer"]
        PG[("PostgreSQL / SQLite<br/><i>Threads, jobs, skills,<br/>versions, usage metrics</i>")]
        Redis[("Redis<br/><i>Task payloads, results,<br/>message queues, gateway scopes</i>")]
    end

    subgraph Agent["Agent Pod (Ephemeral K8s Job)"]
        Entry["entrypoint.sh<br/><i>Clone repo, inject skills,<br/>merge MCP config</i>"]
        Claude["claude -p<br/><i>Headless Claude Code</i>"]
        MQ["MCP: message-queue<br/><i>check_messages,<br/>spawn_subagent</i>"]
    end

    subgraph Gateway["MCP Gateway (K8s Deployment)"]
        GWServer["Express + MCP SDK<br/><i>SSE transport</i>"]
        FileAnalysis["analyze_file<br/><i>Sandboxed file analysis</i>"]
        WebSearch["search_web<br/><i>Brave Search API</i>"]
        DBQuery["query_database<br/><i>Read-only PostgreSQL</i>"]
    end

    Slack & GitHub & Linear & CLI --> Webhook
    Webhook --> Orchestrator
    Orchestrator --> Classifier
    Classifier <--> Registry
    Classifier --> Resolver
    Orchestrator --> GWManager
    GWManager --> Redis
    Orchestrator --> Spawner
    Orchestrator --> Tracker
    Tracker --> PG
    Registry <--> PG
    Spawner --> Agent
    Orchestrator --> Redis

    Entry --> Claude
    Claude <--> MQ
    MQ <--> Redis
    Claude <-.-> GWServer
    GWServer --> FileAnalysis & WebSearch & DBQuery

    Redis --> Safety
    Safety --> |Report result| Sources
    SubHandler <--> Redis
    SubHandler --> Spawner

    style Sources fill:#f9f,stroke:#333
    style Controller fill:#e8f4fd,stroke:#333
    style Skills fill:#fff3cd,stroke:#333
    style Agent fill:#d4edda,stroke:#333
    style Gateway fill:#f8d7da,stroke:#333
    style Data fill:#e2e3e5,stroke:#333
```

> **1. Receive** — Webhook arrives &nbsp;→&nbsp; **2. Classify** — Select skills + agent type &nbsp;→&nbsp; **3. Scope** — Set gateway tools &nbsp;→&nbsp; **4. Spawn** — Create K8s Job with injected skills &nbsp;→&nbsp; **5. Execute** — Claude Code runs with skills + MCP tools &nbsp;→&nbsp; **6. Track** — Record outcomes for feedback loop &nbsp;→&nbsp; **7. Report** — Post results to origin

---

## Quick Start

### Local Development (Docker Compose)

```bash
git clone https://github.com/tedahn/ditto-factory.git
cd ditto-factory
docker compose up -d

# Verify
curl http://localhost:8000/health
# → {"status":"ok"}
```

Starts the controller with SQLite (no Postgres needed) and Redis.

### Kubernetes (Helm)

```bash
helm install ditto-factory ./charts/ditto-factory \
  --set secrets.anthropicApiKey=$ANTHROPIC_API_KEY \
  --set secrets.slackSigningSecret=$SLACK_SIGNING_SECRET \
  --set secrets.slackBotToken=$SLACK_BOT_TOKEN
```

Includes PostgreSQL, Redis (Bitnami subcharts), RBAC, network policies, and optional ingress.

### Running Tests

```bash
cd controller
uv pip install -e ".[dev]"
uv run pytest tests/ -v          # 134+ tests, ~2 seconds

# K8s live tests (requires running cluster + Redis)
AAL_K8S_LIVE_TEST=1 uv run pytest tests/e2e/test_k8s_live.py -v
```

---

## Architecture

<details>
<summary><strong>Project Structure</strong></summary>

```
controller/src/controller/
├── main.py                  # FastAPI app, lifespan, webhook routing
├── config.py                # Pydantic Settings (DF_ env prefix)
├── models.py                # TaskRequest, AgentResult, Thread, Job
├── orchestrator.py          # Core lifecycle: receive → spawn → complete
├── gateway.py               # MCP Gateway scope management
├── subagent.py              # Subagent spawn handler (Redis pubsub)
├── state/
│   ├── protocol.py          # StateBackend protocol (swappable)
│   ├── postgres.py          # Production backend (asyncpg)
│   ├── sqlite.py            # Local dev backend (aiosqlite)
│   └── redis_state.py       # Ephemeral state (task handoff, queues)
├── integrations/
│   ├── protocol.py          # Integration protocol
│   ├── registry.py          # Dynamic webhook router
│   ├── slack.py             # Slack: signatures, bot filtering, threading
│   ├── github.py            # GitHub: 4 event types, org allowlist, auto-PR
│   ├── linear.py            # Linear: GraphQL, team-to-repo mapping
│   ├── thread_id.py         # Deterministic SHA256 thread IDs
│   └── sanitize.py          # Untrusted content wrapping
├── skills/
│   ├── api.py               # REST API (CRUD, search, metrics, agent types)
│   ├── registry.py          # Skill CRUD + tag/embedding search
│   ├── classifier.py        # Task → skill matching (semantic + tag fallback)
│   ├── injector.py          # Format skills for Redis payload injection
│   ├── resolver.py          # Skill requirements → Docker image selection
│   ├── tracker.py           # Performance tracking + feedback loop
│   ├── embedding.py         # Voyage-3 / NoOp embedding providers
│   ├── embedding_cache.py   # LRU cache for task embeddings
│   └── models.py            # Skill, SkillVersion, AgentType, etc.
├── jobs/
│   ├── spawner.py           # K8s Job creation with security context
│   ├── monitor.py           # Redis result polling + K8s status
│   └── safety.py            # Post-run: PR check, anti-stall, queue drain
└── prompt/
    └── builder.py           # System prompt with CLAUDE.md + history

src/mcp/
├── message_queue/
│   └── server.js            # MCP: check_messages + spawn_subagent
└── gateway/
    ├── server.js             # MCP Gateway (Express + SSE transport)
    └── tools/
        ├── file-analysis.js  # Sandboxed file structure analysis
        ├── web-search.js     # Brave Search API client
        ├── db-query.js       # Read-only PostgreSQL queries
        └── index.js          # Tool registry
```

</details>

### Key Design Decisions

- **Claude Code as runtime** — No custom agent loop. Claude Code handles file editing, context management, tool selection, error recovery, and git operations natively.
- **Ephemeral K8s Jobs** — Each task gets a fresh container. No persistent sandboxes, no state leakage. Jobs auto-clean via `ttlSecondsAfterFinished`.
- **Protocol-based backends** — `StateBackend` and `Integration` are Python protocols. Swap Postgres for SQLite, or add a new integration by implementing 4 methods.
- **Skill hotloading** — Controller-side semantic search selects per-task skills from a registry and injects them into the agent workspace before launch. Avoids Claude Code's ~42 skill metadata cap.
- **Three-layer capability model** — Agent Types (Docker images) for coarse capabilities, Skills (injected per-task) for fine-grained instructions, Subagents (child K8s Jobs) for parallel subtasks.
- **MCP Gateway** — Centralized MCP server with per-session tool scoping. Agents connect via SSE, reducing the need for specialized Docker images.
- **Redis for ephemeral state** — Task handoff, result retrieval, message queuing, and gateway scopes use Redis with TTLs. Durable state lives in Postgres/SQLite.
- **Advisory locks** — Prevent duplicate job spawns. Postgres uses `pg_try_advisory_lock`, SQLite uses a locks table.

---

## Configuration

All settings use the `DF_` environment variable prefix.

<details>
<summary><strong>Environment Variables</strong></summary>

| Variable | Default | Description |
|:--|:--|:--|
| `DF_ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `DF_REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `DF_DATABASE_URL` | `postgresql://localhost:5432/aal` | Postgres or `sqlite:///path` |
| `DF_AGENT_IMAGE` | `ditto-factory-agent:latest` | Agent container image |
| `DF_MAX_JOB_DURATION_SECONDS` | `1800` | K8s Job timeout |
| `DF_AUTO_OPEN_PR` | `true` | Auto-create PRs on commits |
| `DF_RETRY_ON_EMPTY_RESULT` | `true` | Retry if agent produces no changes |
| `DF_SLACK_ENABLED` | `false` | Enable Slack integration |
| `DF_GITHUB_ENABLED` | `false` | Enable GitHub integration |
| `DF_LINEAR_ENABLED` | `false` | Enable Linear integration |
| `DF_SKILL_REGISTRY_ENABLED` | `false` | Enable skill hotloading |
| `DF_SKILL_EMBEDDING_PROVIDER` | `none` | Embedding provider (`none` or `voyage`) |
| `DF_VOYAGE_API_KEY` | | Voyage-3 API key for semantic search |
| `DF_GATEWAY_ENABLED` | `false` | Enable MCP Gateway |
| `DF_GATEWAY_URL` | | Gateway service URL |
| `DF_SUBAGENT_ENABLED` | `false` | Enable subagent spawning |

See [`controller/src/controller/config.py`](controller/src/controller/config.py) for the full list.

</details>

---

## Integrations

<table>
<tr>
<td width="33%" valign="top">

### Slack
- Mention the bot or message in a thread
- Follow-ups queue while agent runs, delivered via MCP
- Results posted as thread replies with PR links

</td>
<td width="33%" valign="top">

### GitHub
- Issue comments, new issues, PR reviews
- Org allowlist for security
- Auto-PR creation on commits

</td>
<td width="33%" valign="top">

### Linear
- Comment on an issue to trigger agent
- Team-to-repo mapping for auto resolution
- Results posted as comments via GraphQL

</td>
</tr>
</table>

---

## Security

| Layer | Protection |
|:--|:--|
| **Container isolation** | Non-root (UID 1000), drop all capabilities, no privilege escalation |
| **Network policies** | Agent egress restricted to DNS, HTTPS (GitHub), and Redis only |
| **Webhook verification** | HMAC-SHA256 signature validation for all integrations |
| **Prompt safety** | Untrusted content wrapped in XML tags to prevent injection |
| **Concurrency** | Advisory locks prevent race conditions on duplicate webhooks |

---

<div align="center">

**[MIT License](LICENSE)**

</div>
