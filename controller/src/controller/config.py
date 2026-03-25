from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Core
    anthropic_api_key: str = ""
    redis_url: str = "redis://localhost:6379"
    database_url: str = "postgresql://localhost:5432/aal"

    # Agent
    agent_image: str = "ditto-factory-agent:latest"
    image_pull_policy: str = "IfNotPresent"
    max_job_duration_seconds: int = 1800
    job_ttl_seconds: int = 300
    agent_cpu_request: str = "500m"
    agent_memory_request: str = "2Gi"
    agent_cpu_limit: str = "2"
    agent_memory_limit: str = "8Gi"

    # Conversation
    conversation_history_limit: int = 50

    # Safety
    auto_open_pr: bool = True
    require_ci: bool = False
    ci_timeout_seconds: int = 600
    retry_on_empty_result: bool = True
    max_empty_retries: int = 1

    # Integrations (enabled flags)
    slack_enabled: bool = False
    slack_signing_secret: str = ""
    slack_bot_token: str = ""
    slack_bot_user_id: str = ""

    linear_enabled: bool = False
    linear_webhook_secret: str = ""
    linear_api_key: str = ""

    github_enabled: bool = False
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_private_key: str = ""
    github_allowed_orgs: list[str] = []
    github_user_oauth: bool = False

    # Skill Registry
    skill_registry_enabled: bool = False
    skill_embedding_provider: str = "none"
    skill_embedding_model: str = "voyage-3"
    skill_max_per_task: int = 5
    skill_min_similarity: float = 0.5
    skill_max_total_chars: int = 16000
    voyage_api_key: str = ""

    # MCP Gateway
    gateway_enabled: bool = False
    gateway_url: str = ""  # e.g., "http://ditto-factory-gateway:3001"
    gateway_default_tools: list[str] = []  # tools enabled for all sessions

    # API
    api_key: str = ""

    # Subagent spawning
    subagent_enabled: bool = False
    max_subagents_per_task: int = 3
    subagent_timeout_seconds: int = 600
    subagent_inherit_branch: bool = True
    subagent_depth_limit: int = 1

    # Tracing
    tracing_enabled: bool = False
    trace_db_path: str = "traces.db"
    trace_retention_days: int = 30
    trace_batch_size: int = 50
    trace_flush_interval: float = 5.0
    trace_auto_report: bool = False

    # Observability
    structured_logs: bool = True
    metrics_enabled: bool = False
    metrics_port: int = 9090

    # Generalized Task Types
    analysis_enabled: bool = False
    db_mutation_enabled: bool = False
    file_output_enabled: bool = False
    api_action_enabled: bool = False
    artifact_storage_path: str = "/tmp/df-artifacts"
    require_approval_for_mutations: bool = True
    max_artifact_size_mb: int = 100

    # Swarm Communication
    swarm_enabled: bool = False
    swarm_max_agents_per_group: int = 10
    swarm_heartbeat_interval_seconds: int = 30
    swarm_heartbeat_timeout_seconds: int = 90
    swarm_stream_ttl_seconds: int = 7200
    swarm_message_max_size_bytes: int = 65536
    swarm_stream_maxlen: int = 10000
    swarm_pel_gc_interval_seconds: int = 60
    swarm_stream_checkpoint_interval: int = 60
    swarm_redis_max_connections: int = 20
    swarm_redis_socket_timeout: float = 5.0

    # Swarm Rate Limiting
    swarm_rate_limit_messages_per_min: int = 60
    swarm_rate_limit_broadcasts_per_min: int = 20
    swarm_rate_limit_bytes_per_min: int = 524288

    # Scheduling Watchdog
    scheduling_watchdog_interval_seconds: int = 15
    scheduling_unschedulable_grace_seconds: int = 120

    # Workflow Engine
    workflow_enabled: bool = False
    max_agents_per_execution: int = 20
    max_concurrent_agents: int = 50
    workflow_step_timeout_seconds: int = 1800
    workflow_default_template: str = "single-task"
    intent_classifier_enabled: bool = False
    intent_confidence_threshold: float = 0.7
    intent_max_input_chars: int = 2000

    model_config = {"env_prefix": "DF_"}
