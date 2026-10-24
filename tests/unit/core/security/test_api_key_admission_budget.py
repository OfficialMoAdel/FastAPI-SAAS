"""An API-key request must be metered on the API-key budget, not the anonymous one.

Bead FastAPI_saas-g4my, found by bead M-7 (``FastAPI_saas-o0fg``) at commit
93750647: ``RequestAdmissionService._get_rate_limit_config`` resolved identity
ONLY from the ``Authorization: Bearer`` payload, never from ``X-API-Key``, so an
API-key request reached ``_resolve_legacy_config`` with ``user_id is None`` and
was metered on ``settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT`` - 10 requests per
hour per source IP - and additionally spent the anonymous IP aggregate bucket.
A Pro-tier key owner gets 1000/hour with a Bearer token, so an API key was
throttled 100x below its tier.

M-7 measured it: 50 successful API-key requests produced ZERO
``gcra:*rate_limit:user:*`` buckets and ``x-ratelimit-limit: 10 period 3600``; the
same account with a Bearer token answered ``x-ratelimit-limit: 1000``.

The budget is keyed by SOURCE IP, not by the presented key: admission runs
before any API-key validation (``core/guard/ordering.py`` puts RateLimit ahead
of routing), so a presented key is UNVERIFIED at the edge and keying on it would
let a brute-force attempt mint an unlimited number of buckets.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

from src.app.core.config import settings
from src.app.core.security import rate_policy
from src.app.core.security.auto_ban import BanInfo
from src.app.core.security.request_admission import (
    AdmissionOutcome,
    RequestAdmissionService,
    _RateLimitContext,
)

_API_KEY_LIMIT = (1000, 3600)
_ANONYMOUS_LIMIT = (10, 3600)
_AGGREGATE_LIMIT = (300, 3600)
_IP = "10.0.0.1"
_API_KEY_HEADER = {"X-API-Key": "sk-m7-key"}


class _Run(NamedTuple):
    outcome: AdmissionOutcome
    consumed: list[tuple[str, int, int]]
    aggregate: AsyncMock
    auto_ban: AsyncMock
    gcra_consume: AsyncMock


def _request(headers: dict[str, str] | None = None, path: str = "/api/v1/users/me") -> MagicMock:
    """A request mock carrying the attributes admission actually reads.

    ``request.url.path`` must be a real string: a bare MagicMock makes
    ``request.url.path.rstrip("/").lower()`` a MagicMock, which
    ``is_path_exempt`` treats as exempt, and admission then returns at the
    passthrough without ever reaching the budget under test.

    ``state.client_ip`` is set explicitly because ``get_client_ip_state`` is
    ``getattr(state, "client_ip", default)`` - on a bare MagicMock that returns a
    truthy MagicMock, so the real ``get_client_ip(request)`` is never reached and
    the key would be built with a mock as the IP.
    """
    req = MagicMock()
    req.url.path = path
    req.method = "GET"
    req.headers = Headers(headers or {})
    req.state = MagicMock()
    req.state.client_ip = _IP
    req.state.ip_whitelisted = False
    req.client = SimpleNamespace(host=_IP, port=1234)
    return req


def _ctx(**overrides: object) -> _RateLimitContext:
    """A keyword-built context: the admission tests construct it by keyword."""
    fields: dict[str, object] = {
        "user_id": None,
        "tier_name": None,
        "tenant_id": None,
        "tier_id_raw": None,
        "token": None,
        "jti": None,
        "path": "users_me",
    }
    fields.update(overrides)
    return _RateLimitContext(**fields)  # type: ignore[arg-type]


async def _evaluate(
    request: MagicMock,
    *,
    auto_ban: bool = False,
    jwt_payload: dict[str, object] | None = None,
    limited: bool = False,
) -> _Run:
    """Drive ``evaluate`` once with every counter recorded instead of run.

    ``consume`` is the single meter both the per-path bucket and the anonymous
    aggregate go through, so recording its ``(key, limit, period)`` calls proves
    the budget AND the aggregate exemption in one assertion.

    ``AUTO_BAN_ENABLED`` and ``IP_WHITELIST_BYPASS_RATE_LIMIT`` must be explicit
    booleans: left as MagicMock attributes they are TRUTHY, and the whitelist
    branch then returns ``admit`` before the budget is ever resolved - a silent
    no-op that would make these tests lie.
    """
    consumed: list[tuple[str, int, int]] = []

    async def _record(key: str, limit: int, period: int, **_: object) -> tuple[bool, int, int, bool]:
        consumed.append((key, limit, period))
        return False, max(limit - 1, 0), period, False

    aggregate = AsyncMock(return_value=(False, 250, _AGGREGATE_LIMIT[1]))
    auto_ban_check = AsyncMock(return_value=(False, 0))
    gcra_consume = AsyncMock(
        return_value=((False, 999, _API_KEY_LIMIT[1], False) if not limited else (True, 0, _API_KEY_LIMIT[1], False))
    )

    with (
        patch("src.app.core.security.request_admission.settings") as settings_mock,
        patch("src.app.core.security.request_admission.get_cached_jwt_payload", return_value=jwt_payload),
        patch("src.app.core.security.request_admission.get_ban_info", AsyncMock(return_value=BanInfo(False))),
        patch("src.app.core.security.request_admission.consume", AsyncMock(side_effect=_record)),
        patch(
            "src.app.core.security.request_admission.check_ban_and_rate_limit",
            AsyncMock(return_value=(BanInfo(False), (limited, 0, _API_KEY_LIMIT[1], False), None)),
        ),
        patch(
            "src.app.core.security.request_admission.check_ban_blacklist_and_rate_limit",
            AsyncMock(return_value=(BanInfo(False), False, False, (False, 999, 3600, False), False, False)),
        ),
        patch("src.app.core.security.request_admission.check_and_auto_ban", auto_ban_check),
        patch("src.app.core.security.request_admission.uses_gcra", MagicMock(return_value=True)),
        patch.object(RequestAdmissionService, "_check_anonymous_aggregate", aggregate),
    ):
        settings_mock.security.AUTO_BAN_ENABLED = auto_ban
        settings_mock.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
        settings_mock.rate_limit.API_KEY_LIMIT = _API_KEY_LIMIT
        settings_mock.rate_limit.DEFAULT_ANONYMOUS_LIMIT = _ANONYMOUS_LIMIT
        settings_mock.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = _AGGREGATE_LIMIT
        outcome = await RequestAdmissionService().evaluate(request)

    return _Run(outcome, consumed, aggregate, auto_ban_check, gcra_consume)


class TestApiKeyBudgetIsItsOwnBucket:
    """The API-key budget, on the API-key key, resolved from the header alone."""

    @pytest.mark.asyncio
    async def test_api_key_caller_is_metered_on_the_api_key_budget(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER))

        assert run.outcome.action == "admit"
        assert run.consumed == [(f"rate_limit:api_key:ip:{_IP}:users_me", *_API_KEY_LIMIT)]

    @pytest.mark.asyncio
    async def test_api_key_caller_never_spends_the_anonymous_aggregate_ceiling(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER))

        run.aggregate.assert_not_awaited()
        assert rate_policy.build_anonymous_aggregate_key(_IP) not in [key for key, _, _ in run.consumed]

    @pytest.mark.asyncio
    async def test_api_key_header_lookup_is_case_insensitive(self) -> None:
        # Starlette's Headers.get is case-insensitive, and the auth seam in
        # api/deps/auth.py tests `"X-API-Key" in request.headers`. If admission
        # ever reads the header with a different spelling it would quietly meter
        # keyed traffic as anonymous again.
        run = await _evaluate(_request({"x-api-key": "sk-m7-key"}))

        assert run.consumed == [(f"rate_limit:api_key:ip:{_IP}:users_me", *_API_KEY_LIMIT)]

    def test_the_api_key_budget_is_not_the_anonymous_default(self) -> None:
        # Read from the real settings object, not a mock: a dedicated budget
        # equal to the anonymous default would be a rename, not a fix.
        api_key_limit = settings.rate_limit.API_KEY_LIMIT
        assert api_key_limit != settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT
        assert api_key_limit[0] > settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT[0]


class TestApiKeyKeyShape:
    """The key is its own namespace, and an absent IP still lands in one bucket."""

    def test_the_api_key_key_is_not_the_anonymous_key(self) -> None:
        assert rate_policy.build_api_key_rate_limit_key("users_me", _IP) != rate_policy.build_rate_limit_key(
            "users_me", user_id=None, anonymous_ip=_IP
        )

    def test_a_missing_ip_collapses_into_one_explicit_bucket(self) -> None:
        assert rate_policy.build_api_key_rate_limit_key("users_me", None) == "rate_limit:api_key:ip:unknown:users_me"
        assert (
            rate_policy.build_api_key_rate_limit_key("users_me", "unknown") == "rate_limit:api_key:ip:unknown:users_me"
        )


class TestApiKeyViolationsDoNotEarnAutoBan:
    """A violation is 429 + Retry-After only, exactly like the refresh budget.

    The budget is metered by source IP, so the address behind a key is often a
    shared NAT egress. Escalating that to an ``AUTO_BAN_DURATION`` outage would
    ban every other caller behind the same address for a limit they never spent.
    """

    @pytest.mark.asyncio
    async def test_a_violation_does_not_escalate_to_auto_ban(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), auto_ban=True, limited=True)

        run.auto_ban.assert_not_awaited()
        assert run.outcome.action == "limited"
        assert run.outcome.violation_count == 0
        assert run.outcome.verdict is not None
        assert "X-Banned" not in run.outcome.verdict.headers


class TestAnonymousAndRevokedTokenPathsAreUnchanged:
    """The fix must not move a bucket that was already correct."""

    @pytest.mark.asyncio
    async def test_a_browser_without_a_key_still_gets_the_anonymous_budget_and_ceiling(self) -> None:
        run = await _evaluate(_request())

        # The aggregate bucket is metered INSIDE _check_anonymous_aggregate, which
        # this test replaces with a spy, so the aggregate key is asserted on the
        # spy rather than on the recorded `consume` calls - asking for both would
        # demand the very implementation detail the spy removes.
        run.aggregate.assert_awaited_once_with(_IP)
        assert run.consumed == [(f"rate_limit:anonymous:ip:{_IP}:users_me", *_ANONYMOUS_LIMIT)]

    @pytest.mark.asyncio
    async def test_a_bearer_token_keeps_the_per_user_budget(self) -> None:
        run = await _evaluate(
            _request({"Authorization": "Bearer live-token"}),
            jwt_payload={"sub": "42", "tier_name": "pro", "tier_id": 2, "tenant_id": "t1", "jti": "j1"},
        )

        assert run.aggregate.assert_not_awaited() is None
        assert not any("api_key:ip" in key for key, _, _ in run.consumed)
        assert not any("anonymous:ip" in key for key, _, _ in run.consumed)

    @pytest.mark.asyncio
    async def test_a_revoked_token_presented_alongside_a_key_still_downgrades_to_anonymous(self) -> None:
        """S-17 stays authoritative: a REVOKED bearer credential is anonymous traffic.

        The API-key budget applies to a caller presenting NO bearer credential
        at all. A request that presents one and is found blacklisted is a
        revoked session, and it keeps the anonymous key and the aggregate ceiling
        S-17 pinned - otherwise revoking a token would RAISE the caller's budget
        instead of lowering it.
        """
        service = RequestAdmissionService()
        ctx = _ctx(user_id="42", token="revoked-token", api_key=True)
        with (
            patch("src.app.core.security.token_blacklist_cache.is_token_blacklisted", AsyncMock(return_value=True)),
            patch("src.app.core.security.request_admission.settings"),
        ):
            limit, period, key, downgraded = await service._resolve_legacy_config(_request(_API_KEY_HEADER), ctx)

        assert downgraded is True
        assert (limit, period) == _ANONYMOUS_LIMIT
        assert key == f"rate_limit:anonymous:ip:{_IP}:users_me"
