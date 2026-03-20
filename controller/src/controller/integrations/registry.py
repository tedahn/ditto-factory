from __future__ import annotations
from fastapi import APIRouter, Request, Response
from controller.integrations.protocol import Integration

class IntegrationRegistry:
    def __init__(self):
        self._integrations: dict[str, Integration] = {}

    def register(self, integration: Integration) -> None:
        self._integrations[integration.name] = integration

    def get(self, name: str) -> Integration | None:
        return self._integrations.get(name)

    def all(self) -> list[Integration]:
        return list(self._integrations.values())

    def create_router(self) -> APIRouter:
        router = APIRouter(prefix="/webhooks")
        for name, integration in self._integrations.items():
            async def webhook_handler(request: Request, _integ=integration):
                return await _integ.parse_webhook(request)
            router.add_api_route(f"/{name}", webhook_handler, methods=["POST"])
        return router
