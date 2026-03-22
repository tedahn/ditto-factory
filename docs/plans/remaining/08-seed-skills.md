# Plan 08: Seed the Skill Registry with Starter Skills

## Status: Proposed
**Author:** Software Architect Agent
**Date:** 2026-03-21

---

## 1. Problem Statement

After a fresh Ditto Factory deployment, the skill registry is empty. Teams must manually author every skill before agents can do useful work. This creates a cold-start problem: the system appears inert until someone writes SKILL.md files and registers them through the API.

We need a curated set of 10 starter skills that cover the most common task types, are safe to apply broadly, and demonstrate best practices for skill authoring.

---

## 2. Proposed Starter Skill Set (10 Skills)

### Frontend Domain

| # | Name | Slug | Description | Languages | Domain | Tags |
|---|------|------|-------------|-----------|--------|------|
| 1 | React Debugger | `react-debug` | Diagnose React rendering issues, hook misuse, and state management bugs | `typescript`, `javascript` | `frontend` | `react`, `debugging`, `hooks` |
| 2 | CSS Review | `css-review` | Review CSS/SCSS for specificity issues, layout bugs, responsive breakpoints, and design-system compliance | `css`, `scss` | `frontend` | `styling`, `review`, `responsive` |
| 3 | Accessibility Audit | `a11y-audit` | Check components for WCAG 2.1 AA compliance: ARIA roles, focus management, contrast, semantic HTML | `typescript`, `javascript`, `html` | `frontend` | `accessibility`, `wcag`, `audit` |

### Backend Domain

| # | Name | Slug | Description | Languages | Domain | Tags |
|---|------|------|-------------|-----------|--------|------|
| 4 | API Design Reviewer | `api-design` | Review REST/GraphQL API designs for consistency, naming conventions, error handling, and versioning | `python`, `typescript`, `go` | `backend` | `api`, `rest`, `review` |
| 5 | Database Migration Author | `db-migration` | Write safe, reversible database migrations with rollback plans and data-integrity checks | `python`, `sql` | `backend` | `database`, `migration`, `schema` |
| 6 | Error Handling Hardener | `error-handling` | Improve error handling: add structured error types, retry logic, circuit breakers, and user-facing messages | `python`, `typescript`, `go` | `backend` | `errors`, `resilience`, `reliability` |

### Testing Domain

| # | Name | Slug | Description | Languages | Domain | Tags |
|---|------|------|-------------|-----------|--------|------|
| 7 | Test Writer | `test-writer` | Generate unit and integration tests for existing code, targeting uncovered branches and edge cases | `python`, `typescript`, `go` | `testing` | `unit-test`, `integration-test`, `coverage` |
| 8 | Test Debugger | `test-debug` | Diagnose flaky or failing tests: isolate root cause, fix assertions, mock mismatches, and timing issues | `python`, `typescript`, `go` | `testing` | `debugging`, `flaky-tests`, `ci` |

### DevOps Domain

| # | Name | Slug | Description | Languages | Domain | Tags |
|---|------|------|-------------|-----------|--------|------|
| 9 | Dockerfile Reviewer | `dockerfile-review` | Review Dockerfiles for security (non-root user, minimal base image), layer optimization, and build caching | `dockerfile` | `devops` | `docker`, `security`, `optimization` |

### General Domain

| # | Name | Slug | Description | Languages | Domain | Tags |
|---|------|------|-------------|-----------|--------|------|
| 10 | Code Reviewer | `code-review` | General-purpose code review: readability, naming, complexity, SOLID principles, and PR-ready feedback | `python`, `typescript`, `go`, `java`, `rust` | `general` | `review`, `quality`, `refactoring` |

All 10 skills have `is_default: true` and `org_id: null` (global defaults).

---

## 3. Content Outline Per Skill

Each skill's `content` field is a SKILL.md-format markdown document injected into the agent's prompt. Every skill MUST follow this template structure:

```markdown
# <Skill Name>

## Role
One-sentence identity statement for the agent.

## Scope
- What this skill covers (3-5 bullet points)
- Explicit boundaries: what it does NOT do

## Process
Step-by-step instructions the agent must follow:
1. Gather context (what to read first)
2. Analysis phase (what to look for)
3. Action phase (what to produce)
4. Verification phase (how to validate the output)

## Rules
- Hard constraints (e.g., "never modify production data")
- Style preferences (e.g., "prefer explicit over clever")
- Output format requirements

## Examples (optional)
Before/after snippets showing expected behavior.
```

### Per-Skill Content Notes

| Slug | Key Instructions to Include |
|------|-----------------------------|
| `react-debug` | Check React DevTools patterns, identify unnecessary re-renders, validate hooks rules (deps arrays), check for stale closures, suggest React.memo/useMemo only when profiling justifies it |
| `css-review` | Flag `!important` overuse, check for magic numbers, validate responsive breakpoints match design system, prefer logical properties, check dark-mode support |
| `a11y-audit` | Run against WCAG 2.1 AA checklist, check tab order, validate ARIA roles match semantics, ensure form labels, check color contrast ratios, test keyboard navigation |
| `api-design` | Check REST resource naming, HTTP method semantics, pagination patterns, error response format (RFC 7807), auth header conventions, versioning strategy |
| `db-migration` | Require `up` and `down` migrations, check for table locks on large tables, validate index additions, warn on column renames (prefer add+backfill+drop), check foreign key cascades |
| `error-handling` | Introduce error type hierarchies, replace bare `except`/`catch` with specific types, add correlation IDs, ensure errors are logged before re-thrown, check retry idempotency |
| `test-writer` | Target uncovered branches first, use Arrange-Act-Assert structure, mock external dependencies, parametrize edge cases, ensure test names describe the scenario not the method |
| `test-debug` | Identify non-determinism sources (time, random, network), check test isolation, validate mock setup matches production behavior, suggest `pytest -x --tb=short` workflow |
| `dockerfile-review` | Check multi-stage builds, validate non-root USER, pin base image digests, minimize layers, separate build and runtime deps, check .dockerignore exists |
| `code-review` | Check cyclomatic complexity, flag functions >50 lines, validate naming consistency, check error paths, suggest extractions for repeated logic, verify PR scope is focused |

---

## 4. Delivery Mechanism

### Decision: JSON fixture file + seed CLI command

**Why not migrations?** Skills are application data, not schema. Mixing them into Alembic/schema migrations couples deployment to content changes.

**Why not raw API calls?** Requires the server to be running. Seed should work during initial deployment before the API is exposed.

**Why fixture file?** Declarative, version-controlled, easy to review in PRs, supports per-org overrides.

### Implementation

```
controller/
  src/controller/skills/
    seeds/
      __init__.py
      starter_skills.json      # The 10 skill definitions
      contents/                 # Individual SKILL.md files
        react-debug.md
        css-review.md
        a11y-audit.md
        api-design.md
        db-migration.md
        error-handling.md
        test-writer.md
        test-debug.md
        dockerfile-review.md
        code-review.md
    seed.py                    # Seed loader module
```

#### `starter_skills.json` structure

```json
[
  {
    "name": "React Debugger",
    "slug": "react-debug",
    "description": "Diagnose React rendering issues...",
    "content_file": "contents/react-debug.md",
    "language": ["typescript", "javascript"],
    "domain": ["frontend"],
    "tags": ["react", "debugging", "hooks"],
    "is_default": true,
    "created_by": "system:seed"
  }
]
```

The `content_file` field references the SKILL.md file. The seed loader reads it and populates the `content` field. This keeps the JSON metadata compact and the skill content editable as standalone markdown.

#### `seed.py` module

```python
async def seed_skills(registry, *, force: bool = False) -> SeedResult:
    """
    Load starter skills into the registry.

    Args:
        registry: SkillRegistry instance
        force: If True, overwrite existing skills with same slug

    Returns:
        SeedResult with counts of created, skipped, and updated skills
    """
```

Behavior:
- **Idempotent**: Skip skills whose slug already exists (unless `force=True`)
- **Versioned**: When `force=True`, update existing skills and bump version
- **Logged**: Print summary of what was created/skipped/updated

#### CLI entry point

```bash
# Seed during deployment
python -m controller.skills.seed

# Force re-seed (overwrite existing)
python -m controller.skills.seed --force

# Seed specific skills only
python -m controller.skills.seed --only react-debug,code-review

# Dry run
python -m controller.skills.seed --dry-run
```

#### Integration with deployment

Add to the Helm chart / docker-compose as an init container or post-start hook:

```yaml
initContainers:
  - name: seed-skills
    image: ditto-controller:latest
    command: ["python", "-m", "controller.skills.seed"]
```

---

## 5. Per-Org Configurability

### Approach: Layered defaults with org overrides

```
Global defaults (is_default=true, org_id=null)
  |
  v
Org overrides (org_id="acme-corp")
  |
  v
Repo overrides (repo_pattern="acme-corp/payments-*")
```

#### Mechanism

1. **Org seed profiles**: An org admin can create a JSON file listing which default skills to enable/disable and any org-specific skill overrides.

```json
{
  "org_id": "acme-corp",
  "exclude_defaults": ["dockerfile-review"],
  "override_skills": [
    {
      "slug": "code-review",
      "content_file": "acme-code-review.md"
    }
  ],
  "additional_skills": [
    {
      "slug": "acme-compliance",
      "name": "ACME Compliance Check",
      "content_file": "acme-compliance.md",
      "domain": ["compliance"],
      "tags": ["acme", "soc2"]
    }
  ]
}
```

2. **Skill resolution order** (at task classification time):
   - Repo-pattern-matched skills (most specific)
   - Org-specific skills
   - Global defaults (excluding org-excluded slugs)

3. **API support**: Add `POST /api/v1/orgs/{org_id}/skill-profile` endpoint (Phase 2) for managing org profiles through the API instead of JSON files.

### Trade-offs

| Approach | Pro | Con |
|----------|-----|-----|
| JSON profiles in repo | Version-controlled, declarative | Requires re-deploy to change |
| API-managed profiles | Dynamic, self-service | Needs UI/CLI tooling, harder to audit |
| **Both (recommended)** | JSON for bootstrap, API for runtime | Two code paths to maintain |

---

## 6. Effort Estimate

| Task | Effort | Dependencies |
|------|--------|--------------|
| Write `seed.py` loader + CLI | 1 day | Existing `SkillRegistry` |
| Write `starter_skills.json` metadata | 0.5 day | Skill schema finalized |
| Author 10 SKILL.md content files | 3 days | Domain expertise |
| Unit tests for seed loader | 0.5 day | `seed.py` complete |
| Integration test (seed + query) | 0.5 day | Registry tests exist |
| Helm/docker-compose init container | 0.5 day | Deployment config |
| Org profile system (Phase 2) | 2 days | API + resolution logic |
| **Total Phase 1** | **6 days** | |
| **Total with Phase 2 org profiles** | **8 days** | |

---

## 7. Implementation Order

1. **Day 1**: `seed.py` module + CLI with `--dry-run` and `--force` flags
2. **Day 1.5**: `starter_skills.json` with metadata for all 10 skills
3. **Days 2-4**: Author the 10 SKILL.md content files (parallelize across contributors)
4. **Day 4.5**: Unit + integration tests
5. **Day 5**: Helm init container config
6. **Day 6**: Manual QA -- deploy fresh, verify skills appear, test search/filter

---

## 8. Success Criteria

- Fresh deployment shows 10 skills via `GET /api/v1/skills`
- Skills are filterable by language and domain
- `seed --dry-run` shows what would be created without side effects
- Re-running seed is idempotent (no duplicates)
- Each SKILL.md produces correct agent behavior when injected (manual verification)

---

## 9. ADR

### ADR-008: Seed skills via JSON fixture + CLI command

**Status:** Proposed

**Context:** The skill registry is empty after deployment. We need a repeatable, version-controlled way to populate it with starter skills that works in CI/CD pipelines, local development, and production.

**Decision:** Use a JSON fixture file (`starter_skills.json`) referencing individual SKILL.md content files, loaded by a `seed.py` CLI module. The seed runs as an init container in Kubernetes and is idempotent by default.

**Consequences:**
- *Easier*: Adding new starter skills (just add a JSON entry + markdown file). Reviewing skill changes in PRs. Running in any environment without API dependency.
- *Harder*: Dynamic skill management requires separate API tooling (Phase 2). Two content formats to maintain (JSON metadata + markdown content) vs a single API payload.
