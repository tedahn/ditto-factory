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
