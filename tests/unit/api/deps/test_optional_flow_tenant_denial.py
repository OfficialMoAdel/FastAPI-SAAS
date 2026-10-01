"""P1-6: the optional-auth path must not convert a tenant denial into anonymous.

``_resolve_optional_bearer_flow`` states the C3 rule in its own docstring: a
PRESENTED bearer token that fails verification is fail-loud (401/403), never
silent anonymous. Before the sentinel fix, a tenant-less request never reached
the binding check. Now that it does, the resulting TenantContextError must
surface rather than being caught by the bare ``except Exception`` and
rewritten as NO_CREDENTIAL — which would serve the anonymous view to a caller
holding a valid token, the exact fail-open C3 forbids.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest


def _bound_tenant(tenant_id: int = 5):
    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=UUID(int=tenant_id),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


def _request():
    request = MagicMock()
    request.headers = {}
    request.method = "GET"
    request.url.path = "/"
    request.app = MagicMock()
    request.app.dependency_overrides = {}
    request.state = MagicMock()
    request.state.is_token_blacklisted = False
    return request


def _user(tenant_id: int = 5):
    return {
        "id": 1,
        "email": "user@example.com",
        "is_active": True,
        "is_deleted": False,
        "token_version": 0,
        "tenant_id": UUID(int=tenant_id),
    }


def _dumpable(user):
    m = MagicMock()
    m.model_dump.return_value = user
    return m


def _valid_token_patches():
    return (
        patch(
            "src.app.api.deps.auth._resolve_token_data",
            new=AsyncMock(return_value=(MagicMock(), "user@example.com", 0, "j1", None, "sid-1")),
        ),
        patch("src.app.api.deps.auth._load_and_validate_user", new=AsyncMock(return_value=_user())),
        patch("src.app.api.deps.auth._enforce_session_valid", new=AsyncMock(return_value=None)),
    )


class TestOptionalFlowFailsLoudOnTenantDenial:
    """A valid token plus an unbound tenant must NOT become anonymous."""

    @pytest.mark.asyncio
    async def test_tenant_denial_is_not_converted_to_anonymous(self) -> None:
        """The C3 rule: a presented credential that fails is never silent."""
        from src.app.api.deps.auth import _resolve_optional_bearer_flow
        from src.app.core.tenancy.context import NO_TENANT

        p1, p2, p3 = _valid_token_patches()

        with p1, p2, p3, patch("src.app.api.deps.auth.get_current_tenant", return_value=NO_TENANT):
            with pytest.raises(Exception) as exc:
                await _resolve_optional_bearer_flow(_request(), MagicMock(), "tok", None, NO_TENANT)

        # It must RAISE the tenant denial, not NO_CREDENTIAL ("User not authenticated").
        message = str(exc.value).lower()
        assert "tenant context required" in message, (
            f"tenant denial was swallowed, got: {type(exc.value).__name__}: {exc.value}"
        )
        assert "not authenticated" not in message, "the denial was rewritten as anonymous"

    @pytest.mark.asyncio
    async def test_unexpected_error_still_becomes_no_credential(self) -> None:
        """Anti-over-fix: a genuine surprise is still logged and mapped.

        The re-raise is scoped to the three domain errors; the bare handler
        must keep its C3 behaviour for anything else.
        """
        from src.app.api.deps.auth import _resolve_optional_bearer_flow
        from src.app.core.schemas import TokenData

        p1, p2, p3 = _valid_token_patches()
        p3.stop()  # replace the session guard with a surprise
        p1.stop()

        with (
            patch(
                "src.app.api.deps.auth._resolve_token_data",
                new=AsyncMock(return_value=(TokenData(username_or_email="u", token_version=0), "u", 0, "j", None, "s")),
            ),
            patch("src.app.api.deps.auth._load_and_validate_user", new=AsyncMock(return_value=_user())),
            patch(
                "src.app.api.deps.auth._enforce_session_valid",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with pytest.raises(Exception) as exc:
                await _resolve_optional_bearer_flow(_request(), MagicMock(), "tok", None, _bound_tenant())

        assert "not authenticated" in str(exc.value).lower(), f"expected NO_CREDENTIAL, got: {exc.value}"
