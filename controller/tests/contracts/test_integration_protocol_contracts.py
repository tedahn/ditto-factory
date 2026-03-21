"""Contract 12: Integration Protocol Conformance.

Verifies all Integration implementations satisfy the Integration protocol.
"""
import pytest
from controller.integrations.protocol import Integration
from controller.integrations.github import GitHubIntegration
from controller.integrations.slack import SlackIntegration
from controller.integrations.linear import LinearIntegration


INTEGRATION_CLASSES = [
    ("github", GitHubIntegration, {"webhook_secret": "test"}),
    ("slack", SlackIntegration, {"signing_secret": "test", "bot_token": "xoxb-test"}),
    ("linear", LinearIntegration, {"webhook_secret": "test", "api_key": "lin_test"}),
]


class TestIntegrationProtocolConformance:
    """Verify all Integration implementations satisfy the protocol."""

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_is_protocol_conformant(self, name, cls, kwargs):
        """Each integration class must satisfy the Integration protocol."""
        instance = cls(**kwargs)
        assert isinstance(instance, Integration), (
            f"{cls.__name__} does not satisfy Integration protocol"
        )

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_has_name_attribute(self, name, cls, kwargs):
        instance = cls(**kwargs)
        assert hasattr(instance, "name")
        assert instance.name == name

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_has_all_protocol_methods(self, name, cls, kwargs):
        """All required methods exist with correct names."""
        instance = cls(**kwargs)
        for method_name in ("parse_webhook", "fetch_context", "report_result", "acknowledge"):
            assert hasattr(instance, method_name), f"{cls.__name__} missing {method_name}"
            assert callable(getattr(instance, method_name))

    @pytest.mark.parametrize("name,cls,kwargs", INTEGRATION_CLASSES)
    def test_method_signatures_match_protocol(self, name, cls, kwargs):
        """Method signatures must be compatible with protocol."""
        import inspect

        instance = cls(**kwargs)

        # parse_webhook(self, request) -> TaskRequest | None
        sig = inspect.signature(instance.parse_webhook)
        params = list(sig.parameters.keys())
        assert "request" in params

        # fetch_context(self, thread) -> str
        sig = inspect.signature(instance.fetch_context)
        params = list(sig.parameters.keys())
        assert "thread" in params

        # report_result(self, thread, result) -> None
        sig = inspect.signature(instance.report_result)
        params = list(sig.parameters.keys())
        assert "thread" in params
        assert "result" in params

        # acknowledge(self, request) -> None
        sig = inspect.signature(instance.acknowledge)
        params = list(sig.parameters.keys())
        assert "request" in params
