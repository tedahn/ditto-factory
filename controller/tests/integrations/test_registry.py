from unittest.mock import AsyncMock
from fastapi import Request
from controller.integrations.registry import IntegrationRegistry
from controller.models import TaskRequest

class FakeIntegration:
    name = "test"
    parse_webhook = AsyncMock(return_value=None)
    fetch_context = AsyncMock(return_value="")
    report_result = AsyncMock()
    acknowledge = AsyncMock()

def test_register_and_get():
    reg = IntegrationRegistry()
    fake = FakeIntegration()
    reg.register(fake)
    assert reg.get("test") is fake
    assert reg.get("missing") is None

def test_all_integrations():
    reg = IntegrationRegistry()
    reg.register(FakeIntegration())
    assert len(reg.all()) == 1

def test_create_router():
    reg = IntegrationRegistry()
    reg.register(FakeIntegration())
    router = reg.create_router()
    routes = [r.path for r in router.routes]
    assert "/webhooks/test" in routes
