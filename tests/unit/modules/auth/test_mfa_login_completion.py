"""B1 regressions for the real MFA completion workflow, with mocked I/O only."""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.auth.schemas import LoginMFAResponse, MFACompleteLoginRequest, MFAVerifyRequest
from src.app.modules.auth.services import mfa
from src.app.modules.auth.services.mfa import MFAService
from src.app.modules.auth.services.tokens import IssuedSession
from src.app.shared.exceptions import RateLimitExceededError, UnauthorizedError, ValidationError


@pytest.fixture
def completion() -> Iterator[SimpleNamespace]:
    """Keep orchestration real; isolate stores, verification, and issuance."""
    service = MFAService()
    pending = {"scope": "mfa_pending", "user_id": 12345, "username": "alice", "ip_address": "192.0.2.1"}
    store = AsyncMock()
    store.peek.return_value = pending
    store.remaining_ttl.return_value = 300
    store.consume.return_value = pending
    user = {"id": 12345, "username": "alice", "is_active": True, "is_deleted": False}
    users = Mock(get_user_auth_info=AsyncMock(return_value=user))
    issued = IssuedSession(access_token="access", refresh_token="refresh")
    with (
        patch.object(mfa, "check_security_budget", new_callable=AsyncMock) as budget,
        patch.object(mfa, "get_redis", return_value=Mock()) as redis,
        patch.object(mfa, "get_client_ip", return_value="192.0.2.1") as ip,
        patch.object(mfa, "get_mfa_pending_token_manager", return_value=store) as manager,
        patch(
            "src.app.modules.users.crud.crud_users.get_user_with_tier_eager",
            new=users.get_user_auth_info,
        ) as user_service,
        patch.object(service, "verify_totp_token", new_callable=AsyncMock, return_value={"valid": True}) as verify,
        patch.object(mfa.TokenIssuer, "issue_session", new_callable=AsyncMock, return_value=issued) as issue,
        patch.object(mfa.settings.auth, "MFA_PENDING_EXPIRY_SECONDS", 300),
        patch.object(mfa.settings.auth, "MFA_ENFORCE_IP_BINDING", True),
        patch.object(mfa.settings.auth, "MFA_IP_BINDING_GRACE_SECONDS", 30),
    ):
        budget.return_value = SimpleNamespace(is_limited=False, period=60, retry_after=0)
        order = Mock()
        for name, dependency in [
            ("budget", budget), ("redis", redis), ("peek", store.peek), ("ttl", store.remaining_ttl),
            ("verify", verify), ("consume", store.consume), ("lookup", users.get_user_auth_info), ("issue", issue),
        ]:
            order.attach_mock(dependency, name)
        yield SimpleNamespace(
            service=service, db=AsyncMock(spec=AsyncSession),
            request=MFACompleteLoginRequest(mfa_token="a" * 32, totp_token="123456"),
            http=Request({"type": "http", "headers": [], "client": ("192.0.2.1", 1234)}), response=Response(),
            pending=pending, store=store, users=users, user=user, issued=issued, budget=budget, redis=redis,
            ip=ip, manager=manager, user_service=user_service, verify=verify, issue=issue, order=order,
        )


async def _complete(env: SimpleNamespace) -> IssuedSession:
    return await env.service.complete_login(env.request, env.db, fastapi_request=env.http, response=env.response)


@pytest.mark.asyncio
class TestCompleteLogin:
    @pytest.mark.parametrize("retry_after,expected", [(17, 17), (0, 60)])
    async def test_budget_denial_precedes_redis(
        self, completion: SimpleNamespace, retry_after: int, expected: int
    ) -> None:
        env = completion
        env.budget.return_value = SimpleNamespace(is_limited=True, period=60, retry_after=retry_after)
        with pytest.raises(RateLimitExceededError) as exc:
            await _complete(env)
        assert exc.value.retry_after == expected
        env.budget.assert_awaited_once_with("mfa_verify", env.request.mfa_token)
        env.redis.assert_not_called()
        env.verify.assert_not_awaited()
        env.issue.assert_not_awaited()

    async def test_per_account_budget_trips_across_fresh_tokens(self, completion: SimpleNamespace) -> None:
        """P1: the per-token mfa_verify leg rotates with each pending token,
        so the SECOND leg (keyed on the bound user_id from the peek) is what
        accumulates brute-force guesses across DIFFERENT tokens for one
        account. When it trips, the attempt dies before any factor verify."""
        env = completion
        env.budget.side_effect = [
            SimpleNamespace(is_limited=False, period=60, retry_after=0),
            SimpleNamespace(is_limited=True, period=60, retry_after=17),
        ]
        env.request = MFACompleteLoginRequest(mfa_token="b" * 32, totp_token="654321")
        with pytest.raises(RateLimitExceededError) as exc:
            await _complete(env)
        assert exc.value.retry_after == 17
        # Both legs charged: fresh token (rotated) + stable account key.
        assert env.budget.await_args_list[0].args == ("mfa_verify", "b" * 32)
        assert env.budget.await_args_list[1].args == ("mfa_verify_global", "user:12345")
        env.verify.assert_not_awaited()
        env.store.consume.assert_not_awaited()
        env.issue.assert_not_awaited()

    async def test_missing_redis_denies_before_store_access(self, completion: SimpleNamespace) -> None:
        env = completion
        env.redis.return_value = None
        with pytest.raises(UnauthorizedError, match="service unavailable"):
            await _complete(env)
        assert [c[0] for c in env.order.mock_calls] == ["budget", "redis"]
        env.manager.assert_not_called()
        env.verify.assert_not_awaited()
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("pending", [None, {}, {"scope": "other"}, {"user_id": 12345, "username": "alice"}])
    async def test_peek_requires_pending_scope(self, completion: SimpleNamespace, pending: dict | None) -> None:
        env = completion
        env.store.peek.return_value = pending
        with pytest.raises(UnauthorizedError, match="Invalid or expired"):
            await _complete(env)
        env.manager.assert_called_once_with(env.redis.return_value)
        env.store.peek.assert_awaited_once_with(env.request.mfa_token)
        env.store.remaining_ttl.assert_not_awaited()
        env.verify.assert_not_awaited()
        env.store.consume.assert_not_awaited()
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("field,value", [("user_id", None), ("user_id", 0), ("username", None), ("username", "")])
    async def test_pending_identity_required(self, completion: SimpleNamespace, field: str, value: object) -> None:
        env = completion
        env.pending[field] = value
        with pytest.raises(UnauthorizedError, match="Invalid MFA pending token"):
            await _complete(env)
        env.verify.assert_not_awaited()
        env.store.consume.assert_not_awaited()
        env.users.get_user_auth_info.assert_not_awaited()
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("ttl,allowed", [
        (300, True), (299, True), (270, True), (269, False), (301, False), (0, False), (-1, False), (-2, False),
    ])
    async def test_ip_grace_boundaries(self, completion: SimpleNamespace, ttl: int, allowed: bool) -> None:
        env = completion
        env.ip.return_value = "192.0.2.2"
        env.store.remaining_ttl.return_value = ttl
        if allowed:
            assert await _complete(env) is env.issued
        else:
            message = "Invalid or expired" if ttl < 0 else "IP address changed"
            with pytest.raises(UnauthorizedError, match=message):
                await _complete(env)
            env.verify.assert_not_awaited()
            env.store.consume.assert_not_awaited()
            env.issue.assert_not_awaited()
        env.store.remaining_ttl.assert_awaited_once_with(env.request.mfa_token)

    async def test_zero_grace_rejects_even_fresh_ip_change(self, completion: SimpleNamespace) -> None:
        env = completion
        env.ip.return_value = "192.0.2.2"
        with patch.object(mfa.settings.auth, "MFA_IP_BINDING_GRACE_SECONDS", 0):
            with pytest.raises(UnauthorizedError, match="IP address changed"):
                await _complete(env)
        env.verify.assert_not_awaited()
        env.store.consume.assert_not_awaited()
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("stored_ip,enforce", [("192.0.2.1", True), (None, True), ("192.0.2.2", False)])
    async def test_ip_binding_optional_or_matching(
        self, completion: SimpleNamespace, stored_ip: str | None, enforce: bool,
    ) -> None:
        env = completion
        env.pending["ip_address"] = stored_ip
        env.store.remaining_ttl.return_value = 100  # Beyond the grace period.
        with patch.object(mfa.settings.auth, "MFA_ENFORCE_IP_BINDING", enforce):
            assert await _complete(env) is env.issued

    @pytest.mark.parametrize("failure", [None, UnauthorizedError("Invalid MFA token"), ValidationError("already used")])
    async def test_failed_verification_preserves_pending(
        self, completion: SimpleNamespace, failure: Exception | None,
    ) -> None:
        env = completion
        env.verify.return_value = {"valid": False}
        env.verify.side_effect = failure
        with pytest.raises(type(failure) if failure else UnauthorizedError):
            await _complete(env)
        env.verify.assert_awaited_once()
        env.store.consume.assert_not_awaited()
        env.users.get_user_auth_info.assert_not_awaited()
        env.issue.assert_not_awaited()

    async def test_consume_miss_prevents_lookup_and_issuance(self, completion: SimpleNamespace) -> None:
        env = completion
        env.store.consume.return_value = None
        with pytest.raises(UnauthorizedError, match="Invalid or expired"):
            await _complete(env)
        env.verify.assert_awaited_once()
        env.store.consume.assert_awaited_once_with(env.request.mfa_token)
        assert [c[0] for c in env.order.mock_calls][-2:] == ["verify", "consume"]
        env.users.get_user_auth_info.assert_not_awaited()
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize(
        "user", [None, {"is_active": False}, {"is_active": True, "is_deleted": True}, {"id": 12345}]
    )
    async def test_inactive_user_denies_issuance(self, completion: SimpleNamespace, user: dict | None) -> None:
        env = completion
        env.users.get_user_auth_info.return_value = user
        with pytest.raises(UnauthorizedError, match="no longer active"):
            await _complete(env)
        env.store.consume.assert_awaited_once_with(env.request.mfa_token)
        env.users.get_user_auth_info.assert_awaited_once_with(db=env.db, id=12345)
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("codes", [{"totp_token": "123456"}, {"backup_code": "BC-ABCDE-12345"}])
    async def test_success_orders_verification_consumption_and_issuance(
        self, completion: SimpleNamespace, codes: dict,
    ) -> None:
        env = completion
        env.request = MFACompleteLoginRequest(mfa_token="a" * 32, **codes)
        assert await _complete(env) is env.issued
        assert [c[0] for c in env.order.mock_calls] == [
            "budget", "redis", "peek", "ttl", "budget", "verify", "consume", "lookup", "issue",
        ]
        assert env.budget.await_count == 2
        assert env.budget.await_args_list[0].args == ("mfa_verify", env.request.mfa_token)
        assert env.budget.await_args_list[1].args[1] == "user:12345"
        env.ip.assert_called_once_with(env.http)
        env.verify.assert_awaited_once_with(
            user_id=12345, request=MFAVerifyRequest(**codes), db=env.db, prevent_replay=True,
            redis_client=env.redis.return_value,
        )
        env.store.consume.assert_awaited_once_with(env.request.mfa_token)
        env.users.get_user_auth_info.assert_awaited_once_with(db=env.db, id=12345)
        env.issue.assert_awaited_once_with(
            env.user, channel="mfa", request=env.http, response=env.response, subject="alice", verify=False,
        )

    async def test_username_change_rejects_bound_account(self, completion: SimpleNamespace) -> None:
        env = completion
        env.user["username"] = "renamed"
        with pytest.raises(UnauthorizedError, match="Invalid MFA pending token"):
            await _complete(env)
        env.users.get_user_auth_info.assert_awaited_once_with(db=env.db, id=12345)
        env.issue.assert_not_awaited()

    @pytest.mark.parametrize("ttl", [-1, -2])
    async def test_negative_ttl_rejects_matching_ip_before_factor(self, completion: SimpleNamespace, ttl: int) -> None:
        env = completion
        env.store.remaining_ttl.return_value = ttl
        with pytest.raises(UnauthorizedError, match="Invalid or expired"):
            await _complete(env)
        env.verify.assert_not_awaited()
        env.store.consume.assert_not_awaited()
        env.issue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["password", "passkey", "magic_link", "sso"])
async def test_login_doors_bind_mfa_to_client_ip(door: str) -> None:
    from contextlib import ExitStack

    from src.app.modules.auth.service import AuthService
    from src.app.modules.auth.services.magic_link import MagicLinkService
    from src.app.modules.auth.services.passkey import PasskeyService
    from src.app.modules.auth.services.sso import SSOService

    db, redis = AsyncMock(), AsyncMock()
    http = Request({"type": "http", "headers": [], "client": ("192.0.2.1", 1234)})
    user = {"id": 12345, "username": "alice", "email": "alice@example.com", "is_active": True, "tenant_id": None}
    challenge = LoginMFAResponse(mfa_token="a" * 32, expires_in=300, message="MFA required")
    with ExitStack() as stack:
        gate = stack.enter_context(patch.object(MFAService, "challenge_if_required", return_value=challenge))
        stack.enter_context(patch("src.app.modules.auth.service.audit", new_callable=AsyncMock))
        if door == "password":
            lockout = AsyncMock()
            lockout.is_locked.return_value = False
            service = AuthService(lockout_policy=lockout)
            stack.enter_context(patch.object(service, "authenticate_user", return_value=user))
            await service.login("alice", "password", request=http, db=db, redis=redis)
        elif door == "passkey":
            service = PasskeyService()
            stack.enter_context(patch.object(service, "_get_stored_auth_challenge", return_value=b"challenge"))
            stack.enter_context(patch.object(service, "_find_credential_by_id", return_value=Mock(counter=0)))
            stack.enter_context(
                patch.object(service, "_verify_authentication_response", return_value=Mock(new_sign_count=1))
            )
            stack.enter_context(patch.object(service, "_update_credential_counter", new_callable=AsyncMock))
            stack.enter_context(patch.object(service, "_get_user_for_credential", return_value=user))
            await service.verify_authentication(db, redis, {"id": "credential"}, "challenge-id", request=http)
        elif door == "magic_link":
            service = MagicLinkService(db, redis)
            manager = AsyncMock()
            manager.consume.return_value = {"email": user["email"]}
            stack.enter_context(patch.object(service, "_get_magic_link_manager", return_value=manager))
            stack.enter_context(patch("src.app.modules.auth.crud.get_magic_link_by_token", return_value=None))
            stack.enter_context(patch.object(service, "_get_user_by_email", return_value=user))
            await service.verify_magic_link("a" * 43, "192.0.2.1", request=http)
        else:
            from src.app.modules.auth.services import sso

            service = SSOService()
            manager = AsyncMock()
            manager.consume.return_value = {"provider": "google", "redirect_uri": "https://example.com/callback"}
            provider = AsyncMock()
            provider.exchange_code_for_token.return_value = {"access_token": "provider-token"}
            provider.get_user_info.return_value = Mock(provider_user_id="provider-user", email=user["email"])
            users = Mock(get=AsyncMock(return_value=user), get_user_auth_info=AsyncMock(return_value=user))
            stack.enter_context(patch("src.app.core.redis_provider.get_redis", return_value=redis))
            stack.enter_context(patch.object(sso, "get_sso_state_manager", return_value=manager))
            stack.enter_context(patch.object(service, "_get_provider_instance", return_value=provider))
            stack.enter_context(patch("src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings"))
            stack.enter_context(patch.object(sso, "get_current_tenant", return_value=Mock(id=1)))
            stack.enter_context(patch.object(sso, "UserService", return_value=users))
            connections = stack.enter_context(patch.object(sso, "crud_user_sso_connections"))
            connections.get_by_provider_user_id = AsyncMock(return_value=Mock(user_id=12345, id=1))
            connections.update = AsyncMock()
            await service.process_callback(db, http, "google", "code", "state")
        gate.assert_awaited_once()
        assert gate.await_args is not None
        assert gate.await_args.kwargs["ip_address"] == "192.0.2.1"
        assert gate.await_args.kwargs["redis"] is redis


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("ip_address", [None, "192.0.2.1"])
async def test_challenge_if_required_returns_optional_typed_response(enabled: bool, ip_address: str | None) -> None:
    service = MFAService()
    db = AsyncMock(spec=AsyncSession)
    user = {"id": 12345, "username": "alice"}
    redis = Mock()
    challenge = {"mfa_required": True, "mfa_token": "a" * 32, "expires_in": 300, "message": "MFA verification required"}
    with (
        patch.object(service, "is_mfa_enabled", new_callable=AsyncMock, return_value=enabled) as check,
        patch.object(mfa.TokenIssuer, "issue_mfa_pending", new_callable=AsyncMock, return_value=challenge) as issue,
    ):
        result = await service.challenge_if_required(user, db, redis=redis, ip_address=ip_address)
    check.assert_awaited_once_with(user["id"], db)
    if enabled:
        assert isinstance(result, LoginMFAResponse)
        assert result.model_dump() == challenge
        issue.assert_awaited_once_with(user, redis=redis, ip_address=ip_address)
    else:
        assert result is None
        issue.assert_not_awaited()
