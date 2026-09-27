"""Step-up re-authentication tests for factor enrollment.

Run-3 finding ``auth.mfa.passkey_enrollment_without_reauthentication``: passkey
registration and MFA setup/enable authenticated with a bearer token only. A
stolen access token could enrol a durable credential that outlived the session
and the password change.

These tests pin the fixed behavior at the ROUTER seam (the service layer never
saw the password before this change) plus the reusable verification helper.

The pre-existing tests elsewhere in the suite assert the *service* contract,
where the step-up field is simply carried on the request model. These assert the
*route* refuses to reach the service without a valid password.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.helpers.auth import override_test_principal

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
TENANT_HEADERS = {"Authorization": "Bearer stolen-token", "X-Tenant-ID": "test-tenant"}

GOOD_PASSWORD = "correct-horse-battery-staple"
BAD_PASSWORD = "wrong-password-entirely"


@pytest_asyncio.fixture
async def client(get_test_app) -> AsyncClient:
    mock_redis = AsyncMock()
    mock_redis.evalsha = AsyncMock(return_value=1)
    mock_redis.script_load = AsyncMock(return_value="fake_sha")
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.pipeline = MagicMock(return_value=AsyncMock())

    with patch("src.app.core.utils.cache.client", mock_redis):
        transport = ASGITransport(app=get_test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _tenant():
    return MagicMock(id=MOCK_TENANT_ID, slug="test-tenant", is_active=True)


def _user():
    # A session-holder with NO password knowledge - the threat model.
    return {"id": 42, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}


def _patch_tenant(target):
    async def _get_tenant_by_slug(*args, **kwargs):
        return target

    return patch(
        "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
        new=_get_tenant_by_slug,
    )


def _patch_step_up(outcome: str):
    """Control the step-up verdict.

    ``"ok"``   -> correct password
    ``"bad"``  -> wrong password
    ``"none"`` -> step-up not satisfied
    """
    if outcome == "ok":
        return patch(
            "src.app.modules.auth.routers.passkey.verify_step_up_password",
            new=AsyncMock(return_value=None),
        )
    from src.app.shared.exceptions import UnauthorizedError

    if outcome == "none":
        err = UnauthorizedError("Step-up authentication failed.", reason="step_up_missing")
    else:
        err = UnauthorizedError("Step-up authentication failed.", reason="step_up_bad_password")
    return patch(
        "src.app.modules.auth.routers.passkey.verify_step_up_password",
        new=AsyncMock(side_effect=err),
    )


class TestVerifyStepUpPassword:
    """Unit-level behaviour of the shared verification helper."""

    @pytest.mark.asyncio
    async def test_correct_password_passes(self) -> None:
        from src.app.modules.auth.services.step_up import verify_step_up_password

        with (
            patch(
                "src.app.modules.auth.services.step_up.verify_password",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "src.app.modules.users.UserDirectory.active",
                new=AsyncMock(return_value=MagicMock(model_dump=lambda: {"hashed_password": "h"})),
            ),
        ):
            await verify_step_up_password(db=MagicMock(), user_id=1, password=GOOD_PASSWORD)

    @pytest.mark.asyncio
    async def test_wrong_password_raises(self) -> None:
        from src.app.modules.auth.services.step_up import verify_step_up_password
        from src.app.shared.exceptions import UnauthorizedError

        with (
            patch(
                "src.app.modules.auth.services.step_up.verify_password",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "src.app.modules.users.UserDirectory.active",
                new=AsyncMock(return_value=MagicMock(model_dump=lambda: {"hashed_password": "h"})),
            ),
            pytest.raises(UnauthorizedError),
        ):
            await verify_step_up_password(db=MagicMock(), user_id=1, password=BAD_PASSWORD)

    @pytest.mark.asyncio
    async def test_missing_password_raises(self) -> None:
        from src.app.modules.auth.services.step_up import verify_step_up_password
        from src.app.shared.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            await verify_step_up_password(db=MagicMock(), user_id=1, password=None)

    @pytest.mark.asyncio
    async def test_failures_are_indistinguishable(self) -> None:
        """No enumeration oracle: unknown subject and wrong password are identical."""
        from src.app.modules.auth.services.step_up import verify_step_up_password
        from src.app.shared.exceptions import UnauthorizedError

        messages = []
        reasons = []
        for active_return, password_ok in ((None, True), ({"hashed_password": "h"}, False)):
            with (
                patch(
                    "src.app.modules.auth.services.step_up.verify_password",
                    new=AsyncMock(return_value=password_ok),
                ),
                patch(
                    "src.app.modules.users.UserDirectory.active",
                    new=AsyncMock(
                        return_value=(
                            None
                            if active_return is None
                            else MagicMock(model_dump=lambda: active_return)
                        )
                    ),
                ),
                pytest.raises(UnauthorizedError) as exc,
            ):
                await verify_step_up_password(db=MagicMock(), user_id=1, password=GOOD_PASSWORD)
            messages.append(str(exc.value))
            reasons.append(getattr(exc.value, "reason", None))

        assert messages[0] == messages[1], "failure messages differ -> enumeration oracle"
        assert reasons[0] != reasons[1], "internal reason should still differ for logs"


class TestPasskeyRegistrationRequiresStepUp:
    """The core finding: a stolen session must not mint a durable credential."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route,payload", [
        ("/api/v1/auth/passkey/register", {"device_type": "platform", "password": BAD_PASSWORD}),
        ("/api/v1/auth/passkey/register/verify", {"credential": {}, "registration_id": "r", "password": BAD_PASSWORD}),
    ])
    async def test_wrong_password_does_not_reach_service(self, client, route, payload) -> None:
        """NEGATIVE: bad step-up stops before any credential is created."""
        tenant = _tenant()
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            _patch_step_up("bad"),
            patch(
                "src.app.modules.auth.routers.passkey.passkey_service.generate_registration_challenge",
                new=AsyncMock(),
            ) as gen,
            patch(
                "src.app.modules.auth.routers.passkey.passkey_service.verify_registration",
                new=AsyncMock(),
            ) as ver,
        ):
            response = await client.post(route, json=payload, headers=TENANT_HEADERS)

        assert response.status_code == 401
        gen.assert_not_awaited()
        ver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_password_field_is_rejected_by_schema(self, client) -> None:
        """NEGATIVE: the old request shape (no password) is now invalid."""
        tenant = _tenant()
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            _patch_step_up("ok"),
        ):
            response = await client.post(
                "/api/v1/auth/passkey/register",
                json={"device_type": "platform"},
                headers=TENANT_HEADERS,
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_legitimate_user_with_password_proceeds(self, client) -> None:
        """POSITIVE: a user who knows the password reaches the service."""
        tenant = _tenant()
        # The route returns PasskeyRegistrationOptionsResponse(publicKey=options,
        # ...), so `options` must be the real public-key mapping.
        public_key = {"challenge": "x", "rp": {"id": "test-tenant"}}
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            _patch_step_up("ok"),
            patch(
                "src.app.modules.auth.routers.passkey.passkey_service.generate_registration_challenge",
                new=AsyncMock(return_value=(public_key, "reg-1")),
            ) as gen,
        ):
            response = await client.post(
                "/api/v1/auth/passkey/register",
                json={"device_type": "platform", "password": GOOD_PASSWORD},
                headers=TENANT_HEADERS,
            )

        assert response.status_code == 200, response.json()
        gen.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_credential_deletion_requires_step_up(self, client) -> None:
        """NEGATIVE: removing a factor is also step-up protected."""
        tenant = _tenant()
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            _patch_step_up("bad"),
            patch(
                "src.app.modules.auth.routers.passkey.passkey_service.delete_credential",
                new=AsyncMock(),
            ) as delete,
        ):
            response = await client.request(
                "DELETE",
                "/api/v1/auth/passkey/credentials/1",
                json={"password": BAD_PASSWORD},
                headers=TENANT_HEADERS,
            )

        assert response.status_code == 401
        delete.assert_not_awaited()


class TestMFAEnrollmentRequiresStepUp:
    """MFA setup/enable must sit behind the same bar as disable."""

    @pytest.mark.asyncio
    async def test_setup_rejects_wrong_password(self, client) -> None:
        tenant = _tenant()
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            patch(
                "src.app.modules.auth.routers.mfa.verify_step_up_password",
                new=AsyncMock(side_effect=_unauthorized()),
            ),
            patch(
                "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "src.app.modules.auth.routers.mfa.get_mfa_service",
                new=lambda: MagicMock(),
            ),
        ):
            response = await client.post(
                "/api/v1/mfa/setup",
                json={"password": BAD_PASSWORD},
                headers=TENANT_HEADERS,
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_enable_requires_password_field(self, client) -> None:
        """NEGATIVE: the old enable body (no password) is now invalid."""
        tenant = _tenant()
        with (
            _patch_tenant(tenant),
            override_test_principal(_user(), tenant),
            patch(
                "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = await client.post(
                "/api/v1/mfa/enable",
                json={"setup_token": "tok", "totp_token": "123456"},
                headers=TENANT_HEADERS,
            )

        assert response.status_code == 422


def _unauthorized():
    from src.app.shared.exceptions import UnauthorizedError

    return UnauthorizedError("Step-up authentication failed.", reason="step_up_bad_password")


class TestRegistrationCeremonyUserVerification:
    """Enrollment ceremony must demand UV, not merely prefer it."""

    def test_registration_ceremony_requires_user_verification(self) -> None:
        """A regression test for the PREFERRED -> REQUIRED change."""
        from pathlib import Path

        source = (
            Path("src/app/modules/auth/services/passkey.py")
            .read_text(encoding="utf-8")
        )
        # The registration ceremony block must not fall back to PREFERRED.
        registration_block = source.split("generate_registration_challenge", 1)[-1]
        registration_block = registration_block.split("def ", 1)[0]
        assert "UserVerificationRequirement.PREFERRED" not in registration_block
        assert "UserVerificationRequirement.REQUIRED" in source
