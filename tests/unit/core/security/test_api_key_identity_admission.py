"""Admission owns API-key identity: a RESOLVED key is metered on its owner's tier budget.

Bead ``FastAPI_saas-hfps`` (ADR-0023). ``FastAPI_saas-g4my`` gave an
``X-API-Key`` request its own named budget, ``API_KEY_LIMIT``, keyed by SOURCE
IP, because admission runs before any key is validated
(``core/guard/ordering.py`` puts RateLimit ahead of routing) so a presented key
is unverified at the edge. That is correct as far as it goes, and it is where
g4my left the problem: two integrations behind one NAT share one bucket, and
the owner's tier is ignored - an ``enterprise`` key (10000/hour) and a ``free``
key (10/hour) get the same 1000.

The information needed to fix this is already on the request: every API-key
request already reads the ``api_key:<hmac>`` cache entry that carries the owner.
So ADR-0023 resolves it ONCE, in admission, binds it on ``request.state``, and
the authentication path consumes it instead of re-probing.

What this file pins, in the order the decisions were made:

* the cache-key format and the grant rules have ONE owner in
  ``core/security/api_key_identity.py`` (the service re-exports the constants);
* a resolved identity with a known tier is metered on the owner's own bucket -
  the SAME key that owner's Bearer session spends, which is why the assertions
  compare against ``build_rate_limit_key`` rather than a hand-written string;
* an identity WITHOUT a tier does not reach the owner bucket. ``resolve_rate_limits``
  would answer ``DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)`` for a resolved user
  with no tier name, i.e. a 20x TIGHTENING presented as a feature. g4my's
  per-source-IP budget is the correct answer for that case;
* an UNVERIFIED key still gets g4my's per-source-IP budget;
* a resolved identity still does not earn an auto-ban, even though it now
  populates ``user_id`` - ``api_key_metered`` used to require ``user_id is
  None``, which is precisely the trap that would have re-enabled auto-ban for
  every caller behind a shared NAT;
* the response path consumes the resolution without re-probing the cache, and
  without skipping the tenant-liveness probe. That is criterion 3's unit-level
  half; the measured half is the Linux run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps
from src.app.core.security.api_key_identity import (
    API_KEY_CACHE_PREFIX,
    API_KEY_CACHE_TTL,
    ApiKeyIdentity,
    api_key_cache_key,
    read_cached_grant,
    resolve_api_key_identity,
)
from src.app.core.security.rate_policy import (
    build_api_key_rate_limit_key,
    build_rate_limit_key,
)
from src.app.core.security.request_admission import RequestAdmissionService
from src.app.core.security.tokens import token_digest_candidates

_IP = "10.0.0.1"
_TENANT_ID = "3f6b1c2a-0000-4000-8000-000000000001"
_OWNER_ID = 4242
_KEY_ID = 7
_PATH = "users_me"
_MEASURED_PATH = "/api/v1/users/me"
_API_KEY = "sk-hfps-key"
_API_KEY_HEADER = {"X-API-Key": _API_KEY}

# Pinned to production values AND asserted against the live settings below, so
# a future edit to the budget cannot quietly invalidate these expectations.
_API_KEY_LIMIT = (1000, 3600)
_ANONYMOUS_LIMIT = (10, 3600)
_AUTHENTICATED_LIMIT = (50, 3600)
_AGGREGATE_LIMIT = (300, 3600)
_ENTERPRISE_LIMIT = (10000, 3600)
_FUTURE = "2999-01-01T00:00:00+00:00"
_PAST = "2000-01-01T00:00:00+00:00"


def _owner_bucket() -> str:
    """The bucket this owner's Bearer session spends - the reference key."""
    return build_rate_limit_key(_PATH, user_id=str(_OWNER_ID), tenant_id=_TENANT_ID)


def _source_ip_bucket() -> str:
    """g4my's unverified-key bucket."""
    return build_api_key_rate_limit_key(_PATH, _IP)


def _identity(tier_name: str | None = "enterprise", tier_id: int | None = 9) -> ApiKeyIdentity:
    return ApiKeyIdentity(
        user_id=_OWNER_ID,
        tenant_id=_TENANT_ID,
        key_id=_KEY_ID,
        scopes=["read"],
        tier_id=tier_id,
        tier_name=tier_name,
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": _OWNER_ID,
        "tenant_id": _TENANT_ID,
        "scopes": ["read"],
        "key_id": _KEY_ID,
        "expires_at": None,
        "valid_from": None,
        "is_active": True,
        "tier_id": 9,
        "tier_name": "enterprise",
    }
    base.update(overrides)
    return base


def _request(headers: dict[str, str] | None = None, path: str = _MEASURED_PATH) -> MagicMock:
    """A request mock carrying the attributes admission actually reads.

    ``request.url.path`` must be a real string (a bare MagicMock is treated as an
    exempt path and admission returns before any budget is resolved), and
    ``state.client_ip`` must be set explicitly because
    ``get_client_ip_state`` is a ``getattr`` that returns a truthy MagicMock on a
    bare state object.
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


class _Run(NamedTuple):
    outcome: Any
    consumed: list[tuple[str, int, int]]
    aggregate: AsyncMock
    auto_ban: AsyncMock
    resolver: AsyncMock
    metered: list[tuple[str, int, int]]


async def _evaluate(
    request: MagicMock,
    *,
    identity: ApiKeyIdentity | None = None,
    auto_ban: bool = False,
    limited: bool = False,
    jwt_payload: dict[str, Any] | None = None,
) -> _Run:
    """Drive ``evaluate`` once with every counter recorded instead of run.

    ``consume`` is the one meter both the per-path bucket and the anonymous
    aggregate pass through, so recording its ``(key, limit, period)`` calls proves
    the budget and the aggregate exemption in a single assertion.

    ``settings.security.AUTO_BAN_ENABLED`` and ``IP_WHITELIST_BYPASS_RATE_LIMIT``
    must be explicit booleans: left as MagicMock attributes they are TRUTHY and
    the whitelist branch returns ``admit`` before the budget is ever resolved -
    a silent no-op that would make these tests lie.

    ``rate_policy.get_tier_policy_limits`` is stubbed to ``(None, None)`` - it is
    the per-path policy-table lookup, a cache read this unit does not own. The
    REAL ``resolve_rate_limits`` precedence then runs against the real
    ``TIER_RATE_LIMITS``, so "enterprise gets 10000" is the production mapping
    and not a number invented here.

    ``metered`` records ``check_ban_and_rate_limit``'s ``(client_ip, key,
    limit, period)``. With AUTO_BAN_ENABLED and GCRA both on, that composite
    call - not ``consume`` - is what receives the bucket, so an assertion about
    which bucket the limiter was handed has to look here. Recording both and
    asserting the right one per arm is what keeps the test honest: ``consumed``
    empty in this configuration is not "no limit was applied".
    """
    consumed: list[tuple[str, int, int]] = []
    metered: list[tuple[str, int, int]] = []

    async def _record(key: str, limit: int, period: int, **_: object) -> tuple[bool, int, int, bool]:
        consumed.append((key, limit, period))
        return False, max(limit - 1, 0), period, False

    async def _record_ban_and_rate_limit(
        client_ip: str, key: str, limit: int, period: int, **kwargs: Any
    ) -> tuple[Any, tuple[bool, int, int, bool], None]:
        # F-10: the composite now returns a third element (the folded
        # aggregate verdict). An API-key caller never folds the ceiling
        # (`api_key_metered` excludes it), so the third element is None.
        metered.append((key, limit, period))
        assert kwargs.get("aggregate_limit", 0) == 0, "an API-key caller must not fold the anonymous ceiling"
        return MagicMock(is_banned=False), (limited, 0, period, False), None

    aggregate = AsyncMock(return_value=(False, 250, _AGGREGATE_LIMIT[1]))
    auto_ban_check = AsyncMock(return_value=(False, 0))
    resolver = AsyncMock(return_value=identity)

    with (
        patch("src.app.core.security.request_admission.settings") as settings_mock,
        patch("src.app.core.security.request_admission.resolve_api_key_identity", resolver),
        patch("src.app.core.security.request_admission.get_cached_jwt_payload", return_value=jwt_payload),
        patch(
            "src.app.core.security.request_admission.get_ban_info", AsyncMock(return_value=MagicMock(is_banned=False))
        ),
        patch("src.app.core.security.request_admission.consume", AsyncMock(side_effect=_record)),
        patch(
            "src.app.core.security.request_admission.check_ban_and_rate_limit",
            AsyncMock(side_effect=_record_ban_and_rate_limit),
        ),
        patch(
            "src.app.core.security.request_admission.check_ban_blacklist_and_rate_limit",
            AsyncMock(return_value=(MagicMock(is_banned=False), False, False, (False, 999, 3600, False), False, False)),
        ),
        patch("src.app.core.security.request_admission.check_and_auto_ban", auto_ban_check),
        patch("src.app.core.security.request_admission.uses_gcra", MagicMock(return_value=True)),
        patch(
            "src.app.core.security.rate_policy.get_tier_policy_limits",
            AsyncMock(return_value=(None, None)),
        ),
        patch.object(RequestAdmissionService, "_check_anonymous_aggregate", aggregate),
    ):
        settings_mock.security.AUTO_BAN_ENABLED = auto_ban
        settings_mock.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
        settings_mock.rate_limit.API_KEY_LIMIT = _API_KEY_LIMIT
        settings_mock.rate_limit.DEFAULT_ANONYMOUS_LIMIT = _ANONYMOUS_LIMIT
        settings_mock.rate_limit.DEFAULT_AUTHENTICATED_LIMIT = _AUTHENTICATED_LIMIT
        settings_mock.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = _AGGREGATE_LIMIT
        settings_mock.rate_limit.TIER_RATE_LIMITS = dict(settings.rate_limit.TIER_RATE_LIMITS)
        outcome = await RequestAdmissionService().evaluate(request)

    return _Run(outcome, consumed, aggregate, auto_ban_check, resolver, metered)


class TestOneOwnerForTheCacheFormat:
    def test_the_cache_key_is_the_prefix_and_the_whole_digest(self) -> None:
        assert api_key_cache_key("deadbeef") == f"{API_KEY_CACHE_PREFIX}:deadbeef"

    def test_the_service_re_exports_the_core_constants(self) -> None:
        from src.app.modules.api_keys import service as api_key_service

        assert api_key_service.API_KEY_CACHE_PREFIX == API_KEY_CACHE_PREFIX
        assert api_key_service.API_KEY_CACHE_TTL == API_KEY_CACHE_TTL

    def test_the_ttl_is_the_one_the_module_documents(self) -> None:
        assert API_KEY_CACHE_TTL == 300
        assert API_KEY_CACHE_PREFIX == "api_key"

    @pytest.mark.asyncio
    async def test_both_digest_candidates_are_probed_current_first(self) -> None:
        current, legacy = token_digest_candidates(_API_KEY)
        seen: list[str] = []
        # A real cache entry as `create`/validate writes it: the tier fields are
        # part of the payload now (ADR-0023), which is what this case exists to
        # prove survives the probe.
        cached = json_dumps(
            {
                "user_id": 1,
                "tenant_id": "t",
                "scopes": [],
                "key_id": 2,
                "expires_at": None,
                "valid_from": None,
                "is_active": True,
                "tier_id": 3,
                "tier_name": "pro",
            },
            as_str=True,
        )

        async def _get(key: str) -> str | None:
            seen.append(key)
            if key == api_key_cache_key(legacy):
                return cached
            return None

        with patch("src.app.core.security.api_key_identity.cache_client") as client:
            client.get = AsyncMock(side_effect=_get)
            resolved = await resolve_api_key_identity(_API_KEY)

        assert seen == [api_key_cache_key(current), api_key_cache_key(legacy)]
        assert resolved is not None
        assert resolved.user_id == 1
        assert resolved.tier_name == "pro"


class TestCachedGrantRules:
    """One implementation of "is this cached payload still a grant?"."""

    def test_a_granted_payload_becomes_an_identity(self) -> None:
        grant = read_cached_grant(_payload())

        assert grant.refusal == ""
        assert grant.identity is not None
        assert grant.identity.user_id == _OWNER_ID
        assert grant.identity.key_id == _KEY_ID
        assert grant.identity.tenant_id == _TENANT_ID
        assert grant.identity.tier_name == "enterprise"

    def test_a_revoked_payload_is_refused_and_says_why(self) -> None:
        grant = read_cached_grant(_payload(is_active=False))

        assert grant.refusal == "revoked"
        assert grant.identity is None

    def test_an_expired_payload_is_refused(self) -> None:
        grant = read_cached_grant(_payload(expires_at=_PAST))

        assert grant.refusal == "expired"
        assert grant.identity is None

    def test_a_not_yet_valid_payload_is_refused(self) -> None:
        grant = read_cached_grant(_payload(valid_from=_FUTURE))

        assert grant.refusal == "not_yet_valid"
        assert grant.identity is None

    def test_a_payload_without_is_active_still_grants(self) -> None:
        """An entry written by a deploy older than the tombstone field.

        It keeps working; every such entry carries the TTL, so the exposure is
        bounded by one TTL from deploy. Making this refuse would lock out every
        key in flight at deploy time.
        """
        payload = _payload()
        del payload["is_active"]

        grant = read_cached_grant(payload)

        assert grant.refusal == ""
        assert grant.identity is not None

    def test_a_payload_without_a_tier_still_grants_but_carries_no_tier(self) -> None:
        """The tier is new; entries written before ADR-0023 do not have it.

        They must grant (the key is genuinely valid) and must NOT invent a tier,
        because admission meters an untiered identity on the per-source-IP budget
        rather than the 50/hour authenticated fallback.
        """
        payload = _payload()
        del payload["tier_id"]
        del payload["tier_name"]

        grant = read_cached_grant(payload)

        assert grant.refusal == ""
        assert grant.identity is not None
        assert grant.identity.tier_id is None
        assert grant.identity.tier_name is None


class TestOwnerTierMetering:
    @pytest.mark.asyncio
    async def test_a_resolved_key_is_metered_on_its_owner_tier_budget(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=_identity())

        assert run.outcome.action == "admit"
        # enterprise, not API_KEY_LIMIT: the LIMIT differs too, so this cannot
        # pass by key shape alone.
        assert run.consumed == [(_owner_bucket(), *_ENTERPRISE_LIMIT)]

    @pytest.mark.asyncio
    async def test_the_owner_bucket_is_the_one_a_bearer_session_spends(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=_identity())

        assert run.consumed[0][0] == _owner_bucket()
        assert not run.consumed[0][0].startswith("rate_limit:api_key:ip:")

    @pytest.mark.asyncio
    async def test_a_resolved_key_never_spends_the_anonymous_aggregate_ceiling(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=_identity())

        run.aggregate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_resolution_is_bound_on_request_state(self) -> None:
        from src.app.core.middleware_state import get_api_key_identity

        identity = _identity()
        request = _request(_API_KEY_HEADER)
        await _evaluate(request, identity=identity)

        assert get_api_key_identity(request.state) is identity

    @pytest.mark.asyncio
    async def test_the_resolution_runs_once_and_only_for_a_presented_key(self) -> None:
        await _evaluate(_request(_API_KEY_HEADER), identity=_identity())

        with patch("src.app.core.security.request_admission.resolve_api_key_identity") as resolver:
            await _evaluate(_request())
            resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_bearer_request_is_not_resolved_as_an_api_key(self) -> None:
        payload: dict[str, Any] = {"sub": "s4x001", "tier_name": "pro", "tenant_id": _TENANT_ID, "tier_id": 2}
        with patch("src.app.core.security.request_admission.resolve_api_key_identity") as resolver:
            run = await _evaluate(
                _request(headers={"Authorization": f"Bearer {payload['sub']}"}),
                identity=_identity(),
                jwt_payload=payload,
            )
            resolver.assert_not_awaited()

        assert run.consumed[0][0].startswith(f"tenant:{_TENANT_ID}:rate_limit:user:")


class TestAnIdentityWithoutATierKeepsG4mysBudget:
    """The anti-tightening arm.

    ``resolve_rate_limits`` answers ``DEFAULT_AUTHENTICATED_LIMIT`` for a
    resolved user with no tier name, so metering an untiered identity through
    the owner path would cut a 1000/hour integration to 50/hour. That is a
    regression dressed as a feature, and this is the case that forbids it.
    """

    @pytest.mark.asyncio
    async def test_an_untiered_identity_is_metered_on_the_per_source_ip_budget(self) -> None:
        run = await _evaluate(
            _request(_API_KEY_HEADER),
            identity=_identity(tier_name=None, tier_id=None),
        )

        assert run.consumed == [(_source_ip_bucket(), *_API_KEY_LIMIT)]

    @pytest.mark.asyncio
    async def test_the_authenticated_fallback_is_never_the_api_key_answer(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=_identity(tier_name=None, tier_id=None))

        assert _AUTHENTICATED_LIMIT not in [(limit, period) for _, limit, period in run.consumed]


class TestUnverifiedKeyBehaviour:
    """g4my's behaviour, unchanged. These pass before AND after: that is the point."""

    @pytest.mark.asyncio
    async def test_a_cache_miss_keeps_the_per_source_ip_budget(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=None)

        assert run.outcome.action == "admit"
        assert run.consumed == [(_source_ip_bucket(), *_API_KEY_LIMIT)]

    @pytest.mark.asyncio
    async def test_an_empty_header_value_is_treated_as_unverified(self) -> None:
        run = await _evaluate(_request({"X-API-Key": ""}), identity=_identity())

        assert run.consumed == [(_source_ip_bucket(), *_API_KEY_LIMIT)]


class TestApiKeyViolationsNeverEarnAutoBan:
    @pytest.mark.asyncio
    async def test_a_resolved_key_violation_does_not_earn_an_auto_ban(self) -> None:
        run = await _evaluate(
            _request(_API_KEY_HEADER),
            identity=_identity(),
            auto_ban=True,
            limited=True,
        )

        # All three, together: the bucket assertion is what proves `user_id` was
        # actually populated, which is exactly the state in which g4my's
        # `api_key_metered` flag used to go false and re-enable escalation.
        # Asserted on `metered`, not `consumed`: AUTO_BAN_ENABLED + GCRA routes
        # the bucket through the composite `check_ban_and_rate_limit`, and an
        # empty `consumed` here is the configuration, not a missing limit.
        assert run.metered == [(_owner_bucket(), *_ENTERPRISE_LIMIT)]
        assert run.outcome.action == "limited"
        assert run.outcome.violation_count == 0
        run.auto_ban.assert_not_awaited()
        assert "X-Banned" not in (run.outcome.verdict.headers if run.outcome.verdict else {})

    @pytest.mark.asyncio
    async def test_an_unverified_key_violation_does_not_either(self) -> None:
        run = await _evaluate(_request(_API_KEY_HEADER), identity=None, auto_ban=True, limited=True)

        assert run.outcome.action == "limited"
        run.auto_ban.assert_not_awaited()

    def test_the_live_api_key_budget_is_still_above_the_anonymous_default(self) -> None:
        assert tuple(settings.rate_limit.API_KEY_LIMIT) == _API_KEY_LIMIT
        assert _API_KEY_LIMIT > tuple(settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT)
        assert tuple(settings.rate_limit.TIER_RATE_LIMITS["enterprise"]) == _ENTERPRISE_LIMIT


class TestTheResponsePathConsumesTheResolution:
    """Criterion 3, unit level: one Redis GET moved, none added."""

    @pytest.mark.asyncio
    async def test_a_supplied_identity_is_accepted_without_probing_the_cache(self) -> None:
        from src.app.modules.api_keys.service import APIKeyService

        identity = _identity(tier_name="pro", tier_id=2)
        with (
            patch("src.app.modules.api_keys.service.cache_client") as client,
            patch.object(APIKeyService, "_get_cached_tenant_active", AsyncMock(return_value=True)),
        ):
            client.get = AsyncMock(side_effect=AssertionError("the cache must not be probed when identity is supplied"))
            info = await APIKeyService(MagicMock()).validate_api_key(_API_KEY, identity=identity)

        assert info is not None
        assert info["user_id"] == _OWNER_ID
        assert info["tenant_id"] == _TENANT_ID
        assert info["scopes"] == ["read"]
        assert info["key_id"] == _KEY_ID

    @pytest.mark.asyncio
    async def test_a_supplied_identity_still_pays_the_tenant_liveness_probe(self) -> None:
        """No security check is skipped to save a round trip."""
        from src.app.modules.api_keys.service import APIKeyService

        probe = AsyncMock(return_value=True)
        with (
            patch("src.app.modules.api_keys.service.cache_client") as client,
            patch.object(APIKeyService, "_get_cached_tenant_active", probe),
        ):
            client.get = AsyncMock(side_effect=AssertionError("cache must not be probed"))
            info = await APIKeyService(MagicMock()).validate_api_key(_API_KEY, identity=_identity())

        probe.assert_awaited_once_with(_TENANT_ID)
        assert info is not None

    @pytest.mark.asyncio
    async def test_a_suspended_tenant_still_refuses_a_supplied_identity(self) -> None:
        from src.app.modules.api_keys.service import APIKeyService

        with (
            patch("src.app.modules.api_keys.service.cache_client") as client,
            patch.object(APIKeyService, "_get_cached_tenant_active", AsyncMock(return_value=False)),
        ):
            client.get = AsyncMock(side_effect=AssertionError("cache must not be probed"))
            info = await APIKeyService(MagicMock()).validate_api_key(_API_KEY, identity=_identity())

        assert info is None

    @pytest.mark.asyncio
    async def test_an_invalidated_tenant_entry_falls_back_to_the_authoritative_path(self) -> None:
        """``None`` means "the tenant cache entry is gone", not "suspended".

        Suspended refuses. Missing must re-validate from the database, exactly as
        the cache-hit path does today - a stubbed ``None`` that read as a refusal
        would turn a cache eviction into an outage.
        """
        from src.app.modules.api_keys.service import APIKeyService

        with (
            patch("src.app.modules.api_keys.service.cache_client") as client,
            patch.object(APIKeyService, "_get_cached_tenant_active", AsyncMock(return_value=None)),
            patch.object(APIKeyService, "_validate_from_db", AsyncMock(return_value=None)) as db_path,
        ):
            client.get = AsyncMock(side_effect=AssertionError("cache must not be probed"))
            info = await APIKeyService(MagicMock()).validate_api_key(_API_KEY, identity=_identity())

        db_path.assert_awaited()
        assert info is None
