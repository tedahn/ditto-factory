"""Tests for Phase 2: Orchestrator-side tracing instrumentation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from controller.orchestrator import Orchestrator
from controller.config import Settings
from controller.models import TaskRequest, Thread, ThreadStatus
from controller.skills.models import (
    ClassificationDiagnostics,
    ClassificationResult,
    Skill,
)
from controller.tracing.models import TraceEventType


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="test",
        tracing_enabled=True,
        skill_registry_enabled=True,
    )


@pytest.fixture
def settings_tracing_off():
    return Settings(
        anthropic_api_key="test",
        tracing_enabled=False,
    )


@pytest.fixture
def state():
    mock = AsyncMock()
    mock.get_thread = AsyncMock(return_value=None)
    mock.get_active_job_for_thread = AsyncMock(return_value=None)
    mock.try_acquire_lock = AsyncMock(return_value=True)
    mock.release_lock = AsyncMock()
    mock.get_conversation = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def redis_state():
    return AsyncMock()


@pytest.fixture
def registry():
    mock = MagicMock()
    mock.get = MagicMock(return_value=AsyncMock())
    return mock


@pytest.fixture
def spawner():
    mock = MagicMock()
    mock.spawn = MagicMock(return_value="df-abc123-99999")
    return mock


@pytest.fixture
def monitor():
    return AsyncMock()


@pytest.fixture
def trace_store():
    mock = AsyncMock()
    mock.insert_span = AsyncMock()
    return mock


@pytest.fixture
def sample_skill():
    return Skill(
        id="s1",
        name="React Testing",
        slug="react-testing",
        description="React component testing patterns",
        content="test content",
        language=["javascript"],
        domain=["frontend"],
        tags=["react", "testing"],
    )


@pytest.fixture
def classifier(sample_skill):
    mock = AsyncMock()
    mock.classify = AsyncMock(return_value=ClassificationResult(
        skills=[sample_skill],
        agent_type="frontend",
        diagnostics=ClassificationDiagnostics(
            method="semantic",
            candidates_evaluated=5,
            scores=[
                {"skill_slug": "react-testing", "score": 0.87, "boosted_score": 0.92},
            ],
            threshold=0.5,
            embedding_cached=False,
        ),
    ))
    return mock


@pytest.fixture
def injector():
    mock = MagicMock()
    mock.format_for_redis = MagicMock(return_value=[{"name": "React Testing", "content": "test"}])
    return mock


@pytest.fixture
def task_request():
    return TaskRequest(
        thread_id="abc123",
        source="slack",
        source_ref={"channel": "C1", "thread_ts": "123.456"},
        repo_owner="org",
        repo_name="repo",
        task="fix the login bug",
    )


async def test_tracing_emits_task_received_span(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """TASK_RECEIVED span is emitted at start of _spawn_job."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    # trace_store.insert_span should be called at least once for TASK_RECEIVED
    assert trace_store.insert_span.call_count >= 1
    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    event_types = [s.operation_name for s in spans]
    assert TraceEventType.TASK_RECEIVED in event_types


async def test_tracing_emits_classification_span(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """TASK_CLASSIFIED span is emitted when classification runs."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    event_types = [s.operation_name for s in spans]
    assert TraceEventType.TASK_CLASSIFIED in event_types

    # Check classification span has diagnostics metadata
    cls_spans = [s for s in spans if s.operation_name == TraceEventType.TASK_CLASSIFIED]
    assert len(cls_spans) == 1
    cls_span = cls_spans[0]
    assert cls_span.metadata is not None
    assert cls_span.metadata["method"] == "semantic"
    assert cls_span.metadata["candidates_evaluated"] == 5
    assert "scores" in cls_span.metadata


async def test_tracing_emits_skills_injected_span(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """SKILLS_INJECTED span is emitted when skills are matched."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    event_types = [s.operation_name for s in spans]
    assert TraceEventType.SKILLS_INJECTED in event_types


async def test_tracing_emits_agent_spawned_span(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """AGENT_SPAWNED span is emitted after k8s job creation."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    event_types = [s.operation_name for s in spans]
    assert TraceEventType.AGENT_SPAWNED in event_types

    spawn_spans = [s for s in spans if s.operation_name == TraceEventType.AGENT_SPAWNED]
    assert spawn_spans[0].metadata["job_name"] == "df-abc123-99999"


async def test_tracing_propagates_trace_id_to_redis(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """trace_id and parent_span_id are included in Redis task payload."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    # Check Redis push_task was called with trace_id
    redis_state.push_task.assert_called_once()
    payload = redis_state.push_task.call_args[0][1]
    assert "trace_id" in payload
    assert len(payload["trace_id"]) == 32  # W3C trace ID format
    assert "parent_span_id" in payload
    assert len(payload["parent_span_id"]) == 16  # W3C span ID format


async def test_tracing_all_spans_share_trace_id(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """All emitted spans share the same trace_id."""
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    trace_ids = {s.trace_id for s in spans}
    assert len(trace_ids) == 1, f"Expected 1 trace_id, got {trace_ids}"


async def test_no_tracing_when_disabled(
    settings_tracing_off, state, redis_state, registry, spawner, monitor,
    trace_store, task_request,
):
    """No spans emitted when tracing_enabled is False."""
    orch = Orchestrator(
        settings=settings_tracing_off,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    trace_store.insert_span.assert_not_called()


async def test_no_tracing_when_store_is_none(
    settings, state, redis_state, registry, spawner, monitor,
    task_request,
):
    """No error when trace_store is None even if tracing_enabled."""
    # tracing_enabled=True but trace_store=None (initialization failure case)
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        trace_store=None,
    )
    # Should not raise
    await orch.handle_task(task_request)


async def test_tracing_does_not_block_on_store_error(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, classifier, injector, task_request,
):
    """Orchestrator continues even if trace store raises."""
    trace_store.insert_span = AsyncMock(side_effect=Exception("db write failed"))

    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        trace_store=trace_store,
    )
    # Should complete without raising
    await orch.handle_task(task_request)

    # Job should still be created
    state.create_job.assert_called_once()


async def test_redis_payload_has_no_trace_id_when_tracing_off(
    settings_tracing_off, state, redis_state, registry, spawner, monitor,
    task_request,
):
    """Redis payload should not contain trace_id when tracing is disabled."""
    orch = Orchestrator(
        settings=settings_tracing_off,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
    )
    await orch.handle_task(task_request)

    payload = redis_state.push_task.call_args[0][1]
    assert "trace_id" not in payload
    assert "parent_span_id" not in payload


async def test_no_skills_injected_span_when_no_skills(
    settings, state, redis_state, registry, spawner, monitor,
    trace_store, task_request,
):
    """SKILLS_INJECTED span is NOT emitted when no skills matched."""
    # No classifier = no skills
    orch = Orchestrator(
        settings=settings,
        state=state,
        redis_state=redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        trace_store=trace_store,
    )
    await orch.handle_task(task_request)

    spans = [call.args[0] for call in trace_store.insert_span.call_args_list]
    event_types = [s.operation_name for s in spans]
    assert TraceEventType.SKILLS_INJECTED not in event_types
