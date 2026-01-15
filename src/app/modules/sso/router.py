"""SSO router - HTTP handlers for OAuth2 authentication endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_loads

from ...api.dependencies import get_current_user
from ...core.db.database import async_get_db
from ...core.tenancy.context import get_tenant_context
from ..auth.schemas import AuthTokenResponse
from .crud import crud_user_sso_connections
from .schemas import SSOProvider, SSOProvidersResponse
from .service import SSOService, get_sso_service

router = APIRouter(prefix="/auth/sso", tags=["sso"])


@router.get("/providers", response_model=SSOProvidersResponse)
async def list_providers(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> SSOProvidersResponse:
    """List enabled SSO providers."""
    providers = await sso_service.get_enabled_providers(db)
    return {"providers": providers}


@router.get("/{provider}/authorize")
async def initiate_sso(
    request: Request,
    provider: SSOProvider,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    redirect_uri: Annotated[str | None, Query] = None,
) -> RedirectResponse:
    """Initiate SSO OAuth flow and redirect to provider."""
    # tenant context available via get_tenant_context() if needed

    auth_url = await sso_service.get_authorization_url(
        provider=provider,
        redirect_uri=redirect_uri,
        request=request,
        db=db,
    )
    return RedirectResponse(url=auth_url)


@router.get("/{provider}/callback")
async def handle_callback(
    request: Request,
    provider: SSOProvider,
    code: Annotated[str, Query],
    state: Annotated[str, Query],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> AuthTokenResponse:
    """Handle OAuth callback and return authentication tokens."""
    try:
        tokens = await sso_service.process_callback(
            provider=provider,
            code=code,
            state=state,
            request=request,
            db=db,
        )
        return tokens
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Provider timeout or error: {str(e)}",
        )


@router.post("/{provider}/callback")
async def handle_post_callback(
    request: Request,
    provider: SSOProvider,
    code: Annotated[str, Form()],
    state: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    user: Annotated[str | None, Form()] = None,
) -> AuthTokenResponse:
    """Handle OAuth POST callback (for Apple Sign-In which uses response_mode=form_post).

    Apple sends the callback as a POST request with form data instead of GET query params.
    The `user` field contains JSON with name info on first authorization only.
    """
    try:
        # Parse user data if provided (Apple sends this on first auth)
        user_data = None
        if user:
            try:
                user_data = json_loads(user)
            except json.JSONDecodeError:
                pass

        tokens = await sso_service.process_callback(
            provider=provider,
            code=code,
            state=state,
            request=request,
            db=db,
            user_data=user_data,
        )
        return tokens
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Provider timeout or error: {str(e)}",
        )


@router.post("/{provider}/connect")
async def connect_provider(
    request: Request,
    provider: SSOProvider,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
    redirect_uri: Annotated[str | None, Query] = None,
) -> RedirectResponse:
    """Connect SSO provider to existing authenticated account."""
    tenant = get_tenant_context()
    tenant_id = tenant.id if tenant else None

    existing_connection = await crud_user_sso_connections.exists(
        db=db,
        user_id=current_user["id"],
        provider=provider.value,
        tenant_id=tenant_id,
    )
    if existing_connection:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider {provider.value} already connected to this account",
        )

    auth_url = await sso_service.get_authorization_url(
        provider=provider,
        redirect_uri=redirect_uri,
        request=request,
        db=db,
        connect_mode=True,
        user_id=current_user["id"],
    )
    return RedirectResponse(url=auth_url)


@router.delete("/{provider}")
async def disconnect_provider(
    request: Request,
    provider: SSOProvider,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    sso_service: Annotated[SSOService, Depends(get_sso_service)],
) -> dict[str, str]:
    """Disconnect SSO provider from account."""
    tenant = get_tenant_context()
    tenant_id = tenant.id if tenant else None

    await sso_service.disconnect_provider(
        db=db,
        user_id=current_user["id"],
        provider=provider.value,
        tenant_id=tenant_id,
    )
    return {"message": f"Provider {provider.value} disconnected successfully"}
