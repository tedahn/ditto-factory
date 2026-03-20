# src/controller/main.py
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from controller.config import Settings
from controller.state.redis_state import RedisState
from controller.integrations.registry import IntegrationRegistry
from controller.integrations.slack import SlackIntegration
from controller.integrations.linear import LinearIntegration
from controller.integrations.github import GitHubIntegration

logger = logging.getLogger(__name__)

settings = Settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Redis
    from redis.asyncio import Redis
    redis_client = Redis.from_url(settings.redis_url)
    app.state.redis_state = RedisState(redis_client)

    # Initialize state backend
    if settings.database_url.startswith("sqlite"):
        from controller.state.sqlite import SQLiteBackend
        app.state.db = await SQLiteBackend.create(settings.database_url)
    else:
        from controller.state.postgres import PostgresBackend
        app.state.db = await PostgresBackend.create(settings.database_url)

    # Initialize integration registry
    registry = IntegrationRegistry()

    if settings.slack_enabled:
        slack = SlackIntegration(
            signing_secret=settings.slack_signing_secret,
            bot_token=settings.slack_bot_token,
            bot_user_id=settings.slack_bot_user_id,
        )
        registry.register(slack)

    if settings.linear_enabled:
        linear = LinearIntegration(
            webhook_secret=settings.linear_webhook_secret,
            api_key=settings.linear_api_key,
        )
        registry.register(linear)

    if settings.github_enabled:
        github = GitHubIntegration(
            webhook_secret=settings.github_webhook_secret,
            app_id=settings.github_app_id,
            private_key=settings.github_private_key,
            allowed_orgs=settings.github_allowed_orgs,
        )
        registry.register(github)

    app.state.registry = registry

    # Mount webhook routes
    webhook_router = registry.create_router()
    app.include_router(webhook_router)

    logger.info("Ditto Factory started with %d integrations", len(registry.all()))

    yield

    # Cleanup
    await redis_client.aclose()
    logger.info("Ditto Factory shut down")

app = FastAPI(title="Ditto Factory", version="0.1.0", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    return {"status": "ready"}
