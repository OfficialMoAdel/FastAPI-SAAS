"""The default SSO callback URL is derived from configuration, not the Host header.

`SSOService.get_authorization_url` used to build its default `redirect_uri`
from `request.base_url`. That value is assembled from the incoming Host
header, which the client controls: a request arriving through a proxy (or
straight from a hostile client) could pin the callback at an attacker's host
and the flow would hand that URI to the identity provider. The URI is now
built from `settings.app.FRONTEND_URL` and rejected when that value cannot
produce a usable absolute URL.

These pin the three properties that matter:
* the Host header cannot influence the default callback URL;
* the derived URI is exactly the one the IdP would have registered, including
  the provider's wire value (the pre-fix f-string rendered the str-Enum as
  `SSOProvider.GOOGLE`, producing a path segment that matches no real route);
* a misconfigured FRONTEND_URL fails closed with a domain error instead of
  silently producing a URI that will never be accepted.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import Request

from src.app.core.config import settings
from src.app.modules.auth.schemas import SSOProvider
from src.app.modules.auth.services.sso import SSOService
from src.app.shared.exceptions import ValidationError

CALLBACK_TEMPLATE = "/api/v1/account/sso/{provider}/callback"


@pytest.fixture
def service():
    return SSOService()


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_request():
    """A request whose Host header is attacker-chosen, not the real origin."""
    request = MagicMock(spec=Request)
    request.base_url = "http://evil.example.com/"
    request.headers = {"host": "evil.example.com", "user-agent": "test-agent"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


@pytest.fixture
def configured_origin():
    """Pin FRONTEND_URL for the duration of a test."""
    original = settings.app.FRONTEND_URL

    def _set(value):
        settings.app.FRONTEND_URL = value
        return value

    yield _set
    settings.app.FRONTEND_URL = original


@pytest.fixture
def enabled_provider():
    """Provider settings + the Redis state seam, so the flow runs to the URL."""
    with patch(
        "src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings",
        new_callable=AsyncMock,
    ) as get_settings:
        get_settings.return_value = {
            "is_enabled": True,
            "client_id": "client_id",
            "client_secret": "secret",
        }
        with patch("src.app.modules.auth.services.sso.get_current_tenant") as tenant:
            tenant.return_value = MagicMock(id=uuid4())
            with patch("src.app.modules.auth.services.sso.get_sso_state_manager") as state_manager:
                manager = AsyncMock()
                manager.create = AsyncMock()
                state_manager.return_value = manager
                with patch(
                    "src.app.core.security.tokens.TokenGenerator.generate_sso_state",
                    return_value="state-token",
                ):
                    yield manager


def _redirect_uri_of(auth_url: str) -> str:
    """Pull the redirect_uri the provider URL actually advertises."""
    return parse_qs(urlsplit(auth_url).query)["redirect_uri"][0]


# ==================== the Host header is not the source of truth ====================


class TestDefaultRedirectUriIgnoresHostHeader:
    """The defect: a client-chosen Host must not reach the IdP."""

    async def test_attacker_host_header_does_not_reach_the_provider(
        self, service, mock_db, mock_request, configured_origin, enabled_provider
    ):
        configured_origin("https://app.example.com")

        auth_url, _state = await service.get_authorization_url(mock_db, mock_request, SSOProvider.GOOGLE)

        assert "evil.example.com" not in auth_url
        assert _redirect_uri_of(auth_url) == f"https://app.example.com{CALLBACK_TEMPLATE.format(provider='google')}"

    async def test_two_hosts_same_config_produce_one_callback(
        self, service, mock_db, enabled_provider, configured_origin
    ):
        """Two clients on different Hosts must not get two different callbacks."""
        configured_origin("https://app.example.com")

        def _request_with(host):
            request = MagicMock(spec=Request)
            request.base_url = f"http://{host}/"
            request.headers = {"host": host}
            return request

        first, _ = await service.get_authorization_url(mock_db, _request_with("a.example.com"), "google")
        second, _ = await service.get_authorization_url(mock_db, _request_with("b.example.com"), "google")

        assert _redirect_uri_of(first) == _redirect_uri_of(second)


# ==================== the derived URI is the registered one ====================


class TestDerivedRedirectUriShape:
    async def test_uses_the_configured_origin_and_provider_wire_value(
        self, service, mock_db, mock_request, configured_origin, enabled_provider
    ):
        configured_origin("https://app.example.com")

        auth_url, _ = await service.get_authorization_url(mock_db, mock_request, SSOProvider.GOOGLE)

        # `google`, not the str-Enum's repr rendering — the pre-fix f-string
        # produced a `SSOProvider.GOOGLE` path segment matching no real route.
        assert _redirect_uri_of(auth_url) == "https://app.example.com/api/v1/account/sso/google/callback"

    @pytest.mark.parametrize("provider", list(SSOProvider))
    def test_every_provider_gets_its_own_callback(self, provider, configured_origin):
        configured_origin("https://app.example.com")

        assert SSOService.default_redirect_uri(provider) == (
            f"https://app.example.com{CALLBACK_TEMPLATE.format(provider=provider.value)}"
        )

    def test_trailing_slash_does_not_double_up(self, configured_origin):
        configured_origin("https://app.example.com/")

        assert SSOService.default_redirect_uri("google") == "https://app.example.com/api/v1/account/sso/google/callback"

    def test_deployment_path_prefix_is_preserved(self, configured_origin):
        """A reverse proxy mounting the app under a prefix must not lose it."""
        configured_origin("https://app.example.com/edge/")

        assert (
            SSOService.default_redirect_uri("google")
            == "https://app.example.com/edge/api/v1/account/sso/google/callback"
        )

    def test_plain_string_provider_matches_enum_provider(self, configured_origin):
        """Routers pass the enum, internal callers may pass the raw string."""
        configured_origin("https://app.example.com")

        assert SSOService.default_redirect_uri("google") == SSOService.default_redirect_uri(SSOProvider.GOOGLE)

    async def test_caller_supplied_redirect_uri_still_wins(
        self, service, mock_db, mock_request, configured_origin, enabled_provider
    ):
        """The explicit parameter is still honoured (the router pins it)."""
        configured_origin("https://app.example.com")

        auth_url, _ = await service.get_authorization_url(
            mock_db, mock_request, SSOProvider.GOOGLE, redirect_uri="https://app.example.com/custom/cb"
        )

        assert _redirect_uri_of(auth_url) == "https://app.example.com/custom/cb"


# ==================== a bad configuration fails closed ====================


class TestMisconfiguredOriginFailsClosed:
    """No silent fallback: an unusable FRONTEND_URL must raise, not guess."""

    @pytest.mark.parametrize(
        ("value", "reason"),
        [
            ("", "not configured"),
            ("   ", "blank"),
            ("app.example.com", "no scheme"),
            ("//app.example.com", "no scheme"),
            ("ftp://app.example.com", "non-http scheme"),
            ("javascript:alert(1)", "non-http scheme"),
            ("https://app.example.com/?next=evil", "query string"),
            ("https://app.example.com/#frag", "fragment"),
            ("https://user:pass@app.example.com", "embedded credentials"),
        ],
    )
    def test_rejects_unusable_origin(self, value, reason, configured_origin):
        configured_origin(value)

        with pytest.raises(ValidationError):
            SSOService.default_redirect_uri("google"), reason

    async def test_flow_refuses_to_start_with_a_misconfigured_origin(
        self, service, mock_db, mock_request, configured_origin, enabled_provider
    ):
        configured_origin("app.example.com")

        with pytest.raises(ValidationError):
            await service.get_authorization_url(mock_db, mock_request, SSOProvider.GOOGLE)

        # Nothing was handed to the provider or stored in state.
        assert enabled_provider.create.await_count == 0
