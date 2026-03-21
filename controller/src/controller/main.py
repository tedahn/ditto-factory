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
from controller.integrations.cli import CLIIntegration
from controller.orchestrator import Orchestrator
from controller.jobs.spawner import JobSpawner
from controller.jobs.monitor import JobMonitor
from controller.api import router as api_router, get_db, get_orchestrator, get_settings

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
    registry.register(CLIIntegration())

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

    # Initialize orchestrator
    try:
        from kubernetes import client as k8s, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            try:
                k8s_config.load_kube_config()
            except Exception:
                pass
        batch_api = k8s.BatchV1Api()
    except ImportError:
        batch_api = None

    spawner = JobSpawner(settings, batch_api)
    monitor = JobMonitor(app.state.redis_state, batch_api)

    # Initialize skill services (optional, gated by skill_registry_enabled)
    classifier = None
    injector = None
    resolver = None
    tracker = None
    skill_registry = None

    if settings.skill_registry_enabled:
        try:
            from controller.skills.registry import SkillRegistry
            from controller.skills.classifier import TaskClassifier
            from controller.skills.injector import SkillInjector
            from controller.skills.resolver import AgentTypeResolver
            from controller.skills.tracker import PerformanceTracker
            from controller.skills.embedding import create_embedding_provider

            # Derive db_path for skill services (SQLite only for now)
            skill_db_path = settings.database_url.replace("sqlite:///", "") if settings.database_url.startswith("sqlite") else settings.database_url

            # Create embedding provider (NoOp if no API key configured)
            embedding_provider = create_embedding_provider(settings)

            skill_registry = SkillRegistry(db_path=skill_db_path, embedding_provider=embedding_provider)
            classifier = TaskClassifier(registry=skill_registry, embedding_provider=embedding_provider, settings=settings)
            injector = SkillInjector()
            resolver = AgentTypeResolver(db_path=skill_db_path)
            tracker = PerformanceTracker(db_path=skill_db_path)
            logger.info("Skill registry initialized (embedding_provider=%s)", type(embedding_provider).__name__)
        except Exception:
            logger.exception("Failed to initialize skill registry, continuing without skills")

    app.state.orchestrator = Orchestrator(
        settings=settings,
        state=app.state.db,
        redis_state=app.state.redis_state,
        registry=registry,
        spawner=spawner,
        monitor=monitor,
        classifier=classifier,
        injector=injector,
        resolver=resolver,
        tracker=tracker,
    )

    # Wire up API dependency injection
    app.dependency_overrides[get_db] = lambda: app.state.db
    app.dependency_overrides[get_orchestrator] = lambda: app.state.orchestrator
    app.dependency_overrides[get_settings] = lambda: settings

    # Mount skills API if registry is available
    if skill_registry:
        try:
            from controller.skills.api import router as skills_router, get_skill_registry
            app.dependency_overrides[get_skill_registry] = lambda: skill_registry
            app.include_router(skills_router)
            logger.info("Skills API router mounted")
        except Exception:
            logger.exception("Failed to mount skills API router")

    # Mount webhook routes
    webhook_router = registry.create_router()
    app.include_router(webhook_router)

    logger.info("Ditto Factory started with %d integrations", len(registry.all()))

    yield

    # Cleanup
    await redis_client.aclose()
    logger.info("Ditto Factory shut down")

app = FastAPI(title="Ditto Factory", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    return {"status": "ready"}
