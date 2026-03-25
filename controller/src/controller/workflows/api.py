"""
REST API endpoints for the Workflow Engine.

  # Template CRUD
  POST   /api/v1/workflows/templates           - Create template
  GET    /api/v1/workflows/templates           - List templates
  GET    /api/v1/workflows/templates/{slug}    - Get template
  PUT    /api/v1/workflows/templates/{slug}    - Update template (creates version)
  DELETE /api/v1/workflows/templates/{slug}    - Soft delete
  GET    /api/v1/workflows/templates/{slug}/versions  - Version history
  POST   /api/v1/workflows/templates/{slug}/rollback  - Rollback to version

  # Execution
  POST   /api/v1/workflows/executions          - Start workflow
  GET    /api/v1/workflows/executions          - List executions
  GET    /api/v1/workflows/executions/{id}     - Get execution + steps
  POST   /api/v1/workflows/executions/{id}/cancel  - Cancel execution

  # Estimation
  POST   /api/v1/workflows/estimate            - Estimate cost
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from controller.workflows.models import (
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)


# ---------------------------------------------------------------------------
# Structured error codes (from DX review)
# ---------------------------------------------------------------------------

ERROR_CODES = {
    "TEMPLATE_NOT_FOUND": {"status": 404, "message": "Template not found"},
    "EXECUTION_NOT_FOUND": {"status": 404, "message": "Execution not found"},
    "TEMPLATE_SLUG_EXISTS": {"status": 409, "message": "Template with this slug already exists"},
    "INVALID_PARAMETERS": {"status": 422, "message": "Parameters do not match template schema"},
    "COMPILATION_FAILED": {"status": 422, "message": "Template compilation failed"},
    "EXECUTION_NOT_CANCELLABLE": {"status": 409, "message": "Execution is not in a cancellable state"},
}


def _raise_error(code: str, detail: str | None = None) -> None:
    """Raise an HTTPException using a structured error code."""
    err = ERROR_CODES[code]
    raise HTTPException(
        status_code=err["status"],
        detail={"code": code, "message": detail or err["message"]},
    )


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class TemplateCreateRequest(BaseModel):
    slug: str
    name: str
    description: str = ""
    definition: dict
    parameter_schema: dict | None = None
    created_by: str = ""


class TemplateUpdateRequest(BaseModel):
    definition: dict | None = None
    parameter_schema: dict | None = None
    description: str | None = None
    changelog: str | None = None
    updated_by: str = ""


class TemplateResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    version: int
    definition: dict
    parameter_schema: dict | None = None
    is_active: bool = True
    created_by: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class TemplateListResponse(BaseModel):
    templates: list[TemplateResponse]
    total: int


class TemplateVersionResponse(BaseModel):
    version: int
    changelog: str | None = None
    created_by: str = ""
    created_at: str | None = None


class RollbackRequest(BaseModel):
    target_version: int


class ExecutionStartRequest(BaseModel):
    template_slug: str
    parameters: dict = {}
    thread_id: str | None = None  # auto-generated if not provided


class ExecutionResponse(BaseModel):
    id: str
    template_id: str
    thread_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict | None = None
    error: str | None = None
    steps: list[dict] | None = None  # included in GET /{id}


class ExecutionListResponse(BaseModel):
    executions: list[ExecutionResponse]
    total: int


class EstimateRequest(BaseModel):
    template_slug: str
    parameters: dict = {}


class EstimateResponse(BaseModel):
    estimated_agents: int
    estimated_steps: int
    estimated_cost_usd: float
    estimated_duration_seconds: int
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Dependency injection helpers (overridden in main.py / tests)
# ---------------------------------------------------------------------------

def get_template_crud():
    """Provide the TemplateCRUD -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


def get_workflow_engine():
    """Provide the WorkflowEngine -- overridden via dependency_overrides."""
    raise NotImplementedError("Must be overridden via dependency_overrides")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/workflows")


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------

@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreateRequest,
    crud=Depends(get_template_crud),
):
    """Create a new workflow template."""
    # Check for duplicate slug
    existing = await crud.get(body.slug)
    if existing is not None:
        _raise_error("TEMPLATE_SLUG_EXISTS")

    create_obj = WorkflowTemplateCreate(
        slug=body.slug,
        name=body.name,
        description=body.description,
        definition=body.definition,
        parameter_schema=body.parameter_schema,
        created_by=body.created_by,
    )
    template = await crud.create(create_obj)
    return _template_to_response(template)


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    crud=Depends(get_template_crud),
):
    """List all active workflow templates."""
    templates = await crud.list_all()
    return TemplateListResponse(
        templates=[_template_to_response(t) for t in templates],
        total=len(templates),
    )


@router.get("/templates/{slug}", response_model=TemplateResponse)
async def get_template(
    slug: str,
    crud=Depends(get_template_crud),
):
    """Get a workflow template by slug."""
    template = await crud.get(slug)
    if template is None:
        _raise_error("TEMPLATE_NOT_FOUND")
    return _template_to_response(template)


@router.put("/templates/{slug}", response_model=TemplateResponse)
async def update_template(
    slug: str,
    body: TemplateUpdateRequest,
    crud=Depends(get_template_crud),
):
    """Update a workflow template, creating a new version."""
    existing = await crud.get(slug)
    if existing is None:
        _raise_error("TEMPLATE_NOT_FOUND")

    update_obj = WorkflowTemplateUpdate(
        definition=body.definition,
        parameter_schema=body.parameter_schema,
        description=body.description,
        changelog=body.changelog,
        updated_by=body.updated_by,
    )
    template = await crud.update(slug, update_obj)
    return _template_to_response(template)


@router.delete("/templates/{slug}", status_code=204)
async def delete_template(
    slug: str,
    crud=Depends(get_template_crud),
):
    """Soft-delete a workflow template."""
    existing = await crud.get(slug)
    if existing is None:
        _raise_error("TEMPLATE_NOT_FOUND")

    await crud.delete(slug)
    return None


# ---------------------------------------------------------------------------
# Versions & Rollback
# ---------------------------------------------------------------------------

@router.get(
    "/templates/{slug}/versions",
    response_model=list[TemplateVersionResponse],
)
async def list_versions(
    slug: str,
    crud=Depends(get_template_crud),
):
    """List version history for a template."""
    existing = await crud.get(slug)
    if existing is None:
        _raise_error("TEMPLATE_NOT_FOUND")

    versions = await crud.get_versions(slug)
    return [
        TemplateVersionResponse(
            version=v["version"],
            changelog=v.get("changelog"),
            created_by=v.get("created_by", ""),
            created_at=v.get("created_at"),
        )
        for v in versions
    ]


@router.post("/templates/{slug}/rollback", response_model=TemplateResponse)
async def rollback_template(
    slug: str,
    body: RollbackRequest,
    crud=Depends(get_template_crud),
):
    """Rollback a template to a specific version."""
    existing = await crud.get(slug)
    if existing is None:
        _raise_error("TEMPLATE_NOT_FOUND")

    template = await crud.rollback(slug, body.target_version)
    if template is None:
        raise HTTPException(status_code=404, detail="Target version not found")
    return _template_to_response(template)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@router.post("/executions", response_model=ExecutionResponse, status_code=201)
async def start_execution(
    body: ExecutionStartRequest,
    engine=Depends(get_workflow_engine),
):
    """Start a new workflow execution."""
    thread_id = body.thread_id or uuid.uuid4().hex

    try:
        execution_id = await engine.start(
            template_slug=body.template_slug,
            parameters=body.parameters,
            thread_id=thread_id,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            _raise_error("TEMPLATE_NOT_FOUND")
        _raise_error("COMPILATION_FAILED", str(exc))

    execution = await engine.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=500, detail="Execution failed to start")

    return _execution_to_response(execution)


@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    status: str | None = Query(default=None, description="Filter by status"),
    engine=Depends(get_workflow_engine),
):
    """List workflow executions with optional status filter."""
    executions = await engine.list_executions(status=status)
    return ExecutionListResponse(
        executions=[_execution_to_response(e) for e in executions],
        total=len(executions),
    )


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: str,
    engine=Depends(get_workflow_engine),
):
    """Get a workflow execution with step details."""
    execution = await engine.get_execution(execution_id)
    if execution is None:
        _raise_error("EXECUTION_NOT_FOUND")

    steps = await engine.get_steps(execution_id)
    return _execution_to_response(execution, steps=steps)


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=ExecutionResponse,
)
async def cancel_execution(
    execution_id: str,
    engine=Depends(get_workflow_engine),
):
    """Cancel a running workflow execution."""
    execution = await engine.get_execution(execution_id)
    if execution is None:
        _raise_error("EXECUTION_NOT_FOUND")

    if execution.status.value not in ("pending", "running"):
        _raise_error("EXECUTION_NOT_CANCELLABLE")

    await engine.cancel(execution_id)

    execution = await engine.get_execution(execution_id)
    return _execution_to_response(execution)


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------

@router.post("/estimate", response_model=EstimateResponse)
async def estimate_workflow(
    body: EstimateRequest,
    engine=Depends(get_workflow_engine),
):
    """Estimate cost and resources for a workflow without executing."""
    try:
        estimate = await engine.estimate(
            template_slug=body.template_slug,
            parameters=body.parameters,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            _raise_error("TEMPLATE_NOT_FOUND")
        _raise_error("COMPILATION_FAILED", str(exc))

    return EstimateResponse(**estimate)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_dt(dt: Any) -> str | None:
    """Format a datetime to ISO string, handling None."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _template_to_response(template: Any) -> TemplateResponse:
    """Convert an internal WorkflowTemplate to TemplateResponse."""
    return TemplateResponse(
        id=getattr(template, "id", ""),
        slug=template.slug,
        name=template.name,
        description=getattr(template, "description", ""),
        version=getattr(template, "version", 1),
        definition=getattr(template, "definition", {}),
        parameter_schema=getattr(template, "parameter_schema", None),
        is_active=getattr(template, "is_active", True),
        created_by=getattr(template, "created_by", ""),
        created_at=_format_dt(getattr(template, "created_at", None)),
        updated_at=_format_dt(getattr(template, "updated_at", None)),
    )


def _execution_to_response(
    execution: Any,
    steps: list | None = None,
) -> ExecutionResponse:
    """Convert an internal WorkflowExecution to ExecutionResponse."""
    steps_data = None
    if steps is not None:
        steps_data = [
            {
                "id": s.id,
                "step_id": s.step_id,
                "step_type": s.step_type.value if hasattr(s.step_type, "value") else s.step_type,
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "error": s.error,
            }
            for s in steps
        ]

    return ExecutionResponse(
        id=execution.id,
        template_id=execution.template_id,
        thread_id=execution.thread_id,
        status=execution.status.value if hasattr(execution.status, "value") else execution.status,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        result=execution.result,
        error=execution.error,
        steps=steps_data,
    )
