# Implementation Plan: Wire MCP Gateway into Orchestrator

## Status
Proposed

## Context
`GatewayManager` exists in `controller/src/controller/gateway.py` with methods `scope_from_skills()`, `set_scope()`, `get_gateway_mcp_config()`, and `clear_scope()`. However, none of these are called from the orchestrator. The `_spawn_job` flow pushes a Redis task payload without `gateway_mcp`, so the agent entrypoint never receives gateway configuration. Gateway scopes are never set in Redis, meaning the gateway has no per-session tool filtering.

## Decision
Wire `GatewayManager` into the orchestrator following the same optional-dependency pattern used for skill services (classifier, injector, resolver, tracker).

---

## 1. Changes to `orchestrator.py` -- Constructor

**File:** `controller/src/controller/orchestrator.py`

Add `gateway_manager` as an optional parameter to `__init__`, matching the pattern of other optional services:

```python
from controller.gateway import GatewayManager  # under TYPE_CHECKING block

class Orchestrator:
    def __init__(
        self,
        ...
        tracker: PerformanceTracker | None = None,
        gateway_manager: GatewayManager | None = None,  # NEW
    ):
        ...
        self._gateway = gateway_manager  # NEW
```

**Rationale:** Optional with `None` default preserves backward compatibility for tests and environments without the gateway.

---

## 2. Changes to `orchestrator.py` -- `_spawn_job` Method

Insert gateway wiring **after** skill classification/injection and **before** `push_task`. The gateway depends on `matched_skills` being resolved, so it must come after the skill block.

### Insert point: After `skills_payload` is built, before `push_task`

```python
        # === Gateway scope (after skills are resolved) ===
        gateway_mcp = {}
        if (
            self._settings.gateway_enabled
            and self._gateway is not None
            and matched_skills
        ):
            try:
                gw_tools = await self._gateway.scope_from_skills(matched_skills)
                # Merge with default tools from settings
                all_tools = list(set(gw_tools + self._settings.gateway_default_tools))
                if all_tools:
                    await self._gateway.set_scope(thread_id, all_tools)
                    gateway_mcp = self._gateway.get_gateway_mcp_config(thread_id)
                    logger.info(
                        "Gateway scope set for %s: %d tools", thread_id, len(all_tools)
                    )
            except Exception:
                logger.exception("Failed to set gateway scope, continuing without gateway")
                gateway_mcp = {}

        # Also set default tools even when no skills matched
        elif (
            self._settings.gateway_enabled
            and self._gateway is not None
            and self._settings.gateway_default_tools
        ):
            try:
                await self._gateway.set_scope(thread_id, self._settings.gateway_default_tools)
                gateway_mcp = self._gateway.get_gateway_mcp_config(thread_id)
                logger.info(
                    "Gateway scope set (defaults only) for %s: %d tools",
                    thread_id, len(self._settings.gateway_default_tools),
                )
            except Exception:
                logger.exception("Failed to set default gateway scope")
                gateway_mcp = {}
```

### Modify the `push_task` call to include `gateway_mcp`:

```python
        await self._redis.push_task(thread_id, {
            "task": task_request.task,
            "system_prompt": system_prompt,
            "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
            "branch": branch,
            "skills": skills_payload,
            "gateway_mcp": gateway_mcp,  # NEW -- empty dict means no gateway
        })
```

**Key design decisions:**
- `gateway_mcp` is always present in the payload (empty dict when not needed) -- the entrypoint.sh already handles this by checking if the field is non-empty.
- `gateway_default_tools` are merged with skill-derived tools, deduplicated.
- Failures are caught and logged but never block job spawning -- the gateway is an enhancement, not a requirement.

---

## 3. Changes to `orchestrator.py` -- `handle_job_completion` Method

Add gateway scope cleanup after job result processing, before skill outcome recording:

```python
    async def handle_job_completion(self, thread_id: str) -> None:
        ...
        await pipeline.process(thread, result)

        # Clean up gateway scope  # NEW BLOCK
        if self._settings.gateway_enabled and self._gateway is not None:
            try:
                await self._gateway.clear_scope(thread_id)
            except Exception:
                logger.exception("Failed to clear gateway scope for %s", thread_id)

        # Record skill performance outcome (existing code)
        ...
```

**Rationale:** Scopes have a 2-hour TTL as a safety net, but explicit cleanup prevents stale scopes from accumulating when jobs complete normally.

---

## 4. Changes to `main.py` -- Create and Pass GatewayManager

**File:** `controller/src/controller/main.py`

### Add import:

```python
from controller.gateway import GatewayManager
```

### Create GatewayManager instance (after `monitor` is created, before Orchestrator):

```python
    # Initialize MCP Gateway (optional)
    gateway_manager = None
    if settings.gateway_enabled:
        gateway_manager = GatewayManager(
            redis_state=app.state.redis_state,
            settings=settings,
        )
        logger.info("GatewayManager initialized (url=%s)", settings.gateway_url)
```

### Pass to Orchestrator constructor:

```python
    app.state.orchestrator = Orchestrator(
        settings=settings,
        state=app.state.db,
        redis_state=app.state.redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        resolver=resolver,
        tracker=tracker,
        gateway_manager=gateway_manager,  # NEW
    )
```

---

## 5. Redis Task Payload Format

### Before (current):

```json
{
  "task": "...",
  "system_prompt": "...",
  "repo_url": "https://github.com/owner/repo.git",
  "branch": "df/abc123/def456",
  "skills": [...]
}
```

### After:

```json
{
  "task": "...",
  "system_prompt": "...",
  "repo_url": "https://github.com/owner/repo.git",
  "branch": "df/abc123/def456",
  "skills": [...],
  "gateway_mcp": {
    "gateway": {
      "url": "http://ditto-factory-gateway:3001/sse?thread_id=abc123",
      "transport": "sse"
    }
  }
}
```

When no gateway is needed: `"gateway_mcp": {}`

The agent `entrypoint.sh` already has logic to check for `gateway_mcp` in the payload and inject it into the agent's MCP configuration. No changes needed on the agent side.

---

## 6. Test Plan

### 6a. Unit Tests

**File:** `controller/tests/test_gateway_wiring.py`

| Test | Description |
|------|-------------|
| `test_spawn_job_sets_gateway_scope` | Mock `GatewayManager`, set `gateway_enabled=True`, provide skills with `gw:db-query` tag. Assert `set_scope` called with correct tools, `push_task` payload contains `gateway_mcp`. |
| `test_spawn_job_no_gateway_when_disabled` | Set `gateway_enabled=False`. Assert `GatewayManager` methods never called, payload has `gateway_mcp={}`. |
| `test_spawn_job_default_tools_no_skills` | No skills matched but `gateway_default_tools=["health"]`. Assert scope set with defaults only. |
| `test_spawn_job_merges_default_and_skill_tools` | Skills yield `["db-query"]`, defaults are `["health"]`. Assert scope contains both, deduplicated. |
| `test_spawn_job_gateway_error_non_fatal` | `set_scope` raises exception. Assert job still spawned, `gateway_mcp={}` in payload. |
| `test_completion_clears_scope` | Assert `clear_scope(thread_id)` called in `handle_job_completion`. |
| `test_completion_clear_scope_error_non_fatal` | `clear_scope` raises. Assert completion still proceeds normally. |
| `test_orchestrator_without_gateway_manager` | Pass `gateway_manager=None`. Assert no errors, `gateway_mcp={}`. |

### 6b. Integration Tests

| Test | Description |
|------|-------------|
| `test_gateway_scope_in_redis` | Use real Redis. Submit task with gateway-tagged skills. Verify `gateway_scope:{thread_id}` key exists with correct JSON. |
| `test_gateway_scope_cleared_after_completion` | Full lifecycle: submit -> complete -> verify scope key deleted. |
| `test_gateway_scope_ttl` | Verify the Redis key has TTL set (2 hours). |

### 6c. Contract Tests

| Test | Description |
|------|-------------|
| `test_task_payload_schema` | Validate `push_task` payload against a JSON schema that includes optional `gateway_mcp` field. |

---

## 7. Effort Estimate

| Task | Estimate |
|------|----------|
| Orchestrator constructor change | 15 min |
| `_spawn_job` gateway wiring | 30 min |
| `handle_job_completion` cleanup | 10 min |
| `main.py` GatewayManager creation | 10 min |
| Unit tests (8 tests) | 1.5 hr |
| Integration tests (3 tests) | 1 hr |
| Contract test | 30 min |
| Manual E2E verification | 30 min |
| **Total** | **~4.5 hours** |

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Gateway URL misconfigured | Agent gets empty MCP config | `get_gateway_mcp_config` returns `{}` when URL is empty; entrypoint skips injection |
| Redis connection failure during `set_scope` | Job blocked | All gateway calls are try/except wrapped; failures log and continue |
| Stale scopes from crashed jobs | Wrong tools exposed | 2-hour TTL on Redis keys provides automatic cleanup |
| `scope_from_skills` returns huge tool list | Security concern | Consider adding a `gateway_max_tools` setting in a follow-up |

---

## 9. Files Modified

| File | Type of Change |
|------|---------------|
| `controller/src/controller/orchestrator.py` | Add `gateway_manager` param, gateway wiring in `_spawn_job`, cleanup in `handle_job_completion` |
| `controller/src/controller/main.py` | Create `GatewayManager`, pass to `Orchestrator` |
| `controller/tests/test_gateway_wiring.py` | New file -- unit tests |
| `controller/tests/integration/test_gateway_redis.py` | New file -- integration tests |

No changes needed to `gateway.py` or `config.py` -- they already have everything required.
