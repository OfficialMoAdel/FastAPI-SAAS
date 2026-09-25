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
    set_token_blacklisted,
)
from src.app.core.security.auto_ban import (
    BanInfo,
    check_and_auto_ban,
    check_ban_and_rate_limit,
    check_ban_blacklist_and_rate_limit,
    get_ban_info,
)
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_rate_limit_key,
    build_verdict,
    check_security_budget,
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
_REFRESH_BUDGET_PATHS: frozenset[str] = frozenset(
    {"/api/v1/account/refresh", "/api/v1/auth/refresh"}
)


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
        # The merged composite hard-codes GCRA arithmetic — that is what buys
        # its single round-trip. Taking it while the operator configured the
        # fixed-window engine would silently serve GCRA here and fixed window
        # everywhere else, so the algorithm gets the last word.
        if settings.security.AUTO_BAN_ENABLED and uses_gcra() and ctx.user_id and ctx.token:
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
                    "Blacklisted/unknown token detected in rate limiter | user_id=%s | treating as anonymous",
                    ctx.user_id,
                )
            if ban_info.is_banned:
                return AdmissionOutcome("reject_banned", client_ip, ban_info=ban_info)
        else:
            limit, period, key = await self._resolve_legacy_config(request, ctx)
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

        if is_limited:
            logger.warning("Rate limit exceeded | key=%s | limit=%s | period=%s", key, limit, period)

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
            return await self._handle_violation(client_ip, verdict)
        return AdmissionOutcome("admit", client_ip, verdict)

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
        verdict = await check_security_budget("refresh", client_ip or "unknown")
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
                    "Auto-ban check failed, skipping | ip=%s | error=%s",
                    client_ip,
                    auto_ban_err,
                )
                was_banned, violation_count = False, 0
            if was_banned:
                logger.warning(
                    "Auto-ban triggered | ip=%s | violations=%d | threshold=%d",
                    client_ip,
                    violation_count,
                    settings.security.AUTO_BAN_THRESHOLD,
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
        return _RateLimitContext(user_id, tier_name, tenant_id, tier_id_raw, token, jti, path)

    async def _resolve_legacy_config(self, request: Request, ctx: _RateLimitContext) -> tuple[int, int, str]:
        user_id = ctx.user_id
        tier_name = ctx.tier_name
        tier_id = ctx.tier_id_raw
        if user_id and ctx.token:
            from src.app.core.security.token_blacklist_cache import is_token_blacklisted

            is_blacklisted = await is_token_blacklisted(ctx.token, jti=ctx.jti)
            set_token_blacklisted(request.state, is_blacklisted)
            if is_blacklisted is not False:
                logger.info(
                    "Blacklisted/unknown token detected in rate limiter | user_id=%s | treating as anonymous",
                    user_id,
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
        key = build_rate_limit_key(ctx.path, user_id=user_id, tenant_id=ctx.tenant_id, anonymous_ip=anonymous_ip)
        return limit, period, key
