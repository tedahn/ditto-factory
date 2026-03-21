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

    # API
    api_key: str = ""

    # Observability
    structured_logs: bool = True
    metrics_enabled: bool = False
    metrics_port: int = 9090

    # Skill Registry
    skill_registry_enabled: bool = False
    skill_embedding_provider: str = "none"  # "none" for Phase 1, "voyage" for Phase 2
    skill_embedding_model: str = "voyage-3"
    skill_max_per_task: int = 5
    skill_min_similarity: float = 0.5
    skill_max_total_chars: int = 16000
    voyage_api_key: str = ""

    model_config = {"env_prefix": "DF_"}
