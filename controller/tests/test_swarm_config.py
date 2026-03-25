"""Tests for swarm configuration settings."""
from controller.config import Settings


class TestSwarmConfig:
    def test_swarm_disabled_by_default(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_enabled is False

    def test_swarm_max_agents_default(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_max_agents_per_group == 10

    def test_swarm_heartbeat_defaults(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_heartbeat_interval_seconds == 30
        assert s.swarm_heartbeat_timeout_seconds == 90

    def test_swarm_stream_defaults(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_stream_maxlen == 10000
        assert s.swarm_stream_ttl_seconds == 7200

    def test_swarm_rate_limit_defaults(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_rate_limit_messages_per_min == 60
        assert s.swarm_rate_limit_broadcasts_per_min == 20
        assert s.swarm_rate_limit_bytes_per_min == 524288

    def test_scheduling_watchdog_defaults(self):
        s = Settings(anthropic_api_key="test")
        assert s.scheduling_watchdog_interval_seconds == 15
        assert s.scheduling_unschedulable_grace_seconds == 120

    def test_swarm_message_max_size(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_message_max_size_bytes == 65536

    def test_swarm_redis_pool(self):
        s = Settings(anthropic_api_key="test")
        assert s.swarm_redis_max_connections == 20
        assert s.swarm_redis_socket_timeout == 5.0
