# Plan 03: Design Docs Cleanup and Commit

## Status
Ready to execute

## Context
There are 13 untracked documentation files across `docs/` that were generated during the skill-hotloading and tracing design sessions on 2026-03-21. They need to be committed and organized before the directory becomes unwieldy.

---

## 1. Untracked Files to Commit

### Skill Hotloading Design (3 approach docs + 1 spec + 3 reviews)
| File | Category |
|------|----------|
| `docs/plans/approach-a-skill-registry.md` | Approach exploration (selected) |
| `docs/plans/approach-b-agent-type-matrix.md` | Approach exploration (rejected) |
| `docs/plans/approach-c-hybrid-platform.md` | Approach exploration (rejected) |
| `docs/superpowers/specs/2026-03-21-skill-hotloading-design.md` | Final design spec |
| `docs/reviews/2026-03-21-skill-hotloading-spec-review.md` | Code review of spec |
| `docs/reviews/2026-03-21-devops-ops-assessment.md` | DevOps review |
| `docs/reviews/2026-03-21-pm-cross-examination.md` | PM review |

### Tracing Design (4 approach docs + 1 review)
| File | Category |
|------|----------|
| `docs/plans/tracing-approach-a-otel-native.md` | Approach exploration |
| `docs/plans/tracing-approach-b-structured-logs.md` | Approach exploration |
| `docs/plans/tracing-approach-c-event-sourcing.md` | Approach exploration |
| `docs/plans/tracing-approach-d-langfuse.md` | Approach exploration |
| `docs/reviews/2026-03-21-traceability-analysis.md` | Cross-cutting review |

### Other
| File | Category |
|------|----------|
| `docs/architecture-diagram-changes.md` | Architecture delta doc |

**Total: 13 files**

---

## 2. Reorganization Recommendation

**Keep the current structure.** The existing layout is already well-organized:

```
docs/
├── architecture.md                          # (already committed)
├── architecture-diagram-changes.md          # NEW - commit as-is
├── plans/
│   ├── approach-a-skill-registry.md         # skill approaches
│   ├── approach-b-agent-type-matrix.md
│   ├── approach-c-hybrid-platform.md
│   ├── tracing-approach-a-otel-native.md    # tracing approaches
│   ├── tracing-approach-b-structured-logs.md
│   ├── tracing-approach-c-event-sourcing.md
│   ├── tracing-approach-d-langfuse.md
│   ├── contract-testing-plan.md             # (already committed)
│   ├── contract-testing-plan-review.md      # (already committed)
│   ├── e2e-integration-test-plan.md         # (already committed)
│   └── remaining/                           # execution plans
│       └── 03-design-docs-cleanup.md        # (this file)
├── reviews/
│   ├── 2026-03-21-devops-ops-assessment.md
│   ├── 2026-03-21-pm-cross-examination.md
│   ├── 2026-03-21-skill-hotloading-spec-review.md
│   ├── 2026-03-21-traceability-analysis.md
│   └── e2e-integration-test-plan-review.md  # (already committed)
└── superpowers/
    └── specs/
        ├── 2026-03-21-ditto-cli-skill-design.md  # (already committed)
        └── 2026-03-21-skill-hotloading-design.md
```

**No renames needed.** The naming conventions are consistent:
- Approach docs use `approach-{letter}-{name}.md` or `tracing-approach-{letter}-{name}.md`
- Reviews use `YYYY-MM-DD-{topic}.md`
- Specs use `YYYY-MM-DD-{topic}.md`

---

## 3. Approach B and C: Keep, Do Not Archive

**Recommendation: Keep approach-b and approach-c in `docs/plans/` alongside approach-a.**

Rationale:
- The skill-hotloading design spec (the final deliverable) explicitly references Phase 4 as "cherry-picked from Approach B" and Phase 5 as "cherry-picked from Approach C"
- These docs serve as architectural decision context -- understanding *why* Approach A was selected requires seeing the alternatives
- Moving them to an `archive/` folder suggests they are irrelevant, when they are actually referenced design context
- The files are static documentation, not code -- they impose zero maintenance cost where they are

The same applies to tracing approaches B, C, D -- no decision has been made yet on tracing, so all four remain active candidates.

---

## 4. Proposed docs/ Directory Structure

No structural changes needed. The current layout follows a clear pattern:

| Directory | Purpose |
|-----------|---------|
| `docs/` | Top-level architecture docs |
| `docs/plans/` | Design explorations, approach comparisons, test plans |
| `docs/plans/remaining/` | Execution checklists for upcoming work |
| `docs/reviews/` | Date-stamped review reports from different perspectives |
| `docs/superpowers/specs/` | Final accepted design specifications |

This structure scales well. If it grows beyond ~20 files in `plans/`, consider subdirectories like `plans/skill-hotloading/` and `plans/tracing/`, but that is not needed today.

---

## 5. Commit Message

```
Add skill-hotloading and tracing design documentation

Include approach explorations (A/B/C for skills, A/B/C/D for tracing),
the skill-hotloading design spec, review reports from code review,
DevOps, PM, and traceability perspectives, and architecture diagram
change notes.
```

---

## 6. Effort Estimate

**~5 minutes.** This is a single `git add` + `git commit`. No file moves, no renames, no content edits required.

```bash
cd /Users/tedahn/Documents/codebase/ditto-factory
git add docs/
git commit -m "Add skill-hotloading and tracing design documentation

Include approach explorations (A/B/C for skills, A/B/C/D for tracing),
the skill-hotloading design spec, review reports from code review,
DevOps, PM, and traceability perspectives, and architecture diagram
change notes."
```
