"""Tests for MCP Gateway scope management."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from controller.gateway import GatewayManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    """Fake RedisState wrapping an AsyncMock inner client."""
    inner = AsyncMock()
    state = MagicMock()
    state._redis = inner
    return state


@pytest.fixture
def settings_with_gateway():
    return SimpleNamespace(gateway_url="http://ditto-factory-gateway:3001")


@pytest.fixture
def settings_no_gateway():
    return SimpleNamespace(gateway_url="")


@pytest.fixture
def manager(mock_redis, settings_with_gateway):
    return GatewayManager(mock_redis, settings_with_gateway)


@pytest.fixture
def manager_disabled(mock_redis, settings_no_gateway):
    return GatewayManager(mock_redis, settings_no_gateway)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_scope(manager, mock_redis):
    """set_scope writes JSON-encoded tool list to Redis with TTL."""
    await manager.set_scope("thread-abc", ["db-query", "web-search"])

    mock_redis._redis.set.assert_awaited_once_with(
        "gateway_scope:thread-abc",
        json.dumps(["db-query", "web-search"]),
        ex=7200,
    )


@pytest.mark.asyncio
async def test_clear_scope(manager, mock_redis):
    """clear_scope removes the Redis key."""
    await manager.clear_scope("thread-abc")

    mock_redis._redis.delete.assert_awaited_once_with(
        "gateway_scope:thread-abc",
    )


def test_get_gateway_mcp_config(manager):
    """Config contains the gateway SSE URL with thread_id."""
    config = manager.get_gateway_mcp_config("thread-xyz")
    assert config == {
        "gateway": {
            "url": "http://ditto-factory-gateway:3001/sse?thread_id=thread-xyz",
            "transport": "sse",
        }
    }


def test_gateway_disabled_returns_empty_config(manager_disabled):
    """When gateway_url is empty, config should be empty dict."""
    config = manager_disabled.get_gateway_mcp_config("thread-xyz")
    assert config == {}


@pytest.mark.asyncio
async def test_scope_from_skills_with_gateway_tags(manager):
    """Skills with 'gw:' prefixed tags produce correct tool list."""
    skills = [
        SimpleNamespace(tags=["python", "gw:db-query", "gw:web-search"]),
        SimpleNamespace(tags=["gw:db-query"]),  # duplicate
        SimpleNamespace(tags=["javascript"]),  # no gw tags
    ]
    result = await manager.scope_from_skills(skills)
    assert result == ["db-query", "web-search"]  # sorted, deduplicated


@pytest.mark.asyncio
async def test_scope_from_skills_no_gateway_tags(manager):
    """Skills without any 'gw:' tags return empty list."""
    skills = [
        SimpleNamespace(tags=["python", "javascript"]),
        SimpleNamespace(tags=None),
        SimpleNamespace(tags=[]),
    ]
    result = await manager.scope_from_skills(skills)
    assert result == []
