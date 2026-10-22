import asyncio
import re
import time
from typing import Any
from uuid import UUID

import jwt

from src.app.core.logger import get_logger
from src.app.modules.auth.providers.base import BaseOAuthProvider

logger = get_logger(__name__)

# P1-4: see apple.py. One cached client per module; ``lifespan`` bounds the
# key cache so a rotated key is still picked up.
#
# The client is MODULE-scoped and the shared "common" key set is used for
# every authority. P1-4 forbids constructing a client inside a function (the
# cache defeat), and a per-tenant client could only be built at request
# time — so the key source stays common. The tenant binding that A12 adds
# is the ISSUER check plus the audience pin, not a different key set.
_JWKS_CACHE_SECONDS = 300
_jwks_client = jwt.PyJWKClient(
    "https://login.microsoftonline.com/common/discovery/v2.0/keys",
    cache_keys=True,
    lifespan=_JWKS_CACHE_SECONDS,
)

# A12: the exact shape of a v2.0 id_token issuer in multi-tenant mode: a
# concrete tenant GUID under login.microsoftonline.com. The old check was
# prefix+suffix, which accepted ANY path segment.
_MULTI_TENANT_ISSUER_RE = re.compile(
    r"^https://login\.microsoftonline\.com/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/v2\.0$"
)

# Authority segments Microsoft accepts in place of a concrete tenant. They are
# what you REQUEST, never what an id_token carries as ``iss`` (that is always
# the concrete tenant GUID).
_RESERVED_AUTHORITIES = frozenset({"common", "organizations", "consumers"})


def _normalise_tenant_id(tenant_id: str | None) -> str | None:
    """Coerce the configured tenant to the form Microsoft puts in ``iss``.

    A12 pinned the issuer check to the configured value, but the settings
    schema accepts a free-form string and the docs offered "GUID or domain".
    A domain never appears in ``iss`` (the issuer is the tenant's GUID), so
    pinning against it rejected every token — and the failure was then
    swallowed into "unknown", which Microsoft's structural trust admitted.
    Rejecting the domain here makes the misconfiguration loud at construction
    instead of silently disabling the pin.
    """
    if tenant_id is None:
        return None
    value = tenant_id.strip()
    if not value:
        return None
    if value.lower() in _RESERVED_AUTHORITIES:
        return value.lower()
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError(
            "Microsoft tenant_id must be a directory (tenant) GUID or one of "
            f"{sorted(_RESERVED_AUTHORITIES)}; got {tenant_id!r}. Use the "
            "tenant ID from Entra, not its domain name."
        ) from exc


class MicrosoftProvider(BaseOAuthProvider):
    """Microsoft Azure AD OAuth2 Provider implementation.

    ``tenant_id`` selects the authority segment of every URL (authorize,
    token, id_token ``iss`` check). Unset = multi-tenant ("common"); set to
    a tenant GUID (or a reserved authority segment) to pin sign-in to exactly
    one tenant (A12). A domain form is rejected: ``iss`` carries the tenant
    GUID, never the domain.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: list[str] | None = None,
        *,
        tenant_id: str | None = None,
    ):
        super().__init__(client_id, client_secret, redirect_uri, scope)
        self.tenant_id = _normalise_tenant_id(tenant_id)

    @property
    def _authority(self) -> str:
        # getattr, not attribute access: the provider is sometimes built via
        # object.__new__ (tests) without __init__ running.
        return getattr(self, "tenant_id", None) or "common"

    @property
    def provider_name(self) -> str:
        return "microsoft"

    @property
    def authorization_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self._authority}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self._authority}/oauth2/v2.0/token"

    @property
    def userinfo_endpoint(self) -> str:
        # The OIDC UserInfo endpoint, NOT the Graph API /me endpoint.
        #
        # The v2.0 endpoint issues an access token whose audience matches the
        # requested scopes. Requesting the OIDC scopes (openid/email/profile)
        # yields a token for the OIDC UserInfo endpoint, which is the surface
        # those scopes authorise. Graph's /me is a *different* resource: it
        # needs the delegated ``https://graph.microsoft.com/User.Read``
        # permission, which is a separate tenant consent. Asking for OIDC
        # scopes and then calling Graph /me is the scope/resource mismatch
        # that made every Microsoft callback fail.
        #
        # UserInfo returns the standard OIDC claims this provider already
        # reads: ``sub`` (the stable per-app object id) and ``email``.
        return "https://graph.microsoft.com/oidc/userinfo"

    def get_default_scopes(self) -> list[str]:
        # OIDC scopes — these authorise the OIDC UserInfo endpoint above.
        # ``offline_access`` is included so the provider's own refresh_token
        # (if the tenant issues one) can be used; ``email`` is required for
        # the email claim the UserInfo endpoint returns.
        return ["openid", "email", "profile", "offline_access"]

    @property
    def is_oidc_compliant(self) -> bool:
        return True

    def trusts_identity_without_claim(self) -> bool:
        """Entra ID is a directory authority: the tenant owns the mailbox
        namespace, so an absent xms_edov/email_verified claim is unknown,
        not unverified.
        """
        return True

    def requires_verified_email(self) -> bool:
        """Whether this provider guarantees a verified email.

        Microsoft/Entra ID is a *directory* authority: the tenant that owns
        the account also owns the mailbox namespace (``userPrincipalName``
        / ``mail`` come from the tenant's own directory, and the
        ``xms_edov`` claim asserts email-ownership verification for
        personal Microsoft accounts). So a Microsoft identity is as
        trustworthy as the tenant that vouched for it.

        The claim is NOT named ``email_verified`` though — that is a
        Google-shaped claim, and Microsoft id_tokens never carry it. Reading
        it unconditionally is what made ``email_verified`` permanently
        False here and blocked every Microsoft signup. The provider
        therefore reports the verification verdict from the claims Microsoft
        actually emits, and the SSO gate consumes that verdict instead of
        applying a blanket rule.
        """
        return True

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        response_data = await self._http_post(
            self.token_endpoint,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(self.scopes) if self.scopes else " ".join(self.get_default_scopes()),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return response_data

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> Any:
        """Get user info from the OIDC UserInfo endpoint.

        Returns standard OIDC claims: ``sub`` (stable object id), ``email``,
        ``name``.

        A4: ``email_verified`` is tri-state (True / False / None=unknown).
        Microsoft does NOT carry the Google-shaped ``email_verified`` claim;
        personal accounts signal verification via ``xms_edov``. Reading the
        wrong claim made ``email_verified`` permanently False and blocked
        every Microsoft signup. An id_token that is PRESENT but fails
        verification is an assertion we cannot trust, so it fails closed
        (False) rather than degrading to "unknown". Only a genuinely absent
        claim on a verified token is None, letting the SSO gate consult the
        provider's structural trust policy.

        A11: a missing ``sub`` raises ValueError rather than yielding the
        literal string "None", which would collapse distinct users onto a
        single connection row.
        """
        data = await self._http_get(
            self.userinfo_endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        # A11: sub is mandatory in an OIDC UserInfo response; absence means
        # the provider misbehaved and we must not invent an identity.
        provider_user_id = data.get("sub")
        if not provider_user_id:
            raise ValueError("Microsoft UserInfo response is missing the 'sub' claim")

        # A4: tri-state verification from the claims Microsoft actually emits.
        # An EXPLICIT negative is honoured (fails closed); an ABSENT claim is
        # unknown, not unverified, and the SSO gate consults the provider's
        # structural-trust policy for that case.
        email_verified: bool | None = None
        claims: dict[str, Any] = {}
        if id_token:
            try:
                claims = await self._verify_id_token(id_token)
                if claims.get("email_verified") is True or claims.get("xms_edov") is True:
                    email_verified = True
                elif claims.get("email_verified") is False or claims.get("xms_edov") is False:
                    email_verified = False
            except Exception as exc:
                # A presented token that fails verification must not be
                # treated as "unknown": that let a domain-form tenant_id (whose
                # issuer never matched) turn an explicit xms_edov=False into a
                # trust decision. Fail closed and keep the reason in the logs.
                logger.warning(
                    "microsoft_id_token_unverifiable",
                    error_type=type(exc).__name__,
                )
                email_verified = False

        # UserInfo does not return ``userPrincipalName``; for Entra work
        # accounts the address often lives only in the id_token as
        # ``preferred_username``/``upn``. Without this fallback an account
        # with no mailbox claim failed with "Email required from provider".
        email = (
            data.get("email")
            or data.get("mail")
            or data.get("userPrincipalName")
            or claims.get("preferred_username")
            or claims.get("upn")
        )

        # Normalize to internal structure
        class UserInfo:
            def __init__(self, data, provider_user_id, email, email_verified):
                self.provider_user_id = str(provider_user_id)
                self.email = email
                self.email_verified = email_verified
                self.name = data.get("name") or data.get("displayName") or data.get("givenName", "")
                self.picture = data.get("picture")

        return UserInfo(data, provider_user_id, email, email_verified)

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify and decode Microsoft's OIDC id_token JWT.

        Mirrors AppleProvider._verify_id_token: signature checked against
        the Microsoft JWKS, audience pinned to our client_id, issuer
        pinned to login.microsoftonline.com v2.0. Fail-closed: any
        verification error raises, and the caller treats the token as
        unverified (email_verified=False).
        """
        # P1-4: get_signing_key_from_jwt performs a SYNCHRONOUS urllib HTTPS
        # GET. Called directly from async def it blocks the event loop, so it
        # is offloaded to a worker thread. See apple.py for the full rationale.
        started = time.monotonic()
        signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, id_token)
        logger.info(
            "microsoft_jwks_signing_key_resolved",
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )

        claims: dict[str, Any] = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.client_id,
            options={"verify_iss": False},
        )
        issuer = str(claims.get("iss", ""))
        if not self._issuer_is_allowed(issuer):
            raise ValueError(f"Unexpected id_token issuer: {issuer}")
        return claims

    def _issuer_is_allowed(self, issuer: str) -> bool:
        """Whether an id_token ``iss`` is acceptable.

        A12: the old check was a prefix/suffix test, which accepted ANY
        path segment (``https://login.microsoftonline.com/<anything>/v2.0``).
        Now the issuer must be EITHER the pinned tenant, or — in
        multi-tenant mode — exactly the shape Microsoft issues: a tenant
        GUID under ``login.microsoftonline.com``.

        The ``common`` / ``organizations`` values are AUTHORITY segments
        (what you request), never what a v2.0 id_token carries as ``iss``
        (that is always the concrete tenant GUID), so a reserved authority
        is checked with the multi-tenant shape rule rather than an exact
        match.

        The signature stays pinned to Microsoft's JWKS with RS256 and the
        audience to our client_id, so this closes the issuer axis, not
        forgery.
        """
        tenant_id = getattr(self, "tenant_id", None)
        if tenant_id and tenant_id not in _RESERVED_AUTHORITIES:
            return issuer == f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        return _MULTI_TENANT_ISSUER_RE.fullmatch(issuer) is not None
