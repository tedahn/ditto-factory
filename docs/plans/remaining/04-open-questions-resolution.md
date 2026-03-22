# Plan: Resolving Open Questions from Skill Hotloading Design Spec

**Source**: `docs/superpowers/specs/2026-03-21-skill-hotloading-design.md`, Section 10
**Date**: 2026-03-21

---

## Priority Order

| Priority | Question | Effort | Rationale for ordering |
|----------|----------|--------|------------------------|
| P0 | Q6 - Classifier override | 1 day | Unblocks testing and power-user workflows immediately |
| P0 | Q2 - Skill scope | 0.5 day | Data model supports it but API/UX needs a decision to avoid rework |
| P1 | Q3 - Embedding refresh | 1 day | Needed before Phase 2 (semantic search) ships |
| P1 | Q7 - Skill packs | 2 days | High user-facing value, moderate implementation cost |
| P1 | Q1 - Skill authoring UX | 0.5 day | Decision only; no implementation in this round |
| P2 | Q5 - Entrypoint rewrite | 3 days | Technical debt; defer until entrypoint gains one more feature |
| P2 | Q4 - Agent-side feedback | 2 days | Blocked on Claude Code exposing skill activation telemetry |
| P3 | Q8 - Cross-agent-type migration | 3-5 days | Architecturally significant; defer to Phase 4+ |

**Total estimated effort**: ~13-14 days

---

## Question-by-Question Resolution

### Q1: Skill Authoring UX

> Who writes skills? Is the API + CLI sufficient for v1, or do we need a web UI?

**Recommendation**: API + CLI is sufficient for v1. Defer web UI.

**Rationale**: The current user base is developers who are comfortable with CLI tools. The existing `POST /api/v1/skills` endpoint plus the Ditto CLI `/ditto skill create` command cover the creation workflow. A web UI adds frontend development scope with unclear ROI at this stage.

**Implementation**: No code changes needed. Document the CLI-based authoring workflow in a skills authoring guide. Revisit when non-developer users (e.g., team leads, PMs) need to create skills.

**Effort**: 0.5 day (documentation only)

---

### Q2: Skill Scope

> Should skills be per-org, per-repo, or global? The data model supports all three, but the UX implications differ.

**Recommendation**: Three-tier scoping -- global, org, repo -- with a merge strategy.

**Rationale**: The data model already has `org_id` and `repo_pattern` columns. The question is really about resolution order and conflict handling. A clear merge strategy avoids ambiguity:

1. **Global skills** (`org_id = NULL, repo_pattern = NULL`): Available to all tasks. Used for universal best practices (e.g., "always run tests").
2. **Org skills** (`org_id = 'acme', repo_pattern = NULL`): Available to all repos in an org. Org-specific conventions.
3. **Repo skills** (`org_id = 'acme', repo_pattern = 'acme/frontend-*'`): Narrowest scope. Repo-specific workflows.

**Merge strategy**: When classifying a task, collect skills from all three tiers. If two skills have the same slug at different scopes, the narrower scope wins (repo > org > global). This mirrors how `.gitconfig` works (local > global).

**Implementation changes**:
- `classifier.py` -- `classify()` method: query registry with `org_id` and repo info, then deduplicate by slug with scope priority.
- `registry.py` -- `search_by_tags()` and `search_by_embedding()`: add `org_id` and `repo_pattern` to the query filters. The `_apply_scope_filter` helper already partially exists via the `org_id` field on the `Skill` model.
- `api.py` -- `POST /api/v1/skills`: validate that `org_id` and `repo_pattern` are consistent (repo pattern requires org_id).

**Effort**: 0.5 day

---

### Q3: Embedding Refresh

> When a skill's content is updated, do we re-embed immediately (synchronous) or in a background job (asynchronous)?

**Recommendation**: Synchronous for v1, with an async escape hatch.

**Rationale**: Skill updates are infrequent (a few per day at most) and embedding generation via Voyage-3 takes ~100-300ms. The simplicity of synchronous embedding outweighs the latency cost. The API response time goes from ~50ms to ~350ms on update -- acceptable for an admin operation.

However, we should design the interface to support async later:

```python
# In registry.py, update method:
async def update_skill(self, slug, updates, changelog, updated_by):
    # ... save to DB ...
    # Re-embed synchronously (v1)
    if self._embedder:
        embedding = await self._embedder.embed(skill.content)
        await self._save_embedding(slug, embedding)
    return skill
```

**Async escape hatch**: If embedding latency becomes a problem (e.g., batch imports of 50+ skills), add a `POST /api/v1/skills/reindex` endpoint that queues re-embedding as background tasks. This is additive and does not require changing the synchronous path.

**Implementation changes**:
- `registry.py` -- `update_skill()`: call `self._embedder.embed()` after saving the new version. Already partially implemented in the current code (the method updates the DB but does not re-embed).
- `api.py` -- `PUT /api/v1/skills/{slug}`: no change needed; the registry handles embedding internally.
- Add `POST /api/v1/skills/reindex` as a future endpoint (stub it now with a TODO).

**Effort**: 1 day

---

### Q4: Agent-Side Feedback

> Can the agent report back which skills it actually used vs which were injected but ignored?

**Recommendation**: Defer implementation. Design a convention now; implement when Claude Code supports it.

**Rationale**: Claude Code does not currently expose skill activation telemetry. We cannot reliably determine which SKILL.md files the agent actually referenced during execution. Heuristic approaches (e.g., checking if the agent's output mentions skill-related terms) are unreliable and add complexity.

**Convention to establish now**: Define a structured feedback format that agents can optionally write to a well-known file:

```json
// .ditto/skill-feedback.json (written by agent if supported)
{
  "skills_used": ["debug-react", "typescript-testing"],
  "skills_ignored": ["python-formatting"],
  "skill_ratings": {
    "debug-react": {"helpful": true, "comment": "Guided correct fix"}
  }
}
```

**Implementation changes (when ready)**:
- `entrypoint.sh` or Python entrypoint: after agent exits, check for `.ditto/skill-feedback.json` and include it in the result payload.
- `tracker.py` -- `record_outcome()`: parse feedback file and store per-skill usage data.
- `registry.py` -- `compute_boost()`: weight skills by actual usage, not just injection.

**Effort**: 2 days (when unblocked)

---

### Q5: Entrypoint Rewrite

> The bash entrypoint is getting complex. Should we rewrite it in Python as part of this work, or defer?

**Recommendation**: Defer, but set a trigger condition.

**Rationale**: The current bash entrypoint handles: (1) Redis payload fetch, (2) git clone, (3) SKILL.md file writing, (4) Claude Code invocation, (5) result posting. This is ~5 responsibilities and is approaching the complexity threshold where bash becomes a liability (error handling, JSON parsing with jq, conditional logic).

**Trigger condition**: Rewrite when the entrypoint needs to gain any ONE of:
- Retry logic with backoff (currently not needed)
- Dynamic mcp.json generation (Phase 5)
- Structured logging (currently uses echo)
- Feedback file parsing (Q4 above)

**When rewriting**, the Python entrypoint should:
- Use `click` for argument parsing
- Use the same Redis client library as the controller
- Be a single `entrypoint.py` file in the agent image, invoked as `python entrypoint.py`
- Keep the same interface: environment variables for config, Redis for payload

**Effort**: 3 days (when triggered)

---

### Q6: Classifier Override

> Should webhook payloads support an explicit `skills` or `agent_type` field to bypass the classifier?

**Recommendation**: Yes. Support both `skills` and `agent_type` override fields.

**Rationale**: This is essential for (a) testing skills in isolation, (b) power users who know exactly what they need, and (c) CI/CD pipelines that deterministically assign skills. The classifier should be the default, not a gate.

**Design**:

```python
# In the webhook payload / TaskRequest model:
class TaskRequest:
    task: str
    # ... existing fields ...
    skill_overrides: list[str] | None = None   # Explicit skill slugs
    agent_type_override: str | None = None      # Explicit agent type

# In orchestrator._spawn_job:
if task_request.skill_overrides:
    # Bypass classifier entirely
    matched_skills = await self._registry.get_skills_by_slugs(
        task_request.skill_overrides
    )
    agent_type = task_request.agent_type_override or "general"
else:
    # Normal classification path
    classification = await self._classifier.classify(...)
    matched_skills = classification.skills
    agent_type = classification.agent_type
```

**Implementation changes**:
- `models.py` -- Add `skill_overrides` and `agent_type_override` to `TaskRequest`.
- `orchestrator.py` -- `_spawn_job()`: add override check before classifier call.
- `registry.py` -- Add `get_skills_by_slugs(slugs: list[str]) -> list[Skill]` method.
- `api.py` -- The webhook endpoint already deserializes `TaskRequest`; the new fields are optional and backward-compatible.
- Validate that overridden skill slugs exist and are active; return 400 if not.

**Effort**: 1 day

---

### Q7: Skill Packs

> Should we support grouping skills into packs for common task patterns?

**Recommendation**: Yes, as a lightweight grouping mechanism -- not a new entity type.

**Rationale**: Skill packs address a real need: "when I work on React bugs, I always want these 4 skills together." However, creating a full `SkillPack` entity with its own CRUD, versioning, and embedding adds significant complexity. A simpler approach: use tags as pack identifiers.

**Design**: A skill pack is a tag convention, not a database entity.

```
# Skills tagged with pack:react-debugging
- debug-react (tags: ["pack:react-debugging", "react", "debugging"])
- typescript-testing (tags: ["pack:react-debugging", "testing"])
- browser-devtools (tags: ["pack:react-debugging", "browser"])
```

Users can then override with a pack reference:

```json
{
  "task": "Fix the login form",
  "skill_overrides": ["pack:react-debugging"]
}
```

The override resolver expands pack references:
1. If a slug starts with `pack:`, query all skills with that tag.
2. Otherwise, treat it as a direct skill slug.

**Implementation changes**:
- `registry.py` -- Add `get_skills_by_tag(tag: str) -> list[Skill]` method.
- `orchestrator.py` -- In the override path (Q6), expand `pack:` prefixes before fetching skills.
- `api.py` -- Add `GET /api/v1/packs` endpoint that lists distinct `pack:*` tags and their member skills. This is a read-only view over existing tag data.
- Documentation: document the `pack:` tag convention.

**Why not a separate entity**: A `SkillPack` table with its own versioning, permissions, and embedding would add ~2 days of implementation and ongoing maintenance. The tag convention achieves 90% of the value with 10% of the cost. If packs need independent versioning or permissions later, we can promote them to a first-class entity.

**Effort**: 2 days

---

### Q8: Cross-Agent-Type Migration

> If a task starts on `general` but the agent discovers it needs `frontend` tools, should it be able to request a restart on a different image?

**Recommendation**: Defer to Phase 4 (Subagent Spawning). Do not implement restart-based migration.

**Rationale**: Restarting a task on a different image is expensive (lose all agent context, git state, and progress) and complex (need to serialize agent state, handle partial commits, manage the restart lifecycle). The subagent spawning model from Phase 4 is architecturally superior: the general agent spawns a frontend subagent for the specific subtask that needs browser tools, without losing its own context.

**Interim mitigation**: The classifier override (Q6) lets users manually specify the correct agent type upfront. Combined with better classification in Phase 2 (semantic search), the frequency of misclassification should be low.

**If we eventually need this**, the design should be:
1. Agent writes a `.ditto/restart-request.json` with the desired agent type and a state snapshot.
2. The controller detects this on job completion (non-zero exit with restart flag).
3. The controller re-queues the task with the new agent type and the accumulated context.
4. A restart counter prevents infinite loops (max 1 restart per task).

**Effort**: 3-5 days (when needed, Phase 4+)

---

## Implementation Sequence

```
Week 1:
  Q6 - Classifier override (P0, 1 day)
  Q2 - Skill scope resolution (P0, 0.5 day)
  Q1 - Authoring UX documentation (P1, 0.5 day)

Week 2:
  Q3 - Embedding refresh (P1, 1 day) -- needed before Phase 2
  Q7 - Skill packs via tag convention (P1, 2 days)

Deferred:
  Q5 - Entrypoint rewrite (trigger-based, 3 days)
  Q4 - Agent feedback (blocked on Claude Code, 2 days)
  Q8 - Cross-agent migration (Phase 4+, 3-5 days)
```

## Dependencies Between Questions

- Q7 (skill packs) depends on Q6 (classifier override) -- packs use the override mechanism to expand `pack:` prefixes.
- Q3 (embedding refresh) should land before Phase 2 semantic search goes live.
- Q4 (agent feedback) depends on Q5 (entrypoint rewrite) if we want structured feedback parsing.
- Q8 (cross-agent migration) is superseded by Phase 4 subagent spawning if that ships first.
