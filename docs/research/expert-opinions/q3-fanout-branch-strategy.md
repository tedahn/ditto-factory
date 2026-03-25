# Q3: How Should Fan-Out Agents Handle Git Branches?

| Field       | Value                          |
|-------------|--------------------------------|
| **Date**    | 2026-03-25                     |
| **Author**  | Platform Architect (AI)        |
| **Status**  | Recommendation                 |
| **Related** | ADR-001 (Generalized Task Agents) |

## Problem

The workflow engine fans out to N agents in parallel (e.g., "search 5 regions for events"). These agents produce **data, not code**. The current entrypoint assumes every agent clones a repo, creates a branch, and pushes. This creates three failure modes for fan-out:

1. **Shared branch** -- parallel pushes cause merge conflicts on non-code files
2. **Branch-per-agent** -- 5 PRs for one logical request; nonsensical for JSON results
3. **No git at all** -- breaks the entrypoint and workspace assumptions

## Options Evaluated

### Option A: Skip Git Entirely for Non-Code Tasks

- Non-code agents get a bare `/workspace` directory, no clone
- Results flow directly to Redis/Postgres as structured data
- Requires a second entrypoint mode or significant branching in `entrypoint.sh`

**Trade-offs:**
- (+) Fastest container startup (no clone overhead)
- (+) No branch pollution
- (-) Claude Code still needs a working directory but does NOT require a git repo -- it works fine in a plain directory
- (-) Two fundamentally different agent lifecycles to maintain and debug

### Option B: Git for All, Merge in Workflow Engine

- Every agent clones and gets a branch, but results are NOT committed
- Agent writes results to stdout/Redis; workflow engine aggregates
- Git workspace exists but is not the output channel

**Trade-offs:**
- (+) Uniform entrypoint
- (-) Cloning a repo for agents that never use it wastes 10-30s of startup time and network I/O
- (-) Branches are created but never pushed -- ghost branches if cleanup fails

### Option C: Shared Branch with Locking

- All fan-out agents commit to one branch sequentially

**Trade-offs:**
- (-) Serializes parallel work -- defeats the purpose of fan-out
- (-) Lock contention, deadlock risk, retry complexity
- (-) Architectural mismatch: using a serialization primitive on a parallel workload

**Verdict: Eliminated.** This option has no redeeming qualities for the fan-out case.

### Option D: Output-Type Routing (Recommended)

- `entrypoint.sh` inspects `TASK_TYPE` environment variable
- `code_change` -> full git flow (clone, branch, push, PR) -- current behavior, unchanged
- `analysis` / `scraping` / `file_output` -> lightweight mode: create `/workspace`, skip clone, agent writes results to stdout or a results file, sidecar or post-exec hook ships results to Redis
- ADR-001 already defines these task types; this is the entrypoint implementation of that decision

**Trade-offs:**
- (+) Fast startup for non-code agents (skip clone)
- (+) Aligns with ADR-001's `TaskType` enum -- no new concepts
- (+) Claude Code works fine without git -- it just needs a writable directory
- (+) Fan-out agents write results independently; workflow engine aggregates from Redis
- (+) Reversible: if a non-code task later needs git, add it back for that task type
- (-) Two code paths in entrypoint (mitigated: they share 80% of setup logic)
- (-) Non-code agents lose git-based audit trail (mitigated: Redis + `task_artifacts` table provides equivalent traceability)

## Recommendation: Option D -- Output-Type Routing

### Rationale

The core insight is that **git is an output mechanism, not a workspace requirement**. Claude Code needs a directory to work in, but it does not need that directory to be a git repo. For fan-out agents producing JSON results, git adds latency (clone), complexity (branch management), and confusion (PRs for non-code output) with zero benefit.

Option D is the natural entrypoint-level implementation of ADR-001. The ADR defines `TaskType` at the model layer; this decision defines what `TaskType` means at the infrastructure layer.

### Entrypoint Changes Needed

```bash
# entrypoint.sh -- proposed changes (pseudocode)

TASK_TYPE="${TASK_TYPE:-code_change}"

# --- Shared setup (all task types) ---
mkdir -p /workspace
setup_mcp_gateway
inject_skills

# --- Task-type-specific setup ---
case "$TASK_TYPE" in
  code_change)
    git clone "$REPO_URL" /workspace/repo
    cd /workspace/repo
    git checkout -b "df/${THREAD_ID}/${UUID}"
    # ... existing flow unchanged ...
    ;;
  analysis|scraping|file_output)
    # No git clone. Lightweight workspace.
    mkdir -p /workspace/output
    echo '{"task_id": "'"$TASK_ID"'"}' > /workspace/metadata.json
    # Results written to /workspace/output/ by agent
    # Post-exec hook ships /workspace/output/* to Redis
    ;;
  *)
    echo "Unknown TASK_TYPE: $TASK_TYPE" >&2
    exit 1
    ;;
esac
```

### Fan-Out Result Aggregation

The workflow engine, not the agents, is responsible for aggregation:

```
Fan-out step:
  Agent-1 (region=us-east) --> writes results to Redis key: wf:{workflow_id}:step:{step_id}:agent:1
  Agent-2 (region=us-west) --> writes results to Redis key: wf:{workflow_id}:step:{step_id}:agent:2
  ...
  Agent-N (region=eu-west) --> writes results to Redis key: wf:{workflow_id}:step:{step_id}:agent:N

Fan-in step:
  Workflow engine reads all Redis keys for step_id
  Merges results into single payload
  Passes merged payload to next step (or final report)
```

This keeps agents stateless and unaware of each other -- they write results to a known key pattern and exit.

### What Claude Code Actually Needs

Tested behavior: Claude Code operates in any writable directory. It uses git when present but does not require it. For non-code tasks, a plain `/workspace` directory is sufficient. The agent can:

- Write temporary files
- Run scripts
- Use MCP tools
- Produce structured output to stdout or a results file

None of these require git.

### Migration Path

1. **MVP**: Add `TASK_TYPE` env var to entrypoint. Default to `code_change` (zero behavior change for existing tasks).
2. **Phase 1**: Implement `analysis` path. Fan-out agents use this mode. Results to Redis.
3. **Phase 2**: Add `file_output` path with S3 upload in post-exec hook.
4. **Phase 3**: Converge with ADR-001 Phase 2 implementation.

### Decision Record

This recommendation, if accepted, should become **ADR-002: Output-Type Routing in Agent Entrypoint** with status "Proposed."
