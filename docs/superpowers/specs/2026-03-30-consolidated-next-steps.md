# Ditto Factory — Consolidated Next Steps

Based on reviews from Architecture, Product, and Technical reviewers.

## Phase 0: Bug Fixes (do first)

Must fix before building further — these cause data corruption and broken UX.

### Bug 1: `file_count` always 0
- **Location:** `registry.py` `_row_to_component()` — hardcoded to 0, never queries actual file count
- **Impact:** Frontend shows "0 files" for every component
- **Fix:** Query `toolkit_component_files` count or compute during `import_from_manifest`

### Bug 2: Non-atomic imports
- **Location:** `registry.py` `import_from_manifest()` — creates toolkit row first, then components. If crash/error mid-import, orphaned toolkit with `component_count=0` remains. Re-import skips it (slug exists).
- **Impact:** The 3 failed imports (agency-agents, beads, claude-code-best-practice) are stuck in this state
- **Fix:** Wrap entire import in a single DB transaction. On failure, everything rolls back.

### Bug 3: Seeder can't retry partial failures
- **Location:** `seeder.py` `seed_if_empty()` — checks `if sources exist, skip`. If some sources were created but their toolkits failed to import, seeder never retries.
- **Fix:** Check for sources with 0 toolkits and retry those. Or seed per-source (skip if source+toolkit both exist).

### Bug 4: Version shows internal counter instead of source version
- **Location:** `registry.py` — always sets `version=1`
- **Impact:** All toolkits show "v1" regardless of the actual tool version
- **Fix:** During import, fetch repo's latest release tag from GitHub (or read from package.json/pyproject.toml). Store as `source_version` field. Fall back to branch@SHA if no releases.

## Phase 1: Runtime Path (the critical missing piece)

The activation bridge — how toolkit components reach running agents.

### 1a: Unify toolkit components with skill system
- **Problem:** `toolkits/` and `skills/` are disconnected registries
- **Solution:** When a toolkit component is "activated", create a corresponding `Skill` record linked back via `source_component_id`
- This lets the existing skill classifier, injector, and resolver work unchanged
- Toolkit component is the source of truth; Skill is the runtime projection

### 1b: AgentLoadout concept
- **New model:** `AgentLoadout` — defines what a specific agent pod gets:
  - Skills to mount (`.claude/skills/`)
  - MCP config entries (merged into `mcp.json`)
  - Rules/profiles (injected into CLAUDE.md or `.claude/rules/`)
  - Environment variables
- Loadout is composed from: task requirements + workflow config + activated toolkit components
- The Job Spawner consumes the loadout to configure the pod

### 1c: Mount toolkit files into agent pod
- **Location:** Job Spawner (`controller/src/controller/jobs/spawner.py`)
- When spawning an agent pod:
  1. Resolve which toolkit components are needed (from loadout)
  2. Write component files to a ConfigMap or init container
  3. Mount into the pod at `.claude/skills/`, `.claude/rules/`, etc.
  4. Merge MCP configs into the pod's `mcp.json`

## Phase 2: Structured Output + Agent-Driven Onboarding

### 2a: Add `structured_output` result type
- **Problem:** Workflow engine can produce PRs and reports but can't write structured data back into internal tables
- **Solution:** New `ResultType.STRUCTURED_OUTPUT` — agent returns JSON that the controller parses and acts on
- For toolkit onboarding: agent returns a manifest JSON, controller imports it

### 2b: Built-in toolkit onboarding workflow
- **Task type:** `toolkit_onboarding`
- **Built-in skill:** Teaches the agent how to analyze a repo, classify components, map relationships
- **Flow:**
  1. User provides GitHub URL via dashboard
  2. Controller creates a task with `toolkit_onboarding` type
  3. Agent pod clones the repo (zero API calls)
  4. Agent reads README, configs, directory structure
  5. Agent produces structured manifest JSON
  6. Controller receives manifest via `structured_output`
  7. Controller imports into registry
  8. User reviews in dashboard

### 2c: Replace discovery.py with agent-driven discovery
- Current `discovery.py` (pattern matching) becomes a fallback for when no agent is available
- Primary discovery path goes through the onboarding workflow
- Seeder uses the agent path on first startup (if Anthropic API key is set)

## Phase 3: Workflow Composition (Flow 2)

### 3a: Toolkit selection in workflow templates
- Workflow template definition gains a `toolkits` field: which toolkit components this workflow needs
- The workflow compiler resolves components → loadout for each step's agent

### 3b: Task submission with toolkit selection
- Task submission form gains "Toolkits" picker: browse available components, attach to task
- Classifier can also auto-suggest toolkits based on task description

## Execution Order

```
Phase 0 (bugs)     → Fix file_count, atomic imports, seeder retry, version
Phase 1a           → Activation bridge (toolkit component → skill)
Phase 1b           → AgentLoadout model
Phase 1c           → Mount files in Job Spawner
Phase 2a           → Structured output result type
Phase 2b           → Onboarding workflow + skill
Phase 2c           → Replace discovery.py
Phase 3            → Workflow composition
```

Phase 0 is prerequisite for everything. Phase 1 is the critical path to value. Phase 2 enables dogfooding. Phase 3 completes the vision.
