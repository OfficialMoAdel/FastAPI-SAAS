import uuid
from typing import Any, cast
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.events import EventPriority
from src.app.core.exceptions.http_exceptions import (
    BadRequestException,
    NotFoundException,
    UnauthorizedException,
)
from src.app.core.logger import get_logger
from src.app.core.security.tokens import get_sso_state_manager
from src.app.core.tenancy.context import get_current_tenant, set_current_tenant
from src.app.modules.audit_logs import audit
from src.app.modules.auth.schemas import AuthTokenResponse
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.modules.sso.crud import crud_user_sso_connections
from src.app.modules.sso.providers.apple import AppleProvider
from src.app.modules.sso.providers.base import BaseOAuthProvider
from src.app.modules.sso.providers.github import GitHubProvider
from src.app.modules.sso.providers.google import GoogleProvider
from src.app.modules.sso.providers.microsoft import MicrosoftProvider
from src.app.modules.sso.schemas import SSOProvider, UserSSOConnectionCreate, UserSSOConnectionUpdate
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.schemas import UserCreate
from src.app.modules.users.service import UserService

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
            redirect_uri = f"{base_url}/api/v1/account/sso/{provider}/callback"

        tenant = get_current_tenant()
        extra_data: dict[str, Any] = {"tenant_id": tenant.id} if tenant else {}

        # Store connect mode info in state for callback
        if connect_mode:
            extra_data["connect_mode"] = True
            extra_data["user_id"] = user_id

        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        if not redis_client:
            raise BadRequestException("Redis client not initialized")
        sso_state_manager = get_sso_state_manager(redis_client)
        from src.app.core.security.tokens import TokenGenerator

        state = TokenGenerator.generate_sso_state()
        state_data: dict[str, Any] = {
            "provider": provider,
            "redirect_uri": redirect_uri,
        }
        if extra_data:
            state_data["extra_data"] = extra_data

        await sso_state_manager.create(
            token=state,
            data=state_data,
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
    ) -> AuthTokenResponse | dict[str, Any]:
        """Handle OAuth2 callback, verify state, and authenticate user.

        Args:
            db: Database session
            request: FastAPI request
            provider: SSO provider name
            code: Authorization code from provider
            state: State token for CSRF protection
            user_data: Optional user data (Apple sends name on first auth)
        """
        from src.app.core.redis_provider import get_redis

        redis_client = get_redis()

        if not redis_client:
            raise BadRequestException("Redis client not initialized")
        sso_state_manager = get_sso_state_manager(redis_client)
        state_data = await sso_state_manager.consume(state)

        if not state_data:
            raise BadRequestException("Invalid or expired state parameter")

        if state_data["provider"] != provider:
            raise BadRequestException("State provider mismatch")

        # Preserve any tenant context already set by upstream middleware (e.g. an
        # authenticated user in connect_mode). We only override it below if the SSO
        # state carries an explicit tenant_id, and we must restore the prior value
        # afterwards so we don't discard the caller's context.
        prior_tenant_ctx = get_current_tenant()

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
                        # Need to fetch actual model for set_current_tenant
                        from sqlalchemy import select

                        from src.app.modules.tenants.models import Tenant as TenantModel

                        query = select(TenantModel).where(TenantModel.id == tenant_id)
                        result = await db.execute(query)
                        tenant_model = result.scalar_one_or_none()
                        if tenant_model:
                            set_current_tenant(tenant_model)
                        else:
                            raise BadRequestException("Tenant context not found")
                    else:
                        set_current_tenant(cast(Tenant, tenant_result))
                else:
                    raise BadRequestException("Tenant context not found")

            from src.app.modules.sso_settings.service import sso_settings_service

            settings_data = await sso_settings_service.get_decrypted_settings(db, provider)

            oauth_provider = self._get_provider_instance(provider, settings_data, redirect_uri)
            try:
                token_response = await oauth_provider.exchange_code_for_token(code)
            except Exception as e:
                logger.error("SSO Code Exchange Failed", error_type=type(e).__name__, provider=provider)
                raise BadRequestException("Failed to exchange code with provider")

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
                elif provider == SSOProvider.MICROSOFT:
                    # M10: pass the id_token so email_verified can be gated on
                    # the provider's claim instead of being hardcoded True.
                    user_info = await oauth_provider.get_user_info(
                        access_token, id_token=token_response.get("id_token")
                    )
                else:
                    user_info = await oauth_provider.get_user_info(access_token)
            except Exception as e:
                logger.error("SSO User Info Fetch Failed", error_type=type(e).__name__, provider=provider)
                raise BadRequestException("Failed to fetch user info from provider")

            current_tenant = get_current_tenant()
            if not current_tenant:
                raise BadRequestException("Cannot process SSO login without tenant context")

            connection = await crud_user_sso_connections.get_by_provider_user_id(
                db=db,
                provider=provider,
                provider_user_id=user_info.provider_user_id,
                tenant_id=current_tenant.id,
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

                found_user_data = await UserService(db).get(connect_user_id)
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
                    found_user_data = await UserService(db).get(connection.user_id)
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

                    user_by_email = await UserService(db).get_by_email(user_info.email)

                    if user_by_email:
                        # M27: Security - prevent account takeover via IdP
                        raise BadRequestException(
                            "An account with this email already exists. "
                            "Please log in using your existing credentials and "
                            "link this provider in your account settings."
                        )
                    else:
                        current_tenant = get_current_tenant()
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

                            set_current_tenant(tenant_orm)

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
            user_svc = UserService(db)
            normalized_user = await user_svc.get_user_auth_info(email=email_val)

            if not normalized_user:
                raise UnauthorizedException("Account not found")

            # SECURITY FIX: Check if MFA is enabled before issuing access token
            from src.app.modules.mfa.service import get_mfa_service

            mfa_service = get_mfa_service()
            mfa_enabled = await mfa_service.is_mfa_enabled(normalized_user["id"], db)

            if mfa_enabled:
                from src.app.core.redis_provider import get_redis
                from src.app.core.security import get_mfa_pending_token_manager
                from src.app.core.security.tokens import TokenGenerator

                mfa_redis_client = get_redis()
                from src.app.core.utils.ip_utils import get_client_ip

                if not mfa_redis_client:
                    raise BadRequestException("Authentication service unavailable")

                mfa_token_store = get_mfa_pending_token_manager(mfa_redis_client)
                mfa_token = TokenGenerator.generate_magic_link_token()
                await mfa_token_store.create(
                    token=mfa_token,
                    data={
                        "user_id": normalized_user["id"],
                        "username": normalized_user.get("username") or normalized_user.get("email"),
                        "ip_address": get_client_ip(request),
                        "scope": "mfa_pending",
                    },
                )
                return {
                    "mfa_required": True,
                    "mfa_token": mfa_token,
                    "expires_in": int(settings.auth.MFA_PENDING_EXPIRY_SECONDS),
                    "message": "MFA verification required",
                }

            # Legacy SSO tokens carried extra id/email claims; kept via
            # extra_claims so existing consumers of those tokens are unaffected.
            issued = await TokenIssuer.issue_session(
                normalized_user,
                channel="sso",
                request=request,
                extra_claims={
                    "id": normalized_user.get("id"),
                    "email": normalized_user.get("email"),
                },
            )

            # Audit log SSO login success
            try:
                user_id_val = found_user_data.id if hasattr(found_user_data, "id") else found_user_data["id"]
                tenant_id_val = (
                    found_user_data.tenant_id
                    if hasattr(found_user_data, "tenant_id")
                    else found_user_data.get("tenant_id")
                )
                email_val = found_user_data.email if hasattr(found_user_data, "email") else found_user_data.get("email")
                username_val = (
                    found_user_data.username
                    if hasattr(found_user_data, "username")
                    else found_user_data.get("username")
                )

                await audit(
                    action="auth.sso_login",
                    entity_type="user",
                    entity_id=user_id_val,
                    tenant_id=tenant_id_val,
                    user_id=user_id_val,
                    details={
                        "provider": provider,
                        "email": email_val,
                        "username": username_val,
                        "success": True,
                    },
                    ip_address=get_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    priority=EventPriority.NORMAL,
                )
            except Exception as e:
                logger.error("failed_to_publish_sso_audit_log", error=str(e))

            return AuthTokenResponse(
                access_token=issued.access_token,
                refresh_token=issued.refresh_token,
                expires_in=issued.expires_in,
                token_type="bearer",
            )
        finally:
            # Restore the tenant context that existed before this call (which may
            # have been set by upstream auth middleware), rather than unconditionally
            # clearing it and discarding the caller's context.
            set_current_tenant(prior_tenant_ctx)

    async def _link_account(self, db: AsyncSession, user_id: int, provider: str, user_info: Any) -> None:
        """Create UserSSOConnection."""
        current_tenant = get_current_tenant()
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
        import secrets
        import string

        from src.app.core.exceptions.http_exceptions import DuplicateValueException

        base_username = user_info.email.split("@")[0]
        # Ensure username is lowercase and alphanumeric
        clean_base = "".join(c for c in base_username if c.isalnum()).lower()

        user_svc = UserService(db)

        # M26: Retry loop for username collisions
        max_retries = 3
        for attempt in range(max_retries):
            random_suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
            username = f"{clean_base}{random_suffix}"[:30]  # Limit length

            # Generate a secure random password that satisfies complexity requirements
            # so that even if the process crashes before the sentinel is set, the account is safe.
            # Ensure at least one of each required character type to pass validation.
            lower = secrets.choice(string.ascii_lowercase)
            upper = secrets.choice(string.ascii_uppercase)
            digit = secrets.choice(string.digits)
            special = secrets.choice("!@#$%^&*")
            chars = string.ascii_letters + string.digits + "!@#$%^&*"
            remaining = [secrets.choice(chars) for _ in range(28)]
            password_chars = [lower, upper, digit, special] + remaining
            secrets.SystemRandom().shuffle(password_chars)
            secure_temp_password = "".join(password_chars)
            user_in = UserCreate(
                email=user_info.email,
                username=username,
                password=secure_temp_password,
                name=user_info.name or base_username,
            )

            try:
                result = await user_svc.create(user=user_in)

                # Overwrite hashed_password with sentinel string so no valid password exists
                await user_svc.crud.update(
                    db=db, id=result.id, object={"hashed_password": "!SSO_ONLY_ACCOUNT_NO_PASSWORD"}, commit=True
                )

                return result
            except DuplicateValueException as e:
                # If email duplicate, it should have been caught earlier or it's a real collision
                if "email" in str(e).lower():
                    raise
                # If username duplicate, try again
                if attempt == max_retries - 1:
                    raise BadRequestException("Failed to generate a unique username. Please try again.")
                logger.warning("Username collision during SSO registration", username=username, attempt=attempt + 1)

    async def get_enabled_providers(self, db: AsyncSession) -> list[dict[str, Any]]:
        """List all available SSO providers with their status."""
        from src.app.modules.sso_settings.service import sso_settings_service

        providers_list = []
        supported_providers = [SSOProvider.GOOGLE, SSOProvider.GITHUB, SSOProvider.MICROSOFT, SSOProvider.APPLE]

        for provider in supported_providers:
            is_enabled = False
            try:
                sso_settings = await sso_settings_service.get_settings_by_provider(db, provider.value)
                is_enabled = sso_settings.is_enabled
            except NotFoundException:
                pass

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

        # Check if user has a real password (SSO-only accounts store a sentinel
        # "!SSO_ONLY_ACCOUNT_NO_PASSWORD" placeholder, which is NOT a valid password).
        user = await UserService(db).get(user_id)
        if user:
            hashed_pw = (
                user.get("hashed_password") if isinstance(user, dict) else getattr(user, "hashed_password", None)
            )
            if hashed_pw and not str(hashed_pw).startswith("!SSO_ONLY"):
                return True

        # Check if user has multiple SSO connections
        connection_count = await crud_user_sso_connections.count_for_user(
            db=db,
            user_id=user_id,
            tenant_id=cast(UUID, tenant_id),
        )

        return connection_count > 1


def get_sso_service() -> "SSOService":
    """Dependency injection factory for SSOService.

    Returns
    -------
    SSOService
        Service instance
    """
    return SSOService()
