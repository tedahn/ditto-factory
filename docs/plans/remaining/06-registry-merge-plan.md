# Plan: Resolving Phase 1 vs Phase 2 Registry Divergence

**Date**: 2026-03-21
**Status**: Proposed
**Scope**: Merge strategy for PR #3 (Phases 2-5) into main (Phase 1)

---

## 1. Expected Conflicts

The diff shows **27 files changed, +3001 / -375 lines**. The primary conflict zone is the `controller/src/controller/skills/` package.

### High-conflict files

| File | Conflict Severity | Reason |
|------|-------------------|--------|
| `skills/registry.py` | **Critical** | 574 lines changed. Phase 1 has `__init__(self, db_path: str)`, Phase 2 adds `embedding_provider: EmbeddingProvider \| None = None`. Methods rewritten: `create()` now generates embeddings, new `search_by_embedding()` and `ScoredSkill` support. Import list changed (`uuid`/`timezone` removed, `math`/`EmbeddingProvider`/`ScoredSkill` added). |
| `skills/classifier.py` | **High** | 151 lines changed. Phase 2 adds `EmbeddingProvider`, `EmbeddingCache`, `PerformanceTracker` imports and embedding-based fallback matching. |
| `skills/__init__.py` | **Medium** | Phase 2 appends embedding exports (`EmbeddingProvider`, `EmbeddingError`, `VoyageEmbeddingProvider`, `NoOpEmbeddingProvider`, `create_embedding_provider`, `EmbeddingCache`). |
| `skills/api.py` | **Medium** | 25 lines changed for embedding-aware endpoints. |
| `skills/tracker.py` | **High** | 158 lines added for performance tracking enhancements. |

### New files (no conflict, additive)

- `skills/embedding.py` (117 lines) -- Voyage-3 embedding provider
- `skills/embedding_cache.py` (37 lines) -- LRU cache for embeddings
- `controller/subagent.py` (215 lines) -- Subagent spawning
- `images/gateway/` -- MCP Gateway (Dockerfile, server.js)
- All new test files (`test_embedding.py`, `test_semantic_search.py`, `test_performance_tracker.py`, `test_subagent.py`, `test_gateway.py`)

### Modified tests (likely to conflict)

| File | Lines Changed | Risk |
|------|--------------|------|
| `test_skill_api.py` | 13 lines | Low -- minor adjustments |
| `test_skill_services.py` | 16 lines | Low -- constructor signature changes |

---

## 2. Rebase vs Merge: Recommendation

**Recommendation: Merge (not rebase).**

Rationale:

| Factor | Merge | Rebase |
|--------|-------|--------|
| Conflict resolution | Resolve once | Resolve per-commit (10+ commits on PR #3) |
| History | Preserves PR #3's commit sequence | Cleaner linear history but higher risk |
| Reversibility | Easy to revert merge commit | Hard to undo if conflicts resolved incorrectly |
| Risk | Low -- single conflict resolution | High -- `registry.py` has 574 lines changed across many commits |

Since `registry.py` was essentially rewritten (not incrementally patched), rebasing would force re-resolution of the same semantic conflict across multiple commits. A merge commit resolves it once.

---

## 3. File-by-File Resolution Strategy

### `skills/registry.py` -- TAKE PHASE 2 ENTIRELY

Phase 2 is a superset of Phase 1. The Phase 2 registry:
- Preserves all Phase 1 CRUD methods (`create`, `get`, `update`, `delete`, `list_skills`, `search_by_tags`)
- Adds embedding support via optional `embedding_provider` parameter (backward-compatible)
- Adds `search_by_embedding()`, `_cosine_similarity()`, `_ensure_embedding_column()`
- Changes internal helpers (e.g., `_now_str()` simplified, `uuid` generation approach)

**Resolution**: Accept all Phase 2 changes. Discard Phase 1 version.

### `skills/classifier.py` -- TAKE PHASE 2 ENTIRELY

Phase 2 classifier adds embedding-based matching with tag-based fallback. It is backward-compatible: when no embedding provider is configured, it falls back to Phase 1 tag matching.

**Resolution**: Accept all Phase 2 changes.

### `skills/__init__.py` -- TAKE PHASE 2 (ADDITIVE)

Phase 2 appends embedding imports. Phase 1 exports are preserved.

**Resolution**: Accept Phase 2. No Phase 1 exports are removed.

### `skills/api.py` -- TAKE PHASE 2

Minor changes to support embedding-aware skill creation.

**Resolution**: Accept Phase 2 changes.

### `skills/tracker.py` -- TAKE PHASE 2

Significant additions for performance tracking. Phase 1 tracker is a subset.

**Resolution**: Accept Phase 2 changes.

### `test_skill_api.py` and `test_skill_services.py` -- TAKE PHASE 2

Tests updated to match Phase 2 constructor signatures. Minimal changes.

**Resolution**: Accept Phase 2 changes.

### New files -- NO CONFLICT

All new files (`embedding.py`, `embedding_cache.py`, `subagent.py`, gateway files, new test files) are additive. No resolution needed.

---

## 4. Phase 1 Test Compatibility with Phase 2 Registry

### Constructor change is backward-compatible

```
Phase 1: SkillRegistry(db_path: str)
Phase 2: SkillRegistry(db_path: str, embedding_provider: EmbeddingProvider | None = None)
```

The `embedding_provider` parameter defaults to `None`, so **existing Phase 1 call sites that pass only `db_path` will continue to work without modification**.

### Test files that reference the registry

Located at:
- `controller/tests/test_skill_services.py` -- Updated in PR #3 (16 lines changed)
- `controller/tests/test_skill_api.py` -- Updated in PR #3 (13 lines changed)
- `controller/tests/integrations/test_registry.py` -- May need verification
- `controller/tests/contracts/test_orchestrator_contracts.py` -- References registry indirectly

### Risks

1. **`SkillVersion` import removal**: Phase 2 registry uses a lazy import for `SkillVersion` (line 210: `from controller.skills.models import SkillVersion`). Phase 1 imports it at module level. Tests that rely on `SkillVersion` being importable from `registry` will break -- but none do (it is imported from `models`).

2. **`ScoredSkill` model**: Phase 2 imports `ScoredSkill` from models. If Phase 1 models do not define `ScoredSkill`, the registry module will fail to import. **Verify that the models file on main includes `ScoredSkill`** -- it should, since the models were added in Phase 1 (commit `4a460ed`).

3. **`uuid` / `timezone` removal**: Phase 2 removes `import uuid` and `from datetime import timezone`. Any Phase 1 tests that monkey-patch these on the registry module will break. Unlikely but worth checking.

### Verdict

Phase 1 tests should pass with Phase 2 registry **after PR #3's test modifications are applied**, since the constructor is backward-compatible and PR #3 already updates the affected test files.

---

## 5. Pre-Merge Checklist

- [ ] Verify `ScoredSkill` exists in `controller/skills/models.py` on main
- [ ] Verify `EmbeddingProvider` and `EmbeddingError` exist in `controller/skills/embedding.py` (new file from PR #3)
- [ ] Run Phase 1 tests against Phase 2 registry with `embedding_provider=None`:
  ```bash
  cd controller && python -m pytest tests/test_skill_services.py tests/test_skill_api.py -v
  ```
- [ ] Run full PR #3 test suite:
  ```bash
  cd controller && python -m pytest tests/ -v
  ```
- [ ] Confirm no other branches depend on Phase 1 registry internals (grep for `from controller.skills.registry import` across all branches)
- [ ] Review `controller/tests/integrations/test_registry.py` for Phase 1 constructor assumptions
- [ ] Ensure `images/gateway/Dockerfile` and `src/mcp/gateway/` do not conflict with any main-branch gateway work
- [ ] After merge, verify imports:
  ```python
  from controller.skills.registry import SkillRegistry
  from controller.skills.classifier import TaskClassifier
  from controller.skills import EmbeddingProvider, EmbeddingCache
  ```

---

## 6. Effort Estimate

| Task | Effort |
|------|--------|
| Merge conflict resolution | 30 min (mostly `registry.py` -- take Phase 2) |
| Test verification | 30 min (run existing + new tests) |
| `ScoredSkill` model verification | 10 min |
| Integration smoke test | 20 min |
| **Total** | **~1.5 hours** |

### Why this is low-effort

The merge is conceptually simple despite the large diff: Phase 2 is a **strict superset** of Phase 1. The strategy for every conflicting file is "take Phase 2." There are no cases where Phase 1 added functionality that Phase 2 lacks. The only risk is a missing model definition (`ScoredSkill`) which is a 5-minute fix if absent.

---

## ADR: Phase 2 Registry Supersedes Phase 1

### Status
Proposed

### Context
PR #1 introduced a Phase 1 skill registry with tag-based CRUD. PR #3 rewrites the registry to add embedding-based semantic search while preserving all Phase 1 functionality. The two implementations diverge significantly in `registry.py` (574 lines changed) but Phase 2 is backward-compatible via optional `embedding_provider` parameter.

### Decision
When merging PR #3, accept all Phase 2 changes for every conflicting file. Phase 1 registry code is fully superseded. No Phase 1-specific code paths need preservation.

### Consequences
- **Easier**: Single registry implementation to maintain. Embedding support is opt-in (graceful degradation when provider is None).
- **Harder**: Rollback to Phase 1 requires reverting the merge commit, which also reverts Phases 2-5. Consider this a one-way door.
