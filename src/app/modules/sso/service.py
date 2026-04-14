import uuid
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.exceptions.http_exceptions import (
    BadRequestException,
    NotFoundException,
    UnauthorizedException,
)
from src.app.core.logger import get_logger
from src.app.core.security import create_access_token, create_refresh_token
from src.app.core.security.tokens import get_sso_state_manager
from src.app.core.service_registry import get_service
from src.app.core.tenancy.context import clear_tenant_context, get_tenant_context, set_tenant_context
from src.app.modules.auth.schemas import AuthTokenResponse
from src.app.modules.sso.crud import crud_user_sso_connections
from src.app.modules.sso.providers.apple import AppleProvider
from src.app.modules.sso.providers.base import BaseOAuthProvider
from src.app.modules.sso.providers.github import GitHubProvider
from src.app.modules.sso.providers.google import GoogleProvider
from src.app.modules.sso.providers.microsoft import MicrosoftProvider
from src.app.modules.sso.schemas import SSOProvider, UserSSOConnectionCreate, UserSSOConnectionUpdate
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.schemas import UserCreate

logger = get_logger(__name__)


class SSOService:
    """Service for handling SSO authentication flows."""

    def _get_provider_instance(
        self, provider_name: str, settings: dict[str, Any], redirect_uri: str
    ) -> BaseOAuthProvider:
        """Factory method to get appropriate provider instance."""
        if provider_name == SSOProvider.GOOGLE:
            return GoogleProvider(
                client_id=settings["client_id"],
                client_secret=settings["client_secret"],
                redirect_uri=redirect_uri,
            )
        if provider_name == SSOProvider.GITHUB:
            return GitHubProvider(
                client_id=settings["client_id"],
                client_secret=settings["client_secret"],
                redirect_uri=redirect_uri,
            )
        if provider_name == SSOProvider.MICROSOFT:
            return MicrosoftProvider(
                client_id=settings["client_id"],
                client_secret=settings["client_secret"],
                redirect_uri=redirect_uri,
            )
        if provider_name == SSOProvider.APPLE:
            return AppleProvider(
                client_id=settings["client_id"],
                client_secret=settings.get("client_secret", ""),
                redirect_uri=redirect_uri,
                team_id=settings["team_id"],
                key_id=settings["key_id"],
                private_key=settings["private_key"],
            )
        raise BadRequestException(f"Provider '{provider_name}' is not supported yet")

    async def get_authorization_url(
        self,
        db: AsyncSession,
        request: Request,
        provider: str,
        redirect_uri: str | None = None,
        connect_mode: bool = False,
        user_id: int | None = None,
    ) -> tuple[str, str]:
        """Generate authorization URL for provider.

        Args:
            db: Database session
            request: FastAPI request
            provider: SSO provider name
            redirect_uri: Optional custom redirect URI
            connect_mode: If True, this is linking provider to existing account
            user_id: User ID to link provider to (required if connect_mode=True)

        Returns:
            Tuple of (authorization_url, state_token)
        """
        from src.app.modules.sso_settings.service import sso_settings_service

        settings_data = await sso_settings_service.get_decrypted_settings(db, provider)
        if not settings_data["is_enabled"]:
            raise BadRequestException(f"Provider '{provider}' is disabled")

        if not redirect_uri:
            base_url = str(request.base_url).rstrip("/")
            redirect_uri = f"{base_url}/api/v1/auth/sso/{provider}/callback"

        tenant = get_tenant_context()
        extra_data: dict[str, Any] = {"tenant_id": tenant.id} if tenant else {}

        # Store connect mode info in state for callback
        if connect_mode:
            extra_data["connect_mode"] = True
            extra_data["user_id"] = user_id

        from src.app.core.utils.cache import client as redis_client

        if not redis_client:
            raise BadRequestException("Redis client not initialized")
        sso_state_manager = get_sso_state_manager(redis_client)
        state = await sso_state_manager.create_state(
            provider=provider,
            redirect_uri=redirect_uri,
            extra_data=extra_data if extra_data else None,
        )

        oauth_provider = self._get_provider_instance(provider, settings_data, redirect_uri)
        return oauth_provider.get_authorization_url(state=state), state

    async def process_callback(  # noqa: C901
        self,
        db: AsyncSession,
        request: Request,
        provider: str,
        code: str,
        state: str,
        user_data: dict[str, Any] | None = None,
    ) -> AuthTokenResponse | dict[str, str]:
        """Handle OAuth2 callback, verify state, and authenticate user.

        Args:
            db: Database session
            request: FastAPI request
            provider: SSO provider name
            code: Authorization code from provider
            state: State token for CSRF protection
            user_data: Optional user data (Apple sends name on first auth)
        """
        from src.app.core.utils.cache import client as redis_client

        if not redis_client:
            raise BadRequestException("Redis client not initialized")
        sso_state_manager = get_sso_state_manager(redis_client)
        state_data = await sso_state_manager.validate_state(state)

        if not state_data:
            raise BadRequestException("Invalid or expired state parameter")

        if state_data["provider"] != provider:
            raise BadRequestException("State provider mismatch")

        try:
            redirect_uri = state_data["redirect_uri"]

            extra_data = state_data.get("extra_data") or {}
            tenant_id: int | None = extra_data.get("tenant_id")

            if tenant_id:
                from src.app.modules.tenants.crud import crud_tenants

                tenant_result = await crud_tenants.get(db, id=tenant_id)
                if tenant_result:
                    # tenant_result may be dict or model - handle both
                    if isinstance(tenant_result, dict):
                        # Need to fetch actual model for set_tenant_context
                        from sqlalchemy import select

                        from src.app.modules.tenants.models import Tenant as TenantModel

                        query = select(TenantModel).where(TenantModel.id == tenant_id)
                        result = await db.execute(query)
                        tenant_model = result.scalar_one_or_none()
                        if tenant_model:
                            set_tenant_context(tenant_model)
                        else:
                            raise BadRequestException("Tenant context not found")
                    else:
                        set_tenant_context(cast(Tenant, tenant_result))
                else:
                    raise BadRequestException("Tenant context not found")

            from src.app.modules.sso_settings.service import sso_settings_service

            settings_data = await sso_settings_service.get_decrypted_settings(db, provider)

            oauth_provider = self._get_provider_instance(provider, settings_data, redirect_uri)
            try:
                token_response = await oauth_provider.exchange_code_for_token(code)
            except Exception as e:
                raise BadRequestException(f"Failed to exchange code: {str(e)}")

            access_token = token_response.get("access_token")
            if not access_token:
                raise BadRequestException("No access token received from provider")

            # Apple uses id_token for user info (no userinfo endpoint)
            user_info: Any
            try:
                if provider == SSOProvider.APPLE:
                    id_token = token_response.get("id_token")
                    if not id_token:
                        raise BadRequestException("No id_token received from Apple")
                    # Apple provider has different signature, cast to access it
                    apple_provider = cast(AppleProvider, oauth_provider)
                    user_info = await apple_provider.get_user_info(id_token, user_data=user_data)
                else:
                    user_info = await oauth_provider.get_user_info(access_token)
            except Exception as e:
                raise BadRequestException(f"Failed to fetch user info: {str(e)}")

            connection = await crud_user_sso_connections.get_by_provider_user_id(
                db=db,
                provider=provider,
                provider_user_id=user_info.provider_user_id,
            )

            found_user_data = None
            connect_mode = extra_data.get("connect_mode", False)
            connect_user_id = extra_data.get("user_id")

            if connect_mode:
                if not connect_user_id:
                    raise BadRequestException("Missing user ID for connect mode")

                if connection:
                    if connection.user_id != connect_user_id:
                        raise BadRequestException(
                            f"This {provider.title()} account is already connected to another user"
                        )

                    await crud_user_sso_connections.update(
                        db=db,
                        object=UserSSOConnectionUpdate(
                            provider_email=user_info.email,
                        ),
                        id=connection.id,
                    )
                else:
                    await self._link_account(db, connect_user_id, provider, user_info)

                found_user_data = await get_service("user", db).get(connect_user_id)
                if not found_user_data:
                    raise NotFoundException("User to connect not found")
            else:
                if connection:
                    await crud_user_sso_connections.update(
                        db=db,
                        object=UserSSOConnectionUpdate(
                            provider_email=user_info.email,
                        ),
                        id=connection.id,
                    )
                    found_user_data = await get_service("user", db).get(connection.user_id)
                    if not found_user_data:
                        raise NotFoundException("Associated user not found")
                else:
                    if not user_info.email:
                        raise BadRequestException("Email required from provider")

                    is_verified = getattr(user_info, "email_verified", False)
                    if isinstance(is_verified, bool):
                        verified = is_verified
                    else:
                        verified = str(is_verified).lower() in ("true", "1", "yes")

                    if not verified:
                        raise BadRequestException("Unverified provider emails are not permitted for security reasons")

                    user_by_email = await get_service("user", db).get_by_email(user_info.email)

                    if user_by_email:
                        found_user_data = user_by_email
                        user_id = found_user_data.id if hasattr(found_user_data, "id") else found_user_data["id"]
                        await self._link_account(db, user_id, provider, user_info)
                    else:
                        current_tenant = get_tenant_context()
                        if not current_tenant:
                            # Auto-create a personal tenant for organic SSO signups
                            from src.app.modules.tenants.models import Tenant as TenantModel
                            from src.app.modules.tenants.schemas import TenantCreate
                            from src.app.modules.tenants.service import get_tenant_service

                            tenant_svc = get_tenant_service(db)

                            base_slug = "".join(c for c in user_info.email.split("@")[0] if c.isalnum()).lower()
                            if not base_slug:
                                base_slug = "workspace"

                            slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"
                            tenant_name = f"{user_info.name or base_slug}'s Workspace"

                            tenant_in = TenantCreate(name=tenant_name, slug=slug)
                            new_tenant_dict = await tenant_svc.create_tenant(tenant_in)

                            # Retrieve ORM object to set in context
                            tenant_orm = await db.get(TenantModel, new_tenant_dict["id"])
                            if not tenant_orm:
                                raise BadRequestException("Failed to create personal tenant")

                            set_tenant_context(tenant_orm)

                        found_user_data = await self._create_sso_user(db, user_info)
                        user_id = found_user_data.id if hasattr(found_user_data, "id") else found_user_data["id"]
                        await self._link_account(db, user_id, provider, user_info)

            is_disabled = False
            # BUG FIX: Previously only checked is_deleted, not is_active.
            # A deactivated (suspended) user could still log in via SSO.
            if hasattr(found_user_data, "is_deleted") and found_user_data.is_deleted:
                is_disabled = True
            elif hasattr(found_user_data, "is_active") and not found_user_data.is_active:
                is_disabled = True
            elif isinstance(found_user_data, dict) and found_user_data.get("is_deleted"):
                is_disabled = True
            elif isinstance(found_user_data, dict) and not found_user_data.get("is_active", False):
                is_disabled = True
            if is_disabled:
                raise UnauthorizedException("Account is disabled")

            # Fetch fully normalized User object with tier eager loaded for token claims
            email_val = found_user_data.email if hasattr(found_user_data, "email") else found_user_data["email"]
            user_svc = get_service("user", db)
            normalized_user = await user_svc.get_user_auth_info(email=email_val)

            if not normalized_user:
                raise UnauthorizedException("Account not found")

            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

            tier = normalized_user.get("tier")
            tier_name = tier.get("name") if tier else None

            # SECURITY FIX: Check if MFA is enabled before issuing access token
            from src.app.modules.mfa.service import get_mfa_service
            mfa_service = get_mfa_service()
            mfa_enabled = await mfa_service.is_mfa_enabled(normalized_user["id"], db)

            if mfa_enabled:
                from src.app.core.security import MFA_PENDING_EXPIRY_SECONDS, MFAPendingTokenManager
                from src.app.core.utils.cache import client as mfa_redis_client
                mfa_token_manager = MFAPendingTokenManager(mfa_redis_client)
                mfa_token = await mfa_token_manager.create_token(
                    user_id=normalized_user["id"],
                    username=normalized_user.get("username") or normalized_user.get("email"),
                )
                return {
                    "mfa_required": "true",
                    "mfa_token": mfa_token,
                    "expires_in": str(MFA_PENDING_EXPIRY_SECONDS),
                    "message": "MFA verification required",
                }

            token_data = {
                "sub": normalized_user.get("username") or normalized_user.get("email"),
                "id": normalized_user.get("id"),
                "email": normalized_user.get("email"),
                "tenant_id": str(normalized_user["tenant_id"]) if normalized_user.get("tenant_id") else None,
                "tier_id": normalized_user.get("tier_id"),
                "tier_name": tier_name,
                "token_version": normalized_user.get("token_version", 0),
            }

            access_token = await create_access_token(
                data=token_data,
                expires_delta=access_token_expires,
            )

            refresh_token = await create_refresh_token(
                data=token_data,
            )

            try:
                import json

                import jwt

                from src.app.core.security.jwt_utils import (
                    _get_algorithm,
                    _get_parsed_verification_key,
                    validate_jwt_length,
                )

                if not validate_jwt_length(access_token):
                    decoded_token = {}
                else:
                    alg = _get_algorithm()
                    key = _get_parsed_verification_key()
                    decoded_token = jwt.decode(access_token, key, algorithms=[alg])
                session_id = decoded_token.get("jti", access_token[:32])

                device_info_str = json.dumps({"user_agent": "SSO Login", "ip": "Unknown"})

                from src.app.core.security.session_manager import session_manager as global_session_manager

                await global_session_manager.register_session(
                    user_id=normalized_user["id"],
                    session_id=session_id,
                    device_info=device_info_str,
                )
            except Exception as e:
                logger.warning("session_registration_failed", error=str(e))

            # Audit log SSO login success
            try:
                from src.app.core.events import EventPriority, event_bus
                # BUG FIX: Removed redundant `logger = get_logger(__name__)` that
                # shadowed the module-level logger within this try block.

                await event_bus.publish(
                    "audit.log",
                    {
                        "action": "auth.sso_login",
                        "entity_type": "user",
                        "entity_id": str(
                            found_user_data.id if hasattr(found_user_data, "id") else found_user_data["id"]
                        ),
                        "tenant_id": str(
                            found_user_data.tenant_id
                            if hasattr(found_user_data, "tenant_id")
                            else found_user_data["tenant_id"]
                        ),
                        "user_id": str(found_user_data.id if hasattr(found_user_data, "id") else found_user_data["id"]),
                        "details": {
                            "provider": provider,
                            "email": found_user_data.email
                            if hasattr(found_user_data, "email")
                            else found_user_data["email"],
                            "username": found_user_data.username
                            if hasattr(found_user_data, "username")
                            else found_user_data["username"],
                            "success": True,
                        },
                        "ip_address": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent"),
                    },
                    priority=EventPriority.NORMAL,
                )
            except Exception as e:
                logger.error("failed_to_publish_sso_audit_log", error=str(e))

            return AuthTokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=int(access_token_expires.total_seconds()),
                token_type="bearer",
            )
        finally:
            clear_tenant_context()

    async def _link_account(self, db: AsyncSession, user_id: int, provider: str, user_info: Any) -> None:
        """Create UserSSOConnection."""
        current_tenant = get_tenant_context()
        if not current_tenant:
            raise BadRequestException("Cannot link account without tenant context")
        connection_in = UserSSOConnectionCreate(
            user_id=user_id,
            tenant_id=current_tenant.id,
            provider=SSOProvider(provider),
            provider_user_id=user_info.provider_user_id,
            provider_email=user_info.email,
        )
        await crud_user_sso_connections.create(db=db, object=connection_in)

    async def _create_sso_user(self, db: AsyncSession, user_info: Any) -> Any:
        """Create a new user from SSO data."""
        # Generate a 23-char random password that satisfies complexity rules:
        # at least 1 uppercase, 1 lowercase, 1 digit, 1 special char - all random.
        import secrets
        import string

        _upper = string.ascii_uppercase
        _lower = string.ascii_lowercase
        _digits = string.digits
        _special = "!@#$%^&*"
        _all = _upper + _lower + _digits + _special
        # Guarantee at least one of each required class, then fill the rest randomly
        _required = [
            secrets.choice(_upper),
            secrets.choice(_lower),
            secrets.choice(_digits),
            secrets.choice(_special),
        ]
        _rest = [secrets.choice(_all) for _ in range(19)]
        _combined = _required + _rest
        secrets.SystemRandom().shuffle(_combined)
        password = "".join(_combined)

        base_username = user_info.email.split("@")[0]
        # Ensure username is lowercase and alphanumeric
        clean_base = "".join(c for c in base_username if c.isalnum()).lower()
        random_suffix = "".join(secrets.choice(_lower + _digits) for _ in range(4))
        username = f"{clean_base}{random_suffix}"[:30]  # Limit length

        user_in = UserCreate(
            email=user_info.email,
            username=username,
            password=password,
            name=user_info.name or base_username,
        )

        # Use service registry for cross-module decoupling
        from src.app.core.service_registry import get_service

        user_svc = get_service("user", db)
        result = await user_svc.create(user=user_in)
        return result

    async def get_enabled_providers(self, db: AsyncSession) -> list[dict[str, Any]]:
        """List all available SSO providers with their status."""
        from src.app.modules.sso_settings.service import sso_settings_service

        providers_list = []
        supported_providers = [SSOProvider.GOOGLE, SSOProvider.GITHUB, SSOProvider.MICROSOFT, SSOProvider.APPLE]

        for provider in supported_providers:
            is_enabled = False
            try:
                # We use get_settings_by_provider which is public/safe
                # Using crud directly might be faster/safer to avoid circular dep issues if service logic is complex
                # But service is better for encapsulation.
                # However, sso_settings_service.get_settings_by_provider raises NotFoundException if not found.
                sso_settings = await sso_settings_service.get_settings_by_provider(db, provider.value)
                is_enabled = sso_settings.is_enabled
            except NotFoundException:
                # Provider not configured -> disabled
                pass

            # If the endpoint is strictly for "enabled" providers as buttons, we might filter here.
            # But the schema has an 'enabled' field, implying we might return all.
            # Let's filter for now to match method name 'get_enabled_providers',
            # OR we change it to return all?
            # The test expected a specific structure. If I change to return all, it's safer for UI to filter.
            # Let's return all so UI knows what's available vs enabled.
            # Wait, if method is 'get_enabled_providers', it should return enabled ones.
            # But the schema 'SSOProviderRead' has 'enabled: bool'.
            # Logically, if I only return enabled ones, 'enabled' is always true.
            # The error 'Field required' for 'enabled' suggests we are sending wrong data structure.
            # Let's assume we return ALL supported providers with their status.

            providers_list.append(
                {
                    "name": provider,
                    "enabled": is_enabled,
                    "display_name": provider.value.title(),
                }
            )

        return providers_list

    async def disconnect_provider(
        self,
        db: AsyncSession,
        user_id: int,
        provider: str,
        tenant_id: UUID | None,
    ) -> None:
        """Disconnect SSO provider from user account.

        Args:
            db: Database session
            user_id: User ID to disconnect provider from
            provider: SSO provider name
            tenant_id: Optional tenant ID for multi-tenant context

        Raises:
            BadRequestException: If this is the user's only authentication method
            NotFoundException: If no connection exists
        """
        # Check if connection exists
        connection = await crud_user_sso_connections.get_by_criteria(
            db=db,
            user_id=user_id,
            provider=provider,
            tenant_id=cast(UUID, tenant_id),
        )

        if not connection:
            raise NotFoundException(f"No {provider} connection found for this account")

        # Check if user can disconnect this provider
        can_disconnect = await self._can_disconnect(db, user_id, tenant_id)
        if not can_disconnect:
            raise BadRequestException(
                "Cannot disconnect: this is your only authentication method. "
                "Please set a password or connect another SSO provider first."
            )

        # Delete the connection
        await crud_user_sso_connections.delete_by_criteria(db=db, id=connection.id, tenant_id=cast(UUID, tenant_id))

    async def _can_disconnect(
        self,
        db: AsyncSession,
        user_id: int,
        tenant_id: UUID | None,
    ) -> bool:
        """Check if user can disconnect an SSO provider.

        User can disconnect if they have:
        - A password set, OR
        - More than one SSO provider connected

        Args:
            db: Database session
            user_id: User ID to check
            tenant_id: Optional tenant ID

        Returns:
            True if user can safely disconnect a provider
        """
        if tenant_id is None:
            raise BadRequestException("Tenant context required")

        # Check if user has a password
        user = await get_service("user", db).get(user_id)
        if user:
            hashed_pw = (
                user.get("hashed_password") if isinstance(user, dict) else getattr(user, "hashed_password", None)
            )
            if hashed_pw:
                return True

        # Check if user has multiple SSO connections
        connection_count = await crud_user_sso_connections.count_for_user(
            db=db,
            user_id=user_id,
            tenant_id=cast(UUID, tenant_id),
        )

        return connection_count > 1


def get_sso_service(db: AsyncSession = Depends(async_get_db)) -> "SSOService":
    """Dependency injection factory for SSOService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    SSOService
        Service instance
    """
    return SSOService()
