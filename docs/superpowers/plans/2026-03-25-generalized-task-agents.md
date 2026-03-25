# Generalized Task Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend ditto-factory from code-only (PR output) to a generalized task agent platform that supports multiple result types: analysis reports, database mutations, file artifacts, and API actions — per [ADR-001](../../adr/001-generalized-task-agents.md).

**Architecture:** Add `TaskType` and `ResultType` enums to the model layer. Thread both through `TaskRequest` → orchestrator → safety pipeline → integration reporting. Introduce a `ResultValidator` protocol and dispatch table in `SafetyPipeline` so each result type gets its own validation path. Add `task_artifacts` table for non-code outputs. All new fields default to the current behavior (`CODE_CHANGE` / `PULL_REQUEST`) for full backwards compatibility.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, aiosqlite/asyncpg, Redis, Kubernetes client

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `controller/src/controller/models.py` | Add `TaskType`, `ResultType`, `Artifact` dataclass; extend `TaskRequest`, `AgentResult` |
| Modify | `controller/src/controller/config.py` | Add `DF_ANALYSIS_ENABLED`, `DF_ARTIFACT_STORAGE_PATH` settings |
| Create | `controller/src/controller/jobs/validators.py` | `ResultValidator` protocol + `PRValidator`, `ReportValidator` implementations |
| Modify | `controller/src/controller/jobs/safety.py` | Dispatch to validators by result type instead of hardcoded PR logic |
| Modify | `controller/src/controller/orchestrator.py` | Pass `task_type` through to task payload and job metadata |
| Modify | `controller/src/controller/state/protocol.py` | Add `create_artifact`, `get_artifacts_for_task` methods |
| Modify | `controller/src/controller/state/sqlite.py` | Add `task_artifacts` table + implement new protocol methods |
| Modify | `controller/src/controller/state/postgres.py` | Add `task_artifacts` table + implement new protocol methods |
| — | `controller/src/controller/integrations/protocol.py` | No change needed — `report_result` already receives `AgentResult` which now carries `result_type` + `artifacts` |
| — | `controller/src/controller/integrations/slack.py` | Future: use `formatting.py` in `report_result` (not in scope for this plan) |
| Modify | `controller/src/controller/prompt/builder.py` | Add task-type-aware prompt sections |
| Create | `controller/tests/test_models_task_types.py` | Tests for new enums, backwards compat |
| Create | `controller/tests/test_validators.py` | Tests for `PRValidator`, `ReportValidator` |
| Create | `controller/tests/test_safety_dispatch.py` | Tests for safety pipeline dispatch logic |
| Create | `controller/tests/test_artifact_storage.py` | Tests for artifact CRUD in SQLite |

---

## Phase 1: Foundation — Models & Enums (Tasks 1–3)

### Task 1: Add TaskType and ResultType Enums + Artifact Dataclass

**Files:**
- Modify: `controller/src/controller/models.py:1-71`
- Test: `controller/tests/test_models_task_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# controller/tests/test_models_task_types.py
"""Tests for TaskType, ResultType enums and Artifact dataclass."""
from controller.models import (
    TaskType,
    ResultType,
    ReversibilityLevel,
    Artifact,
    TaskRequest,
    AgentResult,
)


class TestTaskTypeEnum:
    def test_code_change_is_default(self):
        assert TaskType.CODE_CHANGE == "code_change"

    def test_all_task_types_exist(self):
        assert TaskType.CODE_CHANGE == "code_change"
        assert TaskType.ANALYSIS == "analysis"
        assert TaskType.DB_MUTATION == "db_mutation"
        assert TaskType.FILE_OUTPUT == "file_output"
        assert TaskType.API_ACTION == "api_action"

    def test_task_type_is_str_enum(self):
        assert isinstance(TaskType.CODE_CHANGE, str)
        assert TaskType.CODE_CHANGE == "code_change"


class TestResultTypeEnum:
    def test_pull_request_exists(self):
        assert ResultType.PULL_REQUEST == "pull_request"

    def test_all_result_types_exist(self):
        assert ResultType.PULL_REQUEST == "pull_request"
        assert ResultType.REPORT == "report"
        assert ResultType.DB_ROWS == "db_rows"
        assert ResultType.FILE_ARTIFACT == "file_artifact"
        assert ResultType.API_RESPONSE == "api_response"


class TestReversibilityLevel:
    def test_all_levels_exist(self):
        assert ReversibilityLevel.TRIVIAL == "trivial"
        assert ReversibilityLevel.POSSIBLE == "possible"
        assert ReversibilityLevel.DIFFICULT == "difficult"
        assert ReversibilityLevel.IMPOSSIBLE == "impossible"


class TestArtifact:
    def test_artifact_creation(self):
        a = Artifact(
            result_type=ResultType.REPORT,
            location="s3://bucket/report.json",
            metadata={"rows": 100},
        )
        assert a.result_type == ResultType.REPORT
        assert a.location == "s3://bucket/report.json"
        assert a.metadata == {"rows": 100}

    def test_artifact_defaults(self):
        a = Artifact(
            result_type=ResultType.FILE_ARTIFACT,
            location="/tmp/output.csv",
        )
        assert a.metadata == {}
        assert a.id is not None


class TestTaskRequestBackwardsCompat:
    def test_task_request_defaults_to_code_change(self):
        tr = TaskRequest(
            thread_id="t1",
            source="slack",
            source_ref={},
            repo_owner="org",
            repo_name="repo",
            task="fix the bug",
        )
        assert tr.task_type == TaskType.CODE_CHANGE

    def test_task_request_accepts_explicit_task_type(self):
        tr = TaskRequest(
            thread_id="t1",
            source="slack",
            source_ref={},
            repo_owner="org",
            repo_name="repo",
            task="analyze logs",
            task_type=TaskType.ANALYSIS,
        )
        assert tr.task_type == TaskType.ANALYSIS


class TestAgentResultBackwardsCompat:
    def test_agent_result_defaults_to_pull_request(self):
        ar = AgentResult(branch="main", exit_code=0, commit_count=1)
        assert ar.result_type == ResultType.PULL_REQUEST
        assert ar.artifacts == []

    def test_agent_result_accepts_report_type(self):
        ar = AgentResult(
            branch="",
            exit_code=0,
            commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(
                    result_type=ResultType.REPORT,
                    location="inline",
                    metadata={"summary": "all good"},
                )
            ],
        )
        assert ar.result_type == ResultType.REPORT
        assert len(ar.artifacts) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_models_task_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'TaskType' from 'controller.models'`

- [ ] **Step 3: Implement the enums and extend models**

Add to `controller/src/controller/models.py` — insert after the existing `JobStatus` enum (line 18), before `TaskRequest`:

```python
class TaskType(str, Enum):
    CODE_CHANGE = "code_change"
    ANALYSIS = "analysis"
    DB_MUTATION = "db_mutation"
    FILE_OUTPUT = "file_output"
    API_ACTION = "api_action"


class ResultType(str, Enum):
    PULL_REQUEST = "pull_request"
    REPORT = "report"
    DB_ROWS = "db_rows"
    FILE_ARTIFACT = "file_artifact"
    API_RESPONSE = "api_response"


class ReversibilityLevel(str, Enum):
    TRIVIAL = "trivial"
    POSSIBLE = "possible"
    DIFFICULT = "difficult"
    IMPOSSIBLE = "impossible"


@dataclass
class Artifact:
    result_type: ResultType
    location: str
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
```

Add `import uuid` at top of models.py.

Add new field to `TaskRequest` (after existing fields, before closing):
```python
    task_type: TaskType = TaskType.CODE_CHANGE
```

Add new fields to `AgentResult` (after existing fields):
```python
    result_type: ResultType = ResultType.PULL_REQUEST
    artifacts: list[Artifact] = field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_models_task_types.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All existing tests still PASS (defaults preserve existing behavior)

- [ ] **Step 6: Commit**

```bash
git add controller/src/controller/models.py controller/tests/test_models_task_types.py
git commit -m "feat: add TaskType, ResultType, ReversibilityLevel enums and Artifact dataclass

Extends TaskRequest with task_type (default: CODE_CHANGE) and AgentResult
with result_type (default: PULL_REQUEST) + artifacts list. Fully backwards
compatible — all existing code continues to work with defaults."
```

---

### Task 2: Add Config Settings for Generalized Tasks

**Files:**
- Modify: `controller/src/controller/config.py:71-84`
- Test: `controller/tests/test_models_task_types.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `controller/tests/test_models_task_types.py`:

```python
from controller.config import Settings


class TestGeneralizedTaskConfig:
    def test_analysis_enabled_defaults_false(self):
        s = Settings(anthropic_api_key="test")
        assert s.analysis_enabled is False

    def test_artifact_storage_path_default(self):
        s = Settings(anthropic_api_key="test")
        assert s.artifact_storage_path == "/tmp/df-artifacts"

    def test_db_mutation_enabled_defaults_false(self):
        s = Settings(anthropic_api_key="test")
        assert s.db_mutation_enabled is False

    def test_require_approval_for_mutations_defaults_true(self):
        s = Settings(anthropic_api_key="test")
        assert s.require_approval_for_mutations is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_models_task_types.py::TestGeneralizedTaskConfig -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'analysis_enabled'`

- [ ] **Step 3: Add settings to config.py**

Add before the `model_config` line (line 84) in `controller/src/controller/config.py`:

```python
    # Generalized Task Types
    analysis_enabled: bool = False
    db_mutation_enabled: bool = False
    file_output_enabled: bool = False
    api_action_enabled: bool = False
    artifact_storage_path: str = "/tmp/df-artifacts"
    require_approval_for_mutations: bool = True
    max_artifact_size_mb: int = 100
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_models_task_types.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add controller/src/controller/config.py controller/tests/test_models_task_types.py
git commit -m "feat: add generalized task type config settings

Adds DF_ANALYSIS_ENABLED, DF_DB_MUTATION_ENABLED, DF_FILE_OUTPUT_ENABLED,
DF_API_ACTION_ENABLED, DF_ARTIFACT_STORAGE_PATH, and
DF_REQUIRE_APPROVAL_FOR_MUTATIONS settings. All disabled by default."
```

---

### Task 3: Add task_artifacts Table to State Backends

> **Design note:** The ADR names the column `task_id` referencing job IDs, but we use `thread.id` as the artifact `task_id` in the safety pipeline. This is intentional — threads are the user-facing unit of work, and artifacts should be queryable by thread. If we later need per-job artifacts, we can add a `job_id` column without breaking the thread-level query.

**Files:**
- Modify: `controller/src/controller/state/protocol.py:1-21`
- Modify: `controller/src/controller/state/sqlite.py:24-57` (schema) + new methods
- Modify: `controller/src/controller/state/postgres.py:20-45` (schema) + new methods
- Test: `controller/tests/test_artifact_storage.py`

- [ ] **Step 1: Write the failing tests**

```python
# controller/tests/test_artifact_storage.py
"""Tests for task_artifacts storage in SQLite backend."""
import pytest
from controller.models import ResultType, Artifact
from controller.state.sqlite import SQLiteBackend


@pytest.fixture
async def sqlite_backend(tmp_path):
    backend = await SQLiteBackend.create(f"sqlite:///{tmp_path}/test.db")
    return backend


class TestArtifactStorage:
    async def test_create_and_retrieve_artifact(self, sqlite_backend):
        artifact = Artifact(
            result_type=ResultType.REPORT,
            location="s3://bucket/report.json",
            metadata={"rows": 42},
        )
        await sqlite_backend.create_artifact(task_id="job-001", artifact=artifact)

        artifacts = await sqlite_backend.get_artifacts_for_task("job-001")
        assert len(artifacts) == 1
        assert artifacts[0].result_type == ResultType.REPORT
        assert artifacts[0].location == "s3://bucket/report.json"
        assert artifacts[0].metadata == {"rows": 42}

    async def test_multiple_artifacts_per_task(self, sqlite_backend):
        a1 = Artifact(result_type=ResultType.REPORT, location="report.json")
        a2 = Artifact(result_type=ResultType.FILE_ARTIFACT, location="output.csv")
        await sqlite_backend.create_artifact(task_id="job-002", artifact=a1)
        await sqlite_backend.create_artifact(task_id="job-002", artifact=a2)

        artifacts = await sqlite_backend.get_artifacts_for_task("job-002")
        assert len(artifacts) == 2

    async def test_no_artifacts_returns_empty(self, sqlite_backend):
        artifacts = await sqlite_backend.get_artifacts_for_task("nonexistent")
        assert artifacts == []

    async def test_artifact_id_persisted(self, sqlite_backend):
        artifact = Artifact(
            id="custom-id-123",
            result_type=ResultType.DB_ROWS,
            location="pg://table/rows",
            metadata={"count": 10},
        )
        await sqlite_backend.create_artifact(task_id="job-003", artifact=artifact)

        artifacts = await sqlite_backend.get_artifacts_for_task("job-003")
        assert artifacts[0].id == "custom-id-123"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_artifact_storage.py -v`
Expected: FAIL — `AttributeError: 'SQLiteBackend' object has no attribute 'create_artifact'`

- [ ] **Step 3: Add methods to StateBackend protocol**

Add to `controller/src/controller/state/protocol.py` after `release_lock` (line 20), and add import:

```python
from controller.models import Thread, Job, ThreadStatus, JobStatus, Artifact, ResultType
```

New protocol methods:
```python
    async def create_artifact(self, task_id: str, artifact: Artifact) -> None: ...
    async def get_artifacts_for_task(self, task_id: str) -> list[Artifact]: ...
```

- [ ] **Step 4: Add task_artifacts table + methods to SQLite backend**

In `controller/src/controller/state/sqlite.py`, add import at top:
```python
from controller.models import Thread, Job, ThreadStatus, JobStatus, Artifact, ResultType
```

Add table creation in `_init_schema` after the `locks` table (line 56):
```python
            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    result_type TEXT NOT NULL,
                    location TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id
                ON task_artifacts(task_id)
            """)
```

Add methods at end of `SQLiteBackend` class:
```python
    async def create_artifact(self, task_id: str, artifact: Artifact) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT INTO task_artifacts (id, task_id, result_type, location, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                artifact.id, task_id, artifact.result_type.value,
                artifact.location, json.dumps(artifact.metadata),
                self._now_str(),
            ))
            await db.commit()

    async def get_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ) as cur:
                rows = await cur.fetchall()
            return [
                Artifact(
                    id=row["id"],
                    result_type=ResultType(row["result_type"]),
                    location=row["location"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
                for row in rows
            ]
```

- [ ] **Step 5: Add task_artifacts table + methods to Postgres backend**

In `controller/src/controller/state/postgres.py`, add import:
```python
from controller.models import Thread, Job, ThreadStatus, JobStatus, Artifact, ResultType
```

Add table creation in `_init_schema` after the `jobs` table:
```python
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    result_type TEXT NOT NULL,
                    location TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id
                ON task_artifacts(task_id);
```

Add methods at end of `PostgresBackend` class:
```python
    async def create_artifact(self, task_id: str, artifact: Artifact) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO task_artifacts (id, task_id, result_type, location, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
            """, artifact.id, task_id, artifact.result_type.value,
                artifact.location, json.dumps(artifact.metadata))

    async def get_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM task_artifacts WHERE task_id = $1 ORDER BY created_at",
                task_id)
            return [
                Artifact(
                    id=row["id"],
                    result_type=ResultType(row["result_type"]),
                    location=row["location"],
                    # asyncpg auto-deserializes JSONB to dict, no json.loads needed
                    metadata=row["metadata"] if row["metadata"] else {},
                )
                for row in rows
            ]
```

- [ ] **Step 6: Run tests to verify pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_artifact_storage.py -v`
Expected: All 4 tests PASS

- [ ] **Step 7: Run all tests for regression**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add controller/src/controller/state/protocol.py controller/src/controller/state/sqlite.py controller/src/controller/state/postgres.py controller/tests/test_artifact_storage.py
git commit -m "feat: add task_artifacts table to state backends

Adds create_artifact() and get_artifacts_for_task() to StateBackend
protocol. Implements in both SQLite and Postgres backends with indexed
task_id lookups."
```

---

## Phase 2: Result Validators & Safety Pipeline Dispatch (Tasks 4–5)

### Task 4: Create ResultValidator Protocol and Implementations

**Files:**
- Create: `controller/src/controller/jobs/validators.py`
- Test: `controller/tests/test_validators.py`

- [ ] **Step 1: Write the failing tests**

```python
# controller/tests/test_validators.py
"""Tests for result type validators."""
import pytest
from unittest.mock import AsyncMock
from controller.models import (
    AgentResult, ResultType, Thread, ThreadStatus,
    Artifact, ReversibilityLevel,
)
from controller.jobs.validators import (
    ValidationOutcome,
    PRValidator,
    ReportValidator,
    get_validator,
    REVERSIBILITY,
)


class TestValidationOutcome:
    def test_approved_outcome(self):
        outcome = ValidationOutcome(approved=True)
        assert outcome.approved is True
        assert outcome.reason is None

    def test_rejected_outcome(self):
        outcome = ValidationOutcome(approved=False, reason="no commits")
        assert outcome.approved is False
        assert outcome.reason == "no commits"


class TestPRValidator:
    async def test_approves_result_with_commits(self):
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    async def test_approves_failed_result(self):
        """Failed results are still 'approved' — they get reported, not retried here."""
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=1, commit_count=0)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    async def test_flags_empty_result_for_retry(self):
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is False
        assert "no changes" in outcome.reason.lower()


class TestReportValidator:
    async def test_approves_report_with_artifacts(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "analysis complete"})
            ],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    async def test_rejects_report_with_no_artifacts(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is False
        assert "no artifacts" in outcome.reason.lower()

    async def test_approves_report_even_on_nonzero_exit(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=1, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"error": "partial results"})
            ],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True


class TestGetValidator:
    def test_returns_pr_validator_for_pull_request(self):
        v = get_validator(ResultType.PULL_REQUEST)
        assert isinstance(v, PRValidator)

    def test_returns_report_validator_for_report(self):
        v = get_validator(ResultType.REPORT)
        assert isinstance(v, ReportValidator)

    def test_returns_pr_validator_as_fallback(self):
        """Unknown/future types fall back to PR validator."""
        v = get_validator(ResultType.DB_ROWS)
        assert isinstance(v, PRValidator)


class TestReversibilityMapping:
    def test_pull_request_is_trivial(self):
        assert REVERSIBILITY[ResultType.PULL_REQUEST] == ReversibilityLevel.TRIVIAL

    def test_report_is_trivial(self):
        assert REVERSIBILITY[ResultType.REPORT] == ReversibilityLevel.TRIVIAL

    def test_db_rows_is_possible(self):
        assert REVERSIBILITY[ResultType.DB_ROWS] == ReversibilityLevel.POSSIBLE

    def test_api_response_is_difficult(self):
        assert REVERSIBILITY[ResultType.API_RESPONSE] == ReversibilityLevel.DIFFICULT
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_validators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.jobs.validators'`

- [ ] **Step 3: Implement validators.py**

```python
# controller/src/controller/jobs/validators.py
"""Result-type-specific validators for the safety pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from controller.models import (
    AgentResult,
    Thread,
    ResultType,
    ReversibilityLevel,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationOutcome:
    approved: bool
    reason: str | None = None


@runtime_checkable
class ResultValidator(Protocol):
    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome: ...


class PRValidator:
    """Validates CODE_CHANGE results (the original behavior)."""

    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome:
        if result.exit_code != 0:
            # Failed runs are reported as-is, not retried by the validator
            return ValidationOutcome(approved=True)
        if result.commit_count == 0:
            return ValidationOutcome(
                approved=False,
                reason="Agent produced no changes (0 commits)",
            )
        return ValidationOutcome(approved=True)


class ReportValidator:
    """Validates ANALYSIS results — must produce at least one artifact."""

    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome:
        if not result.artifacts:
            return ValidationOutcome(
                approved=False,
                reason="Report produced no artifacts — expected at least a summary",
            )
        return ValidationOutcome(approved=True)


# Dispatch table — extend as new result types are implemented
_VALIDATORS: dict[ResultType, ResultValidator] = {
    ResultType.PULL_REQUEST: PRValidator(),
    ResultType.REPORT: ReportValidator(),
}

# Fallback types that haven't been implemented yet use PRValidator
_FALLBACK_VALIDATOR = PRValidator()


def get_validator(result_type: ResultType) -> ResultValidator:
    """Get the validator for a given result type."""
    return _VALIDATORS.get(result_type, _FALLBACK_VALIDATOR)


# Reversibility mapping — used by approval gates (future phases)
REVERSIBILITY: dict[ResultType, ReversibilityLevel] = {
    ResultType.PULL_REQUEST: ReversibilityLevel.TRIVIAL,
    ResultType.REPORT: ReversibilityLevel.TRIVIAL,
    ResultType.FILE_ARTIFACT: ReversibilityLevel.TRIVIAL,
    ResultType.DB_ROWS: ReversibilityLevel.POSSIBLE,
    ResultType.API_RESPONSE: ReversibilityLevel.DIFFICULT,
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_validators.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add controller/src/controller/jobs/validators.py controller/tests/test_validators.py
git commit -m "feat: add ResultValidator protocol with PR and Report validators

Implements the dispatch table pattern from ADR-001. PRValidator preserves
existing empty-result detection. ReportValidator checks for artifacts.
Includes reversibility level mapping for future approval gates."
```

---

### Task 5: Refactor SafetyPipeline to Dispatch by Result Type

**Files:**
- Modify: `controller/src/controller/jobs/safety.py:1-51`
- Test: `controller/tests/test_safety_dispatch.py`

- [ ] **Step 1: Write the failing tests**

```python
# controller/tests/test_safety_dispatch.py
"""Tests for safety pipeline dispatch by result type."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from controller.models import (
    AgentResult, Thread, ThreadStatus, ResultType,
    Artifact,
)
from controller.jobs.safety import SafetyPipeline
from controller.config import Settings


def _make_thread(thread_id="t1"):
    return Thread(
        id=thread_id, source="slack", source_ref={},
        repo_owner="org", repo_name="repo",
        status=ThreadStatus.RUNNING,
    )


def _make_settings(**overrides):
    defaults = dict(
        anthropic_api_key="test",
        auto_open_pr=True,
        retry_on_empty_result=True,
        max_empty_retries=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestSafetyPipelinePRDispatch:
    """Existing PR behavior preserved through the new dispatch path."""

    async def test_pr_auto_create_on_commits_no_pr(self):
        github_client = AsyncMock()
        github_client.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/1")
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

        thread = _make_thread()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=2)
        await pipeline.process(thread, result)

        github_client.create_pr.assert_called_once()
        integration.report_result.assert_called_once()

    async def test_pr_retry_on_empty_result(self):
        spawner = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(retry_on_empty_result=True, max_empty_retries=1),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=spawner,
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=0)

        # Should have called spawner for retry with correct args, NOT reported result
        spawner.assert_called_once_with("t1", is_retry=True, retry_count=1)
        integration.report_result.assert_not_called()


class TestSafetyPipelineReportDispatch:
    """Report result type skips PR logic entirely."""

    async def test_report_skips_pr_creation(self):
        github_client = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

        thread = _make_thread()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "done"})
            ],
        )
        await pipeline.process(thread, result)

        # PR creation should NOT be called for reports
        github_client.create_pr.assert_not_called()
        # Result should still be reported
        integration.report_result.assert_called_once()

    async def test_report_no_retry_on_empty_commits(self):
        """Reports with 0 commits should not trigger anti-stall retry."""
        spawner = AsyncMock()
        state = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(retry_on_empty_result=True),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=spawner,
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "done"})
            ],
        )
        await pipeline.process(thread, result)

        spawner.assert_not_called()
        integration.report_result.assert_called_once()

    async def test_report_stores_artifacts(self):
        state = AsyncMock()
        state.create_artifact = AsyncMock()
        redis_state = AsyncMock()
        redis_state.drain_messages = AsyncMock(return_value=[])
        integration = AsyncMock()

        pipeline = SafetyPipeline(
            settings=_make_settings(),
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=AsyncMock(),
        )

        thread = _make_thread()
        artifact = Artifact(result_type=ResultType.REPORT, location="inline",
                            metadata={"summary": "done"})
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[artifact],
        )
        await pipeline.process(thread, result)

        state.create_artifact.assert_called_once_with(task_id=thread.id, artifact=artifact)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_safety_dispatch.py -v`
Expected: FAIL — tests that depend on new dispatch logic will fail

- [ ] **Step 3: Refactor safety.py to dispatch by result type**

> **Note (pre-existing bug):** The current `safety.py` calls `self._spawner(thread.id, is_retry=True, ...)` but the orchestrator's `_spawn_job` expects `(thread: Thread, task_request: TaskRequest, ...)`. This mismatch exists in the current codebase and is NOT introduced by this refactor. Fixing it is out of scope for this plan.

Replace `controller/src/controller/jobs/safety.py` entirely:

```python
from __future__ import annotations
import logging
from controller.config import Settings
from controller.models import AgentResult, Thread, ThreadStatus, ResultType
from controller.jobs.validators import get_validator

logger = logging.getLogger(__name__)


class SafetyPipeline:
    def __init__(self, settings, state_backend, redis_state, integration, spawner, github_client):
        self._settings = settings
        self._state = state_backend
        self._redis_state = redis_state
        self._integration = integration
        self._spawner = spawner
        self._github_client = github_client

    async def process(self, thread: Thread, result: AgentResult, retry_count: int = 0) -> None:
        result_type = result.result_type

        if result_type == ResultType.PULL_REQUEST:
            await self._process_pr(thread, result, retry_count)
        elif result_type == ResultType.REPORT:
            await self._process_report(thread, result)
        else:
            # Future types (DB_ROWS, FILE_ARTIFACT, API_RESPONSE) fall back to PR path
            logger.warning(
                "Unhandled result type %s for thread %s, falling back to PR path",
                result_type, thread.id,
            )
            await self._process_pr(thread, result, retry_count)

    async def _process_pr(self, thread: Thread, result: AgentResult, retry_count: int) -> None:
        """Original PR-based safety pipeline (preserved behavior)."""
        # Step 1: VALIDATE using ResultValidator
        validator = get_validator(ResultType.PULL_REQUEST)
        outcome = await validator.validate(result, thread)

        # Step 2: PR CHECK
        if result.commit_count > 0 and not result.pr_url and self._settings.auto_open_pr:
            try:
                pr_url = await self._github_client.create_pr(
                    owner=thread.repo_owner,
                    repo=thread.repo_name,
                    branch=result.branch,
                    title=f"[Ditto Factory] Changes for {thread.id[:8]}",
                    body=f"Automated PR created by Ditto Factory agent.\n\nThread: `{thread.id}`",
                )
                result.pr_url = pr_url
            except Exception:
                logger.exception("Failed to auto-create PR for thread %s", thread.id)

        # Step 3: Anti-stall retry (uses validator outcome)
        if not outcome.approved and result.exit_code == 0:
            if self._settings.retry_on_empty_result and retry_count < self._settings.max_empty_retries:
                logger.info("Empty result for thread %s, retrying (attempt %d)", thread.id, retry_count + 1)
                await self._spawner(thread.id, is_retry=True, retry_count=retry_count + 1)
                return
            else:
                result.stderr = result.stderr or "Agent produced no changes after retries."

        # Step 4: REPORT
        await self._integration.report_result(thread, result)

        # Step 5: CLEANUP
        await self._state.update_thread_status(thread.id, ThreadStatus.IDLE)
        queued = await self._redis_state.drain_messages(thread.id)
        if queued:
            logger.info("Found %d queued messages for thread %s, spawning follow-up", len(queued), thread.id)

    async def _process_report(self, thread: Thread, result: AgentResult) -> None:
        """Report/analysis result pipeline — no PR, no anti-stall retry."""
        # Step 1: VALIDATE using ResultValidator
        validator = get_validator(ResultType.REPORT)
        outcome = await validator.validate(result, thread)

        if not outcome.approved:
            logger.warning("Report validation failed for thread %s: %s", thread.id, outcome.reason)
            result.stderr = result.stderr or outcome.reason or "Report validation failed."

        # Step 2: STORE ARTIFACTS
        for artifact in result.artifacts:
            try:
                await self._state.create_artifact(task_id=thread.id, artifact=artifact)
            except Exception:
                logger.exception("Failed to store artifact %s for thread %s", artifact.id, thread.id)

        # Step 3: REPORT
        await self._integration.report_result(thread, result)

        # Step 4: CLEANUP
        await self._state.update_thread_status(thread.id, ThreadStatus.IDLE)
        queued = await self._redis_state.drain_messages(thread.id)
        if queued:
            logger.info("Found %d queued messages for thread %s, spawning follow-up", len(queued), thread.id)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_safety_dispatch.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run all tests for regression**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add controller/src/controller/jobs/safety.py controller/tests/test_safety_dispatch.py
git commit -m "refactor: dispatch safety pipeline by result type

SafetyPipeline now branches on result.result_type instead of assuming
all results are PRs. REPORT type skips PR creation and anti-stall retry,
stores artifacts to state backend instead. PR path preserves all existing
behavior exactly."
```

---

## Phase 3: Orchestrator & Prompt Wiring (Tasks 6–7)

### Task 6: Thread task_type Through Orchestrator

**Files:**
- Modify: `controller/src/controller/orchestrator.py:96-404`

- [ ] **Step 1: Write the failing test**

Create `controller/tests/test_orchestrator_task_type.py`:

```python
# controller/tests/test_orchestrator_task_type.py
"""Tests for task_type flowing through orchestrator to Redis and Job."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from controller.models import TaskRequest, TaskType, Thread, ThreadStatus, JobStatus
from controller.config import Settings
from controller.orchestrator import Orchestrator


def _make_settings(**overrides):
    defaults = dict(
        anthropic_api_key="test",
        redis_url="redis://localhost:6379",
        skill_registry_enabled=False,
        gateway_enabled=False,
        tracing_enabled=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestOrchestratorTaskTypePassthrough:
    async def test_task_type_included_in_redis_payload(self):
        """TaskRequest.task_type should appear in the Redis task payload."""
        state = AsyncMock()
        state.get_thread = AsyncMock(return_value=None)
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        state.try_acquire_lock = AsyncMock(return_value=True)
        state.release_lock = AsyncMock()
        state.upsert_thread = AsyncMock()
        state.append_conversation = AsyncMock()
        state.get_conversation = AsyncMock(return_value=[])
        state.create_job = AsyncMock()
        state.update_thread_status = AsyncMock()

        redis_state = AsyncMock()
        redis_state.push_task = AsyncMock()

        spawner = MagicMock()
        spawner.spawn = MagicMock(return_value="df-test-123")

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)

        orch = Orchestrator(
            settings=_make_settings(),
            state=state,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=AsyncMock(),
        )

        tr = TaskRequest(
            thread_id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
            task="analyze error rates",
            task_type=TaskType.ANALYSIS,
        )
        await orch.handle_task(tr)

        # Verify Redis payload includes task_type
        redis_state.push_task.assert_called_once()
        payload = redis_state.push_task.call_args[0][1]
        assert payload["task_type"] == "analysis"

    async def test_task_type_in_job_context(self):
        """Job.task_context should include task_type."""
        state = AsyncMock()
        state.get_thread = AsyncMock(return_value=None)
        state.get_active_job_for_thread = AsyncMock(return_value=None)
        state.try_acquire_lock = AsyncMock(return_value=True)
        state.release_lock = AsyncMock()
        state.upsert_thread = AsyncMock()
        state.append_conversation = AsyncMock()
        state.get_conversation = AsyncMock(return_value=[])
        state.create_job = AsyncMock()
        state.update_thread_status = AsyncMock()

        redis_state = AsyncMock()
        redis_state.push_task = AsyncMock()

        spawner = MagicMock()
        spawner.spawn = MagicMock(return_value="df-test-456")

        registry = MagicMock()
        registry.get = MagicMock(return_value=None)

        orch = Orchestrator(
            settings=_make_settings(),
            state=state,
            redis_state=redis_state,
            registry=registry,
            spawner=spawner,
            monitor=AsyncMock(),
        )

        tr = TaskRequest(
            thread_id="t2", source="github", source_ref={},
            repo_owner="org", repo_name="repo",
            task="backfill data",
            task_type=TaskType.DB_MUTATION,
        )
        await orch.handle_task(tr)

        # Verify Job.task_context includes task_type
        state.create_job.assert_called_once()
        job = state.create_job.call_args[0][0]
        assert job.task_context["task_type"] == "db_mutation"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_orchestrator_task_type.py -v`
Expected: FAIL — `KeyError: 'task_type'` (field not yet in payload)

- [ ] **Step 3: Add task_type to Redis payload in orchestrator**

In `controller/src/controller/orchestrator.py`, modify the `task_payload` dict construction (around line 332-344).

Add `"task_type": task_request.task_type.value` to the payload dict:

```python
        task_payload = {
            "task": task_request.task,
            "task_type": task_request.task_type.value,
            "system_prompt": system_prompt,
            "repo_url": f"https://github.com/{thread.repo_owner}/{thread.repo_name}.git",
            "branch": branch,
            "skills": skills_payload,
            "gateway_mcp": gateway_mcp,
        }
```

And in the `Job` creation (around line 379-388), add `task_type` to `task_context`:

```python
        job = Job(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            k8s_job_name=job_name,
            status=JobStatus.RUNNING,
            task_context={
                "task": task_request.task,
                "branch": branch,
                "task_type": task_request.task_type.value,
            },
            agent_type=getattr(classification, 'agent_type', 'general') if classification else 'general',
            skills_injected=skill_names,
            started_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 3: Run all tests for regression**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add controller/src/controller/orchestrator.py controller/tests/test_orchestrator_task_type.py
git commit -m "feat: thread task_type through orchestrator to Redis payload and job context

TaskRequest.task_type now flows into the Redis task payload and the Job's
task_context dict so agents and monitors can act on it."
```

---

### Task 7: Add Task-Type-Aware Prompt Sections

**Files:**
- Modify: `controller/src/controller/prompt/builder.py`
- Test: inline in existing test run

- [ ] **Step 1: Write the failing test**

Create `controller/tests/test_prompt_task_type.py`:

```python
# controller/tests/test_prompt_task_type.py
"""Tests for task-type-aware prompt building."""
from controller.prompt.builder import build_system_prompt
from controller.models import TaskType


class TestPromptTaskType:
    def test_code_change_prompt_has_commit_instructions(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="fix the bug",
            task_type=TaskType.CODE_CHANGE,
        )
        assert "commit" in prompt.lower()
        assert "push your branch" in prompt.lower()

    def test_analysis_prompt_has_no_commit_instructions(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="analyze error rates",
            task_type=TaskType.ANALYSIS,
        )
        assert "push your branch" not in prompt.lower()
        assert "report" in prompt.lower() or "findings" in prompt.lower()

    def test_default_task_type_is_code_change(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="fix the bug",
        )
        assert "commit" in prompt.lower()

    def test_analysis_prompt_mentions_artifacts(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="audit the codebase",
            task_type=TaskType.ANALYSIS,
        )
        assert "artifact" in prompt.lower() or "result" in prompt.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_prompt_task_type.py -v`
Expected: FAIL — `build_system_prompt() got an unexpected keyword argument 'task_type'`

- [ ] **Step 3: Update prompt builder**

Replace `controller/src/controller/prompt/builder.py`:

```python
from controller.integrations.sanitize import sanitize_untrusted
from controller.models import TaskType


_CODE_CHANGE_RULES = (
    "# Task Execution Rules\n"
    "- You must make concrete changes to the codebase.\n"
    "- Do not exit without committing at least one change or explicitly explaining why no changes are needed.\n"
    "- Run any available linters, formatters, or tests before committing.\n"
    "- Create small, focused commits with clear messages.\n"
    "- Push your branch when done."
)

_ANALYSIS_RULES = (
    "# Task Execution Rules\n"
    "- You are performing an analysis task. Your result is a structured report, not code changes.\n"
    "- Investigate the codebase, data, or system as described in the task.\n"
    "- Produce your findings as a clear, structured result with actionable insights.\n"
    "- Include relevant data, metrics, or evidence to support your findings.\n"
    "- If you produce file artifacts, describe them clearly."
)

_TASK_TYPE_RULES = {
    TaskType.CODE_CHANGE: _CODE_CHANGE_RULES,
    TaskType.ANALYSIS: _ANALYSIS_RULES,
    # Future types will get their own rules; default to CODE_CHANGE for now
}


def build_system_prompt(
    repo_owner: str,
    repo_name: str,
    task: str,
    claude_md: str = "",
    conversation: list[str] | None = None,
    is_retry: bool = False,
    task_type: TaskType = TaskType.CODE_CHANGE,
) -> str:
    sections = []

    sections.append(f"# Working Environment\nYou are working in the repository {repo_owner}/{repo_name}.")
    sections.append("The repository has been cloned to /workspace. You are on a feature branch.")

    if claude_md:
        sections.append(f"# Repository Rules\n{claude_md}")

    rules = _TASK_TYPE_RULES.get(task_type, _CODE_CHANGE_RULES)
    sections.append(rules)

    if conversation:
        sections.append("# Conversation History\n" + "\n".join(conversation))

    task_content = sanitize_untrusted(task)
    if is_retry:
        sections.append(
            "# Task (RETRY)\n"
            "Your previous attempt produced no changes. Review the task again and make the required changes.\n\n"
            + task_content
        )
    else:
        sections.append(f"# Task\n{task_content}")

    return "\n\n".join(sections)
```

- [ ] **Step 4: Update orchestrator to pass task_type to build_system_prompt**

In `controller/src/controller/orchestrator.py`, update the `build_system_prompt` call (around line 124-131):

```python
        system_prompt = build_system_prompt(
            repo_owner=thread.repo_owner,
            repo_name=thread.repo_name,
            task=task_request.task,
            claude_md=claude_md,
            conversation=conversation_strs if conversation_strs else None,
            is_retry=is_retry,
            task_type=task_request.task_type,
        )
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_prompt_task_type.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Run all tests for regression**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add controller/src/controller/prompt/builder.py controller/src/controller/orchestrator.py controller/tests/test_prompt_task_type.py
git commit -m "feat: task-type-aware system prompts

Analysis tasks get report-focused instructions instead of commit/push
rules. Prompt builder now accepts task_type parameter (default:
CODE_CHANGE for backwards compat). Orchestrator passes task_type through."
```

---

## Phase 4: Integration Reporting (Task 8)

### Task 8: Update Integration Reporting for Non-PR Results

**Files:**
- Modify: `controller/src/controller/integrations/slack.py` (report_result method)
- Test: inline with existing integration tests

- [ ] **Step 1: Write the failing test**

Create `controller/tests/test_integration_report_types.py`:

```python
# controller/tests/test_integration_report_types.py
"""Tests for integration result reporting by result type."""
from controller.models import (
    AgentResult, ResultType, Thread, ThreadStatus, Artifact,
)


class TestResultMessageFormatting:
    """Verify result messages are formatted differently by result type."""

    def test_pr_result_message_mentions_pr(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=0, commit_count=2,
            pr_url="https://github.com/org/repo/pull/1",
        )
        msg = _format_result_message(result)
        assert "pull request" in msg.lower() or "pr" in msg.lower()

    def test_report_result_message_mentions_findings(self):
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "Found 3 issues"})
            ],
        )
        msg = _format_result_message(result)
        assert "report" in msg.lower() or "analysis" in msg.lower()
        assert "Found 3 issues" in msg

    def test_failed_result_message(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=1, commit_count=0,
            stderr="ImportError: no module named foo",
        )
        msg = _format_result_message(result)
        assert "failed" in msg.lower() or "error" in msg.lower()


def _format_result_message(result: AgentResult) -> str:
    """Shared formatting logic extracted for testing.
    This is the function we'll create in a shared module."""
    from controller.integrations.formatting import format_result_message
    return format_result_message(result)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_integration_report_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.integrations.formatting'`

- [ ] **Step 3: Create formatting module**

```python
# controller/src/controller/integrations/formatting.py
"""Shared result message formatting for integrations."""
from __future__ import annotations

from controller.models import AgentResult, ResultType


def format_result_message(result: AgentResult) -> str:
    """Format a human-readable result message based on result type."""
    if result.exit_code != 0:
        msg = f"Task failed (exit code {result.exit_code})."
        if result.stderr:
            # Truncate long stderr
            stderr_preview = result.stderr[:500]
            msg += f"\n```\n{stderr_preview}\n```"
        return msg

    if result.result_type == ResultType.REPORT:
        return _format_report(result)
    elif result.result_type == ResultType.PULL_REQUEST:
        return _format_pr(result)
    else:
        return _format_pr(result)  # fallback


def _format_pr(result: AgentResult) -> str:
    if result.pr_url:
        return f"Done — {result.commit_count} commit(s). Pull request: {result.pr_url}"
    elif result.commit_count > 0:
        return f"Done — {result.commit_count} commit(s) on `{result.branch}`."
    else:
        return "Agent produced no changes."


def _format_report(result: AgentResult) -> str:
    parts = ["Analysis report complete."]
    for artifact in result.artifacts:
        summary = artifact.metadata.get("summary", "")
        if summary:
            parts.append(summary)
        if artifact.location and artifact.location != "inline":
            parts.append(f"Artifact: `{artifact.location}`")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/test_integration_report_types.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run all tests for regression**

Run: `cd /Users/tedahn/Documents/codebase/ditto-factory/controller && python -m pytest tests/ -v --ignore=tests/e2e`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add controller/src/controller/integrations/formatting.py controller/tests/test_integration_report_types.py
git commit -m "feat: add result-type-aware message formatting

New formatting module produces different messages for PR results vs
analysis reports vs failures. Extracted from integration layer for
shared use across Slack/GitHub/Linear."
```

---

## Out of Scope — Future Phases (Documented for Context)

These are **not part of this implementation plan** but are recorded here so the work is scoped and the ideal end-state is visible.

### Future Phase A: DB_MUTATION Result Type
- Dry-run → approve → execute flow
- Approval gate in Slack (interactive messages) and GitHub (check runs)
- New `DBMutationValidator` in `validators.py`
- `ditto-factory-data-agent` Docker image with psql/DuckDB
- Idempotency key enforcement
- Row count sanity check post-execution

### Future Phase B: FILE_OUTPUT Result Type
- S3/GCS upload integration
- `FileArtifactValidator` — schema validation, size limits, checksum
- Artifact retention policies with TTLs on `task_artifacts`
- `ditto-factory-file-agent` Docker image

### Future Phase C: API_ACTION Result Type
- External API orchestration with rollback capture
- Dry-run where APIs support it
- `APIResponseValidator` — status validation, rollback instructions
- `ditto-factory-ops-agent` Docker image with cloud CLIs

### Future Phase D: Composable Tasks
- One task triggers another (e.g., analysis → code change)
- Task dependency graph
- Multi-step approval workflows

### Future Phase E: Approval Gateway
- Uses `ReversibilityLevel` mapping from `validators.py`
- `TRIVIAL` → auto-execute
- `POSSIBLE` → approve-or-auto with timeout
- `DIFFICULT`/`IMPOSSIBLE` → mandatory human approval with confirmation code
- Slack interactive messages for approve/reject
- GitHub check runs for approve/reject

### Future Phase F: REST API Extensions
- `GET /api/tasks/{id}/artifacts` — list artifacts for a task
- `GET /api/artifacts/{id}` — download/view artifact
- `POST /api/tasks` with `task_type` field
- Filter tasks by `task_type` in list endpoints

---

## Summary

| Phase | Tasks | Files Modified | Files Created | Tests Added |
|-------|-------|----------------|---------------|-------------|
| 1: Foundation | 1–3 | 5 (`models`, `config`, `protocol`, `sqlite`, `postgres`) | 2 (`test_models_task_types`, `test_artifact_storage`) | ~19 |
| 2: Validators | 4–5 | 1 (`safety`) | 3 (`validators`, `test_validators`, `test_safety_dispatch`) | ~17 |
| 3: Wiring | 6–7 | 2 (`orchestrator`, `prompt/builder`) | 2 (`test_orchestrator_task_type`, `test_prompt_task_type`) | ~8 |
| 4: Reporting | 8 | 0 | 2 (`formatting`, `test_integration_report_types`) | ~3 |
| **Total** | **8** | **8** | **9** | **~47** |

All changes are backwards compatible. Existing behavior is preserved through defaults (`CODE_CHANGE` / `PULL_REQUEST`). New task types are feature-flagged via `DF_ANALYSIS_ENABLED` etc.
