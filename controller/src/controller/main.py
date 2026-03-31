# src/controller/main.py
from __future__ import annotations
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
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

            # Ensure skill tables exist (SQLite only)
            if settings.database_url.startswith("sqlite"):
                import aiosqlite
                async with aiosqlite.connect(skill_db_path) as _db:
                    await _db.executescript("""
                        CREATE TABLE IF NOT EXISTS skills (
                            id TEXT PRIMARY KEY,
                            slug TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            description TEXT DEFAULT '',
                            content TEXT DEFAULT '',
                            language TEXT DEFAULT '[]',
                            domain TEXT DEFAULT '[]',
                            requires TEXT DEFAULT '[]',
                            tags TEXT DEFAULT '[]',
                            org_id TEXT,
                            repo_pattern TEXT,
                            is_default INTEGER DEFAULT 0,
                            is_active INTEGER DEFAULT 1,
                            version INTEGER DEFAULT 1,
                            embedding TEXT,
                            usage_count INTEGER DEFAULT 0,
                            created_by TEXT DEFAULT '',
                            source_toolkit_id TEXT,
                            source_component_id TEXT,
                            created_at TIMESTAMP DEFAULT (datetime('now')),
                            updated_at TIMESTAMP DEFAULT (datetime('now'))
                        );
                        CREATE TABLE IF NOT EXISTS skill_versions (
                            id TEXT PRIMARY KEY,
                            skill_id TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            content TEXT DEFAULT '',
                            description TEXT DEFAULT '',
                            changelog TEXT,
                            created_by TEXT DEFAULT '',
                            created_at TIMESTAMP DEFAULT (datetime('now'))
                        );
                    """)
                    # Migration: add provenance columns if missing
                    cursor = await _db.execute("PRAGMA table_info(skills)")
                    columns = {row[1] for row in await cursor.fetchall()}
                    if "source_toolkit_id" not in columns:
                        await _db.execute("ALTER TABLE skills ADD COLUMN source_toolkit_id TEXT")
                        await _db.execute("ALTER TABLE skills ADD COLUMN source_component_id TEXT")
                        await _db.commit()
                        logger.info("Migrated skills table: added source_toolkit_id, source_component_id")
                logger.info("Skill tables ensured in SQLite")

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

            # Ensure workflow tables exist (SQLite only)
            if settings.database_url.startswith("sqlite"):
                import aiosqlite
                async with aiosqlite.connect(wf_db_path) as _db:
                    await _db.executescript("""
                        CREATE TABLE IF NOT EXISTS workflow_templates (
                            id TEXT PRIMARY KEY,
                            slug TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            description TEXT DEFAULT '',
                            definition TEXT DEFAULT '{}',
                            parameter_schema TEXT DEFAULT '{}',
                            version INTEGER DEFAULT 1,
                            is_active INTEGER DEFAULT 1,
                            created_by TEXT DEFAULT '',
                            created_at TIMESTAMP DEFAULT (datetime('now')),
                            updated_at TIMESTAMP DEFAULT (datetime('now'))
                        );
                        CREATE TABLE IF NOT EXISTS workflow_template_versions (
                            id TEXT PRIMARY KEY,
                            template_id TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            definition TEXT DEFAULT '{}',
                            parameter_schema TEXT DEFAULT '{}',
                            description TEXT DEFAULT '',
                            changelog TEXT,
                            created_by TEXT DEFAULT '',
                            created_at TIMESTAMP DEFAULT (datetime('now'))
                        );
                        CREATE TABLE IF NOT EXISTS workflow_executions (
                            id TEXT PRIMARY KEY,
                            template_slug TEXT NOT NULL,
                            template_version INTEGER DEFAULT 1,
                            parameters TEXT DEFAULT '{}',
                            status TEXT DEFAULT 'pending',
                            triggered_by TEXT DEFAULT '',
                            started_at TIMESTAMP,
                            completed_at TIMESTAMP,
                            created_at TIMESTAMP DEFAULT (datetime('now'))
                        );
                        CREATE TABLE IF NOT EXISTS workflow_steps (
                            id TEXT PRIMARY KEY,
                            execution_id TEXT NOT NULL,
                            name TEXT NOT NULL,
                            step_type TEXT DEFAULT 'agent',
                            agent_type TEXT DEFAULT 'general',
                            task TEXT DEFAULT '',
                            dependencies TEXT DEFAULT '[]',
                            status TEXT DEFAULT 'pending',
                            result_summary TEXT DEFAULT '{}',
                            started_at TIMESTAMP,
                            completed_at TIMESTAMP
                        );
                    """)
                logger.info("Workflow tables ensured in SQLite")

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

    # Ensure toolkit tables exist (SQLite only) — always created, not feature-gated
    if settings.database_url.startswith("sqlite"):
        import aiosqlite
        tk_db_path = settings.database_url.replace("sqlite:///", "")
        async with aiosqlite.connect(tk_db_path) as _db:
            # Check if we need to migrate from old flat schema
            try:
                cursor = await _db.execute("PRAGMA table_info(toolkits)")
                columns = [row[1] for row in await cursor.fetchall()]
                if "path" in columns or "load_strategy" in columns:
                    # Old flat schema detected — drop and recreate
                    logger.info("Migrating toolkit tables from flat to hierarchical schema")
                    await _db.executescript("""
                        DROP TABLE IF EXISTS toolkit_versions;
                        DROP TABLE IF EXISTS toolkits;
                    """)
            except Exception:
                pass  # Table doesn't exist yet, that's fine

            await _db.executescript("""
                CREATE TABLE IF NOT EXISTS toolkit_sources (
                    id TEXT PRIMARY KEY,
                    github_url TEXT NOT NULL,
                    github_owner TEXT NOT NULL,
                    github_repo TEXT NOT NULL,
                    branch TEXT DEFAULT 'main',
                    last_commit_sha TEXT,
                    last_synced_at TIMESTAMP,
                    status TEXT DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT (datetime('now')),
                    updated_at TIMESTAMP DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS toolkits (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'mixed',
                    description TEXT DEFAULT '',
                    version INTEGER DEFAULT 1,
                    pinned_sha TEXT,
                    source_version TEXT DEFAULT NULL,
                    status TEXT DEFAULT 'available',
                    tags TEXT DEFAULT '[]',
                    component_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT (datetime('now')),
                    updated_at TIMESTAMP DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS toolkit_versions (
                    id TEXT PRIMARY KEY,
                    toolkit_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    pinned_sha TEXT NOT NULL,
                    changelog TEXT,
                    created_at TIMESTAMP DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS toolkit_components (
                    id TEXT PRIMARY KEY,
                    toolkit_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    directory TEXT NOT NULL DEFAULT '',
                    primary_file TEXT NOT NULL DEFAULT '',
                    load_strategy TEXT DEFAULT 'mount_file',
                    content TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    risk_level TEXT DEFAULT 'safe',
                    is_active INTEGER DEFAULT 1,
                    file_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT (datetime('now')),
                    UNIQUE(toolkit_id, slug)
                );

                CREATE TABLE IF NOT EXISTS toolkit_component_files (
                    id TEXT PRIMARY KEY,
                    component_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    is_primary INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT (datetime('now')),
                    UNIQUE(component_id, path)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT (datetime('now'))
                );
            """)

            # Migrate: add source_version column if missing
            try:
                cursor = await _db.execute("PRAGMA table_info(toolkits)")
                columns = [row[1] for row in await cursor.fetchall()]
                if "source_version" not in columns:
                    await _db.execute("ALTER TABLE toolkits ADD COLUMN source_version TEXT DEFAULT NULL")
                    await _db.commit()
                    logger.info("Added source_version column to toolkits table")
            except Exception:
                pass

        logger.info("Toolkit tables ensured in SQLite (hierarchical schema)")

    # Initialize toolkit registry and discovery engine (always, not feature-gated)
    from controller.toolkits.registry import ToolkitRegistry
    from controller.toolkits.github_client import GitHubClient
    from controller.toolkits.discovery import DiscoveryEngine

    tk_db_path = settings.database_url.replace("sqlite:///", "")
    toolkit_registry = ToolkitRegistry(db_path=tk_db_path)

    # Load GitHub token: prefer DB-persisted token, fall back to env var
    github_token = settings.github_token or None
    if settings.database_url.startswith("sqlite"):
        try:
            import aiosqlite as _aiosqlite
            async with _aiosqlite.connect(tk_db_path) as _db:
                cursor = await _db.execute(
                    "SELECT value FROM app_settings WHERE key = ?", ("github_token",)
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    github_token = row[0]
                    logger.info("GitHub token loaded from database")
        except Exception:
            pass

    github_client = GitHubClient(token=github_token)
    discovery_engine = DiscoveryEngine(github_client=github_client)
    logger.info("Toolkit registry and discovery engine initialized (token=%s)", "yes" if github_token else "no")

    # Seed toolkit registry with curated sources on first startup
    from controller.toolkits.seeder import ToolkitSeeder
    seeder = ToolkitSeeder(registry=toolkit_registry, discovery_engine=discovery_engine)
    try:
        seed_result = await seeder.seed_if_empty()
        if not seed_result.get("seeded") and not seed_result.get("failed"):
            logger.info("Toolkit seeding: all sources already imported")
        else:
            seeded = seed_result.get("seeded", [])
            failed = seed_result.get("failed", [])
            skipped = seed_result.get("skipped", [])
            total_components = sum(s.get("components_imported", 0) for s in seeded)
            logger.info(
                "Toolkit seeding: %d imported (%d components), %d skipped, %d failed",
                len(seeded), total_components, len(skipped), len(failed),
            )
    except Exception:
        logger.exception("Toolkit seeding failed (non-fatal)")

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

    # Mount toolkit API
    try:
        from controller.toolkits.api import router as toolkit_router, get_toolkit_registry, get_discovery_engine, get_github_client, get_db_path
        app.dependency_overrides[get_toolkit_registry] = lambda: toolkit_registry
        app.dependency_overrides[get_discovery_engine] = lambda: discovery_engine
        app.dependency_overrides[get_github_client] = lambda: github_client
        app.dependency_overrides[get_db_path] = lambda: tk_db_path
        app.include_router(toolkit_router)
        logger.info("Toolkit API router mounted")
    except Exception:
        logger.exception("Failed to mount toolkit API router")

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


# ---------------------------------------------------------------------------
# SSE endpoint: stream events for a thread via Redis pub/sub
# ---------------------------------------------------------------------------

@app.get("/api/events/{thread_id}")
async def stream_events(thread_id: str, request: Request):
    """Server-Sent Events endpoint that subscribes to Redis pub/sub for a thread."""
    redis_client = app.state.redis_state._redis  # reuse existing Redis connection pool
    channel_name = f"thread:{thread_id}:events"

    async def event_generator():
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel_name)
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=30.0,
                )

                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")

                    # Try to parse as JSON to extract event type
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event", "message")
                        yield f"event: {event_type}\ndata: {data}\n\n"
                    except (json.JSONDecodeError, TypeError):
                        yield f"data: {data}\n\n"
                elif message is None:
                    # Timeout — send keepalive
                    yield ": keepalive\n\n"
        except asyncio.TimeoutError:
            # 30s timeout with no message — send keepalive and continue
            yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Dashboard summary endpoint
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
async def dashboard_summary():
    """Return summary stats for the dashboard."""
    db = app.state.db

    threads = await db.list_threads()
    total_threads = len(threads)

    active_count = sum(1 for t in threads if t.status.value in ("running", "queued"))

    # Count completed/failed in last 24 hours
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    completed_24h = 0
    failed_24h = 0
    durations = []

    for thread in threads:
        # Try to get the latest job for each thread
        try:
            job = await db.get_latest_job_for_thread(thread.id)
        except Exception:
            job = None

        if job is None:
            continue

        completed_at = getattr(job, "completed_at", None)
        started_at = getattr(job, "started_at", None)

        if completed_at:
            try:
                if isinstance(completed_at, str):
                    completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                else:
                    completed_dt = completed_at
                if completed_dt.tzinfo is None:
                    completed_dt = completed_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                completed_dt = None
        else:
            completed_dt = None

        if completed_dt and completed_dt >= cutoff:
            if job.status.value == "completed":
                completed_24h += 1
            elif job.status.value == "failed":
                failed_24h += 1

        if started_at and completed_at:
            try:
                if isinstance(started_at, str):
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                else:
                    started_dt = started_at
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                if completed_dt:
                    delta = (completed_dt - started_dt).total_seconds()
                    if delta > 0:
                        durations.append(delta)
            except (ValueError, TypeError):
                pass

    avg_duration = sum(durations) / len(durations) if durations else 0

    return {
        "active_count": active_count,
        "completed_24h": completed_24h,
        "failed_24h": failed_24h,
        "avg_duration_seconds": round(avg_duration, 1),
    }
