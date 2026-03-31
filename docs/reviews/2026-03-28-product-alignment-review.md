# Product Alignment Review — Ditto Factory
**Reviewer**: Alex (PM)  **Date**: 2026-03-28  **Status**: Review Complete

---

## 1. User Journey Coherence

**Can we trace: GitHub repo of tools -> tools available -> agent uses tools?**

The journey has three segments. Two are designed; one is missing.

| Segment | Status | Design Quality |
|---------|--------|---------------|
| GitHub repo -> discovered manifest | Designed (Flow 1) | Solid. Guided 4-step flow with agent-driven analysis. |
| Manifest -> registered in toolkit DB | Designed (Flow 1) | Solid. Clear data model, API, versioning. |
| Registered toolkit -> agent pod uses it at runtime | **NOT DESIGNED** | This is Flow 2 — explicitly out of scope. |

**The journey breaks at the handoff from registry to runtime.** The toolkit spec defines four `load_strategy` values (`mount_file`, `install_plugin`, `inject_rules`, `install_package`) but there is no design for how the K8s Job Spawner reads from the toolkit registry, selects relevant toolkits for a task, and assembles them into the agent pod's runtime environment.

The existing Skill Registry (skills/) handles `.claude/skills/*.md` injection. The new Toolkit system is a parallel, broader registry — but there is no bridge between them. A registered toolkit of type `skill` has no path to become an injected skill in the existing `skills/injector.py` pipeline.

**Critical gap:** The user imports a toolkit, sees it in the dashboard, but has no way to make an agent actually use it.

---

## 2. Flow Separation

**Flow 1 (Discovery/Registration) vs Flow 2 (Workflow Composition)**

The separation is intentionally clean — perhaps too clean. The issues:

**A) No lightweight "just use this toolkit" path.** The design assumes Flow 2 will handle all composition. But users will want to import a toolkit and immediately test it on a task. There should be a minimal bridge: "attach toolkit X to the next task I submit" — even before full workflow composition exists.

**B) Two registries, unclear relationship.** The existing `skills/` module has its own registry, classifier, injector, and resolver. The new `toolkits/` module has a separate registry with different data models. The relationship is undefined:
- Does `toolkits` replace `skills`? Subsume it? Run parallel?
- If a user imports a superpowers skill via toolkits, does it also appear in the skills registry?
- Which classifier picks toolkits for tasks — the existing `skills/classifier.py` or something new?

**Recommendation:** Decide now whether toolkits is the successor to skills or a separate layer. The answer shapes everything in Flow 2.

**C) Missing handoff contract.** Flow 1's output (registered toolkit in DB) needs a defined contract that Flow 2 consumes. The `load_strategy` field is a start, but Flow 2 needs to know:
- Runtime dependencies (what packages to install in the pod)
- Ordering (does this toolkit's CLAUDE.md injection happen before or after skill injection?)
- Conflicts (can two toolkits modify the same file?)
- Resource requirements (does this toolkit need more memory, network access, etc.?)

---

## 3. Value Delivery — Minimum Path

**What is the shortest path to: user imports a toolkit, agent uses it, task completes?**

### Current blockers (in dependency order):

1. **Toolkit registry backend** — Not built yet. Need: DB tables, CRUD API, discovery service.
2. **Discovery agent/service** — Not built. Need: GitHub client, repo analyzer, manifest generator.
3. **Dashboard import UI** — Not built. Need: the `/toolkits/import` guided flow.
4. **Runtime bridge (THE BLOCKER)** — No design exists for how a registered toolkit gets loaded into an agent pod.
5. **Task-toolkit association** — No mechanism to say "use toolkit X for this task."

### Proposed minimum viable path:

Skip the full Flow 2 design. Instead, build a thin bridge:

| Step | What to Build | Effort |
|------|--------------|--------|
| 1 | Toolkit DB tables + CRUD API | S |
| 2 | Manual toolkit import (paste content, set type) — skip GitHub discovery | S |
| 3 | "Attach toolkit" field on task submission form | S |
| 4 | Job Spawner reads attached toolkits + applies load_strategy | M |
| 5 | Test: import a superpowers skill manually, attach to task, verify agent uses it | — |

**Total: ~1.5 engineer-weeks to first real value.** The GitHub discovery flow and full dashboard can layer on after.

---

## 4. Feature Gaps

### Designed but not connected:

| Feature | Gap |
|---------|-----|
| Toolkit versioning & update detection | Designed in detail, but the toolkit registry itself doesn't exist yet |
| Dashboard toolkit pages | Designed, but no backend to connect to |
| Four toolkit types (skill/plugin/profile/tool) | Load strategies defined but no runtime implementation |
| Risk level classification | Captured in schema but nothing acts on it (no approval gate for `high` risk) |

### Designed that should be questioned:

| Feature | Concern |
|---------|---------|
| Periodic background sync | Premature. Users won't have enough toolkits to justify a background job. Manual "check for updates" is sufficient for v1. |
| Diff viewer for pending updates | Nice but complex. A "re-import and bump version" flow is simpler and delivers 80% of the value. |
| Discovery agent as K8s pod | The issues doc correctly identifies this should be a controller-side service. Don't over-engineer the discovery step. |

### Missing entirely:

| Gap | Impact | Priority |
|-----|--------|----------|
| **Runtime loader** — how toolkits actually get into agent pods | Blocks all value delivery | P0 |
| **Toolkit-to-task binding** — UI/API to associate toolkits with specific tasks or task types | Blocks usability | P0 |
| **Toolkit conflict resolution** — what happens when two toolkits modify `.claude/skills/` or `CLAUDE.md` | Will cause runtime failures | P1 |
| **Toolkit testing** — "dry run" a toolkit against a sample task to verify it loads correctly | Critical for trust | P1 |
| **Skills-to-toolkits migration path** — how existing skills move into the new system | Prevents registry confusion | P2 |
| **Toolkit composition rules** — which toolkits can coexist, ordering, resource budgets | Needed at scale | P2 |

---

## 5. Dogfooding: Toolkit Onboarding as a Ditto Factory Workflow

**The issues doc proposes this. How ready is the system?**

### What exists:
- Workflow engine with DAG templates, compiler, executor — built and tested
- K8s Job spawner with skill injection — built
- Skill system for teaching agents how to do things — built

### What's needed for dogfooding:

| Requirement | Status | Notes |
|-------------|--------|-------|
| Workflow engine operational | Exists (behind `DF_WORKFLOW_ENABLED` flag) | Needs validation with a real workflow |
| `toolkit_onboarding` workflow template | Does not exist | Must be authored — the DAG steps, the agent skill for repo analysis |
| Structured output from agent | Partially supported | Agent can produce JSON, but there's no schema validation for manifest format |
| Workflow output -> toolkit registry write | Does not exist | The workflow engine has no "write result to DB" post-step hook |
| Dashboard trigger for workflow | Partially exists | `/workflows/[slug]/run` page is designed but not connected |

### Chicken-and-egg problem:
To dogfood toolkit onboarding as a workflow, you need:
1. The workflow engine running (exists)
2. A skill that teaches the agent how to analyze repos (must write this)
3. A way to capture the agent's structured output and write it to the toolkit registry (does not exist — this is a new integration point)

**The missing piece is #3.** The workflow engine runs agents and collects results, but results today go to PRs/reports/comments. There's no "write structured output to an internal DB table" result type. This is a new capability the platform needs.

### Recommendation:
Add a `structured_output` result type that writes agent JSON output to a specified DB table/API endpoint. This unlocks dogfooding AND is broadly useful (any workflow that produces data, not just code).

---

## Summary: Critical Path

```
Priority 0 — Unblock value delivery:
  1. Decide: toolkits replaces or wraps the skills registry
  2. Build toolkit DB + CRUD API (thin, no GitHub integration yet)
  3. Build runtime loader in Job Spawner (read toolkit, apply load_strategy)
  4. Add "attach toolkit" to task submission
  5. Test end-to-end with one manually imported toolkit

Priority 1 — Enable discovery flow:
  6. Build GitHub client + discovery service (controller-side, not K8s)
  7. Build dashboard import UI
  8. Connect discovery output to toolkit registry

Priority 2 — Dogfooding:
  9. Add structured_output result type to workflow engine
  10. Author the toolkit_onboarding workflow template + skill
  11. Replace Python discovery service with the dogfooded workflow
```

**The biggest risk is building more design surface (dashboard pages, update flows, version diffs) before the runtime bridge exists.** No amount of UI polish matters if an imported toolkit can't reach an agent pod. Sequence ruthlessly toward step 5.
