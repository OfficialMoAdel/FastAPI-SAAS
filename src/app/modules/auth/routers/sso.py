"""SSO router - HTTP handlers for OAuth2 authentication endpoints."""

from typing import Annotated, Any

import httpx
import orjson
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import ORJSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.json_utils import json_loads
from src.app.modules.auth.crud import crud_user_sso_connections
from src.app.modules.auth.errors import SSOGatewayError
from src.app.modules.auth.rate_limit import check_auth_rate_limit
from src.app.modules.auth.schemas import AuthTokenResponse, SSOProvider, SSOProviderRead, SSOProvidersResponse
from src.app.modules.auth.services.sso import SSOService, get_sso_service
from src.app.shared.exceptions import AppException as _DomainAppException
from src.app.shared.exceptions import (
    DuplicateError as _DomainDuplicateError,
)
from src.app.shared.exceptions import (
    ValidationError as _DomainValidationError,
)

router = APIRouter(prefix="/account/sso", tags=["sso"])

def _with_cleared_cookie(exc: _DomainAppException) -> _DomainAppException:
    """Attach ``sso_state``-clearing cookie hygiene to a domain error.

    Item 7: the central table merges ``exc.headers`` into the envelope
    response, so routers raise domain errors (with transport headers) and
    never convert to HTTPException. Status/code mapping stays in
    core/errors.py.
    """
    exc.headers = {"Set-Cookie": _get_clear_cookie_str()}
    return exc


def _gateway_error(detail: str, status_code: int) -> SSOGatewayError:
    """Build a provider-transport domain error with cookie hygiene."""
    exc = SSOGatewayError(detail=detail, status_code=status_code)
    exc.headers = {"Set-Cookie": _get_clear_cookie_str()}
    return exc


def _get_state_cookie_kwargs(provider: str | None = None) -> dict:
    """Cookie kwargs for the sso_state login-CSRF cookie.

    M1: Apple uses response_mode=form_post — the browser POSTs cross-site back
    to us. SameSite=Lax cookies are not sent on cross-site POSTs (Firefox/Safari
    enforce this strictly), so the Apple flow must use SameSite=None; Secure.
    Other providers use GET redirects and keep the stricter Lax default.
    """
    if provider == SSOProvider.APPLE.value:
        return {
            "httponly": True,
            "secure": True,
            "samesite": "none",
        }
    return {
        "httponly": True,
        "secure": settings.admin.SESSION_SECURE_COOKIES,
        "samesite": "lax",
    }


def _get_clear_cookie_str() -> str:
    secure_flag = "Secure; " if settings.admin.SESSION_SECURE_COOKIES else ""
    return f'sso_state=""; Max-Age=0; Path=/; HttpOnly; {secure_flag}SameSite=Lax'


def _access_only_response(tokens: AuthTokenResponse | dict[str, Any]) -> Response:
    """M4: never expose the refresh token in the JSON body.

    Mirrors passkey/magic_link flows: the refresh token travels in an HttpOnly
    cookie set via set_refresh_cookie; the body carries only the access token.
    Returns a raw Response so the stripped field cannot be re-serialized.
    """
    from src.app.core.security.login_helpers import set_refresh_cookie

    if isinstance(tokens, AuthTokenResponse):
        body: dict[str, Any] = {
            "access_token": tokens.access_token,
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        }
        refresh_token: Any = tokens.refresh_token
    else:
        body = {k: v for k, v in tokens.items() if k != "refresh_token"}
        refresh_token = tokens.get("refresh_token")

    json_response = ORJSONResponse(content=body)
    if isinstance(refresh_token, str) and refresh_token:
        set_refresh_cookie(json_response, refresh_token)
    # Clear the login-CSRF state cookie on the same response we hand back.
    json_response.delete_cookie("sso_state")
    return json_response


def _validate_redirect_uri(uri: str | None) -> None:
    """Validate redirect URI to prevent open redirects."""
    if not uri:
        return
    # M6: WHATWG URL parsing treats '\' as '/', so '/\evil.com' would be a
    # protocol-relative redirect to evil.com. Reject any backslash outright.
    if "\\" in uri:
        raise _DomainValidationError("Invalid redirect URI.")
    # Allow relative paths or URLs matching the frontend domain
    if uri == settings.app.FRONTEND_URL or uri.startswith(settings.app.FRONTEND_URL + "/"):
        return

    raise _DomainValidationError(
        "Invalid redirect URI. Must be a relative path or match the permitted frontend domain."
    )


@router.get("/providers", response_model=SSOProvidersResponse)
async def list_providers(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> SSOProvidersResponse:
    """List enabled SSO providers."""
    providers = await sso_service.get_enabled_providers(db)
    provider_reads = [SSOProviderRead(**p) for p in providers]
    return SSOProvidersResponse(providers=provider_reads)


@router.get("/{provider}/authorize", dependencies=[Depends(check_auth_rate_limit)])
async def initiate_sso(
    request: Request,
    provider: SSOProvider,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    redirect_uri: Annotated[str | None, Query] = None,
) -> RedirectResponse:
    """Initiate SSO OAuth flow and redirect to provider."""
    # Validate redirect URI to prevent open redirects (H-11)
    _validate_redirect_uri(redirect_uri)

    # tenant context available via get_current_tenant() if needed

    auth_url, state = await sso_service.get_authorization_url(
        provider=provider,
        redirect_uri=redirect_uri,
        request=request,
        db=db,
    )

    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        "sso_state",
        state,
        max_age=600,  # 10 minutes to match Redis TTL
        **_get_state_cookie_kwargs(provider.value),  # M1: SameSite=None for Apple form_post
    )
    return response


# Alias for backward compatibility with old frontend
@router.get("/{provider}", dependencies=[Depends(check_auth_rate_limit)])
async def initiate_sso_alias(
    request: Request,
    provider: SSOProvider,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    redirect_uri: Annotated[str | None, Query] = None,
) -> RedirectResponse:
    """Initiate SSO OAuth flow (alias for /{provider}/authorize for backward compatibility)."""
    _validate_redirect_uri(redirect_uri)
    return await initiate_sso(request, provider, db, sso_service, redirect_uri)


@router.get("/{provider}/callback", dependencies=[Depends(check_auth_rate_limit)])
async def handle_callback(
    request: Request,
    response: Response,
    provider: SSOProvider,
    code: Annotated[str, Query],
    state: Annotated[str, Query],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> Response:
    """Handle OAuth callback and return authentication tokens.

    M4: returns a JSON Response containing only the access token; the refresh
    token is delivered via HttpOnly cookie (set inside _access_only_response).
    """
    # Login CSRF Protection Constraint
    import hmac

    cookie_state = request.cookies.get("sso_state")
    if not cookie_state or not hmac.compare_digest(state or "", cookie_state or ""):
        raise _with_cleared_cookie(
            _DomainValidationError("State mismatch or missing session cookie (Login CSRF detection)")
        )
    try:
        tokens = await sso_service.process_callback(
            provider=provider,
            code=code,
            state=state,
            request=request,
            db=db,
        )
        return _access_only_response(tokens)  # M4
    except _DomainAppException as e:
        raise _with_cleared_cookie(e)
    except httpx.TimeoutException:
        # Item 7: provider-transport 504 as a domain error with cookie
        # hygiene (was a sanctioned HTTPException); the table maps it.
        raise _gateway_error("Provider timeout.", 504)
    except httpx.HTTPStatusError:
        # Item 7: provider-transport 502 as a domain error with cookie
        # hygiene (was a sanctioned HTTPException); the table maps it.
        raise _gateway_error("Provider returned an error response.", 502)
    finally:
        response.delete_cookie("sso_state")


@router.post("/{provider}/callback", dependencies=[Depends(check_auth_rate_limit)])
async def handle_post_callback(
    request: Request,
    response: Response,
    provider: SSOProvider,
    code: Annotated[str, Form()],
    state: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    user: Annotated[str | None, Form()] = None,
) -> Response:
    """Handle OAuth POST callback (for Apple Sign-In which uses response_mode=form_post).

    M4: returns a JSON Response containing only the access token; the refresh
    token is delivered via HttpOnly cookie (set inside _access_only_response).

    Apple sends the callback as a POST request with form data instead of GET query params.
    The `user` field contains JSON with name info on first authorization only.
    """
    # Login CSRF Protection Constraint
    import hmac

    cookie_state = request.cookies.get("sso_state")
    if not cookie_state or not hmac.compare_digest(state or "", cookie_state or ""):
        raise _with_cleared_cookie(
            _DomainValidationError("State mismatch or missing session cookie (Login CSRF detection)")
        )

    try:
        # Parse user data if provided (Apple sends this on first auth)
        user_data = None
        if user:
            try:
                user_data = json_loads(user)
            except orjson.JSONDecodeError:
                pass

        tokens = await sso_service.process_callback(
            provider=provider,
            code=code,
            state=state,
            request=request,
            db=db,
            user_data=user_data,
        )
        return _access_only_response(tokens)  # M4
    except _DomainAppException as e:
        raise _with_cleared_cookie(e)
    except httpx.TimeoutException:
        # Item 7: provider-transport 504 as a domain error with cookie
        # hygiene (was a sanctioned HTTPException); the table maps it.
        raise _gateway_error("Provider timeout.", 504)
    except httpx.HTTPStatusError:
        # Item 7: provider-transport 502 as a domain error with cookie
        # hygiene (was a sanctioned HTTPException); the table maps it.
        raise _gateway_error("Provider returned an error response.", 502)
    finally:
        response.delete_cookie("sso_state")


@router.post("/{provider}/connect", dependencies=[Depends(check_auth_rate_limit)])
async def connect_provider(
    request: Request,
    provider: SSOProvider,
    ctx: RequestContextDep,
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    redirect_uri: Annotated[str | None, Query] = None,
) -> RedirectResponse:
    """Connect SSO provider to existing authenticated account."""
    _validate_redirect_uri(redirect_uri)

    tenant_id = ctx.tenant_id
    if not ctx.has_tenant:
        raise _DomainValidationError("Tenant context not set")

    existing_connection = await crud_user_sso_connections.exists_for_user(
        db=ctx.db,
        user_id=ctx.user_id,
        provider=provider.value,
        tenant_id=tenant_id,
    )
    if existing_connection:
        raise _DomainDuplicateError(
            "provider",
            provider.value,
            message=f"Provider {provider.value} already connected to this account",
        )

    auth_url, state = await sso_service.get_authorization_url(
        provider=provider,
        redirect_uri=redirect_uri,
        request=request,
        db=ctx.db,
        connect_mode=True,
        user_id=ctx.user_id,
    )

    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        "sso_state",
        state,
        max_age=600,  # 10 minutes to match Redis TTL
        httponly=True,
        secure=settings.admin.SESSION_SECURE_COOKIES,
        samesite="lax",
    )
    return response


@router.delete("/{provider}")
async def disconnect_provider(
    request: Request,
    provider: SSOProvider,
    ctx: RequestContextDep,
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> dict[str, str]:
    """Disconnect SSO provider from account."""
    tenant_id = ctx.tenant_id
    if not ctx.has_tenant:
        raise _DomainValidationError("Tenant context not set")

    await sso_service.disconnect_provider(
        db=ctx.db,
        user_id=ctx.user_id,
        provider=provider.value,
        tenant_id=tenant_id,
    )
    return {"message": f"Provider {provider.value} disconnected successfully"}
