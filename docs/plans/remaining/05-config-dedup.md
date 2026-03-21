# Plan: Fix Duplicate Config Fields in config.py

## Status
Ready to implement

## Problem
`controller/src/controller/config.py` has duplicate field definitions accumulated across multi-phase implementation. Pydantic silently uses the **last** definition, so the earlier ones are dead code that creates confusion.

## 1. Duplicate/Conflicting Fields Found

| Field | Line (1st) | Line (2nd) | Line (3rd) | Conflict? |
|-------|-----------|-----------|-----------|-----------|
| `skill_registry_enabled` | 48 | 72 | 80 | No -- all `bool = False` |
| `skill_embedding_provider` | 49 | -- | 81 | Minor -- L81 has comment `"none" for Phase 1, "voyage" for Phase 2`; L49 has no comment. Same default. |
| `skill_embedding_model` | 50 | -- | 82 | No -- both `"voyage-3"` |
| `skill_max_per_task` | 51 | -- | 83 | No -- both `5` |
| `skill_min_similarity` | 52 | -- | 84 | No -- both `0.5` |
| `skill_max_total_chars` | 53 | -- | 85 | No -- both `16000` |
| `voyage_api_key` | 54 | -- | 86 | No -- both `""` |

**Summary**: 7 duplicate field definitions total across 3 sections. No actual value conflicts -- just dead code.

## 2. Which Definition to Keep

Keep the **first block** (lines 47-54, section `# Skill Registry`) because:
- It sits logically with other feature-flag groups (Integrations, MCP Gateway, API).
- The useful comment from L81 (`"none" for Phase 1, "voyage" for Phase 2`) should be merged into L49.

Remove:
- Line 71-72: Standalone `# Skill registry (Phase 3)` block with bare `skill_registry_enabled`.
- Lines 79-86: Full duplicate `# Skill Registry` block.

## 3. Exact Diff to Apply

```diff
--- a/controller/src/controller/config.py
+++ b/controller/src/controller/config.py
@@ -46,7 +46,7 @@

     # Skill Registry
     skill_registry_enabled: bool = False
-    skill_embedding_provider: str = "none"
+    skill_embedding_provider: str = "none"  # "none" for Phase 1, "voyage" for Phase 2
     skill_embedding_model: str = "voyage-3"
     skill_max_per_task: int = 5
     skill_min_similarity: float = 0.5
@@ -68,19 +68,6 @@
     subagent_depth_limit: int = 1

-    # Skill registry (Phase 3)
-    skill_registry_enabled: bool = False
-
     # Observability
     structured_logs: bool = True
     metrics_enabled: bool = False
     metrics_port: int = 9090

-    # Skill Registry
-    skill_registry_enabled: bool = False
-    skill_embedding_provider: str = "none"  # "none" for Phase 1, "voyage" for Phase 2
-    skill_embedding_model: str = "voyage-3"
-    skill_max_per_task: int = 5
-    skill_min_similarity: float = 0.5
-    skill_max_total_chars: int = 16000
-    voyage_api_key: str = ""
-
     model_config = {"env_prefix": "DF_"}
```

## 4. Test Impact

Tests referencing these fields (found in 4 files):
- `controller/tests/test_semantic_search.py`
- `controller/tests/test_skill_services.py`
- `controller/tests/test_subagent.py`
- `controller/tests/test_embedding.py`

**No test changes needed.** The field names and default values are identical. Removing duplicates does not change the Pydantic model's runtime behavior -- it already uses the last definition, which matches the first.

## 5. Effort Estimate

**Trivial** -- approximately 5 minutes.
- Delete 12 lines (two duplicate blocks).
- Add one inline comment to the kept `skill_embedding_provider` line.
- Run existing tests to confirm no regressions.
