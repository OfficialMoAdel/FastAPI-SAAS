"""
MFA Router Tests

Unit tests for the MFA router endpoints, including rate limiting.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestMFAMeterSelection:
    """Which meter each MFA door crosses, and on which identity fact.

    Replaces tests for the deleted ``check_mfa_rate_limit``, whose whole
    premise was that the caller chose the identifier (falling back to the
    client IP). That choice is what let one defense be metered under several
    key spellings, so it is gone: the door names the action, the table names
    the meter and the identity kind.
    """

    @pytest.mark.asyncio
    async def test_step_up_door_charges_the_stepup_meter_on_the_account(self, mock_redis):
        """POST /verify must charge MFA_STEPUP_VERIFY, keyed on the account.

        This test used to assert only that two static enum strings differed
        and that a just-constructed Mock was not None — it could not fail.
        The three-MFA-meter separation is a security property (merging any
        two meters would couple two independent limits), so the step-up
        door needs the same wiring assertion its mutate sibling already has.

        /verify calls check() directly rather than through
        check_mfa_mutate_rate_limit, so the route itself is driven here.
        """
        from src.app.core.security.budgets import Identity, SecurityAction
        from src.app.modules.auth.routers import mfa as mfa_router
        from src.app.modules.auth.schemas import MFAVerifyRequest

        ctx = Mock()
        ctx.user_id = 42
        ctx.db = AsyncMock()

        service = Mock()
        service.verify_totp_token = AsyncMock(return_value={"valid": True})

        with patch("src.app.modules.auth.routers.mfa.check", new_callable=AsyncMock) as mock_budget:
            await mfa_router.verify_mfa(
                request=MFAVerifyRequest(totp_token="123456"),
                ctx=ctx,
                service=service,
                fastapi_request=Mock(),
            )

            mock_budget.assert_awaited_once_with(
                SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id=42)
            )

    @pytest.mark.asyncio
    async def test_mutate_door_charges_the_mutation_meter_on_the_account(self, mock_redis):
        """P1-5: mutations cross a separate budget (mfa_mutate), keyed on user_id."""
        from fastapi import Request

        from src.app.core.security.budgets import Identity, SecurityAction
        from src.app.modules.auth.routers.mfa import check_mfa_mutate_rate_limit

        request = Mock(spec=Request)

        with patch(
            "src.app.modules.auth.routers.mfa.check",
            new_callable=AsyncMock,
        ) as mock_budget:
            await check_mfa_mutate_rate_limit(request, user_id=42)

            mock_budget.assert_awaited_once_with(SecurityAction.MFA_MUTATE, Identity(user_id=42))

    @pytest.mark.asyncio
    async def test_no_mfa_helper_still_accepts_a_caller_chosen_identifier(self, mock_redis):
        """The caller cannot pick the identity kind, so the IP fallback is gone."""
        import inspect

        from src.app.modules.auth.routers import mfa as mfa_router

        helper = mfa_router.check_mfa_mutate_rate_limit
        assert "identifier" not in inspect.signature(helper).parameters
        assert "budget" not in inspect.signature(helper).parameters

class TestMFARouterEndpoints:
    """Test MFA router endpoint registration and basic functionality."""

    def test_complete_login_maps_issued_session_via_dependency_override(self) -> None:
        from fastapi import FastAPI, Request, Response
        from fastapi.testclient import TestClient

        from src.app.core.db.database import async_get_db
        from src.app.modules.auth.routers.mfa import router
        from src.app.modules.auth.schemas import MFACompleteLoginRequest
        from src.app.modules.auth.services.mfa import MFAService, get_mfa_service
        from src.app.modules.auth.services.tokens import IssuedSession

        app = FastAPI()
        app.include_router(router)
        db = Mock()
        service = Mock(spec=MFAService)

        async def complete(request, passed_db, *, fastapi_request, response):
            assert isinstance(request, MFACompleteLoginRequest)
            assert request.mfa_token == "a" * 32
            assert request.totp_token == "123456"
            assert passed_db is db
            assert isinstance(fastapi_request, Request)
            assert isinstance(response, Response)
            response.set_cookie("completion-test", "set-by-service", httponly=True)
            return IssuedSession(access_token="issued-access", refresh_token="private-refresh")

        service.complete_login = AsyncMock(side_effect=complete)
        app.dependency_overrides[async_get_db] = lambda: db
        app.dependency_overrides[get_mfa_service] = lambda: service
        with TestClient(app) as client:
            response = client.post("/mfa/complete-login", json={"mfa_token": "a" * 32, "totp_token": "123456"})
        assert response.status_code == 200
        assert response.json() == {
            "access_token": "issued-access", "token_type": "bearer", "message": "Login successful",
        }
        assert response.cookies["completion-test"] == "set-by-service"
        assert "private-refresh" not in response.text
        service.complete_login.assert_awaited_once()

    def test_mfa_router_registered(self, client):
        """MFA router should be registered in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check that MFA endpoints exist
        assert "/api/v1/mfa/setup" in paths
        assert "/api/v1/mfa/enable" in paths
        assert "/api/v1/mfa/verify" in paths
        assert "/api/v1/mfa/disable" in paths
        assert "/api/v1/mfa/status" in paths
        assert "/api/v1/mfa/backup-codes" in paths
        assert "/api/v1/mfa/complete-login" in paths

    def test_mfa_endpoint_tags(self, client):
        """MFA endpoints should have correct tags."""
        response = client.get("/openapi.json")
        spec = response.json()

        # Check tags
        setup_endpoint = spec["paths"]["/api/v1/mfa/setup"]["post"]
        assert "mfa" in setup_endpoint["tags"]

        verify_endpoint = spec["paths"]["/api/v1/mfa/verify"]["post"]
        assert "mfa" in verify_endpoint["tags"]
