"""
Tests for the Workflow Engine REST API endpoints.

Uses mock TemplateCRUD and WorkflowEngine to avoid database dependencies.
Follows the same pattern as test_skill_api.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from controller.workflows.models import (
    ExecutionStatus,
    StepStatus,
    StepType,
    WorkflowExecution,
    WorkflowStep,
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)


# ---------------------------------------------------------------------------
# In-memory TemplateCRUD mock
# ---------------------------------------------------------------------------

class InMemoryTemplateCRUD:
    """Lightweight in-memory implementation for testing API endpoints."""

    def __init__(self):
        self._templates: dict[str, WorkflowTemplate] = {}
        self._versions: dict[str, list[dict]] = {}

    async def create(self, create_obj: WorkflowTemplateCreate) -> WorkflowTemplate:
        template_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        template = WorkflowTemplate(
            id=template_id,
            slug=create_obj.slug,
            name=create_obj.name,
            description=create_obj.description,
            version=1,
            definition=create_obj.definition,
            parameter_schema=create_obj.parameter_schema,
            is_active=True,
            created_by=create_obj.created_by,
            created_at=now,
            updated_at=now,
        )
        self._templates[create_obj.slug] = template
        self._versions[create_obj.slug] = [
            {
                "version": 1,
                "definition": create_obj.definition,
                "parameter_schema": create_obj.parameter_schema,
                "description": create_obj.description,
                "changelog": "Initial version",
                "created_by": create_obj.created_by,
                "created_at": now,
            }
        ]
        return template

    async def get(self, slug: str) -> WorkflowTemplate | None:
        return self._templates.get(slug)

    async def list_all(self) -> list[WorkflowTemplate]:
        return list(self._templates.values())

    async def update(
        self, slug: str, update: WorkflowTemplateUpdate
    ) -> WorkflowTemplate | None:
        template = self._templates.get(slug)
        if template is None:
            return None

        if update.definition is not None:
            template.definition = update.definition
        if update.parameter_schema is not None:
            template.parameter_schema = update.parameter_schema
        if update.description is not None:
            template.description = update.description

        template.version += 1
        template.updated_at = datetime.now(timezone.utc).isoformat()

        self._versions[slug].append(
            {
                "version": template.version,
                "definition": template.definition,
                "parameter_schema": template.parameter_schema,
                "description": template.description,
                "changelog": update.changelog,
                "created_by": update.updated_by,
                "created_at": template.updated_at,
            }
        )
        return template

    async def delete(self, slug: str) -> bool:
        if slug in self._templates:
            del self._templates[slug]
            return True
        return False

    async def get_versions(self, slug: str) -> list[dict]:
        return self._versions.get(slug, [])

    async def rollback(
        self, slug: str, target_version: int
    ) -> WorkflowTemplate | None:
        versions = self._versions.get(slug, [])
        target = None
        for v in versions:
            if v["version"] == target_version:
                target = v
                break
        if target is None:
            return None

        template = self._templates[slug]
        template.definition = target["definition"]
        template.parameter_schema = target["parameter_schema"]
        # Restore description if stored in version snapshot
        if "description" in target:
            template.description = target["description"]
        template.version += 1
        template.updated_at = datetime.now(timezone.utc).isoformat()

        self._versions[slug].append(
            {
                "version": template.version,
                "definition": template.definition,
                "parameter_schema": template.parameter_schema,
                "changelog": f"Rollback to version {target_version}",
                "created_by": "",
                "created_at": template.updated_at,
            }
        )
        return template


# ---------------------------------------------------------------------------
# In-memory WorkflowEngine mock
# ---------------------------------------------------------------------------

class InMemoryWorkflowEngine:
    """Lightweight in-memory workflow engine for testing API endpoints."""

    def __init__(self):
        self._executions: dict[str, WorkflowExecution] = {}
        self._steps: dict[str, list[WorkflowStep]] = {}

    async def start(
        self, template_slug: str, parameters: dict, thread_id: str
    ) -> str:
        if template_slug == "nonexistent":
            raise ValueError(f"Template not found: {template_slug}")

        execution_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        execution = WorkflowExecution(
            id=execution_id,
            template_id="tmpl-" + template_slug,
            template_version=1,
            thread_id=thread_id,
            parameters=parameters,
            status=ExecutionStatus.RUNNING,
            started_at=now,
        )
        self._executions[execution_id] = execution

        # Create a sample step
        step = WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id="step-1",
            step_type=StepType.SEQUENTIAL,
            status=StepStatus.PENDING,
        )
        self._steps[execution_id] = [step]
        return execution_id

    async def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        return self._executions.get(execution_id)

    async def get_steps(self, execution_id: str) -> list[WorkflowStep]:
        return self._steps.get(execution_id, [])

    async def list_executions(
        self, status: str | None = None
    ) -> list[WorkflowExecution]:
        executions = list(self._executions.values())
        if status:
            executions = [
                e for e in executions if e.status.value == status
            ]
        return executions

    async def cancel(self, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        if execution:
            execution.status = ExecutionStatus.CANCELLED
            execution.completed_at = datetime.now(timezone.utc).isoformat()
            # Mark pending steps as skipped
            for step in self._steps.get(execution_id, []):
                if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                    step.status = StepStatus.SKIPPED

    async def estimate(
        self, template_slug: str, parameters: dict
    ) -> dict:
        if template_slug == "nonexistent":
            raise ValueError(f"Template not found: {template_slug}")
        return {
            "estimated_agents": 3,
            "estimated_steps": 5,
            "estimated_cost_usd": 0.15,
            "estimated_duration_seconds": 360,
            "warnings": [],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def crud():
    return InMemoryTemplateCRUD()


@pytest.fixture
def engine():
    return InMemoryWorkflowEngine()


@pytest.fixture
def app(crud, engine):
    from controller.workflows.api import (
        router,
        get_template_crud,
        get_workflow_engine,
    )

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_template_crud] = lambda: crud
    test_app.dependency_overrides[get_workflow_engine] = lambda: engine
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TEMPLATE = {
    "slug": "threat-scan",
    "name": "Threat Scan",
    "description": "Multi-region threat scanning workflow",
    "definition": {
        "steps": [
            {"id": "search", "type": "fan_out", "depends_on": []},
            {"id": "merge", "type": "aggregate", "depends_on": ["search"]},
        ]
    },
    "parameter_schema": {
        "type": "object",
        "properties": {
            "regions": {"type": "array", "items": {"type": "string"}},
        },
    },
    "created_by": "test-user",
}


def _create_template(client, **overrides) -> dict:
    body = {**SAMPLE_TEMPLATE, **overrides}
    resp = client.post("/api/v1/workflows/templates", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Tests
# ===========================================================================


class TestCreateTemplate:
    """POST /api/v1/workflows/templates"""

    def test_create_template(self, client):
        resp = client.post("/api/v1/workflows/templates", json=SAMPLE_TEMPLATE)
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "threat-scan"
        assert data["name"] == "Threat Scan"
        assert data["version"] == 1
        assert data["is_active"] is True
        assert "id" in data

    def test_create_template_duplicate_slug(self, client):
        _create_template(client)
        resp = client.post("/api/v1/workflows/templates", json=SAMPLE_TEMPLATE)
        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["code"] == "TEMPLATE_SLUG_EXISTS"

    def test_create_template_missing_fields(self, client):
        resp = client.post(
            "/api/v1/workflows/templates",
            json={"name": "incomplete"},
        )
        assert resp.status_code == 422


class TestGetTemplate:
    """GET /api/v1/workflows/templates/{slug}"""

    def test_get_template(self, client):
        _create_template(client)
        resp = client.get("/api/v1/workflows/templates/threat-scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "threat-scan"
        assert data["definition"] == SAMPLE_TEMPLATE["definition"]

    def test_get_template_not_found(self, client):
        resp = client.get("/api/v1/workflows/templates/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["code"] == "TEMPLATE_NOT_FOUND"


class TestListTemplates:
    """GET /api/v1/workflows/templates"""

    def test_list_templates(self, client):
        _create_template(client, slug="tmpl-1", name="Template 1")
        _create_template(client, slug="tmpl-2", name="Template 2")
        _create_template(client, slug="tmpl-3", name="Template 3")

        resp = client.get("/api/v1/workflows/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["templates"]) == 3


class TestUpdateTemplate:
    """PUT /api/v1/workflows/templates/{slug}"""

    def test_update_template(self, client):
        _create_template(client)

        update = {
            "description": "Updated description",
            "changelog": "v2 update",
        }
        resp = client.put(
            "/api/v1/workflows/templates/threat-scan", json=update
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Updated description"
        assert data["version"] == 2

    def test_update_template_not_found(self, client):
        resp = client.put(
            "/api/v1/workflows/templates/nonexistent",
            json={"description": "new"},
        )
        assert resp.status_code == 404


class TestDeleteTemplate:
    """DELETE /api/v1/workflows/templates/{slug}"""

    def test_delete_template(self, client):
        _create_template(client)

        resp = client.delete("/api/v1/workflows/templates/threat-scan")
        assert resp.status_code == 204

        # Verify GET returns 404
        resp = client.get("/api/v1/workflows/templates/threat-scan")
        assert resp.status_code == 404

    def test_delete_template_not_found(self, client):
        resp = client.delete("/api/v1/workflows/templates/nonexistent")
        assert resp.status_code == 404


class TestListVersions:
    """GET /api/v1/workflows/templates/{slug}/versions"""

    def test_list_versions(self, client):
        _create_template(client)
        client.put(
            "/api/v1/workflows/templates/threat-scan",
            json={"description": "v2", "changelog": "Second version"},
        )
        client.put(
            "/api/v1/workflows/templates/threat-scan",
            json={"description": "v3", "changelog": "Third version"},
        )

        resp = client.get("/api/v1/workflows/templates/threat-scan/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["version"] == 1
        assert data[1]["version"] == 2
        assert data[2]["version"] == 3
        assert data[1]["changelog"] == "Second version"

    def test_list_versions_not_found(self, client):
        resp = client.get(
            "/api/v1/workflows/templates/nonexistent/versions"
        )
        assert resp.status_code == 404


class TestRollback:
    """POST /api/v1/workflows/templates/{slug}/rollback"""

    def test_rollback(self, client):
        _create_template(client)

        # Update to v2
        client.put(
            "/api/v1/workflows/templates/threat-scan",
            json={"description": "v2 desc"},
        )

        # Verify v2
        resp = client.get("/api/v1/workflows/templates/threat-scan")
        assert resp.json()["version"] == 2
        assert resp.json()["description"] == "v2 desc"

        # Rollback to v1
        resp = client.post(
            "/api/v1/workflows/templates/threat-scan/rollback",
            json={"target_version": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 3  # rollback creates a new version
        assert data["description"] == SAMPLE_TEMPLATE["description"]

    def test_rollback_not_found(self, client):
        resp = client.post(
            "/api/v1/workflows/templates/nonexistent/rollback",
            json={"target_version": 1},
        )
        assert resp.status_code == 404


class TestStartExecution:
    """POST /api/v1/workflows/executions"""

    def test_start_execution(self, client):
        resp = client.post(
            "/api/v1/workflows/executions",
            json={
                "template_slug": "threat-scan",
                "parameters": {"regions": ["us-east", "eu-west"]},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "running"
        assert data["template_id"] == "tmpl-threat-scan"
        assert "id" in data
        assert "thread_id" in data

    def test_start_execution_with_thread_id(self, client):
        resp = client.post(
            "/api/v1/workflows/executions",
            json={
                "template_slug": "threat-scan",
                "parameters": {},
                "thread_id": "custom-thread-123",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["thread_id"] == "custom-thread-123"

    def test_start_execution_template_not_found(self, client):
        resp = client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "nonexistent"},
        )
        assert resp.status_code == 404


class TestGetExecution:
    """GET /api/v1/workflows/executions/{id}"""

    def test_get_execution(self, client):
        # Start an execution first
        start_resp = client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "threat-scan", "parameters": {}},
        )
        execution_id = start_resp.json()["id"]

        resp = client.get(f"/api/v1/workflows/executions/{execution_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == execution_id
        assert data["steps"] is not None
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_id"] == "step-1"

    def test_get_execution_not_found(self, client):
        resp = client.get("/api/v1/workflows/executions/nonexistent-id")
        assert resp.status_code == 404


class TestListExecutions:
    """GET /api/v1/workflows/executions"""

    def test_list_executions(self, client):
        client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "scan-1", "parameters": {}},
        )
        client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "scan-2", "parameters": {}},
        )

        resp = client.get("/api/v1/workflows/executions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["executions"]) == 2

    def test_list_executions_with_status_filter(self, client, engine):
        # Start an execution and cancel it
        client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "scan-1", "parameters": {}},
        )
        start_resp = client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "scan-2", "parameters": {}},
        )
        eid = start_resp.json()["id"]
        client.post(f"/api/v1/workflows/executions/{eid}/cancel")

        resp = client.get("/api/v1/workflows/executions?status=cancelled")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["executions"][0]["status"] == "cancelled"


class TestCancelExecution:
    """POST /api/v1/workflows/executions/{id}/cancel"""

    def test_cancel_execution(self, client):
        start_resp = client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "threat-scan", "parameters": {}},
        )
        execution_id = start_resp.json()["id"]

        resp = client.post(
            f"/api/v1/workflows/executions/{execution_id}/cancel"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    def test_cancel_execution_not_found(self, client):
        resp = client.post(
            "/api/v1/workflows/executions/nonexistent-id/cancel"
        )
        assert resp.status_code == 404

    def test_cancel_already_cancelled(self, client):
        start_resp = client.post(
            "/api/v1/workflows/executions",
            json={"template_slug": "threat-scan", "parameters": {}},
        )
        execution_id = start_resp.json()["id"]

        # Cancel once
        client.post(f"/api/v1/workflows/executions/{execution_id}/cancel")

        # Cancel again - should fail
        resp = client.post(
            f"/api/v1/workflows/executions/{execution_id}/cancel"
        )
        assert resp.status_code == 409


class TestEstimate:
    """POST /api/v1/workflows/estimate"""

    def test_estimate(self, client):
        resp = client.post(
            "/api/v1/workflows/estimate",
            json={
                "template_slug": "threat-scan",
                "parameters": {"regions": ["us-east", "eu-west"]},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["estimated_agents"] == 3
        assert data["estimated_steps"] == 5
        assert data["estimated_cost_usd"] == 0.15
        assert data["estimated_duration_seconds"] == 360
        assert data["warnings"] == []

    def test_estimate_template_not_found(self, client):
        resp = client.post(
            "/api/v1/workflows/estimate",
            json={"template_slug": "nonexistent"},
        )
        assert resp.status_code == 404
