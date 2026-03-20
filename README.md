# Ditto Factory

**Kubernetes-native coding agent platform using headless Claude Code.**

Ditto Factory is a self-hostable platform that turns Slack messages, GitHub issues, and Linear comments into autonomous coding agents. Each agent runs as an ephemeral K8s Job with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) as the runtime — no proprietary orchestration layers, no vendor lock-in beyond the Anthropic API.

## Why

Internal coding agents at companies like Stripe, Ramp, and Coinbase share a common pattern: they meet engineers where they work (Slack, GitHub, Linear), run in isolated sandboxes, and report back with PRs. Ditto Factory implements this pattern with minimal dependencies — just the Anthropic API and standard K8s infrastructure.

| Concern | Ditto Factory |
|---|---|
| **Agent runtime** | Headless Claude Code (`claude -p`) |
| **Orchestration** | FastAPI controller + K8s Jobs |
| **Sandboxes** | Ephemeral Docker containers |
| **State** | PostgreSQL/SQLite + Redis |
| **Deployment** | Helm chart (any K8s cluster) |
| **Paid dependencies** | Anthropic API only |

## How It Works

```
Slack/GitHub/Linear webhook
        │
        ▼
┌──────────────────┐
│  FastAPI Controller │  ← Verifies signatures, parses webhooks
│  (Deployment)       │  ← Manages threads, conversations, locks
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌─────────────┐
│  K8s Job Spawner │────▶│  Agent Pod  │  ← Ephemeral container
└──────────────────┘     │  claude -p  │  ← Clones repo, makes changes
                         │  + MCP      │  ← Polls for follow-up messages
                         └──────┬──────┘
                                │
                                ▼
                     ┌────────────────┐
                     │  Safety Pipeline │  ← Auto-PR, anti-stall retry
                     │  → Report back  │  ← Posts results to Slack/GH/Linear
                     └────────────────┘
```

1. **Receive** — Webhook arrives from Slack, GitHub, or Linear
2. **Resolve** — Derive a deterministic thread ID, create or resume conversation
3. **Lock** — Advisory lock prevents duplicate spawns for the same thread
4. **Spawn** — Create an ephemeral K8s Job running `claude -p` with the task
5. **Monitor** — Poll Redis for the agent's result
6. **Safety** — Auto-create PR if commits exist, retry if agent stalled, drain queued follow-ups
7. **Report** — Post result back to the originating integration

## Quick Start

### Local Development (Docker Compose)

```bash
# Clone and start
git clone https://github.com/tedahn/ditto-factory.git
cd ditto-factory
docker compose up -d

# Verify
curl http://localhost:8000/health
# → {"status":"ok"}
```

This starts the controller with SQLite (no Postgres needed) and Redis.

### Kubernetes (Helm)

```bash
helm install ditto-factory ./charts/ditto-factory \
  --set secrets.anthropicApiKey=$ANTHROPIC_API_KEY \
  --set secrets.slackSigningSecret=$SLACK_SIGNING_SECRET \
  --set secrets.slackBotToken=$SLACK_BOT_TOKEN
```

The Helm chart includes PostgreSQL, Redis (via Bitnami subcharts), RBAC for Job creation, network policies for agent egress control, and optional ingress.

### Running Tests

```bash
cd controller
uv pip install -e ".[dev]"
uv run pytest tests/ -v          # 106 tests, ~1 second

# K8s live tests (requires running cluster + Redis)
AAL_K8S_LIVE_TEST=1 uv run pytest tests/e2e/test_k8s_live.py -v
```

## Architecture

```
controller/src/controller/
├── main.py                 # FastAPI app, lifespan, webhook routing
├── config.py               # Pydantic Settings (DF_ env prefix)
├── models.py               # TaskRequest, AgentResult, Thread, Job
├── orchestrator.py          # Core lifecycle: receive → spawn → complete
├── state/
│   ├── protocol.py         # StateBackend protocol (swappable)
│   ├── postgres.py         # Production backend (asyncpg)
│   ├── sqlite.py           # Local dev backend (aiosqlite)
│   └── redis_state.py      # Ephemeral state (task handoff, queues)
├── integrations/
│   ├── protocol.py         # Integration protocol
│   ├── registry.py         # Dynamic webhook router
│   ├── slack.py            # Slack: signatures, bot filtering, threading
│   ├── github.py           # GitHub: 4 event types, org allowlist, auto-PR
│   ├── linear.py           # Linear: GraphQL, team-to-repo mapping
│   ├── thread_id.py        # Deterministic SHA256 thread IDs
│   └── sanitize.py         # Untrusted content wrapping
├── jobs/
│   ├── spawner.py          # K8s Job creation with security context
│   ├── monitor.py          # Redis result polling + K8s status
│   └── safety.py           # Post-run: PR check, anti-stall, queue drain
└── prompt/
    └── builder.py          # System prompt with CLAUDE.md + history
```

### Key Design Decisions

- **Claude Code as agent runtime** — No custom agent loop. Claude Code handles file editing, context management, tool selection, error recovery, and git operations natively. We just invoke `claude -p` with a system prompt.
- **Ephemeral K8s Jobs** — Each task gets a fresh container. No persistent sandboxes, no state leakage between tasks. Jobs auto-clean via `ttlSecondsAfterFinished`.
- **Protocol-based backends** — `StateBackend` and `Integration` are Python protocols. Swap Postgres for SQLite, add a new integration by implementing 4 methods.
- **Redis for ephemeral state** — Task handoff, result retrieval, and message queuing use Redis with TTLs. Durable state lives in Postgres/SQLite.
- **Advisory locks** — Prevent duplicate job spawns for the same thread. Postgres uses `pg_try_advisory_lock`, SQLite uses a locks table.

## Configuration

All settings use the `DF_` environment variable prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `DF_ANTHROPIC_API_KEY` | (required) | Anthropic API key |
| `DF_REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `DF_DATABASE_URL` | `postgresql://localhost:5432/aal` | Postgres or `sqlite:///path` |
| `DF_AGENT_IMAGE` | `ditto-factory-agent:latest` | Agent container image |
| `DF_MAX_JOB_DURATION_SECONDS` | `1800` | K8s Job timeout |
| `DF_AUTO_OPEN_PR` | `true` | Auto-create PRs on commits |
| `DF_RETRY_ON_EMPTY_RESULT` | `true` | Retry if agent produces no changes |
| `DF_SLACK_ENABLED` | `false` | Enable Slack integration |
| `DF_GITHUB_ENABLED` | `false` | Enable GitHub integration |
| `DF_LINEAR_ENABLED` | `false` | Enable Linear integration |

See `controller/src/controller/config.py` for the full list.

## Integrations

### Slack
- Mention the bot or message in a thread → agent picks up the task
- Follow-up messages queue while agent is running, delivered via MCP
- Results posted as thread replies with PR links

### GitHub
- Issue comments, new issues, PR review comments, PR reviews
- Org allowlist for security
- Auto-PR creation when agent pushes commits

### Linear
- Comment on an issue → agent works on it
- Team-to-repo mapping for automatic repo resolution
- Results posted as Linear comments via GraphQL

## Security

- **Agent containers** run as non-root (UID 1000), drop all capabilities, no privilege escalation
- **Network policies** restrict agent egress to DNS, HTTPS (GitHub), and Redis only
- **Webhook signatures** verified for all integrations (HMAC-SHA256)
- **Untrusted content** wrapped in XML tags to prevent prompt injection
- **Advisory locks** prevent race conditions on concurrent webhook delivery

## License

MIT
