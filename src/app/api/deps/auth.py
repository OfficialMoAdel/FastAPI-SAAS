from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from fastapi.params import Depends as DependsClass
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import ForbiddenException, UnauthorizedException
from src.app.core.logger import get_logger
from src.app.core.security import TokenType, api_key_scheme, oauth2_scheme, verify_token
from src.app.core.security.jwt_utils import get_cached_jwt_payload
from src.app.core.tenancy.context import get_current_tenant as get_tenant_context
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.users.service import UserService

logger = get_logger(__name__)


class AuthenticatedUser(BaseModel):
    """Strict typed user model for request dependencies.

    Provides IDE autocomplete while maintaining backward compatibility
    with dict-based access via __getitem__.
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True, from_attributes=True)

    id: int
    uuid: UUID | str | None = None
    email: str
    username: str | None = None
    is_superuser: bool = False
    is_active: bool = True
    is_deleted: bool = False
    tier_id: int | None = None
    tenant_id: UUID | str | int | None = None
    profile_image_url: str | None = None
    token_version: int = 0
    tier: dict[str, Any] | None = None
    api_key_id: str | int | None = None
    api_key_scopes: list[str] | None = None

    def __getitem__(self, key: str) -> Any:
        return self.model_dump()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.model_dump().get(key, default)

    def keys(self) -> Any:
        return self.model_dump().keys()

    def __contains__(self, key: str) -> bool:
        return key in self.model_dump()

    def __len__(self) -> int:
        return len(self.model_dump())


class AuthContext(BaseModel):
    user: AuthenticatedUser
    token_data: Any  # The token data returned from verify_token or cached JWT payload dict
    tenant: Any  # The tenant context (or None if no tenant context is available)
    is_dependency_override: bool = False


async def _create_auth_context(
    user: Any,
    token_data: Any,
    tenant: Any,
    db: AsyncSession,
    is_dependency_override: bool = False,
) -> AuthContext:
    if isinstance(user, dict):
        user_obj = AuthenticatedUser(**user)
    else:
        user_obj = user

    return AuthContext(
        user=user_obj,
        token_data=token_data,
        tenant=tenant,
        is_dependency_override=is_dependency_override,
    )


async def _is_token_blacklisted_with_fallback(token: str, jti: str | None, db: AsyncSession) -> bool:
    """Check if token is blacklisted, falling back to DB if Redis fails."""
    from src.app.core.db.crud_token_blacklist import crud_token_blacklist
    from src.app.core.security.token_blacklist_cache import is_token_blacklisted

    cache_result = await is_token_blacklisted(token, jti=jti)
    if cache_result is True:
        return True
    if cache_result is None:
        if jti and await crud_token_blacklist.exists(db, token=f"jti:{jti}"):
            return True
        if await crud_token_blacklist.exists(db, token=token):
            return True
    return False


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    token: str = Depends(oauth2_scheme),
) -> AuthContext:
    """Get current authenticated user's context, cached per request by FastAPI."""
    if not token or not isinstance(token, str):
        auth_header = request.headers.get("Authorization") if request is not None else None
        if auth_header:
            _, _, token = auth_header.partition(" ")
        else:
            token = ""

    if request is not None and hasattr(request, "app") and hasattr(request.app, "dependency_overrides"):
        if get_current_user in request.app.dependency_overrides:
            override_func = request.app.dependency_overrides[get_current_user]
            import inspect

            if inspect.iscoroutinefunction(override_func):
                user_obj = await override_func()
            else:
                user_obj = override_func()
            current_tenant = get_tenant_context()
            return await _create_auth_context(user_obj, None, current_tenant, db, is_dependency_override=True)

    cached_payload = get_cached_jwt_payload(request) if request is not None else None
    token_data: Any

    if cached_payload:
        jti = cached_payload.get("jti")
        if getattr(request.state, "is_token_blacklisted", False) or (
            not hasattr(request.state, "is_token_blacklisted")
            and await _is_token_blacklisted_with_fallback(token, jti, db)
        ):
            raise UnauthorizedException("Token has been revoked.")

        token_type = cached_payload.get("token_type")
        if token_type != TokenType.ACCESS:
            raise UnauthorizedException("User not authenticated.")

        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            raise UnauthorizedException("User not authenticated.")
        token_data = cached_payload
        payload_version = cached_payload.get("token_version", 0)
    else:
        token_data = await verify_token(token, TokenType.ACCESS, db)
        if token_data is None:
            raise UnauthorizedException("User not authenticated.")

        username_or_email = token_data.username_or_email
        payload_version = getattr(token_data, "token_version", 0) or 0

    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if not user:
        raise UnauthorizedException("User not authenticated.")

    if not user.get("is_active", False):
        raise UnauthorizedException("User account is no longer active.")
    if user.get("is_deleted", False):
        raise UnauthorizedException("User account has been deleted.")

    user_version = user.get("token_version", 0)
    if payload_version != user_version:
        raise UnauthorizedException("Session expired. Please log in again.")

    # Check session validity (revocation)
    jti = cached_payload.get("jti") if cached_payload else getattr(token_data, "jti", None)
    if jti:
        from src.app.core.security.session_manager import session_manager

        session_valid = await session_manager.is_session_valid(
            user_id=user["id"],
            session_id=jti,
        )
        if not session_valid:
            raise UnauthorizedException("Session has been revoked.")

    current_tenant = get_tenant_context()
    if current_tenant and not user.get("is_superuser", False):
        if str(user.get("tenant_id")) != str(current_tenant.id):
            logger.warning(
                "tenant_mismatch_detected",
                user_id=user["id"],
                user_tenant_id=user.get("tenant_id"),
                requested_tenant_id=current_tenant.id,
                requested_tenant_slug=current_tenant.slug,
            )
            raise ForbiddenException("User does not belong to the current tenant.")

    return await _create_auth_context(user, token_data, current_tenant, db)


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    auth_context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthenticatedUser:
    """Get current authenticated user by consuming get_auth_context."""
    if not token or not isinstance(token, str):
        auth_header = request.headers.get("Authorization") if request is not None else None
        if auth_header:
            _, _, token = auth_header.partition(" ")
        else:
            token = ""

    if not auth_context or not isinstance(auth_context, AuthContext):
        auth_context = await get_auth_context(request, db, token)
    return auth_context.user


async def get_user_by_api_key(
    api_key: Annotated[str | None, Depends(api_key_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    request: Request,
) -> AuthenticatedUser | None:
    """Validate API key and return user info.

    This dependency allows authentication using 'X-API-Key' header.
    Returns user dict if valid, None otherwise.
    """
    if not api_key:
        return None

    from src.app.modules.api_keys.service import APIKeyService

    api_key_service = APIKeyService(db)
    key_info = await api_key_service.validate_api_key(api_key)

    if not key_info:
        return None

    from src.app.core.tenancy.context import get_current_tenant

    current_tenant = get_current_tenant()
    if current_tenant:
        key_tenant_id = key_info.get("tenant_id")
        if not key_tenant_id or str(key_tenant_id) != str(current_tenant.tenant_id):
            raise UnauthorizedException("API key does not match the requested tenant context.")

    user_service = UserService(db)
    user_data = await user_service.get(key_info["user_id"])

    if not user_data:
        return None

    if not isinstance(user_data, dict):
        user_dict = {
            "id": user_data.id,
            "username": user_data.username,
            "email": user_data.email,
            "tenant_id": user_data.tenant_id,
            "is_active": user_data.is_active,
            "is_superuser": user_data.is_superuser,
            "token_version": user_data.token_version,
        }
    else:
        user_dict = user_data

    if user_dict.get("is_active", True) and not user_dict.get("is_deleted", False):
        user_dict["api_key_scopes"] = key_info.get("scopes", [])
        user_dict["api_key_id"] = key_info.get("key_id")
        return AuthenticatedUser(**user_dict)  # type: ignore[arg-type]

    return None


async def _handle_auth_mocks(
    request: Request, db: AsyncSession, current_tenant: Any
) -> tuple[bool, AuthContext | None]:
    """Helper to handle test mocks and dependency overrides."""
    if request is not None and hasattr(request, "app") and hasattr(request.app, "dependency_overrides"):
        for dep in (get_current_user, get_optional_user):
            if dep in request.app.dependency_overrides:
                override_func = request.app.dependency_overrides[dep]
                import inspect

                if inspect.iscoroutinefunction(override_func):
                    user_obj = await override_func()
                else:
                    user_obj = override_func()
                if user_obj is None:
                    return True, None
                return True, await _create_auth_context(user_obj, None, current_tenant, db)

    return False, None


async def _verify_optional_bearer_token(
    token_value: str,
    db: AsyncSession,
    request: Request,
    current_tenant: Any,
) -> AuthContext | None:
    cached_payload = get_cached_jwt_payload(request) if request is not None else None
    token_data: Any

    if cached_payload:
        jti = cached_payload.get("jti")
        if getattr(request.state, "is_token_blacklisted", False) or (
            not hasattr(request.state, "is_token_blacklisted")
            and await _is_token_blacklisted_with_fallback(token_value, jti, db)
        ):
            return None

        token_type = cached_payload.get("token_type")
        if token_type != TokenType.ACCESS:
            return None

        username_or_email = cached_payload.get("sub")
        if not username_or_email:
            return None
        token_data = cached_payload
        payload_version = cached_payload.get("token_version", 0)
    else:
        try:
            token_data = await verify_token(token_value, TokenType.ACCESS, db)
        except Exception:
            return None
        if token_data is None:
            return None

        username_or_email = token_data.username_or_email
        payload_version = getattr(token_data, "token_version", 0) or 0

    user_service = UserService(db)
    if "@" in username_or_email:
        user = await user_service.get_user_auth_info(email=username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=username_or_email)

    if not user:
        return None

    if not user.get("is_active", False):
        return None
    if user.get("is_deleted", False):
        return None

    user_version = user.get("token_version", 0)
    if payload_version != user_version:
        return None

    if current_tenant:
        if str(user.get("tenant_id")) != str(current_tenant.id):
            return None

    return await _create_auth_context(user, token_data, current_tenant, db)


async def get_optional_auth_context(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
    api_key_user: AuthenticatedUser | None = Depends(get_user_by_api_key),
) -> AuthContext | None:
    """Get optional auth context from API key or token, validating credentials if provided."""
    if api_key_user is None or hasattr(api_key_user, "dependency") or type(api_key_user).__name__ == "Depends":
        api_key = request.headers.get("X-API-Key") if request is not None else None
        if api_key:
            api_key_user = await get_user_by_api_key(api_key, db, request)
        else:
            api_key_user = None

    current_tenant = get_tenant_context()

    is_mock, mock_context = await _handle_auth_mocks(request, db, current_tenant)
    if is_mock:
        return mock_context

    has_api_key = "X-API-Key" in request.headers if request is not None else False
    if has_api_key:
        if api_key_user is None:
            raise UnauthorizedException("Invalid API key.")
        return await _create_auth_context(api_key_user, None, current_tenant, db)

    auth_header = request.headers.get("Authorization") if request is not None else None
    if not auth_header:
        return None

    token_type_val, _, token_value = auth_header.partition(" ")
    if token_type_val.lower() != "bearer" or not token_value:
        return None

    try:
        return await _verify_optional_bearer_token(token_value, db, request, current_tenant)
    except Exception as e:
        logger.error(f"Error in get_optional_auth_context token verification: {e}")
        return None


async def get_optional_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    api_key_user: Annotated[AuthenticatedUser | None, Depends(get_user_by_api_key)] = None,
    auth_context: Annotated[AuthContext | None, Depends(get_optional_auth_context)] = None,
) -> AuthenticatedUser | None:
    """Get optional user from Bearer token or API Key by consuming get_optional_auth_context."""
    if (
        auth_context is None
        or isinstance(auth_context, DependsClass)
        or hasattr(auth_context, "dependency")
        or type(auth_context).__name__ == "Depends"
    ):
        auth_context = await get_optional_auth_context(request, db, api_key_user)
    if auth_context:
        return auth_context.user
    return None


def requires_api_key_scope(required_scope: str) -> Any:
    """Dependency to check if the current API key has the required scope."""

    async def dependency(
        current_user: Annotated[AuthenticatedUser | None, Depends(get_optional_user)],
    ) -> AuthenticatedUser:
        if current_user is None:
            raise UnauthorizedException("Not authenticated")

        key_id = get_user_attr(current_user, "api_key_id")
        scopes = get_user_attr(current_user, "api_key_scopes", [])

        if key_id and required_scope not in scopes:
            raise ForbiddenException(f"API key missing required scope: {required_scope}")

        return current_user

    return dependency


async def get_current_superuser(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if not get_user_attr(current_user, "is_active", False):
        raise ForbiddenException("User account is no longer active.")
    if not get_user_attr(current_user, "is_superuser", False):
        raise ForbiddenException("You do not have enough privileges.")

    return current_user
