# Skill Hotloading Design Spec -- Review Report

**Reviewed by**: Senior Code Reviewer (Claude Opus 4.6)
**Date**: 2026-03-21
**Spec**: `docs/superpowers/specs/2026-03-21-skill-hotloading-design.md`

---

## 1. Orchestrator Integration Points

**Verdict: PASS with issues**

The spec's `_spawn_job` changes are structurally consistent with the actual method signature at `orchestrator.py:73-139`. The method does accept `(self, thread, task_request, is_retry, retry_count)` and the spec's diff preserves this signature. The existing flow (prompt build, conversation store, branch create, Redis push, spawn, job track) is accurately reproduced.

### Issues Found

**[Important] `classification.agent_image` does not match the classifier's return type.** The spec defines `ClassificationResult` with fields `skills`, `agent_type`, and `task_embedding` (line 322-326). But the orchestrator diff references `classification.agent_image` (line 495) -- a field that does not exist on the result type. The classifier returns `agent_type` (a string like "general"), not `agent_image` (a Docker image URL). The agent type resolver returns `ResolvedAgent(image=..., agent_type=...)`, but this resolver is called *inside* the classifier, and the result is not surfaced as `agent_image` on `ClassificationResult`.

**Recommended fix**: Either add `agent_image: str` to `ClassificationResult`, or change the orchestrator diff to use `classification.agent_type` and resolve the image separately.

**[Important] New constructor dependencies are incomplete.** The spec shows three new dependencies (`_classifier`, `_injector`, `_tracker`) but does not update the `__init__` parameter list (lines 559-564). The actual constructor at `orchestrator.py:19-35` takes explicit parameters -- these new deps need to be added as constructor params, not constructed inline. The spec hand-waves with `(...)`.

**Recommended fix**: Show the full updated `__init__` signature with `classifier: TaskClassifier`, `injector: SkillInjector`, `tracker: PerformanceTracker` as explicit params, consistent with the existing dependency injection pattern.

**[Suggestion] `self._detect_language(thread)` is called but never defined.** Line 491 calls a method that does not exist on the current Orchestrator. The spec should either define this helper or note it as new.

---

## 2. Spawner Integration Points

**Verdict: PASS**

The spec's proposed change to `build_job_spec` and `spawn` (adding `agent_image: str | None = None`) is straightforward and fully compatible with the actual signatures at `spawner.py:18` and `spawner.py:73`. The fallback `agent_image or self._settings.agent_image` is correct -- the existing code uses `self._settings.agent_image` at line 26.

No issues found. This is a clean, backward-compatible change.

---

## 3. Entrypoint.sh Changes

**Verdict: PASS with issues**

The proposed bash insertion point (after git clone, before claude invocation) is correct per the actual entrypoint structure.

### Issues Found

**[Important] `jq -r '.skills // empty'` returns empty string for missing key, but returns the raw JSON array for present key.** The `-r` flag will strip outer quotes from strings but does NOT affect arrays/objects -- so `SKILLS_JSON` will contain the raw JSON array `[{"name":"...","content":"..."},...]`. This is actually correct for the subsequent `jq -c '.[]'` pipe. However, the spec should note that if `jq` is not installed in the agent image, this will fail silently under `set -e`. The current entrypoint already uses `jq` (line 18, 31-34), so this is safe, but worth documenting as a dependency.

**[Important] Subshell variable scope in `while read` loop.** The `echo "$SKILLS_JSON" | jq -c '.[]' | while read -r skill; do ... done` pattern runs the `while` body in a subshell (due to the pipe). This means any variables set inside the loop are lost after the loop. In this particular case, no variables need to survive the loop, so it is functionally correct. However, if future modifications need to track state (e.g., count of successfully written skills), this pattern will silently fail. A safer pattern would be:

```bash
while read -r skill; do
    ...
done < <(echo "$SKILLS_JSON" | jq -c '.[]')
```

**[Suggestion] Skill file names are not sanitized.** The skill `name` from Redis is used directly as a filename: `".claude/skills/${SKILL_NAME}.md"`. If a skill slug contains `/`, `..`, or shell metacharacters, this could write files outside the intended directory or cause path traversal. The controller side should enforce slug format (and does, via the `slug` field), but a defensive `basename` call in the entrypoint would be prudent:

```bash
SKILL_NAME=$(echo "$skill" | jq -r '.name' | tr -cd 'a-zA-Z0-9_-')
```

---

## 4. Config Changes

**Verdict: PASS**

The proposed new settings (lines 637-643) are consistent with the existing `Settings` pattern in `config.py`. All new fields have sensible defaults. The `skill_registry_enabled: bool = False` feature flag ensures zero impact when disabled, meeting NFR-5.

### Minor Notes

**[Suggestion]** The `voyage_api_key` should probably use the same secret injection pattern as `anthropic_api_key` rather than being a plain settings field, but this is an implementation detail, not a spec issue.

---

## 5. Models Changes

**Verdict: PASS**

Adding `agent_type: str = "general"` and `skills_injected: list[str]` to the `Job` dataclass (lines 663-664) is backward-compatible with the existing dataclass at `models.py:57-66`. Default values ensure existing code continues to work.

---

## 6. Data Model (SQL Schema)

**Verdict: PASS with issues**

### Issues Found

**[Important] IVFFlat index created before data exists.** The schema at line 729-730 creates an IVFFlat index with `lists = 100`. IVFFlat indexes require a significant number of existing rows to be effective -- creating the index on an empty table will result in poor recall. The spec's Phase 2 timeline acknowledges pgvector setup, but the schema should note that the index should be created *after* initial skill seeding, or use HNSW instead (which does not require pre-existing data).

**Recommended fix**: Either (a) use `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)` which works well on small datasets, or (b) add a comment noting the index should be created after seeding at least 1000 rows.

**[Important] Missing migration for existing `jobs` table.** The schema includes `ALTER TABLE jobs ADD COLUMN ...` (lines 812-814), but the current codebase uses dataclass-based models (`models.py`), not SQLAlchemy ORM. There is no evidence of an existing `jobs` table in Postgres -- the state backend abstraction suggests the table may or may not exist. The spec should clarify whether these ALTER statements target an existing table or whether the `jobs` table needs to be created first.

**[Suggestion]** The `skill_usage.job_id` is `VARCHAR(128)` but `Job.id` is generated as `uuid.uuid4().hex` (a 32-char hex string). This works but is inconsistent with the UUID types used elsewhere in the skill schema.

---

## 7. API Reference

**Verdict: PASS**

The API endpoints are well-designed and RESTful. The CRUD operations, search, versioning, and rollback APIs are internally consistent with the data model. The rollback creating a new version (rather than mutating) is the correct design choice.

### Minor Notes

**[Suggestion]** The `POST /api/v1/skills/usage` endpoint (line 1039) appears to be an external API for recording usage, but the spec describes usage recording as happening internally via `PerformanceTracker`. If this endpoint is for external consumers, it should have authentication/authorization. If it is internal-only, consider using the `/api/v1/internal/` prefix.

---

## 8. Internal Consistency

**Verdict: PASS with issues**

The sequence diagram (Section 3.3) and the code diffs (Section 5) tell the same story with one discrepancy:

**[Important]** The sequence diagram shows step 3 "resolve agent_type from skill.requires" as a separate step, but in the code diff (Section 5.1), agent type resolution happens *inside* `self._classifier.classify()` which returns `classification.agent_type`. The resolver is not called separately in the orchestrator. This is fine architecturally (the classifier delegates to the resolver), but the sequence diagram implies the orchestrator calls the resolver directly, which is misleading.

---

## 9. Completeness

**Verdict: PASS with issues**

### Missing Items

**[Important] No error handling for classifier failures.** The orchestrator diff shows `await self._classifier.classify(...)` but does not show what happens if the classifier itself throws (not just the embedding provider). If the skill registry DB is down, the entire task pipeline would fail. The feature flag check should wrap the entire block in a try/except that falls back to no-skills behavior.

**[Important] No concurrency consideration for skill updates during classification.** If a skill is updated (new version) while the classifier is building a payload, the injected content could be inconsistent. The spec should note whether skill reads use snapshot isolation or if this is an accepted race condition.

**[Suggestion] No rate limiting on the Voyage API.** The spec mentions Voyage-3 for embeddings but does not discuss rate limits, batching, or retry logic. At scale, every incoming task triggers an embedding call.

**[Suggestion] No discussion of skill content validation.** What prevents a skill from containing malicious instructions (e.g., "ignore all previous instructions")? The spec mentions a review process but does not define it.

---

## 10. Feasibility Assessment

**Verdict: PASS**

The proposed changes are realistic given the current codebase structure:

- The orchestrator's `_spawn_job` method is well-isolated and easy to extend
- The spawner's `build_job_spec` accepts a clean signature that is trivially extensible
- The entrypoint already uses `jq` and Redis, so skill injection is natural
- The modular monolith approach avoids infrastructure overhead
- The phased rollout is well-scoped (Phase 1 at ~8 days is realistic for the described scope)

### Feasibility Concerns

**[Suggestion]** Phase 1 estimates 1-2 days for "Skill Injector + entrypoint changes" but this includes writing integration tests for the bash changes, which typically takes longer than the implementation itself. Consider allocating an additional day.

**[Suggestion]** The spec does not mention how skills will be initially seeded. Without a seed script or import tool, the system ships with zero skills and no way to validate the pipeline. Consider adding a seed step to Phase 1.

---

## Summary

| Section | Verdict |
|---------|---------|
| Orchestrator integration | PASS with issues |
| Spawner integration | PASS |
| Entrypoint changes | PASS with issues |
| Config changes | PASS |
| Models changes | PASS |
| Data model (SQL) | PASS with issues |
| API reference | PASS |
| Internal consistency | PASS with issues |
| Completeness | PASS with issues |
| Feasibility | PASS |

### Critical Issues (must fix before implementation)

None.

### Important Issues (should fix)

1. `classification.agent_image` field does not exist on `ClassificationResult` -- fix the orchestrator diff or the data model
2. New constructor dependencies should show explicit params, not `(...)`
3. `self._detect_language(thread)` is undefined -- define or document it
4. Entrypoint skill filename not sanitized against path traversal
5. IVFFlat index on empty table will have poor recall -- use HNSW or defer index creation
6. Clarify whether `jobs` table already exists in Postgres for the ALTER statements
7. No error handling for classifier failures in orchestrator (should fall back to no-skills)
8. Sequence diagram implies orchestrator calls resolver directly, but code shows it is internal to the classifier

### Suggestions (nice to have)

1. Use process substitution (`< <(...)`) instead of pipe for `while read` loop
2. Sanitize skill slugs in entrypoint with `tr -cd`
3. Consider secret injection for `voyage_api_key`
4. Normalize `skill_usage.job_id` type to UUID
5. Clarify whether `/api/v1/skills/usage` is internal or external
6. Add rate limiting/retry discussion for Voyage API
7. Add skill content validation/review process
8. Add seed script to Phase 1
9. Add buffer day for entrypoint integration tests

---

**Overall Assessment**: The spec is well-structured, thorough, and demonstrates strong architectural thinking. The phased rollout is realistic and the ADRs are well-reasoned. The issues identified are all addressable within a revision pass -- none require fundamental rearchitecting. The biggest gap is the `ClassificationResult` field mismatch, which would cause a runtime error if implemented as-written. Recommend a quick revision to address the 8 important issues before moving to implementation.
