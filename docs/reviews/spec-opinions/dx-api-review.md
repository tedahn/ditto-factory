# DX Review: Two-State Workflow Engine Design Spec

**Reviewer:** Developer Advocate Agent
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Existing API reference:** `controller/src/controller/skills/api.py`

---

## DX Scorecard

| Category | Score (1-5) | Summary |
|:---------|:-----------:|:--------|
| API Ergonomics | 4 | Consistent with existing skills API; good REST patterns; a few naming gaps |
| Template Authoring | 2 | JSON schema is powerful but steep learning curve; no validation tooling |
| Execution Monitoring | 4 | GET execution response is excellent; missing real-time/streaming |
| Debugging | 3 | Step-level errors exist; per-agent error drilldown needs work |
| Documentation | 3 | Spec has examples but no curl commands; error responses undefined |
| SDK/CLI Experience | 1 | No CLI tooling, no SDK helpers, no `curl` examples in spec |
| Error Handling | 2 | Error field exists but no structured error codes or actionable hints |
| Versioning | 3 | Immutable versions is correct; UX for "what happens to running workflows" is unclear |

**Overall DX Score: 2.75 / 5** -- Strong architecture, weak developer surface area.

---

## Category Details

### 1. API Ergonomics (4/5)

**What works well:**
- Endpoint naming follows the existing `/api/v1/skills` convention exactly (`/api/v1/workflows/templates`, `/api/v1/workflows/executions`). Developers who learned the skills API will feel at home.
- Using slugs as identifiers (not UUIDs) for template lookups is the right call -- `GET /api/v1/workflows/templates/geo-search` reads better than `GET /api/v1/workflows/templates/tmpl_abc123`.
- Separation of templates (definitions) and executions (runs) is clear and intuitive.

**Friction points:**
- **Missing pagination on executions.** The skills API has `page` and `per_page` on `GET /skills`. The workflow spec shows `GET /api/v1/workflows/executions` as "filterable by status" but does not define pagination parameters. With fan-out workflows generating many executions, this will break quickly.
- **Inconsistent ID prefixes.** The response example shows `"id": "exec_def456"` and `"id": "tmpl_abc123"` but the SQL schema uses bare UUIDs (`uuid.uuid4().hex`). Pick one convention and use it everywhere.
- **`POST /api/v1/workflows/execute` vs `/executions`** -- the verb endpoint feels like an RPC hiding in a REST API. Suggestion: `POST /api/v1/workflows/executions` (creating an execution resource) with `template_slug` in the body. This matches REST semantics and the existing `POST /api/v1/skills` pattern.

**Suggested fix:**
```
# Instead of:
POST /api/v1/workflows/execute

# Use:
POST /api/v1/workflows/executions
```

This also means listing and creating use the same base path, which is how the skills API works.

### 2. Template Authoring (2/5)

**This is the biggest DX gap in the spec.**

**Friction points:**
- **No template validation endpoint.** A developer writes a 50-line JSON template and POSTs it. If it fails, what error do they get? The spec shows DAG validation in the compiler but does not define a `POST /api/v1/workflows/templates/validate` (dry-run) endpoint. Developers should be able to validate without creating.
- **The `over` syntax is non-obvious.** `"over": "regions x sources"` uses a custom DSL (cartesian product operator `x`). This will confuse developers. Where is this documented? What happens with `"over": "regions"` (single dimension)? What about `"over": "regions x sources x categories"` (3-way)? The spec mentions single-dimension but does not show `x` operator precedence or limits.
- **Jinja2 in JSON is painful.** Template authors must write `"task_template": "Search {{ source }} for {{ query }} in {{ region }}"` inside JSON strings. Escaping is fragile. Consider supporting YAML template definitions (converted to JSON internally) or at minimum, provide a template rendering preview endpoint.
- **No example of a conditional step template.** The TypeScript types define `ConditionalStep` but neither starter template uses it. First-time template authors have no example to copy from.
- **The `input` field semantics vary by step type.** In `AggregateStep`, `input: "search.*"` uses glob syntax. In `TransformStep`, `input: "merge"` is a bare step ID. In `ReportStep`, `input: "dedupe"` is also bare. Are these the same resolver? What values are valid? This needs a clear reference table.

**Suggested additions:**
1. `POST /api/v1/workflows/templates/validate` -- validates template JSON, returns errors or a preview of the expanded DAG
2. `POST /api/v1/workflows/templates/{slug}/preview` -- given parameters, shows what the compiled execution plan would look like (step count, agent count, estimated cost) without actually running it
3. A "Template Authoring Guide" with at least 3 examples of increasing complexity

**Suggested curl (validate):**
```bash
curl -X POST http://localhost:8000/api/v1/workflows/templates/validate \
  -H "Content-Type: application/json" \
  -d '{
    "definition": {
      "steps": [
        {"id": "search", "type": "fan_out", "over": "regions x sources", ...},
        {"id": "merge", "type": "aggregate", "depends_on": ["search"], ...}
      ]
    },
    "parameters": {
      "regions": ["Dallas, TX"],
      "sources": ["eventbrite"]
    }
  }'
```
**Expected response:**
```json
{
  "valid": true,
  "dag": {
    "steps": 4,
    "agents_to_spawn": 1,
    "estimated_duration_seconds": 300,
    "warnings": []
  }
}
```

### 3. Execution Monitoring (4/5)

**What works well:**
- The GET execution response (Section 9.2) is genuinely excellent. It shows workflow status, each step's status, and individual agent status within fan-out steps. A developer can answer "what's happening?" in one API call.
- Including `region` and `source` labels on agent entries is smart -- you can see which agent is doing what.

**Friction points:**
- **No SSE/WebSocket endpoint for live updates.** Workflows can run for minutes. Polling `GET /executions/{id}` every 2 seconds wastes bandwidth and adds latency. Add `GET /api/v1/workflows/executions/{id}/stream` (SSE) that pushes step state changes.
- **No execution history/timeline.** The response shows current state but not state transitions. When debugging, developers need: "step `search` started at T1, agent 0 completed at T2, agent 1 failed at T3, retried at T4." Add a `timeline` or `events` array to the execution response.
- **No duration per step/agent.** `started_at` and `completed_at` exist in the schema but are not shown in the API response example. Always include them -- they are the first thing a developer looks at when a workflow is slow.

**Suggested curl (monitor):**
```bash
# Poll execution status
curl http://localhost:8000/api/v1/workflows/executions/exec_def456

# Stream live updates (SSE)
curl -N http://localhost:8000/api/v1/workflows/executions/exec_def456/stream
```

### 4. Debugging (3/5)

**What works well:**
- Per-agent error tracking in `workflow_step_agents` table.
- `on_failure: continue` semantics let partial failures surface without killing the workflow.
- Step-level `error` field and `retry_count` tracking.

**Friction points:**
- **No structured error codes.** The `error` field is a free-text string (`"3 agents failed"`). Developers cannot programmatically react to errors. Use structured error objects:
  ```json
  {
    "error_code": "PARTIAL_FAN_OUT_FAILURE",
    "message": "3 of 4 agents failed in step 'search'",
    "failed_agents": [1, 2, 3],
    "details": [
      {"agent_index": 1, "error": "Timeout after 300s"},
      {"agent_index": 2, "error": "Schema validation: missing field 'date'"},
      {"agent_index": 3, "error": "Agent exited with code 1: rate limited by eventbrite"}
    ]
  }
  ```
- **No agent logs endpoint.** When an agent fails, the developer needs to see what it tried. The spec stores `stderr` in the agent result but there is no API to retrieve individual agent output/logs. Add `GET /api/v1/workflows/executions/{id}/steps/{step_id}/agents/{index}`.
- **No "replay step" capability.** If step 3 of 5 fails, you have to re-run the entire workflow. A `POST /api/v1/workflows/executions/{id}/steps/{step_id}/retry` would save significant time and cost.

**Suggested curl (debug failed agent):**
```bash
# Get specific agent details including stderr and output
curl http://localhost:8000/api/v1/workflows/executions/exec_def456/steps/search/agents/1

# Expected response:
# {
#   "agent_index": 1,
#   "status": "failed",
#   "input": {"region": "Dallas, TX", "source": "meetup"},
#   "output": null,
#   "error": "Timeout after 300s",
#   "stderr": "Error: API rate limit exceeded...",
#   "started_at": "2026-03-25T10:01:15Z",
#   "completed_at": "2026-03-25T10:06:15Z",
#   "k8s_job_name": "wf-exec-def456-search-1",
#   "duration_seconds": 300
# }
```

### 5. Documentation (3/5)

**What works well:**
- The spec itself is well-structured with clear sections.
- Starter templates provide copy-paste starting points.
- The glossary (Appendix B) is genuinely useful for onboarding.

**Friction points:**
- **No curl examples anywhere in the spec.** A developer reading this spec cannot test a single endpoint without guessing the request format. Every endpoint should have a curl example.
- **Error responses are undefined.** What does a 400 look like? A 409 (duplicate slug)? The skills API returns `HTTPException(status_code=404, detail=f"Skill '{slug}' not found")` -- is the workflow API doing the same? Define the error response schema.
- **No OpenAPI/Swagger mention.** The existing skills API uses FastAPI, which auto-generates OpenAPI. Confirm the workflow API does the same and mention where developers can find the interactive docs (`/docs`, `/redoc`).
- **The `parameter_schema` field is JSON Schema but this is not stated explicitly.** Say "this uses JSON Schema Draft 7" (or whichever version `jsonschema` validates against).

### 6. SDK/CLI Experience (1/5)

**This category scores lowest because there is nothing here.**

**What is missing:**
- **No CLI commands.** A developer should be able to:
  ```bash
  ditto workflow list-templates
  ditto workflow create-template --file template.yaml
  ditto workflow validate --file template.yaml
  ditto workflow run geo-search --param query="music events" --param regions='["Dallas"]'
  ditto workflow status exec_def456
  ditto workflow cancel exec_def456
  ditto workflow logs exec_def456 --step search --agent 1
  ```
- **No Python SDK.** Template authors should not be writing raw JSON. Provide a builder:
  ```python
  from ditto.workflows import Template, FanOutStep, AggregateStep

  t = Template("geo-search", "Geographic Search")
  t.add_parameter("regions", type="array", required=True)
  t.add_step(FanOutStep("search", over="regions x sources", agent=...))
  t.add_step(AggregateStep("merge", depends_on=["search"], strategy="merge_arrays"))
  t.validate()  # raises if DAG invalid
  t.save()      # POST to API
  ```
- **No curl examples.** See documentation section above.

**Minimum viable curl examples that should exist in the spec:**

```bash
# Create a template
curl -X POST http://localhost:8000/api/v1/workflows/templates \
  -H "Content-Type: application/json" \
  -d @template.json

# List templates
curl http://localhost:8000/api/v1/workflows/templates

# Run a workflow
curl -X POST http://localhost:8000/api/v1/workflows/executions \
  -H "Content-Type: application/json" \
  -d '{
    "template_slug": "geo-search",
    "parameters": {
      "query": "music events",
      "regions": ["Dallas, TX", "Austin, TX"],
      "sources": ["eventbrite", "meetup"]
    }
  }'

# Check status
curl http://localhost:8000/api/v1/workflows/executions/exec_def456

# Cancel
curl -X POST http://localhost:8000/api/v1/workflows/executions/exec_def456/cancel
```

### 7. Error Handling (2/5)

**Friction points:**
- **No error response schema defined.** The skills API uses FastAPI's `HTTPException` with a `detail` string. This is the bare minimum. The workflow API should return structured errors:
  ```json
  {
    "error": {
      "code": "TEMPLATE_VALIDATION_FAILED",
      "message": "Workflow template contains a cycle",
      "details": {
        "cycle": ["step_a", "step_b", "step_a"],
        "hint": "Remove the dependency from step_b -> step_a to break the cycle"
      }
    }
  }
  ```
- **No error catalog.** The spec introduces several failure modes (DAG cycle, parameter validation, fan-out partial failure, timeout, schema validation) but does not enumerate error codes. Developers will hit these errors and have no reference. Create an error reference table:

  | Code | HTTP Status | Meaning | Fix |
  |:-----|:------------|:--------|:----|
  | `TEMPLATE_CYCLE_DETECTED` | 422 | Template DAG has a cycle | Remove circular dependency |
  | `PARAMETER_VALIDATION_FAILED` | 400 | Parameters don't match schema | Check parameter_schema |
  | `TEMPLATE_NOT_FOUND` | 404 | Slug doesn't match any active template | Check slug spelling |
  | `EXECUTION_ALREADY_CANCELLED` | 409 | Tried to cancel a finished workflow | No action needed |
  | `STEP_TIMEOUT` | -- (internal) | Agent exceeded timeout_seconds | Increase timeout or simplify task |
  | `PARTIAL_FAN_OUT_FAILURE` | -- (internal) | Some agents failed in fan-out | Check individual agent errors |

- **`_eval_condition` in TransformStep is a code injection risk.** The spec mentions "JSONPath expression" for filter conditions but the Python code uses a generic `_eval_condition` method. If this calls `eval()`, it is a security vulnerability. Document exactly what expression language is supported and ensure it is sandboxed.

### 8. Versioning (3/5)

**What works well:**
- Immutable versions (PUT bumps version number) is the correct design. Template authors cannot accidentally break running workflows.
- `template_version` is snapshot'd in `workflow_executions`, so you always know which version ran.

**Friction points:**
- **No API to get a specific version of a template.** `GET /api/v1/workflows/templates/{slug}` returns the latest. Add `GET /api/v1/workflows/templates/{slug}/versions` and `GET /api/v1/workflows/templates/{slug}/versions/{version}` (matching the skills API pattern).
- **No diff between versions.** When a template is updated, developers need to see what changed. `GET /api/v1/workflows/templates/{slug}/versions/{v1}/diff/{v2}` would be ideal, but at minimum, require a `changelog` field on update (like the skills API does).
- **What happens to running workflows when a template is updated?** The spec says `template_version` is snapshot'd, which implies running workflows continue on the old version. State this explicitly in the spec -- developers will ask. Add a sentence: "Updating a template does not affect workflows already in progress. Running workflows continue using the template version that was active when they started."
- **No rollback endpoint for templates.** The skills API has `POST /api/v1/skills/{slug}/rollback`. The workflow API should have the same: `POST /api/v1/workflows/templates/{slug}/rollback`.

---

## Top 5 DX Improvements (Priority Order)

### 1. Add template validation endpoint
**Impact:** Saves every template author from the create-fail-fix-delete-recreate loop.
**Effort:** Low (compiler.validate_params + compiler._validate_dag already exist).
**Endpoint:** `POST /api/v1/workflows/templates/validate`

### 2. Add structured error codes and an error catalog
**Impact:** Every developer who hits an error can self-serve the fix instead of reading source code.
**Effort:** Low (wrap existing exceptions in structured responses).
**File:** `workflows/api.py` -- add error response models.

### 3. Add agent-level detail endpoint
**Impact:** Debugging fan-out failures is impossible without per-agent logs and output.
**Effort:** Low (data already exists in `workflow_step_agents` table).
**Endpoint:** `GET /api/v1/workflows/executions/{id}/steps/{step_id}/agents/{index}`

### 4. Rename `POST /execute` to `POST /executions`
**Impact:** API consistency with existing skills API and REST conventions.
**Effort:** Trivial (rename route).

### 5. Add curl examples to the spec
**Impact:** First developer reading this spec can test every endpoint immediately.
**Effort:** Low (write examples for each endpoint).

---

## Consistency Check: Workflow API vs Skills API

| Pattern | Skills API | Workflow API (Proposed) | Consistent? |
|:--------|:-----------|:------------------------|:------------|
| Base path | `/api/v1/skills` | `/api/v1/workflows/templates` | Yes |
| Identifier | slug | slug | Yes |
| Pagination | `page`, `per_page` params | Not defined | No -- add it |
| Versions list | `GET /{slug}/versions` | Not defined | No -- add it |
| Rollback | `POST /{slug}/rollback` | Not defined | No -- add it |
| Search | `POST /skills/search` | Not defined | Maybe not needed yet |
| Metrics | `GET /{slug}/metrics` | Not defined | Add later (Phase 3+) |
| Create response | Full object + 201 | Partial object + 201 | No -- return full object |
| Error format | `{"detail": "..."}` | Undefined | No -- define structured format |
| Soft delete | `DELETE` sets `is_active=false` | `DELETE` sets `is_active=false` | Yes |

---

## What the Spec Gets Right

These are genuine strengths that should be preserved:

1. **Two-state separation is the right architectural call.** Deterministic orchestration + scoped agent reasoning is exactly how you build debuggable, cost-predictable AI systems. This should be highlighted in developer-facing docs as a core design principle.

2. **The execution response is a great DX artifact.** Showing step status with per-agent breakdown in a single GET call is better than most workflow engines. Stripe-level API response quality.

3. **Backwards compatibility via `single-task` template** is thoughtful. Existing users see zero changes unless they opt in. This is how you ship breaking architecture changes without breaking developers.

4. **Feature flag rollout in 3 phases** gives operators control and rollback confidence. The rollback strategy ("set flag to false, done") is exactly what operators want.

5. **ADRs are included in the spec.** Developers who want to understand *why* can read ADR-002 and ADR-003 without asking the team. This builds trust.

---

## Missing from the Spec (DX-Critical)

| Item | Why It Matters |
|:-----|:---------------|
| Rate limiting on workflow execution | Without limits, a single API call can spawn 20+ agents at $5/each |
| Cost estimation before execution | Developers need to know "this will spawn 15 agents" before hitting go |
| Webhook/callback on completion | External systems need to know when a workflow finishes |
| Execution TTL / auto-cleanup | Old executions will accumulate; define retention policy |
| Template sharing / import-export | Teams need to share templates; define a portable format |
| `dry_run` parameter on execute | "Show me what would happen" without actually spawning agents |
| Health check for the workflow engine | Operators need `GET /api/v1/workflows/health` |
| Metrics endpoint | `GET /api/v1/workflows/metrics` -- execution count, success rate, avg duration |

---

## Summary

The architecture is sound -- the two-state model, DAG-only workflows, and output-type routing are all good engineering decisions. The DX surface area (the parts developers actually touch) needs significant work before this ships. The template authoring experience and error handling are the two areas where developers will struggle most. Adding a validation endpoint, structured error codes, and curl examples would move the overall DX score from 2.75 to 4.0 with minimal engineering effort.

The spec should not ship to implementation without at minimum:
1. A template validation endpoint
2. Structured error responses with codes
3. Pagination on execution listing
4. curl examples for every endpoint
5. Explicit documentation of what happens to running workflows during template updates
