"""Token issuance — JWT claim building, token pairs, and session establishment.

Extracted from ``AuthService`` so token mechanics live in one unit. This module
is the single seam every authentication door crosses: claim shape, parallel
token generation, refresh-cookie handling, and session registration are defined
here exactly once. Flows provide credentials-check + ``channel``; everything
post-authentication is owned by :meth:`TokenIssuer.issue_session`.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security import create_access_token, create_refresh_token, get_mfa_pending_token_manager
from src.app.core.security.login_helpers import register_login_session
from src.app.core.security.tokens import TokenGenerator
from src.app.shared.exceptions import ServiceUnavailableError, UnauthorizedError

logger = get_logger(__name__)


@dataclass(frozen=True)
class IssuedSession:
    """Result of a successful session issuance across any auth door."""

    access_token: str
    refresh_token: str

    @property
    def expires_in(self) -> int:
        return int(settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES) * 60


# Device-info label used when a login flow has no Request available
# (e.g. magic-link verification outside an HTTP round-trip).
_CHANNEL_DEVICE_LABELS: dict[str, str] = {
    "password": "Password Login",
    "magic_link": "Magic Link Login",
    "passkey": "Passkey Login",
    "mfa": "MFA Login",
    "sso": "SSO Login",
}


class TokenIssuer:
    """Builds JWT claims for authenticated users and establishes sessions."""

    @staticmethod
    def build_token_data(subject: str, user: dict[str, Any]) -> dict[str, Any]:
        """Build the JWT claim payload shared by login and token refresh.

        Includes tier info for Zero-DB rate limiting, ``token_version`` for
        session invalidation on password change, and ``tenant_id`` for tenant
        context validation.
        """
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        return {
            "sub": subject,
            "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
            "token_version": user.get("token_version", 0),
        }

    @staticmethod
    async def create_token_pair(token_data: dict[str, Any]) -> tuple[str, str]:
        """Create an access/refresh token pair in parallel.

        Returns
        -------
        tuple[str, str]
            ``(access_token, refresh_token)``
        """
        # A3: mint ONE stable sid per session up front so access and refresh
        # tokens share it (copy data to avoid mutating caller's dict).
        data_copy = dict(token_data)
        data_copy.setdefault("sid", str(uuid4()))
        access_token_expires = timedelta(minutes=settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES)
        return await asyncio.gather(
            create_access_token(data=data_copy, expires_delta=access_token_expires),
            create_refresh_token(data=data_copy),
        )

    @staticmethod
    async def rotate_token_pair(
        token_data: dict[str, Any],
        old_refresh_payload: dict[str, Any],
    ) -> tuple[str, str]:
        """Create a rotated access/refresh pair preserving the original session.

        A2/A3: ``absolute_exp``, original ``iat`` and stable ``sid`` are copied
        from the OLD refresh payload so rotation cannot extend the absolute
        session window or fork the session identity. Only a brand-new login
        mints fresh values (see :meth:`create_token_pair`).

        Args:
            token_data: Fresh claim payload (sub/tier/token_version/tenant_id).
            old_refresh_payload: Decoded claims of the refresh token being rotated.

        Returns
        -------
        tuple[str, str]
            ``(access_token, refresh_token)``
        """
        carried = {
            key: old_refresh_payload[key]
            for key in ("sid", "absolute_exp", "iat")
            if old_refresh_payload.get(key) is not None
        }
        access_token_expires = timedelta(minutes=settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES)
        return await asyncio.gather(
            create_access_token(data={**token_data, **carried}, expires_delta=access_token_expires),
            create_refresh_token(data={**token_data, **carried}),
        )

    @staticmethod
    async def rotate_session(
        *,
        token_data: dict[str, Any],
        old_refresh_payload: dict[str, Any],
        old_refresh_token: str,
        user: dict[str, Any],
        db: AsyncSession,
        request: Request | None = None,
        response: Response | None = None,
        device_label: str | None = None,
        audit_details: dict[str, Any] | None = None,
    ) -> IssuedSession:
        """Rotate a verified refresh token into a new pair, preserving the session.

        Session-rotation counterpart of :meth:`issue_session`: distributed
        lock → rotated pair (``sid``/``absolute_exp``/``iat`` carried, never
        extended) → old-token blacklist → rotation marker → session
        invalidate + re-register → refresh cookie → audit event.

        The caller (``AuthService.refresh_access_token``) keeps the
        verification pre-checks — reuse detection, token verification,
        absolute expiry, user-active and tenant binding — and hands over the
        verified payload. Everything after verification is owned here, so
        login and refresh share one cookie/session/audit ownership rule.

        Args:
            token_data: Fresh claim payload (``build_token_data`` output).
            old_refresh_payload: Verified decode of the presented refresh
                token (carries ``sid``/``absolute_exp``/``iat``/``jti``).
            old_refresh_token: The presented token string, blacklisted after
                the new pair is minted.
            user: Freshly fetched, active-checked user dict.
            db: Database session (token blacklist write).
            request: Optional FastAPI request; supplies IP/User-Agent.
            response: Optional FastAPI response; the refresh cookie is
                written iff given (the router no longer writes it).
            device_label: Session device label (caller passes User-Agent).
            audit_details: Optional context merged into the audit payload.

        Raises:
            UnauthorizedError: Lock contention (refresh already in progress).
        """
        import json

        from src.app.core.cache.client import cache_client
        from src.app.core.security import blacklist_token
        from src.app.core.security.jwt_utils import (
            extract_claim_unverified,
            extract_jti_unverified,
        )

        # Create new tokens FIRST to ensure atomicity. If this fails, the user is not left locked out.
        lock_key = None
        old_jti = old_refresh_payload.get("jti")
        if cache_client.client is not None and old_jti:
            lock_key = f"refresh_lock:{old_jti}"
            lock_acquired = await cache_client.set_nx(lock_key, "1", ttl=30)
            if not lock_acquired:
                raise UnauthorizedError("Token refresh already in progress.")
        try:
            new_access_token, new_refresh_token = await TokenIssuer.rotate_token_pair(
                token_data, old_refresh_payload
            )

            # Blacklist the used refresh token (Rotation) AFTER new tokens are successfully generated.
            # Thread the already-verified payload so blacklist_token() reads
            # 'exp'/'jti' directly instead of performing another verified decode.
            await blacklist_token(token=old_refresh_token, db=db, payload=old_refresh_payload)

            # Plant a rotation marker so a replayed old token is
            # detectable as theft (refresh-reuse → family revocation).
            try:
                from datetime import UTC
                from datetime import datetime as dt

                now_ts = dt.now(UTC).timestamp()
                marker_ttl = int(
                    min(
                        max(60, float(old_refresh_payload.get("exp", now_ts + 60)) - now_ts),
                        settings.crypt.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
                    )
                )
                # The new token was minted by this process (rotate_token_pair
                # above), so its signature is valid by construction — read jti
                # and exp with cheap unverified base64 extraction instead of
                # another verified decode.
                new_jti = extract_jti_unverified(new_refresh_token)
                new_exp_raw = extract_claim_unverified(new_refresh_token, "exp")
                new_exp = now_ts + marker_ttl
                if new_exp_raw is not None:
                    try:
                        new_exp = float(new_exp_raw)
                    except (TypeError, ValueError):
                        pass
                if cache_client.client is not None and old_jti and new_jti:
                    await cache_client.set(
                        f"refresh_rotated:{old_jti}",
                        json.dumps(
                            {
                                "user_id": user["id"],
                                "sid": old_refresh_payload.get("sid"),
                                "new_jti": new_jti,
                                "new_exp": new_exp,
                            }
                        ),
                        ttl=marker_ttl,
                    )
            except Exception as e:
                logger.debug("failed to plant refresh rotation marker", error=str(e))
        finally:
            if lock_key:
                try:
                    await cache_client.delete(lock_key)
                except Exception as e:
                    # Bounded by ex=30, but log for incident diagnosis
                    logger.debug("Failed to delete refresh lock key", lock_key=lock_key, error=str(e))

        # Invalidate the old session entry to prevent session proliferation,
        # keyed by the stable sid claim carried through rotation.
        try:
            from src.app.core.security.session_manager import session_manager as global_session_manager

            carried_sid = old_refresh_payload.get("sid")
            if isinstance(carried_sid, str) and carried_sid:
                await global_session_manager.invalidate_session(user["id"], carried_sid)
        except Exception as e:
            logger.warning("failed_to_invalidate_old_session_on_refresh", error=str(e))

        # Session re-registration under the carried sid (no token re-parse)
        # and the single cookie write — same ownership as issue_session.
        await register_login_session(
            user_id=user["id"],
            access_token=new_access_token,
            request=request,
            verify=False,
            device_label=device_label,
            session_id=old_refresh_payload.get("sid"),
        )
        if response is not None:
            from src.app.core.security.login_helpers import set_refresh_cookie

            set_refresh_cookie(response, new_refresh_token)

        try:
            from src.app.core.events import event_bus

            ip_address = None
            user_agent = None
            if request is not None:
                from src.app.core.utils.ip_utils import get_client_ip

                ip_address = get_client_ip(request)
                user_agent = request.headers.get("user-agent")
            payload: dict[str, Any] = {
                "user_id": str(user["id"]),
                "tenant_id": str(user.get("tenant_id")) if user.get("tenant_id") is not None else None,
                "channel": "refresh",
                "username": user.get("username"),
                "email": user.get("email"),
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
            if audit_details:
                payload.update(audit_details)
            await event_bus.publish("auth.login_issued", payload)
        except Exception as audit_err:  # pragma: no cover - defensive seam
            logger.warning("login_issued_audit_publish_failed", channel="refresh", error=str(audit_err))

        return IssuedSession(access_token=new_access_token, refresh_token=new_refresh_token)

    @staticmethod
    async def issue_session(
        user: dict[str, Any],
        *,
        channel: str,
        request: Request | None = None,
        response: Response | None = None,
        verify: bool = True,
        subject: str | None = None,
        extra_claims: dict[str, Any] | None = None,
        audit_details: dict[str, Any] | None = None,
    ) -> IssuedSession:
        """Issue tokens and establish the session for an authenticated user.

        Single post-authentication choreography for every login door:
        claims -> token pair (parallel) -> refresh cookie -> session registration.

        Args:
            user: Authenticated user dict (needs ``id``; ``username``/``email``
                and tier/token_version/tenant_id feed the claims).
            channel: Login door name (``password``, ``magic_link``, ``passkey``,
                ``mfa``, ``sso``) — used as the session device label when no
                request is available.
            request: Optional FastAPI request; supplies client IP / User-Agent
                for session tracking.
            response: Optional FastAPI response; when given, the refresh token
                is set as an httpOnly cookie.
            verify: Passed to session registration. Use ``False`` only when the
                access token cannot be verified yet (e.g. MFA completion).
            subject: Claim ``sub`` override; defaults to ``username`` falling
                back to ``email``.
            extra_claims: Additional JWT claims merged on top of the standard
                shape (e.g. SSO's legacy ``id``/``email`` claims).
            audit_details: Optional door-specific context merged into the
                ``auth.login_issued`` audit event payload (e.g. SSO provider).

        Returns:
            IssuedSession with both tokens.

        Raises:
            fastapi.HTTPException: 503 when Redis fails and session registration
                is configured ``fail_closed``.
        """
        resolved_subject = subject if subject is not None else (user.get("username") or user.get("email"))
        if not isinstance(resolved_subject, str):
            raise ValueError(f"Cannot resolve token subject for user {user.get('id')!r}")
        token_data = TokenIssuer.build_token_data(
            subject=resolved_subject,
            user=user,
        )
        # Protect core JWT claims from external extra_claims overwrite (A2-02).
        # R1-2: session/time claims included — a future caller passing sid
        # would fork session identity; absolute_exp/iat would extend the
        # absolute session window that rotation deliberately preserves.
        RESERVED_CLAIMS = frozenset(
            {
                "sub",
                "tenant_id",
                "tier_id",
                "tier_name",
                "token_version",
                "sid",
                "jti",
                "iat",
                "absolute_exp",
                "exp",
                "token_type",
                "aud",
                "iss",
            }
        )
        if extra_claims:
            safe_extra = {k: v for k, v in extra_claims.items() if k not in RESERVED_CLAIMS}
            token_data.update(safe_extra)

        # Mint the stable sid here so both tokens share it AND the known value
        # threads straight into session registration with no token re-parse.
        # (create_token_pair keeps its setdefault as a fallback for direct
        # callers that do not pre-mint.)
        token_data.setdefault("sid", str(uuid4()))
        access_token, refresh_token = await TokenIssuer.create_token_pair(token_data)
        # Cookie write lives here so every door gets identical semantics.
        # Channels that must transform the response (SSO strips the refresh
        # token from the JSON body) keep their own call — set_refresh_cookie
        # is idempotent for the same token, so at most one extra Set-Cookie
        # is ever emitted.
        if response is not None:
            from src.app.core.security.login_helpers import set_refresh_cookie

            set_refresh_cookie(response, refresh_token)
        await register_login_session(
            user_id=user["id"],
            access_token=access_token,
            request=request,
            verify=verify,
            device_label=_CHANNEL_DEVICE_LABELS.get(channel, f"{channel} Login"),
            # The sid was minted above and shared by both tokens — thread it
            # straight into session registration with no token re-parse.
            # (create_token_pair keeps its setdefault as a fallback for
            # direct callers that do not pre-mint.)
            session_id=token_data["sid"],
        )

        # P2-#8: single issuance seam publishes the login-audit event so every
        # door (password, sso, passkey, magic_link, mfa) is audited exactly
        # once, with the door label carried in the payload. Audit must never
        # break issuance — publish failures are logged and swallowed.
        try:
            from src.app.core.events import event_bus

            ip_address = None
            user_agent = None
            if request is not None:
                from src.app.core.utils.ip_utils import get_client_ip

                ip_address = get_client_ip(request)
                user_agent = request.headers.get("user-agent")
            payload: dict[str, Any] = {
                "user_id": str(user["id"]),
                "tenant_id": str(user.get("tenant_id")) if user.get("tenant_id") is not None else None,
                "channel": channel,
                "username": user.get("username"),
                "email": user.get("email"),
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
            if audit_details:
                payload.update(audit_details)
            await event_bus.publish("auth.login_issued", payload)
        except Exception as audit_err:  # pragma: no cover - defensive seam
            logger.warning("login_issued_audit_publish_failed", channel=channel, error=str(audit_err))

        return IssuedSession(access_token=access_token, refresh_token=refresh_token)

    @staticmethod
    async def issue_mfa_pending(
        user: dict[str, Any],
        *,
        redis: Redis | None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Issue an MFA-pending token for a user with MFA enabled.

        Single seam for the MFA-required branch shared by every login door
        (password, magic-link, passkey, SSO): token generation, pending-token
        storage, and response shape are defined here exactly once.

        Args:
            user: Authenticated user dict (needs ``id``; ``username``/``email``
                feed the stored display name).
            redis: Async Redis client for the MFA pending token store.
                ``None`` fails closed with 503 — the pending token cannot be
                stored, so MFA cannot proceed safely.
            ip_address: Client IP recorded with the pending token, if known.

        Returns:
            dict with ``mfa_required``, ``mfa_token``, ``expires_in`` and
            ``message``.
        """
        if redis is None:
            raise ServiceUnavailableError("Authentication service unavailable")
        mfa_token_store = get_mfa_pending_token_manager(redis)
        mfa_token = TokenGenerator.generate_magic_link_token()
        await mfa_token_store.create(
            token=mfa_token,
            data={
                "user_id": user["id"],
                "username": str(user.get("username") or user.get("email") or ""),
                "ip_address": ip_address,
                "scope": "mfa_pending",
            },
        )
        return {
            "mfa_required": True,
            "mfa_token": mfa_token,
            "expires_in": int(settings.auth.MFA_PENDING_EXPIRY_SECONDS),
            "message": "MFA verification required",
        }
