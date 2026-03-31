# Ditto Factory Architecture Review

**Date:** 2026-03-28
**Reviewer:** Software Architect Agent
**Scope:** Toolkit Discovery system alignment with existing controller architecture

---

## 1. Architectural Coherence

**Rating: NEEDS WORK**

The toolkit system lives in `controller/src/controller/toolkits/` as a peer to `skills/` and `workflows/` -- structurally correct. However, there is no integration point between them. The toolkit registry is a standalone CRUD island: it imports, stores, and lists toolkits, but nothing in the orchestrator, classifier, or workflow engine references it. The `toolkits/` module has zero imports from `skills/` or `workflows/`, and vice versa.

**Key concern:** The toolkit system discovers `ToolkitComponent` objects of `type: ComponentType.SKILL`, but the skill classifier operates on `Skill` objects from `skills/models.py`. There is no bridge that materializes a `ToolkitComponent` into a `Skill` that the classifier can actually use. This is the most critical seam.

**Recommendation:** Build a `toolkit_activator` that, when a user "activates" a toolkit component, creates a corresponding `Skill` record (or `AgentType`, or MCP config entry) in the appropriate registry. This keeps toolkit discovery as a supply-side concern and skill classification as a demand-side concern, with activation as the explicit boundary.

---

## 2. Data Model Alignment

**Rating: NEEDS WORK**

Side-by-side comparison reveals significant overlap with subtle misalignment:

| Concept | `Skill` (skills/models.py) | `ToolkitComponent` (toolkits/models.py) |
|---------|---------------------------|----------------------------------------|
| Identity | `id`, `slug`, `name` | `id`, `slug`, `name` |
| Content | `content` (the skill text) | `content` (cached primary file) |
| Versioning | `version` (int), `SkillVersion` | Inherits from parent `Toolkit.version` |
| Classification | `language`, `domain`, `requires` | `type`, `tags`, `load_strategy` |
| Tracking | `SkillUsage` (per-job tracking) | None |
| Provenance | `created_by` (string) | `toolkit_id` -> `source_id` -> GitHub URL |

The `ToolkitComponent` has richer provenance (full GitHub lineage) but weaker runtime metadata (no `language`, `domain`, `requires` fields that the classifier needs). The `Skill` has richer runtime metadata but no provenance tracking.

**Recommendation:** `ToolkitComponent` should be the **source of record**, and `Skill` should be a **materialized view** created at activation time, enriched with runtime metadata. Add a `source_component_id` foreign key to `Skill` to maintain the link.

---

## 3. Separation of Concerns

**Rating: STRONG (with one caveat)**

The bounded contexts are well-drawn:
- **Toolkit Registry** = supply-side catalog (what tools exist, where they come from)
- **Skill Classifier** = demand-side matching (given a task, which skills apply)
- **Workflow Engine** = orchestration (multi-step execution with DAG semantics)

The issues doc correctly identifies that toolkit onboarding should be a built-in workflow (`toolkit_onboarding` task type). The workflow engine already supports `AgentSpec` with `task_template`, `skills`, and `output_schema` -- this is sufficient to model the onboarding flow.

**Caveat:** The workflow `AgentSpec.skills` field references skill slugs as strings. If the onboarding workflow needs a skill that teaches agents how to analyze repos, that skill must exist in the skill registry *before* the workflow runs. This creates a bootstrap dependency: the toolkit system needs the skill system to import toolkits, but some of those toolkits *are* skills. Document this bootstrap order explicitly.

---

## 4. Missing Abstractions

**Rating: GAP**

Three abstractions are conspicuously absent:

### 4a. Agent Loadout
The spec explicitly defers "agent pod loadout assembly" to Flow 2, but the concept has no model anywhere. An agent loadout should be: which skills to inject, which MCP tools to enable, which env vars to set, which agent type/image to use. Currently this logic is scattered across the orchestrator (`skill_slugs`), gateway (`scope_from_skills`), and job spawner.

**Recommendation:** Introduce an `AgentLoadout` dataclass that aggregates: `skills: list[Skill]`, `mcp_config: dict`, `env_vars: dict`, `agent_type: AgentType`, `resource_profile: dict`. The orchestrator produces a loadout; the spawner consumes it.

### 4b. Component Activation Record
No model tracks "user activated component X from toolkit Y, creating skill Z at time T." Without this, there is no way to trace a running skill back to its toolkit origin or to handle updates (toolkit v2 ships -- which activated skills need refresh?).

### 4c. Toolkit Compatibility Matrix
No model captures which components are compatible with which agent types, or which components conflict. The `requires` field on `Skill` partially covers this but is not connected to toolkit components.

---

## 5. Scaling Concerns

**Rating: NEEDS WORK**

What breaks first, in order:

1. **SQLite contention** -- The toolkit registry uses `aiosqlite` with per-operation connections (`async with aiosqlite.connect()`). Under concurrent imports or high dashboard traffic, SQLite's single-writer lock will become a bottleneck. The skill registry likely has the same pattern. This is fine for single-controller deployments but blocks horizontal scaling.

2. **Content duplication** -- `ToolkitComponent.content` and `ComponentFile.content` store full file contents in SQLite. A large toolkit repo (100+ skills, each with multiple files) will bloat the database. No compression, no external storage, no lazy loading.

3. **Discovery agent cost** -- The onboarding workflow spawns a full Claude Code agent to analyze each repo. At scale (importing 50 toolkits), this is expensive. Consider a tiered approach: fast heuristic scan first, agent-driven deep analysis only for ambiguous repos.

4. **No caching layer** -- The web dashboard hits FastAPI which hits SQLite on every request. Adding Redis caching for toolkit/skill listings would be straightforward given Redis is already in the stack.

---

## Summary

| Question | Rating |
|----------|--------|
| 1. Architectural Coherence | NEEDS WORK |
| 2. Data Model Alignment | NEEDS WORK |
| 3. Separation of Concerns | STRONG |
| 4. Missing Abstractions | GAP |
| 5. Scaling Concerns | NEEDS WORK |

**Top 3 Actions (priority order):**

1. **Build the activation bridge** -- `ToolkitComponent` -> `Skill` materialization with `source_component_id` backlink. This is the critical missing integration.
2. **Introduce `AgentLoadout`** -- Consolidate scattered loadout logic into a single model the orchestrator produces and the spawner consumes.
3. **Add bootstrap workflow template** -- Ship a `toolkit_onboarding` workflow template with its prerequisite skill, and document the bootstrap order.
