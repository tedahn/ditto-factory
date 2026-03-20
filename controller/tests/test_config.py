from controller.config import Settings


def test_default_settings():
    s = Settings(anthropic_api_key="test-key")
    assert s.redis_url == "redis://localhost:6379"
    assert s.max_job_duration_seconds == 1800
    assert s.slack_enabled is False


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("DF_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DF_SLACK_ENABLED", "true")
    s = Settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.slack_enabled is True
