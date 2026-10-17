import time
from dataclasses import dataclass, replace
from typing import Any, Literal, NamedTuple, Protocol

from fastapi import Request

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.middleware_exempt import is_path_exempt
from src.app.core.middleware_state import (
    get_client_ip_state,
    is_whitelisted,
    set_api_key_identity,
    set_token_blacklisted,
)
from src.app.core.security.api_key_identity import ApiKeyIdentity, resolve_api_key_identity
from src.app.core.security.auto_ban import (
    BanInfo,
    check_and_auto_ban,
    check_ban_and_rate_limit,
    check_ban_blacklist_and_rate_limit,
    get_ban_info,
)
from src.app.core.security.budgets import Identity, SecurityAction, probe
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_anonymous_aggregate_key,
    build_api_key_rate_limit_key,
    build_rate_limit_key,
    build_verdict,
    consume,
    resolve_rate_limits,
    uses_gcra,
)
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.path_utils import sanitize_path

logger = get_logger("src.app.middleware.rate_limit")

# P1: exempt paths that still cross the ban boundary. Webhook receivers skip
# rate limiting (third-party retries must never 429) but a banned IP stays
# banned. Every other exempt path (health probes, docs) is fully unchecked.
_BAN_CHECKED_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/billing/webhook",
    "/api/v1/webhooks/stripe",
)

# Refresh-budget paths get an identity-keyed security budget at the edge
# (early 429 + Retry-After). Matching is pre-routing on the raw path string,
# so "/api/v1/auth/refresh" is covered deliberately even though no such route
# exists today (the real route is /api/v1/account/refresh): refresh floods
# throttle at the edge regardless of route existence, and a future /auth/*
# refresh route inherits the budget. Pinned by
# test_refresh_budget_exceeded_returns_429 — do not "clean up" this entry.
_REFRESH_BUDGET_PATHS: frozenset[str] = frozenset({"/api/v1/account/refresh", "/api/v1/auth/refresh"})


@dataclass(frozen=True)
class AdmissionOutcome:
    action: Literal["passthrough", "reject_banned", "admit", "limited", "reject_auto_ban"]
    client_ip: str
    verdict: RateLimitVerdict | None = None
    ban_info: BanInfo | None = None
    violation_count: int = 0


class RequestAdmission(Protocol):
    async def evaluate(self, request: Request) -> AdmissionOutcome: ...


class _RateLimitContext(NamedTuple):
    user_id: Any
    tier_name: Any
    tenant_id: Any
    tier_id_raw: Any
    token: str | None
    jti: str | None
    path: str
    # An X-API-Key header was PRESENT. Not "the key is valid": admission runs
    # before the key is verified (bead g4my). Presence is all this needs - it
    # selects the API-key budget, which is keyed by source IP for exactly the
    # reason an unverified credential cannot be allowed to name a bucket.
    api_key: bool = False
    # Bead hfps / ADR-0023: the owner admission resolved for the presented key,
    # or None when it did not resolve. Present in the context (rather than read
    # back off request.state) so `_resolve_legacy_config` can state its budget
    # decision without a second lookup, and so the RAW key never lives on a
    # context that gets logged.
    api_key_identity: ApiKeyIdentity | None = None


class RequestAdmissionService:
    async def evaluate(self, request: Request) -> AdmissionOutcome:
        client_ip = get_client_ip_state(request.state) or get_client_ip(request)
        # R1-OPT: request.url.path is the already-parsed path — urlparse(str(request.url))
        # re-serialized then re-parsed the full URL per request for an identical result.
        norm_path = request.url.path.rstrip("/").lower() or "/"
        if request.method == "OPTIONS":
            return await self._passthrough_unless_banned(client_ip)
        if is_path_exempt(norm_path):
            if norm_path in _BAN_CHECKED_EXEMPT_PREFIXES or norm_path.startswith(
                tuple(f"{prefix}/" for prefix in _BAN_CHECKED_EXEMPT_PREFIXES)
            ):
                return await self._passthrough_unless_banned(client_ip)
            return AdmissionOutcome("passthrough", client_ip)
        if settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT:
            if hasattr(request.state, "ip_whitelisted"):
                is_white = is_whitelisted(request.state)
            else:
                logger.warning(
                    "RateLimitMiddleware: ip_whitelisted attribute not set. "
                    "Ensure IPFilterMiddleware is enabled and ordered before RateLimitMiddleware."
                )
                is_white = False
            if is_white:
                # P1: whitelist bypasses the *counter*, never the ban. A
                # banned IP stays rejected even when whitelisted.
                if settings.security.AUTO_BAN_ENABLED:
                    ban_info = await get_ban_info(client_ip)
                    if ban_info.is_banned:
                        return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
                return AdmissionOutcome(
                    "admit",
                    client_ip,
                    build_verdict(
                        key="whitelisted",
                        limit=None,
                        period=None,
                        is_limited=False,
                        remaining=None,
                        bypassed=True,
                    ),
                )

        # C1: refresh endpoints get an identity-keyed security budget at the
        # edge (early 429 + Retry-After) instead of an unconditional skip.
        # Placed AFTER the whitelist branch: operator-declared whitelisted IPs
        # keep the documented counter-bypass; the endpoint's own `auth` budget
        # still caps them (defense-in-depth).
        if norm_path in _REFRESH_BUDGET_PATHS:
            return await self._evaluate_refresh_budget(client_ip)

        ctx = await self._get_rate_limit_config(request)
        degraded = False
        # Bead g4my. A caller presenting X-API-Key and no bearer credential is
        # on the API-key budget, not the anonymous one, and is therefore not
        # ANONYMOUS traffic: it must not spend the anonymous aggregate ceiling
        # and its violation must not earn an auto-ban. Recorded HERE, before the
        # S-17 rebind below clears `ctx.user_id`, because after that rebind a
        # revoked bearer token and an API-key caller are indistinguishable - and
        # they must stay distinguishable: S-17 lowers a revoked session to
        # anonymous, and that downgrade is the stricter outcome.
        #
        # Bead hfps: `user_id is None` is deliberately NOT part of this. A
        # resolved API-key identity populates `user_id` (that is how the owner's
        # tier budget is reached), and a flag that went false there would
        # re-enable auto-ban for every caller behind a shared NAT - the exact
        # outcome g4my's oracle exists to forbid. The question this flag answers
        # is "which CREDENTIAL was presented", and an API-key credential is not
        # anonymous whether or not anybody has looked it up yet.
        api_key_metered = bool(ctx.api_key) and ctx.token is None
        # The merged composite hard-codes GCRA arithmetic — that is what buys
        # its single round-trip. Taking it while the operator configured the
        # fixed-window engine would silently serve GCRA here and fixed window
        # everywhere else, so the algorithm gets the last word.
        if settings.security.AUTO_BAN_ENABLED and uses_gcra() and ctx.user_id and ctx.token:
            downgraded_to_anonymous = False
            (
                is_blacklisted,
                limit,
                period,
                key,
                ban_info,
                (is_limited, remaining, retry_after),
            ) = await check_ban_blacklist_and_rate_limit(
                client_ip=client_ip,
                token=ctx.token,
                jti=ctx.jti,
                user_id=ctx.user_id,
                tier_name=ctx.tier_name,
                tenant_id=ctx.tenant_id,
                tier_id_raw=ctx.tier_id_raw,
                anonymous_ip=client_ip,
                path=ctx.path,
            )
            set_token_blacklisted(request.state, is_blacklisted)
            if is_blacklisted is not False:
                logger.info(
                    "blacklisted_token_treated_as_anonymous",
                    user_id=ctx.user_id,
                    treated_as_anonymous=True,
                )
                # S-17 FIX (second site): the GCRA seam performs the SAME
                # revoked-token downgrade, so the flag has to be set here too.
                # It was hard-coded False on this branch, which meant the
                # anonymous aggregate ceiling below was skipped for exactly the
                # attacker it exists to stop — a caller holding their own
                # revoked token, now re-keyed into the anonymous bucket.
                downgraded_to_anonymous = True
            if ban_info.is_banned:
                return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
        else:
            limit, period, key, downgraded_to_anonymous = await self._resolve_legacy_config(request, ctx)

            if settings.security.AUTO_BAN_ENABLED and uses_gcra():
                ban_info, (is_limited, remaining, retry_after) = await check_ban_and_rate_limit(
                    client_ip, key, limit, period
                )
                if ban_info.is_banned:
                    return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
            else:
                # Non-GCRA operators pay one extra round-trip here: the ban
                # check and the counter run separately so the counter uses the
                # engine they actually configured.
                if settings.security.AUTO_BAN_ENABLED:
                    ban_info = await get_ban_info(client_ip)
                    if ban_info.is_banned:
                        return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
                is_limited, remaining, retry_after, degraded = await consume(key, limit, period)

        # S-17 FIX: a revoked token is downgraded to ANONYMOUS inside
        # _resolve_legacy_config (or, on the GCRA branch, by the ban seam
        # above). `ctx.user_id` still held the pre-downgrade `sub`, so the
        # aggregate gate further down (`not ctx.user_id`) saw an authenticated
        # caller and SKIPPED the anonymous ceiling — for a caller that had just
        # been re-keyed into the anonymous per-path bucket, under anonymous
        # limits.
        #
        # That is exactly the case the ceiling exists for (the comment above it
        # spells out the rotation attack it closes). The caller owns their own
        # revoked token, so bypassing it is attacker-controlled.
        #
        # Reported explicitly rather than by mutating ctx inside the resolver:
        # returning the decision keeps it visible at the one place that made it.
        if downgraded_to_anonymous:
            ctx = ctx._replace(user_id=None)

        if is_limited:
            logger.warning(
                "rate_limit_exceeded",
                key=key,
                limit=limit,
                period=period,
            )

        # Run-3 N-1 (middleware.rate_limit.anonymous_key_path_scoped):
        # per-path buckets alone do not bound an anonymous caller, because
        # sanitize_path collapses only numeric/UUID segments - rotating a string
        # segment minted a fresh bucket per request, and auto-ban never fired
        # because no single bucket was ever exhausted.
        #
        # An IP-keyed aggregate ceiling, applied only to ANONYMOUS traffic and
        # only when the per-path bucket did not already reject, bounds the total.
        # Its key uses a constant path token, so this check cannot itself be
        # rotated around. Authenticated callers are unaffected: they are already
        # keyed by user id and covered by their tier budget.
        if not is_limited and not ctx.user_id and not api_key_metered:
            aggregate = await self._check_anonymous_aggregate(client_ip)
            if aggregate is not None and aggregate[0]:
                # Only the REJECTION is taken from the aggregate ceiling. The
                # per-path `remaining` header keeps reporting the per-route
                # budget, which is what a well-behaved client paces against;
                # overwriting it with the aggregate value changed the documented
                # header contract for every anonymous caller.
                is_limited = True
                remaining = 0
                retry_after = aggregate[2]

        verdict = build_verdict(
            key=key,
            limit=limit,
            period=period,
            is_limited=is_limited,
            remaining=remaining,
            retry_after=retry_after,
            degraded=degraded,
        )
        if is_limited:
            if api_key_metered:
                # Deliberately NOT auto-ban eligible, for the reason
                # _evaluate_refresh_budget gives: an integration's traffic
                # typically egresses from ONE shared NAT, so one over-budget
                # caller would otherwise ban that address - and with it every
                # other integration behind it - for AUTO_BAN_DURATION. 429 +
                # Retry-After is the answer; a ban is the wrong tool.
                return AdmissionOutcome("limited", client_ip, verdict)
            return await self._handle_violation(client_ip, verdict)
        return AdmissionOutcome("admit", client_ip, verdict)

    async def _check_anonymous_aggregate(self, client_ip: str) -> tuple[bool, int, int] | None:
        """Consume the per-IP aggregate ceiling for anonymous traffic.

        Returns ``None`` when the ceiling is disabled, otherwise the
        ``(is_limited, remaining, retry_after)`` triple for the aggregate bucket.

        Fails in the same direction as the rest of the limiter: the per-path
        ``consume`` call already decided fail-open/fail-closed for this request,
        so a degraded aggregate read is not allowed to flip a request that the
        per-path layer admitted. This ceiling can only ever ADD rejections.
        """
        aggregate_limit, aggregate_period = settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT
        if not aggregate_limit or aggregate_limit <= 0:
            return None

        key = build_anonymous_aggregate_key(client_ip)
        try:
            is_limited, remaining, retry_after, _degraded = await consume(key, aggregate_limit, aggregate_period)
        except Exception as exc:  # noqa: BLE001 - ceiling must not break admission
            logger.warning("anonymous_aggregate_check_failed", error=str(exc), key=key)
            return None

        if is_limited:
            logger.warning(
                "anonymous_aggregate_rate_limit_exceeded",
                key=key,
                limit=aggregate_limit,
                period=aggregate_period,
            )
        return is_limited, remaining, retry_after

    async def _passthrough_unless_banned(self, client_ip: str) -> AdmissionOutcome:
        if settings.security.AUTO_BAN_ENABLED:
            ban_info = await get_ban_info(client_ip)
            if ban_info.is_banned:
                return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
        return AdmissionOutcome("passthrough", client_ip)

    async def _evaluate_refresh_budget(self, client_ip: str) -> AdmissionOutcome:
        """Evaluate the refresh-token probing budget (C1).

        Banned IPs stay rejected (ban boundary preserved from the old
        ``_passthrough_unless_banned`` skip); clean IPs get the ``refresh``
        security budget (fail-closed per C2) so rotation-marker floods get
        an early 429 + Retry-After at the edge.
        """
        if settings.security.AUTO_BAN_ENABLED:
            ban_info = await get_ban_info(client_ip)
            if ban_info.is_banned:
                return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
        verdict = await probe(SecurityAction.REFRESH, Identity(ip=client_ip))
        if verdict.is_limited:
            # Deliberately NOT auto-ban eligible: 429 + Retry-After only, so a
            # shared NAT behind the budget never earns an AUTO_BAN_DURATION
            # outage for every user behind it.
            return AdmissionOutcome("limited", client_ip, verdict)
        return AdmissionOutcome("admit", client_ip, verdict)

    async def _handle_violation(self, client_ip: str, verdict: RateLimitVerdict) -> AdmissionOutcome:
        if settings.security.AUTO_BAN_ENABLED and client_ip:
            try:
                was_banned, violation_count = await check_and_auto_ban(client_ip)
            except Exception as auto_ban_err:
                logger.warning(
                    "auto_ban_check_failed",
                    ip=client_ip,
                    error=str(auto_ban_err),
                )
                was_banned, violation_count = False, 0
            if was_banned:
                logger.warning(
                    "auto_ban_triggered",
                    ip=client_ip,
                    violations=violation_count,
                    threshold=settings.security.AUTO_BAN_THRESHOLD,
                )
                # P3: RateLimitVerdict is frozen — build the final headers
                # dict up front instead of mutating the shared mapping.
                ban_headers = {
                    **verdict.headers,
                    "X-Banned": "true",
                    "X-Ban-Reason": f"Auto-ban after {violation_count} violations",
                }
                try:
                    fresh_ban_info = await get_ban_info(client_ip)
                    if fresh_ban_info.expires_at:
                        ban_retry_after = int(fresh_ban_info.expires_at - time.time()) + 1
                        if ban_retry_after > 0:
                            ban_headers["Retry-After"] = str(ban_retry_after)
                except Exception as exc:
                    # H2: log-and-continue — the ban verdict stands without
                    # Retry-After when the fresh TTL read fails.
                    logger.debug("auto-ban TTL refresh failed", error=str(exc))
                verdict = replace(verdict, headers=ban_headers)
                return AdmissionOutcome("reject_auto_ban", client_ip, verdict, violation_count=violation_count)
        return AdmissionOutcome("limited", client_ip, verdict)

    async def _get_rate_limit_config(self, request: Request) -> _RateLimitContext:
        # Identity comes from the verified token payload, never from
        # request.state. Admission runs before routing (core/guard/ordering.py
        # places RateLimit ahead of Tenant), so the endpoint-level auth
        # dependency cannot have written identity attributes by this point.
        # Four reads of request.state.user_id / tier_name / tenant_id / tier_id
        # sat here and always resolved to None — a state contract no production
        # writer honoured, which is what let a test that fabricated those
        # fields look load-bearing. jwt_payload is the one identity carrier on
        # request.state with a real writer (extract_jwt_claims_to_state).
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.lower().startswith("bearer ") else None
        cached_payload = get_cached_jwt_payload(request)
        if cached_payload is None and token:
            cached_payload = await extract_jwt_claims_to_state(request)
        user_id = cached_payload.get("sub") if cached_payload else None
        tier_name = cached_payload.get("tier_name") if cached_payload else None
        tenant_id = cached_payload.get("tenant_id") if cached_payload else None
        tier_id_raw = cached_payload.get("tier_id") if cached_payload else None
        jti = cached_payload.get("jti") if cached_payload else None
        # P1: the exempt checks in evaluate() match on the lowercased path —
        # the bucket key must use the same normalization or /API/V1/X and
        # /api/v1/x mint separate buckets (and dodge tier-policy matching).
        path = sanitize_path(request.url.path.lower())
        # Bead g4my: presence of the API-key header, nothing more. The same
        # `"X-API-Key" in request.headers` seam decides authentication in
        # src/app/api/deps/auth.py::resolve_principal, and reading it here keeps
        # the two agreeing on what counts as a keyed request. The key is NOT
        # validated here - it cannot be, admission precedes the auth deps - which
        # is exactly why the budget it selects is keyed by source IP.
        api_key_header = request.headers.get("x-api-key")
        api_key = api_key_header is not None
        # Bead hfps / ADR-0023: resolve the key ONCE here, at the edge, instead
        # of leaving the owner's identity (and therefore the owner's tier)
        # invisible to the budget decision. One Redis GET on an entry every
        # API-key request already reads; `get_user_by_api_key` consumes the
        # result below instead of re-probing, so the request pays the same one
        # GET it paid before. The binding on request.state is what makes that
        # possible - admission runs first, so this is the only place a warm
        # resolution can be produced for the request.
        #
        # An EMPTY header value is a presented-but-unusable key: it stays
        # `api_key` (so it is metered, not served anonymously) and resolves to
        # nothing (so it cannot name a bucket).
        identity: ApiKeyIdentity | None = None
        if api_key and token is None and api_key_header:
            identity = await resolve_api_key_identity(api_key_header)
            set_api_key_identity(request.state, identity)
        # A bearer credential wins for metering, which is what g4my's
        # `ctx.token is None` condition already meant and is unchanged here.
        if identity is not None and identity.tier_name:
            # The owner's TIER is what selects the budget, so it is also what
            # gates the owner bucket. An identity without one (a cache entry
            # written before ADR-0023, or an owner with no tier) deliberately
            # leaves `user_id` None, which routes the request to g4my's
            # per-source-IP budget below. Taking the owner bucket without a tier
            # would land in resolve_rate_limits' authenticated fallback
            # (50/hour) - a 20x tightening sold as a feature.
            user_id = identity.user_id
            tier_name = identity.tier_name
            tier_id_raw = identity.tier_id
            tenant_id = identity.tenant_id
        return _RateLimitContext(
            user_id,
            tier_name,
            tenant_id,
            tier_id_raw,
            token,
            jti,
            path,
            api_key,
            identity,
        )

    async def _resolve_legacy_config(self, request: Request, ctx: _RateLimitContext) -> tuple[int, int, str, bool]:
        user_id = ctx.user_id
        tier_name = ctx.tier_name
        tier_id = ctx.tier_id_raw
        if user_id and ctx.token:
            from src.app.core.security.token_blacklist_cache import is_token_blacklisted

            is_blacklisted = await is_token_blacklisted(ctx.token, jti=ctx.jti)
            set_token_blacklisted(request.state, is_blacklisted)
            if is_blacklisted is not False:
                logger.info(
                    "blacklisted_token_treated_as_anonymous",
                    user_id=user_id,
                    treated_as_anonymous=True,
                )
                # P1: downgrade clears the whole tier identity so resolution
                # falls to the anonymous default — keeping tier_id would let
                # a revoked premium token keep premium per-path policy.
                user_id = None
                tier_name = None
                tier_id = None
        limit, period = await resolve_rate_limits(ctx.path, tier_id=tier_id, tier_name=tier_name, user_id=user_id)
        raw_ip = get_client_ip_state(request.state) if not user_id else None
        if not isinstance(raw_ip, str) or not raw_ip or raw_ip == "unknown":
            raw_ip = get_client_ip(request) if not user_id else None
        # P2: collapse missing/unparseable client IPs to None so they share
        # one explicit "unknown" bucket instead of colliding with (or
        # fragmenting around) a literal IP-looking string.
        anonymous_ip = raw_ip if isinstance(raw_ip, str) and raw_ip and raw_ip != "unknown" else None
        # Bead g4my. A caller with an X-API-Key and NO bearer credential has an
        # authenticated credential, so resolve_rate_limits must not be asked to
        # guess for it: with no tier identity it lands on DEFAULT_ANONYMOUS_LIMIT
        # (10 per 3600s), which is what M-7 measured against a Pro account that
        # answers 1000 with its Bearer token. Same shape as the defaults in
        # resolve_rate_limits' own fallback chain - a named budget applied
        # directly, no DB lookup.
        #
        # The condition is deliberately narrow. `ctx.token is None` keeps a
        # request that ALSO presents a bearer credential on the JWT path, and
        # `user_id is None` keeps a revoked token's S-17 downgrade - which has
        # just cleared the tier identity above - anonymous, because revoking a
        # token must never RAISE the caller's budget.
        if ctx.api_key and ctx.token is None and user_id is None:
            api_key_limit, api_key_period = settings.rate_limit.API_KEY_LIMIT
            return api_key_limit, api_key_period, build_api_key_rate_limit_key(ctx.path, anonymous_ip), False
        key = build_rate_limit_key(ctx.path, user_id=user_id, tenant_id=ctx.tenant_id, anonymous_ip=anonymous_ip)
        # S-17: tell the caller the caller was downgraded, so the anonymous
        # aggregate ceiling is not skipped for a caller who is now anonymous.
        return limit, period, key, user_id is None and ctx.user_id is not None
