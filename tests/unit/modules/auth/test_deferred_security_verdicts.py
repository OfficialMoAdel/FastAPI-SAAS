"""sj09.2: tests closing the gaps found in the seven-file security audit.

The audit record lives in ``docs/security/deferred-file-security-audit.md``.
This file closes the coverage gaps it recorded — properties that were **verified
clean by reading** but had **no test** anywhere in the suite:

- passkey: a registration challenge minted for user A is refused for user B
  (``services/passkey.py:173``). Every existing ``verify_registration`` test
  mocks the stored challenge with the *same* user, so the binding was
  unverified.
- billing: the **tenant** half of the ``or`` on ``routers/checkouts.py:124``.
  The existing suite parametres only the user mismatch, so the second operand
  of a payment-route ownership gate was unverified.
- sso settings: the provider-settings cache entry must never carry the OAuth
  client secret. ``get_settings_by_provider`` writes through a payload built at
  ``services/sso_settings.py:110-128`` and nothing asserted its contents.

Each is a security property, not a restatement of the implementation, and each
has a counter-test so that "always refuse" and "always allow" both fail.

Audit Finding 1 (a pending MFA token can burn backup codes it will never
redeem) is deliberately **untested here**: it is a defect, not a clean
property, and ``sj09.2`` forbids fixing it as a side effect of the audit.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from tests.helpers.auth import build_test_auth_context

OWNER_TENANT = UUID("22222222-2222-4222-8222-222222222222")
OTHER_TENANT = UUID("33333333-3333-4333-8333-333333333333")


class TestRegistrationChallengeIsBoundToIssuer:
    """A registration challenge is a capability, and a capability has one owner.

    ``verify_registration`` takes ``registration_id`` from the request body. If
    the stored challenge were not compared against the *caller's* id, any
    authenticated user who learned another user's ``registration_id`` could
    complete that user's ceremony and mint a credential on their account. The
    comparison at ``services/passkey.py:173`` is what prevents that, and no
    test reached it: every ``verify_registration`` test in
    ``test_passkey_service.py`` returns a stored challenge carrying
    ``user_id: 1`` for a caller also using ``id: 1``.
    """

    # A well-formed WebAuthn attestation envelope: verify_registration parses it
    # before the ownership check is even relevant to the crypto double.
    _CREDENTIAL = {
        "id": "mock-id",
        "rawId": "mock-raw-id",
        "type": "public-key",
        "response": {
            "clientDataJSON": "mock-client-data",
            "attestationObject": "mock-attestation",
            "transports": ["internal"],
        },
    }

    @staticmethod
    def _patched(service, stored, verify_ret=None):
        """Doubles for everything except the ownership comparison."""
        return (
            patch.object(service, "_get_stored_challenge", new=AsyncMock(return_value=stored)),
            patch.object(service, "_verify_with_webauthn", return_value=verify_ret),
            patch.object(service, "_store_credential", new=AsyncMock(
                return_value={"id": 1, "device_type": "Laptop", "created_at": None}
            )),
            patch(
                "src.app.core.security.encryption.get_encryption",
                return_value=MagicMock(encrypt_credential=lambda raw: b"enc"),
            ),
        )

    @pytest.mark.asyncio
    async def test_foreign_challenge_is_refused_before_the_ceremony(self) -> None:
        """The stolen capability must not even reach WebAuthn verification."""
        from src.app.modules.auth.services.passkey import PasskeyService
        from src.app.shared.exceptions import ValidationError

        service = PasskeyService()
        stored = {"challenge": b"c", "device_type": "Laptop", "user_id": 1}
        get_challenge, verify, store, _enc = self._patched(service, stored)

        with get_challenge, verify as verify_mock, store as store_mock:
            with pytest.raises(ValidationError, match="belongs to another user"):
                await service.verify_registration(
                    db=AsyncMock(),
                    user={"id": 2, "email": "b@e.com", "tenant_id": str(OWNER_TENANT)},
                    credential=self._CREDENTIAL,
                    registration_id="reg-1",
                    redis=AsyncMock(),
                )

            # Refused BEFORE the crypto runs and BEFORE anything is written,
            # so no partial credential can be created on the victim's account.
            verify_mock.assert_not_called()
            store_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_owner_completes_their_own_ceremony(self) -> None:
        """COUNTER-TEST: the binding rejects strangers, not the owner.

        Without this, a fix that simply always raised would satisfy the test
        above and break every registration.
        """
        from src.app.modules.auth.services.passkey import PasskeyService

        service = PasskeyService()
        stored = {"challenge": b"c", "device_type": "Laptop", "user_id": 1}
        verification = MagicMock(
            credential_id=b"cred",
            credential_public_key=b"pub",
            sign_count=0,
            aaguid=UUID(int=0),
        )
        get_challenge, verify, store, enc = self._patched(service, stored, verify_ret=verification)

        with get_challenge, verify, store as store_mock, enc:
            result = await service.verify_registration(
                db=AsyncMock(),
                user={"id": 1, "email": "a@e.com", "tenant_id": str(OWNER_TENANT)},
                credential=self._CREDENTIAL,
                registration_id="reg-1",
                redis=AsyncMock(),
            )

        assert result["id"] == 1
        store_mock.assert_awaited_once()
        # The credential is minted for the CALLER and stamped with their tenant.
        written = store_mock.await_args[0][1]
        assert written.user_id == 1
        assert written.tenant_id == OWNER_TENANT


class TestCheckoutCancelOwnership:
    """``cancel_current_subscription`` gates on ``user`` **and** ``tenant``.

    ``routers/checkouts.py:124`` reads
    ``subscription["user_id"] != current_user.id or
    str(subscription.get("tenant_id")) != str(current_user.tenant_id)``. The
    existing suite (``routers/test_checkouts.py:107``) exercises only the user
    half, so deleting the tenant operand — leaving a caller able to cancel a
    subscription row that names them but belongs to another tenant — passed
    every existing test. This is the payment path, and the bead counts an
    unverified ownership predicate there as a finding in its own right.
    """

    @staticmethod
    def _service(sub_user_id: int, sub_tenant_id: UUID) -> MagicMock:
        service = MagicMock()
        service.get_user_subscriptions = AsyncMock(return_value={"data": [{"id": 9, "status": "active"}]})
        service.get_subscription = AsyncMock(
            return_value={"id": 9, "user_id": sub_user_id, "tenant_id": sub_tenant_id}
        )
        service.cancel_subscription = AsyncMock(return_value={"id": 9, "status": "canceled"})
        return service

    @staticmethod
    def _patch(service: MagicMock):
        return patch(
            "src.app.modules.billing.routers.checkouts.get_user_subscription_service",
            return_value=service,
        )

    @pytest.mark.parametrize(
        "sub_user_id,sub_tenant_id",
        [
            pytest.param(2, OWNER_TENANT, id="user-mismatch"),
            pytest.param(1, OTHER_TENANT, id="tenant-mismatch-only"),
        ],
    )
    @pytest.mark.asyncio
    async def test_mismatch_forbids_cancellation(self, sub_user_id: int, sub_tenant_id: UUID) -> None:
        """Neither axis alone may be sufficient — each forbids on its own."""
        from src.app.api.deps.auth import AuthenticatedUser
        from src.app.modules.billing.routers.checkouts import cancel_current_subscription
        from src.app.shared.exceptions import ForbiddenError

        service = self._service(sub_user_id, sub_tenant_id)
        with self._patch(service):
            with pytest.raises(ForbiddenError, match="Not authorized to cancel"):
                await cancel_current_subscription(
                    current_user=AuthenticatedUser(id=1, email="a@e.com", tenant_id=OWNER_TENANT),
                    db=MagicMock(),
                    subscription_id=9,
                )

        # Refused BEFORE the write: no Stripe call, no row mutation.
        service.cancel_subscription.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_owner_in_the_right_tenant_still_cancels(self) -> None:
        """COUNTER-TEST: the owner in the right tenant still cancels.

        Without this, "always raise Forbidden" satisfies both negatives above.
        """
        from src.app.api.deps.auth import AuthenticatedUser
        from src.app.modules.billing.routers.checkouts import cancel_current_subscription

        service = self._service(1, OWNER_TENANT)
        with self._patch(service):
            result = await cancel_current_subscription(
                current_user=AuthenticatedUser(id=1, email="a@e.com", tenant_id=OWNER_TENANT),
                db=MagicMock(),
                subscription_id=9,
            )

        assert result["status"] == "canceled"
        service.cancel_subscription.assert_awaited_once_with(9)


class TestProviderSettingsCacheCarriesNoSecrets:
    """The provider-settings cache must never hold an OAuth client secret.

    ``get_settings_by_provider`` writes through to Redis after a miss so reads
    stay warm. What it writes is built field by field at
    ``services/sso_settings.py:110-128`` — and nothing asserted the payload.
    The cached value outlives the request and is shared across every caller of
    a globally unique ``provider``, so a secret in it would be a durable
    plaintext-at-rest leak in Redis that no code path would ever show again.
    """

    @staticmethod
    def _sso_row() -> dict:
        """A full DB row — the public read validates ``created_at``/``updated_at``."""
        from datetime import UTC, datetime

        return {
            "id": 7,
            "provider": "google",
            "client_id": b"ENCRYPTED_CID",
            "client_secret": b"ENCRYPTED_SECRET",
            "is_enabled": True,
            "additional_config": {"hd": "example.com"},
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        }

    @pytest.mark.asyncio
    async def test_cached_payload_excludes_both_secrets(self) -> None:
        """A cache MISS must repopulate with the public projection only.

        The payload is what survives the request in Redis, readable by every
        later reader of that ``provider``.
        """
        from src.app.core.cache.client import cache_client
        from src.app.modules.auth.services.sso_settings import SSOSettingsService

        captured: dict = {}

        async def _set_json(key, value, ttl=None):
            captured.update(key=key, value=value, ttl=ttl)
            return True

        with (
            patch.object(cache_client, "get_json", new=AsyncMock(return_value=None)),
            patch.object(cache_client, "set_json", new=_set_json),
            patch(
                "src.app.modules.auth.services.sso_settings.crud_sso_settings.get",
                new=AsyncMock(return_value=self._sso_row()),
            ),
        ):
            public = await SSOSettingsService().get_settings_by_provider(AsyncMock(), "google")

        # COUNTER-TEST: the public read itself carries no secret either, so a
        # write-through that simply serialised the response would still pass
        # the cache assertions only if the response is secret-free — which is
        # the invariant, stated from the consumer's side.
        assert not hasattr(public, "client_secret")
        assert not hasattr(public, "client_id")

        value = captured["value"]
        assert set(value) == {"id", "provider", "is_enabled", "additional_config"}, (
            "the cached payload must be exactly the public projection"
        )
        blob = repr(value)
        assert "ENCRYPTED_CID" not in blob, "client_id leaked into the provider cache"
        assert "ENCRYPTED_SECRET" not in blob, "client_secret leaked into the provider cache"

    def test_cache_key_needs_no_tenant_component(self) -> None:
        """The key is provider-only, and that is correct — the table is global.

        ``SSOSettings`` carries no ``tenant_id`` column and its ``provider`` is
        globally ``unique`` (``auth/models.py:165-179``), so a tenant component
        would never discriminate. A future change that adds ``tenant_id`` to
        the model must come back here.
        """
        from src.app.modules.auth.models import SSOSettings

        assert "tenant_id" not in SSOSettings.__table__.columns
        assert SSOSettings.__table__.columns["provider"].unique is True


class TestSSOSettingsReadRouteIsSuperuserOnly:
    """``GET /api/v1/ssos/{provider}`` is the ONLY HTTP door to the decryption seam.

    Audit Finding 2. ``SSOSettingsService.get_decrypted_settings``
    (``auth/services/sso_settings.py:134``) decrypts ``client_id`` and
    ``client_secret`` back to plaintext. Its sole HTTP caller is
    ``get_sso_settings`` (``auth/routers/sso_settings.py:113-129``); the other
    caller, ``services/sso.py:155,271``, is the login redirect flow and
    returns no settings to the caller. That route's ONLY guard is
    ``dependencies=[Depends(get_current_superuser)]`` on the decorator
    (``:116``) — nothing in the handler re-checks.

    ``SSOSettings`` is a host-level table with no ``tenant_id``
    (``auth/models.py:165-179``), so these are credentials for every tenant
    at once: the decorator gate IS the whole containment story, and it is the
    kind of thing that a later refactor drops silently.

    NOTE on what this test does and does not claim. The route declares
    ``response_model=SSOSettingsRead``, which carries no ``client_id``/
    ``client_secret`` field (``auth/schemas.py:254-257``; the secret-bearing
    sibling ``SSOSettingsReadWithSecrets`` is NOT used here). So today the
    response body would not have carried the plaintext even if the guard
    were absent — ``test_secrets_never_reach_the_response_body`` pins that
    second line of defence so it cannot be traded away for the first. What
    the guard itself buys is that the *decryption* never runs for a
    non-superuser, and that the request is refused outright rather than
    served a redacted 200. Both are asserted here, and the stub returns the
    full plaintext payload so a 403 can only come from the guard — not from
    response validation rejecting a bad mock.
    """

    @staticmethod
    def _app():
        """A mini-app holding the REAL router — real decorator, real guard.

        Deliberately not ``src.app.main``: this file must not depend on the
        whole composition root to prove one decorator is still wired. The
        exception handlers are registered so an ``UnauthorizedError``/
        ``ForbiddenError`` from the real denial table becomes its real status
        code rather than a 500 that "refused" could not be told apart from
        "broke".
        """
        from fastapi import FastAPI

        from src.app.core.errors import setup_exception_handlers
        from src.app.modules.auth.routers.sso_settings import router as sso_router

        app = FastAPI()
        setup_exception_handlers(app)
        app.include_router(sso_router, prefix="/api/v1")
        return app

    @staticmethod
    def _user(*, is_superuser: bool, tenant_id: UUID) -> "object":
        from src.app.api.deps.auth import AuthenticatedUser

        return AuthenticatedUser(
            id=1,
            email="a@e.com",
            is_superuser=is_superuser,
            is_active=True,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _tenant_ctx(tenant_id: UUID):
        from src.app.core.tenancy.context import TenantContext

        return TenantContext(
            tenant_id=tenant_id,
            tenant_slug="acme",
            tenant_name="acme",
            is_active=True,
        )

    @staticmethod
    def _seam():
        """Spy on the decryption call, returning the full PLAINTEXT payload.

        If the guard regresses, the route proceeds and the body validates —
        because the payload is exactly what the real service returns — so the
        only thing that can turn the test red is the missing refusal.
        """
        from datetime import UTC, datetime

        seam = AsyncMock(
            return_value={
                "id": 1,
                "provider": "google",
                "client_id": "PLAINTEXT_CLIENT_ID",
                "client_secret": "PLAINTEXT_CLIENT_SECRET",
                "additional_config": {},
                "is_enabled": True,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )
        return seam

    def _run(self, *, is_superuser: bool, seam):
        """Issue the real request through the real dependency graph."""
        from fastapi.testclient import TestClient

        from src.app.api.deps.auth import get_current_user
        from src.app.core.db.database import async_get_db
        from src.app.core.principal import get_auth_context
        from src.app.core.tenancy.lifecycle import bound_tenant

        app = self._app()
        app.dependency_overrides[async_get_db] = lambda: MagicMock()
        # Only the token VERIFICATION is stubbed. ``get_current_superuser``
        # itself runs production code, so the assertions below exercise the
        # real guard rather than a re-implementation of it.
        user = self._user(is_superuser=is_superuser, tenant_id=OWNER_TENANT)
        app.dependency_overrides[get_current_user] = lambda: user
        # The guard reads the tenant off the resolved principal
        # (``get_auth_context``), the same principal ``requires_permission``
        # reads — so the test must supply one, via the sanctioned override
        # seam, or the REAL ``get_auth_context`` runs here and 401s before the
        # guard is ever reached. The tenant is the one bound below, so the
        # guard sees the same binding the route does.
        app.dependency_overrides[get_auth_context] = lambda: build_test_auth_context(
            user, self._tenant_ctx(OWNER_TENANT)
        )

        # The real tenant binding, not a patched accessor: the route builds a
        # RequestContext (which resolves the tenant) and the guard reads the
        # same binding back, so both must see it. Patching only
        # ``superuser_tenant.get_current_tenant`` would satisfy the guard while
        # the route still 403s on "No tenant context available" — a refusal
        # that proves nothing about the superuser check.
        with bound_tenant(self._tenant_ctx(OWNER_TENANT)):
            # The handler imports the singleton INSIDE the function body
            # (``routers/sso_settings.py:124``), so the name to patch is the
            # one on the service module, not on the router module.
            with patch(
                "src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings",
                seam,
            ):
                return TestClient(app).get("/api/v1/ssos/google")

    def test_non_superuser_is_refused_before_the_decryption_seam(self) -> None:
        """A plain tenant member cannot make the server decrypt provider secrets.

        Both halves matter, and either alone is satisfiable by a broken test:

        1. 403 — the request is refused, not served a redacted 200.
        2. the seam is never awaited — the credentials were never decrypted.
           A status-only assertion would still pass if the handler decrypted
           first and the response model dropped the fields on the way out,
           which is exactly the shape a future "just add client_id to the
           response" change would take.
        """
        seam = self._seam()

        response = self._run(is_superuser=False, seam=seam)

        assert response.status_code == 403, (
            f"a non-superuser was served by the superuser-only SSO settings route: "
            f"{response.status_code} {response.text[:200]}"
        )
        seam.assert_not_awaited()

    def test_superuser_is_served_and_the_guard_is_not_just_always_refusing(self) -> None:
        """COUNTER-TEST: a superuser in their own tenant still gets the row.

        Without this, "always raise 403" satisfies the refusal above. This is
        also the assertion that gives the refusal teeth: the same URL, the
        same seam stub, one flag different.
        """
        seam = self._seam()

        response = self._run(is_superuser=True, seam=seam)

        assert response.status_code == 200, response.text
        assert response.json()["provider"] == "google"
        seam.assert_awaited_once()

    def test_secrets_never_reach_the_response_body(self) -> None:
        """The plaintext must not be serialized even for a superuser.

        This is the second line of defence the first test is standing on. The
        route declares ``response_model=SSOSettingsRead`` — no ``client_id``,
        no ``client_secret`` — while the handler returns a dict carrying
        both. If someone widens the response model (or drops it, so the raw
        dict is serialized) this fails on the body rather than shipping
        OAuth client secrets to a browser.
        """
        seam = self._seam()

        response = self._run(is_superuser=True, seam=seam)

        assert response.status_code == 200, response.text
        body = response.text
        assert "PLAINTEXT_CLIENT_SECRET" not in body, "client_secret reached the wire"
        assert "PLAINTEXT_CLIENT_ID" not in body, "client_id reached the wire"
        # And the fields the public projection is supposed to carry are there,
        # so this is a real response and not an empty/error body that trivially
        # contains no secrets.
        assert response.json()["is_enabled"] is True
