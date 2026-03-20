import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

def test_health_endpoint():
    # Import with mocked settings to avoid needing real Redis/DB
    with patch("controller.main.settings") as mock_settings:
        mock_settings.database_url = "sqlite:///test.db"
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.slack_enabled = False
        mock_settings.linear_enabled = False
        mock_settings.github_enabled = False

        from controller.main import app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

def test_ready_endpoint():
    with patch("controller.main.settings") as mock_settings:
        mock_settings.database_url = "sqlite:///test.db"
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.slack_enabled = False
        mock_settings.linear_enabled = False
        mock_settings.github_enabled = False

        from controller.main import app
        client = TestClient(app)
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
