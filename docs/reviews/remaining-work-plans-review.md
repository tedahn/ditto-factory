# Review: Remaining Work Plans for Ditto Factory Skill Hotloading System

**Reviewer:** Senior Code Reviewer (Claude Opus 4.6)
**Date:** 2026-03-21
**Plans Reviewed:** 8 implementation plans in `docs/plans/remaining/`
**Codebase Validated Against:** `config.py`, `orchestrator.py`, `gateway.py`, `server.js`

---

## 1. Per-Plan Verdicts

### Plan 01: Gateway Tool Backends -- PASS

**Strengths:**
- Thorough security analysis per tool (path traversal, SQL injection, API key handling).
- ADR for tool handler extraction is well-reasoned.
- Clear file structure under `src/mcp/gateway/tools/`.
- Implementation order (file-analysis first, db-query early for security review) is sound.

**Issues:**
- *[Suggestion]* The `web-search` tool depends on SearXNG deployment -- this external dependency is acknowledged but no fallback timeline is given. Consider making Brave the default v1 backend and SearXNG a v2 option.
- *[Important]* The `db-query` tool's `LIMIT` injection strategy needs clarification: the plan says "inject LIMIT if missing" but does not specify how to detect subqueries that already contain LIMIT clauses. A naive regex approach could break `UNION` queries.
- *[Suggestion]* Effort estimate of 20-24 hours (3 days) seems slightly optimistic given the security layers needed for `db-query`. Budget 4 days to account for security review iteration.

---

### Plan 02: Gateway Orchestrator Wiring -- PASS

**Strengths:**
- Accurately reflects the current codebase. The `GatewayManager` class, its methods (`set_scope`, `clear_scope`, `scope_from_skills`, `get_gateway_mcp_config`), and the `Orchestrator` constructor signature all match the actual code.
- Non-fatal error handling pattern (try/except with `gateway_mcp={}` fallback) is the correct approach.
- Test plan is comprehensive (8 unit tests covering happy path, disabled, errors, defaults).
- 2-hour TTL safety net for stale scopes is already implemented in `gateway.py`.

**Issues:**
- *[Important]* The plan references `matched_skills` variable in `_spawn_job`, but the current `orchestrator.py` does not have this variable yet -- it is introduced by the skill injection work. This creates an implicit dependency on the registry merge (Plan 06) being completed first. The plan should explicitly state this prerequisite.
- *[Suggestion]* The plan mentions `gateway_max_tools` as a follow-up but does not create a tracking issue. Consider adding it to Plan 04's open questions.

---

### Plan 03: Design Docs Cleanup -- PASS

**Strengths:**
- Comprehensive inventory of 13 untracked files.
- Sensible directory reorganization (`docs/designs/skill-hotloading/`, `docs/designs/tracing/`).
- Good decision to keep Approach B and C docs as architectural alternatives rather than archiving.

**Issues:**
- *[Suggestion]* The plan lists `docs/architecture-diagram-changes.md` under "Other" but this file relates to the main architecture and could be confusing alongside design approach docs. Consider moving it to `docs/architecture/` instead.
- *[Suggestion]* Effort estimate of 1-2 hours is reasonable but could be done in 30 minutes if approached as a simple `git add` + `git mv` session.

---

### Plan 04: Open Questions Resolution -- PASS (with reservations)

**Strengths:**
- All 8 open questions from the design spec are addressed with concrete recommendations.
- Priority ordering (P0: classifier override, skill scope; P1: embedding refresh, skill packs) is well-justified.
- Dependency graph between questions (Q7 depends on Q6, Q4 depends on Q5) is clearly documented.

**Issues:**
- *[Important]* Q6 (Classifier Override) proposes adding `skill_overrides` to the Redis task payload, but this conflicts with Plan 02's payload format. Plan 02 shows the payload as `{task, system_prompt, repo_url, branch, skills, gateway_mcp}` -- `skill_overrides` would need to be added there too. These plans should be coordinated.
- *[Important]* Q7 (Skill Packs) proposes a `pack:frontend-debug` tag convention that expands at classification time. This is a significant feature with 2-day effort but no test plan is provided. At minimum, it needs unit tests for pack expansion logic.
- *[Suggestion]* The "Deferred" items (Q5, Q4, Q8) total 8-10 days of work but have no target timeline. Consider creating separate plan documents for these when they become actionable.

---

### Plan 05: Config Dedup -- PASS

**Strengths:**
- Correctly identified all 7 duplicate field definitions across 3 sections in `config.py`.
- The exact diff is accurate and matches the current file content (verified against codebase).
- Pydantic's "last definition wins" behavior is correctly noted.
- Minimal risk -- pure cleanup with no behavioral change.

**Issues:**
- *[Suggestion]* The plan could mention running `python -c "from controller.config import Settings; print(Settings())"` as a quick verification step after the change.
- No issues found. This is a clean, well-scoped plan.

---

### Plan 06: Registry Merge -- PASS

**Strengths:**
- Merge-over-rebase recommendation is well-justified given the 574-line rewrite.
- File-by-file resolution strategy is clear ("TAKE PHASE 2" for all conflict files).
- Pre-merge checklist is thorough (8 verification steps).
- `ScoredSkill` import risk is correctly identified.

**Issues:**
- *[Important]* The plan assumes PR #3 exists as a separate branch, but the current git status shows we are on `main` with no feature branches visible. The plan should clarify: is this a merge from a branch, or are Phase 2 changes being applied directly? If the branch no longer exists, the merge strategy section is moot and this becomes a direct application of Phase 2 code.
- *[Suggestion]* The ADR ("Phase 2 Registry Supersedes Phase 1") should be committed as a standalone file in `docs/decisions/` rather than embedded in the plan document.

---

### Plan 07: E2E Integration Test -- PASS

**Strengths:**
- Five well-defined scenarios covering happy path, no match, classifier failure, budget exceeded, and bash entrypoint.
- Smart mock strategy: real registry/classifier, mocked Redis/embeddings/spawner.
- ADR explaining why not Docker Compose E2E is pragmatic.
- `conftest.py` fixture design with deterministic embedding vectors is clever.

**Issues:**
- *[Important]* The mock embedding provider uses 3-dimensional vectors (`[1.0, 0.0, 0.0]`), but the real `voyage-3` model produces 1024-dimensional vectors. If any code path validates vector dimensionality, these tests will fail. The plan should add a note about mocking the dimension validation or using the correct dimensionality.
- *[Suggestion]* Scenario 5 (bash entrypoint test) wraps a shell script in pytest. Consider using `subprocess.run` directly in Python instead of a separate `.sh` file + wrapper, reducing file count.
- *[Suggestion]* The `fakeredis` dependency should be pinned in `pyproject.toml` or `requirements-dev.txt`. The plan mentions adding it but does not specify the version.

---

### Plan 08: Seed Skills -- PASS

**Strengths:**
- Well-chosen starter set of 10 skills across 5 domains (frontend, backend, testing, DevOps, general).
- JSON fixture + CLI command delivery mechanism is the right approach for reproducibility.
- Idempotent seeding with `--force` flag and `--dry-run` for safety.
- Per-org configurability design (layered defaults with org overrides) is forward-looking without over-engineering v1.

**Issues:**
- *[Important]* The plan estimates 3 days for authoring 10 SKILL.md content files. This is the highest-effort item and depends on domain expertise. If content quality is important (it is -- these are the skills the system will use), consider having domain experts review each skill file. The 3-day estimate assumes parallelization across contributors, which may not be available.
- *[Suggestion]* The `starter_skills.json` structure should be validated against a JSON schema. Consider adding a schema file to prevent malformed skill definitions.
- *[Suggestion]* The plan does not address embedding generation for seed skills. If Phase 2 semantic search is active, seeded skills need embeddings computed at seed time. Add a `--compute-embeddings` flag or make it automatic when `skill_embedding_provider != "none"`.

---

## 2. Cross-Plan Analysis

### 2.1 Dependencies

```
Plan 05 (Config Dedup) ──> Plan 06 (Registry Merge) ──> Plan 02 (Gateway Wiring)
                                                    ──> Plan 07 (E2E Tests)
                                                    ──> Plan 08 (Seed Skills)

Plan 01 (Tool Backends) ──> Plan 02 (Gateway Wiring)

Plan 03 (Design Docs) ──> (independent, can run anytime)

Plan 04 (Open Questions) ──> Plan 02 (payload format alignment)
                         ──> Plan 08 (skill scope affects seeding)
```

Key dependency chain: **Plan 05 -> Plan 06 -> Plan 02 -> Plan 07**. The config dedup must happen before the registry merge to avoid carrying duplicate fields into the merged code. The registry merge must complete before gateway wiring (which depends on `matched_skills` from the skill pipeline). E2E tests validate the full pipeline and should come last.

### 2.2 Conflicts

| Plans | Conflicting Area | Resolution |
|-------|-----------------|------------|
| 02 + 04 | Redis task payload format | Plan 04 (Q6) adds `skill_overrides` to the payload that Plan 02 defines. Coordinate: implement Plan 02 first, then extend the payload in Plan 04. |
| 01 + 02 | `server.js` modifications | Plan 01 extracts handlers from `server.js` into `tools/`. Plan 02 does not modify `server.js` directly but depends on the tool registry structure. No actual conflict -- Plan 01 should complete first. |
| 05 + 06 | `config.py` modifications | Plan 05 cleans up duplicate fields. Plan 06 may introduce new config fields during merge. Run Plan 05 first so the merge target is clean. |

### 2.3 Gaps

1. **Monitoring and observability for the gateway.** Plan 01 implements tool backends and Plan 02 wires them in, but neither plan addresses metrics, logging, or alerting for gateway tool execution (latency, error rates, tool usage frequency). The `structured_logs` and `metrics_enabled` fields exist in config but no plan covers gateway metrics integration.

2. **Agent entrypoint changes.** Plan 02 adds `gateway_mcp` to the Redis payload, but no plan covers the agent-side entrypoint script changes needed to consume this field and configure the MCP client. The plan references "entrypoint.sh already handles this" but this should be verified and tested.

3. **Deployment and rollout strategy.** No plan covers how to roll out the gateway (feature flag progression, canary deployment, rollback procedure). The `gateway_enabled` flag exists but there is no plan for when and how to flip it.

4. **API documentation.** The REST API endpoints added in commit `c37d012` are not covered by any plan for OpenAPI/Swagger documentation.

### 2.4 Redundancy

- Plans 05 and 06 both touch `config.py` but in non-overlapping ways (dedup vs. merge). Minimal redundancy.
- Plans 04 (Q2: Skill Scope) and 08 (Per-Org Configurability) overlap on the concept of org-scoped skills. Plan 04 defines the data model, Plan 08 uses it. This is complementary, not redundant, but should be sequenced correctly (Plan 04 Q2 before Plan 08).

---

## 3. Recommended Execution Order

| Priority | Plan | Effort | Rationale |
|----------|------|--------|-----------|
| 1 | **Plan 05: Config Dedup** | 30 min | Quick win, zero risk, cleans foundation for everything else |
| 2 | **Plan 03: Design Docs Cleanup** | 1-2 hrs | Quick win, gets untracked files committed, no code risk |
| 3 | **Plan 06: Registry Merge** | 1.5 hrs | Unblocks Plans 02, 07, 08. Low effort, high leverage. |
| 4 | **Plan 01: Tool Backends** | 3-4 days | Independent of Python-side work. Can parallelize with Plan 04. |
| 5 | **Plan 04: Open Questions (Q6, Q2 only)** | 1.5 days | P0 items (classifier override, skill scope) needed before gateway wiring |
| 6 | **Plan 02: Gateway Wiring** | 4.5 hrs | Depends on Plans 01, 06. Core integration work. |
| 7 | **Plan 08: Seed Skills** | 6 days | Depends on registry merge. Content authoring can start earlier. |
| 8 | **Plan 07: E2E Tests** | 7.5 hrs | Should come last -- validates the full pipeline after all pieces are in place |

**Parallelization opportunities:**
- Plans 01 (JS/Node) and 04+06 (Python) can run in parallel on separate tracks.
- Plan 08's content authoring (SKILL.md files) can start during Plan 01 implementation.
- Plan 03 can be done at any time as a quick cleanup task.

---

## 4. Total Effort Estimate

| Plan | Estimate |
|------|----------|
| 01: Gateway Tool Backends | 3-4 days |
| 02: Gateway Orchestrator Wiring | 0.5 day |
| 03: Design Docs Cleanup | 0.25 day |
| 04: Open Questions (P0 + P1) | 5 days (full scope) / 1.5 days (P0 only) |
| 05: Config Dedup | 0.1 day |
| 06: Registry Merge | 0.25 day |
| 07: E2E Integration Tests | 1 day |
| 08: Seed Skills | 6 days (Phase 1) |
| **Total (all plans, full scope)** | **~16-17 developer-days** |
| **Total (P0 items only, deferred Q4/Q5/Q8)** | **~12-13 developer-days** |

For a single developer, this is approximately **2.5-3.5 weeks** of focused work. With two developers (one on JS/gateway, one on Python/skills), the critical path shortens to approximately **2 weeks** by parallelizing Plans 01 and 04+06+08.

The estimates in the individual plans are generally realistic, with the exception of Plan 01 (slightly optimistic for db-query security work) and Plan 08 (SKILL.md content authoring depends heavily on domain expert availability).

---

## 5. Top 3 Risks Across All Plans

### Risk 1: Agent Entrypoint Gap (Critical)
**What:** No plan covers the agent-side changes needed to consume `gateway_mcp` from the Redis task payload. Plan 02 adds the field to the payload, but the agent's `entrypoint.sh` must parse it and configure the MCP client connection. If this is not implemented, the entire gateway integration is dead code.
**Mitigation:** Add an explicit task to Plan 02 (or create a Plan 02b) covering `entrypoint.sh` modifications and testing. Verify the existing entrypoint script's MCP configuration logic.

### Risk 2: Registry Merge Branch Ambiguity (Important)
**What:** Plan 06 assumes a separate PR #3 branch exists with Phase 2 registry changes, but current git status shows only the `main` branch. If the branch has been deleted or the changes are already partially on main, the merge strategy in Plan 06 may be inapplicable, wasting time on conflict resolution that does not exist.
**Mitigation:** Before starting Plan 06, verify the current state of the Phase 2 registry code. Run `git branch -a` and check if `skills/registry.py` on main already contains Phase 2 changes. Adjust the plan accordingly.

### Risk 3: Seed Skill Content Quality (Important)
**What:** Plan 08 allocates 3 days for authoring 10 SKILL.md files, but these files are the core value proposition of the skill system. Low-quality skills (vague instructions, wrong patterns, outdated practices) will make the entire hotloading system appear broken even if the infrastructure works perfectly.
**Mitigation:** Define quality criteria for SKILL.md files before authoring begins (minimum word count, required sections, example code requirements). Have at least one domain expert review each skill file. Consider starting with 3-5 high-quality skills rather than 10 mediocre ones.

---

## Summary

All 8 plans pass review, though Plans 02, 04, 06, 07, and 08 each have Important-level issues that should be addressed before implementation. The plans are well-structured individually, but the cross-plan coordination needs attention -- particularly the Redis payload format alignment between Plans 02 and 04, and the critical gap around agent entrypoint changes.

The recommended execution order prioritizes quick wins (Plans 05, 03) to clean the codebase, then unblocks the dependency chain (Plan 06), followed by the core implementation work (Plans 01, 04, 02) and validation (Plans 07, 08).
