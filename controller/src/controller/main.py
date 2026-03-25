# src/controller/main.py
from __future__ import annotations
import asyncio
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
            tracker = PerformanceTracker(db_path=skill_db_path)
            classifier = TaskClassifier(registry=skill_registry, embedding_provider=embedding_provider, settings=settings, tracker=tracker)
            injector = SkillInjector()
            resolver = AgentTypeResolver(db_path=skill_db_path)
            logger.info("Skill registry initialized (embedding_provider=%s)", type(embedding_provider).__name__)
        except Exception:
            logger.exception("Failed to initialize skill registry, continuing without skills")

    # Initialize tracing (optional)
    trace_store = None
    if settings.tracing_enabled:
        try:
            from controller.tracing import TraceStore
            trace_store = TraceStore(
                db_path=settings.trace_db_path,
                batch_size=settings.trace_batch_size,
                flush_interval=settings.trace_flush_interval,
            )
            await trace_store.initialize()
            logger.info("Trace store initialized (db=%s)", settings.trace_db_path)
        except Exception:
            logger.exception("Failed to initialize trace store, continuing without tracing")
            trace_store = None

    # Initialize MCP Gateway (optional)
    gateway_manager = None
    if settings.gateway_enabled:
        from controller.gateway import GatewayManager
        gateway_manager = GatewayManager(
            redis_state=app.state.redis_state,
            settings=settings,
        )
        logger.info("GatewayManager initialized (url=%s)", settings.gateway_url)

    # Initialize workflow engine (optional)
    workflow_engine = None
    template_crud = None
    if settings.workflow_enabled:
        try:
            from controller.workflows.engine import WorkflowEngine
            from controller.workflows.compiler import WorkflowCompiler
            from controller.workflows.templates import TemplateCRUD

            wf_db_path = settings.database_url.replace("sqlite:///", "") if settings.database_url.startswith("sqlite") else settings.database_url
            template_crud = TemplateCRUD(db_path=wf_db_path)
            wf_compiler = WorkflowCompiler(max_agents_per_execution=settings.max_agents_per_execution)
            workflow_engine = WorkflowEngine(
                db_path=wf_db_path,
                settings=settings,
                compiler=wf_compiler,
                spawner=spawner,
                redis_state=app.state.redis_state,
            )
            logger.info("Workflow engine initialized")
        except Exception:
            logger.exception("Failed to initialize workflow engine")

    # Initialize swarm communication (optional)
    swarm_manager = None
    swarm_task = None
    if settings.swarm_enabled:
        try:
            from controller.swarm.redis_streams import SwarmRedisStreams
            from controller.swarm.async_spawner import AsyncJobSpawner
            from controller.swarm.manager import SwarmManager
            from controller.swarm.watchdog import SchedulingWatchdog
            from controller.swarm.monitor import SwarmMonitor
            from controller.models import SwarmStatus

            swarm_streams = SwarmRedisStreams(redis_client, maxlen=settings.swarm_stream_maxlen)
            async_spawner = AsyncJobSpawner(spawner, max_concurrent=20)
            swarm_manager = SwarmManager(
                settings=settings,
                state=app.state.db,
                redis_streams=swarm_streams,
                async_spawner=async_spawner,
                spawner=spawner,
            )

            # Recover Redis state for active swarms
            await swarm_manager.recover_redis_state()

            # Start scheduling watchdog
            from kubernetes import client as k8s_client
            try:
                core_api = k8s_client.CoreV1Api()
                watchdog = SchedulingWatchdog(
                    core_api=core_api,
                    state=app.state.db,
                    redis_streams=swarm_streams,
                    namespace=getattr(settings, 'k8s_namespace', 'default'),
                    grace_seconds=settings.scheduling_unschedulable_grace_seconds,
                )
                monitor_svc = SwarmMonitor(
                    state=app.state.db,
                    redis_streams=swarm_streams,
                    heartbeat_timeout=settings.swarm_heartbeat_timeout_seconds,
                )

                async def swarm_background_loop():
                    while True:
                        try:
                            active_groups = await app.state.db.list_swarm_groups(
                                status_in=[SwarmStatus.ACTIVE]
                            )
                            for group in active_groups:
                                await watchdog.check_group(group)
                                await monitor_svc.check_heartbeats(group)
                        except Exception:
                            logger.exception("Swarm background loop error")
                        await asyncio.sleep(settings.scheduling_watchdog_interval_seconds)

                swarm_task = asyncio.create_task(swarm_background_loop())
                logger.info("Swarm communication enabled, watchdog started")
            except Exception:
                logger.exception("Failed to start swarm watchdog")
        except Exception:
            logger.exception("Failed to initialize swarm communication")
>>>>>>> origin/main

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
        gateway_manager=gateway_manager,
        trace_store=trace_store,
        swarm_manager=swarm_manager,
        workflow_engine=workflow_engine,
    )

    # Wire up API dependency injection
    app.dependency_overrides[get_db] = lambda: app.state.db
    app.dependency_overrides[get_orchestrator] = lambda: app.state.orchestrator
    app.dependency_overrides[get_settings] = lambda: settings

    # Mount skills API if registry is available
    if skill_registry:
        try:
            from controller.skills.api import router as skills_router, get_skill_registry, get_performance_tracker
            app.dependency_overrides[get_skill_registry] = lambda: skill_registry
            app.dependency_overrides[get_performance_tracker] = lambda: tracker
            app.include_router(skills_router)
            logger.info("Skills API router mounted")
        except Exception:
            logger.exception("Failed to mount skills API router")

    # Mount workflow API if engine is available
    if workflow_engine and template_crud:
        try:
            from controller.workflows.api import router as wf_router, get_template_crud, get_workflow_engine
            app.dependency_overrides[get_template_crud] = lambda: template_crud
            app.dependency_overrides[get_workflow_engine] = lambda: workflow_engine
            app.include_router(wf_router)
            logger.info("Workflow API router mounted")
        except Exception:
            logger.exception("Failed to mount workflow API router")

    # Mount traces API if tracing is enabled
    if trace_store:
        try:
            from controller.tracing.api import router as traces_router, get_trace_store
            app.dependency_overrides[get_trace_store] = lambda: trace_store
            app.include_router(traces_router)
            logger.info("Traces API router mounted")
        except Exception:
            logger.exception("Failed to mount traces API router")

    # Mount webhook routes
    webhook_router = registry.create_router()
    app.include_router(webhook_router)

    logger.info("Ditto Factory started with %d integrations", len(registry.all()))

    # Start subagent handler if enabled
    subagent_handler = None
    subagent_task = None
    if settings.subagent_enabled:
        try:
            from controller.subagent import SubagentHandler

            subagent_handler = SubagentHandler(
                settings=settings,
                redis_state=app.state.redis_state,
                spawner=spawner,
                state=app.state.db,
            )
            subagent_task = asyncio.create_task(subagent_handler.start())
            logger.info("SubagentHandler started")
        except Exception:
            logger.exception("Failed to start SubagentHandler")

    yield

    # Cleanup
    if swarm_task:
        swarm_task.cancel()
        try:
            await swarm_task
        except asyncio.CancelledError:
            pass
    if trace_store:
        await trace_store.close()
    if subagent_handler:
        await subagent_handler.stop()
    if subagent_task:
        subagent_task.cancel()
        try:
            await subagent_task
        except asyncio.CancelledError:
            pass
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
