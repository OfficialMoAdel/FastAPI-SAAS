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

from src.app.core.config import settings
from src.app.core.security import create_access_token, create_refresh_token, get_mfa_pending_token_manager
from src.app.core.security.login_helpers import register_login_session
from src.app.core.security.tokens import TokenGenerator
from src.app.shared.exceptions import ServiceUnavailableError


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
    async def issue_session(
        user: dict[str, Any],
        *,
        channel: str,
        request: Request | None = None,
        response: Response | None = None,
        verify: bool = True,
        subject: str | None = None,
        extra_claims: dict[str, Any] | None = None,
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
        # Protect core JWT claims from external extra_claims overwrite (A2-02)
        RESERVED_CLAIMS = frozenset({"sub", "tenant_id", "tier_id", "tier_name", "token_version"})
        if extra_claims:
            safe_extra = {k: v for k, v in extra_claims.items() if k not in RESERVED_CLAIMS}
            token_data.update(safe_extra)

        access_token, refresh_token = await TokenIssuer.create_token_pair(token_data)
        await register_login_session(
            user_id=user["id"],
            access_token=access_token,
            request=request,
            verify=verify,
            device_label=_CHANNEL_DEVICE_LABELS.get(channel, f"{channel} Login"),
        )

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
