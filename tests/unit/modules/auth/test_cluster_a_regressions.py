"""Regression tests for Cluster A (Auth & SSO) fixes.

Each test pins behavior that WAS BROKEN before the fix and MUST stay fixed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# A3: Microsoft userinfo_endpoint is OIDC UserInfo, not Graph /me
# =============================================================================


class TestMicrosoftEndpointMismatch:
    def test_userinfo_endpoint_is_oidc_not_graph_me(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        assert "oidc/userinfo" in p.userinfo_endpoint
        assert "/v1.0/me" not in p.userinfo_endpoint

    def test_scopes_do_not_include_graph_permissions(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        scopes = p.get_default_scopes()
        assert "User.Read" not in scopes
        for s in ("openid", "email", "profile", "offline_access"):
            assert s in scopes


# =============================================================================
# A4: tri-state email_verified + trusts_identity_without_claim hook
# =============================================================================


class TestEmailVerifiedTriState:
    def test_google_defaults_trusts_identity_without_claim_to_false(self):
        from src.app.modules.auth.providers.google import GoogleProvider

        g = GoogleProvider("cid", "sec", "http://localhost/cb")
        assert g.trusts_identity_without_claim() is False

    def test_microsoft_overrides_trusts_identity_without_claim_to_true(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        m = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        assert m.trusts_identity_without_claim() is True

    def test_apple_overrides_trusts_identity_without_claim_to_true(self):
        from src.app.modules.auth.providers.apple import AppleProvider

        a = AppleProvider("cid", "sec", "http://localhost/cb", "t", "k", "pk")
        assert a.trusts_identity_without_claim() is True

    @pytest.mark.asyncio
    async def test_microsoft_no_id_token_yields_none_not_false(self):
        """Without id_token, email_verified must be None (unknown), not False."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        mock_data = {"sub": "obj-123", "email": "u@t.onmicrosoft.com", "name": "U"}
        with patch.object(p, "_http_get", new_callable=AsyncMock, return_value=mock_data):
            info = await p.get_user_info("fake-access-token", id_token=None)
        assert info.email_verified is None

    @pytest.mark.asyncio
    async def test_microsoft_reads_xms_edov(self):
        """Personal-account verification comes from xms_edov, not email_verified."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        claims = {"xms_edov": True, "iss": "https://login.microsoftonline.com/common/v2.0"}
        with (
            patch.object(p, "_http_get", new_callable=AsyncMock, return_value={"sub": "s1", "email": "e@x.com"}),
            patch.object(p, "_verify_id_token", new_callable=AsyncMock, return_value=claims),
        ):
            info = await p.get_user_info("tok", id_token="fake-id-token")
        assert info.email_verified is True


# =============================================================================
# A5: step-up password charges MFA_MUTATE budget
# =============================================================================


class TestStepUpBudget:
    @pytest.mark.asyncio
    async def test_budget_charged_before_password_check(self):
        """The MFA_MUTATE budget is charged before the password comparison.

        ``check`` is imported INSIDE the function (to keep the module free of
        a cycle), so the patch target is the budgets module itself.
        """
        from src.app.core.security import budgets
        from src.app.modules.auth.services.step_up import verify_step_up_password

        with (
            patch.object(budgets, "check", new_callable=AsyncMock) as mock_check,
            patch("src.app.modules.users.UserDirectory.active", new_callable=AsyncMock, return_value=None),
        ):
            with pytest.raises(Exception):
                await verify_step_up_password(MagicMock(), user_id=42, password="pw")
            mock_check.assert_called_once()
            from src.app.core.security.budgets import SecurityAction

            assert mock_check.call_args[0][0] == SecurityAction.MFA_MUTATE
            # Keyed on the authenticated principal, not the IP.
            identity = mock_check.call_args[0][1]
            assert identity.user_id == 42

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_propagates(self):
        from src.app.core.security import budgets
        from src.app.modules.auth.services.step_up import verify_step_up_password
        from src.app.shared.exceptions import RateLimitExceededError

        with patch.object(
            budgets,
            "check",
            new_callable=AsyncMock,
            side_effect=RateLimitExceededError("Too many attempts", retry_after=60),
        ):
            with pytest.raises(RateLimitExceededError):
                await verify_step_up_password(MagicMock(), user_id=1, password="x")


# =============================================================================
# A6: login_helpers raises ServiceUnavailableError, not HTTPException
# =============================================================================


class TestSessionRegistrationFailMode:
    def test_fail_closed_raises_domain_error(self):
        from src.app.core.security.login_helpers import _enforce_session_registration_fail_mode
        from src.app.shared.exceptions import ServiceUnavailableError

        with patch("src.app.core.security.login_helpers.settings") as mock_settings:
            mock_settings.auth.SESSION_REGISTRATION_FAIL_MODE = "fail_closed"
            with pytest.raises(ServiceUnavailableError):
                _enforce_session_registration_fail_mode()

    def test_fail_open_does_not_raise(self):
        from src.app.core.security.login_helpers import _enforce_session_registration_fail_mode

        with patch("src.app.core.security.login_helpers.settings") as mock_settings:
            mock_settings.auth.SESSION_REGISTRATION_FAIL_MODE = "fail_open"
            _enforce_session_registration_fail_mode()

# =============================================================================
# A7: delete_user_token_cache performs only the global session sweep
# =============================================================================


class TestDeleteUserTokenCacheNoGuess:
    @pytest.mark.asyncio
    async def test_it_sweeps_every_session_for_the_user(self):
        """The method's remaining contract: kill all device sessions.

        The old implementation ALSO wrote a guessed ``token_version`` key;
        that write is gone. The behavior below is what the method is for.
        """
        from src.app.core.security import session_manager as session_manager_module
        from src.app.modules.auth.services.passwords import PasswordLifecycleService

        manager = AsyncMock()
        with patch.object(session_manager_module, "session_manager", manager):
            service = PasswordLifecycleService()
            await service.delete_user_token_cache({"id": 42})

        manager.invalidate_all_sessions.assert_awaited_once_with("42")


# =============================================================================
# A11: Microsoft provider_user_id rejects missing sub
# =============================================================================


class TestMicrosoftProviderUserIdValidation:
    @pytest.mark.asyncio
    async def test_missing_sub_raises_valueerror(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        with patch.object(p, "_http_get", new_callable=AsyncMock, return_value={"email": "x@y.com"}):
            with pytest.raises(ValueError, match="sub"):
                await p.get_user_info("tok")


# =============================================================================
# A12: issuer pinned exactly, endpoints tenant-aware
# =============================================================================


class TestMicrosoftIssuerPinning:
    def test_multi_tenant_accepts_tenant_guid_shape_only(self):
        """Multi-tenant: a concrete tenant GUID is accepted, junk is not."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert p._issuer_is_allowed(f"https://login.microsoftonline.com/{guid}/v2.0") is True
        # A12: the old prefix/suffix check accepted ANY path segment.
        assert p._issuer_is_allowed("https://login.microsoftonline.com/evil/v2.0") is False
        assert p._issuer_is_allowed("https://login.microsoftonline.com/common/v2.0") is False
        assert p._issuer_is_allowed("https://evil.example.com/x/v2.0") is False

    def test_single_tenant_pins_exact_guid(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        p = MicrosoftProvider("cid", "sec", "http://localhost/cb", tenant_id=guid)
        assert p._issuer_is_allowed(f"https://login.microsoftonline.com/{guid}/v2.0") is True
        # A DIFFERENT tenant is rejected even though its shape is valid.
        other = "ffffffff-1111-2222-3333-444444444444"
        assert p._issuer_is_allowed(f"https://login.microsoftonline.com/{other}/v2.0") is False

    def test_endpoints_follow_tenant(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        p = MicrosoftProvider("cid", "sec", "http://localhost/cb", tenant_id=guid)
        assert guid in p.authorization_endpoint
        assert guid in p.token_endpoint
        # Multi-tenant default uses common
        p2 = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        assert "/common/" in p2.authorization_endpoint


# =============================================================================
# A14: verify_password guards non-string hashed_password
# =============================================================================


class TestVerifyPasswordGuard:
    @pytest.mark.asyncio
    async def test_none_hash_returns_false(self):
        from src.app.core.security.password import verify_password

        result = await verify_password("anything", None)  # type: ignore[arg-type]
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_string_hash_returns_false(self):
        from src.app.core.security.password import verify_password

        result = await verify_password("anything", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_int_hash_returns_false(self):
        from src.app.core.security.password import verify_password

        result = await verify_password("anything", 12345)  # type: ignore[arg-type]
        assert result is False


# =============================================================================
# Microsoft tenant configuration and fail-closed identity verification
# =============================================================================


class TestMicrosoftTenantConfiguration:
    def test_domain_form_tenant_id_is_rejected(self):
        """A domain is not a tenant id: pinning would compare two id forms."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        with pytest.raises(ValueError, match="GUID"):
            MicrosoftProvider(
                "cid",
                "sec",
                "http://localhost/cb",
                tenant_id="contoso.onmicrosoft.com",
            )

    def test_guid_tenant_id_is_normalised(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider(
            "cid",
            "sec",
            "http://localhost/cb",
            tenant_id="  AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE ",
        )
        assert p.tenant_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert p._issuer_is_allowed(
            "https://login.microsoftonline.com/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/v2.0"
        )

    @pytest.mark.asyncio
    async def test_unverifiable_id_token_fails_closed(self):
        """A verification failure must not degrade to 'unknown = trusted'."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        with (
            patch.object(
                p, "_http_get", AsyncMock(return_value={"sub": "s", "email": "x@y.com"})
            ),
            patch.object(p, "_verify_id_token", AsyncMock(side_effect=ValueError("bad issuer"))),
        ):
            info = await p.get_user_info("tok", id_token="jwt")

        assert info.email_verified is False

    @pytest.mark.asyncio
    async def test_email_falls_back_to_the_id_token_preferred_username(self):
        """OIDC UserInfo has no userPrincipalName; the id_token still has it."""
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        with (
            patch.object(p, "_http_get", AsyncMock(return_value={"sub": "s"})),
            patch.object(
                p,
                "_verify_id_token",
                AsyncMock(return_value={"preferred_username": "work@contoso.com"}),
            ),
        ):
            info = await p.get_user_info("tok", id_token="jwt")

        assert info.email == "work@contoso.com"
        assert info.email_verified is None

    @pytest.mark.asyncio
    async def test_email_falls_back_to_the_upn_claim(self):
        from src.app.modules.auth.providers.microsoft import MicrosoftProvider

        p = MicrosoftProvider("cid", "sec", "http://localhost/cb")
        with (
            patch.object(p, "_http_get", AsyncMock(return_value={"sub": "s"})),
            patch.object(
                p, "_verify_id_token", AsyncMock(return_value={"upn": "work@contoso.com"})
            ),
        ):
            info = await p.get_user_info("tok", id_token="jwt")

        assert info.email == "work@contoso.com"
