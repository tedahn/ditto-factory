# Agent-Driven Development Playbook

> A reusable process for executing complex, multi-phase software projects using Claude Code with parallel agent dispatch. Based on the Ditto Factory Skill Hotloading System implementation (March 2026).

## 1. Overview

### What This Playbook Is For

This playbook captures a structured workflow for designing and implementing large software features using AI agents as parallel workers. It is designed for projects that are:

- Too large for a single agent session (>500 lines of code across multiple modules)
- Decomposable into independent work streams
- Complex enough to require spec writing, review, and iterative refinement

### When to Use It

Use this playbook when your project has **three or more** of these characteristics:

- Multiple new modules or services to create
- Cross-cutting concerns (database, API, config, tests)
- Architectural decisions that need evaluation before committing
- A need for parallel implementation to reduce wall-clock time
- Integration points between independently developed components

### The Core Loop

```
Discovery --> Brainstorm --> Spec --> Review --> Implement --> Integrate --> Test --> Ship
    |                                   |           |             |           |
    v                                   v           v             v           v
  Explore                            Fix agent   Parallel     Cherry-pick   Fix loops
  agents                             dispatched  worktree     + conflict    until green
                                                 agents       resolution
```

---

## 2. Prerequisites

### Tools Required

| Tool | Purpose | Required? |
|------|---------|-----------|
| Claude Code CLI | Agent dispatch, code generation, reviews | Yes |
| Git worktrees | Parallel agent isolation | Yes |
| GitHub CLI (`gh`) | PR creation, issue tracking | Yes |
| Task tracking (TaskCreate/TaskUpdate) | Progress visibility | Recommended |
| Feature flags in your codebase | Incremental rollout | Recommended |

### Skills / Slash Commands Used

| Skill | When Used |
|-------|-----------|
| `/brainstorm` | Phase 1: Requirements gathering through iterative Q&A |
| `/ditto` | Agent dispatch for implementation tasks |
| `/explore` | Phase 0: Codebase mapping and research |
| Code review agents | Phase 2: Spec and code review |

### Team Setup

- **One human operator** who makes architectural decisions and approves specs
- **Multiple AI agents** dispatched in parallel for implementation
- **One integration branch** where all agent work is merged

---

## 3. The Process

### Phase 0: Discovery and Research

**Goal**: Understand the current state of the codebase and the problem space before proposing solutions.

**Duration**: 1-2 hours

#### Steps

1. **Map the existing system.** Dispatch an Explore agent to understand the current architecture. In the Skill Hotloading project, this revealed the existing static SKILL.md system, its 42-skill cap, lack of semantic search, and silent failure modes.

   ```
   Dispatch: Explore agent
   Prompt: "Map the current tool/skill selection system in this codebase.
   Identify: how skills are selected, where they're stored, how they're
   injected into agent sessions, and what the current limitations are."
   ```

2. **Research prior art.** If applicable, research existing solutions in the problem space. The Skill Hotloading project researched Coinbase AgentKit and academic literature on skill scalability.

   ```
   Dispatch: Research agent
   Prompt: "Research [comparable system/framework]. Focus on:
   how it solves [specific problem], its limitations for our use case,
   and what we can learn from its architecture."
   ```

3. **Document findings.** Consolidate the Explore agent's output into a brief problem statement that identifies:
   - Current system capabilities and limitations
   - Specific pain points with quantified impact (e.g., "42-skill cap means no room for growth")
   - Constraints the new system must respect (backward compatibility, deployment model)

#### Decision Point

> **Proceed when**: You can articulate the problem clearly, you know what the current system does, and you have a sense of the solution space.

---

### Phase 1: Brainstorming and Approach Selection

**Goal**: Generate multiple architectural approaches and select one (or a phased combination).

**Duration**: 2-4 hours

#### Steps

1. **Run a brainstorming session.** Use an iterative, one-question-at-a-time dialogue to refine requirements. Avoid presenting a solution upfront. Instead, let the requirements emerge through structured questioning.

   Key questions to answer during brainstorming:
   - What are the hard requirements vs. nice-to-haves?
   - What is the deployment model? (monolith, microservices, K8s)
   - What are the scaling constraints?
   - What existing interfaces must be preserved?

   In the Skill Hotloading project, brainstorming identified the three-layer architecture (Agent Types, Skills, Subagents) and the need for both tag-based and semantic skill matching.

2. **Dispatch parallel architect agents.** Once requirements are clear, dispatch 3 agents to propose distinct approaches. Each agent gets the same requirements but a different architectural direction.

   ```
   Dispatch: 3 Software Architect agents (parallel)

   Agent 1 prompt: "Design Approach A: [direction]. Requirements: [shared].
   Produce a detailed design doc covering: architecture, components,
   data model, integration points, phased rollout, effort estimates,
   risks, and trade-offs."

   Agent 2 prompt: "Design Approach B: [different direction]. Requirements: [shared]. ..."
   Agent 3 prompt: "Design Approach C: [third direction]. Requirements: [shared]. ..."
   ```

   In the Skill Hotloading project, the three approaches were:

   | Approach | Core Idea | Strengths | Weaknesses |
   |----------|-----------|-----------|------------|
   | **A: Skill Registry** | Controller-side registry with semantic search | Simplest to implement, fits current architecture | No runtime tool augmentation |
   | **B: Agent Type Matrix** | Composable skill packs per agent type | Clean separation of concerns | Over-engineers the type system |
   | **C: Hybrid Platform** | Remote MCP gateway with dynamic tool injection | Most powerful long-term | Highest complexity, new service to operate |

3. **Evaluate and select.** Compare approaches on these axes:
   - Implementation effort (days/weeks)
   - Architectural risk (new services, new dependencies)
   - Incremental value (can you ship Phase 1 independently?)
   - Long-term extensibility

   The Skill Hotloading project chose: **"Start with A, evolve toward C"** -- meaning Phase 1-3 followed Approach A's design, while Phases 4-5 added Approach C's MCP gateway.

#### Decision Point

> **Proceed when**: The human operator has reviewed all approaches and approved a direction. The decision should be recorded in a brief note (even a one-liner like "Approved: Start A, evolve C").

---

### Phase 2: Spec Writing and Review Loop

**Goal**: Produce a consolidated design specification that is accurate, implementable, and reviewed against the actual codebase.

**Duration**: 2-4 hours

#### Steps

1. **Dispatch a spec-writing agent.** This agent takes the approved approach and writes a full design specification.

   ```
   Dispatch: Software Architect agent
   Prompt: "Write a consolidated design specification for [feature].
   Approved direction: [selected approach].
   Include: executive summary, requirements, architecture diagrams,
   detailed component design, data model (SQL), integration points,
   phased rollout plan with effort estimates, feature flags, and
   testing strategy."
   ```

   The output should be a single markdown file saved to `docs/specs/`.

2. **Dispatch a review agent.** This agent reviews the spec against the actual codebase to find misalignments.

   ```
   Dispatch: Code Reviewer agent
   Prompt: "Review this design spec against the current codebase.
   For each section, verify:
   - Do referenced files/modules actually exist?
   - Are function signatures and class hierarchies accurate?
   - Are integration points correctly described?
   - Is the data model compatible with existing schemas?
   Flag issues as: [Critical], [Important], or [Suggestion]."
   ```

   In the Skill Hotloading project, the review found 8 issues including:
   - IVFFlat index created on an empty table (should use HNSW instead)
   - Missing migration for the `jobs` table (dataclass-based models, not ORM)
   - UUID type inconsistency between `skill_usage.job_id` and `Job.id`

3. **Fix issues.** Dispatch a fix agent to address Critical and Important issues in the spec.

   ```
   Dispatch: Fix agent
   Prompt: "Apply these fixes to the design spec at [path]:
   1. [Issue description] --> [Fix]
   2. [Issue description] --> [Fix]
   ..."
   ```

4. **Human review.** The operator reads the final spec and approves it. This is the last gate before implementation begins.

#### Decision Point

> **Proceed when**: All Critical issues are resolved, the human has read and approved the spec, and the phased rollout plan has clear boundaries between phases.

---

### Phase 3: Implementation via Parallel Agents

**Goal**: Implement the design spec in phases, using parallel agents isolated in git worktrees.

**Duration**: Varies (the Skill Hotloading project took 5 implementation phases)

#### The Worktree Pattern

Each agent works in its own git worktree to prevent file conflicts during parallel execution. The workflow is:

```
main branch
    |
    +-- feature/skill-hotloading  (integration branch)
          |
          +-- worktree-1/  (Agent 1: data layer)
          +-- worktree-2/  (Agent 2: core services)
          +-- worktree-3/  (Agent 3: API endpoints)
          +-- worktree-4/  (Agent 4: integration wiring)
```

#### Steps Per Implementation Phase

1. **Decompose the phase into independent work domains.** Each domain becomes one agent's task. The key constraint: **agents must not modify the same files**. If two agents need to change the same file, they must be sequenced, not parallelized.

   Example decomposition from Phase 1 (MVP):

   | Agent | Domain | Files Created/Modified |
   |-------|--------|----------------------|
   | 1 | Data layer | `models.py`, `migrations/`, `config.py` |
   | 2 | Core services | `registry.py`, `classifier.py`, `injector.py`, `resolver.py`, `tracker.py` |
   | 3 | API endpoints | `api.py`, `routes/skills.py` |
   | 4 | Integration | `orchestrator.py`, `spawner.py`, `entrypoint.sh`, `main.py` |

2. **Create worktrees.** One per agent, branching from the integration branch.

   ```bash
   git worktree add ../worktree-agent1 -b feat/phase1-data feature/skill-hotloading
   git worktree add ../worktree-agent2 -b feat/phase1-services feature/skill-hotloading
   git worktree add ../worktree-agent3 -b feat/phase1-api feature/skill-hotloading
   git worktree add ../worktree-agent4 -b feat/phase1-integration feature/skill-hotloading
   ```

3. **Dispatch agents.** Each agent gets:
   - The design spec (or relevant section)
   - The specific files it is responsible for
   - A clear instruction to commit its work when done

   ```
   Dispatch: Implementer agent (in worktree-agent1)
   Prompt: "Implement the data layer for the Skill Hotloading system.
   Design spec: [path or inline section].
   Create these files:
   - controller/src/controller/skills/models.py (dataclass models)
   - controller/src/controller/skills/migrations/ (SQL migration files)
   - Update config.py with new configuration fields
   Write tests for all models. Commit when done."
   ```

4. **Monitor and wait.** All agents run in parallel. Check their progress periodically.

5. **Cherry-pick into integration branch.** Once all agents complete, cherry-pick their commits into the integration branch.

   ```bash
   git checkout feature/skill-hotloading
   git cherry-pick <agent1-commit-hash>
   git cherry-pick <agent2-commit-hash>
   git cherry-pick <agent3-commit-hash>
   git cherry-pick <agent4-commit-hash>
   ```

6. **Resolve conflicts.** Conflicts are expected and normal. See Section 5 (Merge Strategy) for resolution patterns.

7. **Run tests.** After all cherry-picks, run the full test suite.

   ```bash
   cd controller && python -m pytest tests/ -v
   ```

8. **Fix test failures.** Dispatch a fix agent if tests fail. This often takes 2-3 rounds after a multi-agent merge.

9. **Create PR.** Once tests pass, create a PR for this phase.

   ```bash
   gh pr create --title "feat: Phase 1 MVP - Skill Registry data layer and core services" \
     --body "## Summary\n- Skill registry with tag-based matching\n- ..."
   ```

#### Real Example: Phase-by-Phase Execution

| Phase | Agents | Key Components | Tests | PR |
|-------|--------|---------------|-------|-----|
| 1: MVP | 4 parallel | Models, registry, classifier, injector, resolver, tracker, API, wiring | 45 | #1 |
| 2: Semantic Search | 3 parallel | Embedding provider (Voyage-3 + NoOp + cache), registry upgrade, main.py wiring | 78 | #2 |
| 3: Performance Tracking | 1 sequential | Tracker upgrade, boost algorithm, metrics API | 89 | #2 |
| 4+5: Subagents + Gateway | 2 parallel | MCP tool + handler + spawner, Express server + K8s manifests | 102 | #3 |

---

### Phase 4: Integration and Conflict Resolution

**Goal**: Merge agent work cleanly and resolve the inevitable conflicts.

See Section 5 (Merge Strategy) for detailed procedures.

---

### Phase 5: Remaining Work Identification and Execution

**Goal**: Identify gaps, plan fixes, and execute them in prioritized waves.

**Duration**: 1-2 days

After the main implementation phases, there are always loose ends. The Skill Hotloading project identified 8 remaining items through systematic analysis.

#### Steps

1. **Identify remaining work.** Review the codebase against the spec and look for:
   - Incomplete integrations (e.g., gateway wired but no tool backends)
   - Duplicate or conflicting code from parallel agent merges
   - Missing tests (especially E2E)
   - Documentation gaps
   - Open design questions that were deferred

2. **Dispatch planning agents (one per item).** Each agent produces a detailed plan for its item.

   ```
   Dispatch: 8 Planning agents (parallel, one per remaining item)
   Prompt: "Create a detailed implementation plan for: [item].
   Include: scope, approach, file changes, effort estimate,
   dependencies on other remaining items, and risks."
   ```

3. **Dispatch a reviewer agent.** Reviews all plans holistically for cross-cutting concerns and establishes a dependency order.

   ```
   Dispatch: Reviewer agent
   Prompt: "Review these 8 implementation plans holistically.
   Identify: cross-plan dependencies, conflicting approaches,
   shared file modifications, and the optimal execution order.
   Produce a prioritized execution sequence with parallelization
   opportunities."
   ```

4. **Execute in waves.** Group items by dependency and effort:

   | Wave | Items | Effort | Strategy |
   |------|-------|--------|----------|
   | 1 (trivial) | Config dedup, docs commit | 30 min each | 2 parallel agents |
   | 2 (medium) | Registry merge, gateway wiring, E2E tests, seed skills | Hours each | 4 parallel agents |
   | 3 (large) | Open questions, gateway backends | Days each | 2 parallel agents |

   Each wave follows the same pattern: dispatch, merge, test, fix, commit, push, PR.

#### Decision Point

> **Proceed to ship when**: All waves are complete, tests pass, and the human has verified the integration works end-to-end.

---

### Phase 6: PR Creation and Delivery

**Goal**: Ship the work as reviewable, well-documented pull requests.

#### PR Strategy

- **One PR per implementation phase** (not one PR for the entire project). This keeps reviews manageable.
- **Squash or keep commits** depending on team preference. The Skill Hotloading project kept individual commits for traceability.
- **Include in every PR body**:
  - Summary of what changed and why
  - Test results (count and status)
  - Feature flag(s) that control the new functionality
  - Migration instructions if applicable

#### Example PR Sequence

| PR | Title | Tests | Feature Flag |
|----|-------|-------|-------------|
| #1 | Phase 1 MVP: Skill Registry with tag-based matching | 45 passing | `skill_registry_enabled` |
| #2 | Phases 2+3: Semantic search and performance tracking | 89 passing | `skill_embedding_provider` |
| #3 | Phases 4+5: Subagent spawning and MCP gateway | 102 passing | `subagent_enabled`, `gateway_enabled` |
| #4 | Remaining work: Config cleanup, registry merge, docs | 134 passing | -- |
| #5 | Remaining work: Gateway backends, E2E tests, seed skills | 142 passing | -- |

---

## 4. Agent Dispatch Patterns

### When to Use Parallel vs. Sequential

| Situation | Pattern | Reason |
|-----------|---------|--------|
| Agents modify different files | **Parallel** in worktrees | No conflict risk, maximum speed |
| Agents modify the same file | **Sequential** | Conflicts are expensive to resolve |
| One agent's output is another's input | **Sequential** | Dependency chain |
| Spec writing then review | **Sequential** | Review needs the spec to exist |
| Multiple approach proposals | **Parallel** | Independent creative work |
| Planning for N independent items | **Parallel** | Each plan is self-contained |

### When to Use Worktrees vs. Direct

| Situation | Approach |
|-----------|----------|
| Implementation agents writing code | **Worktrees** -- always isolate |
| Spec writing agents | **Direct** -- single file output, no conflict risk |
| Review agents | **Direct** -- read-only, produces a review document |
| Fix agents | **Direct** -- targeted edits to specific files |

### Prompt Templates

#### Explorer Agent

```
You are a codebase exploration agent. Your job is to map the architecture
of [system/module] in this codebase.

Investigate:
1. Entry points: Where does execution start?
2. Data flow: How does data move through the system?
3. Key abstractions: What are the main classes/interfaces?
4. Configuration: How is the system configured?
5. Limitations: What are the current constraints or pain points?

Output: A structured summary with file paths, class names, and a
data flow diagram. Save to docs/explorations/[name].md.
```

#### Software Architect Agent

```
You are a software architect. Design [Approach X] for [feature].

Requirements:
[paste requirements]

Your design document must include:
1. Executive summary (1 paragraph)
2. Architecture diagram (ASCII or Mermaid)
3. Component breakdown with responsibilities
4. Data model (SQL schema if applicable)
5. Integration points with existing codebase
6. Phased rollout plan with effort estimates per phase
7. Feature flags for incremental enablement
8. Risk assessment and mitigation strategies
9. Testing strategy

Save to docs/plans/approach-[x]-[name].md.
```

#### Implementer Agent

```
You are an implementation agent. Build [component] for the
[feature] system.

Design spec: [path]
Your scope: [specific section of the spec]

Files to create or modify:
- [file1]: [what it should contain]
- [file2]: [what it should contain]

Constraints:
- Do NOT modify files outside your scope
- Write unit tests for all new code
- Use feature flags: [flag_name] = False by default
- Follow existing code patterns in the codebase
- Commit your work when done with a descriptive message

Test command: cd controller && python -m pytest tests/ -v
```

#### Reviewer Agent

```
You are a code/spec reviewer. Review [artifact] against the
current codebase.

For each section, check:
1. Do referenced files, classes, and functions exist?
2. Are function signatures and type hints accurate?
3. Are integration points correctly described?
4. Is the data model compatible with existing schemas?
5. Are there missing edge cases or error handling?

Categorize each issue as:
- [Critical]: Blocks implementation, must fix
- [Important]: Will cause problems, should fix
- [Suggestion]: Improvement, can defer

Save review to docs/reviews/[date]-[name]-review.md.
```

---

## 5. Merge Strategy

### The Cherry-Pick Workflow

When multiple agents work in parallel worktrees, their commits must be integrated into a single branch. Cherry-picking gives you control over the order and lets you resolve conflicts incrementally.

```bash
# 1. Switch to integration branch
git checkout feature/skill-hotloading

# 2. Cherry-pick in dependency order (data layer first, integration last)
git cherry-pick <data-layer-commit>       # Usually clean
git cherry-pick <core-services-commit>    # Usually clean
git cherry-pick <api-commit>              # May conflict on imports
git cherry-pick <integration-commit>      # Most likely to conflict

# 3. If a cherry-pick conflicts:
git status                                # See conflicting files
# Edit conflicts manually
git add <resolved-files>
git cherry-pick --continue
```

### Conflict Resolution Patterns

#### Pattern 1: Import Conflicts

The most common conflict. Two agents add different imports to the same file.

**Resolution**: Keep both import sets. Remove duplicates.

#### Pattern 2: Config Duplication

Multiple agents add configuration fields independently, resulting in duplicate entries.

**Resolution**: Keep one canonical location for each config group. In the Skill Hotloading project, three agents independently added `skill_registry_enabled` to `config.py`, resulting in the field appearing three times. The fix was to keep the first occurrence (in the logical feature-flag section) and remove the duplicates.

**Prevention**: In agent prompts, specify "Add config fields to the `# Skill Registry` section of config.py, after line N."

#### Pattern 3: Module Rewrites

When Phase 2 rewrites a module that Phase 1 created, the cherry-pick sees the entire file as a conflict.

**Resolution**: Use merge (not rebase) when a file was substantially rewritten. In the Skill Hotloading project, the Phase 2 `registry.py` was a complete rewrite of Phase 1's version. Rebasing would have required resolving the same conflict across 10+ commits. A single merge commit resolved it once.

| Factor | Merge | Rebase |
|--------|-------|--------|
| Conflict resolution | Resolve once | Resolve per-commit |
| History | Preserves commit sequence | Cleaner linear history |
| Reversibility | Easy to revert | Hard to undo |
| Risk for rewrites | Low | High |

#### Pattern 4: Entrypoint/Wiring Conflicts

Integration files like `main.py`, `entrypoint.sh`, and `__init__.py` are touched by nearly every agent.

**Resolution**: Designate one agent as the "integration agent" responsible for all wiring files. Other agents create their modules but do not wire them into the application. The integration agent does all the wiring in a single commit.

### Test Fix Loops

After merging, tests will often fail due to incompatibilities between independently developed components. This is expected.

```
Cherry-pick all agents
        |
        v
    Run tests -----> Pass? --> Create PR
        |
        v (fail)
  Dispatch fix agent
        |
        v
    Run tests -----> Pass? --> Create PR
        |
        v (fail)
  Dispatch fix agent (round 2)
        |
        v
    Run tests -----> Pass? --> Create PR (usually passes by round 2-3)
```

Common test failure causes after merge:
- **Missing imports**: Agent A created a module, Agent B imports it, but the import path is slightly wrong
- **Interface mismatches**: Agent A defines `registry.find_skills(task)`, Agent B calls `registry.search(task_description)`
- **Config key disagreements**: Agent A reads `config.embedding_provider`, Agent B writes `config.skill_embedding_provider`
- **Test isolation**: Agent A's tests mock a module that Agent B replaced with a real implementation

---

## 6. Quality Gates

### Gate 1: Post-Discovery

| Check | Criteria |
|-------|----------|
| Problem statement documented? | Yes, with quantified pain points |
| Current system mapped? | File paths, data flows, limitations identified |
| Prior art researched? | At least one comparable system analyzed |

### Gate 2: Post-Approach Selection

| Check | Criteria |
|-------|----------|
| Multiple approaches proposed? | Minimum 2, ideally 3 |
| Trade-offs documented? | Effort, risk, extensibility compared |
| Human approved direction? | Explicit approval recorded |

### Gate 3: Post-Spec Review

| Check | Criteria |
|-------|----------|
| Spec reviewed against codebase? | Reviewer agent checked file existence, signatures, schemas |
| Critical issues resolved? | Zero Critical issues remaining |
| Important issues addressed? | All Important issues fixed or explicitly deferred with rationale |
| Human approved spec? | Explicit approval recorded |

### Gate 4: Post-Implementation (per phase)

| Check | Criteria |
|-------|----------|
| All agents completed? | Every dispatched agent committed its work |
| Cherry-picks clean? | All commits integrated, conflicts resolved |
| Tests pass? | Full test suite green |
| Feature flags default off? | New functionality disabled by default |
| PR created? | With summary, test counts, and flag documentation |

### Gate 5: Post-Remaining Work

| Check | Criteria |
|-------|----------|
| No duplicate code from merges? | Config dedup, import cleanup done |
| E2E test exists? | At least one end-to-end test covering the happy path |
| Documentation updated? | Architecture diagrams, README, API docs current |
| Seed data present? | Default/starter data for the new system |

---

## 7. Anti-Patterns and Lessons Learned

### Anti-Pattern 1: Duplicate Config Fields

**What happened**: Three agents independently added `skill_registry_enabled` to `config.py`. After cherry-picking, the field appeared three times, and Python used the last definition silently.

**Root cause**: Agent prompts did not specify where in `config.py` to add fields.

**Fix**: Be explicit in prompts: "Add config fields to the `# Skill Registry` section of `config.py`, lines 47-54. Do not create a new section."

**Prevention**: Include a "files you may modify" and "files you must not modify" list in every agent prompt.

---

### Anti-Pattern 2: Registry Rewrite Conflicts

**What happened**: Phase 1 created `registry.py` with tag-based matching (~200 lines). Phase 2 rewrote it entirely for semantic search (~574 lines). Cherry-picking Phase 2 on top of Phase 1 showed the entire file as a conflict.

**Root cause**: Phase 2 was designed as a rewrite rather than an extension.

**Fix**: Used merge instead of rebase, resolving the conflict in a single merge commit.

**Prevention**: Design phases as additive extensions, not rewrites. Phase 2's registry should have added an `EmbeddingMixin` or `SemanticSearchStrategy` rather than rewriting the whole class. Alternatively, accept the rewrite and plan for a merge-based integration.

---

### Anti-Pattern 3: Test Incompatibilities Across Phases

**What happened**: Phase 1 tests mocked the registry with `MagicMock()`. Phase 2 changed the registry interface (new methods, different return types). After merging, Phase 1 tests failed because mocks did not match the new interface.

**Root cause**: Each phase's agents wrote tests against their own implementation, not the integrated system.

**Fix**: Three rounds of test-fix dispatches. Each round identified 5-10 test failures, dispatched a fix agent, and re-ran.

**Prevention**: Include a "test compatibility" section in each phase's spec: "Phase 2 must ensure all Phase 1 tests continue to pass, updating mocks as needed."

---

### Anti-Pattern 4: Entrypoint Conflicts

**What happened**: Multiple agents modified `entrypoint.sh` and `main.py` to wire their components. After cherry-picking, these files had overlapping changes.

**Root cause**: Wiring/integration files are inherently shared resources.

**Fix**: Designated one "integration agent" per phase to handle all wiring.

**Prevention**: Always assign wiring files (`main.py`, `entrypoint.sh`, `__init__.py`, `Dockerfile`) to exactly one agent. Other agents create modules but do not import or register them.

---

### Anti-Pattern 5: IVFFlat Index on Empty Table

**What happened**: The spec created an IVFFlat vector index in the migration. IVFFlat requires existing rows to build an effective index -- creating it on an empty table results in poor recall.

**Root cause**: Spec author chose IVFFlat without considering the cold-start scenario.

**Fix**: Switched to HNSW index, which works well on small datasets.

**Prevention**: Spec review agents should check data-dependent operations (indexes, materialized views, caches) against initial state.

---

## 8. Templates

### Project Kickoff Checklist

```markdown
## Project: [Name]

### Discovery
- [ ] Existing system mapped (files, data flows, limitations)
- [ ] Problem statement written with quantified pain points
- [ ] Prior art researched

### Design
- [ ] Requirements documented via brainstorming session
- [ ] N approaches proposed by architect agents
- [ ] Approach selected and approved by human
- [ ] Design spec written and saved to docs/specs/
- [ ] Spec reviewed against codebase
- [ ] Critical issues fixed
- [ ] Human approved final spec

### Implementation Plan
- [ ] Phases defined with clear boundaries
- [ ] Each phase decomposed into independent agent domains
- [ ] File ownership matrix created (which agent touches which files)
- [ ] Feature flags identified (one per major capability)
- [ ] Test strategy defined (unit, integration, E2E)

### Execution (per phase)
- [ ] Worktrees created
- [ ] Agents dispatched
- [ ] All agents completed
- [ ] Cherry-picks done, conflicts resolved
- [ ] Tests pass
- [ ] PR created

### Remaining Work
- [ ] Gaps identified
- [ ] Plans created (one per item)
- [ ] Plans reviewed holistically
- [ ] Execution waves defined
- [ ] All waves completed
- [ ] Final test suite passes
```

### File Ownership Matrix Template

```markdown
## Phase [N] File Ownership

| File | Agent | Action | Notes |
|------|-------|--------|-------|
| `models.py` | Agent 1 | Create | New dataclass models |
| `registry.py` | Agent 2 | Create | Core registry service |
| `classifier.py` | Agent 2 | Create | Task classification |
| `api.py` | Agent 3 | Create | REST endpoints |
| `main.py` | Agent 4 | Modify | Wire new services |
| `config.py` | Agent 1 | Modify | Add config fields (lines 47-54 only) |
| `entrypoint.sh` | Agent 4 | Modify | Add skill injection |
| `orchestrator.py` | Agent 4 | Modify | Call registry during job dispatch |
```

### Wave Execution Tracker

```markdown
## Remaining Work Execution

### Wave 1: Quick Wins
| Item | Agent | Status | Tests Before | Tests After |
|------|-------|--------|-------------|-------------|
| Config dedup | Agent A | Done | 102 | 102 |
| Docs commit | Agent B | Done | 102 | 102 |

### Wave 2: Core Integration
| Item | Agent | Status | Tests Before | Tests After |
|------|-------|--------|-------------|-------------|
| Registry merge | Agent C | Done | 102 | 110 |
| Gateway wiring | Agent D | In progress | -- | -- |
| E2E tests | Agent E | Blocked on D | -- | -- |
| Seed skills | Agent F | Done | 110 | 118 |

### Wave 3: Complex Items
| Item | Agent | Status | Tests Before | Tests After |
|------|-------|--------|-------------|-------------|
| Open questions | Agent G | Not started | -- | -- |
| Gateway backends | Agent H | Not started | -- | -- |
```

---

## 9. Metrics and Tracking

### What to Track

| Metric | How to Track | Target |
|--------|-------------|--------|
| Agents dispatched per phase | Count in wave tracker | 2-4 per phase |
| Cherry-pick conflict rate | Conflicts / total cherry-picks | < 30% |
| Test fix rounds per merge | Count fix agent dispatches | <= 3 |
| Tests added per phase | Cumulative test count | Monotonically increasing |
| PRs created | GitHub PR list | 1 per phase |
| Wall-clock time per phase | Start/end timestamps | Hours, not days |
| Total files created | `git diff --stat` | Tracked for project scope |

### Using Task Tracking

Use TaskCreate and TaskUpdate to maintain visibility across phases:

```
TaskCreate: "Phase 1 MVP Implementation"
  - Subtask: "Agent 1: Data layer" -> status: in_progress
  - Subtask: "Agent 2: Core services" -> status: in_progress
  - Subtask: "Agent 3: API endpoints" -> status: in_progress
  - Subtask: "Agent 4: Integration" -> status: in_progress
  - Subtask: "Cherry-pick and merge" -> status: pending
  - Subtask: "Test fixes" -> status: pending
  - Subtask: "Create PR" -> status: pending
```

Update task statuses as agents complete, providing a real-time view of progress.

### Project Summary Dashboard

At the end of the project, compile final metrics:

| Metric | Skill Hotloading Project |
|--------|------------------------|
| Total PRs | 5 |
| Total Python tests | 134 |
| Total JS tests | 8 |
| Total files created | ~50 |
| Total agents dispatched | ~30+ |
| Implementation phases | 5 + 1 remaining work phase |
| Calendar time | ~3 days |
| Feature flags added | 3 (`skill_registry_enabled`, `gateway_enabled`, `subagent_enabled`) |

---

## Appendix A: The Skill Hotloading Three-Layer Architecture

For reference, this is the architecture that was designed and implemented using this playbook:

```
+------------------------------------------------------------------+
|                        LAYER 3: SUBAGENTS                         |
|  Running agent spawns child agents via message-queue MCP tool     |
|  Controller mediates: classifies, spawns, tracks, returns result  |
+------------------------------------------------------------------+
        |
+------------------------------------------------------------------+
|                        LAYER 2: SKILLS                            |
|  Per-task SKILL.md files injected via Redis payload               |
|  Selected by controller using tag-based or semantic matching      |
|  Written to .claude/skills/ by entrypoint.sh before claude starts |
+------------------------------------------------------------------+
        |
+------------------------------------------------------------------+
|                       LAYER 1: AGENT TYPES                        |
|  Docker images with pre-installed toolchains                      |
|  general | frontend (+ Playwright) | backend (+ DB clients)      |
|  Resolved from skill requirements (e.g., requires: ["browser"])   |
+------------------------------------------------------------------+
```

### Feature Flags

```python
# config.py - all flags default to False for gradual enablement
skill_registry_enabled: bool = False      # Layer 2: Skill matching
skill_embedding_provider: str = "none"    # "none" for tag-based, "voyage" for semantic
gateway_enabled: bool = False             # Layer 2+: MCP Gateway for dynamic tools
subagent_enabled: bool = False            # Layer 3: Agent-spawns-agent
```

### Graceful Degradation

Every component follows the pattern: try the new system, fall back to the old system on failure.

```python
# Pseudocode pattern used throughout
if config.skill_registry_enabled:
    try:
        skills = await registry.find_skills(task)
    except Exception:
        logger.warning("Skill registry failed, falling back to tag-based")
        skills = fallback_tag_based_selection(task)
else:
    skills = []  # Feature disabled, no skills injected
```

---

## Appendix B: Seed Skill Format

Skills stored in the registry follow this YAML format for seeding:

```yaml
name: "kubernetes_expert"
description: "Deep knowledge of Kubernetes operations, debugging, and manifest authoring"
tags: ["kubernetes", "k8s", "devops", "containers", "helm"]
agent_type: "general"
requires: []
content: |
  # Kubernetes Expert

  ## Role
  You are a Kubernetes operations specialist.

  ## Scope
  - Manifest authoring (Deployments, Services, ConfigMaps, etc.)
  - Debugging pod failures, CrashLoopBackOff, networking issues
  - Helm chart development and templating
  - NOT: cloud provider-specific setup (use cloud_aws or cloud_gcp skills)

  ## Process
  1. Read existing manifests and understand the current state
  2. Identify the issue or requirement
  3. Propose changes with explanations
  4. Validate with dry-run where possible

  ## Rules
  - Always use resource limits and requests
  - Prefer Deployments over bare Pods
  - Use namespaces for isolation
  - Never hardcode secrets in manifests
```

---

*This playbook was created from the Ditto Factory Skill Hotloading System project (March 2026). It documents patterns that emerged from dispatching 30+ agents across 5 implementation phases, producing 134 Python tests and 8 JavaScript tests across approximately 50 files.*
