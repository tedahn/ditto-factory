# Onboard Endpoint Wiring Plan

## Answers to Review Questions

### 1. What does `workflow_engine.start()` need?
- `template_slug: str` — `"toolkit-onboarding"`
- `parameters: dict` — `{"github_url": ..., "branch": ...}`
- `thread_id: str` — must be generated (e.g. `uuid.uuid4().hex`)
- Returns: `execution_id: str`

### 2. How does the frontend poll for status?
- `GET /onboard/{execution_id}` must call `workflow_engine.get_execution(execution_id)`
- Returns `WorkflowExecution` with `.status`, `.result`, `.error`
- The workflow engine is already DI-wired in `main.py` via `get_workflow_engine` (from `workflows/api.py`)
- **But** the toolkit API does NOT import or use `get_workflow_engine`. A new DI stub is needed in `toolkits/api.py`.

### 3. Who calls `validate_and_import_manifest()`?
- The workflow engine's `advance()` marks execution complete with the last step's output as `result`.
- There is **no completion callback** mechanism in the engine.
- **Options**:
  - (A) Add a post-completion hook in the engine that calls `validate_and_import_manifest`
  - (B) Make `validate_and_import_manifest` a second workflow step (transform step)
  - (C) Have the polling endpoint (`GET /onboard/{execution_id}`) trigger import on first poll after completion
- **Recommended**: Option (B) — the `onboarding_template.py` already defines this as step 2 in the template description. The template needs a second step definition of type `transform` that calls `validate_and_import_manifest`.

### 4. What DI changes are needed?
- Add `get_workflow_engine` stub in `toolkits/api.py`
- Override it in `main.py` alongside existing toolkit DI overrides
- The workflow engine may be `None` if `workflow_enabled=False`, so the endpoint must handle fallback

### 5. Exact Code Changes

---

## File 1: `controller/src/controller/toolkits/api.py`

### Add DI stub (after existing stubs ~line 254):
```python
def get_workflow_engine():
    """Provide the workflow engine -- overridden via dependency_overrides. Returns None if workflows disabled."""
    return None
```

### Replace `start_onboarding` endpoint (lines 505-563):
```python
@router.post("/onboard", response_model=OnboardStatusResponse)
async def start_onboarding(
    body: OnboardRequest,
    registry=Depends(get_toolkit_registry),
    discovery=Depends(get_discovery_engine),
    workflow_engine=Depends(get_workflow_engine),
):
    """Start the toolkit onboarding workflow.

    If the workflow engine is available, delegates to the toolkit-onboarding
    workflow template. Otherwise falls back to direct discovery+import.
    """
    if workflow_engine is not None:
        try:
            import uuid
            thread_id = uuid.uuid4().hex
            execution_id = await workflow_engine.start(
                template_slug="toolkit-onboarding",
                parameters={
                    "github_url": body.github_url,
                    "branch": body.branch,
                },
                thread_id=thread_id,
            )
            return OnboardStatusResponse(
                execution_id=execution_id,
                status="running",
            )
        except ValueError as exc:
            logger.warning("Workflow template not found, falling back to direct import: %s", exc)
        except Exception as exc:
            logger.exception("Workflow engine start failed, falling back to direct import")

    # Fallback: direct discovery+import (existing logic)
    try:
        manifest = await discovery.discover(
            github_url=body.github_url,
            branch=body.branch,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
    except Exception as exc:
        logger.exception("Onboarding discovery failed for %s", body.github_url)
        raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")

    parsed = GitHubClient.parse_github_url(body.github_url)
    existing_sources = await registry.list_sources()
    source = next(
        (s for s in existing_sources if s.github_url == body.github_url),
        None,
    )

    if not source:
        source = await registry.create_source(
            github_url=body.github_url,
            owner=parsed["owner"],
            repo=parsed["repo"],
            branch=body.branch,
            commit_sha=manifest.commit_sha,
            metadata={"onboarded_via": "workflow"},
        )
    else:
        await registry.update_source_sync(source.id, manifest.commit_sha)

    toolkit = await registry.import_from_manifest(
        source_id=source.id,
        manifest=manifest,
    )

    return OnboardStatusResponse(
        execution_id="direct-import",
        status="completed",
        result={
            "toolkit_slug": toolkit.slug,
            "component_count": toolkit.component_count,
            "category": toolkit.category.value
            if isinstance(toolkit.category, ToolkitCategory)
            else str(toolkit.category),
        },
    )
```

### Replace `get_onboarding_status` endpoint (lines 566-580):
```python
@router.get("/onboard/{execution_id}", response_model=OnboardStatusResponse)
async def get_onboarding_status(
    execution_id: str,
    workflow_engine=Depends(get_workflow_engine),
):
    """Poll the status of an onboarding workflow execution."""
    if execution_id == "direct-import":
        return OnboardStatusResponse(
            execution_id=execution_id,
            status="completed",
            result={"message": "Import completed via direct path"},
        )

    if workflow_engine is None:
        raise HTTPException(
            status_code=404,
            detail="Workflow engine not available",
        )

    execution = await workflow_engine.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    return OnboardStatusResponse(
        execution_id=execution_id,
        status=execution.status.value,
        result=execution.result,
        error=execution.error,
    )
```

---

## File 2: `controller/src/controller/main.py`

### Add workflow engine DI override for toolkit API (after line 593):
```python
    # Wire workflow engine into toolkit API (may be None if workflows disabled)
    from controller.toolkits.api import get_workflow_engine as get_tk_workflow_engine
    app.dependency_overrides[get_tk_workflow_engine] = lambda: workflow_engine
```

---

## No New Dependencies Required
All imports already exist. The `uuid` import in the endpoint is already available at module level but the inline import is safe as a fallback.

## Open Item: `validate_and_import_manifest` Integration
The workflow engine's `advance()` stores the last step's output as the execution result. When the polling endpoint sees `status=completed`, the result contains the LLM-produced manifest JSON. Two approaches:

1. **Polling endpoint triggers import**: On first poll where `status=completed`, call `validate_and_import_manifest(execution.result, ...)`. Add a flag to prevent double-import.
2. **Add transform step to template**: The `ONBOARDING_TEMPLATE` already describes a 2-step flow but only defines 1 step. Add a second step that the engine executes as a transform (non-agent) step.

Option 2 is architecturally cleaner but requires implementing transform step support in the engine. Option 1 is a pragmatic interim solution.
