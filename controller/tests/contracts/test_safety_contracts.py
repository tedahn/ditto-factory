"""Contracts 8, 9, 10: Safety Pipeline.

Contract 8: JobMonitor -> SafetyPipeline (AgentResult + Thread -> side effects).
Contract 9: SafetyPipeline -> GitHub Client (PR creation).
Contract 10: SafetyPipeline -> Integration (result reporting).
"""
import pytest
from unittest.mock import AsyncMock
from controller.config import Settings
from controller.models import Thread, AgentResult, ThreadStatus


class TestSafetyPipelineContract:

    @pytest.fixture
    def settings(self):
        return Settings(
            anthropic_api_key="test",
            auto_open_pr=True,
            retry_on_empty_result=True,
            max_empty_retries=2,
        )

    @pytest.fixture
    def thread(self):
        return Thread(
            id="t1", source="github",
            source_ref={"type": "issue_comment", "number": 42},
            repo_owner="org", repo_name="repo",
            status=ThreadStatus.RUNNING,
        )

    @pytest.fixture
    def state(self):
        mock = AsyncMock()
        mock.update_thread_status = AsyncMock()
        return mock

    @pytest.fixture
    def redis_state(self):
        mock = AsyncMock()
        mock.drain_messages = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def integration(self):
        mock = AsyncMock()
        mock.report_result = AsyncMock()
        return mock

    @pytest.fixture
    def github_client(self):
        mock = AsyncMock()
        mock.create_pr = AsyncMock(return_value="https://github.com/org/repo/pull/99")
        return mock

    @pytest.fixture
    def pipeline(self, settings, state, redis_state, integration, github_client):
        from controller.jobs.safety import SafetyPipeline

        return SafetyPipeline(
            settings=settings,
            state_backend=state,
            redis_state=redis_state,
            integration=integration,
            spawner=AsyncMock(),
            github_client=github_client,
        )

    # --- Contract 9: PR auto-creation ---

    async def test_creates_pr_when_commits_and_no_pr_url(self, pipeline, thread, github_client, integration):
        """Contract 9: commits > 0 AND no pr_url AND auto_open_pr -> create_pr called."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=3)
        await pipeline.process(thread, result)

        github_client.create_pr.assert_called_once_with(
            owner="org", repo="repo", branch="df/test/x",
        )
        # PR URL should be set on result before reporting
        reported_result = integration.report_result.call_args[0][1]
        assert reported_result.pr_url == "https://github.com/org/repo/pull/99"

    async def test_skips_pr_when_already_has_url(self, pipeline, thread, github_client):
        """Contract: if pr_url already set, don't create another."""
        result = AgentResult(
            branch="df/test/x", exit_code=0, commit_count=3,
            pr_url="https://github.com/org/repo/pull/1",
        )
        await pipeline.process(thread, result)
        github_client.create_pr.assert_not_called()

    async def test_skips_pr_when_no_commits(self, pipeline, thread, github_client):
        """Contract: zero commits means nothing to PR."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result)
        github_client.create_pr.assert_not_called()

    async def test_skips_pr_when_auto_open_disabled(self, thread, state, redis_state, integration):
        """Contract: auto_open_pr=False -> no PR creation."""
        from controller.jobs.safety import SafetyPipeline

        settings = Settings(anthropic_api_key="test", auto_open_pr=False)
        github_client = AsyncMock()
        pipeline = SafetyPipeline(
            settings=settings, state_backend=state, redis_state=redis_state,
            integration=integration, spawner=AsyncMock(), github_client=github_client,
        )
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=5)
        await pipeline.process(thread, result)
        github_client.create_pr.assert_not_called()

    # --- Contract 8: anti-stall retry ---

    async def test_retries_on_empty_result(self, pipeline, thread, integration):
        """Contract 8: commit_count=0, exit_code=0, retries left -> re-spawn."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=0)

        # Should NOT report (retrying instead)
        integration.report_result.assert_not_called()
        pipeline._spawner.assert_called_once()

    async def test_reports_after_max_retries(self, pipeline, thread, integration):
        """Contract: after max retries, report with error message."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=0)
        await pipeline.process(thread, result, retry_count=2)  # Already at max

        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert "no changes" in reported.stderr.lower() or len(reported.stderr) > 0

    async def test_no_retry_when_exit_code_nonzero(self, pipeline, thread, integration):
        """Contract: failed exit code -> no retry, always report."""
        result = AgentResult(branch="df/test/x", exit_code=1, commit_count=0, stderr="compile error")
        await pipeline.process(thread, result, retry_count=0)

        integration.report_result.assert_called_once()
        pipeline._spawner.assert_not_called()

    # --- Contract 10: always reports and cleans up ---

    async def test_reports_to_integration(self, pipeline, thread, integration):
        """Contract 10: result is always reported to the source integration."""
        result = AgentResult(
            branch="df/test/x", exit_code=0, commit_count=1,
            pr_url="https://github.com/org/repo/pull/1",
        )
        await pipeline.process(thread, result)
        integration.report_result.assert_called_once_with(thread, result)

    async def test_resets_thread_to_idle(self, pipeline, thread, state):
        """Contract: thread status -> IDLE after processing."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")
        await pipeline.process(thread, result)
        state.update_thread_status.assert_called_with(thread.id, ThreadStatus.IDLE)

    async def test_drains_queued_messages(self, pipeline, thread, redis_state):
        """Contract 11: queued messages are drained after completion."""
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=1, pr_url="url")
        await pipeline.process(thread, result)
        redis_state.drain_messages.assert_called_once_with(thread.id)

    async def test_pr_creation_failure_doesnt_block_reporting(
        self, pipeline, thread, github_client, integration
    ):
        """Contract: PR failure is caught; result still reported."""
        github_client.create_pr.side_effect = Exception("GitHub API error")
        result = AgentResult(branch="df/test/x", exit_code=0, commit_count=2)
        await pipeline.process(thread, result)

        # Should still report even though PR failed
        integration.report_result.assert_called_once()
        reported = integration.report_result.call_args[0][1]
        assert reported.pr_url is None
