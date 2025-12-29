This file is a merged representation of the entire codebase, combined into a single document by Repomix.
The content has been processed where empty lines have been removed, content has been compressed (code blocks are separated by ⋮---- delimiter).

# File Summary

## Purpose

This file contains a packed representation of the entire repository's contents.
It is designed to be easily consumable by AI systems for analysis, code review,
or other automated processes.

## File Format

The content is organized as follows:

1. This summary section
2. Repository information
3. Directory structure
4. Repository files (if enabled)
5. Multiple file entries, each consisting of:
   a. A header with the file path (## File: path/to/file)
   b. The full contents of the file in a code block

## Usage Guidelines

- This file should be treated as read-only. Any changes should be made to the
  original repository files, not this packed version.
- When processing this file, use the file path to distinguish
  between different files in the repository.
- Be aware that this file may contain sensitive information. Handle it with
  the same level of security as you would the original repository.

## Notes

- Some files may have been excluded based on .gitignore rules and Repomix's configuration
- Binary files are not included in this packed representation. Please refer to the Repository Structure section for a complete list of file paths, including binary files
- Files matching patterns in .gitignore are excluded
- Files matching default ignore patterns are excluded
- Empty lines have been removed from all files
- Content has been compressed - code blocks are separated by ⋮---- delimiter
- Files are sorted by Git change count (files with more changes are at the bottom)

# Directory Structure

```
.github/dependabot.yml
.github/workflows/dependabot-auto-merge-minior.yml
.github/workflows/docker.yaml
backend/.dockerignore
backend/.env.example
backend/.gitignore
backend/.python-version
backend/api/__init__.py
backend/api/common/__init__.py
backend/api/common/audit_logs_repository.py
backend/api/common/base_repository.py
backend/api/common/dtos/app_configuration_dto.py
backend/api/common/dtos/cookies.py
backend/api/common/dtos/download_dto.py
backend/api/common/dtos/health_dto.py
backend/api/common/dtos/new_session_dto.py
backend/api/common/dtos/passkey_rep_dto.py
backend/api/common/dtos/token_dto.py
backend/api/common/dtos/worker_dto.py
backend/api/common/enums/gender.py
backend/api/common/exceptions.py
backend/api/common/logging.py
backend/api/common/security.py
backend/api/common/seeder_utils.py
backend/api/common/utils.py
backend/api/core/__init__.py
backend/api/core/config.py
backend/api/core/container.py
backend/api/core/exceptions.py
backend/api/domain/__init__.py
backend/api/domain/dtos/ai_dto.py
backend/api/domain/dtos/audit_logs_dto.py
backend/api/domain/dtos/auth_dto.py
backend/api/domain/dtos/billing_dto.py
backend/api/domain/dtos/checkout_dto.py
backend/api/domain/dtos/coolify_app_dto.py
backend/api/domain/dtos/dashboard_dto.py
backend/api/domain/dtos/email_schema_dto.py
backend/api/domain/dtos/login_dto.py
backend/api/domain/dtos/notification_settings_dto.py
backend/api/domain/dtos/passkey_dto.py
backend/api/domain/dtos/permission_dto.py
backend/api/domain/dtos/pricing_dto.py
backend/api/domain/dtos/product_dto.py
backend/api/domain/dtos/role_dto.py
backend/api/domain/dtos/sso_settings_dto.py
backend/api/domain/dtos/storage_settings_dto.py
backend/api/domain/dtos/stripe_setting_dto.py
backend/api/domain/dtos/subcription_plan_dto.py
backend/api/domain/dtos/tenant_dto.py
backend/api/domain/dtos/user_dto.py
backend/api/domain/dtos/user_preference_dto.py
backend/api/domain/entities/ai.py
backend/api/domain/entities/api_base_model.py
backend/api/domain/entities/notification_settings.py
backend/api/domain/entities/role.py
backend/api/domain/entities/sso_settings.py
backend/api/domain/entities/storage_settings.py
backend/api/domain/entities/stripe_settings.py
backend/api/domain/entities/subscription_plan.py
backend/api/domain/entities/tenant.py
backend/api/domain/entities/user_magic_link.py
backend/api/domain/entities/user_passkey.py
backend/api/domain/entities/user_password_reset.py
backend/api/domain/entities/user_preference.py
backend/api/domain/entities/user.py
backend/api/domain/enum/feature.py
backend/api/domain/enum/permission.py
backend/api/domain/enum/role.py
backend/api/domain/interfaces/background_task.py
backend/api/domain/interfaces/email_service.py
backend/api/domain/security/feature_access_management.py
backend/api/domain/security/user_access_management.py
backend/api/infrastructure/__init__.py
backend/api/infrastructure/background/post_tenant_creation_task_service.py
backend/api/infrastructure/externals/azure_storage.py
backend/api/infrastructure/externals/coolify_app.py
backend/api/infrastructure/externals/dns_resolver.py
backend/api/infrastructure/externals/file_retrieval.py
backend/api/infrastructure/externals/file_upload.py
backend/api/infrastructure/externals/local_ai_model.py
backend/api/infrastructure/externals/s3_storage.py
backend/api/infrastructure/externals/smtp_email.py
backend/api/infrastructure/externals/sso_auth_provider.py
backend/api/infrastructure/externals/stripe_resolver.py
backend/api/infrastructure/messaging/celery_worker.py
backend/api/infrastructure/persistence/__init__.py
backend/api/infrastructure/persistence/mongodb.py
backend/api/infrastructure/persistence/repositories/billing_record_repository_impl.py
backend/api/infrastructure/persistence/repositories/chat_history_ai_repository_impl.py
backend/api/infrastructure/persistence/repositories/chat_session_ai_repository_impl.py
backend/api/infrastructure/persistence/repositories/notification_banner_repository_impl.py
backend/api/infrastructure/persistence/repositories/payment_repository_impl.py
backend/api/infrastructure/persistence/repositories/role_repository_impl.py
backend/api/infrastructure/persistence/repositories/sso_settings_provider_respository_impl.py
backend/api/infrastructure/persistence/repositories/storage_settings_repository_impl.py
backend/api/infrastructure/persistence/repositories/subscription_plan_repository_impl.py
backend/api/infrastructure/persistence/repositories/tenant_repository_impl.py
backend/api/infrastructure/persistence/repositories/user_magic_link_repository_impl.py
backend/api/infrastructure/persistence/repositories/user_passkey_repository_impl.py
backend/api/infrastructure/persistence/repositories/user_password_reset_repository_impl.py
backend/api/infrastructure/persistence/repositories/user_preference_repository_impl.py
backend/api/infrastructure/persistence/repositories/user_repository_impl.py
backend/api/infrastructure/persistence/seeder.py
backend/api/infrastructure/security/current_user.py
backend/api/infrastructure/security/jwt_token_service.py
backend/api/infrastructure/security/passkey_service.py
backend/api/interfaces/__init__.py
backend/api/interfaces/api_controllers/account_endpoint.py
backend/api/interfaces/api_controllers/ai_endpoint.py
backend/api/interfaces/api_controllers/app_configuration_endpoint.py
backend/api/interfaces/api_controllers/audit_logs_endpoint.py
backend/api/interfaces/api_controllers/billing_endpoint.py
backend/api/interfaces/api_controllers/checkout_endpoint.py
backend/api/interfaces/api_controllers/dashboard_endpoint.py
backend/api/interfaces/api_controllers/features_endpoint.py
backend/api/interfaces/api_controllers/health_endpoint.py
backend/api/interfaces/api_controllers/invoice_endpoint.py
backend/api/interfaces/api_controllers/manage_security_endpoint.py
backend/api/interfaces/api_controllers/notification_endpoint.py
backend/api/interfaces/api_controllers/permissions_endpoint.py
backend/api/interfaces/api_controllers/prices_endpoint.py
backend/api/interfaces/api_controllers/product_endpoint.py
backend/api/interfaces/api_controllers/role_endpoint.py
backend/api/interfaces/api_controllers/sso_settings_endpoint.py
backend/api/interfaces/api_controllers/storage_endpoint.py
backend/api/interfaces/api_controllers/stripe_endpoint.py
backend/api/interfaces/api_controllers/tenant_endpoint.py
backend/api/interfaces/api_controllers/user_endpoint.py
backend/api/interfaces/email_templates/activation_template_html.py
backend/api/interfaces/email_templates/dns_notification_html.py
backend/api/interfaces/email_templates/download_template_html.py
backend/api/interfaces/email_templates/email_changes_confirmation_template_html.py
backend/api/interfaces/email_templates/magic_link_login_template_html.py
backend/api/interfaces/email_templates/notify_email_changes_template_html.py
backend/api/interfaces/email_templates/notify_password_changes_template_html.py
backend/api/interfaces/email_templates/password_reset_email_template_html.py
backend/api/interfaces/email_templates/tenant_subdomain_active_html.py
backend/api/interfaces/email_templates/welcome_email_html.py
backend/api/interfaces/middlewares/audit_logs_read_middleware.py
backend/api/interfaces/middlewares/redis_cache_middleware.py
backend/api/interfaces/middlewares/tenant_middleware.py
backend/api/interfaces/security/role_checker.py
backend/api/ui/assets/index-DQtIvVfc.js
backend/api/ui/assets/index-MCH6mY5D.css
backend/api/ui/assets/worker-CgCYKo1M.js
backend/api/ui/index.html
backend/api/ui/vite.svg
backend/api/usecases/audit_logs_service.py
backend/api/usecases/auth_service.py
backend/api/usecases/billing_record_service.py
backend/api/usecases/coolify_app_service.py
backend/api/usecases/file_service.py
backend/api/usecases/local_ai_service.py
backend/api/usecases/magic_link_service.py
backend/api/usecases/notification_banner_service.py
backend/api/usecases/pricing_service.py
backend/api/usecases/product_service.py
backend/api/usecases/role_service.py
backend/api/usecases/sso_settings_service.py
backend/api/usecases/storage_settings_service.py
backend/api/usecases/stripe_setting_service.py
backend/api/usecases/subscription_plan_service.py
backend/api/usecases/tenant_service.py
backend/api/usecases/user_preference_service.py
backend/api/usecases/user_service.py
backend/celerybeat-schedule
backend/Dockerfile
backend/Dockerfile.celery
backend/Makefile
backend/pyproject.toml
backend/pytest.ini
backend/README.md
backend/tests/conftest.py
backend/tests/interfaces/test_role_endpoint.py
backend/tests/interfaces/test_tenant_endpoint.py
backend/tests/interfaces/test_user_endpoint.py
caddy/Caddyfile
CONTRIBUTE.md
demo.gif
dev.compose.yaml
dns.gif
frontend/app/.gitignore
frontend/app/components.json
frontend/app/eslint.config.js
frontend/app/index.html
frontend/app/package.json
frontend/app/public/vite.svg
frontend/app/README.md
frontend/app/src/api/ApiClient.ts
frontend/app/src/api/core/ApiError.ts
frontend/app/src/api/core/ApiRequestOptions.ts
frontend/app/src/api/core/ApiResult.ts
frontend/app/src/api/core/BaseHttpRequest.ts
frontend/app/src/api/core/CancelablePromise.ts
frontend/app/src/api/core/FetchHttpRequest.ts
frontend/app/src/api/core/OpenAPI.ts
frontend/app/src/api/core/request.ts
frontend/app/src/api/index.ts
frontend/app/src/api/models/AIAskRequestDto.ts
frontend/app/src/api/models/AIHistoriesDto.ts
frontend/app/src/api/models/AIHistoryDto.ts
frontend/app/src/api/models/AIModelInfoDto.ts
frontend/app/src/api/models/AISessionByUserIdDto.ts
frontend/app/src/api/models/AppConfigurationDto.ts
frontend/app/src/api/models/AuditLogDto.ts
frontend/app/src/api/models/AuditLogListDto.ts
frontend/app/src/api/models/AuthenticatorTransport.ts
frontend/app/src/api/models/AvailableStorageProviderDto.ts
frontend/app/src/api/models/BillingRecordDto.ts
frontend/app/src/api/models/BillingRecordListDto.ts
frontend/app/src/api/models/Body_login_api_v1_account_login_post.ts
frontend/app/src/api/models/Body_passkey_login_api_v1_account_passkey_login_post.ts
frontend/app/src/api/models/Body_passkey_register_api_v1_account_passkey_register_post.ts
frontend/app/src/api/models/Body_update_profile_picture_api_v1_users__user_id__update_profile_picture_put.ts
frontend/app/src/api/models/ChangeEmailConfirmRequestDto.ts
frontend/app/src/api/models/ChangeEmailRequestDto.ts
frontend/app/src/api/models/ChangeEmailResponseDto.ts
frontend/app/src/api/models/CheckoutRequestDto.ts
frontend/app/src/api/models/CheckoutResponseDto.ts
frontend/app/src/api/models/CreateNotificationBannerSettingDto.ts
frontend/app/src/api/models/CreatePlanDto.ts
frontend/app/src/api/models/CreatePricingDto.ts
frontend/app/src/api/models/CreateProductDto.ts
frontend/app/src/api/models/CreateRoleDto.ts
frontend/app/src/api/models/CreateRoleResponseDto.ts
frontend/app/src/api/models/CreateSSOSettingsDto.ts
frontend/app/src/api/models/CreateStripeSettingDto.ts
frontend/app/src/api/models/CreateTenantDto.ts
frontend/app/src/api/models/CreateTenantResponseDto.ts
frontend/app/src/api/models/CreateUserDto.ts
frontend/app/src/api/models/CreateUserResponseDto.ts
frontend/app/src/api/models/DashboardMetricsDto.ts
frontend/app/src/api/models/DownloadAuditRequestDto.ts
frontend/app/src/api/models/DownloadResponseDto.ts
frontend/app/src/api/models/Feature.ts
frontend/app/src/api/models/FeatureDto.ts
frontend/app/src/api/models/Gender.ts
frontend/app/src/api/models/HasPasskeysDto.ts
frontend/app/src/api/models/HealthCheckResponseDto.ts
frontend/app/src/api/models/HTTPValidationError.ts
frontend/app/src/api/models/InvoiceDto.ts
frontend/app/src/api/models/InvoiceListDto.ts
frontend/app/src/api/models/MagicLinkResponseDto.ts
frontend/app/src/api/models/MeResponseDto.ts
frontend/app/src/api/models/NewSessionResponseDto.ts
frontend/app/src/api/models/NotificationBannerSettingDto.ts
frontend/app/src/api/models/PasswordResetConfirmRequestDto.ts
frontend/app/src/api/models/PasswordResetRequestDto.ts
frontend/app/src/api/models/PasswordResetResponseDto.ts
frontend/app/src/api/models/Permission.ts
frontend/app/src/api/models/PermissionDto.ts
frontend/app/src/api/models/PlanDto.ts
frontend/app/src/api/models/PlanListDto.ts
frontend/app/src/api/models/PricingDto.ts
frontend/app/src/api/models/PricingListDto.ts
frontend/app/src/api/models/ProductDto.ts
frontend/app/src/api/models/ProductListDto.ts
frontend/app/src/api/models/PydanticObjectId.ts
frontend/app/src/api/models/ReadSSOSettingsDto.ts
frontend/app/src/api/models/RecurringDto.ts
frontend/app/src/api/models/RegisteredPasskeyCredentialsDto.ts
frontend/app/src/api/models/RoleDto.ts
frontend/app/src/api/models/RoleListDto.ts
frontend/app/src/api/models/SSOSettingsListDto.ts
frontend/app/src/api/models/SSOSettingsResponseDto.ts
frontend/app/src/api/models/StorageSettingsDto.ts
frontend/app/src/api/models/StripeSettingDto.ts
frontend/app/src/api/models/SubdomainAvailabilityDto.ts
frontend/app/src/api/models/SubscriptionPlanDto.ts
frontend/app/src/api/models/TenantDto.ts
frontend/app/src/api/models/TenantListDto.ts
frontend/app/src/api/models/TimeSeriesDto.ts
frontend/app/src/api/models/TokenSetDto.ts
frontend/app/src/api/models/UpdatePlanDto.ts
frontend/app/src/api/models/UpdatePricingDto.ts
frontend/app/src/api/models/UpdateRoleDto.ts
frontend/app/src/api/models/UpdateSSOSettingsDto.ts
frontend/app/src/api/models/UpdateTenantDto.ts
frontend/app/src/api/models/UpdateTenantResponseDto.ts
frontend/app/src/api/models/UpdateUserDto.ts
frontend/app/src/api/models/UserActivationRequestDto.ts
frontend/app/src/api/models/UserDto.ts
frontend/app/src/api/models/UserListDto.ts
frontend/app/src/api/models/UserPreferenceDto.ts
frontend/app/src/api/models/UserProfileImageUpdateDto.ts
frontend/app/src/api/models/UserResendActivationEmailRequestDto.ts
frontend/app/src/api/models/UserRoleUpdateRequestDto.ts
frontend/app/src/api/models/ValidationError.ts
frontend/app/src/api/services/AccountService.ts
frontend/app/src/api/services/AiService.ts
frontend/app/src/api/services/AppConfigurationService.ts
frontend/app/src/api/services/AuditLogsService.ts
frontend/app/src/api/services/DashboardService.ts
frontend/app/src/api/services/FeaturesService.ts
frontend/app/src/api/services/HealthService.ts
frontend/app/src/api/services/ManageSecurityService.ts
frontend/app/src/api/services/NotificationsService.ts
frontend/app/src/api/services/PermissionsService.ts
frontend/app/src/api/services/RolesService.ts
frontend/app/src/api/services/SsoSettingsService.ts
frontend/app/src/api/services/StorageSettingsService.ts
frontend/app/src/api/services/StripeBillingService.ts
frontend/app/src/api/services/StripeCheckoutService.ts
frontend/app/src/api/services/StripeInvoicesService.ts
frontend/app/src/api/services/StripePricingService.ts
frontend/app/src/api/services/StripeProductsService.ts
frontend/app/src/api/services/StripeService.ts
frontend/app/src/api/services/TenantsService.ts
frontend/app/src/api/services/UsersService.ts
frontend/app/src/App.tsx
frontend/app/src/assets/logo-teal.png
frontend/app/src/assets/react.svg
frontend/app/src/components/features/ai-chat/ai-chat-history.tsx
frontend/app/src/components/features/ai-chat/ai-chat.tsx
frontend/app/src/components/features/audit-logs/audit-logs.tsx
frontend/app/src/components/features/audit-logs/audit-table.tsx
frontend/app/src/components/features/auth/activation.tsx
frontend/app/src/components/features/auth/login.tsx
frontend/app/src/components/features/auth/magic-link-login-validate.tsx
frontend/app/src/components/features/auth/magic-link-login.tsx
frontend/app/src/components/features/auth/passkey-login.tsx
frontend/app/src/components/features/auth/password_reset_confirmation.tsx
frontend/app/src/components/features/auth/password-reset-request.tsx
frontend/app/src/components/features/auth/register.tsx
frontend/app/src/components/features/auth/sso-login-dialog.tsx
frontend/app/src/components/features/billings/stripe/add-a-new-plan.tsx
frontend/app/src/components/features/billings/stripe/billing-overview.tsx
frontend/app/src/components/features/billings/stripe/billing.tsx
frontend/app/src/components/features/billings/stripe/checkout-canceled.tsx
frontend/app/src/components/features/billings/stripe/checkout-overview.tsx
frontend/app/src/components/features/billings/stripe/checkout-success.tsx
frontend/app/src/components/features/billings/stripe/configure-stripe-now.tsx
frontend/app/src/components/features/billings/stripe/configure-stripe.tsx
frontend/app/src/components/features/billings/stripe/invoices.tsx
frontend/app/src/components/features/billings/stripe/products.tsx
frontend/app/src/components/features/dark-mode/dark-mode.tsx
frontend/app/src/components/features/dashboard/all-cards.tsx
frontend/app/src/components/features/dashboard/dashboard.tsx
frontend/app/src/components/features/profile/profile.tsx
frontend/app/src/components/features/roles/create-new-role-dialog.tsx
frontend/app/src/components/features/roles/role-delete-dialog.tsx
frontend/app/src/components/features/roles/role-edit-dialog.tsx
frontend/app/src/components/features/roles/role-manage-permission-dialog.tsx
frontend/app/src/components/features/roles/role-table.tsx
frontend/app/src/components/features/roles/roles.tsx
frontend/app/src/components/features/settings/app-settings.tsx
frontend/app/src/components/features/settings/notification-banner.tsx
frontend/app/src/components/features/settings/settings-layout.tsx
frontend/app/src/components/features/settings/settings.tsx
frontend/app/src/components/features/sso-configurations/create-new-sso-dialog.tsx
frontend/app/src/components/features/sso-configurations/delete-sso-configuration-dialog.tsx
frontend/app/src/components/features/sso-configurations/sso-configuration-edit-dialog.tsx
frontend/app/src/components/features/sso-configurations/sso-provider-configuration.tsx
frontend/app/src/components/features/tenant/create-new-tenant-dialog.tsx
frontend/app/src/components/features/tenant/manage-feature.tsx
frontend/app/src/components/features/tenant/manage-tenant-subscription.tsx
frontend/app/src/components/features/tenant/tenant-delete-dialog.tsx
frontend/app/src/components/features/tenant/tenant-selection.tsx
frontend/app/src/components/features/tenant/tenant-setting.tsx
frontend/app/src/components/features/tenant/tenant-table.tsx
frontend/app/src/components/features/tenant/tenant.tsx
frontend/app/src/components/features/users/create-new-user-dialog.tsx
frontend/app/src/components/features/users/user-delete-dialog.tsx
frontend/app/src/components/features/users/user-edit-dialog.tsx
frontend/app/src/components/features/users/user-table.tsx
frontend/app/src/components/features/users/users.tsx
frontend/app/src/components/layouts/dashboard-layout.tsx
frontend/app/src/components/layouts/dashboard-sidebar-layout.tsx
frontend/app/src/components/layouts/default-layout.tsx
frontend/app/src/components/providers/ai-chat-provider.tsx
frontend/app/src/components/providers/app-config-provider.tsx
frontend/app/src/components/providers/audit-log-provider.tsx
frontend/app/src/components/providers/auth-provider.tsx
frontend/app/src/components/providers/roles-provider.tsx
frontend/app/src/components/providers/settings-provider.tsx
frontend/app/src/components/providers/sso-configuration-provider.tsx
frontend/app/src/components/providers/stripe-provider.tsx
frontend/app/src/components/providers/tenant-provider.tsx
frontend/app/src/components/providers/theme-provider.tsx
frontend/app/src/components/providers/users-provider.tsx
frontend/app/src/components/shared/advance-table.tsx
frontend/app/src/components/shared/banner.tsx
frontend/app/src/components/shared/copy-text.tsx
frontend/app/src/components/shared/feature-disabled.tsx
frontend/app/src/components/shared/footer.tsx
frontend/app/src/components/shared/iresponse-data.inteface.ts
frontend/app/src/components/shared/list-local-ai-models.tsx
frontend/app/src/components/shared/list-permission.tsx
frontend/app/src/components/shared/loading.tsx
frontend/app/src/components/shared/logo-variants.tsx
frontend/app/src/components/shared/logo.tsx
frontend/app/src/components/shared/manage-security.tsx
frontend/app/src/components/shared/manage-user-roles.tsx
frontend/app/src/components/shared/no-permission-access-resource.tsx
frontend/app/src/components/shared/non-active-tenant-view.tsx
frontend/app/src/components/shared/page-header.tsx
frontend/app/src/components/shared/show-screen-loader.tsx
frontend/app/src/components/shared/simple-footer.tsx
frontend/app/src/components/shared/table-actions.tsx
frontend/app/src/components/ui/accordion.tsx
frontend/app/src/components/ui/alert-dialog.tsx
frontend/app/src/components/ui/alert.tsx
frontend/app/src/components/ui/aspect-ratio.tsx
frontend/app/src/components/ui/avatar.tsx
frontend/app/src/components/ui/badge.tsx
frontend/app/src/components/ui/breadcrumb.tsx
frontend/app/src/components/ui/button-group.tsx
frontend/app/src/components/ui/button.tsx
frontend/app/src/components/ui/calendar.tsx
frontend/app/src/components/ui/card.tsx
frontend/app/src/components/ui/carousel.tsx
frontend/app/src/components/ui/chart.tsx
frontend/app/src/components/ui/checkbox.tsx
frontend/app/src/components/ui/collapsible.tsx
frontend/app/src/components/ui/command.tsx
frontend/app/src/components/ui/context-menu.tsx
frontend/app/src/components/ui/dialog.tsx
frontend/app/src/components/ui/drawer.tsx
frontend/app/src/components/ui/dropdown-menu.tsx
frontend/app/src/components/ui/empty.tsx
frontend/app/src/components/ui/field.tsx
frontend/app/src/components/ui/form.tsx
frontend/app/src/components/ui/hover-card.tsx
frontend/app/src/components/ui/input-group.tsx
frontend/app/src/components/ui/input-otp.tsx
frontend/app/src/components/ui/input.tsx
frontend/app/src/components/ui/item.tsx
frontend/app/src/components/ui/kbd.tsx
frontend/app/src/components/ui/label.tsx
frontend/app/src/components/ui/menubar.tsx
frontend/app/src/components/ui/navigation-menu.tsx
frontend/app/src/components/ui/pagination.tsx
frontend/app/src/components/ui/popover.tsx
frontend/app/src/components/ui/progress.tsx
frontend/app/src/components/ui/radio-group.tsx
frontend/app/src/components/ui/resizable.tsx
frontend/app/src/components/ui/scroll-area.tsx
frontend/app/src/components/ui/select.tsx
frontend/app/src/components/ui/separator.tsx
frontend/app/src/components/ui/sheet.tsx
frontend/app/src/components/ui/sidebar.tsx
frontend/app/src/components/ui/skeleton.tsx
frontend/app/src/components/ui/slider.tsx
frontend/app/src/components/ui/sonner.tsx
frontend/app/src/components/ui/spinner.tsx
frontend/app/src/components/ui/switch.tsx
frontend/app/src/components/ui/table.tsx
frontend/app/src/components/ui/tabs.tsx
frontend/app/src/components/ui/textarea.tsx
frontend/app/src/components/ui/toggle-group.tsx
frontend/app/src/components/ui/toggle.tsx
frontend/app/src/components/ui/tooltip.tsx
frontend/app/src/hooks/use-debounce.ts
frontend/app/src/hooks/use-feature-check.ts
frontend/app/src/hooks/use-mobile.ts
frontend/app/src/hooks/use-profile-image.ts
frontend/app/src/hooks/use-subdomain-check.ts
frontend/app/src/index.css
frontend/app/src/lib/image-utils.ts
frontend/app/src/lib/utils.ts
frontend/app/src/main.tsx
frontend/app/src/vite-env.d.ts
frontend/app/src/worker.ts
frontend/app/tsconfig.app.json
frontend/app/tsconfig.json
frontend/app/tsconfig.node.json
frontend/app/vite.config.ts
fsrapp.code-workspace
infra/helm/Chart.yaml
infra/helm/templates/_helpers.tpl
infra/helm/templates/deployment.yaml
infra/helm/templates/hpa.yaml
infra/helm/templates/ingress.yaml
infra/helm/templates/mongodb.yaml
infra/helm/templates/secret.yaml
infra/helm/templates/service.yaml
infra/helm/templates/serviceaccount.yaml
infra/helm/values.yaml
infra/README.md
LICENSE
README.md
```

# Files

## File: .github/dependabot.yml

```yaml
version: 2
updates:
  # --- 1. GitHub Actions ---
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
      include: "scope"
    open-pull-requests-limit: 10
    rebase-strategy: "auto"
  # --- 2. Backend (FastAPI / uv) ---
  # Dependabot treats uv as pip for now
  - package-ecosystem: "uv"
    directory: "/backend"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
      include: "scope"
    open-pull-requests-limit: 10
    rebase-strategy: "auto"
  # --- 3. Frontend (React / pnpm) ---
  - package-ecosystem: "npm"
    directory: "/frontend/app"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
      include: "scope"
    open-pull-requests-limit: 10
    rebase-strategy: "auto"
```

## File: .github/workflows/dependabot-auto-merge-minior.yml

```yaml
name: Dependabot Auto Merge Minor and Patch Updates
on:
  pull_request_target:
    types:
      - opened
      - reopened
      - synchronize
permissions:
  contents: write
  pull-requests: write
jobs:
  auto-merge:
    if: github.actor == 'dependabot[bot]'
    runs-on: ubuntu-latest
    steps:
      - name: Dependabot metadata
        id: metadata
        uses: dependabot/fetch-metadata@v2
        with:
          github-token: "${{ secrets.GITHUB_TOKEN }}"
      - name: Auto-merge minor and patch updates
        if: |
          steps.metadata.outputs.update-type == 'version-update:semver-minor' ||
          steps.metadata.outputs.update-type == 'version-update:semver-patch'
        run: gh pr merge --auto --merge "$PR_URL"
        env:
          PR_URL: ${{ github.event.pull_request.html_url }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## File: .github/workflows/docker.yaml

```yaml
name: Publish a Docker Image
on:
  release:
    types: [published]
env:
  REGISTRY: docker.io
  IMAGE_NAME: sajandev/fsrapp
jobs:
  build-and-push-fastapi:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v5
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            # Release tags
            type=ref,event=tag
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            # Latest tag
            type=raw,value=latest
          labels: |
            org.opencontainers.image.title=Full-Stack FastAPI React Template
            org.opencontainers.image.description=Production-ready FastAPI backend with React frontend
            org.opencontainers.image.vendor=sajandev
      - name: Build and push Docker image
        id: build
        uses: docker/build-push-action@v6
        with:
          context: ./backend
          file: ./backend/Dockerfile
          platforms: linux/amd64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          # Add build args for version info
          build-args: |
            VERSION=${{ github.ref_name }}
            BUILD_DATE=${{ steps.meta.outputs.labels['org.opencontainers.image.created'] }}
            VCS_REF=${{ github.sha }}
      - name: Show build results
        run: |
          echo "Image digest: ${{ steps.build.outputs.digest }}"
          echo "Tags created:"
          echo "${{ steps.meta.outputs.tags }}"
          echo "Labels applied:"
          echo "${{ steps.meta.outputs.labels }}"
      - name: Trigger deployment fsrapp
        run: |
          echo "Triggering deployment via Coolify webhook"
          curl --request GET "${{ secrets.COOLIFY_WEBHOOK_URI }}" --header "Authorization: Bearer ${{ secrets.COOLIFY_DEPLOY_KEY }}"
          echo "Deployment triggered."
  push-fsrapp-worker-celery:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v5
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - name: Extract metadata for Celery
        id: meta-celery
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/sajandev/fsrappworker
          tags: |
            # Release tags
            type=ref,event=tag
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            # Latest tag
            type=raw,value=latest
          labels: |
            org.opencontainers.image.title=Full-Stack FastAPI React Template - Celery Worker
            org.opencontainers.image.description=Celery worker for background tasks
            org.opencontainers.image.vendor=sajandev
      - name: Build and push Celery Docker image
        id: build-celery
        uses: docker/build-push-action@v6
        with:
          context: ./backend
          file: ./backend/Dockerfile.celery
          platforms: linux/amd64
          push: true
          tags: ${{ steps.meta-celery.outputs.tags }}
          labels: ${{ steps.meta-celery.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          # Add build args for version info
          build-args: |
            VERSION=${{ github.ref_name }}
            BUILD_DATE=${{ steps.meta-celery.outputs.labels['org.opencontainers.image.created'] }}
            VCS_REF=${{ github.sha }}
      - name: Show Celery build results
        run: |
          echo "Celery image digest: ${{ steps.build-celery.outputs.digest }}"
          echo "Celery tags created:"
          echo "${{ steps.meta-celery.outputs.tags }}"
      - name: Trigger deployment fsrappworker
        run: |
          echo "Triggering deployment via Coolify webhook"
          curl --request GET "${{ secrets.COOLIFY_FSRAPP_WORKER_WEBHOOK_URI }}" --header "Authorization: Bearer ${{ secrets.COOLIFY_DEPLOY_KEY }}"
          echo "Deployment triggered."
```

## File: backend/.dockerignore

```
# Testing files
tests/
.pytest_cache/
pytest.ini
.coverage
htmlcov/
.tox/

# Python cache
__pycache__/
*.py[cod]
*$py.class
*.pyc
*.pyo
*.pyd

# Development files
.env
.env.*
.env.example

# Version control
.git/
.gitignore
README.md

# Virtual environments
.venv/
venv/
ENV/
env/

# IDE files
.vscode/
.idea/
*.swp
*.swo

# Logs and temporary files
*.log
logs/
*.tmp
*.temp

# OS files
.DS_Store
Thumbs.db

# Documentation
docs/
*.md

# Build artifacts
build/
dist/
*.egg-info/

# Docker files
Dockerfile*
docker-compose*.yml
.dockerignore
```

## File: backend/.env.example

```
# MongoDB settings
MONGO_URI=mongodb://127.0.0.1:27012
MONGO_DB_NAME=full_stack_fastapi_react_template

# JWT settings
JWT_SECRET=your_jwt_secret_key
REFRESH_TOKEN_SECRET=your_refresh_jwt_secret_key

# Configure your default app admin credentials.
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Test@123!

# SMTP Email settings
SMTP_HOST="localhost"
SMTP_PORT=1023
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_MAIL_FROM="noreply@example.com"
SMTP_MAIL_FROM_NAME="Full-Stack Fast API"
SMTP_STARTTLS=False
SMTP_SSL_TLS=False

# Redis settings

REDIS_URI="redis://localhost:6372/0"

# App configuration
APP_TITLE="Full-Stack FastAPI React Template"
APP_VERSION="0.1.0"

API_ENDPOINT_BASE="http://localhost:8000/api"

# Configure Multi-Tenancy
MULTI_TENANCY_STRATEGY="header"  # Options: "header", "subdomain", "none"

# Host main domain name
HOST_MAIN_DOMAIN="fsrapp.com"

# Ollama settings
OLLAMA_HOST="http://localhost:11434"


# Coolify settings for deployment
COOLIFY_ENABLED="false" # Set to "true" to enable Coolify integration
COOLIFY_API_URL="https://{replace_with_your_coolify_instance_endpoint}/api/v1"
COOLIFY_API_KEY="{replace_with_your_coolify_api_key}" # Read here https://coolify.io/docs/api-reference/authorization
COOLIFY_APPLICATION_ID="{replace_with_your_coolify_application_id}" # Read here https://coolify.io/docs/api-reference/api/operations/get-application-by-uuid

# Default aws s3 settings for file uploads this belongs to the Host
AWS_REGION="eu-central-1"
AWS_ACCESS_KEY_ID="Ab..."
AWS_SECRET_ACCESS_KEY="xxe..."
AWS_S3_BUCKET_NAME="fs.."

# Stripe credentials settings
STRIPE_API_KEY="fg_test..."
STRIPE_PUBLISHABLE_KEY="ks_test...."
STRIPE_SECRET_KEY="fs_test...."
STRIPE_WEBHOOK_SECRET="whsec_..."
```

## File: backend/.gitignore

```
venv/
__pycache__/
*.pyc
*.pyo
*.pyd
.env
```

## File: backend/.python-version

```
3.13
```

## File: backend/api/**init**.py

```python
build_path = os.path.join(os.path.dirname(__file__), "ui")
db: Database = container.resolve(Database)
logger = get_logger(__name__)
⋮----
@asynccontextmanager
async def lifespan(app: FastAPI)
⋮----
# initialization database with application-wide models Typically the host database
⋮----
# Shutdown code
⋮----
app = FastAPI(
⋮----
@app.exception_handler(ApiBaseException)
async def api_exception_handler(req: Request, ex: ApiBaseException) -> JSONResponse
⋮----
status_code = status.HTTP_400_BAD_REQUEST
⋮----
status_code = status.HTTP_404_NOT_FOUND
⋮----
status_code = status.HTTP_409_CONFLICT
⋮----
status_code = status.HTTP_401_UNAUTHORIZED
⋮----
status_code = status.HTTP_406_NOT_ACCEPTABLE
⋮----
# Mount static files only if they exist (for production Docker deployment)
⋮----
router = APIRouter(prefix="/api/v1")
⋮----
# Catch-all route for React Router (only in production with static files)
⋮----
@app.get("/{full_path:path}", include_in_schema=False)
async def react_router(full_path: str)
```

## File: backend/api/common/**init**.py

```python

```

## File: backend/api/common/audit_logs_repository.py

```python
logger = get_logger(__name__)
class AuditLogRepository
⋮----
async def _get_files_matching_pattern(self, tenant_id: str | None = None) -> List[str]
⋮----
file_path_pattern = "/tmp/audit_logs_forhost_*.jsonl"
⋮----
file_path_pattern = f"/tmp/audit_logs_for{tenant_id}_*.jsonl"
⋮----
files = glob.glob(file_path_pattern)
# IMPORTANT: scan oldest → newest so the deque ends up with the last K lines
# If your filenames end with YYYY-MM-DD, name sort works for chronological asc.
files.sort()  # ascending
⋮----
async def add_audit_log(self, audit_log: AuditLogDto) -> None
async def list_audit_logs(self, tenant_id: str | None = None, limit: int = 10, skip: int = 0, action: Optional[AuditActionType] = None) -> AuditLogListDto
⋮----
files = await self._get_files_matching_pattern(tenant_id=tenant_id)
total = 0
keep = skip + limit
ring = deque(maxlen=keep) # keep the last (skip + limit) lines
⋮----
obj = json.loads(line)
⋮----
ring.append(obj)  # older ones drop automatically when full
⋮----
# Now ring holds the last (skip+limit) lines globally (latest lines overall)
# Convert to newest-first
newest_first = list(ring)[::-1]
# Slice for pagination
sliced = newest_first[skip:skip + limit]
# Parse JSON and map to DTOs
logs: List[AuditLogDto] = [AuditLogDto(**o) for o in sliced]
⋮----
async def return_all_audit_logs(self, action: Optional[AuditActionType] = None, tenant_id: str | None = None) -> List[AuditLogDto]
⋮----
results: List[AuditLogDto] = []
⋮----
return results[::-1]  # return newest first
```

## File: backend/api/common/base_repository.py

```python
logger = get_logger(__name__)
T = TypeVar("T", bound=Document)
class BaseRepository(Generic[T])
⋮----
def __init__(self, model: type[T])
async def single_or_none(self, **kwargs) -> Optional[T]
⋮----
result = await self.model.find(kwargs).first_or_none()
⋮----
async def get(self, id: str) -> Optional[T]
⋮----
result = await self.model.find_one({"_id": PydanticObjectId(id)})
⋮----
async def list(self) -> List[T]
⋮----
results = await self.model.find_all().to_list()
⋮----
async def search(self, query: Dict[str, Any], limit: int = 100) -> List[T]
⋮----
results = await self.model.find(query).to_list(length=limit)
⋮----
async def create(self, data: dict) -> T
⋮----
doc = self.model(**data)
result = await doc.insert()
⋮----
async def update(self, id: str, data: dict) -> Optional[T]
⋮----
doc = await self.get(id)
⋮----
async def delete(self, id: str) -> bool
async def count(self, params: Optional[Any] | None = None) -> int
async def aggregate(self, pipeline: List[dict], projection_model: type[DocumentProjectionType] | None = None) -> type[DocumentProjectionType] | AggregationQuery[Dict[str, Any]]
⋮----
data = self.model.aggregate(pipeline, projection_model=projection_model)
⋮----
async def collection_name(self) -> str
```

## File: backend/api/common/dtos/app_configuration_dto.py

```python
class AppConfigurationDto(BaseModel)
⋮----
is_multi_tenant_enabled: bool
multi_tenancy_strategy: str  # "subdomain" or "header"
host_main_domain: str
is_user_logged_in: Optional[bool] = False
user_preferences: Optional[UserPreferenceDto] = None
current_tenant: TenantDto | None = None
environment: str
enabled_sso_providers: Optional[list[str]] = None
```

## File: backend/api/common/dtos/cookies.py

```python
class Cookies(BaseModel)
⋮----
refresh_token: Optional[str] | None = None
# Add more session Id, facebook_tracking etc..
```

## File: backend/api/common/dtos/download_dto.py

```python
class DownloadResponseDto(BaseModel)
⋮----
message: str
```

## File: backend/api/common/dtos/health_dto.py

```python
class HealthCheckResponseDto(BaseModel)
⋮----
status: str = Field(..., example="Ok")
```

## File: backend/api/common/dtos/new_session_dto.py

```python
class NewSessionResponseDto(BaseModel)
⋮----
session_id: str
```

## File: backend/api/common/dtos/passkey_rep_dto.py

```python
class PasskeyRepDto(BaseModel)
⋮----
rp_id: str
rp_name: str
class HasPasskeysDto(BaseModel)
⋮----
has_passkeys: bool
```

## File: backend/api/common/dtos/token_dto.py

```python
logger = get_logger(__name__)
class TokenPayloadDto(BaseModel)
⋮----
sub: PydanticObjectId
email: EmailStr
is_active: bool
activated_at: datetime | None = None
tenant_id: PydanticObjectId | None = None
role: RoleDto | None = None
⋮----
@field_serializer('sub', 'tenant_id', "activated_at")
    def serialize_object_id(self, v: PydanticObjectId | datetime | None) -> str | None
class RefreshTokenPayloadDto(BaseModel)
⋮----
tenant_id: PydanticObjectId | None  = None
type: Literal["refresh"] = "refresh"
⋮----
@field_serializer('sub', 'tenant_id')
    def serialize_object_id(self, v: PydanticObjectId | None) -> str | None
class AccessTokenDto(BaseModel)
⋮----
access_token: str
token_type: str
expires_in: datetime
class RefreshTokenDto(BaseModel)
⋮----
refresh_token: str
refresh_token_expires_in: datetime
class TokenSetDto(AccessTokenDto, RefreshTokenDto)
⋮----
expires_in: int
refresh_token_expires_in: int
⋮----
class TokenRefreshRequestDto(BaseModel)
class ActivationTokenPayloadDto(BaseModel)
⋮----
user_id: str
⋮----
tenant_id: str | None = None
type: Literal["activation", "password_reset_confirmation", "change_email_confirmation"] = "activation",
jwt_secret: str | None = None
class VerifyEmailTokenPayloadDto(BaseModel)
```

## File: backend/api/common/dtos/worker_dto.py

```python
T = TypeVar("T", bound=BaseModel)
class WorkerPayloadDto(BaseModel, Generic[T])
⋮----
label: Literal["post-tenant-creation", "email-sending", "post-delete-cleanup", "post-tenant-deletion", "update-tenant-dns"] = "email-sending"
data: T | None = None
tenant_id: str | None = None
```

## File: backend/api/common/enums/gender.py

```python
class Gender(str, Enum)
⋮----
MALE = "male"
FEMALE = "female"
OTHER = "other"
PREFER_NOT_TO_SAY = "prefer_not_to_say"
```

## File: backend/api/common/exceptions.py

```python
# Keep generic exceptions here
class ApiBaseException(Exception)
⋮----
"""Base exception for the app."""
def __init__(self, message: str)
class NotFoundException(ApiBaseException)
⋮----
"""Entity not found."""
def __init__(self, entity: str, identifier: str)
⋮----
message = f"{entity} with identifier {identifier} not found."
⋮----
class ConflictException(ApiBaseException)
⋮----
"""Entity already exists / conflict."""
⋮----
message = f"{entity} with identifier {identifier} already exists."
⋮----
class UnauthorizedException(ApiBaseException)
⋮----
"""Unauthorized or forbidden action."""
⋮----
class InvalidOperationException(ApiBaseException)
⋮----
"""Invalid input or operation."""
⋮----
class ForbiddenException(ApiBaseException)
⋮----
"""Forbidden action."""
⋮----
class AuthenticationFailedException(ApiBaseException)
⋮----
"""Authentication failed."""
def __init__(self, message: str = "Authentication failed.")
class AwsException(ApiBaseException)
class AzureException(ApiBaseException)
```

## File: backend/api/common/logging.py

```python
def configure_logging() -> None
⋮----
# "class": "logging.Formatter",
```

## File: backend/api/common/security.py

```python
JWT_SECRET = settings.jwt_secret
REFRESH_TOKEN_SECRET = settings.refresh_token_secret
ALGORITHM = settings.algorithm
REFRESH_ALGORITHM = settings.refresh_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days
ACTIVATION_TOKEN_EXPIRE_HOURS = 24  # Token expires in 24 hours
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/account/login", refreshUrl="api/v1/account/refresh", auto_error=False)
tenant_header = APIKeyHeader(name="x-tenant-id", auto_error=False)
def verify_password(plain_password: str, hashed_password: str) -> bool
def hash_it(password: str) -> str
# Utility function to hash and validate values (like tokens) with secret. Don't use for passwords.
def validate_hashed_value(value: str, hashed_value: str) -> bool
⋮----
"""Validate a value against its hashed version. Returns True if they match, False otherwise."""
v = value + JWT_SECRET
```

## File: backend/api/common/seeder_utils.py

```python
class Role(BaseModel)
⋮----
name: RoleType
description: str
permissions: List[Permission] = Field(default_factory=list)
def get_seed_roles() -> List[Role]
⋮----
"""Returns a list of default roles to be seeded into the database for a new tenant."""
roles = [
```

## File: backend/api/common/utils.py

```python
def get_utc_now()
def get_logger(name: str) -> logging.Logger
def is_tenancy_enabled() -> bool
def get_tenancy_strategy() -> str
def get_host_main_domain_name() -> str
def get_app_environment() -> str
def get_app_title() -> str
def is_production_environment() -> bool
def is_development_environment() -> bool
PASSWORD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]")
def validate_password(password: str) -> str
⋮----
"""Validate password strength. Raises InvalidOperationException if invalid."""
⋮----
def get_email_sharing_link(user_id: str, type: str, token: str, domain: str, tenant_id: Optional[str] = None) -> str
⋮----
"""
    Generate a complete email sharing link with token.
    """
sharing_link = f"https://{domain}/{type}?user_id={user_id}&token={token}"
⋮----
def get_date_range(filter_type: str)
⋮----
"""Return (start, end, group_format) based on filter type"""
now = get_utc_now()
⋮----
start = datetime(now.year, now.month, now.day)
group_format = "%H:00"  # group by hour
⋮----
start = now - timedelta(days=now.weekday())  # Monday start
start = datetime(start.year, start.month, start.day)
group_format = "%Y-%m-%d"  # group by day
⋮----
start = now - timedelta(days=90)
group_format = "%Y-%m"  # group by month
⋮----
start = None
group_format = "%Y-%m-%d"
⋮----
def format_bytes_to_human_readable_size(size_in_bytes: int) -> str
⋮----
"""Convert bytes into a human-readable format (KB, MB, GB, TB)."""
⋮----
def is_subdomain(host: str) -> bool
⋮----
"""Check if the given host is a subdomain of the main domain."""
main_domain = get_host_main_domain_name().lower()
host = host.lower()
⋮----
def create_temp_file(file_name: str) -> Path
⋮----
"""Create a temporary file and return its path."""
temp_dir = Path(tempfile.gettempdir())
path = temp_dir / file_name
# Ensure directory exists (temp directories always exist, but safe to include)
⋮----
"""Capture an audit log entry."""
file_name = f"audit_logs_for{log_data.tenant_id if log_data.tenant_id else 'host'}_{get_utc_now().strftime('%Y%m%d')}.jsonl"
file_path = create_temp_file(file_name)
⋮----
def get_sso_redirect_uri(provider_name: str, domain: Optional[str] = None) -> str
⋮----
local_domain = domain if domain else get_host_main_domain_name()
```

## File: backend/api/core/**init**.py

```python

```

## File: backend/api/core/config.py

```python
class Settings(BaseSettings)
⋮----
model_config = ConfigDict(
mongo_uri: str = "mongodb://localhost:27017"
mongo_db_name: str = "myapp"
jwt_secret: str
access_token_expire_minutes: int = 15
refresh_token_expire_days: int = 7 * 24 * 60 * 60
algorithm: str = "HS256"
refresh_algorithm: str = "HS512"
refresh_token_secret: str
admin_email: EmailStr = "admin@example.com"
admin_password: str = "Test@123!"
app_title: str = "Full-Stack FastAPI React Template"
app_version: str = "0.1.0"
api_endpoint_base: str = "http://localhost:8000/api"
multi_tenancy_strategy: str = "header"
host_main_domain: str = "fsrapp.com"
host_main_domain_prefix: str = "demo"  # Prefix for subdomains, e.g., demo.fsrapp.com. Change as needed.
redis_uri: str = "redis://localhost:6372"
celery_result_backend: str = "redis://localhost:6372/0"
celery_broker_url: str = "redis://localhost:6372/0"
fastapi_env: str = "development"  # Options: "development", "production"
smtp_user: str
smtp_password: str
smtp_host: str
smtp_port: int
smtp_mail_from_name: str = "FSR App"
smtp_mail_from: EmailStr
smtp_starttls: bool = False
smtp_ssl_tls: bool = False
smtp_use_credentials: bool = False # Changed to True when running production env
smtp_validate_certs: bool = False
ollama_host: str = "http://localhost:11434"
# Coolify settings for deployment integration only used for domain setup
coolify_enabled: bool = False
coolify_api_url: str
coolify_api_key: str
coolify_application_id: str
# AWS S3 settings for file uploads this belongs to the Host - level settings
aws_region: str = "eu-central-1"
aws_access_key_id: str
aws_secret_access_key: str
aws_s3_bucket_name: str = "fsrapptest"
# Stripe Settings for Billing and Payments - Host Level Settings
stripe_api_key: str
stripe_publishable_key: str
stripe_secret_key: str
stripe_webhook_secret: str
# Instantiate settings once
settings = Settings()
```

## File: backend/api/core/container.py

```python
container = punq.Container()
# Register database
⋮----
# Register security components
## Smtp email Service
⋮----
## JWT Token Service
⋮----
## Passkey Components
⋮----
## Email magic link user login
⋮----
# Register infra components
## DNS Resolver
⋮----
## Coolify Integration
⋮----
# Register repositories and services
## Tenant
⋮----
## User
⋮----
## User Preference
⋮----
## Role
⋮----
## Auth service
⋮----
## Storage Settings
⋮----
## File Service
⋮----
## AI Components
⋮----
## Subscription Plan Components
⋮----
## Stripe and Payment Components
⋮----
## Notification Banner Components
⋮----
## Audit Log Components
⋮----
## SSO Settings Components
⋮----
## Dependency resolver functions
def get_database() -> Database
def get_tenant_service() -> TenantService
def get_user_service() -> UserService
def get_user_preference_service() -> UserPreferenceService
def get_role_service() -> RoleService
def get_jwt_token_service() -> JwtTokenService
def get_auth_service() -> AuthService
def get_storage_settings_service() -> StorageSettingsService
def get_storage_settings_repository() -> StorageSettingsRepository
def get_file_service() -> FileService
def get_email_service() -> IEmailService
def get_local_ai_service() -> LocalAIService
def get_dns_resolver() -> DnsResolver
def get_coolify_app_service() -> CoolifyAppService
def get_user_passkey_repository() -> UserPasskeyRepository
def get_user_passkey_challenges_repository() -> UserPasskeyChallengesRepository
def get_passkey_service() -> PasskeyService
def get_user_magic_link_repository() -> UserMagicLinkRepository
def get_email_magic_link_service() -> EmailMagicLinkService
def get_billing_record_service() -> BillingRecordService
def get_product_service() -> ProductService
def get_pricing_service() -> PricingService
def get_stripe_resolver() -> StripeResolver
def get_stripe_setting_service() -> StripeSettingService
def get_subscription_plan_service() -> SubscriptionPlanService
def get_notification_banner_service() -> NotificationBannerService
def get_audit_logs_service() -> AuditLogsService
def get_sso_settings_service() -> SSOSettingsService
def get_sso_auth_provider() -> SSOAuthProvider
```

## File: backend/api/core/exceptions.py

```python
# Keep only domain-specific exceptions here
⋮----
class UserNotFoundException(NotFoundException)
⋮----
def __init__(self, user_id: str)
class EmailAlreadyExistsException(ConflictException)
⋮----
def __init__(self, email: str)
class InvalidSubdomainException(InvalidOperationException)
⋮----
def __init__(self, subdomain: str)
⋮----
message = f"Subdomain '{subdomain}' is invalid format (3–63 chars, letters/digits/hyphens, no leading/trailing hyphen)."
⋮----
class InvalidCustomDomainException(InvalidOperationException)
⋮----
def __init__(self, custom_domain: str)
⋮----
message = f"Custom domain '{custom_domain}' is invalid format."
⋮----
class TenantNotFoundException(NotFoundException)
⋮----
def __init__(self, tenant_id: str)
class RoleNotFoundException(NotFoundException)
⋮----
def __init__(self, role_id: str)
class RoleAlreadyExistsException(ConflictException)
⋮----
def __init__(self, name: str)
class TenantNotActiveException(InvalidOperationException)
⋮----
message = f"Tenant with ID '{tenant_id}' is not active."
⋮----
class S3StorageException(AwsException)
⋮----
def __init__(self, message: str)
⋮----
local_message = f"S3 Storage Error: {message}"
⋮----
class AzureBlobStorageException(AzureException)
⋮----
local_message = f"Azure Blob Storage Error: {message}"
⋮----
class StorageNotEnabledException(InvalidOperationException)
⋮----
def __init__(self)
⋮----
message = "No storage provider is enabled. Please enable a storage provider in the settings."
⋮----
class CoolifyIntegrationException(InvalidOperationException)
⋮----
local_message = f"Coolify Integration Error: {message}"
⋮----
class PassKeyException(InvalidOperationException)
⋮----
local_message = f"PassKey Error: {message}"
⋮----
class FeatureNotEnabledException(ForbiddenException)
⋮----
def __init__(self, feature: str)
⋮----
message = f"Feature '{feature}' is not enabled."
⋮----
class StripeSettingsNotFoundException(NotFoundException)
class ProductException(InvalidOperationException)
⋮----
local_message = f"Product Error: {message}"
⋮----
class ProductNotFoundException(NotFoundException)
⋮----
def __init__(self, product_id: str)
class PricingException(InvalidOperationException)
⋮----
local_message = f"Pricing Error: {message}"
⋮----
class PricingNotFoundException(NotFoundException)
⋮----
def __init__(self, pricing_id: str)
class BillingRecordException(InvalidOperationException)
⋮----
local_message = f"BillingRecord Error: {message}"
⋮----
class BillingRecordNotFoundException(NotFoundException)
⋮----
def __init__(self, record_id: str)
```

## File: backend/api/domain/**init**.py

```python

```

## File: backend/api/domain/dtos/ai_dto.py

```python
class AIModelInfoDto(BaseModel)
⋮----
name: str
digest: str
size: str
created: str
class AIHistoryDto(BaseModel)
⋮----
uid: str
query: str
response: str
timestamp: str
tenant_id: str | None = None
class AIHistoriesDto(BaseModel)
⋮----
id: str
histories: List[AIHistoryDto]
class AISessionDto(BaseModel)
⋮----
session_id: str
created_at: str
history_id: str
⋮----
user_id: str
class AISessionByUserIdDto(AISessionDto)
⋮----
sessions: List[AIHistoriesDto]
class AIAskRequestDto(BaseModel)
⋮----
question: str
model_name: Optional[str] = None
session_id: Optional[str] = None
```

## File: backend/api/domain/dtos/audit_logs_dto.py

```python
AuditActionType = Literal["create", "update", "delete", "read", "login", "logout", "error", "download"]
class AuditLogDto(BaseModel)
⋮----
entity: str
action: AuditActionType
changes: Dict[str, Any] = {}
user_id: Optional[str] = None
timestamp: str = datetime.now().isoformat()
tenant_id: Optional[str] = None
class AuditLogListDto(BaseModel)
⋮----
total: int
logs: list[AuditLogDto]
has_next: bool
has_previous: bool
limit: int
skip: int
class DownloadAuditRequestDto(BaseModel)
⋮----
action: Optional[AuditActionType] = None
total_records: int
```

## File: backend/api/domain/dtos/auth_dto.py

```python
class PasswordResetRequestDto(BaseModel)
⋮----
email: EmailStr
class PasswordResetConfirmRequestDto(BaseModel)
⋮----
new_password: str
class PasswordResetResponseDto(BaseModel)
⋮----
message: str
class ChangeEmailRequestDto(BaseModel)
⋮----
new_email: EmailStr
class ChangeEmailConfirmRequestDto(BaseModel)
⋮----
token: str
class ChangeEmailResponseDto(BaseModel)
class MeResponseDto(UserDto)
⋮----
role: RoleDto
subscription: Optional[SubscriptionPlanDto] | None
class MagicLinkResponseDto(BaseModel)
```

## File: backend/api/domain/dtos/billing_dto.py

```python
class PlanDto(BaseModel)
⋮----
id: str
active: bool
amount: int
amount_decimal: str
billing_scheme: str
currency: str = "eur"
interval: str
interval_count: int
product: str
trial_period_days: Optional[int] = None
usage_type: str
class PlanListDto(BaseModel)
⋮----
plans: List[PlanDto]
has_more: bool
class CreatePlanDto(BaseModel)
⋮----
currency: str = Field(default="eur", description="Must be a supported currency: https://docs.stripe.com/currencies")
interval: Literal["month", "year"] # Add day or week later... if needed.
product_id: str = Field(..., description="The product whose pricing the created plan will represent. This must be the ID of an existing product.")
⋮----
class UpdatePlanDto(BaseModel)
⋮----
trial_period_days: int
active: bool = True
metadata: Optional[Dict[str, Any]] = None
class InvoiceDto(BaseModel)
⋮----
account_country: str
account_name: str
amount_due: int = 0
amount_paid: int = 0
amount_remaining: int = 0
amount_overpaid: int = 0
attempt_count: int = 0
attempted: bool = False
auto_advance: bool = False
billing_reason: str
collection_method: str
created: int
currency: str
customer: str
customer_name: str
status: str
total: int
receipt_number: Optional[str] = None
class InvoiceListDto(BaseModel)
⋮----
invoices: List[InvoiceDto]
has_more: bool = False
# Billing Record stored in our system
class BillingRecordDto(BaseModel)
⋮----
scope: ScopeType
actor: ActorType
user_id: Optional[str] = None
payment_type: str
⋮----
amount: Optional[int] = None
stripe_customer_id: Optional[str] = None
stripe_subscription_id: Optional[str] = None
stripe_session_id: Optional[str] = None
product_id: Optional[str] = None
price_id: Optional[str] = None
status: StatusType
current_period_end: Optional[int] = None
canceled_at: Optional[int] = None
cancellation_reason: Optional[str] = None
metadata: Optional[Dict[str, str]] = None
class BillingRecordListDto(BaseModel)
⋮----
billing_records: List[BillingRecordDto]
skip: int
limit: int
⋮----
has_previous: bool
has_next: bool
```

## File: backend/api/domain/dtos/checkout_dto.py

```python
class CheckoutRequestDto(BaseModel)
⋮----
price_id: str
tenant_id: str | None = None
mode: Literal["payment", "subscription", "setup"] = "subscription"
email: EmailStr
class CheckoutResponseDto(BaseModel)
⋮----
checkout_url: str
```

## File: backend/api/domain/dtos/coolify_app_dto.py

```python
class CoolifyAppDto(BaseModel)
⋮----
uuid: str
name: str
fqdn: Optional[str] = None
class UpdateCoolifyAppDto(BaseModel)
⋮----
domain: str
class UpdateDomainDto(UpdateCoolifyAppDto)
⋮----
mode: Literal["add", "remove"] = "add"
```

## File: backend/api/domain/dtos/dashboard_dto.py

```python
class TimeSeriesDto(BaseModel)
⋮----
time_or_date: str
count: int
class DashboardMetricsDto(BaseModel)
⋮----
filter: Literal["today", "this_week", "last_3_months", "all"]
joined_users: int
total_users: int
timeseries: list[TimeSeriesDto]
```

## File: backend/api/domain/dtos/email_schema_dto.py

```python
class ActivationEmailSchemaDto(BaseModel)
⋮----
email: EmailStr
user_id: str
first_name: str
tenant_id: Optional[str] = None
jwt_secret: Optional[str] = None  # Used for password reset tokens
```

## File: backend/api/domain/dtos/login_dto.py

```python
class LoginRequestDto(BaseModel)
⋮----
email: EmailStr
password: str
```

## File: backend/api/domain/dtos/notification_settings_dto.py

```python
class NotificationBannerSettingDto(BaseModel)
⋮----
is_enabled: bool
message: str | None = None
id: str | None = None
tenant_id: str | None = None
created_at: str | None = None
updated_at: str | None = None
class CreateNotificationBannerSettingDto(BaseModel)
class UpdateNotificationBannerSettingDto(CreateNotificationBannerSettingDto)
```

## File: backend/api/domain/dtos/passkey_dto.py

```python
class RegisteredPasskeyCredentialsDto(BaseModel)
⋮----
credential_id: str
public_key: str
sigin_count: int
transports: list[AuthenticatorTransport] = []
created_at: datetime
last_used_at: Optional[datetime] = None
```

## File: backend/api/domain/dtos/permission_dto.py

```python
class PermissionDto(BaseModel)
⋮----
name: str
```

## File: backend/api/domain/dtos/pricing_dto.py

```python
class RecurringDto(BaseModel)
⋮----
interval: Literal["day", "week", "month", "year"] = Field(default="month", description="Billing interval, e.g., 'month'")
interval_count: int = Field(default=1, description="Number of intervals between each billing cycle")
usage_type: Literal["licensed", "metered"] = Field(default="licensed", description="Configures how the quantity per period should be determined. Can be either metered or licensed. licensed automatically bills the quantity set when adding it to a subscription. metered aggregates the total usage based on usage records. Defaults to licensed.")
class CreatePricingDto(BaseModel)
⋮----
currency: str = Field(default="eur", description="Currency code, e.g., 'usd'")
unit_amount: int = Field(..., description="A positive integer in cents (or 0 for a free price) representing how much to charge. One of unit_amount, unit_amount_decimal, or custom_unit_amount is required, unless billing_scheme=tiered.")
product: str = Field(..., description="The ID of the product this price is associated with")
active: bool = Field(default=True, description="Whether the price is active or not")
recurring: RecurringDto
class PricingDto(BaseModel)
⋮----
id: str
currency: str
unit_amount: int
product: str
active: bool
⋮----
tax_behavior: str
type: str
unit_amount_decimal: str
class PricingListDto(BaseModel)
⋮----
pricings: list[PricingDto]
has_more: bool
class UpdatePricingDto(BaseModel)
⋮----
metadata: Optional[Dict[str, Any]] = None
tax_behavior: Optional[Literal["exclusive", "inclusive", "unspecified"]] = None
```

## File: backend/api/domain/dtos/product_dto.py

```python
class ProductDto(BaseModel)
⋮----
id: str
name: str
description: str
tax_code: str | None = None
active: bool
class ProductListDto(BaseModel)
⋮----
products: list[ProductDto]
has_more: bool
class CreateProductDto(BaseModel)
⋮----
active: bool = True
```

## File: backend/api/domain/dtos/role_dto.py

```python
class RoleDto(BaseModel)
⋮----
id: str
name: str
description: Optional[str] | None
permissions: List[Permission] = []
created_at: str
updated_at: str
tenant_id: Optional[str] | None = None
class RoleListDto(BaseModel)
⋮----
roles: List[RoleDto]
skip: int
limit: int
total: int
has_previous: bool
has_next: bool
class CreateRoleDto(BaseModel)
⋮----
description: Optional[str] | None = None
tenant_id: PydanticObjectId | None = None
class UpdateRoleDto(CreateRoleDto)
⋮----
permissions: List[Permission] = Field(default_factory=list)
class CreateRoleResponseDto(BaseModel)
```

## File: backend/api/domain/dtos/sso_settings_dto.py

```python
class SSOSettingsDto(BaseModel)
⋮----
enabled: bool
provider: SSOProvider
client_id: str
client_secret: Optional[str] | None = None
scopes: Optional[list[str]] = []
redirect_uris: Optional[list[str]] = []
class CreateSSOSettingsDto(SSOSettingsDto)
⋮----
tenant_id: Optional[str] | None = None
class UpdateSSOSettingsDto(BaseModel)
⋮----
enabled: Optional[bool] | None = None
provider: Optional[SSOProvider] | None = None
client_id: Optional[str] | None = None
⋮----
scopes: Optional[list[str]] = None
class ReadSSOSettingsDto(SSOSettingsDto)
⋮----
id: str
⋮----
@field_serializer("client_secret")
    def mask_sensitive_fields(self, value: Optional[str]) -> Optional[str]
⋮----
@field_serializer("client_id")
    def mask_client_id(self, value: str) -> str
class SSOSettingsListDto(BaseModel)
⋮----
items: list[ReadSSOSettingsDto]
class SSOSettingsResponseDto(BaseModel)
⋮----
message: str
```

## File: backend/api/domain/dtos/storage_settings_dto.py

```python
class StorageSettingsDto(BaseModel)
⋮----
provider: Literal["s3", "azure_blob"]
is_enabled: bool
region: str
aws_access_key: Optional[str] = None
aws_secret_key: Optional[str] = None
aws_bucket_name: Optional[str] = None
azure_connection_string: Optional[str] = None
azure_container_name: Optional[str] = None
updated_by_user_id: Optional[str] = None
class AvailableStorageProviderDto(StorageSettingsDto)
⋮----
id: str
created_at: str
updated_at: str
tenant_id: str | None = None
⋮----
@field_serializer("aws_secret_key", "aws_access_key", "azure_connection_string")
    def serialize_sensitive_fields(self, value)
```

## File: backend/api/domain/dtos/stripe_setting_dto.py

```python
class CreateStripeSettingDto(BaseModel)
⋮----
default_currency: str = "eur"
stripe_webhook_secret: str
mode: PaymentType = "recurring"
trial_period_days: int = 14  # Default trial period for subscriptions in days
stripe_secret_key: str  #Secret key for Stripe API only tenant scope
tenant_id: str
class UpdateStripeSettingDto(BaseModel)
⋮----
default_currency: Optional[str] = None
stripe_webhook_secret: Optional[str] = None
mode: Optional[PaymentType] = None
trial_period_days: Optional[int] = None
stripe_secret_key: Optional[str] = None  #Secret key for Stripe API only tenant scope
class StripeSettingDto(BaseModel)
⋮----
id: str
⋮----
default_currency: str
mode: PaymentType
trial_period_days: int
class StripeSettingSecretDto(BaseModel)
```

## File: backend/api/domain/dtos/subcription_plan_dto.py

```python
# This dto is used to represent subscription plans in the system Not used by Stripe directly
⋮----
class SubscriptionPlanDto(BaseModel)
⋮----
id: str
plan_level: Dict[str, int] = Field(..., description="e.g., {'basic': 0, 'pro': 1, 'enterprise': 2}")
is_trial: bool = Field(..., description="Indicates if the subscription is a trial")
tenant_id: str | None = Field(..., description="The tenant who owns the subscription")
actor: str = Field(..., description="who it is applied to, If end_user, then user_id must be set")
user_id: str | None = Field(..., description="The end user who has this subscription, if applicable")
plan_id: str = Field(..., description="Corresponding Stripe Price ID")
class CreateSubscriptionPlanDto(BaseModel)
```

## File: backend/api/domain/dtos/tenant_dto.py

```python
class FeatureDto(BaseModel)
⋮----
name: FeatureEnum
enabled: bool
class TenantDto(BaseModel)
⋮----
id: Optional[str]
name: str
subdomain: Optional[str] | None
is_active: bool
custom_domain: Optional[str] | None
custom_domain_status: Literal["active", "failed", "activation-progress"] = "failed"
features: List[FeatureDto] = []
subscription_id: Optional[str] | None
class CreateTenantDto(BaseModel)
⋮----
subdomain: Subdomain
admin_email: EmailStr
admin_password: str
first_name: str
last_name: str
gender: Gender
class TenantListDto(BaseModel)
⋮----
tenants: List[TenantDto]
skip: int
limit: int
total: int
has_previous: bool
has_next: bool
class CreateTenantResponseDto(BaseModel)
⋮----
id: str
class SubdomainAvailabilityDto(BaseModel)
⋮----
is_available: bool
class UpdateTenantDto(BaseModel)
⋮----
is_active: Optional[bool] | None = None
custom_domain: Optional[CustomDomain] | None = None
subscription_id: Optional[str] | None = None
class UpdateTenantResponseDto(BaseModel)
⋮----
message: str
```

## File: backend/api/domain/dtos/user_dto.py

```python
class BaseUserDto(BaseModel)
⋮----
first_name: str
last_name: str
email: EmailStr
gender: Gender
tenant_id: PydanticObjectId | None = None
role_id: PydanticObjectId | None = None
sso_provider_id: Optional[str] = None
class CreateUserDto(BaseUserDto)
⋮----
password: str
sub_domain: Optional[str] = None
⋮----
@field_serializer("tenant_id", "role_id")
    def serialize(v: PydanticObjectId | None) -> str | None
class UpdateUserDto(BaseModel)
⋮----
first_name: Optional[str] = None
last_name: Optional[str] = None
email: Optional[EmailStr] = None
gender: Optional[Gender] = None
image_url: Optional[str] = None
role_id: Optional[str] = None
is_active: Optional[bool] = None
⋮----
class UserDto(BaseModel)
⋮----
id: str
⋮----
is_active: bool
activated_at: Optional[str] = None
⋮----
created_at: str
updated_at: str
tenant_id: Optional[str] = None
⋮----
class UserListDto(BaseModel)
⋮----
users: List[UserDto]
skip: int
limit: int
total: int
has_previous: bool
has_next: bool
class CreateUserResponseDto(BaseModel)
class UserActivationRequestDto(BaseModel)
⋮----
token: str
class UserResendActivationEmailRequestDto(BaseModel)
class UserProfileImageUpdateDto(BaseModel)
⋮----
image_url: str
class UserRoleUpdateRequestDto(BaseModel)
⋮----
role_id: str
```

## File: backend/api/domain/dtos/user_preference_dto.py

```python
class UserPreferenceDto(BaseModel)
⋮----
user_id: str
preferences: dict[str, Any]
```

## File: backend/api/domain/entities/ai.py

```python
class ChatSessionAI(ApiBaseModel)
⋮----
session_id: PydanticObjectId
user_id: PydanticObjectId
history_id: PydanticObjectId
async def to_serializable_dict(self)
⋮----
base_doc = await super().to_serializable_dict()
⋮----
class Settings
⋮----
name = "chat_unique_sessions_ai"
class History(BaseModel)
⋮----
uid: str
query: str
response: str
timestamp: datetime
def to_serializable_dict(self)
class ChatHistoryAI(ApiBaseModel)
⋮----
histories: list[History]
⋮----
name = "chat_histories_ai"
class AISessions(ChatSessionAI)
⋮----
sessions: list[ChatHistoryAI]
```

## File: backend/api/domain/entities/api_base_model.py

```python
class ApiBaseModel(Document)
⋮----
created_at: datetime = get_utc_now()
updated_at: datetime = get_utc_now()
tenant_id: Optional[PydanticObjectId] = None
# Todo: Remove all the to_serializable_dict methods and use field_serializers instead
# @field_serializer("created_at", "updated_at", "tenant_id")
# def serialize_object_id(self, value):
#     if isinstance(value, PydanticObjectId):
#         return str(value)
#     return str(value)
async def to_serializable_dict(self)
⋮----
data = self.model_dump()
```

## File: backend/api/domain/entities/notification_settings.py

```python
class NotificationBannerSetting(ApiBaseModel)
⋮----
is_enabled: bool
message: Optional[str] = None
⋮----
@field_serializer("id", "tenant_id", "created_at", "updated_at")
    def serialize_fields(value: PydanticObjectId | datetime | None) -> Optional[str]
class Settings
⋮----
name = "notification_banner_settings"
indexes = ["tenant_id"]
```

## File: backend/api/domain/entities/role.py

```python
class Role(ApiBaseModel)
⋮----
name: str = Indexed(str, unique=True)
description: Optional[str] | None
permissions: List[Permission] = [Permission.USER_VIEW_ONLY, Permission.ROLE_VIEW_ONLY]
async def to_serializable_dict(self)
⋮----
base_doc = await super().to_serializable_dict()
⋮----
class Settings
⋮----
name = "roles"
```

## File: backend/api/domain/entities/sso_settings.py

```python
SSOProvider = Literal["google", "github", "discord", "microsoft", "linkedin", "x", "notion", "gitlab", "bitbucket", "facebook"]
class SSOSettings(ApiBaseModel)
⋮----
enabled: bool = False
provider: Optional[SSOProvider] = None
client_id: str | None = None
client_secret: str | None = None
scopes: Optional[list[str]] = []
redirect_uris: Optional[list[str]] = []
⋮----
@field_serializer("id")
    def serialize_id(self, v: PydanticObjectId) -> Optional[str]
class Settings
⋮----
name = "sso_settings"
indexes = ["enabled", "provider", "tenant_id"]
```

## File: backend/api/domain/entities/storage_settings.py

```python
class StorageProvider(str, Enum)
⋮----
AWS_S3 = "s3"
AZURE_BLOB = "azure_blob"
class StorageSettings(ApiBaseModel)
⋮----
provider: StorageProvider
is_enabled: bool
region: str
aws_access_key: Optional[str] | None = None
aws_secret_key: Optional[str] | None = None
aws_bucket_name: Optional[str] | None = None
azure_connection_string: Optional[str] | None = None
azure_container_name: Optional[str] | None = None
updated_by_user_id: Optional[str] | None = None
async def to_serializable_dict(self) -> AvailableStorageProviderDto
⋮----
base_doc = await super().to_serializable_dict()
result = {
⋮----
class Settings
⋮----
name = "storage_settings"
indexes = [
```

## File: backend/api/domain/entities/stripe_settings.py

```python
PaymentType = Literal["one_time", "recurring", "both"]
ScopeType = Literal["host", "tenant"]
StatusType = Literal[
ActorType = Literal["tenant", "end_user"] # who is being billed
class StripeSettings(ApiBaseModel)
⋮----
default_currency: str = "eur"
stripe_webhook_secret: str
mode: PaymentType = "recurring"
trial_period_days: int = 14  # Default trial period for subscriptions in days
stripe_secret_key: Optional[str] = None # Secret key for Stripe API only tenant scope
⋮----
@field_serializer("id", "tenant_id", "created_at", "updated_at")
    def serialize_as_str(self, value: PydanticObjectId | datetime | None) -> str
class Settings
⋮----
name = "stripe_settings"
class BillingRecord(ApiBaseModel)
⋮----
scope: ScopeType
actor: ActorType
user_id: PydanticObjectId | None = None  # The end user who made the payment, if applicable
payment_type: str
currency: str
amount: Optional[int] = None  # unit amount used for one-time payments
stripe_customer_id: Optional[str] = None
stripe_subscription_id: Optional[str] = None
stripe_session_id: Optional[str] = None
product_id: Optional[str] = None
price_id: Optional[str] = None
status: StatusType = "pending"
current_period_end: Optional[datetime] = None
canceled_at: Optional[datetime] = None
cancellation_reason: Optional[str] = None
metadata: Dict[str, str] = Field(default_factory=dict)
⋮----
@field_serializer("id", "tenant_id", "created_at", "updated_at", "current_period_end", "canceled_at", "user_id")
    def serialize_as_str(self, value: PydanticObjectId | datetime | None) -> str
⋮----
name = "billing_records"
indexes = [
```

## File: backend/api/domain/entities/subscription_plan.py

```python
# This entity is used to represent subscription plans in the system Not used by Stripe directly
⋮----
class SubscriptionPlan(ApiBaseModel)
⋮----
plan_level: Dict[str, int] = {"basic": 0, "pro": 1, "enterprise": 2}  # e.g., 0: Basic, 1: Pro, 2: Enterprise
is_trial: bool = False
actor: Literal["tenant", "end_user"] = "tenant" # who it is applied to, If end_userm, then user_id must be set
user_id: PydanticObjectId | None = None  # The end user who has this subscription, if applicable
plan_id: str  # Corresponding Stripe Price ID
⋮----
@field_serializer("id", "tenant_id", "created_at", "updated_at", "user_id")
    def serialize_as_str(self, value: datetime | PydanticObjectId | None) -> str | None
class Settings
⋮----
name = "subscription_plans"
indexes = [
```

## File: backend/api/domain/entities/tenant.py

```python
SUBDOMAIN_REGEX = re.compile(r"^(?!-)[A-Za-z0-9-]{3,63}(?<!-)$")
CUSTOM_DOMAIN_REGEX = re.compile(r"^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$")
def validate_subdomain(subdomain: str | None) -> str | None
⋮----
"""Validate subdomain format. Raises InvalidSubdomainException if invalid. Returns None if subdomain is None."""
⋮----
main_domain = get_host_main_domain_name()
⋮----
# At this point, it's something like <prefix>.demo.fsrapp.xyz
label = subdomain.removesuffix(f".{main_domain}")
# Disallow subdomains that "skip" the main domain (like test.fsrapp.xyz)
# Example: if main_domain = demo.fsrapp.xyz, then "fsrapp.xyz" would not match ".demo.fsrapp.xyz"
# so it's already caught above.
⋮----
def validate_custom_domain(domain: str) -> str
⋮----
"""
    Validate a custom domain provided by a tenant.
    - Must be a valid domain (e.g., tenant1.com, app.tenant1.com)
    - Cannot be your main domain or a subdomain of it
    :param domain: Domain name provided by the tenant
    :return: The normalized, validated domain name
    :raises InvalidCustomDomainException: if validation fails
    """
⋮----
# Normalize and strip protocol if mistakenly included
parsed = urlparse(domain if "://" in domain else f"http://{domain}")
hostname = parsed.hostname
⋮----
# Ensure it's not your main domain or a subdomain of it
main_domain = get_host_main_domain_name()  # e.g. "demo.fsrapp.xyz"
⋮----
Subdomain = Annotated[str, AfterValidator(validate_subdomain)]
CustomDomain = Annotated[str, AfterValidator(validate_custom_domain)]
class Feature(BaseModel)
⋮----
name: FeatureEnum
enabled: bool
class Tenant(Document)
⋮----
name: str = Indexed(str, unique=True)
subdomain: Subdomain | None = None
created_at: datetime = get_utc_now()
updated_at: datetime = get_utc_now()
is_active: bool = False
custom_domain: Optional[CustomDomain] | None = None
custom_domain_status: Literal["active", "failed", "activation-progress"] = "failed"
features: List[Feature] = []
subscription_id: Optional[PydanticObjectId] | None = None
⋮----
@field_serializer("created_at", "updated_at", "id", "subscription_id")
    def serialize_datetime(self, value: datetime | PydanticObjectId) -> str | None
class Settings
⋮----
name = "tenants"
indexes = [
```

## File: backend/api/domain/entities/user_magic_link.py

```python
class UserMagicLink(ApiBaseModel)
⋮----
user_id: PydanticObjectId
token: str
expires_at: datetime = get_utc_now()
class Settings
⋮----
name = "user_magic_links"
indexes = [
⋮----
expireAfterSeconds=900 # 15 minutes
```

## File: backend/api/domain/entities/user_passkey.py

```python
class Credential(BaseModel)
⋮----
credential_id: str
public_key: str
sigin_count: int
transports: list[AuthenticatorTransport] = []
created_at: datetime
last_used_at: Optional[datetime] = None
class UserPasskey(ApiBaseModel)
⋮----
user_email: EmailStr
credentials: list[Credential] = []
class Settings
⋮----
name = "user_passkeys"
class Challenges(ApiBaseModel)
⋮----
email: EmailStr
type: Literal["registration", "authentication"] = "registration"
challenge: str
expires_at: datetime = get_utc_now()
⋮----
name = "user_passkey_challenges"
indexes = [
⋮----
expireAfterSeconds=300  # Document expires 5 minutes after 'expired_at'
```

## File: backend/api/domain/entities/user_password_reset.py

```python
class UserPasswordReset(ApiBaseModel)
⋮----
user_id: PydanticObjectId
token_secret: str
reset_secret_updated_at: datetime
first_name: str
async def to_serializable_dict(self)
⋮----
base_doc = await super().to_serializable_dict()
⋮----
class Settings
⋮----
name = "user_password_resets"
```

## File: backend/api/domain/entities/user_preference.py

```python
class UserPreference(ApiBaseModel)
⋮----
user_id: PydanticObjectId
preferences: dict[str, Any]
async def to_serializable_dict(self)
⋮----
base_doc = await super().to_serializable_dict()
⋮----
class Settings
⋮----
name = "user_preferences"
```

## File: backend/api/domain/entities/user.py

```python
class User(ApiBaseModel)
⋮----
first_name: str
last_name: str
email: EmailStr = Indexed(EmailStr, unique=True)
gender: Gender = Indexed(str)
role_id: Optional[PydanticObjectId] = None
is_active: bool
activated_at: Optional[datetime] = None
image_url: Optional[str] = None
password: str  # hashed password
sso_provider_id: Optional[str] = None
async def to_serializable_dict(self)
⋮----
base_doc = await super().to_serializable_dict()
⋮----
class Settings
⋮----
name = "users"
indexes = [
```

## File: backend/api/domain/enum/feature.py

```python
class Feature(str, Enum)
⋮----
CHAT = "chat"
REPORT = "report"
ANALYTICS = "analytics"
ORGANIZATION = "organization"
TEAMS = "teams"
STRIPE = "stripe"
```

## File: backend/api/domain/enum/permission.py

```python
class Permission(str, Enum)
⋮----
FULL_ACCESS = "full:access"
USER_READ_AND_WRITE_ONLY = "user:read_and_write_only"
USER_DELETE_ONLY = "user:delete_only"
USER_SELF_READ_AND_WRITE_ONLY = "user:self_read_and_write_only"
USER_ROLE_ASSIGN_OR_REMOVE_ONLY = "role:assign_or_remove_only"
USER_VIEW_ONLY = "user:view_only"
ROLE_VIEW_ONLY = "role:view_only"
ROLE_READ_AND_WRITE_ONLY = "role:read_and_write_only"
ROLE_DELETE_ONLY = "role:delete_only"
ROLE_PERMISSION_READ_AND_WRITE_ONLY = "role:permission_read_and_write_only"
HOST_MANAGE_TENANTS = "host:manage_tenants"
MANAGE_STORAGE_SETTINGS = "manage:storage_settings"
MANAGE_TENANT_SETTINGS = "manage:tenant_settings"
MANAGE_BILLING = "manage:billing"
MANAGE_PAYMENTS_SETTINGS = "manage:payments_settings"
MANAGE_PRODUCTS_AND_PRICING = "manage:products_and_pricing"
AUDIT_LOGS_VIEW_ONLY = "audit_logs:view_only"
AUDIT_LOGS_DOWNLOAD = "audit_logs:download"
```

## File: backend/api/domain/enum/role.py

```python
class RoleType(str, Enum)
⋮----
ADMIN = "admin"
USER = "user"
GUEST = "guest"
HOST = "host"
BILLING_MANAGER = "billing_manager"
```

## File: backend/api/domain/interfaces/background_task.py

```python
class IBackgroundTask
⋮----
async def enqueue(self, task: Callable, *args: Any, **kwargs: Any) -> None
```

## File: backend/api/domain/interfaces/email_service.py

```python
class IEmailService(Protocol)
⋮----
@abstractmethod
    async def send_email(self, to: str, subject: str, body: str, type: Literal[MessageType.html, MessageType.plain], attachments: List[UploadFile | dict | str] = []) -> None
```

## File: backend/api/domain/security/feature_access_management.py

```python
logger = get_logger(__name__)
def check_feature_access(feature_name: FeatureEnum) -> callable
⋮----
async def dependency(request: Request, currentUser: CurrentUser) -> bool
⋮----
role: RoleDto = currentUser.role
⋮----
return True  # Host users with tenant management permissions have access to all features.
⋮----
tenant_service = get_tenant_service()
tenant = await tenant_service.get_tenant_by_id(tenant_id=request.state.tenant_id)
```

## File: backend/api/domain/security/user_access_management.py

```python
logger = get_logger(__name__)
class UserAccessManagement
⋮----
"""
        Initialize user access management
        Args:
            required_permissions: List of permissions required to access the resource
            any_permission: If True, user needs ANY of the permissions. If False, user needs ALL permissions
            allow_self_access: If True, allows self-access with USER_SELF_READ_AND_WRITE_ONLY permission
        """
⋮----
async def get_role_detail(self, role_id: str)
⋮----
role_service = get_role_service()
role = await role_service.get_role_by_id(role_id=role_id)
⋮----
async def check_self_access(self, current_user: UserDto, resource_id: str = None) -> bool
⋮----
"""Check if user is accessing their own resource"""
⋮----
async def has_permission(self, user_permissions: List[Permission], required_permissions: List[Permission]) -> bool
⋮----
"""Check if user has required permissions"""
⋮----
# User needs ANY of the required permissions
⋮----
# User needs ALL of the required permissions
⋮----
def __call__(self)
⋮----
async def check_permission(current_user: CurrentUser) -> bool
⋮----
resource_id = current_user.id
⋮----
role = await self.get_role_detail(current_user.role_id)
user_permissions = role.permissions
⋮----
# Check for full access (admin override)
⋮----
# Check for self-access permissions
⋮----
# Check required permissions
⋮----
permission_type = "any" if self.any_permission else "all"
```

## File: backend/api/infrastructure/**init**.py

```python

```

## File: backend/api/infrastructure/background/post_tenant_creation_task_service.py

```python
logger = get_logger(__name__)
class PostTenantCreationTaskService(IBackgroundTask)
⋮----
async def _seed_roles(self, tenant_id: PydanticObjectId) -> None
⋮----
roles = get_seed_roles()
⋮----
async def _init_and_run(self, admin_user: CreateUserDto) -> None
⋮----
response  = await self.user_service.create_user(admin_user)
user = await self.user_service.get_user_by_id(user_id=str(response))
⋮----
role = await self.role_service.find_by_name(name=RoleType.ADMIN)
⋮----
async def enqueue(self, admin_user: CreateUserDto) -> None
```

## File: backend/api/infrastructure/externals/azure_storage.py

```python
class AzureBlobStorage
⋮----
def __init__(self, connection_string: str, container_name: str)
async def upload_file(self, file: UploadFile, destination: str) -> str
⋮----
blob_client = self.container_client.get_blob_client(destination)
data = await file.read()
⋮----
async def generate_read_url(self, blob_name: str, expires_in: int = 3600) -> str
⋮----
sas_token = generate_blob_sas(
blob_client = self.container_client.get_blob_client(blob_name)
```

## File: backend/api/infrastructure/externals/coolify_app.py

```python
## Note: This is just for Coolify integration. You must have a Coolify instance running with API access enabled.
## Documentation: https://coolify.io/docs
⋮----
logger = get_logger(__name__)
class CoolifyApp
⋮----
"""Coolify application integration."""
def __init__(self)
async def get_app_details(self) -> CoolifyAppDto
⋮----
"""
            Fetch the Coolify application details.
            Raises CoolifyIntegrationException if fetching app details fails.
            Returns CoolifyAppDto on success.
        """
⋮----
url = f"{self.api_url}/applications/{self.application_id}"
⋮----
response = await client.get(url, headers=self.headers)
⋮----
data: CoolifyAppDto = response.json()
⋮----
async def update_domain(self, data: UpdateDomainDto) -> bool
⋮----
"""
            Update the Coolify application with the new domain.
            also, raises CoolifyIntegrationException if fetching app details fails.
            Returns True if successful, False otherwise.
            @param data: UpdateDomainDto containing domain and mode ('add' or 'remove')
            mode: 'add' to add a domain, 'remove' to remove a domain
            domain: the domain to add or remove
        """
⋮----
app_data = await self.get_app_details()
domains = [d.strip() for d in app_data.fqdn.split(",") if d.strip()]
⋮----
new_domains = ",".join(domains)
⋮----
payload = {
⋮----
response = await client.patch(url, json=payload, headers=self.headers)
⋮----
async def restart_app(self) -> bool
⋮----
"""
            Restart the Coolify application.
            Raises CoolifyIntegrationException if restarting the app fails.
            Returns True if successful, False otherwise.
        """
⋮----
url = f"{self.api_url}/applications/{self.application_id}/restart"
⋮----
response = await client.post(url, headers=self.headers)
```

## File: backend/api/infrastructure/externals/dns_resolver.py

```python
logger = get_logger(__name__)
class DnsResolver
⋮----
def __init__(self, email_service: IEmailService)
async def resolve(self, hostname: str, expected_target: str) -> bool
⋮----
"""
        Resolve the given domain to its DNS records.
        - Must resolve via DNS
        - DNS result must include `expected_target` (CNAME or A record)
        """
⋮----
answers = dns.resolver.resolve(hostname, 'CNAME')
cname_targets = [rdata.target.to_text().rstrip('.') for rdata in answers]
resolved_to = cname_targets
record_type = "CNAME"
⋮----
answers = dns.resolver.resolve(hostname, 'A')
resolved_to = [rdata.address for rdata in answers]
record_type = "A"
⋮----
# if expected_target:
#     if not any(expected_target in target for target in resolved_to):
#         raise InvalidCustomDomainException(
#             f"DNS {record_type} record for {hostname} does not point to the expected target ({expected_target}). Found: {resolved_to}"
#         )
⋮----
async def notify_dns_status(self, user_info: UserDto,  hostname: str, is_success: bool, message: str)
```

## File: backend/api/infrastructure/externals/file_retrieval.py

```python
logger = get_logger(__name__)
class FileRetrieval
⋮----
def __init__(self)
⋮----
from api.core.container import get_storage_settings_repository # To avoid circular imports
⋮----
async def generate_read_url(self, file_key: str, expires_in: int = 3600) -> str
⋮----
"""
            Generate a pre-signed URL for accessing a
            file stored in the configured storage provider. Otherwise,
            raise StorageNotEnabledException if no provider is enabled
            or raise InvalidOperationException if the provider is unsupported.
        """
⋮----
active_provider: StorageSettings = await self.storage_repository.single_or_none(is_enabled=True)
⋮----
s3 = S3Storage(
⋮----
azure = AzureBlobStorage(
```

## File: backend/api/infrastructure/externals/file_upload.py

```python
logger = get_logger(__name__)
class FileUpload
⋮----
def __init__(self)
⋮----
from api.core.container import get_storage_settings_repository # To avoid circular imports
⋮----
async def upload_file(self, file: UploadFile) -> str
⋮----
active_provider: StorageSettings = await self.storage_repository.single_or_none(is_enabled=True)
⋮----
s3 = S3Storage(
⋮----
azure = AzureBlobStorage(
```

## File: backend/api/infrastructure/externals/local_ai_model.py

```python
logger = get_logger(__name__)
class OllamaModels
⋮----
"""
        Interacts with the local Ollama AI models. Requires Ollama to be installed and configured in the environment.
    """
def list_models(self) -> List[AIModelInfoDto]
⋮----
"""
            Lists available AI models using the Ollama CLI.
        """
models: List[AIModelInfoDto] = []
⋮----
response = requests.get(f"{settings.ollama_host}/api/tags")
⋮----
data = response.json()
⋮----
class OllamaChat
⋮----
async def feed_previous_conversations(self, history_service: LocalAIService)
⋮----
is_session_valid = await history_service.is_session_exists(self.current_session)
⋮----
stored_history = await history_service.get_histories_by_session_id(self.user_id, self.current_session)
⋮----
previous_histories = stored_history["histories"]
⋮----
async def generate_response(self, question: str, background_tasks: BackgroundTasks)
⋮----
async def event_stream()
⋮----
output = ""
recent_history = self.history[-self.MAX_HISTORY:]
```

## File: backend/api/infrastructure/externals/s3_storage.py

```python
class S3Storage
⋮----
def __init__(self, bucket_name: str, aws_access_key: str, aws_secret_key: str, region: str)
async def upload_file(self, file: UploadFile, destination: str) -> str
⋮----
# return f"https://{self.bucket_name}.s3.{self.s3_client.meta.region_name}.amazonaws.com/{destination}"
⋮----
async def generate_read_url(self, key: str, expires_in: int = 3600) -> str
```

## File: backend/api/infrastructure/externals/smtp_email.py

```python
logger = get_logger(__name__)
class SmtpEmail(IEmailService)
⋮----
def __init__(self)
async def send_email(self, to: str, subject: str, body: str, type: Literal[MessageType.html, MessageType.plain], attachments: List[UploadFile | dict | str] = []) -> None
⋮----
message = MessageSchema(
```

## File: backend/api/infrastructure/externals/sso_auth_provider.py

```python
logger = get_logger(__name__)
class SSOAuthProvider
⋮----
def __init__(self, sso_settings_service: SSOSettingsService, tenant_service: TenantService)
async def _provider_instance(self, provider_name: SSOProvider, sso_provider: SSOSettings) -> fastapi_sso.SSOBase
⋮----
domain = None
⋮----
tenant = await self.tenant_service.get_tenant_by_id(tenant_id=sso_provider.tenant_id)
domain = tenant.custom_domain if tenant.custom_domain else tenant.subdomain
allow_insecure_http = settings.fastapi_env != "production"
redirect_uri = get_sso_redirect_uri(provider_name, domain)
⋮----
sso: fastapi_sso.SSOBase = None
⋮----
sso = fastapi_sso.GoogleSSO(
⋮----
sso = fastapi_sso.GithubSSO(
⋮----
sso = fastapi_sso.DiscordSSO(
⋮----
sso = fastapi_sso.MicrosoftSSO(
⋮----
sso = fastapi_sso.LinkedInSSO(
⋮----
sso = fastapi_sso.NotionSSO(
⋮----
sso = fastapi_sso.GitlabSSO(
⋮----
sso = fastapi_sso.BitbucketSSO(
⋮----
sso = fastapi_sso.TwitterSSO(
⋮----
sso = fastapi_sso.FacebookSSO(
⋮----
async def _get_sso_settings(self, provider_name: SSOProvider) -> SSOSettings
⋮----
sso_provider = await self.sso_settings_service.get_provider(provider_name)
⋮----
async def login(self, provider_name: SSOProvider)
⋮----
sso_provider = await self._get_sso_settings(provider_name)
⋮----
sso = await self._provider_instance(provider_name, sso_provider)
redirect = await sso.get_login_redirect()
⋮----
async def callback(self, provider_name: SSOProvider, req: Request) -> fastapi_sso.OpenID
⋮----
user = await sso.verify_and_process(req)
⋮----
def get_redirect_uri_to_app(self) -> str
```

## File: backend/api/infrastructure/externals/stripe_resolver.py

```python
class StripeResolver(StripeSettingsRepository)
⋮----
def __init__(self)
async def get_stripe_client(self, scope: ScopeType) -> stripe.StripeClient
⋮----
tenant_stripe_api_key = await super().single_or_none()
```

## File: backend/api/infrastructure/messaging/celery_worker.py

```python
broker_url = os.getenv("CELERY_BROKER_URL")
backend_url = os.getenv("CELERY_RESULT_BACKEND")
mongo_uri = os.getenv("MONGO_URI")
mongo_db_default = os.getenv("MONGO_DB_NAME")
coolify_enabled = os.getenv("COOLIFY_ENABLED", "false").lower() == "true"
logger = get_logger(__name__)
celery_app = Celery(
## Tenant related tasks
⋮----
@celery_app.task(default_retry_delay=60, max_retries=5)
def handle_post_tenant_creation(payload: str)
⋮----
@celery_app.task(default_retry_delay=60, max_retries=5)
def handle_post_tenant_deletion(payload: str)
⋮----
@celery_app.task(default_retry_delay=60, max_retries=5)
def handle_tenant_dns_update(payload: str)
⋮----
@celery_app.task(default_retry_delay=60, max_retries=5)
def trigger_download_report(payload: str)
async def _get_current_tenant_db(tenant_id: str) -> Database
⋮----
db_name = f"tenant_{tenant_id}"
db = Database(uri=mongo_uri, models=models)
⋮----
async def _handle_post_tenant_creation_async(payload: str)
⋮----
worker_payload = WorkerPayloadDto[CreateUserDto].model_validate_json(payload)
⋮----
db = await _get_current_tenant_db(tenant_id=worker_payload.tenant_id)
worker_payload.data.tenant_id = worker_payload.tenant_id # Set tenant_id in admin user data DTO
⋮----
user_service = get_user_service()
role_service = get_role_service()
post_tenant_creation_service = PostTenantCreationTaskService(
⋮----
async def _handle_post_tenant_deletion_async(payload: str)
⋮----
worker_payload = WorkerPayloadDto[TenantDto].model_validate_json(payload)
⋮----
async def _handle_tenant_dns_update_async(payload: str)
⋮----
worker_payload = WorkerPayloadDto[dict[str, str]].model_validate_json(payload)
⋮----
hostname = worker_payload.data["custom_domain"]
⋮----
dns_service: DnsResolver = get_dns_resolver()
⋮----
result = await dns_service.resolve(hostname=hostname, expected_target=get_host_main_domain_name())
⋮----
async def _notify_dns_status(tenant_id: str, user_id: str, is_success: bool, message: str, hostname: str)
⋮----
db = await _get_current_tenant_db(tenant_id=tenant_id)
⋮----
admin_user: User = await user_service.get_user_by_id(user_id=user_id)
admin_user_dto = await admin_user.to_serializable_dict()
⋮----
async def _update_tenant_custom_domain_status(tenant_id: str, status: str)
⋮----
tenant_service: TenantService = get_tenant_service()
tenant = await tenant_service.get_tenant_by_id(tenant_id=tenant_id)
⋮----
async def _update_coolify_domain(data: UpdateDomainDto)
⋮----
coolify_service: CoolifyAppService = get_coolify_app_service()
hostname = f"https://{data.domain}"
success = await coolify_service.update_domain(data=UpdateDomainDto(domain=hostname, mode=data.mode))
⋮----
async def _handle_download_report_shared_task_async(payload: str)
⋮----
worker_payload = WorkerPayloadDto[Dict[str, str | None]].model_validate_json(payload)
⋮----
ct_id = str(worker_payload.tenant_id) if worker_payload.tenant_id else None
⋮----
db = await _get_current_tenant_db(tenant_id=ct_id)
user_service: UserService = get_user_service()
audit_log_service: AuditLogsService = get_audit_logs_service()
requesting_user: User = await user_service.get_user_by_id(user_id=worker_payload.data["requested_by"])
⋮----
attachment_file_name = f"audit_logs_report_{ct_id or 'host_'}{get_utc_now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
res = await audit_log_service.generate_audit_logs_download(action=worker_payload.data.get("action", None), tenant_id=ct_id)
# Create Excel workbook
wb = Workbook()
ws = wb.active
⋮----
# Save workbook to a temporary file
buffer = BytesIO()
```

## File: backend/api/infrastructure/persistence/**init**.py

```python

```

## File: backend/api/infrastructure/persistence/mongodb.py

```python
logger = get_logger(__name__)
models = [
class Database
⋮----
def __init__(self, uri: str, models: Sequence[type[Document] | type[UnionDoc] | type[View] | str] | None = None) -> None
async def init_db(self, db_name: str, is_tenant: bool | None) -> None
⋮----
tenant_models = [model for model in self.models if model != Tenant]
⋮----
async def init_models(self) -> None
async def get_database(self) -> AsyncDatabase
async def close(self) -> None
async def drop(self) -> None
def is_tenant_active(self) -> bool
mongo_client = Database(
```

## File: backend/api/infrastructure/persistence/repositories/billing_record_repository_impl.py

```python
logger = get_logger(__name__)
class BillingRecordRepository(BaseRepository[BillingRecord])
⋮----
def __init__(self)
async def list (self, skip: int = 0, limit: int = 10) -> BillingRecordListDto
⋮----
docs = await self.model.find_all().skip(skip).limit(limit).to_list()
total = await self.model.count()
result = BillingRecordListDto(
```

## File: backend/api/infrastructure/persistence/repositories/chat_history_ai_repository_impl.py

```python
class ChatHistoryAIRepository(BaseRepository[ChatHistoryAI])
⋮----
def __init__(self) -> None
```

## File: backend/api/infrastructure/persistence/repositories/chat_session_ai_repository_impl.py

```python
class ChatSessionAIRepository(BaseRepository[ChatSessionAI])
⋮----
def __init__(self) -> None
async def is_session_exists(self, session_id: str) -> bool
⋮----
existing = await self.single_or_none(session_id=session_id)
```

## File: backend/api/infrastructure/persistence/repositories/notification_banner_repository_impl.py

```python
class NotificationBannerRepository(BaseRepository[NotificationBannerSetting], AuditLogRepository)
⋮----
def __init__(self)
async def get_banner_setting(self) -> NotificationBannerSetting | None
⋮----
result = await self.list()
⋮----
setting = result[0]
⋮----
async def create_banner_setting(self, is_enabled: bool, message: str | None, tenant_id: PydanticObjectId | None) -> NotificationBannerSetting
⋮----
new_setting = NotificationBannerSetting(is_enabled=is_enabled, message=message, tenant_id=tenant_id)
created_setting = await self.create(new_setting.model_dump())
⋮----
user_id=None  # Todo: Need to add a new property in role.. to determine who added it.
⋮----
async def update_banner_setting(self, id: str, is_enabled: bool, message: str | None) -> NotificationBannerSetting | None
⋮----
exisiting_setting = await self.get(id)
⋮----
user_id=None  # Todo: Need to add a new property in role.. to determine who updated it.
⋮----
updated_setting = await self.update(id, exisiting_setting.model_dump(exclude_none=True))
```

## File: backend/api/infrastructure/persistence/repositories/payment_repository_impl.py

```python
logger = get_logger(__name__)
class StripeSettingsRepository(BaseRepository[StripeSettings], AuditLogRepository)
⋮----
def __init__(self)
async def store_stripe_settings(self, stripe_config: CreateStripeSettingDto) -> None
⋮----
"""
        Store or update Stripe settings in the database.
        Args:
            stripe_config (CreateStripeSettingDto): The Stripe configuration data to store.
        Raises:
            StripeSettingsNotFoundException: If there is an error storing the settings.
        """
settings = await self.single_or_none()
⋮----
user_id=None  # Todo: Need to add a new property in role.. to determine who updated it.
⋮----
settings = StripeSettings(**stripe_config.model_dump())
⋮----
user_id=None  # Todo: Need to add a new property in role.. to determine who added it.
⋮----
async def get_stored_stripe_settings(self) -> StripeSettings
⋮----
"""
        Retrieve stored Stripe settings from the database.
        Returns:
            StripeSettings: The stored Stripe settings.
        Raises:
            StripeSettingsNotFoundException: If no settings are found.
        """
⋮----
result = await self.single_or_none()
⋮----
user_id=None # Todo: Need to add a new property in role.. to determine who added it.
⋮----
class PaymentRepository(StripeSettingsRepository)
```

## File: backend/api/infrastructure/persistence/repositories/role_repository_impl.py

```python
logger = get_logger(__name__)
class RoleRepository(BaseRepository[Role], AuditLogRepository)
⋮----
def __init__(self)
async def list (self, skip: int = 0, limit: int = 10) -> RoleListDto
⋮----
docs = await self.model.find_all().skip(skip).limit(limit).to_list()
total = await self.model.count()
result = RoleListDto(
⋮----
new_role = Role(
result = await super().create(new_role.model_dump())
⋮----
user_id=None # Todo: Need to add a new property in role.. to determine who added it.
⋮----
async def update(self, role_id: str, data: UpdateRoleDto) -> Optional[Role]
⋮----
existing_role = await super().get(role_id)
updated_role = await super().update(id=role_id, data=data.model_dump(exclude_unset=True))
⋮----
existing_role_doc = await existing_role.to_serializable_dict()
⋮----
user_id=None # Todo: Need to add a new property in role.. to determine who updated it.
⋮----
async def delete(self, id: str) -> bool
⋮----
existing_role = await super().get(id)
⋮----
user_id=None # Todo: Need to add a new property in role.. to determine who deleted it.
```

## File: backend/api/infrastructure/persistence/repositories/sso_settings_provider_respository_impl.py

```python
class SSOSettingsProviderRepository(BaseRepository[SSOSettings], AuditLogRepository)
⋮----
def __init__(self)
async def get_by_id(self, id: str) -> SSOSettings | None
⋮----
sso_settings: SSOSettings = await self.get(id=id)
⋮----
async def update_sso_settings(self, id: str, data: SSOSettings) -> SSOSettings | None
⋮----
updated_sso_settings: SSOSettings | None = await super().update(id=id, data=SSOSettings.model_dump(data, exclude_unset=True))
⋮----
async def create_sso_settings(self, data: SSOSettings) -> PydanticObjectId
⋮----
new_sso_settings: SSOSettings = await super().create(data=SSOSettings.model_dump(data))
⋮----
async def delete_sso_settings(self, id: str) -> bool
⋮----
result: bool =  await super().delete(id=id)
⋮----
async def list_sso_settings(self) -> list[SSOSettings]
⋮----
sso_settings_list: list[SSOSettings] = await super().list()
```

## File: backend/api/infrastructure/persistence/repositories/storage_settings_repository_impl.py

```python
logger = get_logger(__name__)
class StorageSettingsRepository(BaseRepository[StorageSettings], AuditLogRepository)
⋮----
def __init__(self)
async def disable_all_active_storages(self) -> None
⋮----
total = await super().count({"is_enabled": True})
⋮----
async def configure_storage(self, setting: StorageSettings) -> PydanticObjectId
⋮----
existing: StorageSettings | None = await self.single_or_none(provider=setting.provider.value)
⋮----
result = await super().create(data=setting.model_dump())
sett: StorageSettings = await super().get(result.id)
⋮----
result = await super().update(id=str(existing.id), data=setting.model_dump())
⋮----
is_enabled_change = existing.is_enabled != setting.is_enabled
region_change = existing.region != setting.region
changes = {}
⋮----
async def get_storages(self) -> list[AvailableStorageProviderDto]
⋮----
settings = await super().list()
result = []
⋮----
serializable = await setting.to_serializable_dict()
⋮----
async def get_storage_by_provider(self, provider: StorageProvider) -> StorageSettings | None
⋮----
setting: StorageSettings | None = await super().single_or_none(provider=provider.value)
⋮----
async def reset_storage(self, id: str, data: StorageSettings) -> None
⋮----
result = await super().update(id=id, data=data.model_dump())
```

## File: backend/api/infrastructure/persistence/repositories/subscription_plan_repository_impl.py

```python
# This repository is used to represent subscription plans in the system Not used by Stripe directly
⋮----
class SubscriptionPlanRepository(BaseRepository[SubscriptionPlan], AuditLogRepository)
⋮----
def __init__(self)
async def get_subscription_plan_by_id(self, subscription_id: str) -> SubscriptionPlan | None
async def get_subscription_plan_by_user_id(self, user_id: str) -> SubscriptionPlan | None
async def get_subscription_plans_by_tenant(self, tenant_id: str) -> SubscriptionPlan | None
async def create_subscription_plan(self, subscription_plan: CreateSubscriptionPlanDto) -> PydanticObjectId | None
⋮----
subcription = SubscriptionPlan(
result = await super().create(subcription.model_dump(exclude_none=True))
⋮----
async def update_subscription_plan(self, subscription_id: str, update_data: SubscriptionPlan) -> None
async def delete_subscription_plan(self, subscription_id: str) -> None
⋮----
exisiting =  await self.get_subscription_plan_by_id(subscription_id)
```

## File: backend/api/infrastructure/persistence/repositories/tenant_repository_impl.py

```python
logger = get_logger(__name__)
default_enable_features = [
class TenantRepository(BaseRepository[Tenant], AuditLogRepository)
⋮----
def __init__(self)
async def list(self, skip: int = 0, limit: int = 10) -> TenantListDto
⋮----
docs = await self.model.find_all().skip(skip).limit(limit).to_list()
total = await self.model.count()
tenant_dto = [TenantDto(**doc.model_dump()) for doc in docs]
result = TenantListDto(
⋮----
async def create(self, data: CreateTenantDto) -> PydanticObjectId | None
⋮----
new_tenant = Tenant(
result = await super().create(new_tenant.model_dump(exclude_none=True))
```

## File: backend/api/infrastructure/persistence/repositories/user_magic_link_repository_impl.py

```python
logger = get_logger(__name__)
class UserMagicLinkRepository(BaseRepository[UserMagicLink], AuditLogRepository)
⋮----
def __init__(self)
async def create_magic_link(self, user_id: str, token: str) -> UserMagicLink
⋮----
"""
        Create a magic link for the given user ID with the provided token. Raises an exception if the user has exceeded the allowed number of magic link requests.
        Args:
            user_id (str): The ID of the user to create the magic link for.
            token (str): The token to associate with the magic link.
        Returns:
            UserMagicLink: The created UserMagicLink document.
        """
count = await self.count({"user_id": PydanticObjectId(user_id)})
⋮----
new_link = UserMagicLink(
res = await self.create(data=new_link.model_dump())
```

## File: backend/api/infrastructure/persistence/repositories/user_passkey_repository_impl.py

```python
logger = get_logger(__name__)
class UserPasskeyRepository(BaseRepository[UserPasskey], AuditLogRepository)
⋮----
def __init__(self)
class UserPasskeyChallengesRepository(BaseRepository[Challenges], AuditLogRepository)
⋮----
async def save_challenge(self, email: str, challenge: bytes, type: Literal["registration", "authentication"], tenant_id: Optional[PydanticObjectId] = None) -> Challenges
⋮----
"""
            Save challenges with current user email, return challenge
        """
cv = base64.urlsafe_b64encode(challenge).decode("utf-8").rstrip("=")
⋮----
data = Challenges(
res = await self.create(data=data.model_dump())
⋮----
async def get_challenge(self, email: str, type: Literal["registration", "authentication"]) -> Optional[Challenges]
⋮----
"""
            Returns current challenge for the given email if found otherwise, None
        """
⋮----
async def delete_challenge(self, email: str, type: Literal["registration", "authentication"]) -> bool
⋮----
"""
            Delete the current challenge for the given email, Returns False if its not found, Otherwise, True
        """
existing = await self.get_challenge(email=email, type=type)
```

## File: backend/api/infrastructure/persistence/repositories/user_password_reset_repository_impl.py

```python
logger = get_logger(__name__)
class UserPasswordResetRepository(BaseRepository[UserPasswordReset], AuditLogRepository)
⋮----
def __init__(self)
async def set_password_reset(self, user_id: str, first_name: str, tenant_id: str | None = None) -> UserPasswordReset
⋮----
existing = await self.single_or_none(user_id=PydanticObjectId(user_id))
token_secret = PydanticObjectId()
⋮----
ex = await existing.save()
⋮----
new_reset = UserPasswordReset(
⋮----
res = await self.create(data = new_reset.model_dump())
⋮----
async def delete_by_user_id(self, user_id: str) -> bool
```

## File: backend/api/infrastructure/persistence/repositories/user_preference_repository_impl.py

```python
logger = get_logger(__name__)
class UserPreferenceRepository(BaseRepository[UserPreference], AuditLogRepository)
⋮----
def __init__(self)
async def get_preferences(self, user_id: str) -> UserPreference | None
⋮----
user_pref = await self.single_or_none(user_id=PydanticObjectId(user_id))
⋮----
async def update_preferences(self, user_id: str, preferences: dict[str, Any]) -> None
⋮----
existing = await self.single_or_none(user_id=PydanticObjectId(user_id))
⋮----
new_pref = UserPreference(
⋮----
existing =  await self.get_preferences(user_id=user_id)
⋮----
prf = existing.preferences.copy()
⋮----
payload = UserPreference(
```

## File: backend/api/infrastructure/persistence/repositories/user_repository_impl.py

```python
logger = get_logger(__name__)
class UserRepository(BaseRepository[User], AuditLogRepository)
⋮----
def __init__(self)
async def list (self, skip: int = 0, limit: int = 10) -> UserListDto
⋮----
docs = await self.model.find_all().skip(skip).limit(limit).to_list()
total = await self.model.count()
result = UserListDto(
⋮----
new_user = User(
d = new_user.model_dump()
result = await super().create(data=d)
⋮----
async def update(self, user_id: str, data: UpdateUserDto) -> Optional[User]
⋮----
exising_user = await self.get(id=user_id)
⋮----
updated_user = await super().update(id=user_id, data=data.model_dump(exclude_unset=True))
⋮----
async def delete(self, user_id: str) -> bool
⋮----
existing_user = await self.get(id=user_id)
⋮----
result = await super().delete(id=user_id)
```

## File: backend/api/infrastructure/persistence/seeder.py

```python
fake = Faker()
logger = get_logger(__name__)
LOCK_FILE = "seed.lock"
async def seed_initial_data()
⋮----
user_repo: UserRepository = get_user_service().user_repository
role_repo: RoleRepository = get_role_service().role_repository
existing_roles = await role_repo.count()
roles = get_seed_roles()
⋮----
is_role_existing = await role_repo.single_or_none(name=role.name)
⋮----
create_role = CreateRoleDto(
role_id = await role_repo.create(data=create_role)
update_role = UpdateRoleDto(
⋮----
# Example: Create an admin user if not exists
existing_admin = await user_repo.single_or_none(email=settings.admin_email)
⋮----
existing_role_admin = await role_repo.single_or_none(name=RoleType.ADMIN)
create_user = CreateUserDto(
user_id = await user_repo.create(data=create_user)
⋮----
update_user = UpdateUserDto(role_id=str(existing_role_admin.id))
⋮----
# Add bunch of fake users
existing_users = await user_repo.count()
⋮----
existing_role_guest = await role_repo.single_or_none(name=RoleType.GUEST)
⋮----
fake_user = CreateUserDto(
⋮----
# Create lock file to indicate seeding is done
```

## File: backend/api/infrastructure/security/current_user.py

```python
logger = get_logger(__name__)
⋮----
payload = await token_service.decode_token(token, type="access_token")
⋮----
user = await user_service.get_user_by_id(user_id=str(payload.sub))
⋮----
user_doc = await user.to_serializable_dict()
user_dto = UserDto(**user_doc)
role = await role_service.get_role_by_id(user.role_id)
role_doc = await role.to_serializable_dict()
# Get subscription plan for the user
subscription =  await subscription_service.get_subscription_plan_by_user_id(str(user.id))
me_response = MeResponseDto(**user_dto.model_dump(), role=role_doc, subscription=subscription)
⋮----
CurrentUser = Annotated[MeResponseDto, Depends(get_current_user)]
⋮----
"""
        Optional current user information. If the user is not authenticated, this will be None.
        Useful for endpoints that can be accessed by both authenticated and unauthenticated users.
    """
⋮----
CurrentUserOptional = Annotated[MeResponseDto | None, Depends(current_user_optional)]
```

## File: backend/api/infrastructure/security/jwt_token_service.py

```python
logger  = get_logger(__name__)
class JwtTokenService
⋮----
def __init__(self)
async def get_access_token(self, payload: TokenPayloadDto, expires_delta: timedelta | None = None) -> AccessTokenDto
⋮----
to_encode = payload.model_dump()
⋮----
expire = get_utc_now() + expires_delta
⋮----
expire = get_utc_now() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
⋮----
encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
⋮----
async def get_refresh_token(self, payload: TokenPayloadDto, expires_delta: timedelta | None = None) -> RefreshTokenDto
⋮----
to_encode = {"sub": str(payload.sub), "tenant_id": str(payload.tenant_id) if payload.tenant_id else None, "type": "refresh"}
⋮----
expire = get_utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
⋮----
encoded_jwt = jwt.encode(to_encode, REFRESH_TOKEN_SECRET, algorithm=REFRESH_ALGORITHM)
⋮----
async def generate_tokens(self, payload: TokenPayloadDto) -> TokenSetDto
⋮----
"""
            Generate access and refresh tokens.
        """
⋮----
access_token = await self.get_access_token(payload)
refresh_token = await self.get_refresh_token(payload)
⋮----
payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
⋮----
payload = jwt.decode(token, REFRESH_TOKEN_SECRET, algorithms=[REFRESH_ALGORITHM])
⋮----
async def verify_activation_token(self, token: str) -> VerifyEmailTokenPayloadDto
⋮----
"""
        Verify and decode the activation token.
        Returns user_id and email if valid, raises exception if invalid.
        """
⋮----
async def verify_password_reset_token(self, token: str, jwt_secret: str) -> VerifyEmailTokenPayloadDto
⋮----
"""
            Verify and decode the password reset token.
            Returns user_id and email if valid, raises exception if invalid.
        """
⋮----
payload = jwt.decode(token, jwt_secret, algorithms=[ALGORITHM])
⋮----
async def verify_change_email_token(self, token: str) -> VerifyEmailTokenPayloadDto
⋮----
"""
            Verify and decode the change email token.
            Returns user_id and email if valid, raises exception if invalid.
        """
⋮----
async def encode_activation_token(self, payload: ActivationTokenPayloadDto) -> str
⋮----
"""
            Generate a JWT token for account activation.
        """
expire = get_utc_now() + timedelta(hours=ACTIVATION_TOKEN_EXPIRE_HOURS)
data = {
```

## File: backend/api/infrastructure/security/passkey_service.py

```python
logger = get_logger(__name__)
def decode_base64url(data: str) -> bytes
class PasskeyService()
⋮----
async def _get_rep_id_and_name(self, tenant_id: PydanticObjectId | None = None) -> PasskeyRepDto
⋮----
"""
          Helper method to get the relying party ID and name based on tenant information.
        """
⋮----
tenant_service: TenantService = get_tenant_service()
⋮----
tenant = await tenant_service.get_tenant_by_id(tenant_id=str(tenant_id))
rp_id = tenant.custom_domain if tenant.custom_domain else tenant.subdomain
rp_name = tenant.name.title()
⋮----
async def register_options(self, user_dto: UserDto) -> str
⋮----
"""
        Generate registration options for a user. If the user does not have a passkey record, create one. Add a challenge to the challenges collection with an expiration time.
        """
user = await self.user_passkey_repo.single_or_none(user_email=user_dto.email)
⋮----
user_passkey = UserPasskey(user_email=user_dto.email, credentials=[], tenant_id=user_dto.tenant_id)
⋮----
rep = await self._get_rep_id_and_name(tenant_id=user_dto.tenant_id)
registration_options = generate_registration_options(
⋮----
async def complete_registration(self, email: EmailStr, credential: dict = Body(...)) -> bool
⋮----
"""
        Complete the registration process by verifying the registration response.
        If successful, store the new credential in the user's passkey record and remove the used challenge.
        Raises PassKeyException on failure.
        """
expected_challenge = await self.challenges_repo.get_challenge(email=email, type="registration")
⋮----
rep = await self._get_rep_id_and_name(expected_challenge.tenant_id)
⋮----
verified = verify_registration_response(
user_passkey = await self.user_passkey_repo.single_or_none(user_email=email)
⋮----
cred_data = Credential(
⋮----
async def auth_login_options(self, user_dto: UserDto) -> str
⋮----
"""
        Generate authentication options for a user. Raises PassKeyException if no credentials are found.
        """
⋮----
user_credentials = user.credentials
allow_credentials: List[PublicKeyCredentialDescriptor] = [
rep = await self._get_rep_id_and_name(tenant_id=user.tenant_id)
login_options = generate_authentication_options(
⋮----
async def complete_auth_login(self, email: EmailStr, credential: dict = Body(...)) -> bool
⋮----
"""
        Complete the authentication process by verifying the authentication response.
        If successful, update the sign-in count for the credential and remove the used challenge.
        Raises PassKeyException on failure.
        """
expected_challenge = await self.challenges_repo.get_challenge(email=email, type="authentication")
⋮----
user_credentials = user_passkey.credentials
⋮----
verified = verify_authentication_response(
# Update sign-in count
⋮----
async def has_passkeys(self, email: EmailStr) -> bool
⋮----
"""
        Check if a user has any registered passkeys.
        """
user = await self.user_passkey_repo.single_or_none(user_email=email)
⋮----
async def get_registered_passkeys(self, email: EmailStr) -> List[Credential]
⋮----
"""
        Retrieve registered passkeys for a user.
        """
⋮----
async def delete_registered_passkey(self, email: EmailStr, credential_id: str) -> None
⋮----
"""
        Delete a registered passkey for a user by credential ID.
        """
⋮----
original_count = len(user_passkey.credentials)
```

## File: backend/api/interfaces/**init**.py

```python

```

## File: backend/api/interfaces/api_controllers/account_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/account", tags=["Account"])
⋮----
token_set: TokenSetDto = await auth_service.login(LoginRequestDto(
⋮----
token = TokenRefreshRequestDto(refresh_token=cookies.refresh_token)
token_set = await auth_service.refresh_token(token)
⋮----
role_guest = await role_service.find_by_name(name=RoleType.GUEST)
⋮----
def process_user_creation(tenant_id: PydanticObjectId)
⋮----
payload=WorkerPayloadDto[CreateUserDto](
⋮----
user_service = await auth_service.get_user_service()
user = await user_service.find_by_email(email=email)
user_dto = await user.to_serializable_dict()
result = await passkey_service.register_options(user_dto=UserDto(**user_dto))
⋮----
result = await passkey_service.complete_registration(email, credential)
⋮----
user_doc = await user.to_serializable_dict()
⋮----
result = await passkey_service.complete_auth_login(email, credential)
⋮----
token_set: TokenSetDto = await auth_service.login_with_passkey(email=email)
⋮----
user_dto = UserDto(**user_doc)
⋮----
token = unquote(token)
⋮----
is_valid = await magic_link_service.validate_magic_link(user_id=user_id, token=token)
⋮----
token_set: TokenSetDto = await auth_service.login_with_magic_link(user_id=user_id)
⋮----
user = await sso_auth_provider.callback(provider_name, req)
token_set = await auth_service.login_with_sso(provider_name=provider_name, user_info=user , tenant_id=tenant_id)
response = RedirectResponse(url=f"{sso_auth_provider.get_redirect_uri_to_app()}/dashboard", status_code=status.HTTP_308_PERMANENT_REDIRECT)
```

## File: backend/api/interfaces/api_controllers/ai_endpoint.py

```python
router = APIRouter(prefix="/ai", dependencies=[Depends(check_feature_access(FeatureEnum.CHAT))])
⋮----
# current_user: CurrentUser
⋮----
@router.get("/new_session", response_model=NewSessionResponseDto)
async def create_new_session(current_user: CurrentUser)
⋮----
payload: dict[str, Any] = {}
⋮----
chat = OllamaChat(
stream = await chat.generate_response(query.question, background_tasks)
```

## File: backend/api/interfaces/api_controllers/app_configuration_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/app_configuration")
⋮----
async def get_tenant(service: TenantService, tenant_id: str | None) -> Tenant | None
⋮----
current_tenant = None
⋮----
t = await service.get_tenant_by_id(tenant_id)
current_tenant = Tenant(**t.model_dump())
⋮----
user_pref_doc = None
⋮----
is_user_logged_in = False
⋮----
user_preferences = await user_pref_service.get_preferences(user_id=current_user.id)
user_pref_doc = await user_preferences.to_serializable_dict() if user_preferences is not None else None
is_user_logged_in = True
passkey_enabled = await passkey_service.has_passkeys(email=current_user.email)
⋮----
tenant_id = str(current_user.tenant_id) if current_user and current_user.tenant_id else str(tenant_id) if tenant_id else None
t = await get_tenant(tenant_service, str(tenant_id))
current_tenant = TenantDto(**t.model_dump()) if t else None
enabled_sso_provider = await sso_settings_service.get_only_enabled_providers()
enabled_sso_provider_names = [str(sso.provider) for sso in enabled_sso_provider.items]
# Fetch available AI models from Ollama
# available_ai_models = OllamaModels().list_models()
```

## File: backend/api/interfaces/api_controllers/audit_logs_endpoint.py

```python
router = APIRouter(prefix="/audit-logs",  dependencies=[
⋮----
tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
⋮----
payload = WorkerPayloadDto[Dict[str, str | None]](
```

## File: backend/api/interfaces/api_controllers/billing_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/billing", dependencies=[Depends(check_feature_access(feature_name=Feature.STRIPE))])
⋮----
scope = "host"
⋮----
scope = "tenant"
```

## File: backend/api/interfaces/api_controllers/checkout_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/checkouts", dependencies=[
⋮----
host_main_domain = f"https://{get_host_main_domain_name()}"
url = await billing_service.create_host_check_out_session_for_tenant(frontend_url=host_main_domain, checkout_req=checkout_req)
⋮----
tenant = await tenant_service.get_tenant_by_id(current_user.tenant_id)
domain = tenant.subdomain
⋮----
domain = tenant.custom_domain
frontend_url = f"https://{domain}"
url = await billing_service.create_tenant_check_out_session_for_tenant_users(
```

## File: backend/api/interfaces/api_controllers/dashboard_endpoint.py

```python
router = APIRouter(prefix="/dashboard") # type: ignore
⋮----
# Simulate fetching data based on the filter
⋮----
# Base match query
match_stage = {}
⋮----
match_stage = {"created_at": {"$gte": start, "$lte": end}}
# Aggregation pipeline for time series
pipeline = [
timeseries =  await user_service.aggregate(pipeline)
total_users = await user_service.total_count()
joined_users = await user_service.total_count(match_stage) if match_stage else total_users
data = {
```

## File: backend/api/interfaces/api_controllers/features_endpoint.py

```python
router = APIRouter(prefix="/features")
```

## File: backend/api/interfaces/api_controllers/health_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/health")
⋮----
@router.get("/", status_code=status.HTTP_200_OK, response_model=HealthCheckResponseDto)
async def health_check()
```

## File: backend/api/interfaces/api_controllers/invoice_endpoint.py

```python
router = APIRouter(
⋮----
scope = "host"
⋮----
scope = "tenant"
```

## File: backend/api/interfaces/api_controllers/manage_security_endpoint.py

```python
router = APIRouter(prefix="/security")
⋮----
"""
    Endpoint to retrieve registered passkeys for the current user.
    """
passkeys = await passkey_service.get_registered_passkeys(email=current_user.email)
res = [RegisteredPasskeyCredentialsDto(**pk.model_dump()) for pk in passkeys]
⋮----
"""
    Endpoint to delete a registered passkey for the current user.
    """
```

## File: backend/api/interfaces/api_controllers/notification_endpoint.py

```python
router = APIRouter(prefix="/notifications")
```

## File: backend/api/interfaces/api_controllers/permissions_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/permissions")
⋮----
permissions = [{"name": perm.value} for perm in Permission]
⋮----
permissions = filter(
permissions = list(permissions)
```

## File: backend/api/interfaces/api_controllers/prices_endpoint.py

```python
router = APIRouter(
⋮----
scope = "tenant" if current_user.tenant_id else "host"
```

## File: backend/api/interfaces/api_controllers/product_endpoint.py

```python
router = APIRouter(
⋮----
scope = "tenant" if current_user.tenant_id else "host"
```

## File: backend/api/interfaces/api_controllers/role_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/roles", tags=["Roles"])
⋮----
roles = await service.search_role_by_name(name)
⋮----
new_user_id = await service.create_role(data)
⋮----
role = await service.get_role_by_id(role_id)
role_doc = await role.to_serializable_dict()
⋮----
role_doc = await service.update_role(role_id=role_id, role_data=data)
serialized_role = await role_doc.to_serializable_dict()
⋮----
users_role = await user_service.total_count(params={"role_id": role_id})
```

## File: backend/api/interfaces/api_controllers/sso_settings_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/ssos", tags=["SSO Settings"], dependencies=[Depends(check_permissions_for_current_role(required_permissions=[Permission.FULL_ACCESS]))])
```

## File: backend/api/interfaces/api_controllers/storage_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(
⋮----
res = await setting_service.get_storages()
⋮----
@router.get("/available", response_model=List[dict[str, str]])
async def get_available_providers()
⋮----
providers = [{"name": p.value} for p in StorageProvider]
⋮----
# We don't expect storage settings to be configured with host context as they are global settings. Only tenants with proper permissions can configure them.
```

## File: backend/api/interfaces/api_controllers/stripe_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(
⋮----
SUPPORTED_EVENTS = {
⋮----
event = None
stripe_webhook_secret = None
payload = await request.body()
stripe_settings = await stripe_setting_service.get_stripe_secret()
# Check the request context for tenant or host. If tenant then get tenant webhook secret otherwise use host from env
⋮----
# Tenant-scoped webhook
⋮----
stripe_webhook_secret = stripe_settings.stripe_webhook_secret
⋮----
# Host-scoped webhook
stripe_webhook_secret = settings.stripe_webhook_secret
⋮----
sig_header = request.headers.get("stripe-signature")
event = stripe.Webhook.construct_event(
⋮----
# Invalid payload
⋮----
# Extract the event type and object
event_type = event["type"]
data_object = event["data"]["object"]
# ------------------------------------------------------------------
# Handle relevant Stripe events
⋮----
tenant_id = invoice.get("metadata", {}).get("tenant_id")
billing_record = await billing_record_service.from_stripe_invoice_paid(
```

## File: backend/api/interfaces/api_controllers/tenant_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/tenants", tags=["Tenants"])
⋮----
tenant_id = await service.create_tenant(data)
response = CreateTenantResponseDto(id=str(tenant_id))
admin_user = CreateUserDto(
payload=WorkerPayloadDto(
⋮----
tenant: Tenant = await service.get_tenant_by_id(tenant_id=id)
tenant_doc = TenantDto(**tenant.model_dump())
⋮----
tenant = await service.find_by_name(name)
⋮----
tenant = await service.find_by_subdomain(subdomain)
⋮----
is_available = False
⋮----
is_available = True
⋮----
tenant = await tenant_service.get_tenant_by_id(tenant_id)
⋮----
payload=WorkerPayloadDto[dict[str, str]](
⋮----
tenant_doc = await tenant_service.get_tenant_by_id(tenant_id)
⋮----
all_plans = await billing_record_service.list_plans('host')
⋮----
trial = list(filter(lambda p: p.trial_period_days is not None and p.trial_period_days > 0, all_plans.plans))
⋮----
id = trial[0].id
⋮----
subcription = await subcription_plan_service.get_subscription_plans_by_tenant(tenant_id)
```

## File: backend/api/interfaces/api_controllers/user_endpoint.py

```python
logger = get_logger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])
⋮----
role_guest = await role_service.find_by_name(name=RoleType.GUEST)
⋮----
new_user_id = await service.create_user(data)
⋮----
user = await service.get_user_by_id(user_id)
user_doc = await user.to_serializable_dict()
⋮----
user_doc = await service.update_user(user_id=user_id, user_data=data)
serialized_user = await user_doc.to_serializable_dict()
⋮----
image_url = await file_service.get_file_url(image_key)
⋮----
file_location = await file_service.upload_file(file)
⋮----
exsisting_user = await user_service.get_user_by_id(user_id)
⋮----
user = await user_service.update_user(user_id=user_id, user_data=UpdateUserDto(role_id=role_update.role_id))
⋮----
user = await user_service.update_user(user_id=user_id, user_data=UpdateUserDto(role_id=None))
```

## File: backend/api/interfaces/email_templates/activation_template_html.py

```python
def activation_template_html(user_first_name: str, activation_link: str) -> str
⋮----
"""HTML email template for account activation."""
```

## File: backend/api/interfaces/email_templates/dns_notification_html.py

```python
def dns_notification_email(user_first_name: str, hostname: str, status: str) -> str
⋮----
"""HTML email template for DNS configuration status."""
```

## File: backend/api/interfaces/email_templates/download_template_html.py

```python
def download_template(user_first_name: str) -> str
⋮----
"""HTML email template for Download reports."""
```

## File: backend/api/interfaces/email_templates/email_changes_confirmation_template_html.py

```python
def email_changes_confirmation_template_html(user_first_name: str, email_change_link: str) -> str
⋮----
"""
    HTML email template for confirming email changes.
    """
```

## File: backend/api/interfaces/email_templates/magic_link_login_template_html.py

```python
def magic_link_login_template(user_first_name: str, magic_link: str) -> str
⋮----
"""HTML email template for magic link login."""
```

## File: backend/api/interfaces/email_templates/notify_email_changes_template_html.py

```python
def notify_email_change_template_html(user_first_name: str) -> str
⋮----
"""
    HTML email template for notifying users about their new email changes.
    """
```

## File: backend/api/interfaces/email_templates/notify_password_changes_template_html.py

```python
def notify_password_change_template_html(user_first_name: str) -> str
⋮----
"""
    HTML email template for notifying users about password changes.
    """
```

## File: backend/api/interfaces/email_templates/password_reset_email_template_html.py

```python
def password_reset_email_template_html(user_first_name: str, password_reset_link: str) -> str
⋮----
"""
    HTML email template for password reset.
    """
```

## File: backend/api/interfaces/email_templates/tenant_subdomain_active_html.py

```python
def tenant_subdomain_active_html(subdomain_link: str) -> str
⋮----
"""HTML email template for tenant subdomain activation."""
```

## File: backend/api/interfaces/email_templates/welcome_email_html.py

```python
def activation_template_html(user_first_name: str, activation_link: str) -> str
⋮----
"""HTML email template for welcome newly created user or tenant."""
```

## File: backend/api/interfaces/middlewares/audit_logs_read_middleware.py

```python
logger = get_logger(__name__)
class AuditLogsReadMiddleware(BaseHTTPMiddleware)
⋮----
def _get_entity_name(self, path: str) -> str
async def dispatch(self, request: Request, call_next)
⋮----
token = None
⋮----
token = request.headers["authorization"].replace("Bearer ", "")
user_service = get_user_service()
jwt_service = get_jwt_token_service()
role_service = get_role_service()
subscription_service = get_subscription_plan_service()
⋮----
current_user = await current_user_optional(
entity = self._get_entity_name(request.url.path)
```

## File: backend/api/interfaces/middlewares/redis_cache_middleware.py

```python
logger = get_logger(__name__)
redis_cache_expiry = 300  # Cache expiry time in seconds (5 minutes)
redis: Redis =  from_url(url=settings.redis_uri, decode_responses=True)
not_allowed_cache_paths = [
class RedisCacheMiddleware(BaseHTTPMiddleware)
⋮----
def __init__(self, app, expiry: int = redis_cache_expiry)
async def _clear_cache(self, user_id: str, tenant_id: str)
async def dispatch(self, request: Request, call_next)
⋮----
token = None
⋮----
token = request.headers["authorization"].replace("Bearer ", "")
⋮----
user_service = get_user_service()
jwt_service = get_jwt_token_service()
role_service = get_role_service()
subscription_service = get_subscription_plan_service()
current_user = await current_user_optional(
⋮----
current_user = None
user_id = getattr(current_user, "id", "anonymous")
tenant_id = getattr(current_user, "tenant_id", "default")
cache_base = f"cu{user_id}:ct{tenant_id}:{request.url.path}?{request.url.query}"
⋮----
cache_key = f"cache:{cache_base}"
⋮----
# Only cache GET requests
⋮----
# --- Try reading from cache
cached_value = await redis.get(cache_key)
⋮----
cached = json.loads(cached_value)
⋮----
# --- Cache miss → execute route handler
response = await call_next(request)
# Read and rebuild response body
body = b"".join([chunk async for chunk in response.body_iterator])
body_text = body.decode()
content_type = response.headers.get("content-type", "")
# Cache only 200 OK + JSON responses
⋮----
to_cache = json.dumps({
⋮----
# Return new Response (since body_iterator is consumed)
```

## File: backend/api/interfaces/middlewares/tenant_middleware.py

```python
logger = get_logger(__name__)
class TenantMiddleware(BaseHTTPMiddleware)
⋮----
async def dispatch(self, request: Request, call_next)
⋮----
tenant_id: Optional[str] = None
tenant_host = get_tenant_host(request)
⋮----
tenant_service = get_tenant_service()
⋮----
tenant: Tenant
⋮----
tenant = await tenant_service.find_by_subdomain(tenant_host)
⋮----
tenant = await tenant_service.find_by_custom_domain(tenant_host)
tenant_id = str(tenant.id)
⋮----
tenant_id = request.headers.get("X-Tenant-ID") or request.query_params.get('tenant_id') or None
⋮----
# Initialize the database for the tenant and initialize Beanie with tenant-specific models
⋮----
# If no tenant ID is provided, ensure we are using the default database
⋮----
# Reset to default DB if no tenant ID is provided
⋮----
def get_tenant_host(request: Request) -> Optional[str]
⋮----
"""
    Extracts the tenant-specific host (subdomain) from the incoming request.
    Returns None if the host matches the main domain.
    """
# Try HTTP/2 ':authority' → fall back to 'host' → finally use parsed URL
host = (
⋮----
host = host.split(':')[0].lower()  # Remove port if present and convert to lowercase
# Compare against main domain
main_domain = get_host_main_domain_name().lower()
⋮----
# If the host is exactly the main domain, return None (no tenant)
⋮----
# Handle localhost with optional
⋮----
def get_tenant_id(request: Request) -> PydanticObjectId | None
def frontend_dynamic_host(request: Request) -> str
FrontendHost = Annotated[str, Depends(frontend_dynamic_host)]
```

## File: backend/api/interfaces/security/role_checker.py

```python
# Factory methods for common permission patterns
⋮----
def check_permissions_for_current_role(required_permissions: List[Permission], any_permission: bool = False, allow_self_access: bool = False) -> UserAccessManagement
⋮----
"""Returns a UserAccessManagement instance with the specified permissions. Any of the permissions can be satisfied if any_permission is True."""
```

## File: backend/api/ui/assets/index-DQtIvVfc.js

```javascript
function LG(e,t)
⋮----
`+le+o+_t}var Gt=!1;function Zt(o,c)
⋮----
`+L[g].replace(" at new "," at ");return o.displayName&&fe.includes("<anonymous>")&&(fe=fe.replace("<anonymous>",o.displayName)),fe}while(1<=g&&0<=b);break}}}finally
⋮----
`+g.stack}}var mr=Object.prototype.hasOwnProperty,V=e.unstable_scheduleCallback,te=e.unstable_cancelCallback,se=e.unstable_shouldYield,ke=e.unstable_requestPaint,_e=e.unstable_now,Ne=e.unstable_getCurrentPriorityLevel,Ie=e.unstable_ImmediatePriority,jt=e.unstable_UserBlockingPriority,Nt=e.unstable_NormalPriority,qn=e.unstable_LowPriority,Pr=e.unstable_IdlePriority,ni=e.log,ja=e.unstable_setDisableYieldValue,Qi=null,On=null;function ri(o)
`).replace(sG,"")}function oO(o,c)
Please change the parent <Route path="$
⋮----
`)},Gee=0,tu=[];function Zee(e)
`)}const bm=e=>(t,n,r,i)=>
⋮----
`))}}const D6=
⋮----
`)}x.write("payload.value = newResult;"),x.write("return payload;");const E=x.compile();return(C,A)=>E(y,C,A)};let a;const s=_u,u=!hg.jitless,m=u&&pz.value,h=t.catchall;let p;e._zod.parse=(y,x)=>
Set the \`cycles\` parameter to \`"ref"\` to resolve cyclical schemas with defs.`)}for(const h of this.seen.entries())
⋮----
For more information, see https://radix-ui.com/primitives/docs/components/$
`,t)}function vle()
⋮----
For more information, see https://radix-ui.com/primitives/docs/components/alert-dialog`;return v.useEffect(()=>
⋮----
height and width.`,T,P,i,a,s,u,n),v.createElement("div",
⋮----
`).concat(y.x,",").concat(y.y," Z")}else p+="L ".concat(t,",").concat(n," Z");return p},rge=e=>
⋮----
A`).concat(a,",").concat(a,",0,0,").concat(+(h<0),",").concat(A.x,",").concat(A.y,"Z")}else E+="L".concat(t,",").concat(n,"Z");return E},ige=
⋮----
`).concat(A.x,",").concat(A.y),T=Qn(e.id)?Gf("recharts-radial-line-"):e.id;return v.createElement("text",No(
⋮----
color: hsl($
```

## File: backend/api/ui/assets/index-MCH6mY5D.css

```css
@layer properties {
  @supports (((-webkit-hyphens: none)) and (not (margin-trim: inline))) or
    ((-moz-orient: inline) and (not (color: rgb(from red r g b)))) {
    *,
    :before,
    :after,
    ::backdrop {
      --tw-translate-x: 0;
      --tw-translate-y: 0;
      --tw-translate-z: 0;
      --tw-scale-x: 1;
      --tw-scale-y: 1;
      --tw-scale-z: 1;
      --tw-rotate-x: initial;
      --tw-rotate-y: initial;
      --tw-rotate-z: initial;
      --tw-skew-x: initial;
      --tw-skew-y: initial;
      --tw-space-y-reverse: 0;
      --tw-space-x-reverse: 0;
      --tw-border-style: solid;
      --tw-gradient-position: initial;
      --tw-gradient-from: #0000;
      --tw-gradient-via: #0000;
      --tw-gradient-to: #0000;
      --tw-gradient-stops: initial;
      --tw-gradient-via-stops: initial;
      --tw-gradient-from-position: 0%;
      --tw-gradient-via-position: 50%;
      --tw-gradient-to-position: 100%;
      --tw-leading: initial;
      --tw-font-weight: initial;
      --tw-tracking: initial;
      --tw-ordinal: initial;
      --tw-slashed-zero: initial;
      --tw-numeric-figure: initial;
      --tw-numeric-spacing: initial;
      --tw-numeric-fraction: initial;
      --tw-shadow: 0 0 #0000;
      --tw-shadow-color: initial;
      --tw-shadow-alpha: 100%;
      --tw-inset-shadow: 0 0 #0000;
      --tw-inset-shadow-color: initial;
      --tw-inset-shadow-alpha: 100%;
      --tw-ring-color: initial;
      --tw-ring-shadow: 0 0 #0000;
      --tw-inset-ring-color: initial;
      --tw-inset-ring-shadow: 0 0 #0000;
      --tw-ring-inset: initial;
      --tw-ring-offset-width: 0px;
      --tw-ring-offset-color: #fff;
      --tw-ring-offset-shadow: 0 0 #0000;
      --tw-outline-style: solid;
      --tw-blur: initial;
      --tw-brightness: initial;
      --tw-contrast: initial;
      --tw-grayscale: initial;
      --tw-hue-rotate: initial;
      --tw-invert: initial;
      --tw-opacity: initial;
      --tw-saturate: initial;
      --tw-sepia: initial;
      --tw-drop-shadow: initial;
      --tw-drop-shadow-color: initial;
      --tw-drop-shadow-alpha: 100%;
      --tw-drop-shadow-size: initial;
      --tw-backdrop-blur: initial;
      --tw-backdrop-brightness: initial;
      --tw-backdrop-contrast: initial;
      --tw-backdrop-grayscale: initial;
      --tw-backdrop-hue-rotate: initial;
      --tw-backdrop-invert: initial;
      --tw-backdrop-opacity: initial;
      --tw-backdrop-saturate: initial;
      --tw-backdrop-sepia: initial;
      --tw-duration: initial;
      --tw-ease: initial;
      --tw-content: "";
      --tw-animation-delay: 0s;
      --tw-animation-direction: normal;
      --tw-animation-duration: initial;
      --tw-animation-fill-mode: none;
      --tw-animation-iteration-count: 1;
      --tw-enter-blur: 0;
      --tw-enter-opacity: 1;
      --tw-enter-rotate: 0;
      --tw-enter-scale: 1;
      --tw-enter-translate-x: 0;
      --tw-enter-translate-y: 0;
      --tw-exit-blur: 0;
      --tw-exit-opacity: 1;
      --tw-exit-rotate: 0;
      --tw-exit-scale: 1;
      --tw-exit-translate-x: 0;
      --tw-exit-translate-y: 0;
    }
  }
}
@layer theme {
  :root,
  :host {
    --font-sans: ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji",
      "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
    --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      "Liberation Mono", "Courier New", monospace;
    --color-red-500: oklch(63.7% 0.237 25.331);
    --color-red-600: oklch(57.7% 0.245 27.325);
    --color-orange-300: oklch(83.7% 0.128 66.29);
    --color-orange-400: oklch(75% 0.183 55.934);
    --color-orange-500: oklch(70.5% 0.213 47.604);
    --color-amber-500: oklch(76.9% 0.188 70.08);
    --color-amber-600: oklch(66.6% 0.179 58.318);
    --color-yellow-50: oklch(98.7% 0.026 102.212);
    --color-yellow-100: oklch(97.3% 0.071 103.193);
    --color-yellow-200: oklch(94.5% 0.129 101.54);
    --color-yellow-600: oklch(68.1% 0.162 75.834);
    --color-yellow-800: oklch(47.6% 0.114 61.907);
    --color-green-100: oklch(96.2% 0.044 156.743);
    --color-green-200: oklch(92.5% 0.084 155.995);
    --color-green-400: oklch(79.2% 0.209 151.711);
    --color-green-500: oklch(72.3% 0.219 149.579);
    --color-green-600: oklch(62.7% 0.194 149.214);
    --color-green-800: oklch(44.8% 0.119 151.328);
    --color-teal-400: oklch(77.7% 0.152 181.912);
    --color-teal-500: oklch(70.4% 0.14 182.503);
    --color-teal-600: oklch(60% 0.118 184.704);
    --color-blue-50: oklch(97% 0.014 254.604);
    --color-blue-100: oklch(93.2% 0.032 255.585);
    --color-blue-200: oklch(88.2% 0.059 254.128);
    --color-blue-300: oklch(80.9% 0.105 251.813);
    --color-blue-400: oklch(70.7% 0.165 254.624);
    --color-blue-500: oklch(62.3% 0.214 259.815);
    --color-blue-600: oklch(54.6% 0.245 262.881);
    --color-blue-800: oklch(42.4% 0.199 265.638);
    --color-blue-900: oklch(37.9% 0.146 265.522);
    --color-blue-950: oklch(28.2% 0.091 267.935);
    --color-indigo-500: oklch(58.5% 0.233 277.117);
    --color-purple-100: oklch(94.6% 0.033 307.174);
    --color-purple-300: oklch(82.7% 0.119 306.383);
    --color-purple-400: oklch(71.4% 0.203 305.504);
    --color-purple-500: oklch(62.7% 0.265 303.9);
    --color-purple-600: oklch(55.8% 0.288 302.321);
    --color-purple-700: oklch(49.6% 0.265 301.924);
    --color-purple-900: oklch(38.1% 0.176 304.987);
    --color-gray-100: oklch(96.7% 0.003 264.542);
    --color-gray-300: oklch(87.2% 0.01 258.338);
    --color-gray-400: oklch(70.7% 0.022 261.325);
    --color-gray-500: oklch(55.1% 0.027 264.364);
    --color-gray-600: oklch(44.6% 0.03 256.802);
    --color-gray-800: oklch(27.8% 0.033 256.848);
    --color-gray-900: oklch(21% 0.034 264.665);
    --color-black: #000;
    --color-white: #fff;
    --spacing: 0.25rem;
    --container-2xs: 18rem;
    --container-xs: 20rem;
    --container-sm: 24rem;
    --container-md: 28rem;
    --container-lg: 32rem;
    --container-xl: 36rem;
    --container-2xl: 42rem;
    --container-4xl: 56rem;
    --container-5xl: 64rem;
    --text-xs: 0.75rem;
    --text-xs--line-height: calc(1 / 0.75);
    --text-sm: 0.875rem;
    --text-sm--line-height: calc(1.25 / 0.875);
    --text-base: 1rem;
    --text-base--line-height: 1.5;
    --text-lg: 1.125rem;
    --text-lg--line-height: calc(1.75 / 1.125);
    --text-xl: 1.25rem;
    --text-xl--line-height: calc(1.75 / 1.25);
    --text-2xl: 1.5rem;
    --text-2xl--line-height: calc(2 / 1.5);
    --text-3xl: 1.875rem;
    --text-3xl--line-height: 1.2;
    --font-weight-normal: 400;
    --font-weight-medium: 500;
    --font-weight-semibold: 600;
    --font-weight-bold: 700;
    --tracking-tight: -0.025em;
    --tracking-wide: 0.025em;
    --tracking-wider: 0.05em;
    --tracking-widest: 0.1em;
    --leading-snug: 1.375;
    --leading-normal: 1.5;
    --leading-relaxed: 1.625;
    --radius-xs: 0.125rem;
    --radius-2xl: 1rem;
    --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
    --animate-spin: spin 1s linear infinite;
    --animate-pulse: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
    --animate-bounce: bounce 1s infinite;
    --blur-sm: 8px;
    --aspect-video: 16/9;
    --default-transition-duration: 0.15s;
    --default-transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    --default-font-family: var(--font-sans);
    --default-mono-font-family: var(--font-mono);
    --color-border: var(--border);
  }
}
@layer base {
  *,
  :after,
  :before,
  ::backdrop {
    box-sizing: border-box;
    border: 0 solid;
    margin: 0;
    padding: 0;
  }
  ::file-selector-button {
    box-sizing: border-box;
    border: 0 solid;
    margin: 0;
    padding: 0;
  }
  html,
  :host {
    -webkit-text-size-adjust: 100%;
    tab-size: 4;
    line-height: 1.5;
    font-family: var(
      --default-font-family,
      ui-sans-serif,
      system-ui,
      sans-serif,
      "Apple Color Emoji",
      "Segoe UI Emoji",
      "Segoe UI Symbol",
      "Noto Color Emoji"
    );
    font-feature-settings: var(--default-font-feature-settings, normal);
    font-variation-settings: var(--default-font-variation-settings, normal);
    -webkit-tap-highlight-color: transparent;
  }
  hr {
    height: 0;
    color: inherit;
    border-top-width: 1px;
  }
  abbr:where([title]) {
    -webkit-text-decoration: underline dotted;
    text-decoration: underline dotted;
  }
  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    font-size: inherit;
    font-weight: inherit;
  }
  a {
    color: inherit;
    -webkit-text-decoration: inherit;
    text-decoration: inherit;
  }
  b,
  strong {
    font-weight: bolder;
  }
  code,
  kbd,
  samp,
  pre {
    font-family: var(
      --default-mono-font-family,
      ui-monospace,
      SFMono-Regular,
      Menlo,
      Monaco,
      Consolas,
      "Liberation Mono",
      "Courier New",
      monospace
    );
    font-feature-settings: var(--default-mono-font-feature-settings, normal);
    font-variation-settings: var(
      --default-mono-font-variation-settings,
      normal
    );
    font-size: 1em;
  }
  small {
    font-size: 80%;
  }
  sub,
  sup {
    vertical-align: baseline;
    font-size: 75%;
    line-height: 0;
    position: relative;
  }
  sub {
    bottom: -0.25em;
  }
  sup {
    top: -0.5em;
  }
  table {
    text-indent: 0;
    border-color: inherit;
    border-collapse: collapse;
  }
  :-moz-focusring {
    outline: auto;
  }
  progress {
    vertical-align: baseline;
  }
  summary {
    display: list-item;
  }
  ol,
  ul,
  menu {
    list-style: none;
  }
  img,
  svg,
  video,
  canvas,
  audio,
  iframe,
  embed,
  object {
    vertical-align: middle;
    display: block;
  }
  img,
  video {
    max-width: 100%;
    height: auto;
  }
  button,
  input,
  select,
  optgroup,
  textarea {
    font: inherit;
    font-feature-settings: inherit;
    font-variation-settings: inherit;
    letter-spacing: inherit;
    color: inherit;
    opacity: 1;
    background-color: #0000;
    border-radius: 0;
  }
  ::file-selector-button {
    font: inherit;
    font-feature-settings: inherit;
    font-variation-settings: inherit;
    letter-spacing: inherit;
    color: inherit;
    opacity: 1;
    background-color: #0000;
    border-radius: 0;
  }
  :where(select:is([multiple], [size])) optgroup {
    font-weight: bolder;
  }
  :where(select:is([multiple], [size])) optgroup option {
    padding-inline-start: 20px;
  }
  ::file-selector-button {
    margin-inline-end: 4px;
  }
  ::placeholder {
    opacity: 1;
  }
  @supports (not ((-webkit-appearance: -apple-pay-button))) or
    (contain-intrinsic-size: 1px) {
    ::placeholder {
      color: currentColor;
    }
    @supports (color: color-mix(in lab, red, red)) {
      ::placeholder {
        color: color-mix(in oklab, currentcolor 50%, transparent);
      }
    }
  }
  textarea {
    resize: vertical;
  }
  ::-webkit-search-decoration {
    -webkit-appearance: none;
  }
  ::-webkit-date-and-time-value {
    min-height: 1lh;
    text-align: inherit;
  }
  ::-webkit-datetime-edit {
    display: inline-flex;
  }
  ::-webkit-datetime-edit-fields-wrapper {
    padding: 0;
  }
  ::-webkit-datetime-edit {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-year-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-month-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-day-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-hour-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-minute-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-second-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-millisecond-field {
    padding-block: 0;
  }
  ::-webkit-datetime-edit-meridiem-field {
    padding-block: 0;
  }
  ::-webkit-calendar-picker-indicator {
    line-height: 1;
  }
  :-moz-ui-invalid {
    box-shadow: none;
  }
  button,
  input:where([type="button"], [type="reset"], [type="submit"]) {
    appearance: button;
  }
  ::file-selector-button {
    appearance: button;
  }
  ::-webkit-inner-spin-button {
    height: auto;
  }
  ::-webkit-outer-spin-button {
    height: auto;
  }
  [hidden]:where(:not([hidden="until-found"])) {
    display: none !important;
  }
  * {
    border-color: var(--border);
    outline-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    * {
      outline-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  body {
    background-color: var(--background);
    color: var(--foreground);
  }
  :root,
  .dark {
    --chart-1: oklch(64.6% 0.222 41.116);
    --chart-2: oklch(60% 0.118 184.704);
    --chart-3: oklch(39.8% 0.07 227.392);
    --chart-4: oklch(82.8% 0.189 84.429);
    --chart-5: oklch(76.9% 0.188 70.08);
  }
}
@layer components;
@layer utilities {
  .\@container\/card {
    container: card/inline-size;
  }
  .\@container\/card-header {
    container: card-header/inline-size;
  }
  .\@container\/field-group {
    container: field-group/inline-size;
  }
  .\@container\/main {
    container: main/inline-size;
  }
  .pointer-events-none {
    pointer-events: none;
  }
  .collapse {
    visibility: collapse;
  }
  .invisible {
    visibility: hidden;
  }
  .visible {
    visibility: visible;
  }
  .sr-only {
    clip-path: inset(50%);
    white-space: nowrap;
    border-width: 0;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    position: absolute;
    overflow: hidden;
  }
  .absolute {
    position: absolute;
  }
  .fixed {
    position: fixed;
  }
  .relative {
    position: relative;
  }
  .inset-0 {
    inset: calc(var(--spacing) * 0);
  }
  .inset-2 {
    inset: calc(var(--spacing) * 2);
  }
  .inset-x-0 {
    inset-inline: calc(var(--spacing) * 0);
  }
  .inset-y-0 {
    inset-block: calc(var(--spacing) * 0);
  }
  .-top-1 {
    top: calc(var(--spacing) * -1);
  }
  .-top-12 {
    top: calc(var(--spacing) * -12);
  }
  .top-0 {
    top: calc(var(--spacing) * 0);
  }
  .top-1\.5 {
    top: calc(var(--spacing) * 1.5);
  }
  .top-1\/2 {
    top: 50%;
  }
  .top-3\.5 {
    top: calc(var(--spacing) * 3.5);
  }
  .top-4 {
    top: calc(var(--spacing) * 4);
  }
  .top-\[1px\] {
    top: 1px;
  }
  .top-\[5px\] {
    top: 5px;
  }
  .top-\[50\%\] {
    top: 50%;
  }
  .top-\[60\%\] {
    top: 60%;
  }
  .top-full {
    top: 100%;
  }
  .-right-1 {
    right: calc(var(--spacing) * -1);
  }
  .-right-12 {
    right: calc(var(--spacing) * -12);
  }
  .right-0 {
    right: calc(var(--spacing) * 0);
  }
  .right-1 {
    right: calc(var(--spacing) * 1);
  }
  .right-2 {
    right: calc(var(--spacing) * 2);
  }
  .right-3 {
    right: calc(var(--spacing) * 3);
  }
  .right-4 {
    right: calc(var(--spacing) * 4);
  }
  .-bottom-1 {
    bottom: calc(var(--spacing) * -1);
  }
  .-bottom-12 {
    bottom: calc(var(--spacing) * -12);
  }
  .bottom-0 {
    bottom: calc(var(--spacing) * 0);
  }
  .-left-1 {
    left: calc(var(--spacing) * -1);
  }
  .-left-12 {
    left: calc(var(--spacing) * -12);
  }
  .left-0 {
    left: calc(var(--spacing) * 0);
  }
  .left-1\/2 {
    left: 50%;
  }
  .left-2 {
    left: calc(var(--spacing) * 2);
  }
  .left-3 {
    left: calc(var(--spacing) * 3);
  }
  .left-\[50\%\] {
    left: 50%;
  }
  .isolate {
    isolation: isolate;
  }
  .z-10 {
    z-index: 10;
  }
  .z-20 {
    z-index: 20;
  }
  .z-50 {
    z-index: 50;
  }
  .z-\[1\] {
    z-index: 1;
  }
  .order-first {
    order: -9999;
  }
  .order-last {
    order: 9999;
  }
  .col-start-2 {
    grid-column-start: 2;
  }
  .row-span-2 {
    grid-row: span 2 / span 2;
  }
  .row-start-1 {
    grid-row-start: 1;
  }
  .container {
    width: 100%;
  }
  @media (min-width: 40rem) {
    .container {
      max-width: 40rem;
    }
  }
  @media (min-width: 48rem) {
    .container {
      max-width: 48rem;
    }
  }
  @media (min-width: 64rem) {
    .container {
      max-width: 64rem;
    }
  }
  @media (min-width: 80rem) {
    .container {
      max-width: 80rem;
    }
  }
  @media (min-width: 96rem) {
    .container {
      max-width: 96rem;
    }
  }
  .\!m-0 {
    margin: calc(var(--spacing) * 0) !important;
  }
  .m-1 {
    margin: calc(var(--spacing) * 1);
  }
  .-mx-1 {
    margin-inline: calc(var(--spacing) * -1);
  }
  .mx-2 {
    margin-inline: calc(var(--spacing) * 2);
  }
  .mx-3\.5 {
    margin-inline: calc(var(--spacing) * 3.5);
  }
  .mx-5 {
    margin-inline: calc(var(--spacing) * 5);
  }
  .mx-auto {
    margin-inline: auto;
  }
  .-my-2 {
    margin-block: calc(var(--spacing) * -2);
  }
  .my-0 {
    margin-block: calc(var(--spacing) * 0);
  }
  .my-0\.5 {
    margin-block: calc(var(--spacing) * 0.5);
  }
  .my-1 {
    margin-block: calc(var(--spacing) * 1);
  }
  .my-4 {
    margin-block: calc(var(--spacing) * 4);
  }
  .my-8 {
    margin-block: calc(var(--spacing) * 8);
  }
  .-mt-4 {
    margin-top: calc(var(--spacing) * -4);
  }
  .mt-0\.5 {
    margin-top: calc(var(--spacing) * 0.5);
  }
  .mt-1 {
    margin-top: calc(var(--spacing) * 1);
  }
  .mt-1\.5 {
    margin-top: calc(var(--spacing) * 1.5);
  }
  .mt-2 {
    margin-top: calc(var(--spacing) * 2);
  }
  .mt-3 {
    margin-top: calc(var(--spacing) * 3);
  }
  .mt-4 {
    margin-top: calc(var(--spacing) * 4);
  }
  .mt-5 {
    margin-top: calc(var(--spacing) * 5);
  }
  .mt-6 {
    margin-top: calc(var(--spacing) * 6);
  }
  .mt-8 {
    margin-top: calc(var(--spacing) * 8);
  }
  .mt-10 {
    margin-top: calc(var(--spacing) * 10);
  }
  .mt-auto {
    margin-top: auto;
  }
  .mr-1 {
    margin-right: calc(var(--spacing) * 1);
  }
  .mr-1\.5 {
    margin-right: calc(var(--spacing) * 1.5);
  }
  .mr-2 {
    margin-right: calc(var(--spacing) * 2);
  }
  .mr-3 {
    margin-right: calc(var(--spacing) * 3);
  }
  .mr-auto {
    margin-right: auto;
  }
  .mb-0 {
    margin-bottom: calc(var(--spacing) * 0);
  }
  .mb-1 {
    margin-bottom: calc(var(--spacing) * 1);
  }
  .mb-2 {
    margin-bottom: calc(var(--spacing) * 2);
  }
  .mb-3 {
    margin-bottom: calc(var(--spacing) * 3);
  }
  .mb-4 {
    margin-bottom: calc(var(--spacing) * 4);
  }
  .mb-5 {
    margin-bottom: calc(var(--spacing) * 5);
  }
  .mb-6 {
    margin-bottom: calc(var(--spacing) * 6);
  }
  .mb-8 {
    margin-bottom: calc(var(--spacing) * 8);
  }
  .-ml-4 {
    margin-left: calc(var(--spacing) * -4);
  }
  .ml-1 {
    margin-left: calc(var(--spacing) * 1);
  }
  .ml-2 {
    margin-left: calc(var(--spacing) * 2);
  }
  .ml-4 {
    margin-left: calc(var(--spacing) * 4);
  }
  .ml-auto {
    margin-left: auto;
  }
  .line-clamp-1 {
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    display: -webkit-box;
    overflow: hidden;
  }
  .line-clamp-2 {
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    display: -webkit-box;
    overflow: hidden;
  }
  .block {
    display: block;
  }
  .flex {
    display: flex;
  }
  .grid {
    display: grid;
  }
  .hidden {
    display: none;
  }
  .inline {
    display: inline;
  }
  .inline-block {
    display: inline-block;
  }
  .inline-flex {
    display: inline-flex;
  }
  .table {
    display: table;
  }
  .table-caption {
    display: table-caption;
  }
  .table-cell {
    display: table-cell;
  }
  .table-row {
    display: table-row;
  }
  .field-sizing-content {
    field-sizing: content;
  }
  .aspect-square {
    aspect-ratio: 1;
  }
  .aspect-video {
    aspect-ratio: var(--aspect-video);
  }
  .size-\(--cell-size\) {
    width: var(--cell-size);
    height: var(--cell-size);
  }
  .size-2 {
    width: calc(var(--spacing) * 2);
    height: calc(var(--spacing) * 2);
  }
  .size-2\.5 {
    width: calc(var(--spacing) * 2.5);
    height: calc(var(--spacing) * 2.5);
  }
  .size-3 {
    width: calc(var(--spacing) * 3);
    height: calc(var(--spacing) * 3);
  }
  .size-3\.5 {
    width: calc(var(--spacing) * 3.5);
    height: calc(var(--spacing) * 3.5);
  }
  .size-4 {
    width: calc(var(--spacing) * 4);
    height: calc(var(--spacing) * 4);
  }
  .size-6 {
    width: calc(var(--spacing) * 6);
    height: calc(var(--spacing) * 6);
  }
  .size-7 {
    width: calc(var(--spacing) * 7);
    height: calc(var(--spacing) * 7);
  }
  .size-8 {
    width: calc(var(--spacing) * 8);
    height: calc(var(--spacing) * 8);
  }
  .size-9 {
    width: calc(var(--spacing) * 9);
    height: calc(var(--spacing) * 9);
  }
  .size-10 {
    width: calc(var(--spacing) * 10);
    height: calc(var(--spacing) * 10);
  }
  .size-14 {
    width: calc(var(--spacing) * 14);
    height: calc(var(--spacing) * 14);
  }
  .size-auto {
    width: auto;
    height: auto;
  }
  .size-full {
    width: 100%;
    height: 100%;
  }
  .h-\(--cell-size\) {
    height: var(--cell-size);
  }
  .h-1\.5 {
    height: calc(var(--spacing) * 1.5);
  }
  .h-2 {
    height: calc(var(--spacing) * 2);
  }
  .h-2\.5 {
    height: calc(var(--spacing) * 2.5);
  }
  .h-3 {
    height: calc(var(--spacing) * 3);
  }
  .h-4 {
    height: calc(var(--spacing) * 4);
  }
  .h-5 {
    height: calc(var(--spacing) * 5);
  }
  .h-6 {
    height: calc(var(--spacing) * 6);
  }
  .h-7 {
    height: calc(var(--spacing) * 7);
  }
  .h-8 {
    height: calc(var(--spacing) * 8);
  }
  .h-9 {
    height: calc(var(--spacing) * 9);
  }
  .h-10 {
    height: calc(var(--spacing) * 10);
  }
  .h-11 {
    height: calc(var(--spacing) * 11);
  }
  .h-12 {
    height: calc(var(--spacing) * 12);
  }
  .h-16 {
    height: calc(var(--spacing) * 16);
  }
  .h-32 {
    height: calc(var(--spacing) * 32);
  }
  .h-40 {
    height: calc(var(--spacing) * 40);
  }
  .h-72 {
    height: calc(var(--spacing) * 72);
  }
  .h-\[1\.2rem\] {
    height: 1.2rem;
  }
  .h-\[1\.15rem\] {
    height: 1.15rem;
  }
  .h-\[400px\] {
    height: 400px;
  }
  .h-\[700px\] {
    height: 700px;
  }
  .h-\[calc\(100\%-1px\)\] {
    height: calc(100% - 1px);
  }
  .h-\[calc\(100rem-80rem\)\] {
    height: 20rem;
  }
  .h-\[calc\(100vh-1rem\)\] {
    height: calc(100vh - 1rem);
  }
  .h-\[calc\(100vh-10rem\)\] {
    height: calc(100vh - 10rem);
  }
  .h-\[calc\(100vh-280px\)\] {
    height: calc(100vh - 280px);
  }
  .h-\[var\(--radix-navigation-menu-viewport-height\)\] {
    height: var(--radix-navigation-menu-viewport-height);
  }
  .h-\[var\(--radix-select-trigger-height\)\] {
    height: var(--radix-select-trigger-height);
  }
  .h-auto {
    height: auto;
  }
  .h-full {
    height: 100%;
  }
  .h-px {
    height: 1px;
  }
  .h-screen {
    height: 100vh;
  }
  .h-svh {
    height: 100svh;
  }
  .max-h-\(--radix-context-menu-content-available-height\) {
    max-height: var(--radix-context-menu-content-available-height);
  }
  .max-h-\(--radix-dropdown-menu-content-available-height\) {
    max-height: var(--radix-dropdown-menu-content-available-height);
  }
  .max-h-\(--radix-select-content-available-height\) {
    max-height: var(--radix-select-content-available-height);
  }
  .max-h-64 {
    max-height: calc(var(--spacing) * 64);
  }
  .max-h-\[120px\] {
    max-height: 120px;
  }
  .max-h-\[300px\] {
    max-height: 300px;
  }
  .min-h-0 {
    min-height: calc(var(--spacing) * 0);
  }
  .min-h-4 {
    min-height: calc(var(--spacing) * 4);
  }
  .min-h-16 {
    min-height: calc(var(--spacing) * 16);
  }
  .min-h-\[40px\] {
    min-height: 40px;
  }
  .min-h-\[400px\] {
    min-height: 400px;
  }
  .min-h-screen {
    min-height: 100vh;
  }
  .min-h-svh {
    min-height: 100svh;
  }
  .w-\(--cell-size\) {
    width: var(--cell-size);
  }
  .w-\(--sidebar-width\) {
    width: var(--sidebar-width);
  }
  .w-0 {
    width: calc(var(--spacing) * 0);
  }
  .w-1 {
    width: calc(var(--spacing) * 1);
  }
  .w-1\.5 {
    width: calc(var(--spacing) * 1.5);
  }
  .w-1\/2 {
    width: 50%;
  }
  .w-2 {
    width: calc(var(--spacing) * 2);
  }
  .w-2\.5 {
    width: calc(var(--spacing) * 2.5);
  }
  .w-3 {
    width: calc(var(--spacing) * 3);
  }
  .w-3\/4 {
    width: 75%;
  }
  .w-4 {
    width: calc(var(--spacing) * 4);
  }
  .w-5 {
    width: calc(var(--spacing) * 5);
  }
  .w-5\/6 {
    width: 83.3333%;
  }
  .w-6 {
    width: calc(var(--spacing) * 6);
  }
  .w-8 {
    width: calc(var(--spacing) * 8);
  }
  .w-9 {
    width: calc(var(--spacing) * 9);
  }
  .w-10 {
    width: calc(var(--spacing) * 10);
  }
  .w-12 {
    width: calc(var(--spacing) * 12);
  }
  .w-16 {
    width: calc(var(--spacing) * 16);
  }
  .w-32 {
    width: calc(var(--spacing) * 32);
  }
  .w-56 {
    width: calc(var(--spacing) * 56);
  }
  .w-64 {
    width: calc(var(--spacing) * 64);
  }
  .w-72 {
    width: calc(var(--spacing) * 72);
  }
  .w-\[1\.2rem\] {
    width: 1.2rem;
  }
  .w-\[100px\] {
    width: 100px;
  }
  .w-\[180px\] {
    width: 180px;
  }
  .w-\[350px\] {
    width: 350px;
  }
  .w-auto {
    width: auto;
  }
  .w-fit {
    width: fit-content;
  }
  .w-full {
    width: 100%;
  }
  .w-max {
    width: max-content;
  }
  .w-px {
    width: 1px;
  }
  .w-xs {
    width: var(--container-xs);
  }
  .max-w-\(--skeleton-width\) {
    max-width: var(--skeleton-width);
  }
  .max-w-2xl {
    max-width: var(--container-2xl);
  }
  .max-w-2xs {
    max-width: var(--container-2xs);
  }
  .max-w-\[85\%\] {
    max-width: 85%;
  }
  .max-w-\[calc\(100\%-2rem\)\] {
    max-width: calc(100% - 2rem);
  }
  .max-w-\[calc\(100vw-1rem\)\] {
    max-width: calc(100vw - 1rem);
  }
  .max-w-max {
    max-width: max-content;
  }
  .max-w-md {
    max-width: var(--container-md);
  }
  .max-w-sm {
    max-width: var(--container-sm);
  }
  .max-w-xl {
    max-width: var(--container-xl);
  }
  .min-w-\(--cell-size\) {
    min-width: var(--cell-size);
  }
  .min-w-0 {
    min-width: calc(var(--spacing) * 0);
  }
  .min-w-5 {
    min-width: calc(var(--spacing) * 5);
  }
  .min-w-8 {
    min-width: calc(var(--spacing) * 8);
  }
  .min-w-9 {
    min-width: calc(var(--spacing) * 9);
  }
  .min-w-10 {
    min-width: calc(var(--spacing) * 10);
  }
  .min-w-\[8rem\] {
    min-width: 8rem;
  }
  .min-w-\[12rem\] {
    min-width: 12rem;
  }
  .min-w-\[300px\] {
    min-width: 300px;
  }
  .min-w-\[var\(--radix-select-trigger-width\)\] {
    min-width: var(--radix-select-trigger-width);
  }
  .flex-1 {
    flex: 1;
  }
  .flex-shrink-0,
  .shrink-0 {
    flex-shrink: 0;
  }
  .grow {
    flex-grow: 1;
  }
  .grow-0 {
    flex-grow: 0;
  }
  .basis-full {
    flex-basis: 100%;
  }
  .caption-bottom {
    caption-side: bottom;
  }
  .border-collapse {
    border-collapse: collapse;
  }
  .origin-\(--radix-context-menu-content-transform-origin\) {
    transform-origin: var(--radix-context-menu-content-transform-origin);
  }
  .origin-\(--radix-dropdown-menu-content-transform-origin\) {
    transform-origin: var(--radix-dropdown-menu-content-transform-origin);
  }
  .origin-\(--radix-hover-card-content-transform-origin\) {
    transform-origin: var(--radix-hover-card-content-transform-origin);
  }
  .origin-\(--radix-menubar-content-transform-origin\) {
    transform-origin: var(--radix-menubar-content-transform-origin);
  }
  .origin-\(--radix-popover-content-transform-origin\) {
    transform-origin: var(--radix-popover-content-transform-origin);
  }
  .origin-\(--radix-select-content-transform-origin\) {
    transform-origin: var(--radix-select-content-transform-origin);
  }
  .origin-\(--radix-tooltip-content-transform-origin\) {
    transform-origin: var(--radix-tooltip-content-transform-origin);
  }
  .-translate-x-1\/2 {
    --tw-translate-x: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .-translate-x-px {
    --tw-translate-x: -1px;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .translate-x-\[-50\%\] {
    --tw-translate-x: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .translate-x-px {
    --tw-translate-x: 1px;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .-translate-y-1\/2 {
    --tw-translate-y: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .translate-y-0\.5 {
    --tw-translate-y: calc(var(--spacing) * 0.5);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .translate-y-\[-50\%\] {
    --tw-translate-y: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .translate-y-\[calc\(-50\%_-_2px\)\] {
    --tw-translate-y: calc(-50% - 2px);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .scale-0 {
    --tw-scale-x: 0%;
    --tw-scale-y: 0%;
    --tw-scale-z: 0%;
    scale: var(--tw-scale-x) var(--tw-scale-y);
  }
  .scale-100 {
    --tw-scale-x: 100%;
    --tw-scale-y: 100%;
    --tw-scale-z: 100%;
    scale: var(--tw-scale-x) var(--tw-scale-y);
  }
  .rotate-0 {
    rotate: none;
  }
  .rotate-45 {
    rotate: 45deg;
  }
  .rotate-90 {
    rotate: 90deg;
  }
  .transform {
    transform: var(--tw-rotate-x) var(--tw-rotate-y) var(--tw-rotate-z) var(
        --tw-skew-x
      )
      var(--tw-skew-y);
  }
  .animate-bounce {
    animation: var(--animate-bounce);
  }
  .animate-caret-blink {
    animation: 1.25s ease-out infinite caret-blink;
  }
  .animate-in {
    animation: enter var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .animate-pulse {
    animation: var(--animate-pulse);
  }
  .animate-spin {
    animation: var(--animate-spin);
  }
  .cursor-default {
    cursor: default;
  }
  .cursor-not-allowed {
    cursor: not-allowed;
  }
  .cursor-pointer {
    cursor: pointer;
  }
  .cursor-text {
    cursor: text;
  }
  .touch-none {
    touch-action: none;
  }
  .resize-none {
    resize: none;
  }
  .scroll-my-1 {
    scroll-margin-block: calc(var(--spacing) * 1);
  }
  .scroll-py-1 {
    scroll-padding-block: calc(var(--spacing) * 1);
  }
  .list-disc {
    list-style-type: disc;
  }
  .list-none {
    list-style-type: none;
  }
  .auto-rows-min {
    grid-auto-rows: min-content;
  }
  .grid-cols-1 {
    grid-template-columns: repeat(1, minmax(0, 1fr));
  }
  .grid-cols-2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .grid-cols-\[0_1fr\] {
    grid-template-columns: 0 1fr;
  }
  .grid-rows-\[auto_auto\] {
    grid-template-rows: auto auto;
  }
  .flex-col {
    flex-direction: column;
  }
  .flex-col-reverse {
    flex-direction: column-reverse;
  }
  .flex-row {
    flex-direction: row;
  }
  .flex-wrap {
    flex-wrap: wrap;
  }
  .place-content-center {
    place-content: center;
  }
  .place-items-center {
    place-items: center;
  }
  .items-center {
    align-items: center;
  }
  .items-end {
    align-items: flex-end;
  }
  .items-start {
    align-items: flex-start;
  }
  .items-stretch {
    align-items: stretch;
  }
  .justify-between {
    justify-content: space-between;
  }
  .justify-center {
    justify-content: center;
  }
  .justify-end {
    justify-content: flex-end;
  }
  .justify-start {
    justify-content: flex-start;
  }
  .justify-items-start {
    justify-items: start;
  }
  .gap-0\.5 {
    gap: calc(var(--spacing) * 0.5);
  }
  .gap-1 {
    gap: calc(var(--spacing) * 1);
  }
  .gap-1\.5 {
    gap: calc(var(--spacing) * 1.5);
  }
  .gap-2 {
    gap: calc(var(--spacing) * 2);
  }
  .gap-2\.5 {
    gap: calc(var(--spacing) * 2.5);
  }
  .gap-3 {
    gap: calc(var(--spacing) * 3);
  }
  .gap-4 {
    gap: calc(var(--spacing) * 4);
  }
  .gap-6 {
    gap: calc(var(--spacing) * 6);
  }
  .gap-7 {
    gap: calc(var(--spacing) * 7);
  }
  .gap-8 {
    gap: calc(var(--spacing) * 8);
  }
  .gap-\[--spacing\(var\(--gap\)\)\] {
    gap: calc(var(--spacing) * var(--gap));
  }
  :where(.space-y-0 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 0) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 0) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-0\.5 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 0.5) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 0.5) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-1 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 1) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 1) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-1\.5 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 1.5) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 1.5) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-2 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 2) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 2) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-3 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 3) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 3) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-4 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 4) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 4) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-5 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 5) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 5) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-y-6 > :not(:last-child)) {
    --tw-space-y-reverse: 0;
    margin-block-start: calc(
      calc(var(--spacing) * 6) * var(--tw-space-y-reverse)
    );
    margin-block-end: calc(
      calc(var(--spacing) * 6) * calc(1 - var(--tw-space-y-reverse))
    );
  }
  :where(.space-x-1 > :not(:last-child)) {
    --tw-space-x-reverse: 0;
    margin-inline-start: calc(
      calc(var(--spacing) * 1) * var(--tw-space-x-reverse)
    );
    margin-inline-end: calc(
      calc(var(--spacing) * 1) * calc(1 - var(--tw-space-x-reverse))
    );
  }
  :where(.space-x-2 > :not(:last-child)) {
    --tw-space-x-reverse: 0;
    margin-inline-start: calc(
      calc(var(--spacing) * 2) * var(--tw-space-x-reverse)
    );
    margin-inline-end: calc(
      calc(var(--spacing) * 2) * calc(1 - var(--tw-space-x-reverse))
    );
  }
  :where(.space-x-3 > :not(:last-child)) {
    --tw-space-x-reverse: 0;
    margin-inline-start: calc(
      calc(var(--spacing) * 3) * var(--tw-space-x-reverse)
    );
    margin-inline-end: calc(
      calc(var(--spacing) * 3) * calc(1 - var(--tw-space-x-reverse))
    );
  }
  :where(.space-x-4 > :not(:last-child)) {
    --tw-space-x-reverse: 0;
    margin-inline-start: calc(
      calc(var(--spacing) * 4) * var(--tw-space-x-reverse)
    );
    margin-inline-end: calc(
      calc(var(--spacing) * 4) * calc(1 - var(--tw-space-x-reverse))
    );
  }
  .gap-y-0\.5 {
    row-gap: calc(var(--spacing) * 0.5);
  }
  .self-start {
    align-self: flex-start;
  }
  .self-stretch {
    align-self: stretch;
  }
  .justify-self-end {
    justify-self: flex-end;
  }
  .truncate {
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow: hidden;
  }
  .overflow-auto {
    overflow: auto;
  }
  .overflow-hidden {
    overflow: hidden;
  }
  .overflow-x-auto {
    overflow-x: auto;
  }
  .overflow-x-hidden {
    overflow-x: hidden;
  }
  .overflow-y-auto {
    overflow-y: auto;
  }
  .rounded {
    border-radius: 0.25rem;
  }
  .rounded-2xl {
    border-radius: var(--radius-2xl);
  }
  .rounded-\[2px\] {
    border-radius: 2px;
  }
  .rounded-\[4px\] {
    border-radius: 4px;
  }
  .rounded-\[calc\(var\(--radius\)-5px\)\] {
    border-radius: calc(var(--radius) - 5px);
  }
  .rounded-\[inherit\] {
    border-radius: inherit;
  }
  .rounded-full {
    border-radius: 3.40282e38px;
  }
  .rounded-lg {
    border-radius: var(--radius);
  }
  .rounded-md {
    border-radius: calc(var(--radius) - 2px);
  }
  .rounded-none {
    border-radius: 0;
  }
  .rounded-sm {
    border-radius: calc(var(--radius) - 4px);
  }
  .rounded-xl {
    border-radius: calc(var(--radius) + 4px);
  }
  .rounded-xs {
    border-radius: var(--radius-xs);
  }
  .rounded-l-md {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .rounded-tl-sm {
    border-top-left-radius: calc(var(--radius) - 4px);
  }
  .rounded-r-md {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .border {
    border-style: var(--tw-border-style);
    border-width: 1px;
  }
  .border-0 {
    border-style: var(--tw-border-style);
    border-width: 0;
  }
  .border-2 {
    border-style: var(--tw-border-style);
    border-width: 2px;
  }
  .border-4 {
    border-style: var(--tw-border-style);
    border-width: 4px;
  }
  .border-\[1\.5px\] {
    border-style: var(--tw-border-style);
    border-width: 1.5px;
  }
  .border-y {
    border-block-style: var(--tw-border-style);
    border-block-width: 1px;
  }
  .border-t {
    border-top-style: var(--tw-border-style);
    border-top-width: 1px;
  }
  .border-r {
    border-right-style: var(--tw-border-style);
    border-right-width: 1px;
  }
  .border-b {
    border-bottom-style: var(--tw-border-style);
    border-bottom-width: 1px;
  }
  .border-l {
    border-left-style: var(--tw-border-style);
    border-left-width: 1px;
  }
  .border-dashed {
    --tw-border-style: dashed;
    border-style: dashed;
  }
  .border-none {
    --tw-border-style: none;
    border-style: none;
  }
  .border-\(--color-border\) {
    border-color: var(--color-border);
  }
  .border-blue-200 {
    border-color: var(--color-blue-200);
  }
  .border-border,
  .border-border\/50 {
    border-color: var(--border);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .border-border\/50 {
      border-color: color-mix(in oklab, var(--border) 50%, transparent);
    }
  }
  .border-destructive {
    border-color: var(--destructive);
  }
  .border-gray-300 {
    border-color: var(--color-gray-300);
  }
  .border-green-200 {
    border-color: var(--color-green-200);
  }
  .border-input {
    border-color: var(--input);
  }
  .border-muted {
    border-color: var(--muted);
  }
  .border-muted-foreground\/25 {
    border-color: var(--muted-foreground);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .border-muted-foreground\/25 {
      border-color: color-mix(
        in oklab,
        var(--muted-foreground) 25%,
        transparent
      );
    }
  }
  .border-primary,
  .border-primary\/10 {
    border-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .border-primary\/10 {
      border-color: color-mix(in oklab, var(--primary) 10%, transparent);
    }
  }
  .border-primary\/20 {
    border-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .border-primary\/20 {
      border-color: color-mix(in oklab, var(--primary) 20%, transparent);
    }
  }
  .border-red-500 {
    border-color: var(--color-red-500);
  }
  .border-sidebar-border {
    border-color: var(--sidebar-border);
  }
  .border-transparent {
    border-color: #0000;
  }
  .border-white {
    border-color: var(--color-white);
  }
  .border-white\/30 {
    border-color: #ffffff4d;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .border-white\/30 {
      border-color: color-mix(in oklab, var(--color-white) 30%, transparent);
    }
  }
  .border-yellow-200 {
    border-color: var(--color-yellow-200);
  }
  .border-t-blue-600 {
    border-top-color: var(--color-blue-600);
  }
  .border-t-transparent {
    border-top-color: #0000;
  }
  .border-l-transparent {
    border-left-color: #0000;
  }
  .bg-\(--color-bg\) {
    background-color: var(--color-bg);
  }
  .bg-accent {
    background-color: var(--accent);
  }
  .bg-background,
  .bg-background\/95 {
    background-color: var(--background);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-background\/95 {
      background-color: color-mix(in oklab, var(--background) 95%, transparent);
    }
  }
  .bg-black {
    background-color: var(--color-black);
  }
  .bg-black\/50 {
    background-color: #00000080;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-black\/50 {
      background-color: color-mix(
        in oklab,
        var(--color-black) 50%,
        transparent
      );
    }
  }
  .bg-blue-50 {
    background-color: var(--color-blue-50);
  }
  .bg-blue-600 {
    background-color: var(--color-blue-600);
  }
  .bg-border {
    background-color: var(--border);
  }
  .bg-card {
    background-color: var(--card);
  }
  .bg-destructive,
  .bg-destructive\/5 {
    background-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-destructive\/5 {
      background-color: color-mix(in oklab, var(--destructive) 5%, transparent);
    }
  }
  .bg-foreground {
    background-color: var(--foreground);
  }
  .bg-gray-100 {
    background-color: var(--color-gray-100);
  }
  .bg-gray-300 {
    background-color: var(--color-gray-300);
  }
  .bg-green-100 {
    background-color: var(--color-green-100);
  }
  .bg-green-400 {
    background-color: var(--color-green-400);
  }
  .bg-green-500 {
    background-color: var(--color-green-500);
  }
  .bg-input {
    background-color: var(--input);
  }
  .bg-muted,
  .bg-muted\/50 {
    background-color: var(--muted);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-muted\/50 {
      background-color: color-mix(in oklab, var(--muted) 50%, transparent);
    }
  }
  .bg-orange-400 {
    background-color: var(--color-orange-400);
  }
  .bg-popover {
    background-color: var(--popover);
  }
  .bg-primary {
    background-color: var(--primary);
  }
  .bg-primary-foreground {
    background-color: var(--primary-foreground);
  }
  .bg-primary\/5 {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-primary\/5 {
      background-color: color-mix(in oklab, var(--primary) 5%, transparent);
    }
  }
  .bg-primary\/10 {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-primary\/10 {
      background-color: color-mix(in oklab, var(--primary) 10%, transparent);
    }
  }
  .bg-primary\/20 {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-primary\/20 {
      background-color: color-mix(in oklab, var(--primary) 20%, transparent);
    }
  }
  .bg-primary\/90 {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-primary\/90 {
      background-color: color-mix(in oklab, var(--primary) 90%, transparent);
    }
  }
  .bg-secondary {
    background-color: var(--secondary);
  }
  .bg-sidebar {
    background-color: var(--sidebar);
  }
  .bg-sidebar-border {
    background-color: var(--sidebar-border);
  }
  .bg-transparent {
    background-color: #0000;
  }
  .bg-white {
    background-color: var(--color-white);
  }
  .bg-white\/20 {
    background-color: #fff3;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .bg-white\/20 {
      background-color: color-mix(
        in oklab,
        var(--color-white) 20%,
        transparent
      );
    }
  }
  .bg-yellow-50 {
    background-color: var(--color-yellow-50);
  }
  .bg-yellow-100 {
    background-color: var(--color-yellow-100);
  }
  .bg-gradient-to-br {
    --tw-gradient-position: to bottom right in oklab;
    background-image: linear-gradient(var(--tw-gradient-stops));
  }
  .bg-gradient-to-r {
    --tw-gradient-position: to right in oklab;
    background-image: linear-gradient(var(--tw-gradient-stops));
  }
  .from-blue-100 {
    --tw-gradient-from: var(--color-blue-100);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .from-blue-400 {
    --tw-gradient-from: var(--color-blue-400);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .from-blue-500 {
    --tw-gradient-from: var(--color-blue-500);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .from-gray-900 {
    --tw-gradient-from: var(--color-gray-900);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .via-purple-400 {
    --tw-gradient-via: var(--color-purple-400);
    --tw-gradient-via-stops: var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position), var(
          --tw-gradient-via
        ) var(--tw-gradient-via-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position);
    --tw-gradient-stops: var(--tw-gradient-via-stops);
  }
  .via-purple-500 {
    --tw-gradient-via: var(--color-purple-500);
    --tw-gradient-via-stops: var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position), var(
          --tw-gradient-via
        ) var(--tw-gradient-via-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position);
    --tw-gradient-stops: var(--tw-gradient-via-stops);
  }
  .via-purple-700 {
    --tw-gradient-via: var(--color-purple-700);
    --tw-gradient-via-stops: var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position), var(
          --tw-gradient-via
        ) var(--tw-gradient-via-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position);
    --tw-gradient-stops: var(--tw-gradient-via-stops);
  }
  .to-blue-600 {
    --tw-gradient-to: var(--color-blue-600);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .to-purple-100 {
    --tw-gradient-to: var(--color-purple-100);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .to-purple-600 {
    --tw-gradient-to: var(--color-purple-600);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .to-teal-400 {
    --tw-gradient-to: var(--color-teal-400);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .to-teal-500 {
    --tw-gradient-to: var(--color-teal-500);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .bg-clip-text {
    -webkit-background-clip: text;
    background-clip: text;
  }
  .fill-current {
    fill: currentColor;
  }
  .fill-foreground {
    fill: var(--foreground);
  }
  .fill-primary {
    fill: var(--primary);
  }
  .p-0 {
    padding: calc(var(--spacing) * 0);
  }
  .p-1 {
    padding: calc(var(--spacing) * 1);
  }
  .p-1\.5 {
    padding: calc(var(--spacing) * 1.5);
  }
  .p-2 {
    padding: calc(var(--spacing) * 2);
  }
  .p-3 {
    padding: calc(var(--spacing) * 3);
  }
  .p-4 {
    padding: calc(var(--spacing) * 4);
  }
  .p-6 {
    padding: calc(var(--spacing) * 6);
  }
  .p-\[3px\] {
    padding: 3px;
  }
  .p-px {
    padding: 1px;
  }
  .px-\(--cell-size\) {
    padding-inline: var(--cell-size);
  }
  .px-1 {
    padding-inline: calc(var(--spacing) * 1);
  }
  .px-1\.5 {
    padding-inline: calc(var(--spacing) * 1.5);
  }
  .px-2 {
    padding-inline: calc(var(--spacing) * 2);
  }
  .px-2\.5 {
    padding-inline: calc(var(--spacing) * 2.5);
  }
  .px-3 {
    padding-inline: calc(var(--spacing) * 3);
  }
  .px-4 {
    padding-inline: calc(var(--spacing) * 4);
  }
  .px-6 {
    padding-inline: calc(var(--spacing) * 6);
  }
  .py-0\.5 {
    padding-block: calc(var(--spacing) * 0.5);
  }
  .py-1 {
    padding-block: calc(var(--spacing) * 1);
  }
  .py-1\.5 {
    padding-block: calc(var(--spacing) * 1.5);
  }
  .py-2 {
    padding-block: calc(var(--spacing) * 2);
  }
  .py-3 {
    padding-block: calc(var(--spacing) * 3);
  }
  .py-4 {
    padding-block: calc(var(--spacing) * 4);
  }
  .py-6 {
    padding-block: calc(var(--spacing) * 6);
  }
  .py-8 {
    padding-block: calc(var(--spacing) * 8);
  }
  .py-12 {
    padding-block: calc(var(--spacing) * 12);
  }
  .pt-0 {
    padding-top: calc(var(--spacing) * 0);
  }
  .pt-2 {
    padding-top: calc(var(--spacing) * 2);
  }
  .pt-3 {
    padding-top: calc(var(--spacing) * 3);
  }
  .pt-4 {
    padding-top: calc(var(--spacing) * 4);
  }
  .pt-6 {
    padding-top: calc(var(--spacing) * 6);
  }
  .pt-10 {
    padding-top: calc(var(--spacing) * 10);
  }
  .pr-1 {
    padding-right: calc(var(--spacing) * 1);
  }
  .pr-2 {
    padding-right: calc(var(--spacing) * 2);
  }
  .pr-2\.5 {
    padding-right: calc(var(--spacing) * 2.5);
  }
  .pr-3 {
    padding-right: calc(var(--spacing) * 3);
  }
  .pr-4 {
    padding-right: calc(var(--spacing) * 4);
  }
  .pr-8 {
    padding-right: calc(var(--spacing) * 8);
  }
  .pr-10 {
    padding-right: calc(var(--spacing) * 10);
  }
  .pr-20 {
    padding-right: calc(var(--spacing) * 20);
  }
  .pb-2 {
    padding-bottom: calc(var(--spacing) * 2);
  }
  .pb-3 {
    padding-bottom: calc(var(--spacing) * 3);
  }
  .pb-4 {
    padding-bottom: calc(var(--spacing) * 4);
  }
  .pb-5 {
    padding-bottom: calc(var(--spacing) * 5);
  }
  .pb-6 {
    padding-bottom: calc(var(--spacing) * 6);
  }
  .pb-10 {
    padding-bottom: calc(var(--spacing) * 10);
  }
  .pl-1 {
    padding-left: calc(var(--spacing) * 1);
  }
  .pl-2 {
    padding-left: calc(var(--spacing) * 2);
  }
  .pl-3 {
    padding-left: calc(var(--spacing) * 3);
  }
  .pl-4 {
    padding-left: calc(var(--spacing) * 4);
  }
  .pl-8 {
    padding-left: calc(var(--spacing) * 8);
  }
  .pl-9 {
    padding-left: calc(var(--spacing) * 9);
  }
  .pl-10 {
    padding-left: calc(var(--spacing) * 10);
  }
  .text-center {
    text-align: center;
  }
  .text-left {
    text-align: left;
  }
  .text-right {
    text-align: right;
  }
  .align-middle {
    vertical-align: middle;
  }
  .font-mono {
    font-family: var(--font-mono);
  }
  .font-sans {
    font-family: var(--font-sans);
  }
  .text-2xl {
    font-size: var(--text-2xl);
    line-height: var(--tw-leading, var(--text-2xl--line-height));
  }
  .text-base {
    font-size: var(--text-base);
    line-height: var(--tw-leading, var(--text-base--line-height));
  }
  .text-lg {
    font-size: var(--text-lg);
    line-height: var(--tw-leading, var(--text-lg--line-height));
  }
  .text-sm {
    font-size: var(--text-sm);
    line-height: var(--tw-leading, var(--text-sm--line-height));
  }
  .text-sm\/relaxed {
    font-size: var(--text-sm);
    line-height: var(--leading-relaxed);
  }
  .text-xl {
    font-size: var(--text-xl);
    line-height: var(--tw-leading, var(--text-xl--line-height));
  }
  .text-xs {
    font-size: var(--text-xs);
    line-height: var(--tw-leading, var(--text-xs--line-height));
  }
  .text-\[0\.8rem\] {
    font-size: 0.8rem;
  }
  .text-\[10px\] {
    font-size: 10px;
  }
  .leading-6 {
    --tw-leading: calc(var(--spacing) * 6);
    line-height: calc(var(--spacing) * 6);
  }
  .leading-none {
    --tw-leading: 1;
    line-height: 1;
  }
  .leading-normal {
    --tw-leading: var(--leading-normal);
    line-height: var(--leading-normal);
  }
  .leading-relaxed {
    --tw-leading: var(--leading-relaxed);
    line-height: var(--leading-relaxed);
  }
  .leading-snug {
    --tw-leading: var(--leading-snug);
    line-height: var(--leading-snug);
  }
  .font-bold {
    --tw-font-weight: var(--font-weight-bold);
    font-weight: var(--font-weight-bold);
  }
  .font-medium {
    --tw-font-weight: var(--font-weight-medium);
    font-weight: var(--font-weight-medium);
  }
  .font-normal {
    --tw-font-weight: var(--font-weight-normal);
    font-weight: var(--font-weight-normal);
  }
  .font-semibold {
    --tw-font-weight: var(--font-weight-semibold);
    font-weight: var(--font-weight-semibold);
  }
  .tracking-tight {
    --tw-tracking: var(--tracking-tight);
    letter-spacing: var(--tracking-tight);
  }
  .tracking-wide {
    --tw-tracking: var(--tracking-wide);
    letter-spacing: var(--tracking-wide);
  }
  .tracking-wider {
    --tw-tracking: var(--tracking-wider);
    letter-spacing: var(--tracking-wider);
  }
  .tracking-widest {
    --tw-tracking: var(--tracking-widest);
    letter-spacing: var(--tracking-widest);
  }
  .text-balance {
    text-wrap: balance;
  }
  .break-words {
    overflow-wrap: break-word;
  }
  .break-all {
    word-break: break-all;
  }
  .whitespace-normal {
    white-space: normal;
  }
  .whitespace-nowrap {
    white-space: nowrap;
  }
  .whitespace-pre-wrap {
    white-space: pre-wrap;
  }
  .text-accent-foreground {
    color: var(--accent-foreground);
  }
  .text-amber-500 {
    color: var(--color-amber-500);
  }
  .text-amber-600 {
    color: var(--color-amber-600);
  }
  .text-background {
    color: var(--background);
  }
  .text-blue-500 {
    color: var(--color-blue-500);
  }
  .text-blue-600 {
    color: var(--color-blue-600);
  }
  .text-blue-900 {
    color: var(--color-blue-900);
  }
  .text-card-foreground {
    color: var(--card-foreground);
  }
  .text-current {
    color: currentColor;
  }
  .text-destructive {
    color: var(--destructive);
  }
  .text-foreground {
    color: var(--foreground);
  }
  .text-gray-400 {
    color: var(--color-gray-400);
  }
  .text-gray-500 {
    color: var(--color-gray-500);
  }
  .text-gray-600 {
    color: var(--color-gray-600);
  }
  .text-gray-800 {
    color: var(--color-gray-800);
  }
  .text-green-500 {
    color: var(--color-green-500);
  }
  .text-green-600 {
    color: var(--color-green-600);
  }
  .text-green-800 {
    color: var(--color-green-800);
  }
  .text-indigo-500 {
    color: var(--color-indigo-500);
  }
  .text-muted-foreground {
    color: var(--muted-foreground);
  }
  .text-orange-300 {
    color: var(--color-orange-300);
  }
  .text-orange-400 {
    color: var(--color-orange-400);
  }
  .text-orange-500 {
    color: var(--color-orange-500);
  }
  .text-popover-foreground {
    color: var(--popover-foreground);
  }
  .text-primary {
    color: var(--primary);
  }
  .text-primary-foreground {
    color: var(--primary-foreground);
  }
  .text-red-500 {
    color: var(--color-red-500);
  }
  .text-red-600 {
    color: var(--color-red-600);
  }
  .text-secondary {
    color: var(--secondary);
  }
  .text-secondary-foreground {
    color: var(--secondary-foreground);
  }
  .text-sidebar-foreground,
  .text-sidebar-foreground\/70 {
    color: var(--sidebar-foreground);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .text-sidebar-foreground\/70 {
      color: color-mix(in oklab, var(--sidebar-foreground) 70%, transparent);
    }
  }
  .text-transparent {
    color: #0000;
  }
  .text-white {
    color: var(--color-white);
  }
  .text-yellow-600 {
    color: var(--color-yellow-600);
  }
  .text-yellow-800 {
    color: var(--color-yellow-800);
  }
  .capitalize {
    text-transform: capitalize;
  }
  .lowercase {
    text-transform: lowercase;
  }
  .uppercase {
    text-transform: uppercase;
  }
  .italic {
    font-style: italic;
  }
  .tabular-nums {
    --tw-numeric-spacing: tabular-nums;
    font-variant-numeric: var(--tw-ordinal) var(--tw-slashed-zero) var(
        --tw-numeric-figure
      ) var(--tw-numeric-spacing) var(--tw-numeric-fraction);
  }
  .underline {
    text-decoration-line: underline;
  }
  .underline-offset-4 {
    text-underline-offset: 4px;
  }
  .opacity-0 {
    opacity: 0;
  }
  .opacity-50 {
    opacity: 0.5;
  }
  .opacity-70 {
    opacity: 0.7;
  }
  .opacity-75 {
    opacity: 0.75;
  }
  .opacity-100 {
    opacity: 1;
  }
  .shadow {
    --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
        var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-\[0_0_0_1px_hsl\(var\(--sidebar-border\)\)\] {
    --tw-shadow: 0 0 0 1px var(--tw-shadow-color, hsl(var(--sidebar-border)));
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-lg {
    --tw-shadow: 0 10px 15px -3px var(--tw-shadow-color, #0000001a), 0 4px
        6px -4px var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-md {
    --tw-shadow: 0 4px 6px -1px var(--tw-shadow-color, #0000001a), 0 2px
        4px -2px var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-none {
    --tw-shadow: 0 0 #0000;
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-sm {
    --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
        var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-xl {
    --tw-shadow: 0 20px 25px -5px var(--tw-shadow-color, #0000001a), 0 8px
        10px -6px var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-xs {
    --tw-shadow: 0 1px 2px 0 var(--tw-shadow-color, #0000000d);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .ring-0 {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        0px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .ring-1 {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        1px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .shadow-green-400\/50 {
    --tw-shadow-color: #05df7280;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .shadow-green-400\/50 {
      --tw-shadow-color: color-mix(
        in oklab,
        color-mix(in oklab, var(--color-green-400) 50%, transparent) var(
            --tw-shadow-alpha
          ),
        transparent
      );
    }
  }
  .shadow-orange-400\/50 {
    --tw-shadow-color: #ff8b1a80;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .shadow-orange-400\/50 {
      --tw-shadow-color: color-mix(
        in oklab,
        color-mix(in oklab, var(--color-orange-400) 50%, transparent) var(
            --tw-shadow-alpha
          ),
        transparent
      );
    }
  }
  .ring-primary {
    --tw-ring-color: var(--primary);
  }
  .ring-ring\/50 {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .ring-ring\/50 {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  .ring-sidebar-ring {
    --tw-ring-color: var(--sidebar-ring);
  }
  .ring-offset-background {
    --tw-ring-offset-color: var(--background);
  }
  .outline-hidden {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .outline-hidden {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .outline {
    outline-style: var(--tw-outline-style);
    outline-width: 1px;
  }
  .filter {
    filter: var(--tw-blur) var(--tw-brightness) var(--tw-contrast) var(
        --tw-grayscale
      )
      var(--tw-hue-rotate) var(--tw-invert) var(--tw-saturate) var(--tw-sepia)
      var(--tw-drop-shadow);
  }
  .backdrop-blur {
    --tw-backdrop-blur: blur(8px);
    -webkit-backdrop-filter: var(--tw-backdrop-blur) var(
        --tw-backdrop-brightness
      )
      var(--tw-backdrop-contrast) var(--tw-backdrop-grayscale) var(
        --tw-backdrop-hue-rotate
      ) var(--tw-backdrop-invert) var(--tw-backdrop-opacity) var(
        --tw-backdrop-saturate
      )
      var(--tw-backdrop-sepia);
    backdrop-filter: var(--tw-backdrop-blur) var(--tw-backdrop-brightness) var(
        --tw-backdrop-contrast
      ) var(--tw-backdrop-grayscale) var(--tw-backdrop-hue-rotate) var(
        --tw-backdrop-invert
      )
      var(--tw-backdrop-opacity) var(--tw-backdrop-saturate) var(--tw-backdrop-sepia);
  }
  .backdrop-blur-sm {
    --tw-backdrop-blur: blur(var(--blur-sm));
    -webkit-backdrop-filter: var(--tw-backdrop-blur) var(
        --tw-backdrop-brightness
      )
      var(--tw-backdrop-contrast) var(--tw-backdrop-grayscale) var(
        --tw-backdrop-hue-rotate
      ) var(--tw-backdrop-invert) var(--tw-backdrop-opacity) var(
        --tw-backdrop-saturate
      )
      var(--tw-backdrop-sepia);
    backdrop-filter: var(--tw-backdrop-blur) var(--tw-backdrop-brightness) var(
        --tw-backdrop-contrast
      ) var(--tw-backdrop-grayscale) var(--tw-backdrop-hue-rotate) var(
        --tw-backdrop-invert
      )
      var(--tw-backdrop-opacity) var(--tw-backdrop-saturate) var(--tw-backdrop-sepia);
  }
  .transition {
    transition-property: color, background-color, border-color, outline-color,
      text-decoration-color, fill, stroke, --tw-gradient-from,
      --tw-gradient-via, --tw-gradient-to, opacity, box-shadow, transform,
      translate, scale, rotate, filter, -webkit-backdrop-filter, backdrop-filter,
      display, content-visibility, overlay, pointer-events;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-\[color\,box-shadow\] {
    transition-property: color, box-shadow;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-\[left\,right\,width\] {
    transition-property: left, right, width;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-\[margin\,opacity\] {
    transition-property: margin, opacity;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-\[width\,height\,padding\] {
    transition-property: width, height, padding;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-\[width\] {
    transition-property: width;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-all {
    transition-property: all;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-colors {
    transition-property: color, background-color, border-color, outline-color,
      text-decoration-color, fill, stroke, --tw-gradient-from,
      --tw-gradient-via, --tw-gradient-to;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-opacity {
    transition-property: opacity;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-shadow {
    transition-property: box-shadow;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-transform {
    transition-property: transform, translate, scale, rotate;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  .transition-none {
    transition-property: none;
  }
  .delay-500 {
    transition-delay: 0.5s;
  }
  .duration-100 {
    --tw-duration: 0.1s;
    transition-duration: 0.1s;
  }
  .duration-200 {
    --tw-duration: 0.2s;
    transition-duration: 0.2s;
  }
  .duration-300 {
    --tw-duration: 0.3s;
    transition-duration: 0.3s;
  }
  .duration-1000 {
    --tw-duration: 1s;
    transition-duration: 1s;
  }
  .ease-in-out {
    --tw-ease: var(--ease-in-out);
    transition-timing-function: var(--ease-in-out);
  }
  .ease-linear {
    --tw-ease: linear;
    transition-timing-function: linear;
  }
  .delay-500 {
    --tw-animation-delay: 0.5s;
    animation-delay: 0.5s;
  }
  .fade-in-0 {
    --tw-enter-opacity: 0;
  }
  .outline-none {
    --tw-outline-style: none;
    outline-style: none;
  }
  .select-none {
    -webkit-user-select: none;
    user-select: none;
  }
  .zoom-in-95 {
    --tw-enter-scale: 0.95;
  }
  .\[--cell-size\:--spacing\(8\)\] {
    --cell-size: calc(var(--spacing) * 8);
  }
  .\[animation-delay\:0ms\] {
    animation-delay: 0s;
  }
  .\[animation-delay\:150ms\] {
    animation-delay: 0.15s;
  }
  .\[animation-delay\:300ms\] {
    animation-delay: 0.3s;
  }
  .running {
    animation-play-state: running;
  }
  .group-focus-within\/menu-item\:opacity-100:is(
      :where(.group\/menu-item):focus-within *
    ) {
    opacity: 1;
  }
  @media (hover: hover) {
    .group-hover\:translate-x-0\.5:is(:where(.group):hover *) {
      --tw-translate-x: calc(var(--spacing) * 0.5);
      translate: var(--tw-translate-x) var(--tw-translate-y);
    }
    .group-hover\:scale-110:is(:where(.group):hover *) {
      --tw-scale-x: 110%;
      --tw-scale-y: 110%;
      --tw-scale-z: 110%;
      scale: var(--tw-scale-x) var(--tw-scale-y);
    }
    .group-hover\:bg-white\/30:is(:where(.group):hover *) {
      background-color: #ffffff4d;
    }
    @supports (color: color-mix(in lab, red, red)) {
      .group-hover\:bg-white\/30:is(:where(.group):hover *) {
        background-color: color-mix(
          in oklab,
          var(--color-white) 30%,
          transparent
        );
      }
    }
    .group-hover\:text-destructive:is(:where(.group):hover *) {
      color: var(--destructive);
    }
    .group-hover\:text-foreground:is(:where(.group):hover *) {
      color: var(--foreground);
    }
    .group-hover\:opacity-20:is(:where(.group):hover *) {
      opacity: 0.2;
    }
    .group-hover\:opacity-100:is(:where(.group):hover *),
    .group-hover\/menu-item\:opacity-100:is(:where(.group\/menu-item):hover *) {
      opacity: 1;
    }
  }
  .group-has-data-\[sidebar\=menu-action\]\/menu-item\:pr-8:is(
      :where(.group\/menu-item):has([data-sidebar="menu-action"]) *
    ) {
    padding-right: calc(var(--spacing) * 8);
  }
  .group-has-\[\[data-orientation\=horizontal\]\]\/field\:text-balance:is(
      :where(.group\/field):has([data-orientation="horizontal"]) *
    ) {
    text-wrap: balance;
  }
  .group-has-\[\[data-slot\=item-description\]\]\/item\:translate-y-0\.5:is(
      :where(.group\/item):has([data-slot="item-description"]) *
    ) {
    --tw-translate-y: calc(var(--spacing) * 0.5);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .group-has-\[\[data-slot\=item-description\]\]\/item\:self-start:is(
      :where(.group\/item):has([data-slot="item-description"]) *
    ) {
    align-self: flex-start;
  }
  .group-has-\[\>input\]\/input-group\:pt-2\.5:is(
      :where(.group\/input-group):has(> input) *
    ) {
    padding-top: calc(var(--spacing) * 2.5);
  }
  .group-has-\[\>input\]\/input-group\:pb-2\.5:is(
      :where(.group\/input-group):has(> input) *
    ) {
    padding-bottom: calc(var(--spacing) * 2.5);
  }
  .group-data-\[collapsible\=icon\]\:-mt-8:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    margin-top: calc(var(--spacing) * -8);
  }
  .group-data-\[collapsible\=icon\]\:hidden:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    display: none;
  }
  .group-data-\[collapsible\=icon\]\:size-8\!:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    width: calc(var(--spacing) * 8) !important;
    height: calc(var(--spacing) * 8) !important;
  }
  .group-data-\[collapsible\=icon\]\:w-\(--sidebar-width-icon\):is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    width: var(--sidebar-width-icon);
  }
  .group-data-\[collapsible\=icon\]\:w-\[calc\(var\(--sidebar-width-icon\)\+\(--spacing\(4\)\)\)\]:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    width: calc(var(--sidebar-width-icon) + (calc(var(--spacing) * 4)));
  }
  .group-data-\[collapsible\=icon\]\:w-\[calc\(var\(--sidebar-width-icon\)\+\(--spacing\(4\)\)\+2px\)\]:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    width: calc(var(--sidebar-width-icon) + (calc(var(--spacing) * 4)) + 2px);
  }
  .group-data-\[collapsible\=icon\]\:overflow-hidden:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    overflow: hidden;
  }
  .group-data-\[collapsible\=icon\]\:p-0\!:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    padding: calc(var(--spacing) * 0) !important;
  }
  .group-data-\[collapsible\=icon\]\:p-2\!:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    padding: calc(var(--spacing) * 2) !important;
  }
  .group-data-\[collapsible\=icon\]\:opacity-0:is(
      :where(.group)[data-collapsible="icon"] *
    ) {
    opacity: 0;
  }
  .group-data-\[collapsible\=offcanvas\]\:right-\[calc\(var\(--sidebar-width\)\*-1\)\]:is(
      :where(.group)[data-collapsible="offcanvas"] *
    ) {
    right: calc(var(--sidebar-width) * -1);
  }
  .group-data-\[collapsible\=offcanvas\]\:left-\[calc\(var\(--sidebar-width\)\*-1\)\]:is(
      :where(.group)[data-collapsible="offcanvas"] *
    ) {
    left: calc(var(--sidebar-width) * -1);
  }
  .group-data-\[collapsible\=offcanvas\]\:w-0:is(
      :where(.group)[data-collapsible="offcanvas"] *
    ) {
    width: calc(var(--spacing) * 0);
  }
  .group-data-\[collapsible\=offcanvas\]\:translate-x-0:is(
      :where(.group)[data-collapsible="offcanvas"] *
    ) {
    --tw-translate-x: calc(var(--spacing) * 0);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .group-data-\[disabled\=true\]\:pointer-events-none:is(
      :where(.group)[data-disabled="true"] *
    ) {
    pointer-events: none;
  }
  .group-data-\[disabled\=true\]\:opacity-50:is(
      :where(.group)[data-disabled="true"] *
    ),
  .group-data-\[disabled\=true\]\/field\:opacity-50:is(
      :where(.group\/field)[data-disabled="true"] *
    ),
  .group-data-\[disabled\=true\]\/input-group\:opacity-50:is(
      :where(.group\/input-group)[data-disabled="true"] *
    ) {
    opacity: 0.5;
  }
  .group-data-\[focused\=true\]\/day\:relative:is(
      :where(.group\/day)[data-focused="true"] *
    ) {
    position: relative;
  }
  .group-data-\[focused\=true\]\/day\:z-10:is(
      :where(.group\/day)[data-focused="true"] *
    ) {
    z-index: 10;
  }
  .group-data-\[focused\=true\]\/day\:border-ring:is(
      :where(.group\/day)[data-focused="true"] *
    ) {
    border-color: var(--ring);
  }
  .group-data-\[focused\=true\]\/day\:ring-\[3px\]:is(
      :where(.group\/day)[data-focused="true"] *
    ) {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        3px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .group-data-\[focused\=true\]\/day\:ring-ring\/50:is(
      :where(.group\/day)[data-focused="true"] *
    ) {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .group-data-\[focused\=true\]\/day\:ring-ring\/50:is(
        :where(.group\/day)[data-focused="true"] *
      ) {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  .group-data-\[side\=left\]\:-right-4:is(:where(.group)[data-side="left"] *) {
    right: calc(var(--spacing) * -4);
  }
  .group-data-\[side\=left\]\:border-r:is(:where(.group)[data-side="left"] *) {
    border-right-style: var(--tw-border-style);
    border-right-width: 1px;
  }
  .group-data-\[side\=right\]\:left-0:is(:where(.group)[data-side="right"] *) {
    left: calc(var(--spacing) * 0);
  }
  .group-data-\[side\=right\]\:rotate-180:is(
      :where(.group)[data-side="right"] *
    ) {
    rotate: 180deg;
  }
  .group-data-\[side\=right\]\:border-l:is(
      :where(.group)[data-side="right"] *
    ) {
    border-left-style: var(--tw-border-style);
    border-left-width: 1px;
  }
  .group-data-\[state\=open\]\:rotate-180:is(
      :where(.group)[data-state="open"] *
    ) {
    rotate: 180deg;
  }
  .group-data-\[variant\=floating\]\:rounded-lg:is(
      :where(.group)[data-variant="floating"] *
    ) {
    border-radius: var(--radius);
  }
  .group-data-\[variant\=floating\]\:border:is(
      :where(.group)[data-variant="floating"] *
    ) {
    border-style: var(--tw-border-style);
    border-width: 1px;
  }
  .group-data-\[variant\=floating\]\:border-sidebar-border:is(
      :where(.group)[data-variant="floating"] *
    ) {
    border-color: var(--sidebar-border);
  }
  .group-data-\[variant\=floating\]\:shadow-sm:is(
      :where(.group)[data-variant="floating"] *
    ) {
    --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
        var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .group-data-\[variant\=outline\]\/field-group\:-mb-2:is(
      :where(.group\/field-group)[data-variant="outline"] *
    ) {
    margin-bottom: calc(var(--spacing) * -2);
  }
  .group-data-\[vaul-drawer-direction\=bottom\]\/drawer-content\:block:is(
      :where(.group\/drawer-content)[data-vaul-drawer-direction="bottom"] *
    ) {
    display: block;
  }
  .group-data-\[vaul-drawer-direction\=bottom\]\/drawer-content\:text-center:is(
      :where(.group\/drawer-content)[data-vaul-drawer-direction="bottom"] *
    ),
  .group-data-\[vaul-drawer-direction\=top\]\/drawer-content\:text-center:is(
      :where(.group\/drawer-content)[data-vaul-drawer-direction="top"] *
    ) {
    text-align: center;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:top-full:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    top: 100%;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:mt-1\.5:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    margin-top: calc(var(--spacing) * 1.5);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:overflow-hidden:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    overflow: hidden;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:rounded-md:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    border-radius: calc(var(--radius) - 2px);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:border:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    border-style: var(--tw-border-style);
    border-width: 1px;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:bg-popover:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    background-color: var(--popover);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:text-popover-foreground:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    color: var(--popover-foreground);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:shadow:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
        var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:duration-200:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    ) {
    --tw-duration: 0.2s;
    transition-duration: 0.2s;
  }
  @media (hover: hover) {
    .peer-hover\/menu-button\:text-sidebar-accent-foreground:is(
        :where(.peer\/menu-button):hover ~ *
      ) {
      color: var(--sidebar-accent-foreground);
    }
  }
  .peer-disabled\:cursor-not-allowed:is(:where(.peer):disabled ~ *) {
    cursor: not-allowed;
  }
  .peer-disabled\:opacity-50:is(:where(.peer):disabled ~ *) {
    opacity: 0.5;
  }
  .peer-data-\[active\=true\]\/menu-button\:text-sidebar-accent-foreground:is(
      :where(.peer\/menu-button)[data-active="true"] ~ *
    ) {
    color: var(--sidebar-accent-foreground);
  }
  .peer-data-\[size\=default\]\/menu-button\:top-1\.5:is(
      :where(.peer\/menu-button)[data-size="default"] ~ *
    ) {
    top: calc(var(--spacing) * 1.5);
  }
  .peer-data-\[size\=lg\]\/menu-button\:top-2\.5:is(
      :where(.peer\/menu-button)[data-size="lg"] ~ *
    ) {
    top: calc(var(--spacing) * 2.5);
  }
  .peer-data-\[size\=sm\]\/menu-button\:top-1:is(
      :where(.peer\/menu-button)[data-size="sm"] ~ *
    ) {
    top: calc(var(--spacing) * 1);
  }
  .selection\:bg-primary ::selection {
    background-color: var(--primary);
  }
  .selection\:bg-primary::selection {
    background-color: var(--primary);
  }
  .selection\:text-primary-foreground ::selection {
    color: var(--primary-foreground);
  }
  .selection\:text-primary-foreground::selection {
    color: var(--primary-foreground);
  }
  .file\:inline-flex::file-selector-button {
    display: inline-flex;
  }
  .file\:h-7::file-selector-button {
    height: calc(var(--spacing) * 7);
  }
  .file\:border-0::file-selector-button {
    border-style: var(--tw-border-style);
    border-width: 0;
  }
  .file\:bg-transparent::file-selector-button {
    background-color: #0000;
  }
  .file\:text-sm::file-selector-button {
    font-size: var(--text-sm);
    line-height: var(--tw-leading, var(--text-sm--line-height));
  }
  .file\:font-medium::file-selector-button {
    --tw-font-weight: var(--font-weight-medium);
    font-weight: var(--font-weight-medium);
  }
  .file\:text-foreground::file-selector-button {
    color: var(--foreground);
  }
  .placeholder\:text-muted-foreground::placeholder {
    color: var(--muted-foreground);
  }
  .after\:absolute:after {
    content: var(--tw-content);
    position: absolute;
  }
  .after\:-inset-2:after {
    content: var(--tw-content);
    inset: calc(var(--spacing) * -2);
  }
  .after\:inset-y-0:after {
    content: var(--tw-content);
    inset-block: calc(var(--spacing) * 0);
  }
  .after\:left-1\/2:after {
    content: var(--tw-content);
    left: 50%;
  }
  .after\:w-1:after {
    content: var(--tw-content);
    width: calc(var(--spacing) * 1);
  }
  .after\:w-\[2px\]:after {
    content: var(--tw-content);
    width: 2px;
  }
  .after\:-translate-x-1\/2:after {
    content: var(--tw-content);
    --tw-translate-x: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .group-data-\[collapsible\=offcanvas\]\:after\:left-full:is(
      :where(.group)[data-collapsible="offcanvas"] *
    ):after {
    content: var(--tw-content);
    left: 100%;
  }
  .first\:rounded-l-md:first-child {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .first\:border-l:first-child {
    border-left-style: var(--tw-border-style);
    border-left-width: 1px;
  }
  .last\:mt-0:last-child {
    margin-top: calc(var(--spacing) * 0);
  }
  .last\:rounded-r-md:last-child {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .last\:border-b-0:last-child {
    border-bottom-style: var(--tw-border-style);
    border-bottom-width: 0;
  }
  @media (hover: hover) {
    .hover\:scale-105:hover {
      --tw-scale-x: 105%;
      --tw-scale-y: 105%;
      --tw-scale-z: 105%;
      scale: var(--tw-scale-x) var(--tw-scale-y);
    }
    .hover\:border-muted-foreground\/50:hover {
      border-color: var(--muted-foreground);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:border-muted-foreground\/50:hover {
        border-color: color-mix(
          in oklab,
          var(--muted-foreground) 50%,
          transparent
        );
      }
    }
    .hover\:border-primary\/40:hover {
      border-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:border-primary\/40:hover {
        border-color: color-mix(in oklab, var(--primary) 40%, transparent);
      }
    }
    .hover\:bg-accent:hover,
    .hover\:bg-accent\/50:hover {
      background-color: var(--accent);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-accent\/50:hover {
        background-color: color-mix(in oklab, var(--accent) 50%, transparent);
      }
    }
    .hover\:bg-destructive\/10:hover {
      background-color: var(--destructive);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-destructive\/10:hover {
        background-color: color-mix(
          in oklab,
          var(--destructive) 10%,
          transparent
        );
      }
    }
    .hover\:bg-destructive\/90:hover {
      background-color: var(--destructive);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-destructive\/90:hover {
        background-color: color-mix(
          in oklab,
          var(--destructive) 90%,
          transparent
        );
      }
    }
    .hover\:bg-muted:hover,
    .hover\:bg-muted\/50:hover {
      background-color: var(--muted);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-muted\/50:hover {
        background-color: color-mix(in oklab, var(--muted) 50%, transparent);
      }
    }
    .hover\:bg-muted\/80:hover {
      background-color: var(--muted);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-muted\/80:hover {
        background-color: color-mix(in oklab, var(--muted) 80%, transparent);
      }
    }
    .hover\:bg-primary\/5:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-primary\/5:hover {
        background-color: color-mix(in oklab, var(--primary) 5%, transparent);
      }
    }
    .hover\:bg-primary\/10:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-primary\/10:hover {
        background-color: color-mix(in oklab, var(--primary) 10%, transparent);
      }
    }
    .hover\:bg-primary\/20:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-primary\/20:hover {
        background-color: color-mix(in oklab, var(--primary) 20%, transparent);
      }
    }
    .hover\:bg-primary\/80:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-primary\/80:hover {
        background-color: color-mix(in oklab, var(--primary) 80%, transparent);
      }
    }
    .hover\:bg-primary\/90:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-primary\/90:hover {
        background-color: color-mix(in oklab, var(--primary) 90%, transparent);
      }
    }
    .hover\:bg-secondary\/80:hover {
      background-color: var(--secondary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:bg-secondary\/80:hover {
        background-color: color-mix(
          in oklab,
          var(--secondary) 80%,
          transparent
        );
      }
    }
    .hover\:bg-sidebar-accent:hover {
      background-color: var(--sidebar-accent);
    }
    .hover\:bg-transparent:hover {
      background-color: #0000;
    }
    .hover\:from-purple-600:hover {
      --tw-gradient-from: var(--color-purple-600);
      --tw-gradient-stops: var(
        --tw-gradient-via-stops,
        var(--tw-gradient-position),
        var(--tw-gradient-from) var(--tw-gradient-from-position),
        var(--tw-gradient-to) var(--tw-gradient-to-position)
      );
    }
    .hover\:to-teal-600:hover {
      --tw-gradient-to: var(--color-teal-600);
      --tw-gradient-stops: var(
        --tw-gradient-via-stops,
        var(--tw-gradient-position),
        var(--tw-gradient-from) var(--tw-gradient-from-position),
        var(--tw-gradient-to) var(--tw-gradient-to-position)
      );
    }
    .hover\:text-accent-foreground:hover {
      color: var(--accent-foreground);
    }
    .hover\:text-blue-500:hover {
      color: var(--color-blue-500);
    }
    .hover\:text-blue-600:hover {
      color: var(--color-blue-600);
    }
    .hover\:text-destructive:hover {
      color: var(--destructive);
    }
    .hover\:text-foreground:hover {
      color: var(--foreground);
    }
    .hover\:text-gray-900:hover {
      color: var(--color-gray-900);
    }
    .hover\:text-muted-foreground:hover {
      color: var(--muted-foreground);
    }
    .hover\:text-primary\/80:hover {
      color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .hover\:text-primary\/80:hover {
        color: color-mix(in oklab, var(--primary) 80%, transparent);
      }
    }
    .hover\:text-red-500:hover {
      color: var(--color-red-500);
    }
    .hover\:text-sidebar-accent-foreground:hover {
      color: var(--sidebar-accent-foreground);
    }
    .hover\:underline:hover {
      text-decoration-line: underline;
    }
    .hover\:opacity-100:hover {
      opacity: 1;
    }
    .hover\:shadow-\[0_0_0_1px_hsl\(var\(--sidebar-accent\)\)\]:hover {
      --tw-shadow: 0 0 0 1px var(--tw-shadow-color, hsl(var(--sidebar-accent)));
      box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
          --tw-ring-offset-shadow
        ), var(--tw-ring-shadow), var(--tw-shadow);
    }
    .hover\:shadow-md:hover {
      --tw-shadow: 0 4px 6px -1px var(--tw-shadow-color, #0000001a), 0 2px
          4px -2px var(--tw-shadow-color, #0000001a);
      box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
          --tw-ring-offset-shadow
        ), var(--tw-ring-shadow), var(--tw-shadow);
    }
    .hover\:shadow-xl:hover {
      --tw-shadow: 0 20px 25px -5px var(--tw-shadow-color, #0000001a), 0 8px
          10px -6px var(--tw-shadow-color, #0000001a);
      box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
          --tw-ring-offset-shadow
        ), var(--tw-ring-shadow), var(--tw-shadow);
    }
    .hover\:ring-4:hover {
      --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
          4px + var(--tw-ring-offset-width)
        )
        var(--tw-ring-color, currentcolor);
      box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
          --tw-ring-offset-shadow
        ), var(--tw-ring-shadow), var(--tw-shadow);
    }
    .hover\:group-data-\[collapsible\=offcanvas\]\:bg-sidebar:hover:is(
        :where(.group)[data-collapsible="offcanvas"] *
      ) {
      background-color: var(--sidebar);
    }
    .hover\:after\:bg-sidebar-border:hover:after {
      content: var(--tw-content);
      background-color: var(--sidebar-border);
    }
  }
  .focus\:z-10:focus {
    z-index: 10;
  }
  .focus\:bg-accent:focus {
    background-color: var(--accent);
  }
  .focus\:text-accent-foreground:focus {
    color: var(--accent-foreground);
  }
  .focus\:text-destructive:focus {
    color: var(--destructive);
  }
  .focus\:ring-2:focus {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        2px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus\:ring-ring:focus {
    --tw-ring-color: var(--ring);
  }
  .focus\:ring-offset-2:focus {
    --tw-ring-offset-width: 2px;
    --tw-ring-offset-shadow: var(--tw-ring-inset) 0 0 0 var(
        --tw-ring-offset-width
      )
      var(--tw-ring-offset-color);
  }
  .focus\:outline-hidden:focus {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .focus\:outline-hidden:focus {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .focus-visible\:z-10:focus-visible {
    z-index: 10;
  }
  .focus-visible\:border-ring:focus-visible {
    border-color: var(--ring);
  }
  .focus-visible\:ring-0:focus-visible {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        0px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus-visible\:ring-1:focus-visible {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        1px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus-visible\:ring-2:focus-visible {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        2px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus-visible\:ring-4:focus-visible {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        4px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus-visible\:ring-\[3px\]:focus-visible {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        3px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .focus-visible\:ring-destructive\/20:focus-visible {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .focus-visible\:ring-destructive\/20:focus-visible {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 20%, transparent);
    }
  }
  .focus-visible\:ring-ring:focus-visible,
  .focus-visible\:ring-ring\/50:focus-visible {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .focus-visible\:ring-ring\/50:focus-visible {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  .focus-visible\:ring-offset-1:focus-visible {
    --tw-ring-offset-width: 1px;
    --tw-ring-offset-shadow: var(--tw-ring-inset) 0 0 0 var(
        --tw-ring-offset-width
      )
      var(--tw-ring-offset-color);
  }
  .focus-visible\:outline-hidden:focus-visible {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .focus-visible\:outline-hidden:focus-visible {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .focus-visible\:outline-1:focus-visible {
    outline-style: var(--tw-outline-style);
    outline-width: 1px;
  }
  .focus-visible\:outline-ring:focus-visible {
    outline-color: var(--ring);
  }
  .active\:bg-sidebar-accent:active {
    background-color: var(--sidebar-accent);
  }
  .active\:text-sidebar-accent-foreground:active {
    color: var(--sidebar-accent-foreground);
  }
  .disabled\:pointer-events-none:disabled {
    pointer-events: none;
  }
  .disabled\:cursor-not-allowed:disabled {
    cursor: not-allowed;
  }
  .disabled\:opacity-50:disabled {
    opacity: 0.5;
  }
  :where([data-side="left"]) .in-data-\[side\=left\]\:cursor-w-resize {
    cursor: w-resize;
  }
  :where([data-side="right"]) .in-data-\[side\=right\]\:cursor-e-resize {
    cursor: e-resize;
  }
  .has-focus\:border-ring:has(:focus) {
    border-color: var(--ring);
  }
  .has-focus\:ring-\[3px\]:has(:focus) {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        3px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .has-focus\:ring-ring\/50:has(:focus) {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .has-focus\:ring-ring\/50:has(:focus) {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  .has-disabled\:opacity-50:has(:disabled) {
    opacity: 0.5;
  }
  .has-data-\[slot\=card-action\]\:grid-cols-\[1fr_auto\]:has(
      [data-slot="card-action"]
    ) {
    grid-template-columns: 1fr auto;
  }
  .has-data-\[state\=checked\]\:border-primary:has([data-state="checked"]) {
    border-color: var(--primary);
  }
  .has-data-\[state\=checked\]\:bg-primary\/5:has([data-state="checked"]) {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .has-data-\[state\=checked\]\:bg-primary\/5:has([data-state="checked"]) {
      background-color: color-mix(in oklab, var(--primary) 5%, transparent);
    }
  }
  .has-data-\[variant\=inset\]\:bg-sidebar:has([data-variant="inset"]) {
    background-color: var(--sidebar);
  }
  .has-\[\[data-slot\=input-group-control\]\:focus-visible\]\:border-ring:has(
      [data-slot="input-group-control"]:focus-visible
    ) {
    border-color: var(--ring);
  }
  .has-\[\[data-slot\=input-group-control\]\:focus-visible\]\:ring-\[3px\]:has(
      [data-slot="input-group-control"]:focus-visible
    ) {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        3px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .has-\[\[data-slot\=input-group-control\]\:focus-visible\]\:ring-ring\/50:has(
      [data-slot="input-group-control"]:focus-visible
    ) {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .has-\[\[data-slot\=input-group-control\]\:focus-visible\]\:ring-ring\/50:has(
        [data-slot="input-group-control"]:focus-visible
      ) {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  .has-\[\[data-slot\]\[aria-invalid\=true\]\]\:border-destructive:has(
      [data-slot][aria-invalid="true"]
    ) {
    border-color: var(--destructive);
  }
  .has-\[\[data-slot\]\[aria-invalid\=true\]\]\:ring-destructive\/20:has(
      [data-slot][aria-invalid="true"]
    ) {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .has-\[\[data-slot\]\[aria-invalid\=true\]\]\:ring-destructive\/20:has(
        [data-slot][aria-invalid="true"]
      ) {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 20%, transparent);
    }
  }
  .has-\[\>\[data-align\=block-end\]\]\:h-auto:has(> [data-align="block-end"]) {
    height: auto;
  }
  .has-\[\>\[data-align\=block-end\]\]\:flex-col:has(
      > [data-align="block-end"]
    ) {
    flex-direction: column;
  }
  .has-\[\>\[data-align\=block-start\]\]\:h-auto:has(
      > [data-align="block-start"]
    ) {
    height: auto;
  }
  .has-\[\>\[data-align\=block-start\]\]\:flex-col:has(
      > [data-align="block-start"]
    ) {
    flex-direction: column;
  }
  .has-\[\>\[data-slot\=button-group\]\]\:gap-2:has(
      > [data-slot="button-group"]
    ) {
    gap: calc(var(--spacing) * 2);
  }
  .has-\[\>\[data-slot\=checkbox-group\]\]\:gap-3:has(
      > [data-slot="checkbox-group"]
    ) {
    gap: calc(var(--spacing) * 3);
  }
  .has-\[\>\[data-slot\=field-content\]\]\:items-start:has(
      > [data-slot="field-content"]
    ) {
    align-items: flex-start;
  }
  .has-\[\>\[data-slot\=field\]\]\:w-full:has(> [data-slot="field"]) {
    width: 100%;
  }
  .has-\[\>\[data-slot\=field\]\]\:flex-col:has(> [data-slot="field"]) {
    flex-direction: column;
  }
  .has-\[\>\[data-slot\=field\]\]\:rounded-md:has(> [data-slot="field"]) {
    border-radius: calc(var(--radius) - 2px);
  }
  .has-\[\>\[data-slot\=field\]\]\:border:has(> [data-slot="field"]) {
    border-style: var(--tw-border-style);
    border-width: 1px;
  }
  .has-\[\>\[data-slot\=radio-group\]\]\:gap-3:has(
      > [data-slot="radio-group"]
    ) {
    gap: calc(var(--spacing) * 3);
  }
  .has-\[\>button\]\:mr-\[-0\.45rem\]:has(> button) {
    margin-right: -0.45rem;
  }
  .has-\[\>button\]\:ml-\[-0\.45rem\]:has(> button) {
    margin-left: -0.45rem;
  }
  .has-\[\>kbd\]\:mr-\[-0\.35rem\]:has(> kbd) {
    margin-right: -0.35rem;
  }
  .has-\[\>kbd\]\:ml-\[-0\.35rem\]:has(> kbd) {
    margin-left: -0.35rem;
  }
  .has-\[\>svg\]\:grid-cols-\[calc\(var\(--spacing\)\*4\)_1fr\]:has(> svg) {
    grid-template-columns: calc(var(--spacing) * 4) 1fr;
  }
  .has-\[\>svg\]\:gap-x-3:has(> svg) {
    column-gap: calc(var(--spacing) * 3);
  }
  .has-\[\>svg\]\:p-0:has(> svg) {
    padding: calc(var(--spacing) * 0);
  }
  .has-\[\>svg\]\:px-2:has(> svg) {
    padding-inline: calc(var(--spacing) * 2);
  }
  .has-\[\>svg\]\:px-2\.5:has(> svg) {
    padding-inline: calc(var(--spacing) * 2.5);
  }
  .has-\[\>svg\]\:px-3:has(> svg) {
    padding-inline: calc(var(--spacing) * 3);
  }
  .has-\[\>svg\]\:px-4:has(> svg) {
    padding-inline: calc(var(--spacing) * 4);
  }
  .has-\[\>textarea\]\:h-auto:has(> textarea) {
    height: auto;
  }
  .aria-disabled\:pointer-events-none[aria-disabled="true"] {
    pointer-events: none;
  }
  .aria-disabled\:opacity-50[aria-disabled="true"] {
    opacity: 0.5;
  }
  .aria-invalid\:border-destructive[aria-invalid="true"] {
    border-color: var(--destructive);
  }
  .aria-invalid\:ring-destructive\/20[aria-invalid="true"] {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .aria-invalid\:ring-destructive\/20[aria-invalid="true"] {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 20%, transparent);
    }
  }
  .aria-selected\:text-muted-foreground[aria-selected="true"] {
    color: var(--muted-foreground);
  }
  .data-\[active\=true\]\:z-10[data-active="true"] {
    z-index: 10;
  }
  .data-\[active\=true\]\:border-ring[data-active="true"] {
    border-color: var(--ring);
  }
  .data-\[active\=true\]\:bg-accent\/50[data-active="true"] {
    background-color: var(--accent);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .data-\[active\=true\]\:bg-accent\/50[data-active="true"] {
      background-color: color-mix(in oklab, var(--accent) 50%, transparent);
    }
  }
  .data-\[active\=true\]\:bg-sidebar-accent[data-active="true"] {
    background-color: var(--sidebar-accent);
  }
  .data-\[active\=true\]\:font-medium[data-active="true"] {
    --tw-font-weight: var(--font-weight-medium);
    font-weight: var(--font-weight-medium);
  }
  .data-\[active\=true\]\:text-accent-foreground[data-active="true"] {
    color: var(--accent-foreground);
  }
  .data-\[active\=true\]\:text-sidebar-accent-foreground[data-active="true"] {
    color: var(--sidebar-accent-foreground);
  }
  .data-\[active\=true\]\:ring-\[3px\][data-active="true"] {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        3px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .data-\[active\=true\]\:ring-ring\/50[data-active="true"] {
    --tw-ring-color: var(--ring);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .data-\[active\=true\]\:ring-ring\/50[data-active="true"] {
      --tw-ring-color: color-mix(in oklab, var(--ring) 50%, transparent);
    }
  }
  @media (hover: hover) {
    .data-\[active\=true\]\:hover\:bg-accent[data-active="true"]:hover {
      background-color: var(--accent);
    }
  }
  .data-\[active\=true\]\:focus\:bg-accent[data-active="true"]:focus {
    background-color: var(--accent);
  }
  .data-\[active\=true\]\:aria-invalid\:border-destructive[data-active="true"][aria-invalid="true"] {
    border-color: var(--destructive);
  }
  .data-\[active\=true\]\:aria-invalid\:ring-destructive\/20[data-active="true"][aria-invalid="true"] {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .data-\[active\=true\]\:aria-invalid\:ring-destructive\/20[data-active="true"][aria-invalid="true"] {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 20%, transparent);
    }
  }
  .data-\[disabled\]\:pointer-events-none[data-disabled] {
    pointer-events: none;
  }
  .data-\[disabled\]\:opacity-50[data-disabled] {
    opacity: 0.5;
  }
  .data-\[disabled\=true\]\:pointer-events-none[data-disabled="true"] {
    pointer-events: none;
  }
  .data-\[disabled\=true\]\:opacity-50[data-disabled="true"] {
    opacity: 0.5;
  }
  .data-\[error\=true\]\:text-destructive[data-error="true"] {
    color: var(--destructive);
  }
  .data-\[inset\]\:pl-8[data-inset] {
    padding-left: calc(var(--spacing) * 8);
  }
  .data-\[invalid\=true\]\:text-destructive[data-invalid="true"] {
    color: var(--destructive);
  }
  .data-\[motion\=from-end\]\:slide-in-from-right-52[data-motion="from-end"] {
    --tw-enter-translate-x: calc(52 * var(--spacing));
  }
  .data-\[motion\=from-start\]\:slide-in-from-left-52[data-motion="from-start"] {
    --tw-enter-translate-x: calc(52 * var(--spacing) * -1);
  }
  .data-\[motion\=to-end\]\:slide-out-to-right-52[data-motion="to-end"] {
    --tw-exit-translate-x: calc(52 * var(--spacing));
  }
  .data-\[motion\=to-start\]\:slide-out-to-left-52[data-motion="to-start"] {
    --tw-exit-translate-x: calc(52 * var(--spacing) * -1);
  }
  .data-\[motion\^\=from-\]\:animate-in[data-motion^="from-"] {
    animation: enter var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[motion\^\=from-\]\:fade-in[data-motion^="from-"] {
    --tw-enter-opacity: 0;
  }
  .data-\[motion\^\=to-\]\:animate-out[data-motion^="to-"] {
    animation: exit var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[motion\^\=to-\]\:fade-out[data-motion^="to-"] {
    --tw-exit-opacity: 0;
  }
  .data-\[orientation\=horizontal\]\:h-1\.5[data-orientation="horizontal"] {
    height: calc(var(--spacing) * 1.5);
  }
  .data-\[orientation\=horizontal\]\:h-full[data-orientation="horizontal"] {
    height: 100%;
  }
  .data-\[orientation\=horizontal\]\:h-px[data-orientation="horizontal"] {
    height: 1px;
  }
  .data-\[orientation\=horizontal\]\:w-full[data-orientation="horizontal"] {
    width: 100%;
  }
  .data-\[orientation\=vertical\]\:h-auto[data-orientation="vertical"] {
    height: auto;
  }
  .data-\[orientation\=vertical\]\:h-full[data-orientation="vertical"] {
    height: 100%;
  }
  .data-\[orientation\=vertical\]\:min-h-44[data-orientation="vertical"] {
    min-height: calc(var(--spacing) * 44);
  }
  .data-\[orientation\=vertical\]\:w-1\.5[data-orientation="vertical"] {
    width: calc(var(--spacing) * 1.5);
  }
  .data-\[orientation\=vertical\]\:w-auto[data-orientation="vertical"] {
    width: auto;
  }
  .data-\[orientation\=vertical\]\:w-full[data-orientation="vertical"] {
    width: 100%;
  }
  .data-\[orientation\=vertical\]\:w-px[data-orientation="vertical"] {
    width: 1px;
  }
  .data-\[orientation\=vertical\]\:flex-col[data-orientation="vertical"] {
    flex-direction: column;
  }
  .data-\[panel-group-direction\=vertical\]\:h-px[data-panel-group-direction="vertical"] {
    height: 1px;
  }
  .data-\[panel-group-direction\=vertical\]\:w-full[data-panel-group-direction="vertical"] {
    width: 100%;
  }
  .data-\[panel-group-direction\=vertical\]\:flex-col[data-panel-group-direction="vertical"] {
    flex-direction: column;
  }
  .data-\[panel-group-direction\=vertical\]\:after\:left-0[data-panel-group-direction="vertical"]:after {
    content: var(--tw-content);
    left: calc(var(--spacing) * 0);
  }
  .data-\[panel-group-direction\=vertical\]\:after\:h-1[data-panel-group-direction="vertical"]:after {
    content: var(--tw-content);
    height: calc(var(--spacing) * 1);
  }
  .data-\[panel-group-direction\=vertical\]\:after\:w-full[data-panel-group-direction="vertical"]:after {
    content: var(--tw-content);
    width: 100%;
  }
  .data-\[panel-group-direction\=vertical\]\:after\:translate-x-0[data-panel-group-direction="vertical"]:after {
    content: var(--tw-content);
    --tw-translate-x: calc(var(--spacing) * 0);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[panel-group-direction\=vertical\]\:after\:-translate-y-1\/2[data-panel-group-direction="vertical"]:after {
    content: var(--tw-content);
    --tw-translate-y: -50%;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[placeholder\]\:text-muted-foreground[data-placeholder] {
    color: var(--muted-foreground);
  }
  .data-\[range-end\=true\]\:rounded-md[data-range-end="true"] {
    border-radius: calc(var(--radius) - 2px);
  }
  .data-\[range-end\=true\]\:rounded-r-md[data-range-end="true"] {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .data-\[range-end\=true\]\:bg-primary[data-range-end="true"] {
    background-color: var(--primary);
  }
  .data-\[range-end\=true\]\:text-primary-foreground[data-range-end="true"] {
    color: var(--primary-foreground);
  }
  .data-\[range-middle\=true\]\:rounded-none[data-range-middle="true"] {
    border-radius: 0;
  }
  .data-\[range-middle\=true\]\:bg-accent[data-range-middle="true"] {
    background-color: var(--accent);
  }
  .data-\[range-middle\=true\]\:text-accent-foreground[data-range-middle="true"] {
    color: var(--accent-foreground);
  }
  .data-\[range-start\=true\]\:rounded-md[data-range-start="true"] {
    border-radius: calc(var(--radius) - 2px);
  }
  .data-\[range-start\=true\]\:rounded-l-md[data-range-start="true"] {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .data-\[range-start\=true\]\:bg-primary[data-range-start="true"] {
    background-color: var(--primary);
  }
  .data-\[range-start\=true\]\:text-primary-foreground[data-range-start="true"] {
    color: var(--primary-foreground);
  }
  .data-\[selected-single\=true\]\:bg-primary[data-selected-single="true"] {
    background-color: var(--primary);
  }
  .data-\[selected-single\=true\]\:text-primary-foreground[data-selected-single="true"] {
    color: var(--primary-foreground);
  }
  .data-\[selected\=true\]\:rounded-none[data-selected="true"] {
    border-radius: 0;
  }
  .data-\[selected\=true\]\:bg-accent[data-selected="true"] {
    background-color: var(--accent);
  }
  .data-\[selected\=true\]\:text-accent-foreground[data-selected="true"] {
    color: var(--accent-foreground);
  }
  .data-\[side\=bottom\]\:translate-y-1[data-side="bottom"] {
    --tw-translate-y: calc(var(--spacing) * 1);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[side\=bottom\]\:slide-in-from-top-2[data-side="bottom"] {
    --tw-enter-translate-y: calc(2 * var(--spacing) * -1);
  }
  .data-\[side\=left\]\:-translate-x-1[data-side="left"] {
    --tw-translate-x: calc(var(--spacing) * -1);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[side\=left\]\:slide-in-from-right-2[data-side="left"] {
    --tw-enter-translate-x: calc(2 * var(--spacing));
  }
  .data-\[side\=right\]\:translate-x-1[data-side="right"] {
    --tw-translate-x: calc(var(--spacing) * 1);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[side\=right\]\:slide-in-from-left-2[data-side="right"] {
    --tw-enter-translate-x: calc(2 * var(--spacing) * -1);
  }
  .data-\[side\=top\]\:-translate-y-1[data-side="top"] {
    --tw-translate-y: calc(var(--spacing) * -1);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[side\=top\]\:slide-in-from-bottom-2[data-side="top"] {
    --tw-enter-translate-y: calc(2 * var(--spacing));
  }
  .data-\[size\=default\]\:h-9[data-size="default"] {
    height: calc(var(--spacing) * 9);
  }
  .data-\[size\=sm\]\:h-8[data-size="sm"] {
    height: calc(var(--spacing) * 8);
  }
  :is(
      .\*\:data-\[slot\=alert-description\]\:text-destructive\/90 > *
    )[data-slot="alert-description"] {
    color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    :is(
        .\*\:data-\[slot\=alert-description\]\:text-destructive\/90 > *
      )[data-slot="alert-description"] {
      color: color-mix(in oklab, var(--destructive) 90%, transparent);
    }
  }
  :is(
      .\*\:data-\[slot\=alert-description\]\:text-secondary\/90 > *
    )[data-slot="alert-description"] {
    color: var(--secondary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    :is(
        .\*\:data-\[slot\=alert-description\]\:text-secondary\/90 > *
      )[data-slot="alert-description"] {
      color: color-mix(in oklab, var(--secondary) 90%, transparent);
    }
  }
  :is(.\*\:data-\[slot\=card\]\:bg-gradient-to-t > *)[data-slot="card"] {
    --tw-gradient-position: to top in oklab;
    background-image: linear-gradient(var(--tw-gradient-stops));
  }
  :is(.\*\:data-\[slot\=card\]\:from-primary\/5 > *)[data-slot="card"] {
    --tw-gradient-from: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    :is(.\*\:data-\[slot\=card\]\:from-primary\/5 > *)[data-slot="card"] {
      --tw-gradient-from: color-mix(in oklab, var(--primary) 5%, transparent);
    }
  }
  :is(.\*\:data-\[slot\=card\]\:from-primary\/5 > *)[data-slot="card"] {
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  :is(.\*\:data-\[slot\=card\]\:to-card > *)[data-slot="card"] {
    --tw-gradient-to: var(--card);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  :is(.\*\:data-\[slot\=card\]\:shadow-xs > *)[data-slot="card"] {
    --tw-shadow: 0 1px 2px 0 var(--tw-shadow-color, #0000000d);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .data-\[slot\=checkbox-group\]\:gap-3[data-slot="checkbox-group"] {
    gap: calc(var(--spacing) * 3);
  }
  :is(
      .\*\*\:data-\[slot\=command-input-wrapper\]\:h-12 *
    )[data-slot="command-input-wrapper"] {
    height: calc(var(--spacing) * 12);
  }
  :is(
      .\*\*\:data-\[slot\=navigation-menu-link\]\:focus\:ring-0 *
    )[data-slot="navigation-menu-link"]:focus {
    --tw-ring-shadow: var(--tw-ring-inset) 0 0 0 calc(
        0px + var(--tw-ring-offset-width)
      )
      var(--tw-ring-color, currentcolor);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  :is(
      .\*\*\:data-\[slot\=navigation-menu-link\]\:focus\:outline-none *
    )[data-slot="navigation-menu-link"]:focus {
    --tw-outline-style: none;
    outline-style: none;
  }
  :is(
      .\*\:data-\[slot\=select-value\]\:line-clamp-1 > *
    )[data-slot="select-value"] {
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    display: -webkit-box;
    overflow: hidden;
  }
  :is(.\*\:data-\[slot\=select-value\]\:flex > *)[data-slot="select-value"] {
    display: flex;
  }
  :is(
      .\*\:data-\[slot\=select-value\]\:items-center > *
    )[data-slot="select-value"] {
    align-items: center;
  }
  :is(.\*\:data-\[slot\=select-value\]\:gap-2 > *)[data-slot="select-value"] {
    gap: calc(var(--spacing) * 2);
  }
  .data-\[spacing\=0\]\:rounded-none[data-spacing="0"] {
    border-radius: 0;
  }
  .data-\[spacing\=0\]\:shadow-none[data-spacing="0"] {
    --tw-shadow: 0 0 #0000;
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .data-\[spacing\=0\]\:first\:rounded-l-md[data-spacing="0"]:first-child {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .data-\[spacing\=0\]\:last\:rounded-r-md[data-spacing="0"]:last-child {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .data-\[state\=active\]\:bg-background[data-state="active"] {
    background-color: var(--background);
  }
  .data-\[state\=active\]\:shadow-sm[data-state="active"] {
    --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
        var(--tw-shadow-color, #0000001a);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .data-\[state\=checked\]\:translate-x-\[calc\(100\%-2px\)\][data-state="checked"] {
    --tw-translate-x: calc(100% - 2px);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[state\=checked\]\:border-primary[data-state="checked"] {
    border-color: var(--primary);
  }
  .data-\[state\=checked\]\:bg-primary[data-state="checked"] {
    background-color: var(--primary);
  }
  .data-\[state\=checked\]\:text-primary-foreground[data-state="checked"] {
    color: var(--primary-foreground);
  }
  .data-\[state\=closed\]\:animate-accordion-up[data-state="closed"] {
    animation: accordion-up var(
        --tw-animation-duration,
        var(--tw-duration, 0.2s)
      ) var(--tw-ease, ease-out) var(--tw-animation-delay, 0s) var(
        --tw-animation-iteration-count,
        1
      )
      var(--tw-animation-direction, normal) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=closed\]\:animate-out[data-state="closed"] {
    animation: exit var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=closed\]\:duration-300[data-state="closed"] {
    --tw-duration: 0.3s;
    transition-duration: 0.3s;
  }
  .data-\[state\=closed\]\:fade-out-0[data-state="closed"] {
    --tw-exit-opacity: 0;
  }
  .data-\[state\=closed\]\:zoom-out-95[data-state="closed"] {
    --tw-exit-scale: 0.95;
  }
  .data-\[state\=closed\]\:slide-out-to-bottom[data-state="closed"] {
    --tw-exit-translate-y: 100%;
  }
  .data-\[state\=closed\]\:slide-out-to-left[data-state="closed"] {
    --tw-exit-translate-x: -100%;
  }
  .data-\[state\=closed\]\:slide-out-to-right[data-state="closed"] {
    --tw-exit-translate-x: 100%;
  }
  .data-\[state\=closed\]\:slide-out-to-top[data-state="closed"] {
    --tw-exit-translate-y: -100%;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=closed\]\:animate-out:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="closed"] {
    animation: exit var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=closed\]\:fade-out-0:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="closed"] {
    --tw-exit-opacity: 0;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=closed\]\:zoom-out-95:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="closed"] {
    --tw-exit-scale: 0.95;
  }
  .data-\[state\=hidden\]\:animate-out[data-state="hidden"] {
    animation: exit var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=hidden\]\:fade-out[data-state="hidden"] {
    --tw-exit-opacity: 0;
  }
  .data-\[state\=on\]\:bg-accent[data-state="on"] {
    background-color: var(--accent);
  }
  .data-\[state\=on\]\:text-accent-foreground[data-state="on"] {
    color: var(--accent-foreground);
  }
  .data-\[state\=open\]\:animate-accordion-down[data-state="open"] {
    animation: accordion-down var(
        --tw-animation-duration,
        var(--tw-duration, 0.2s)
      ) var(--tw-ease, ease-out) var(--tw-animation-delay, 0s) var(
        --tw-animation-iteration-count,
        1
      )
      var(--tw-animation-direction, normal) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=open\]\:animate-in[data-state="open"] {
    animation: enter var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=open\]\:bg-accent[data-state="open"],
  .data-\[state\=open\]\:bg-accent\/50[data-state="open"] {
    background-color: var(--accent);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .data-\[state\=open\]\:bg-accent\/50[data-state="open"] {
      background-color: color-mix(in oklab, var(--accent) 50%, transparent);
    }
  }
  .data-\[state\=open\]\:bg-secondary[data-state="open"] {
    background-color: var(--secondary);
  }
  .data-\[state\=open\]\:text-accent-foreground[data-state="open"] {
    color: var(--accent-foreground);
  }
  .data-\[state\=open\]\:text-muted-foreground[data-state="open"] {
    color: var(--muted-foreground);
  }
  .data-\[state\=open\]\:opacity-100[data-state="open"] {
    opacity: 1;
  }
  .data-\[state\=open\]\:duration-500[data-state="open"] {
    --tw-duration: 0.5s;
    transition-duration: 0.5s;
  }
  .data-\[state\=open\]\:fade-in-0[data-state="open"] {
    --tw-enter-opacity: 0;
  }
  .data-\[state\=open\]\:zoom-in-90[data-state="open"] {
    --tw-enter-scale: 0.9;
  }
  .data-\[state\=open\]\:zoom-in-95[data-state="open"] {
    --tw-enter-scale: 0.95;
  }
  .data-\[state\=open\]\:slide-in-from-bottom[data-state="open"] {
    --tw-enter-translate-y: 100%;
  }
  .data-\[state\=open\]\:slide-in-from-left[data-state="open"] {
    --tw-enter-translate-x: -100%;
  }
  .data-\[state\=open\]\:slide-in-from-right[data-state="open"] {
    --tw-enter-translate-x: 100%;
  }
  .data-\[state\=open\]\:slide-in-from-top[data-state="open"] {
    --tw-enter-translate-y: -100%;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=open\]\:animate-in:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="open"] {
    animation: enter var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=open\]\:fade-in-0:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="open"] {
    --tw-enter-opacity: 0;
  }
  .group-data-\[viewport\=false\]\/navigation-menu\:data-\[state\=open\]\:zoom-in-95:is(
      :where(.group\/navigation-menu)[data-viewport="false"] *
    )[data-state="open"] {
    --tw-enter-scale: 0.95;
  }
  @media (hover: hover) {
    .data-\[state\=open\]\:hover\:bg-accent[data-state="open"]:hover {
      background-color: var(--accent);
    }
    .data-\[state\=open\]\:hover\:bg-sidebar-accent[data-state="open"]:hover {
      background-color: var(--sidebar-accent);
    }
    .data-\[state\=open\]\:hover\:text-sidebar-accent-foreground[data-state="open"]:hover {
      color: var(--sidebar-accent-foreground);
    }
  }
  .data-\[state\=open\]\:focus\:bg-accent[data-state="open"]:focus {
    background-color: var(--accent);
  }
  .data-\[state\=selected\]\:bg-muted[data-state="selected"] {
    background-color: var(--muted);
  }
  .data-\[state\=unchecked\]\:translate-x-0[data-state="unchecked"] {
    --tw-translate-x: calc(var(--spacing) * 0);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .data-\[state\=unchecked\]\:bg-input[data-state="unchecked"] {
    background-color: var(--input);
  }
  .data-\[state\=visible\]\:animate-in[data-state="visible"] {
    animation: enter var(--tw-animation-duration, var(--tw-duration, 0.15s)) var(
        --tw-ease,
        ease
      )
      var(--tw-animation-delay, 0s) var(--tw-animation-iteration-count, 1) var(
        --tw-animation-direction,
        normal
      ) var(--tw-animation-fill-mode, none);
  }
  .data-\[state\=visible\]\:fade-in[data-state="visible"] {
    --tw-enter-opacity: 0;
  }
  .data-\[variant\=destructive\]\:text-destructive[data-variant="destructive"] {
    color: var(--destructive);
  }
  .data-\[variant\=destructive\]\:focus\:bg-destructive\/10[data-variant="destructive"]:focus {
    background-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .data-\[variant\=destructive\]\:focus\:bg-destructive\/10[data-variant="destructive"]:focus {
      background-color: color-mix(
        in oklab,
        var(--destructive) 10%,
        transparent
      );
    }
  }
  .data-\[variant\=destructive\]\:focus\:text-destructive[data-variant="destructive"]:focus {
    color: var(--destructive);
  }
  .data-\[variant\=label\]\:text-sm[data-variant="label"] {
    font-size: var(--text-sm);
    line-height: var(--tw-leading, var(--text-sm--line-height));
  }
  .data-\[variant\=legend\]\:text-base[data-variant="legend"] {
    font-size: var(--text-base);
    line-height: var(--tw-leading, var(--text-base--line-height));
  }
  .data-\[spacing\=0\]\:data-\[variant\=outline\]\:border-l-0[data-spacing="0"][data-variant="outline"] {
    border-left-style: var(--tw-border-style);
    border-left-width: 0;
  }
  .data-\[spacing\=0\]\:data-\[variant\=outline\]\:first\:border-l[data-spacing="0"][data-variant="outline"]:first-child {
    border-left-style: var(--tw-border-style);
    border-left-width: 1px;
  }
  .data-\[spacing\=default\]\:data-\[variant\=outline\]\:shadow-xs[data-spacing="default"][data-variant="outline"] {
    --tw-shadow: 0 1px 2px 0 var(--tw-shadow-color, #0000000d);
    box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
        --tw-ring-offset-shadow
      ), var(--tw-ring-shadow), var(--tw-shadow);
  }
  .data-\[vaul-drawer-direction\=bottom\]\:inset-x-0[data-vaul-drawer-direction="bottom"] {
    inset-inline: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=bottom\]\:bottom-0[data-vaul-drawer-direction="bottom"] {
    bottom: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=bottom\]\:mt-24[data-vaul-drawer-direction="bottom"] {
    margin-top: calc(var(--spacing) * 24);
  }
  .data-\[vaul-drawer-direction\=bottom\]\:max-h-\[80vh\][data-vaul-drawer-direction="bottom"] {
    max-height: 80vh;
  }
  .data-\[vaul-drawer-direction\=bottom\]\:rounded-t-lg[data-vaul-drawer-direction="bottom"] {
    border-top-left-radius: var(--radius);
    border-top-right-radius: var(--radius);
  }
  .data-\[vaul-drawer-direction\=bottom\]\:border-t[data-vaul-drawer-direction="bottom"] {
    border-top-style: var(--tw-border-style);
    border-top-width: 1px;
  }
  .data-\[vaul-drawer-direction\=left\]\:inset-y-0[data-vaul-drawer-direction="left"] {
    inset-block: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=left\]\:left-0[data-vaul-drawer-direction="left"] {
    left: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=left\]\:w-3\/4[data-vaul-drawer-direction="left"] {
    width: 75%;
  }
  .data-\[vaul-drawer-direction\=left\]\:border-r[data-vaul-drawer-direction="left"] {
    border-right-style: var(--tw-border-style);
    border-right-width: 1px;
  }
  .data-\[vaul-drawer-direction\=right\]\:inset-y-0[data-vaul-drawer-direction="right"] {
    inset-block: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=right\]\:right-0[data-vaul-drawer-direction="right"] {
    right: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=right\]\:w-3\/4[data-vaul-drawer-direction="right"] {
    width: 75%;
  }
  .data-\[vaul-drawer-direction\=right\]\:border-l[data-vaul-drawer-direction="right"] {
    border-left-style: var(--tw-border-style);
    border-left-width: 1px;
  }
  .data-\[vaul-drawer-direction\=top\]\:inset-x-0[data-vaul-drawer-direction="top"] {
    inset-inline: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=top\]\:top-0[data-vaul-drawer-direction="top"] {
    top: calc(var(--spacing) * 0);
  }
  .data-\[vaul-drawer-direction\=top\]\:mb-24[data-vaul-drawer-direction="top"] {
    margin-bottom: calc(var(--spacing) * 24);
  }
  .data-\[vaul-drawer-direction\=top\]\:max-h-\[80vh\][data-vaul-drawer-direction="top"] {
    max-height: 80vh;
  }
  .data-\[vaul-drawer-direction\=top\]\:rounded-b-lg[data-vaul-drawer-direction="top"] {
    border-bottom-right-radius: var(--radius);
    border-bottom-left-radius: var(--radius);
  }
  .data-\[vaul-drawer-direction\=top\]\:border-b[data-vaul-drawer-direction="top"] {
    border-bottom-style: var(--tw-border-style);
    border-bottom-width: 1px;
  }
  .nth-last-2\:-mt-1:nth-last-child(2) {
    margin-top: calc(var(--spacing) * -1);
  }
  @supports (
    (-webkit-backdrop-filter: var(--tw)) or (backdrop-filter: var(--tw))
  ) {
    .supports-\[backdrop-filter\]\:bg-background\/60 {
      background-color: var(--background);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .supports-\[backdrop-filter\]\:bg-background\/60 {
        background-color: color-mix(
          in oklab,
          var(--background) 60%,
          transparent
        );
      }
    }
  }
  @media (min-width: 40rem) {
    .sm\:right-3 {
      right: calc(var(--spacing) * 3);
    }
    .sm\:mt-3 {
      margin-top: calc(var(--spacing) * 3);
    }
    .sm\:mr-1 {
      margin-right: calc(var(--spacing) * 1);
    }
    .sm\:mr-2 {
      margin-right: calc(var(--spacing) * 2);
    }
    .sm\:mr-3 {
      margin-right: calc(var(--spacing) * 3);
    }
    .sm\:block {
      display: block;
    }
    .sm\:flex {
      display: flex;
    }
    .sm\:hidden {
      display: none;
    }
    .sm\:inline {
      display: inline;
    }
    .sm\:inline-flex {
      display: inline-flex;
    }
    .sm\:h-4 {
      height: calc(var(--spacing) * 4);
    }
    .sm\:h-6 {
      height: calc(var(--spacing) * 6);
    }
    .sm\:h-7 {
      height: calc(var(--spacing) * 7);
    }
    .sm\:h-8 {
      height: calc(var(--spacing) * 8);
    }
    .sm\:h-11 {
      height: calc(var(--spacing) * 11);
    }
    .sm\:h-12 {
      height: calc(var(--spacing) * 12);
    }
    .sm\:h-\[600px\] {
      height: 600px;
    }
    .sm\:min-h-\[44px\] {
      min-height: 44px;
    }
    .sm\:w-4 {
      width: calc(var(--spacing) * 4);
    }
    .sm\:w-6 {
      width: calc(var(--spacing) * 6);
    }
    .sm\:w-8 {
      width: calc(var(--spacing) * 8);
    }
    .sm\:w-12 {
      width: calc(var(--spacing) * 12);
    }
    .sm\:w-auto {
      width: auto;
    }
    .sm\:w-full {
      width: 100%;
    }
    .sm\:max-w-\[75\%\] {
      max-width: 75%;
    }
    .sm\:max-w-\[550px\] {
      max-width: 550px;
    }
    .sm\:max-w-\[600px\] {
      max-width: 600px;
    }
    .sm\:max-w-\[650px\] {
      max-width: 650px;
    }
    .sm\:max-w-lg {
      max-width: var(--container-lg);
    }
    .sm\:max-w-md {
      max-width: var(--container-md);
    }
    .sm\:max-w-sm {
      max-width: var(--container-sm);
    }
    .sm\:flex-1 {
      flex: 1;
    }
    .sm\:grid-cols-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .sm\:flex-row {
      flex-direction: row;
    }
    .sm\:items-center {
      align-items: center;
    }
    .sm\:justify-center {
      justify-content: center;
    }
    .sm\:justify-end {
      justify-content: flex-end;
    }
    .sm\:gap-2\.5 {
      gap: calc(var(--spacing) * 2.5);
    }
    .sm\:gap-3 {
      gap: calc(var(--spacing) * 3);
    }
    :where(.sm\:space-y-0 > :not(:last-child)) {
      --tw-space-y-reverse: 0;
      margin-block-start: calc(
        calc(var(--spacing) * 0) * var(--tw-space-y-reverse)
      );
      margin-block-end: calc(
        calc(var(--spacing) * 0) * calc(1 - var(--tw-space-y-reverse))
      );
    }
    :where(.sm\:space-y-4 > :not(:last-child)) {
      --tw-space-y-reverse: 0;
      margin-block-start: calc(
        calc(var(--spacing) * 4) * var(--tw-space-y-reverse)
      );
      margin-block-end: calc(
        calc(var(--spacing) * 4) * calc(1 - var(--tw-space-y-reverse))
      );
    }
    :where(.sm\:space-y-6 > :not(:last-child)) {
      --tw-space-y-reverse: 0;
      margin-block-start: calc(
        calc(var(--spacing) * 6) * var(--tw-space-y-reverse)
      );
      margin-block-end: calc(
        calc(var(--spacing) * 6) * calc(1 - var(--tw-space-y-reverse))
      );
    }
    :where(.sm\:space-x-2 > :not(:last-child)) {
      --tw-space-x-reverse: 0;
      margin-inline-start: calc(
        calc(var(--spacing) * 2) * var(--tw-space-x-reverse)
      );
      margin-inline-end: calc(
        calc(var(--spacing) * 2) * calc(1 - var(--tw-space-x-reverse))
      );
    }
    :where(.sm\:space-x-3 > :not(:last-child)) {
      --tw-space-x-reverse: 0;
      margin-inline-start: calc(
        calc(var(--spacing) * 3) * var(--tw-space-x-reverse)
      );
      margin-inline-end: calc(
        calc(var(--spacing) * 3) * calc(1 - var(--tw-space-x-reverse))
      );
    }
    :where(.sm\:space-x-4 > :not(:last-child)) {
      --tw-space-x-reverse: 0;
      margin-inline-start: calc(
        calc(var(--spacing) * 4) * var(--tw-space-x-reverse)
      );
      margin-inline-end: calc(
        calc(var(--spacing) * 4) * calc(1 - var(--tw-space-x-reverse))
      );
    }
    .sm\:p-3 {
      padding: calc(var(--spacing) * 3);
    }
    .sm\:p-4 {
      padding: calc(var(--spacing) * 4);
    }
    .sm\:p-6 {
      padding: calc(var(--spacing) * 6);
    }
    .sm\:px-2 {
      padding-inline: calc(var(--spacing) * 2);
    }
    .sm\:px-4 {
      padding-inline: calc(var(--spacing) * 4);
    }
    .sm\:px-6 {
      padding-inline: calc(var(--spacing) * 6);
    }
    .sm\:py-3 {
      padding-block: calc(var(--spacing) * 3);
    }
    .sm\:py-6 {
      padding-block: calc(var(--spacing) * 6);
    }
    .sm\:py-12 {
      padding-block: calc(var(--spacing) * 12);
    }
    .sm\:pr-2\.5 {
      padding-right: calc(var(--spacing) * 2.5);
    }
    .sm\:pr-12 {
      padding-right: calc(var(--spacing) * 12);
    }
    .sm\:pl-2\.5 {
      padding-left: calc(var(--spacing) * 2.5);
    }
    .sm\:text-left {
      text-align: left;
    }
    .sm\:text-2xl {
      font-size: var(--text-2xl);
      line-height: var(--tw-leading, var(--text-2xl--line-height));
    }
    .sm\:text-3xl {
      font-size: var(--text-3xl);
      line-height: var(--tw-leading, var(--text-3xl--line-height));
    }
    .sm\:text-base {
      font-size: var(--text-base);
      line-height: var(--tw-leading, var(--text-base--line-height));
    }
    .sm\:text-sm {
      font-size: var(--text-sm);
      line-height: var(--tw-leading, var(--text-sm--line-height));
    }
    .sm\:text-xl {
      font-size: var(--text-xl);
      line-height: var(--tw-leading, var(--text-xl--line-height));
    }
    .data-\[vaul-drawer-direction\=left\]\:sm\:max-w-sm[data-vaul-drawer-direction="left"],
    .data-\[vaul-drawer-direction\=right\]\:sm\:max-w-sm[data-vaul-drawer-direction="right"] {
      max-width: var(--container-sm);
    }
  }
  @media (min-width: 48rem) {
    .md\:absolute {
      position: absolute;
    }
    .md\:block {
      display: block;
    }
    .md\:flex {
      display: flex;
    }
    .md\:hidden {
      display: none;
    }
    .md\:w-\[var\(--radix-navigation-menu-viewport-width\)\] {
      width: var(--radix-navigation-menu-viewport-width);
    }
    .md\:w-auto {
      width: auto;
    }
    .md\:max-w-\[750px\] {
      max-width: 750px;
    }
    .md\:max-w-full {
      max-width: 100%;
    }
    .md\:max-w-sm {
      max-width: var(--container-sm);
    }
    .md\:grid-cols-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .md\:grid-cols-3 {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .md\:grid-cols-4 {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .md\:flex-row {
      flex-direction: row;
    }
    .md\:flex-nowrap {
      flex-wrap: nowrap;
    }
    .md\:gap-1\.5 {
      gap: calc(var(--spacing) * 1.5);
    }
    .md\:p-12 {
      padding: calc(var(--spacing) * 12);
    }
    .md\:pr-3 {
      padding-right: calc(var(--spacing) * 3);
    }
    .md\:text-left {
      text-align: left;
    }
    .md\:text-sm {
      font-size: var(--text-sm);
      line-height: var(--tw-leading, var(--text-sm--line-height));
    }
    .md\:opacity-0 {
      opacity: 0;
    }
    .md\:peer-data-\[variant\=inset\]\:m-2:is(
        :where(.peer)[data-variant="inset"] ~ *
      ) {
      margin: calc(var(--spacing) * 2);
    }
    .md\:peer-data-\[variant\=inset\]\:ml-0:is(
        :where(.peer)[data-variant="inset"] ~ *
      ) {
      margin-left: calc(var(--spacing) * 0);
    }
    .md\:peer-data-\[variant\=inset\]\:rounded-xl:is(
        :where(.peer)[data-variant="inset"] ~ *
      ) {
      border-radius: calc(var(--radius) + 4px);
    }
    .md\:peer-data-\[variant\=inset\]\:shadow-sm:is(
        :where(.peer)[data-variant="inset"] ~ *
      ) {
      --tw-shadow: 0 1px 3px 0 var(--tw-shadow-color, #0000001a), 0 1px 2px -1px
          var(--tw-shadow-color, #0000001a);
      box-shadow: var(--tw-inset-shadow), var(--tw-inset-ring-shadow), var(
          --tw-ring-offset-shadow
        ), var(--tw-ring-shadow), var(--tw-shadow);
    }
    .md\:peer-data-\[variant\=inset\]\:peer-data-\[state\=collapsed\]\:ml-2:is(
        :where(.peer)[data-variant="inset"] ~ *
      ):is(:where(.peer)[data-state="collapsed"] ~ *) {
      margin-left: calc(var(--spacing) * 2);
    }
    .md\:after\:hidden:after {
      content: var(--tw-content);
      display: none;
    }
  }
  @media (min-width: 64rem) {
    .lg\:col-span-1 {
      grid-column: span 1 / span 1;
    }
    .lg\:col-span-2 {
      grid-column: span 2 / span 2;
    }
    .lg\:max-w-\[850px\] {
      max-width: 850px;
    }
    .lg\:grid-cols-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .lg\:grid-cols-3 {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .lg\:grid-cols-5 {
      grid-template-columns: repeat(5, minmax(0, 1fr));
    }
    .lg\:gap-12 {
      gap: calc(var(--spacing) * 12);
    }
    .lg\:text-base {
      font-size: var(--text-base);
      line-height: var(--tw-leading, var(--text-base--line-height));
    }
  }
  @media (min-width: 80rem) {
    .xl\:container {
      width: 100%;
    }
    @media (min-width: 40rem) {
      .xl\:container {
        max-width: 40rem;
      }
    }
    @media (min-width: 48rem) {
      .xl\:container {
        max-width: 48rem;
      }
    }
    @media (min-width: 64rem) {
      .xl\:container {
        max-width: 64rem;
      }
    }
    .xl\:container {
      max-width: 80rem;
    }
    @media (min-width: 96rem) {
      .xl\:container {
        max-width: 96rem;
      }
    }
    .xl\:mx-auto {
      margin-inline: auto;
    }
    .xl\:w-5xl {
      width: var(--container-5xl);
    }
    .xl\:max-w-4xl {
      max-width: var(--container-4xl);
    }
  }
  @media (min-width: 96rem) {
    .\32xl\:container {
      width: 100%;
    }
    @media (min-width: 40rem) {
      .\32xl\:container {
        max-width: 40rem;
      }
    }
    @media (min-width: 48rem) {
      .\32xl\:container {
        max-width: 48rem;
      }
    }
    @media (min-width: 64rem) {
      .\32xl\:container {
        max-width: 64rem;
      }
    }
    @media (min-width: 80rem) {
      .\32xl\:container {
        max-width: 80rem;
      }
    }
    .\32xl\:container {
      max-width: 96rem;
    }
    .\32xl\:mx-auto {
      margin-inline: auto;
    }
  }
  @container card (min-width:250px) {
    .\@\[250px\]\/card\:text-3xl {
      font-size: var(--text-3xl);
      line-height: var(--tw-leading, var(--text-3xl--line-height));
    }
  }
  @container field-group (min-width:28rem) {
    .\@md\/field-group\:flex-row {
      flex-direction: row;
    }
    .\@md\/field-group\:items-center {
      align-items: center;
    }
    .\@md\/field-group\:has-\[\>\[data-slot\=field-content\]\]\:items-start:has(
        > [data-slot="field-content"]
      ) {
      align-items: flex-start;
    }
  }
  @container main (min-width:36rem) {
    .\@xl\/main\:grid-cols-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @container main (min-width:64rem) {
    .\@5xl\/main\:grid-cols-4 {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
  }
  .dark\:scale-0:is(.dark *) {
    --tw-scale-x: 0%;
    --tw-scale-y: 0%;
    --tw-scale-z: 0%;
    scale: var(--tw-scale-x) var(--tw-scale-y);
  }
  .dark\:scale-100:is(.dark *) {
    --tw-scale-x: 100%;
    --tw-scale-y: 100%;
    --tw-scale-z: 100%;
    scale: var(--tw-scale-x) var(--tw-scale-y);
  }
  .dark\:-rotate-90:is(.dark *) {
    rotate: -90deg;
  }
  .dark\:rotate-0:is(.dark *) {
    rotate: none;
  }
  .dark\:border-blue-800:is(.dark *) {
    border-color: var(--color-blue-800);
  }
  .dark\:border-input:is(.dark *) {
    border-color: var(--input);
  }
  .dark\:bg-blue-950:is(.dark *) {
    background-color: var(--color-blue-950);
  }
  .dark\:bg-destructive\/60:is(.dark *) {
    background-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:bg-destructive\/60:is(.dark *) {
      background-color: color-mix(
        in oklab,
        var(--destructive) 60%,
        transparent
      );
    }
  }
  .dark\:bg-input\/30:is(.dark *) {
    background-color: var(--input);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:bg-input\/30:is(.dark *) {
      background-color: color-mix(in oklab, var(--input) 30%, transparent);
    }
  }
  .dark\:bg-primary:is(.dark *) {
    background-color: var(--primary);
  }
  .dark\:bg-transparent:is(.dark *) {
    background-color: #0000;
  }
  .dark\:from-blue-900\/20:is(.dark *) {
    --tw-gradient-from: #1c398e33;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:from-blue-900\/20:is(.dark *) {
      --tw-gradient-from: color-mix(
        in oklab,
        var(--color-blue-900) 20%,
        transparent
      );
    }
  }
  .dark\:from-blue-900\/20:is(.dark *) {
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .dark\:from-white:is(.dark *) {
    --tw-gradient-from: var(--color-white);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .dark\:via-purple-300:is(.dark *) {
    --tw-gradient-via: var(--color-purple-300);
    --tw-gradient-via-stops: var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position), var(
          --tw-gradient-via
        ) var(--tw-gradient-via-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position);
    --tw-gradient-stops: var(--tw-gradient-via-stops);
  }
  .dark\:to-blue-300:is(.dark *) {
    --tw-gradient-to: var(--color-blue-300);
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .dark\:to-purple-900\/20:is(.dark *) {
    --tw-gradient-to: #59168b33;
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:to-purple-900\/20:is(.dark *) {
      --tw-gradient-to: color-mix(
        in oklab,
        var(--color-purple-900) 20%,
        transparent
      );
    }
  }
  .dark\:to-purple-900\/20:is(.dark *) {
    --tw-gradient-stops: var(
      --tw-gradient-via-stops,
      var(--tw-gradient-position),
      var(--tw-gradient-from) var(--tw-gradient-from-position),
      var(--tw-gradient-to) var(--tw-gradient-to-position)
    );
  }
  .dark\:text-blue-100:is(.dark *) {
    color: var(--color-blue-100);
  }
  .dark\:text-gray-400:is(.dark *) {
    color: var(--color-gray-400);
  }
  .dark\:text-muted-foreground:is(.dark *) {
    color: var(--muted-foreground);
  }
  .dark\:text-white:is(.dark *) {
    color: var(--color-white);
  }
  @media (hover: hover) {
    .dark\:hover\:bg-accent\/50:is(.dark *):hover {
      background-color: var(--accent);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .dark\:hover\:bg-accent\/50:is(.dark *):hover {
        background-color: color-mix(in oklab, var(--accent) 50%, transparent);
      }
    }
    .dark\:hover\:bg-input\/50:is(.dark *):hover {
      background-color: var(--input);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .dark\:hover\:bg-input\/50:is(.dark *):hover {
        background-color: color-mix(in oklab, var(--input) 50%, transparent);
      }
    }
    .dark\:hover\:bg-primary\/90:is(.dark *):hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .dark\:hover\:bg-primary\/90:is(.dark *):hover {
        background-color: color-mix(in oklab, var(--primary) 90%, transparent);
      }
    }
    .dark\:hover\:text-accent-foreground:is(.dark *):hover {
      color: var(--accent-foreground);
    }
    .dark\:hover\:text-gray-100:is(.dark *):hover {
      color: var(--color-gray-100);
    }
  }
  .dark\:focus-visible\:ring-destructive\/40:is(.dark *):focus-visible {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:focus-visible\:ring-destructive\/40:is(.dark *):focus-visible {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 40%, transparent);
    }
  }
  .dark\:has-data-\[state\=checked\]\:bg-primary\/10:is(.dark *):has(
      [data-state="checked"]
    ) {
    background-color: var(--primary);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:has-data-\[state\=checked\]\:bg-primary\/10:is(.dark *):has(
        [data-state="checked"]
      ) {
      background-color: color-mix(in oklab, var(--primary) 10%, transparent);
    }
  }
  .dark\:has-\[\[data-slot\]\[aria-invalid\=true\]\]\:ring-destructive\/40:is(
      .dark *
    ):has([data-slot][aria-invalid="true"]) {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:has-\[\[data-slot\]\[aria-invalid\=true\]\]\:ring-destructive\/40:is(
        .dark *
      ):has([data-slot][aria-invalid="true"]) {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 40%, transparent);
    }
  }
  .dark\:aria-invalid\:ring-destructive\/40:is(.dark *)[aria-invalid="true"] {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:aria-invalid\:ring-destructive\/40:is(.dark *)[aria-invalid="true"] {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 40%, transparent);
    }
  }
  .dark\:data-\[active\=true\]\:aria-invalid\:ring-destructive\/40:is(
      .dark *
    )[data-active="true"][aria-invalid="true"] {
    --tw-ring-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:data-\[active\=true\]\:aria-invalid\:ring-destructive\/40:is(
        .dark *
      )[data-active="true"][aria-invalid="true"] {
      --tw-ring-color: color-mix(in oklab, var(--destructive) 40%, transparent);
    }
  }
  :is(
      .dark\:\*\:data-\[slot\=card\]\:bg-card:is(.dark *) > *
    )[data-slot="card"] {
    background-color: var(--card);
  }
  .dark\:data-\[state\=active\]\:border-input:is(.dark *)[data-state="active"] {
    border-color: var(--input);
  }
  .dark\:data-\[state\=active\]\:bg-input\/30:is(.dark *)[data-state="active"] {
    background-color: var(--input);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:data-\[state\=active\]\:bg-input\/30:is(
        .dark *
      )[data-state="active"] {
      background-color: color-mix(in oklab, var(--input) 30%, transparent);
    }
  }
  .dark\:data-\[state\=active\]\:text-foreground:is(
      .dark *
    )[data-state="active"] {
    color: var(--foreground);
  }
  .dark\:data-\[state\=checked\]\:bg-primary:is(.dark *)[data-state="checked"] {
    background-color: var(--primary);
  }
  .dark\:data-\[state\=checked\]\:bg-primary-foreground:is(
      .dark *
    )[data-state="checked"] {
    background-color: var(--primary-foreground);
  }
  .dark\:data-\[state\=unchecked\]\:bg-foreground:is(
      .dark *
    )[data-state="unchecked"] {
    background-color: var(--foreground);
  }
  .dark\:data-\[state\=unchecked\]\:bg-input\/80:is(
      .dark *
    )[data-state="unchecked"] {
    background-color: var(--input);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:data-\[state\=unchecked\]\:bg-input\/80:is(
        .dark *
      )[data-state="unchecked"] {
      background-color: color-mix(in oklab, var(--input) 80%, transparent);
    }
  }
  .dark\:data-\[variant\=destructive\]\:focus\:bg-destructive\/20:is(
      .dark *
    )[data-variant="destructive"]:focus {
    background-color: var(--destructive);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .dark\:data-\[variant\=destructive\]\:focus\:bg-destructive\/20:is(
        .dark *
      )[data-variant="destructive"]:focus {
      background-color: color-mix(
        in oklab,
        var(--destructive) 20%,
        transparent
      );
    }
  }
  .\[\&_\.recharts-cartesian-axis-tick_text\]\:fill-muted-foreground
    .recharts-cartesian-axis-tick
    text {
    fill: var(--muted-foreground);
  }
  .\[\&_\.recharts-cartesian-grid_line\[stroke\=\'\#ccc\'\]\]\:stroke-border\/50
    .recharts-cartesian-grid
    line[stroke="#ccc"] {
    stroke: var(--border);
  }
  @supports (color: color-mix(in lab, red, red)) {
    .\[\&_\.recharts-cartesian-grid_line\[stroke\=\'\#ccc\'\]\]\:stroke-border\/50
      .recharts-cartesian-grid
      line[stroke="#ccc"] {
      stroke: color-mix(in oklab, var(--border) 50%, transparent);
    }
  }
  .\[\&_\.recharts-curve\.recharts-tooltip-cursor\]\:stroke-border
    .recharts-curve.recharts-tooltip-cursor {
    stroke: var(--border);
  }
  .\[\&_\.recharts-dot\[stroke\=\'\#fff\'\]\]\:stroke-transparent
    .recharts-dot[stroke="#fff"] {
    stroke: #0000;
  }
  .\[\&_\.recharts-layer\]\:outline-hidden .recharts-layer {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .\[\&_\.recharts-layer\]\:outline-hidden .recharts-layer {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .\[\&_\.recharts-polar-grid_\[stroke\=\'\#ccc\'\]\]\:stroke-border
    .recharts-polar-grid
    [stroke="#ccc"] {
    stroke: var(--border);
  }
  .\[\&_\.recharts-radial-bar-background-sector\]\:fill-muted
    .recharts-radial-bar-background-sector,
  .\[\&_\.recharts-rectangle\.recharts-tooltip-cursor\]\:fill-muted
    .recharts-rectangle.recharts-tooltip-cursor {
    fill: var(--muted);
  }
  .\[\&_\.recharts-reference-line_\[stroke\=\'\#ccc\'\]\]\:stroke-border
    .recharts-reference-line
    [stroke="#ccc"] {
    stroke: var(--border);
  }
  .\[\&_\.recharts-sector\]\:outline-hidden .recharts-sector {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .\[\&_\.recharts-sector\]\:outline-hidden .recharts-sector {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .\[\&_\.recharts-sector\[stroke\=\'\#fff\'\]\]\:stroke-transparent
    .recharts-sector[stroke="#fff"] {
    stroke: #0000;
  }
  .\[\&_\.recharts-surface\]\:outline-hidden .recharts-surface {
    --tw-outline-style: none;
    outline-style: none;
  }
  @media (forced-colors: active) {
    .\[\&_\.recharts-surface\]\:outline-hidden .recharts-surface {
      outline-offset: 2px;
      outline: 2px solid #0000;
    }
  }
  .\[\&_\[cmdk-group-heading\]\]\:px-2 [cmdk-group-heading] {
    padding-inline: calc(var(--spacing) * 2);
  }
  .\[\&_\[cmdk-group-heading\]\]\:py-1\.5 [cmdk-group-heading] {
    padding-block: calc(var(--spacing) * 1.5);
  }
  .\[\&_\[cmdk-group-heading\]\]\:text-xs [cmdk-group-heading] {
    font-size: var(--text-xs);
    line-height: var(--tw-leading, var(--text-xs--line-height));
  }
  .\[\&_\[cmdk-group-heading\]\]\:font-medium [cmdk-group-heading] {
    --tw-font-weight: var(--font-weight-medium);
    font-weight: var(--font-weight-medium);
  }
  .\[\&_\[cmdk-group-heading\]\]\:text-muted-foreground [cmdk-group-heading] {
    color: var(--muted-foreground);
  }
  .\[\&_\[cmdk-group\]\]\:px-2 [cmdk-group] {
    padding-inline: calc(var(--spacing) * 2);
  }
  .\[\&_\[cmdk-group\]\:not\(\[hidden\]\)_\~\[cmdk-group\]\]\:pt-0
    [cmdk-group]:not([hidden])
    ~ [cmdk-group] {
    padding-top: calc(var(--spacing) * 0);
  }
  .\[\&_\[cmdk-input-wrapper\]_svg\]\:h-5 [cmdk-input-wrapper] svg {
    height: calc(var(--spacing) * 5);
  }
  .\[\&_\[cmdk-input-wrapper\]_svg\]\:w-5 [cmdk-input-wrapper] svg {
    width: calc(var(--spacing) * 5);
  }
  .\[\&_\[cmdk-input\]\]\:h-12 [cmdk-input] {
    height: calc(var(--spacing) * 12);
  }
  .\[\&_\[cmdk-item\]\]\:px-2 [cmdk-item] {
    padding-inline: calc(var(--spacing) * 2);
  }
  .\[\&_\[cmdk-item\]\]\:py-3 [cmdk-item] {
    padding-block: calc(var(--spacing) * 3);
  }
  .\[\&_\[cmdk-item\]_svg\]\:h-5 [cmdk-item] svg {
    height: calc(var(--spacing) * 5);
  }
  .\[\&_\[cmdk-item\]_svg\]\:w-5 [cmdk-item] svg {
    width: calc(var(--spacing) * 5);
  }
  .\[\&_img\]\:size-full img {
    width: 100%;
    height: 100%;
  }
  .\[\&_img\]\:object-cover img {
    object-fit: cover;
  }
  .\[\&_p\]\:leading-relaxed p {
    --tw-leading: var(--leading-relaxed);
    line-height: var(--leading-relaxed);
  }
  .\[\&_svg\]\:pointer-events-none svg {
    pointer-events: none;
  }
  .\[\&_svg\]\:shrink-0 svg {
    flex-shrink: 0;
  }
  .\[\&_svg\:not\(\[class\*\=\'size-\'\]\)\]\:size-3 svg:not([class*="size-"]) {
    width: calc(var(--spacing) * 3);
    height: calc(var(--spacing) * 3);
  }
  .\[\&_svg\:not\(\[class\*\=\'size-\'\]\)\]\:size-4 svg:not([class*="size-"]) {
    width: calc(var(--spacing) * 4);
    height: calc(var(--spacing) * 4);
  }
  .\[\&_svg\:not\(\[class\*\=\'size-\'\]\)\]\:size-6 svg:not([class*="size-"]) {
    width: calc(var(--spacing) * 6);
    height: calc(var(--spacing) * 6);
  }
  .\[\&_svg\:not\(\[class\*\=\'text-\'\]\)\]\:text-muted-foreground
    svg:not([class*="text-"]) {
    color: var(--muted-foreground);
  }
  .\[\&_tr\]\:border-b tr {
    border-bottom-style: var(--tw-border-style);
    border-bottom-width: 1px;
  }
  .\[\&_tr\:last-child\]\:border-0 tr:last-child {
    border-style: var(--tw-border-style);
    border-width: 0;
  }
  .\[\&\+\[data-slot\=item-content\]\]\:flex-none + [data-slot="item-content"] {
    flex: none;
  }
  .\[\&\:first-child\[data-selected\=true\]_button\]\:rounded-l-md:first-child[data-selected="true"]
    button {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .\[\&\:has\(\[role\=checkbox\]\)\]\:pr-0:has([role="checkbox"]) {
    padding-right: calc(var(--spacing) * 0);
  }
  .\[\.border-b\]\:pb-3.border-b {
    padding-bottom: calc(var(--spacing) * 3);
  }
  .\[\.border-b\]\:pb-6.border-b {
    padding-bottom: calc(var(--spacing) * 6);
  }
  .\[\.border-t\]\:pt-3.border-t {
    padding-top: calc(var(--spacing) * 3);
  }
  .\[\.border-t\]\:pt-6.border-t {
    padding-top: calc(var(--spacing) * 6);
  }
  :is(
      .rtl\:\*\*\:\[\.rdp-button\\_next\>svg\]\:rotate-180:where(
          :dir(rtl),
          [dir="rtl"],
          [dir="rtl"] *
        )
        *
    ):is(.rdp-button_next > svg),
  :is(
      .rtl\:\*\*\:\[\.rdp-button\\_previous\>svg\]\:rotate-180:where(
          :dir(rtl),
          [dir="rtl"],
          [dir="rtl"] *
        )
        *
    ):is(.rdp-button_previous > svg) {
    rotate: 180deg;
  }
  .\[a\]\:transition-colors:is(a) {
    transition-property: color, background-color, border-color, outline-color,
      text-decoration-color, fill, stroke, --tw-gradient-from,
      --tw-gradient-via, --tw-gradient-to;
    transition-timing-function: var(
      --tw-ease,
      var(--default-transition-timing-function)
    );
    transition-duration: var(--tw-duration, var(--default-transition-duration));
  }
  @media (hover: hover) {
    .\[a\]\:hover\:bg-accent\/50:is(a):hover {
      background-color: var(--accent);
    }
    @supports (color: color-mix(in lab, red, red)) {
      .\[a\]\:hover\:bg-accent\/50:is(a):hover {
        background-color: color-mix(in oklab, var(--accent) 50%, transparent);
      }
    }
  }
  :is(.\*\:\[span\]\:last\:flex > *):is(span):last-child {
    display: flex;
  }
  :is(.\*\:\[span\]\:last\:items-center > *):is(span):last-child {
    align-items: center;
  }
  :is(.\*\:\[span\]\:last\:gap-2 > *):is(span):last-child {
    gap: calc(var(--spacing) * 2);
  }
  :is(
      .data-\[variant\=destructive\]\:\*\:\[svg\]\:\!text-destructive[data-variant="destructive"]
        > *
    ):is(svg) {
    color: var(--destructive) !important;
  }
  .\[\&\:last-child\[data-selected\=true\]_button\]\:rounded-r-md:last-child[data-selected="true"]
    button {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .\[\&\:nth-child\(2\)\[data-selected\=true\]_button\]\:rounded-l-md:nth-child(
      2
    )[data-selected="true"]
    button {
    border-top-left-radius: calc(var(--radius) - 2px);
    border-bottom-left-radius: calc(var(--radius) - 2px);
  }
  .\[\&\>\*\]\:w-full > * {
    width: 100%;
  }
  .\[\&\>\*\]\:focus-visible\:relative > :focus-visible {
    position: relative;
  }
  .\[\&\>\*\]\:focus-visible\:z-10 > :focus-visible {
    z-index: 10;
  }
  .\[\&\>\*\]\:data-\[slot\=field\]\:p-4 > [data-slot="field"] {
    padding: calc(var(--spacing) * 4);
  }
  @container field-group (min-width:28rem) {
    .\@md\/field-group\:\[\&\>\*\]\:w-auto > * {
      width: auto;
    }
  }
  .\[\&\>\*\:not\(\:first-child\)\]\:rounded-t-none > :not(:first-child) {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
  }
  .\[\&\>\*\:not\(\:first-child\)\]\:rounded-l-none > :not(:first-child) {
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
  }
  .\[\&\>\*\:not\(\:first-child\)\]\:border-t-0 > :not(:first-child) {
    border-top-style: var(--tw-border-style);
    border-top-width: 0;
  }
  .\[\&\>\*\:not\(\:first-child\)\]\:border-l-0 > :not(:first-child) {
    border-left-style: var(--tw-border-style);
    border-left-width: 0;
  }
  .\[\&\>\*\:not\(\:last-child\)\]\:rounded-r-none > :not(:last-child) {
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
  }
  .\[\&\>\*\:not\(\:last-child\)\]\:rounded-b-none > :not(:last-child) {
    border-bottom-right-radius: 0;
    border-bottom-left-radius: 0;
  }
  .\[\&\>\.sr-only\]\:w-auto > .sr-only {
    width: auto;
  }
  .\[\&\>\[data-slot\=field-group\]\]\:gap-4 > [data-slot="field-group"] {
    gap: calc(var(--spacing) * 4);
  }
  .\[\&\>\[data-slot\=field-label\]\]\:flex-auto > [data-slot="field-label"] {
    flex: auto;
  }
  @container field-group (min-width:28rem) {
    .\@md\/field-group\:\[\&\>\[data-slot\=field-label\]\]\:flex-auto
      > [data-slot="field-label"] {
      flex: auto;
    }
  }
  .has-\[select\[aria-hidden\=true\]\:last-child\]\:\[\&\>\[data-slot\=select-trigger\]\:last-of-type\]\:rounded-r-md:has(
      :is(select[aria-hidden="true"]:last-child)
    )
    > [data-slot="select-trigger"]:last-of-type {
    border-top-right-radius: calc(var(--radius) - 2px);
    border-bottom-right-radius: calc(var(--radius) - 2px);
  }
  .\[\&\>\[data-slot\=select-trigger\]\:not\(\[class\*\=\'w-\'\]\)\]\:w-fit
    > [data-slot="select-trigger"]:not([class*="w-"]) {
    width: fit-content;
  }
  .\[\&\>\[role\=checkbox\]\]\:translate-y-\[2px\] > [role="checkbox"] {
    --tw-translate-y: 2px;
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .has-\[\>\[data-slot\=field-content\]\]\:\[\&\>\[role\=checkbox\]\,\[role\=radio\]\]\:mt-px:has(
      > [data-slot="field-content"]
    )
    > [role="checkbox"],
  .has-\[\>\[data-slot\=field-content\]\]\:\[\&\>\[role\=checkbox\]\,\[role\=radio\]\]\:mt-px:has(
      > [data-slot="field-content"]
    )
    [role="radio"] {
    margin-top: 1px;
  }
  @container field-group (min-width:28rem) {
    .\@md\/field-group\:has-\[\>\[data-slot\=field-content\]\]\:\[\&\>\[role\=checkbox\]\,\[role\=radio\]\]\:mt-px:has(
        > [data-slot="field-content"]
      )
      > [role="checkbox"],
    .\@md\/field-group\:has-\[\>\[data-slot\=field-content\]\]\:\[\&\>\[role\=checkbox\]\,\[role\=radio\]\]\:mt-px:has(
        > [data-slot="field-content"]
      )
      [role="radio"] {
      margin-top: 1px;
    }
  }
  .\[\&\>a\]\:underline > a {
    text-decoration-line: underline;
  }
  .\[\&\>a\]\:underline-offset-4 > a {
    text-underline-offset: 4px;
  }
  .\[\&\>a\:hover\]\:text-primary > a:hover {
    color: var(--primary);
  }
  .\[\&\>button\]\:hidden > button {
    display: none;
  }
  .\[\&\>input\]\:flex-1 > input {
    flex: 1;
  }
  .has-\[\>\[data-align\=block-end\]\]\:\[\&\>input\]\:pt-3:has(
      > [data-align="block-end"]
    )
    > input {
    padding-top: calc(var(--spacing) * 3);
  }
  .has-\[\>\[data-align\=block-start\]\]\:\[\&\>input\]\:pb-3:has(
      > [data-align="block-start"]
    )
    > input {
    padding-bottom: calc(var(--spacing) * 3);
  }
  .has-\[\>\[data-align\=inline-end\]\]\:\[\&\>input\]\:pr-2:has(
      > [data-align="inline-end"]
    )
    > input {
    padding-right: calc(var(--spacing) * 2);
  }
  .has-\[\>\[data-align\=inline-start\]\]\:\[\&\>input\]\:pl-2:has(
      > [data-align="inline-start"]
    )
    > input {
    padding-left: calc(var(--spacing) * 2);
  }
  .\[\&\>kbd\]\:rounded-\[calc\(var\(--radius\)-5px\)\] > kbd {
    border-radius: calc(var(--radius) - 5px);
  }
  .\[\&\>span\]\:text-xs > span {
    font-size: var(--text-xs);
    line-height: var(--tw-leading, var(--text-xs--line-height));
  }
  .\[\&\>span\]\:opacity-70 > span {
    opacity: 0.7;
  }
  .\[\&\>span\:last-child\]\:truncate > span:last-child {
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow: hidden;
  }
  .\[\&\>svg\]\:pointer-events-none > svg {
    pointer-events: none;
  }
  .\[\&\>svg\]\:size-3 > svg {
    width: calc(var(--spacing) * 3);
    height: calc(var(--spacing) * 3);
  }
  .\[\&\>svg\]\:size-3\.5 > svg {
    width: calc(var(--spacing) * 3.5);
    height: calc(var(--spacing) * 3.5);
  }
  .\[\&\>svg\]\:size-4 > svg {
    width: calc(var(--spacing) * 4);
    height: calc(var(--spacing) * 4);
  }
  .\[\&\>svg\]\:h-2\.5 > svg {
    height: calc(var(--spacing) * 2.5);
  }
  .\[\&\>svg\]\:h-3 > svg {
    height: calc(var(--spacing) * 3);
  }
  .\[\&\>svg\]\:w-2\.5 > svg {
    width: calc(var(--spacing) * 2.5);
  }
  .\[\&\>svg\]\:w-3 > svg {
    width: calc(var(--spacing) * 3);
  }
  .\[\&\>svg\]\:shrink-0 > svg {
    flex-shrink: 0;
  }
  .\[\&\>svg\]\:translate-y-0\.5 > svg {
    --tw-translate-y: calc(var(--spacing) * 0.5);
    translate: var(--tw-translate-x) var(--tw-translate-y);
  }
  .\[\&\>svg\]\:text-current > svg {
    color: currentColor;
  }
  .\[\&\>svg\]\:text-muted-foreground > svg {
    color: var(--muted-foreground);
  }
  .\[\&\>svg\]\:text-sidebar-accent-foreground > svg {
    color: var(--sidebar-accent-foreground);
  }
  .\[\&\>svg\:not\(\[class\*\=\'size-\'\]\)\]\:size-3\.5
    > svg:not([class*="size-"]) {
    width: calc(var(--spacing) * 3.5);
    height: calc(var(--spacing) * 3.5);
  }
  .\[\&\>svg\:not\(\[class\*\=\'size-\'\]\)\]\:size-4
    > svg:not([class*="size-"]) {
    width: calc(var(--spacing) * 4);
    height: calc(var(--spacing) * 4);
  }
  .\[\&\>tr\]\:last\:border-b-0 > tr:last-child {
    border-bottom-style: var(--tw-border-style);
    border-bottom-width: 0;
  }
  .\[\&\[data-panel-group-direction\=vertical\]\>div\]\:rotate-90[data-panel-group-direction="vertical"]
    > div {
    rotate: 90deg;
  }
  .\[\&\[data-state\=open\]\>svg\]\:rotate-180[data-state="open"] > svg {
    rotate: 180deg;
  }
  [data-side="left"][data-collapsible="offcanvas"]
    .\[\[data-side\=left\]\[data-collapsible\=offcanvas\]_\&\]\:-right-2 {
    right: calc(var(--spacing) * -2);
  }
  [data-side="left"][data-state="collapsed"]
    .\[\[data-side\=left\]\[data-state\=collapsed\]_\&\]\:cursor-e-resize {
    cursor: e-resize;
  }
  [data-side="right"][data-collapsible="offcanvas"]
    .\[\[data-side\=right\]\[data-collapsible\=offcanvas\]_\&\]\:-left-2 {
    left: calc(var(--spacing) * -2);
  }
  [data-side="right"][data-state="collapsed"]
    .\[\[data-side\=right\]\[data-state\=collapsed\]_\&\]\:cursor-w-resize {
    cursor: w-resize;
  }
  [data-slot="card-content"]
    .\[\[data-slot\=card-content\]_\&\]\:bg-transparent,
  [data-slot="popover-content"]
    .\[\[data-slot\=popover-content\]_\&\]\:bg-transparent {
    background-color: #0000;
  }
  [data-slot="tooltip-content"]
    .\[\[data-slot\=tooltip-content\]_\&\]\:bg-background\/20 {
    background-color: var(--background);
  }
  @supports (color: color-mix(in lab, red, red)) {
    [data-slot="tooltip-content"]
      .\[\[data-slot\=tooltip-content\]_\&\]\:bg-background\/20 {
      background-color: color-mix(in oklab, var(--background) 20%, transparent);
    }
  }
  [data-slot="tooltip-content"]
    .\[\[data-slot\=tooltip-content\]_\&\]\:text-background {
    color: var(--background);
  }
  [data-slot="tooltip-content"]
    .dark\:\[\[data-slot\=tooltip-content\]_\&\]\:bg-background\/10:is(
      .dark *
    ) {
    background-color: var(--background);
  }
  @supports (color: color-mix(in lab, red, red)) {
    [data-slot="tooltip-content"]
      .dark\:\[\[data-slot\=tooltip-content\]_\&\]\:bg-background\/10:is(
        .dark *
      ) {
      background-color: color-mix(in oklab, var(--background) 10%, transparent);
    }
  }
  [data-variant="legend"] + .\[\[data-variant\=legend\]\+\&\]\:-mt-1\.5 {
    margin-top: calc(var(--spacing) * -1.5);
  }
  @media (hover: hover) {
    a.\[a\&\]\:hover\:bg-accent:hover {
      background-color: var(--accent);
    }
    a.\[a\&\]\:hover\:bg-destructive\/90:hover {
      background-color: var(--destructive);
    }
    @supports (color: color-mix(in lab, red, red)) {
      a.\[a\&\]\:hover\:bg-destructive\/90:hover {
        background-color: color-mix(
          in oklab,
          var(--destructive) 90%,
          transparent
        );
      }
    }
    a.\[a\&\]\:hover\:bg-primary\/90:hover {
      background-color: var(--primary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      a.\[a\&\]\:hover\:bg-primary\/90:hover {
        background-color: color-mix(in oklab, var(--primary) 90%, transparent);
      }
    }
    a.\[a\&\]\:hover\:bg-secondary\/90:hover {
      background-color: var(--secondary);
    }
    @supports (color: color-mix(in lab, red, red)) {
      a.\[a\&\]\:hover\:bg-secondary\/90:hover {
        background-color: color-mix(
          in oklab,
          var(--secondary) 90%,
          transparent
        );
      }
    }
    a.\[a\&\]\:hover\:text-accent-foreground:hover {
      color: var(--accent-foreground);
    }
  }
}
@property --tw-animation-delay {
  syntax: "*";
  inherits: false;
  initial-value: 0s;
}
@property --tw-animation-direction {
  syntax: "*";
  inherits: false;
  initial-value: normal;
}
@property --tw-animation-duration {
  syntax: "*";
  inherits: false;
}
@property --tw-animation-fill-mode {
  syntax: "*";
  inherits: false;
  initial-value: none;
}
@property --tw-animation-iteration-count {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-enter-blur {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-enter-opacity {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-enter-rotate {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-enter-scale {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-enter-translate-x {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-enter-translate-y {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-exit-blur {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-exit-opacity {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-exit-rotate {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-exit-scale {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-exit-translate-x {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-exit-translate-y {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
:root {
  --radius: 0.625rem;
  --background: oklch(100% 0 0);
  --foreground: oklch(14.5% 0 0);
  --card: oklch(100% 0 0);
  --card-foreground: oklch(14.5% 0 0);
  --popover: oklch(100% 0 0);
  --popover-foreground: oklch(14.5% 0 0);
  --primary: oklch(20.5% 0 0);
  --primary-foreground: oklch(98.5% 0 0);
  --secondary: oklch(97% 0 0);
  --secondary-foreground: oklch(20.5% 0 0);
  --muted: oklch(97% 0 0);
  --muted-foreground: oklch(55.6% 0 0);
  --accent: oklch(97% 0 0);
  --accent-foreground: oklch(20.5% 0 0);
  --destructive: oklch(57.7% 0.245 27.325);
  --border: oklch(92.2% 0 0);
  --input: oklch(92.2% 0 0);
  --ring: oklch(70.8% 0 0);
  --chart-1: oklch(64.6% 0.222 41.116);
  --chart-2: oklch(60% 0.118 184.704);
  --chart-3: oklch(39.8% 0.07 227.392);
  --chart-4: oklch(82.8% 0.189 84.429);
  --chart-5: oklch(76.9% 0.188 70.08);
  --sidebar: oklch(98.5% 0 0);
  --sidebar-foreground: oklch(14.5% 0 0);
  --sidebar-primary: oklch(20.5% 0 0);
  --sidebar-primary-foreground: oklch(98.5% 0 0);
  --sidebar-accent: oklch(97% 0 0);
  --sidebar-accent-foreground: oklch(20.5% 0 0);
  --sidebar-border: oklch(92.2% 0 0);
  --sidebar-ring: oklch(70.8% 0 0);
}
.dark {
  --background: oklch(14.5% 0 0);
  --foreground: oklch(98.5% 0 0);
  --card: oklch(20.5% 0 0);
  --card-foreground: oklch(98.5% 0 0);
  --popover: oklch(20.5% 0 0);
  --popover-foreground: oklch(98.5% 0 0);
  --primary: oklch(92.2% 0 0);
  --primary-foreground: oklch(20.5% 0 0);
  --secondary: oklch(26.9% 0 0);
  --secondary-foreground: oklch(98.5% 0 0);
  --muted: oklch(26.9% 0 0);
  --muted-foreground: oklch(70.8% 0 0);
  --accent: oklch(26.9% 0 0);
  --accent-foreground: oklch(98.5% 0 0);
  --destructive: oklch(70.4% 0.191 22.216);
  --border: oklch(100% 0 0/0.1);
  --input: oklch(100% 0 0/0.15);
  --ring: oklch(55.6% 0 0);
  --chart-1: oklch(48.8% 0.243 264.376);
  --chart-2: oklch(69.6% 0.17 162.48);
  --chart-3: oklch(76.9% 0.188 70.08);
  --chart-4: oklch(62.7% 0.265 303.9);
  --chart-5: oklch(64.5% 0.246 16.439);
  --sidebar: oklch(20.5% 0 0);
  --sidebar-foreground: oklch(98.5% 0 0);
  --sidebar-primary: oklch(48.8% 0.243 264.376);
  --sidebar-primary-foreground: oklch(98.5% 0 0);
  --sidebar-accent: oklch(26.9% 0 0);
  --sidebar-accent-foreground: oklch(98.5% 0 0);
  --sidebar-border: oklch(100% 0 0/0.1);
  --sidebar-ring: oklch(55.6% 0 0);
}
@property --tw-translate-x {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-translate-y {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-translate-z {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-scale-x {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-scale-y {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-scale-z {
  syntax: "*";
  inherits: false;
  initial-value: 1;
}
@property --tw-rotate-x {
  syntax: "*";
  inherits: false;
}
@property --tw-rotate-y {
  syntax: "*";
  inherits: false;
}
@property --tw-rotate-z {
  syntax: "*";
  inherits: false;
}
@property --tw-skew-x {
  syntax: "*";
  inherits: false;
}
@property --tw-skew-y {
  syntax: "*";
  inherits: false;
}
@property --tw-space-y-reverse {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-space-x-reverse {
  syntax: "*";
  inherits: false;
  initial-value: 0;
}
@property --tw-border-style {
  syntax: "*";
  inherits: false;
  initial-value: solid;
}
@property --tw-gradient-position {
  syntax: "*";
  inherits: false;
}
@property --tw-gradient-from {
  syntax: "<color>";
  inherits: false;
  initial-value: #0000;
}
@property --tw-gradient-via {
  syntax: "<color>";
  inherits: false;
  initial-value: #0000;
}
@property --tw-gradient-to {
  syntax: "<color>";
  inherits: false;
  initial-value: #0000;
}
@property --tw-gradient-stops {
  syntax: "*";
  inherits: false;
}
@property --tw-gradient-via-stops {
  syntax: "*";
  inherits: false;
}
@property --tw-gradient-from-position {
  syntax: "<length-percentage>";
  inherits: false;
  initial-value: 0%;
}
@property --tw-gradient-via-position {
  syntax: "<length-percentage>";
  inherits: false;
  initial-value: 50%;
}
@property --tw-gradient-to-position {
  syntax: "<length-percentage>";
  inherits: false;
  initial-value: 100%;
}
@property --tw-leading {
  syntax: "*";
  inherits: false;
}
@property --tw-font-weight {
  syntax: "*";
  inherits: false;
}
@property --tw-tracking {
  syntax: "*";
  inherits: false;
}
@property --tw-ordinal {
  syntax: "*";
  inherits: false;
}
@property --tw-slashed-zero {
  syntax: "*";
  inherits: false;
}
@property --tw-numeric-figure {
  syntax: "*";
  inherits: false;
}
@property --tw-numeric-spacing {
  syntax: "*";
  inherits: false;
}
@property --tw-numeric-fraction {
  syntax: "*";
  inherits: false;
}
@property --tw-shadow {
  syntax: "*";
  inherits: false;
  initial-value: 0 0 #0000;
}
@property --tw-shadow-color {
  syntax: "*";
  inherits: false;
}
@property --tw-shadow-alpha {
  syntax: "<percentage>";
  inherits: false;
  initial-value: 100%;
}
@property --tw-inset-shadow {
  syntax: "*";
  inherits: false;
  initial-value: 0 0 #0000;
}
@property --tw-inset-shadow-color {
  syntax: "*";
  inherits: false;
}
@property --tw-inset-shadow-alpha {
  syntax: "<percentage>";
  inherits: false;
  initial-value: 100%;
}
@property --tw-ring-color {
  syntax: "*";
  inherits: false;
}
@property --tw-ring-shadow {
  syntax: "*";
  inherits: false;
  initial-value: 0 0 #0000;
}
@property --tw-inset-ring-color {
  syntax: "*";
  inherits: false;
}
@property --tw-inset-ring-shadow {
  syntax: "*";
  inherits: false;
  initial-value: 0 0 #0000;
}
@property --tw-ring-inset {
  syntax: "*";
  inherits: false;
}
@property --tw-ring-offset-width {
  syntax: "<length>";
  inherits: false;
  initial-value: 0;
}
@property --tw-ring-offset-color {
  syntax: "*";
  inherits: false;
  initial-value: #fff;
}
@property --tw-ring-offset-shadow {
  syntax: "*";
  inherits: false;
  initial-value: 0 0 #0000;
}
@property --tw-outline-style {
  syntax: "*";
  inherits: false;
  initial-value: solid;
}
@property --tw-blur {
  syntax: "*";
  inherits: false;
}
@property --tw-brightness {
  syntax: "*";
  inherits: false;
}
@property --tw-contrast {
  syntax: "*";
  inherits: false;
}
@property --tw-grayscale {
  syntax: "*";
  inherits: false;
}
@property --tw-hue-rotate {
  syntax: "*";
  inherits: false;
}
@property --tw-invert {
  syntax: "*";
  inherits: false;
}
@property --tw-opacity {
  syntax: "*";
  inherits: false;
}
@property --tw-saturate {
  syntax: "*";
  inherits: false;
}
@property --tw-sepia {
  syntax: "*";
  inherits: false;
}
@property --tw-drop-shadow {
  syntax: "*";
  inherits: false;
}
@property --tw-drop-shadow-color {
  syntax: "*";
  inherits: false;
}
@property --tw-drop-shadow-alpha {
  syntax: "<percentage>";
  inherits: false;
  initial-value: 100%;
}
@property --tw-drop-shadow-size {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-blur {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-brightness {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-contrast {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-grayscale {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-hue-rotate {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-invert {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-opacity {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-saturate {
  syntax: "*";
  inherits: false;
}
@property --tw-backdrop-sepia {
  syntax: "*";
  inherits: false;
}
@property --tw-duration {
  syntax: "*";
  inherits: false;
}
@property --tw-ease {
  syntax: "*";
  inherits: false;
}
@property --tw-content {
  syntax: "*";
  inherits: false;
  initial-value: "";
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes pulse {
  50% {
    opacity: 0.5;
  }
}
@keyframes bounce {
  0%,
  to {
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    transform: translateY(-25%);
  }
  50% {
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    transform: none;
  }
}
@keyframes enter {
  0% {
    opacity: var(--tw-enter-opacity, 1);
    transform: translate3d(
        var(--tw-enter-translate-x, 0),
        var(--tw-enter-translate-y, 0),
        0
      ) scale3d(
        var(--tw-enter-scale, 1),
        var(--tw-enter-scale, 1),
        var(--tw-enter-scale, 1)
      )
      rotate(var(--tw-enter-rotate, 0));
    filter: blur(var(--tw-enter-blur, 0));
  }
}
@keyframes exit {
  to {
    opacity: var(--tw-exit-opacity, 1);
    transform: translate3d(
        var(--tw-exit-translate-x, 0),
        var(--tw-exit-translate-y, 0),
        0
      ) scale3d(
        var(--tw-exit-scale, 1),
        var(--tw-exit-scale, 1),
        var(--tw-exit-scale, 1)
      )
      rotate(var(--tw-exit-rotate, 0));
    filter: blur(var(--tw-exit-blur, 0));
  }
}
@keyframes accordion-down {
  0% {
    height: 0;
  }
  to {
    height: var(
      --radix-accordion-content-height,
      var(
        --bits-accordion-content-height,
        var(
          --reka-accordion-content-height,
          var(
            --kb-accordion-content-height,
            var(--ngp-accordion-content-height, auto)
          )
        )
      )
    );
  }
}
@keyframes accordion-up {
  0% {
    height: var(
      --radix-accordion-content-height,
      var(
        --bits-accordion-content-height,
        var(
          --reka-accordion-content-height,
          var(
            --kb-accordion-content-height,
            var(--ngp-accordion-content-height, auto)
          )
        )
      )
    );
  }
  to {
    height: 0;
  }
}
@keyframes caret-blink {
  0%,
  70%,
  to {
    opacity: 1;
  }
  20%,
  50% {
    opacity: 0;
  }
}
```

## File: backend/api/ui/assets/worker-CgCYKo1M.js

```javascript

```

## File: backend/api/ui/index.html

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Vite + React + TS</title>
    <script type="module" crossorigin src="/assets/index-DQtIvVfc.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/index-MCH6mY5D.css" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
```

## File: backend/api/ui/vite.svg

```xml
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" role="img" class="iconify iconify--logos" width="31.88" height="32" preserveAspectRatio="xMidYMid meet" viewBox="0 0 256 257"><defs><linearGradient id="IconifyId1813088fe1fbc01fb466" x1="-.828%" x2="57.636%" y1="7.652%" y2="78.411%"><stop offset="0%" stop-color="#41D1FF"></stop><stop offset="100%" stop-color="#BD34FE"></stop></linearGradient><linearGradient id="IconifyId1813088fe1fbc01fb467" x1="43.376%" x2="50.316%" y1="2.242%" y2="89.03%"><stop offset="0%" stop-color="#FFEA83"></stop><stop offset="8.333%" stop-color="#FFDD35"></stop><stop offset="100%" stop-color="#FFA800"></stop></linearGradient></defs><path fill="url(#IconifyId1813088fe1fbc01fb466)" d="M255.153 37.938L134.897 252.976c-2.483 4.44-8.862 4.466-11.382.048L.875 37.958c-2.746-4.814 1.371-10.646 6.827-9.67l120.385 21.517a6.537 6.537 0 0 0 2.322-.004l117.867-21.483c5.438-.991 9.574 4.796 6.877 9.62Z"></path><path fill="url(#IconifyId1813088fe1fbc01fb467)" d="M185.432.063L96.44 17.501a3.268 3.268 0 0 0-2.634 3.014l-5.474 92.456a3.268 3.268 0 0 0 3.997 3.378l24.777-5.718c2.318-.535 4.413 1.507 3.936 3.838l-7.361 36.047c-.495 2.426 1.782 4.5 4.151 3.78l15.304-4.649c2.372-.72 4.652 1.36 4.15 3.788l-11.698 56.621c-.732 3.542 3.979 5.473 5.943 2.437l1.313-2.028l72.516-144.72c1.215-2.423-.88-5.186-3.54-4.672l-25.505 4.922c-2.396.462-4.435-1.77-3.759-4.114l16.646-57.705c.677-2.35-1.37-4.583-3.769-4.113Z"></path></svg>
```

## File: backend/api/usecases/audit_logs_service.py

```python
logger = get_logger(__name__)
class AuditLogsService
⋮----
def __init__(self, audit_log_repository: AuditLogRepository, email_service: IEmailService)
async def read_audit_logs(self, tenant_id: Optional[str] = None, limit: int = 10, skip: int = 0, action: Optional[AuditActionType] = None) -> AuditLogListDto
async def generate_audit_logs_download(self, action: Optional[AuditActionType] = None, tenant_id: Optional[str] = None) -> List[AuditLogDto]
async def create_audit_log(self, audit_log: AuditLogDto) -> None
async def send_audit_log_report_via_email(self, to_email: str, first_name: str, attachment_data: BytesIO, attachment_filename: str) -> None
```

## File: backend/api/usecases/auth_service.py

```python
logger = get_logger(__name__)
class AuthService
⋮----
async def _get_token_set(self, user: User) -> TokenSetDto
⋮----
"""
            Helper method to generate a TokenSetDto for the given user. Dont use this method outside this class.
        """
role = await self.role_service.get_role_by_id(role_id=user.role_id)
role_doc = await role.to_serializable_dict()
payload = TokenPayloadDto(
res = await self.jwt_token_service.generate_tokens(payload)
⋮----
# Host audit log for tenant admin login
⋮----
async def login_with_magic_link(self, user_id: str) -> TokenSetDto
⋮----
user = await self.user_service.get_user_by_id(user_id=user_id)
⋮----
async def login_with_passkey(self, email: str) -> TokenSetDto
⋮----
user = await self.user_service.find_by_email(email=email)
⋮----
async def login(self, req: LoginRequestDto) -> TokenSetDto
⋮----
user = await self.user_service.find_by_email(email=req.email)
⋮----
async def login_with_sso(self, provider_name: SSOProvider, user_info: OpenID, tenant_id: PydanticObjectId | None) -> TokenSetDto
⋮----
email = user_info.email
display_name = user_info.display_name
first_name = user_info.first_name
last_name = user_info.last_name
⋮----
role: Role = await self.role_service.find_by_name(name=RoleType.GUEST)  # Ensure default role exists
⋮----
async def new_user_creation(self, new_user: CreateUserDto, tenant_id: Optional[str] = None) -> None
⋮----
not_found = True
⋮----
enabled_sso = await self.sso_settings_service.get_only_enabled_providers()
⋮----
user = await self.user_service.find_by_email(email = new_user.email)
⋮----
not_found = False
⋮----
async def register(self, new_user: CreateUserDto, cb: Callable) -> None
⋮----
"""
            Register a new user. In multi-tenant mode, if the subdomain is unique, a new tenant will be created.
            The callback function `cb` will be called with the newly created tenant ID after tenant creation.
            In single-tenant mode, the user will be created without a tenant.
            Raises InvalidOperationException if the subdomain is already taken in multi-tenant mode.
            The user will be created with the provided details.
        """
⋮----
tenant = None
⋮----
tenant = await self.tenant_service.find_by_subdomain(subdomain=new_user.sub_domain)
⋮----
# If it is coming here.. that means the request is valid and made by a legit tenant.. because the tenant middleware has already validated it.
⋮----
create_new_tenant = CreateTenantDto(
newly_created_tenant_id = await self.tenant_service.create_tenant(tenant_data=create_new_tenant)
⋮----
async def logout(self, user_id: str, tenant_id: str | None) -> None
async def refresh_token(self, token: TokenRefreshRequestDto) -> TokenSetDto
⋮----
"""
            Refresh the access token using the provided refresh token.
            On success, returns a new TokenSetDto containing the new access and refresh tokens.
            Raises UnauthorizedException if the refresh token is invalid or expired.
        """
refresh_token_payload: RefreshTokenPayloadDto = await self.jwt_token_service.decode_token(token=token.refresh_token, type="refresh_token")
⋮----
user = await self.user_service.get_user_by_id(user_id=str(refresh_token_payload.sub))
⋮----
async def initate_password_reset(self, email: EmailStr, domain: str) -> None
⋮----
"""
            Initiate the password reset process by sending a password reset email to the user.
            Raises InvalidOperationException if the user with the provided email does not exist.
            Sends a password reset email with a link to reset the password.
        """
reset_data = await self.user_service.request_password_reset(email)
data = ActivationTokenPayloadDto(
token = await self.jwt_token_service.encode_activation_token(data)
link = get_email_sharing_link(token=token, user_id=str(reset_data.user_id), domain=domain, type=data.type, tenant_id=str(reset_data.tenant_id))
html = password_reset_email_template_html(user_first_name=reset_data.first_name, password_reset_link=link)
⋮----
async def change_password(self, new_password: str, token: str, user_id: str) -> None
⋮----
"""
            Change the user's password using the provided password reset token.
            Raises InvalidOperationException if the token is invalid or expired.
            On success, updates the user's password and clears the password reset data.
        """
reset_data = await self.user_service.retrieve_password_reset_data_by_user_id(user_id=user_id)
⋮----
payload = await self.jwt_token_service.verify_password_reset_token(token=token, jwt_secret=reset_data.token_secret)
⋮----
html = notify_password_change_template_html(user_first_name=reset_data.first_name)
⋮----
async def send_activation_email(self, payload: UserResendActivationEmailRequestDto, domain: str) -> None
⋮----
"""
            Resend the activation email to the user.
            Raises InvalidOperationException if the user is already active.
            Sends an activation email with a link to activate the account.
        """
user = await self.user_service.find_by_email(email=payload.email)
⋮----
activation_token = await self.jwt_token_service.encode_activation_token(payload=ActivationTokenPayloadDto(
link = get_email_sharing_link(token=activation_token, user_id=payload.id, domain=domain, type="activation", tenant_id=payload.tenant_id)
html = activation_template_html(user_first_name=payload.first_name, activation_link=link)
⋮----
async def activate_account(self, req: UserActivationRequestDto) -> None
⋮----
"""
            Activate a user account using the provided activation token.
            Raises InvalidOperationException if the user is already active or if the token is invalid.
        """
payload = await self.jwt_token_service.verify_activation_token(token=req.token)
user = await self.user_service.get_user_by_id(user_id=payload.user_id)
⋮----
async def change_email_request(self, current_email: EmailStr, new_email: EmailStr, domain: str) -> None
⋮----
"""
            Initiate the email change process by sending a confirmation email to the new email address.
            Raises InvalidOperationException if the new email is the same as the current email or if the new email is already in use.
            Sends a confirmation email to the new email address with a link to confirm the email change.
            The actual email change is completed in the `change_email_confirmation` method.
        """
⋮----
does_exist = await self.user_service.check_email_exists(email=new_email)
⋮----
user = await self.user_service.find_by_email(email=current_email)
⋮----
link = get_email_sharing_link(token=activation_token, user_id=str(user.id), domain=domain, type="change_email_confirmation", tenant_id=str(user.tenant_id))
html = email_changes_confirmation_template_html(user_first_name=user.first_name, email_change_link=link)
⋮----
async def change_email_confirmation(self, token: str) -> None
⋮----
"""
            Confirm and complete the email change process.
            Raises InvalidOperationException if the new email is already in use.
            Updates the user's email to the new email address after confirmation.
            Sends a notification email to the user about the email change.
        """
payload = await self.jwt_token_service.verify_change_email_token(token=token)
⋮----
does_exist = await self.user_service.check_email_exists(email=payload.email)
⋮----
html = notify_email_change_template_html(user_first_name=user.first_name)
⋮----
async def get_user_service(self) -> UserService
```

## File: backend/api/usecases/billing_record_service.py

```python
logger = get_logger(__name__)
class BillingRecordService
⋮----
# Helper methods
⋮----
period = line.get("period", {})
⋮----
pricing = line.get("pricing") or line.get("price")
⋮----
# New Stripe format
details = pricing["price_details"]
⋮----
def _extract_tenant_id(self, line: dict[str, Any], scope: str) -> Optional[str]
⋮----
# Most reliable: line metadata
tenant_id = line.get("metadata", {}).get("tenant_id")
⋮----
# Fallback: subscription metadata
parent = line.get("parent") or {}
sub_details = parent.get("subscription_details") or {}
metadata = sub_details.get("metadata") or {}
⋮----
# Helper methods end here
async def list_plans(self, scope: ScopeType) -> PlanListDto
⋮----
sc = await self.stripe_resolver.get_stripe_client(scope=scope)
result = await sc.v1.plans.list_async(params={"limit": 100})
⋮----
async def create_plan(self, new_plan: CreatePlanDto, scope: ScopeType) -> None
⋮----
async def get_plan(self, plan_id: str, scope: ScopeType) -> PlanDto
async def delete_plan(self, plan_id: str, scope: ScopeType) -> None
⋮----
result = await sc.v1.plans.delete_async(plan=plan_id)
⋮----
async def list_invoices(self, scope: ScopeType) -> InvoiceListDto
⋮----
result = await sc.v1.invoices.list_async(params={"limit": 100})
⋮----
"""
        Create a Stripe checkout session for host scope for a tenant
        """
sc: StripeClient = await self.stripe_resolver.get_stripe_client(scope="host")
customer = await sc.v1.customers.create_async(
checkout_session = await sc.v1.checkout.sessions.create_async(
billing_record = BillingRecord(
⋮----
"""
        Create a Stripe checkout session for tenant scope for tenant's users
        """
sc: StripeClient = await self.stripe_resolver.get_stripe_client(scope="tenant")
⋮----
"""
        Handle successful checkout for host scope
        """
⋮----
session = await sc.v1.checkout.sessions.retrieve_async(session=session_id)
⋮----
billing_record = await self.billing_record_repository.single_or_none(
⋮----
async def handle_host_checkout_canceled(self, tenant_id: str) -> None
⋮----
"""
        Handle cancelled checkout for host scope
        """
billing_records = await self.billing_record_repository.search(
⋮----
"""
        Handle successful checkout for tenant scope
        """
⋮----
async def handle_tenant_checkout_canceled(self, user_id: str) -> None
⋮----
"""
        Handle cancelled checkout for tenant scope
        """
billing_records = await self.billing_record_repository.list(
⋮----
"""
        List checkout records
        """
⋮----
"""
        Converts a Stripe `invoice.paid` event into a BillingRecordDto.
        Returns None if not applicable (e.g. no subscription).
        """
# Extract first line item (99.9% of cases there's only one)
lines = invoice.get("lines", {}).get("data", [])
⋮----
line = lines[0]
⋮----
tenant_id = tenant_id or self._extract_tenant_id(line, scope)
# Build Object
⋮----
async def create_billing_record(self, billing_record: BillingRecord) -> None
⋮----
"""
        Create a billing record in the database
        """
```

## File: backend/api/usecases/coolify_app_service.py

```python
class CoolifyAppService(CoolifyApp)
⋮----
def __init__(self)
⋮----
# Any additional initialization if needed
```

## File: backend/api/usecases/file_service.py

```python
class FileService(FileUpload, FileRetrieval)
⋮----
async def get_file_url(self, file_key: str) -> str
```

## File: backend/api/usecases/local_ai_service.py

```python
logger = get_logger(__name__)
class LocalAIService
⋮----
async def is_session_exists(self, session_id: str) -> bool
async def get_user_sessions(self, user_id: str, limit: int = 100) -> List[AISessionByUserIdDto]
⋮----
"""
        Get user sessions with chat histories
        1. Match sessions by user_id
        2. Sort by created_at desc
        3. Limit by `limit`
        4. Lookup chat histories by history_id
        5. Return sessions with chat histories
        6. Convert ObjectId to str in the result
        7. Return the result as List[AISessionByUserIdDto]
        """
pipeline = [
sessions = await self.chat_session_repository.aggregate(pipeline, projection_model=AISessions)
results: List[AISessionByUserIdDto] = []
⋮----
serializable_dict = await s.to_serializable_dict()
⋮----
async def get_histories_by_session_id(self, user_id: str, session_id: str) -> List[AIHistoriesDto]
⋮----
session = await self.chat_session_repository.single_or_none(session_id=PydanticObjectId(session_id), user_id=PydanticObjectId(user_id))
⋮----
history = await self.chat_history_repository.single_or_none(_id=session.history_id)
⋮----
serializable_dict = await history.to_serializable_dict()
⋮----
async def delete_session(self, user_id: str, session_id: str) -> None
⋮----
history = await self.chat_history_repository.single_or_none(id=session.history_id)
⋮----
async def save_user_query(self, user_id: str, query: str, response: str, session_id: str | None = None, tenant_id: str | None = None) -> None
⋮----
session = await self.chat_session_repository.single_or_none(session_id=PydanticObjectId(session_id))
histories = {
data = ChatHistoryAI(
# If session does not exist, then there is no history, so create a new history
⋮----
new_history = await self.chat_history_repository.create(data=data.model_dump())
session = ChatSessionAI(
new_session = await self.chat_session_repository.create(data=session.model_dump())
⋮----
exisiting_history = await self.chat_history_repository.single_or_none(_id=session.history_id)
```

## File: backend/api/usecases/magic_link_service.py

```python
class EmailMagicLinkService
⋮----
async def create_magic_link(self, user_dto: UserDto)
⋮----
"""
        Create a magic link for the user and store it in the database. And valid for 5 minutes. Tries to send an email with the magic link.
        Args:
            user_dto (UserDto): The user data transfer object containing user information.
        """
⋮----
token = hash_it(user_dto.id + JWT_SECRET)
record = await self.user_magic_link_repo.create_magic_link(user_id=user_dto.id, token=token)
domain = get_host_main_domain_name()
⋮----
tenant = await self.tenant_service.get_tenant_by_id(tenant_id=user_dto.tenant_id)
domain = tenant.custom_domain if tenant and tenant.custom_domain else tenant.subdomain
link = get_email_sharing_link(token=record.token, user_id=str(record.user_id), domain=domain, type="magic_link_login", tenant_id=str(record.tenant_id))
html_content = magic_link_login_template(user_first_name=user_dto.first_name, magic_link=link)
⋮----
async def validate_magic_link(self, user_id: str, token: str) -> bool
⋮----
"""
        Validate the magic link token for the given user ID.
        Args:
            user_id (str): The ID of the user to validate the magic link for.
            token (str): The token to validate.
        Returns:
            bool: True if the token is valid, False otherwise.
        """
record = await self.user_magic_link_repo.single_or_none(user_id=PydanticObjectId(user_id), token=token)
⋮----
result = validate_hashed_value(value=user_id, hashed_value=token)
⋮----
# Optionally, you can delete the magic link after successful validation to prevent reuse
```

## File: backend/api/usecases/notification_banner_service.py

```python
class NotificationBannerService
⋮----
def __init__(self, repository: NotificationBannerRepository)
async def get_banner_setting(self) -> NotificationBannerSettingDto
⋮----
result = await self.repository.get_banner_setting()
⋮----
async def create_banner_setting(self, is_enabled: bool, message: Optional[str] = None, tenant_id: Optional[PydanticObjectId] = None)
async def update_banner_setting(self, id: str, is_enabled: bool, message: Optional[str] = None)
```

## File: backend/api/usecases/pricing_service.py

```python
logger = get_logger(__name__)
class PricingService
⋮----
def __init__(self, payment_repository: PaymentRepository, stripe_resolver: StripeResolver)
async def create_price(self, price: CreatePricingDto, scope: ScopeType) -> None
⋮----
sc = await self.stripe_resolver.get_stripe_client(scope=scope)
⋮----
async def list_prices(self, scope: ScopeType) -> PricingListDto
⋮----
result = await sc.v1.prices.list_async(params={"limit": 100})
⋮----
async def update_price(self, price_id: str, update: UpdatePricingDto, scope: ScopeType) -> None
```

## File: backend/api/usecases/product_service.py

```python
logger = get_logger(__name__)
class ProductService
⋮----
def __init__(self, payment_repository: PaymentRepository, stripe_resolver: StripeResolver)
async def list_products(self, scope: ScopeType, show_active: bool = True) -> ProductListDto
⋮----
sc = await self.stripe_resolver.get_stripe_client(scope=scope)
result =  await sc.v1.products.list_async(params={"limit": 100, "active": show_active})
⋮----
async def get_product_by_id(self, product_id: str,  scope: ScopeType) -> ProductDto
⋮----
product = await sc.v1.products.retrieve_async(id=product_id)
⋮----
async def create_product(self, product_dto: CreateProductDto, scope: ScopeType)
⋮----
params = product_dto.model_dump()
⋮----
# Handle specific exceptions if needed
⋮----
async def update_product(self, product_id: str, product_dto: CreateProductDto, scope: ScopeType)
⋮----
existing_product = await sc.v1.products.retrieve_async(id=product_id)
⋮----
async def delete_product(self, product_id: str, scope: ScopeType)
```

## File: backend/api/usecases/role_service.py

```python
logger = get_logger(__name__)
class RoleService
⋮----
def __init__(self, role_repository: RoleRepository)
async def list_roles(self, skip: int = 0, limit: int = 10) -> RoleListDto
async def find_by_name(self, name: str) -> Role
⋮----
"""Find a role by its name. Raises RoleNotFoundException if not found."""
existing = await self.role_repository.single_or_none(name=name)
⋮----
async def search_role_by_name(self, name: str) -> list[Role]
async def get_role_by_id(self, role_id: str) -> Role
⋮----
existing = await self.role_repository.get(id=role_id)
⋮----
async def create_role(self, role_data: CreateRoleDto) -> PydanticObjectId
⋮----
existing = await self.role_repository.single_or_none(name=role_data.name)
⋮----
async def update_role(self, role_id: str, role_data: UpdateRoleDto) -> Role | None
async def delete_role(self, role_id: str) -> None
async def total_count(self) -> int
```

## File: backend/api/usecases/sso_settings_service.py

```python
logger = get_logger(__name__)
class SSOSettingsService
⋮----
def __init__(self, sso_settings_provider_repository: SSOSettingsProviderRepository, tenant_service: TenantService)
async def get_sso_settings_by_id(self, id: str) -> ReadSSOSettingsDto
⋮----
res = await self.sso_settings_provider_repository.get_by_id(id)
⋮----
async def update_redirect_uri(self, id: str, redirect_uri: str) -> None
async def update_sso_settings(self, id: str, sso_settings: UpdateSSOSettingsDto) -> None
async def create_sso_settings(self, sso_settings: CreateSSOSettingsDto) -> str
⋮----
redirect_uri = get_sso_redirect_uri(sso_settings.provider)
⋮----
tenant = await self.tenant_service.get_tenant_by_id(tenant_id=sso_settings.tenant_id)
redirect_uri = get_sso_redirect_uri(sso_settings.provider, tenant.custom_domain if tenant.custom_domain else tenant.subdomain)
⋮----
res = await self.sso_settings_provider_repository.create_sso_settings(SSOSettings(**sso_settings.model_dump()))
⋮----
async def delete_sso_settings(self, id: str) -> bool
async def list(self) -> SSOSettingsListDto
⋮----
ssos: List[SSOSettings] = await self.sso_settings_provider_repository.list_sso_settings()
⋮----
items = [ReadSSOSettingsDto(**sso.model_dump(exclude={"client_secret": True})) for sso in ssos]
⋮----
async def get_provider(self, provider_name: SSOProvider) -> SSOSettings
async def get_only_enabled_providers(self) -> SSOSettingsListDto
⋮----
enabled_ssos = [sso for sso in ssos if sso.enabled]
items = [ReadSSOSettingsDto(**sso.model_dump(exclude={"client_secret": True})) for sso in enabled_ssos]
⋮----
async def get_available_sso_providers(self) -> List[str]
```

## File: backend/api/usecases/storage_settings_service.py

```python
logger = get_logger(__name__)
class StorageSettingsService
⋮----
def __init__(self, storage_settings_repository: StorageSettingsRepository)
async def configure_storage(self, setting: StorageSettingsDto, tenant_id: Optional[PydanticObjectId] = None) -> PydanticObjectId
⋮----
settings = StorageSettings(
⋮----
async def get_storages(self) -> list[AvailableStorageProviderDto]
async def reset_storage(self, storage_id: str, updated_by_user_id: str, tenant_id: Optional[PydanticObjectId] = None) -> None
⋮----
storage_setting: StorageSettings | None = await self.storage_settings_repository.get(id=storage_id)
```

## File: backend/api/usecases/stripe_setting_service.py

```python
class StripeSettingService
⋮----
def __init__(self, payment_repository: PaymentRepository, stripe_resolver: StripeResolver, tenant_service: TenantService)
async def configure_stripe_settings(self, settings: CreateStripeSettingDto, tenant_id: str) -> None
⋮----
"""
        Configure Stripe settings for the application.
        Args:
            settings (CreateStripeSettingDto): The Stripe settings to configure.
        Raises:
            StripeSettingsException: If there is an error configuring the settings.
            TenantNotFoundException: If the tenant does not exist.
        """
⋮----
async def get_stripe_settings(self) -> StripeSettingDto
⋮----
"""
        Get the Stripe settings for the application.
        Returns:
            StripeSettingDto: The Stripe settings.
        Raises:
            StripeSettingsNotFoundException: If there is an error retrieving the settings.
        """
result = await self.payment_repository.get_stored_stripe_settings()
⋮----
async def get_stripe_secret(self) -> StripeSettingSecretDto
⋮----
"""
        Get the Stripe Webhook Secret.
        Returns:
            StripeSettingSecretDto: The Webhook secret from Stripe settings.
        Raises:
            StripeSettingsNotFoundException: If there is an error retrieving the settings.
        """
```

## File: backend/api/usecases/subscription_plan_service.py

```python
class SubscriptionPlanService
⋮----
def __init__(self, subscription_plan_repository: SubscriptionPlanRepository)
async def get_subscription_plan_by_id(self, subscription_id: str) -> SubscriptionPlanDto | None
⋮----
result = await self.subscription_plan_repository.get_subscription_plan_by_id(subscription_id)
⋮----
async def get_subscription_plans_by_tenant(self, tenant_id: str) -> SubscriptionPlanDto | None
⋮----
result = await self.subscription_plan_repository.get_subscription_plans_by_tenant(tenant_id)
⋮----
async def get_subscription_plan_by_user_id(self, user_id: str) -> SubscriptionPlanDto | None
⋮----
result = await self.subscription_plan_repository.get_subscription_plan_by_user_id(user_id)
⋮----
async def create_trial_plan_for_tenant(self, tenant_id: str, plan_id: str)
⋮----
subscription_plan = CreateSubscriptionPlanDto(
result = await self.subscription_plan_repository.create_subscription_plan(subscription_plan=subscription_plan)
```

## File: backend/api/usecases/tenant_service.py

```python
logger = get_logger(__name__)
class TenantService
⋮----
def __init__(self, tenant_repository: TenantRepository)
async def list_tenants(self, skip: int = 0, limit: int = 10) -> TenantListDto
⋮----
"""List tenants with pagination."""
⋮----
async def find_by_name(self, name: str) -> Tenant | None
⋮----
"""Get tenant by name. Raises TenantNotFoundException if not found."""
exisiting = await self.tenant_repository.single_or_none(name=name)
⋮----
async def find_by_custom_domain(self, custom_domain: str) -> Tenant | None
⋮----
"""Get tenant by custom domain. Raises TenantNotFoundException if not found."""
existing = await self.tenant_repository.single_or_none(custom_domain=custom_domain)
⋮----
async def find_by_subdomain(self, subdomain: str) -> Tenant | None
⋮----
"""Get tenant by subdomain. Raises TenantNotFoundException if not found."""
existing = await self.tenant_repository.single_or_none(subdomain=subdomain)
⋮----
async def get_tenant_by_id(self, tenant_id: str) -> Tenant
⋮----
"""Get tenant by ID. Raises TenantNotFoundException if not found."""
existing = await self.tenant_repository.get(id=tenant_id)
⋮----
async def create_tenant(self, tenant_data: CreateTenantDto) -> PydanticObjectId | None
⋮----
"""Create a new tenant. Raises InvalidOperationException if admin password is weak."""
⋮----
response = await self.tenant_repository.create(tenant_data)
⋮----
async def delete_tenant(self, tenant_id: str) -> None
⋮----
"""Delete tenant by ID. Raises TenantNotFoundException if not found."""
⋮----
async def total_count(self) -> int
⋮----
"""Get total count of tenants. Returns an integer."""
⋮----
async def update_feature(self, tenant_id: str, feature: FeatureDto)
⋮----
"""
            Update a feature for a tenant. If the feature does not exist, it will be added to the tenant's features.
            Raises TenantNotFoundException if tenant not found.
        """
tenant = await self.get_tenant_by_id(tenant_id)
feature_found = False
⋮----
feature_found = True
⋮----
# Save the updated tenant..
⋮----
async def update_tenant(self, tenant_id: str, data: UpdateTenantDto) -> None
⋮----
"""Update tenant details. Raises TenantNotFoundException if tenant not found."""
⋮----
async def get_features_by_tenant_id(self, tenant_id: str) -> List[FeatureDto]
⋮----
"""
            Get features for a tenant by ID.
            Raises
                TenantNotFoundException if tenant not found.
        """
⋮----
all_features = [FeatureDto(name=name, enabled=False) for name in FeatureEnum]
⋮----
tenant_features = [FeatureDto(name=feature.name, enabled=feature.enabled) for feature in tenant.features]
⋮----
# Check if any features are added in the enum that are not present in tenant features
⋮----
available_features: List[FeatureDto] = []
⋮----
# Feature exists in tenant, add it as is
⋮----
# Feature not found in tenant, add it as enabled=False by default
⋮----
# Return tenant features if all features are already set
```

## File: backend/api/usecases/user_preference_service.py

```python
logger = get_logger(__name__)
class UserPreferenceService
⋮----
def __init__(self, user_preference_repository: UserPreferenceRepository)
async def get_preferences(self, user_id: str) -> UserPreference | None
⋮----
user_pref = await self.user_preference_repository.get_preferences(user_id=user_id)
⋮----
async def set_preferences(self, user_id: str, payload: dict[str, Any]) -> None
```

## File: backend/api/usecases/user_service.py

```python
logger = get_logger(__name__)
class UserService
⋮----
async def list_users(self, skip: int = 0, limit: int = 10) -> UserListDto
async def find_by_email(self, email: EmailStr) -> User
⋮----
"""Find user by email. Returns User model. Raises UserNotFoundException if not found."""
existing = await self.user_repository.single_or_none(email=email)
⋮----
async def get_user_by_id(self, user_id: str) -> User
⋮----
"""Get user by ID. Returns User model. Raises UserNotFoundException if not found."""
existing = await self.user_repository.get(id=user_id)
⋮----
async def create_user(self, user_data: CreateUserDto) -> PydanticObjectId
⋮----
"""Create a new user and returns its ID. Raises EmailAlreadyExistsException if email already exists."""
existing = await self.user_repository.single_or_none(email=user_data.email)
⋮----
user_id = await self.user_repository.create(user_data)
# Todo: Refactor this to use Celery task to Or fire and forget
# avoid circular import
# from api.core.container import get_auth_service
# auth_service = get_auth_service()
# welcome_email = UserResendActivationEmailRequestDto(
#     email=user_data.email,
#     first_name=user_data.first_name,
#     tenant_id=str(user_data.tenant_id),
#     id=str(user_id)
# )
# await auth_service.send_activation_email(welcome_email)
⋮----
async def update_user(self, user_id: str, user_data: UpdateUserDto) -> User | None
⋮----
"""Update user by ID. Returns updated User model. Raises UserNotFoundException if user does not exist."""
⋮----
# Todo: Refactor email update logic or check if email is being updated
# existing_email = user_data.email
# if existing_email and existing_email != existing.email:
#     email_in_use = await self.user_repository.single_or_none(email=existing_email)
#     if email_in_use is not None:
#         raise EmailAlreadyExistsException(existing_email)
#     existing.email = existing_email
⋮----
doc = await existing.to_serializable_dict()
⋮----
async def delete_user(self, user_id: str) -> None
⋮----
"""Delete user by ID. Returns None otherwise, Raises UserNotFoundException if user does not exist."""
⋮----
async def update_user_password(self, user_id: str, new_password: str) -> User | None
⋮----
"""Update user password by ID. Returns updated User model. Raises UserNotFoundException if user does not exist."""
⋮----
hashed_password = hash_it(new_password)
⋮----
async def total_count(self, params: Any | None = None) -> int
⋮----
"""Get total user count."""
⋮----
async def request_password_reset(self, email: EmailStr) -> UserPasswordReset
⋮----
"""Set password reset for user by ID. Returns None otherwise, Raises InvalidOperationException on failure."""
⋮----
user = await self.find_by_email(email=email)
⋮----
async def retrieve_password_reset_data_by_user_id(self, user_id: str) -> UserPasswordReset | None
⋮----
"""Get password reset entry by token. Returns UserPasswordReset model or None if not found."""
⋮----
async def clear_password_reset_data_by_user_id(self, user_id: str) -> bool
⋮----
"""Clear password reset entry by user ID. Returns boolean."""
⋮----
async def check_email_exists(self, email: EmailStr) -> bool
⋮----
"""Check if email exists. Returns boolean."""
⋮----
async def aggregate(self, pipeline: list[dict]) -> list[TimeSeriesDto]
⋮----
"""Aggregate users based on the provided pipeline. Returns an async list[TimeSeriesDto]."""
```

## File: backend/Dockerfile

```
# Multi-stage build for optimal size
FROM python:3.13-alpine

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /api

# First stage: sync dependencies *without* project code (fast layer cache)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

ADD . /api

# Second stage: install project into managed environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen


EXPOSE 8000

# Healthcheck optional
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/', timeout=5)" || exit 1

CMD ["uv", "run", "uvicorn", "api:app", "--workers", "5", "--host", "0.0.0.0", "--port", "8000"]
```

## File: backend/Dockerfile.celery

```
# Celery Worker Dockerfile
# Multi-stage build for optimal size
FROM python:3.13-alpine AS builder

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install build dependencies (only needed during build)
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    libffi-dev \
    openssl-dev \
    python3-dev \
    make \
    cmake \
    pkgconfig \
    rust \
    cargo \
    linux-headers

# Set work directory
WORKDIR /api

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Set environment variables for better package installation
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1

# Install dependencies using uv sync in a virtual environment
RUN uv sync --frozen --no-dev

# Production stage - minimal runtime image
FROM python:3.13-alpine AS runtime

# Install only runtime dependencies
RUN apk add --no-cache \
    libffi \
    openssl \
    ca-certificates && \
    rm -rf /var/cache/apk/*

# Create non-root user
RUN addgroup -g 1000 celeryuser && \
    adduser -u 1000 -G celeryuser -s /bin/sh -D celeryuser

# Copy virtual environment from builder
COPY --from=builder /api/.venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/api" \
    CELERY_BROKER_URL="redis://localhost:6372/0" \
    CELERY_RESULT_BACKEND="redis://localhost:6372/0"

# Set work directory
WORKDIR /api

# Copy only application code (exclude unnecessary files)
COPY --chown=celeryuser:celeryuser api/ ./api/
COPY --chown=celeryuser:celeryuser pyproject.toml ./

# Switch to non-root user
USER celeryuser

# Expose port for Flower (if running flower)
EXPOSE 5555

# Default command - can be overridden
CMD ["python", "-m", "celery", "-A", "api.infrastructure.messaging.celery_worker", "worker", "--loglevel=info"]
```

## File: backend/Makefile

```
worker:
	uv run celery -A api.infrastructure.messaging.celery_worker worker --loglevel=info
flower:
	uv run celery -A api.infrastructure.messaging.celery_worker flower
```

## File: backend/pyproject.toml

```toml
[project]
name = "backend"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "azure-storage-blob>=12.26.0",
    "beanie>=2.0.0",
    "boto3>=1.40.32",
    "celery[redis]>=5.3.1",
    "colorlog>=6.9.0",
    "faker>=37.8.0",
    "fastapi-mail>=1.5.0",
    "fastapi-sso>=0.18.0",
    "fastapi[standard]>=0.116.2",
    "flower>=2.0.1",
    "httpx>=0.28.1",
    "langchain>=0.3.27",
    "langchain-ollama>=0.3.8",
    "langchain-openai>=0.3.33",
    "motor>=3.7.1",
    "openpyxl>=3.1.5",
    "passlib>=1.7.4",
    "punq>=0.7.0",
    "pydantic>=2.11.9",
    "pyjwt>=2.10.1",
    "pymongo>=4.15.1",
    "pytest>=8.4.2",
    "pytest-asyncio>=1.2.0",
    "python-dotenv>=1.1.1",
    "redis>=6.4.0",
    "starlette>=0.48.0",
    "streamlit>=1.49.1",
    "stripe>=13.0.1",
    "uvicorn>=0.37.0",
    "webauthn>=2.7.0",
]
```

## File: backend/pytest.ini

```ini
[pytest]
minversion = 7.0
addopts = -ra -q
testpaths =
    tests
pythonpath = .
asyncio_mode = auto
```

## File: backend/README.md

````markdown
# Full-Stack FastAPI React Multi-Tenancy Template

A modern full-stack Multi-tenant application template featuring a FastAPI backend with Clean Architecture, React frontend with TypeScript, and comprehensive tooling for development and deployment.

## ✨ Key Features

This full-stack template provides enterprise-ready features out of the box:

### 🔐 User Management

- **Authentication & Authorization**: JWT-based authentication with refresh tokens
- **User Registration**: Self-registration with email verification
- **Profile Management**: Complete user profile with image upload support
- **Account Management**: User activation, deactivation, and role assignment
- **Password Management**: Secure password reset and update functionality
- **Passkeys Management**: Passkeys and management
- **Magic link**: Passwordless login via Magic link

### 🏢 Tenant Management

- **Multi-tenancy Support**: Isolated data and resources per tenant
- **Tenant Search**: Advanced search functionality by name and subdomain
- **Tenant Administration**: Create, update, and manage tenant configurations
- **Subdomain Routing**: Custom subdomain support for each tenant
- **Custom Domain Routing**: Bring your own domain

### 👥 Role Management

- **Role-Based Access Control (RBAC)**: Granular permission system
- **Dynamic Roles**: Create and manage custom roles with specific permissions
- **Role Assignment**: Assign roles to users within tenant contexts
- **Permission Matrix**: Fine-grained control over feature access

### ☁️ Cloud Storage Integration

- **Azure Blob Storage**: Native integration with Azure cloud storage
- **AWS S3 Compatible**: Full S3 API compatibility for file storage
- **File Upload Management**: Secure file upload with validation and processing
- **Storage Abstraction**: Seamless switching between storage providers

### 🤖 AI Chat Interface

- **Local AI Models**: Integration with locally installed AI models
- **Real-time Streaming**: Live response streaming for better user experience
- **Chat History**: Persistent conversation history and management
- **Model Selection**: Dynamic switching between different AI models
- **Responsive Design**: Mobile-optimized chat interface

### 💳 Stripe Payment Integration

- **Payment Gateway Configuration**: Easy Stripe API and webhook setup
- **Product Management**: Create and manage products with CRUD operations
- **Billing Plans**: Flexible plan creation with monthly/yearly intervals
- **Trial Periods**: Toggle trial periods for any plan (default 14 days)
- **Invoice Management**: Comprehensive invoice listing and tracking
- **Checkout Sessions**: Billing record management with pagination
- **Multi-Currency Support**: Accept payments in multiple currencies
- **Subscription Management**: Handle one-time and recurring payments

### 🎛️ Feature Management

- **Feature Toggles**: Enable/disable features per tenant
- **Dynamic Feature Control**: Real-time feature activation/deactivation
- **Visual Feature Dashboard**: Intuitive interface for feature management (Comming soon!)
- **Granular Permissions**: Control feature access at tenant level

### 🏠 Tenant Experience

- **Custom Branding**: Tenant-specific configurations and settings (Coming soon!)
- **Inactive Tenant View**: Professional inactive account messaging
- **DNS Configuration**: Step-by-step custom domain setup
- **Tenant Settings**: Comprehensive tenant information management

### Coolify Integration

- **Domain Mapping**: Assign domain easily with Coolify app
- **Deployment**: Auto deploy when new domain added.
- **Toggle on/off**: Via env

## Watch Demo

![DEMO](/demo.gif)
![Multi-tenancy DNS Demo](/dns.gif)
[Check out deployed DEMO Version](https://demo.fsrapp.xyz/)

### Login as Host

Use the following credentials to log in as the **host administrator**:

| Username         | Password  |
| ---------------- | --------- |
| admin@fsrapp.xyz | Test@123! |

---

### Login as Tenant

To log in as a **tenant admin**:

1. On the login page, search a **dentally** tenant from the search field.
2. Then enter the credentials below:

| Username          | Password      |
| ----------------- | ------------- |
| admin@dentally.nl | Dentally@123! |

### Login as a Tenant User

1. First, create a new user while logged in as the **Tenant Admin**.
2. After creating the user, **log out** from the admin account.
3. Finally, **log in** again using the newly created user's credentials.

## 🏗️ Architecture Overview

This project follows a **Clean Architecture** pattern with clear separation between backend and frontend, designed for scalability, maintainability, and testability.

### Technology Stack

**Backend:**

- **FastAPI** - Modern Python web framework
- **MongoDB** - NoSQL database with Beanie ODM
- **Redis** - Caching and message broker
- **Celery** - Asynchronous task processing
- **JWT** - Authentication and authorization
- **LangChain** - AI/ML integration capabilities

**Frontend:**

- **React 18** - Modern UI library
- **TypeScript** - Type-safe JavaScript
- **Vite** - Fast build tool
- **Tailwind CSS** - Utility-first CSS framework
- **shadcn/ui** - High-quality React components
- **React Hook Form** - Form handling with validation

**Infrastructure:**

- **Docker** - Containerization
- **Docker Compose** - Development environment orchestration
- **Helm** - Kubernetes deployment charts

## 📁 Project Structure

```
full_stack_fastapi_react_template/
├── backend/                    # FastAPI backend application
│   ├── api/                   # Clean Architecture layers
│   │   ├── common/           # Shared utilities and constants
│   │   ├── core/             # Core business logic
│   │   ├── domain/           # Domain models and entities
│   │   ├── infrastructure/   # External services integration
│   │   ├── interfaces/       # API controllers and routes
│   │   └── usecases/         # Application use cases
│   ├── tests/                # Test suite
│   ├── .env.example          # Environment variables template
│   ├── Dockerfile            # Container configuration
│   ├── Makefile              # Development commands
│   ├── pyproject.toml        # Python dependencies and config
│   └── requirements.txt      # Python dependencies (fallback)
│
├── frontend/                  # React frontend application
│   └── app/                  # Main application code
│       ├── src/
│       │   ├── api/          # Auto-generated API client
│       │   ├── components/   # React components
│       │   │   ├── features/ # Feature-specific components
│       │   │   ├── layouts/  # Layout components
│       │   │   ├── providers/# Context providers
│       │   │   ├── shared/   # Reusable components
│       │   │   └── ui/       # shadcn/ui components
│       │   ├── hooks/        # Custom React hooks
│       │   ├── lib/          # Utility functions
│       │   └── assets/       # Static assets
│       ├── public/           # Public static files
│       ├── package.json      # Node.js dependencies
│       ├── vite.config.ts    # Vite configuration
│       └── components.json   # shadcn/ui configuration
│
├── infra/                     # Infrastructure and deployment
│   └── helm/                 # Kubernetes Helm charts
│       ├── templates/        # Kubernetes manifests
│       ├── Chart.yaml        # Helm chart metadata
│       └── values.yaml       # Default configuration values
│
└── dev.compose.yaml          # Docker Compose for development
```

## 🔧 Backend Architecture

The backend follows **Clean Architecture** principles with clear layer separation:

### Layer Structure

- **Domain Layer** (`domain/`): Core business entities and rules
- **Use Cases Layer** (`usecases/`): Application-specific business logic
- **Interface Adapters** (`interfaces/`): API controllers, presenters
- **Infrastructure Layer** (`infrastructure/`): External services, databases, message queues
- **Common Layer** (`common/`): Shared utilities, constants, and configurations

### Key Features

- **Authentication & Authorization**: JWT-based with refresh tokens, Passkey and Magic Link
- **Database**: MongoDB with Beanie ODM for async operations
- **Caching**: Redis for performance optimization
- **Background Tasks**: Celery with Redis broker
- **Email**: SMTP integration with async mail sending
- **AI Integration**: LangChain for AI/ML capabilities
- **Stripe Integration**: Complete payment gateway with products, plans, and subscriptions
- **API Documentation**: Auto-generated OpenAPI/Swagger docs
- **Testing**: Comprehensive test suite with pytest

### Dependencies Management

- **uv**: Fast Python package installer and resolver
- **pyproject.toml**: Modern Python dependency specification
- **Virtual Environment**: Isolated Python environment in `.venv/`

## 🎨 Frontend Architecture

The frontend is built with modern React patterns and TypeScript for type safety:

### Component Organization

- **Features** (`components/features/`): Business logic components
  - **Auth**: Login, registration, password reset, passkey, and magic link authentication
  - **Profile**: User profile management with image upload
  - **Chat**: AI chat interface with streaming responses
  - **Tenant**: Tenant management, settings, and feature toggles
  - **Billings/Stripe**: Complete payment gateway integration
    - Stripe configuration and settings
    - Product management (CRUD operations)
    - Billing plan creation and management
    - Invoice listing and tracking
    - Checkout session overview
    - Trial period management
- **Layouts** (`components/layouts/`): Page layout components (dashboard, default)
- **Providers** (`components/providers/`): React context providers for global state
- **Shared** (`components/shared/`): Reusable components across features
- **UI** (`components/ui/`): Low-level shadcn/ui components

### Key Features

- **Type Safety**: Full TypeScript integration
- **Component Library**: shadcn/ui for consistent design system
- **Form Handling**: React Hook Form with Zod validation
- **Routing**: React Router for client-side navigation
- **State Management**: React Context for global state
- **Styling**: Tailwind CSS with responsive design
- **API Integration**: Auto-generated client from OpenAPI specs
- **Payment Integration**: Complete Stripe payment gateway components
- **Feature Management**: Dynamic feature toggle interface
- **Professional UI**: Modern card-based layouts with proper loading states

### Build System

- **Vite**: Fast development server and build tool
- **ESLint**: Code linting and formatting
- **TypeScript**: Compile-time type checking
- **pnpm**: Efficient package management

## 🐳 Development Environment

### Stripe Payment Integration Setup

To use the Stripe payment features, you need to configure your Stripe account:

1. **Get Stripe API Keys**:

   - Sign up at [Stripe Dashboard](https://dashboard.stripe.com)
   - Get your API keys from Developers → API keys
   - Get your webhook secret from Developers → Webhooks

2. **Configure Stripe in Application**:

   - Navigate to Stripe Configuration page in the app
   - Enter your Stripe Secret Key
   - Enter your Webhook Secret
   - Set default currency (e.g., USD, EUR)
   - Choose payment mode (one-time, recurring, or both)
   - Set trial period days (optional)

3. **Create Products and Plans**:
   - Navigate to Products page to create your offerings
   - Use "Add a Plan" to create pricing plans
   - Toggle trial periods for any plan
   - Monitor billing through the Billing Overview dashboard

### Prerequisites

- Python 3.13+
- Node.js 22+
- Docker & Docker Compose
- pnpm (recommended) or npm
- Git

### Getting Started

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/sajanv88/full_stack_fastapi_react_template.git
   cd full_stack_fastapi_react_template
   ```

### Environment Setup

1. **Backend Environment**:

   ```bash
   cp backend/.env.example backend/.env
   ```

   Edit `backend/.env` with your configuration:

   ```bash
        # MongoDB settings
        MONGO_URI=mongodb://127.0.0.1:27012
        MONGO_DB_NAME=full_stack_fastapi_react_template

        # JWT settings
        JWT_SECRET=your_jwt_secret_key
        REFRESH_TOKEN_SECRET=your_refresh_jwt_secret_key

        # Configure your default app admin credentials.
        ADMIN_EMAIL=admin@example.com
        ADMIN_PASSWORD=Test@123!

        # SMTP Email settings
        SMTP_HOST="localhost"
        SMTP_PORT=1023
        SMTP_USER=""
        SMTP_PASSWORD=""
        SMTP_MAIL_FROM=noreply@example.com
        SMTP_MAIL_FROM_NAME="Full-Stack Fast API"
        SMTP_STARTTLS=False
        SMTP_SSL_TLS=False
        SMTP_USE_CREDENTIALS=False
        SMTP_VALIDATE_CERTS=False

        # Redis settings

        REDIS_URI="redis://localhost:6372/0"

        # Celery settings
        CELERY_BROKER_URL="redis://localhost:6372/0"
        CELERY_RESULT_BACKEND="redis://localhost:6372/0"

        # App configuration
        APP_TITLE="Full-Stack FastAPI React Template"
        APP_VERSION="0.1.0"

        API_ENDPOINT_BASE="http://localhost:8000/api"

        # Configure Multi-Tenancy
        MULTI_TENANCY_STRATEGY="header"

        # Host main domain name
        HOST_MAIN_DOMAIN="fsrapp.com"

        # Environment
        FASTAPI_ENV="development"  # Options: "development", "production"

        # Ollama settings
        OLLAMA_HOST="http://localhost:11434"

        # Coolify settings for deployment
         COOLIFY_ENABLED="false" # Set to "true" to enable Coolify integration
         COOLIFY_API_URL="https://{replace_with_your_coolify_instance_endpoint}/api/v1"
         COOLIFY_API_KEY="{replace_with_your_coolify_api_key}" # Read here https://coolify.io/docs/api-reference/authorization
         COOLIFY_APPLICATION_ID="{replace_with_your_coolify_application_id}" # Read here https://coolify.io/docs/api-reference/api/operations/get-application-by-uuid


         # Default aws s3 settings for file uploads this belongs to the Host
         AWS_REGION="eu-central-1"
         AWS_ACCESS_KEY_ID="Ab..."
         AWS_SECRET_ACCESS_KEY="xxe..."
         AWS_S3_BUCKET_NAME="fs.."

         # Stripe credentials settings
         STRIPE_API_KEY="rk_test_5..."
         STRIPE_PUBLISHABLE_KEY="pk_test_5..."
         STRIPE_SECRET_KEY="sk_test_$..."

   ```

2. **Start Infrastructure Services**:

   ```bash
   docker-compose -f dev.compose.yaml up -d
   ```

   This starts:

   - MongoDB (port 27012)
   - Redis (port 6372)
   - Fake SMTP Server (ports 1023, 1083)
   - Caddy proxy for local development ssl. Useful for testing passkey login in local development.

3. **Backend Development**:

   ```bash
   cd backend
   uv sync                      # Install dependencies
   uv run fastapi dev api        # Start development server
   ```

4. **Frontend Development**:
   ```bash
   cd frontend/app
   pnpm install              # Install dependencies
   pnpm dev                  # Start development server
   ```

### Available Services

- **Backend API**: http://localhost:8000
- **Frontend App**: http://localhost:5173
- **API Documentation**: http://localhost:8000/docs
- **MongoDB**: localhost:27012
- **Redis**: localhost:6372
- **Fake SMTP UI**: http://localhost:1083

### Available Features in UI

Once you have the application running, you can access the following features:

**Authentication & User Management:**

- Login with email/password
- Passkey authentication (WebAuthn)
- Magic link authentication
- User registration and profile management
- Password reset functionality

**Tenant Management:**

- Tenant settings and configuration
- Custom domain DNS setup
- Feature toggle management
- Inactive tenant view with support information

**Stripe Billing & Payments:**

- Stripe configuration interface
- Product management (create, edit, delete)
- Billing plan creation with trial periods
- Billing overview dashboard
- Invoice listing and management
- Checkout session tracking

**AI Chat:**

- Real-time AI chat interface
- Chat history management
- Model selection

**Dashboard:**

- Overview of key metrics
- Quick access to all features
- Responsive design for mobile and desktop

## 🔄 Integration & Workflow

### API Client Generation

The frontend automatically generates TypeScript API clients from the backend's OpenAPI specification:

```bash
cd frontend/app
pnpm run generate:api
```

This creates type-safe API clients in `src/api/` directory.

### Development Workflow

1. **Backend First**: Define API endpoints and models
2. **Generate Client**: Update frontend API client
3. **Frontend Integration**: Use generated clients in React components
4. **Type Safety**: TypeScript ensures end-to-end type safety

### Background Tasks

Start Celery worker for background processing:

```bash
cd backend
make worker          # Start Celery worker (It is required for post tenant creation process)
make flower          # Start Flower monitoring UI
```

## 🚀 Deployment

### Docker Deployment

Each service includes production-ready Dockerfiles:

- **Backend**: Multi-stage build with Python optimization
- **Frontend**: Static build served by backend.

### Kubernetes Deployment

Helm charts provided in `infra/helm/` for Kubernetes deployment:

```bash
cd infra/helm
helm install my-app . -f values.yaml
```

## 🔧 Configuration

### Backend Configuration

Environment variables in `backend/.env`:

- Database connections (MongoDB, Redis)
- JWT secrets and security settings
- SMTP email configuration
- Admin user credentials
- Feature flags and API settings

### Frontend Configuration

Configuration in `frontend/app/`:

- `vite.config.ts`: Build and development settings
- `components.json`: shadcn/ui component configuration
- `tsconfig.json`: TypeScript compiler options

## 🧪 Testing

### Backend Testing

```bash
cd backend
uv run pytest                # Run all tests
uv run pytest tests/interfaces/  # Run API tests
```

### Frontend Testing

```bash
cd frontend/app
pnpm test                    # Run component tests
pnpm build                   # Test production build
```

## 📚 Additional Resources

- **Backend API Docs**: Visit `/docs` endpoint when running
- **Component Storybook**: shadcn/ui component documentation
- **Architecture Decisions**: See individual README files in `backend/` and `frontend/app/`

## 🤝 Contributing

1. Follow the established folder structure
2. Maintain Clean Architecture principles in backend
3. Use TypeScript for all frontend code
4. Add tests for new features
5. Update API client after backend changes

---

This template provides a solid foundation for building modern, scalable full-stack applications with clear separation of concerns and comprehensive tooling.
````

## File: backend/tests/conftest.py

```python
# tests/conftest.py
⋮----
TEST_MONGO_URI = "mongodb://localhost:27012/test_db"
⋮----
@pytest.fixture
async def test_app()
⋮----
# Initialize test database per test function (same loop as the test)
db = Database(uri=TEST_MONGO_URI, models=[User, Tenant, Role, UserPasswordReset, UserPreference])
⋮----
# Override the get_current_user dependency to return a mocked test user
async def override_get_current_user()
⋮----
@pytest.fixture
async def client(test_app)
```

## File: backend/tests/interfaces/test_role_endpoint.py

```python
async def create_role(client: AsyncClient) -> CreateRoleResponseDto
⋮----
new_role = CreateRoleDto(
response = await client.post("/roles/", json=new_role.model_dump())
⋮----
@pytest.mark.asyncio
async def test_list_roles_from_host(client: AsyncClient)
⋮----
"""
        List all the roles:
    """
response = await client.get("/roles/?skip=0&limit=10")
⋮----
data = RoleListDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_create_role_in_host(client: AsyncClient)
⋮----
"""
        Create a new role:
    """
new_role_response = await create_role(client)
# Verify the role was created by listing roles again
⋮----
@pytest.mark.asyncio
async def test_get_role_by_id_from_host(client: AsyncClient)
⋮----
"""
        Get a role by ID:
    """
⋮----
response = await client.get(f"/roles/{new_role_response.id}")
⋮----
data = RoleDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_update_role_by_id_from_host(client: AsyncClient)
⋮----
"""
        Update a role by ID:
    """
⋮----
# Update the role's name
updated_data = UpdateRoleDto(name="Test Role Updated")
response = await client.put(f"/roles/{new_role_response.id}", json=updated_data.model_dump())
⋮----
new_role_updated_data = RoleDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_delete_role_by_id_from_host(client: AsyncClient)
⋮----
"""
        Delete a role by ID:
    """
⋮----
response = await client.delete(f"/roles/{new_role_response.id}")
⋮----
# Verify the role was deleted by attempting to get the role again
⋮----
@pytest.mark.asyncio
async def test_get_nonexistent_role_by_id_from_host(client: AsyncClient)
⋮----
"""
        Attempt to get a non-existent role by ID:
    """
response = await client.get("/roles/68c3031f449ed3590028c778")
⋮----
@pytest.mark.asyncio
async def test_list_of_roles_paginated_in_host(client: AsyncClient)
⋮----
"""
        List roles with pagination:
    """
⋮----
# Create a single user and return the response
# Verify the user was created by listing users again
response = await client.get("/roles/?skip=0&limit=1")
⋮----
response = await client.get("/roles/?skip=1&limit=1")
⋮----
response = await client.get("/roles/?skip=2&limit=1")
⋮----
@pytest.mark.asyncio
async def test_create_role_with_default_permissions_in_host(client: AsyncClient)
⋮----
"""
        Create a new role with default permissions:
    """
```

## File: backend/tests/interfaces/test_tenant_endpoint.py

```python
# ------------------------
# Fixture to mock PostTenantCreationTaskService._init_and_run
⋮----
@pytest.fixture
def mock_post_tenant_service()
⋮----
# Mock Database for tenant tests
⋮----
@pytest.fixture
def mock_db()
async def create_tenant(client: AsyncClient, mock_post_tenant_service) -> CreateTenantResponseDto
⋮----
new_tenant = CreateTenantDto(
response = await client.post("/tenants/", json=new_tenant.model_dump())
⋮----
@pytest.mark.asyncio
async def test_list_tenants_from_host(client: AsyncClient)
⋮----
"""
        List all the tenants:
    """
response = await client.get("/tenants/?skip=0&limit=10")
⋮----
data = TenantListDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_create_tenant_as_host(client: AsyncClient, mock_post_tenant_service, mock_db)
⋮----
"""
        Create a new tenant:
    """
new_tenant_response = await create_tenant(client, mock_post_tenant_service)
# Verify the user was created by listing users again
⋮----
@pytest.mark.asyncio
async def test_search_tenant_by_subdomain_as_host(client: AsyncClient, mock_post_tenant_service, mock_db)
⋮----
"""
        Get a tenant by subdomain:
    """
⋮----
response = await client.get(f"/tenants/search_by_subdomain/test.fsrapp.com")
⋮----
data = TenantDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_delete_tenant_by_id_from_host(client: AsyncClient, mock_post_tenant_service, mock_db)
⋮----
"""
        Delete a tenant by ID:
    """
⋮----
response = await client.delete(f"/tenants/{new_tenant_response.id}")
⋮----
@pytest.mark.asyncio
async def test_get_nonexistent_tenant_by_name_as_host(client: AsyncClient,  mock_db)
⋮----
"""
        Attempt to get a non-existent tenant by name:
    """
response = await client.get("/tenants/search_by_name/nonexistent-tenant")
⋮----
@pytest.mark.asyncio
async def test_list_of_tenants_paginated_in_host(client: AsyncClient, mock_post_tenant_service, mock_db)
⋮----
"""
        List tenants with pagination:
    """
⋮----
# Create a single user and return the response
⋮----
response = await client.get("/tenants/?skip=0&limit=1")
⋮----
response = await client.get("/tenants/?skip=1&limit=1")
⋮----
response = await client.get("/tenants/?skip=2&limit=1")
⋮----
@pytest.mark.asyncio
async def test_validate_subdomain_when_tenant_creation(client: AsyncClient, mock_db)
⋮----
"""
        Validate subdomain when creating a tenant:
    """
⋮----
subdomain="test-.fsrapp.com",  # invalid
⋮----
@pytest.mark.asyncio
async def test_validate_password_when_tenant_creation(client: AsyncClient, mock_db)
⋮----
"""
        Validate password when creating a tenant:
    """
⋮----
subdomain="test.fsrapp.com",  # invalid
```

## File: backend/tests/interfaces/test_user_endpoint.py

```python
async def create_user(client: AsyncClient) -> CreateUserResponseDto
⋮----
new_user = CreateUserDto(
response = await client.post("/users/", json=new_user.model_dump())
⋮----
@pytest.mark.asyncio
async def test_list_users_from_host(client: AsyncClient)
⋮----
"""
        List all the users:
    """
response = await client.get("/users/?skip=0&limit=10")
⋮----
data = UserListDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_create_user_in_host(client: AsyncClient)
⋮----
"""
        Create a new user:
    """
new_user_response = await create_user(client)
# Verify the user was created by listing users again
⋮----
@pytest.mark.asyncio
async def test_get_user_by_id_from_host(client: AsyncClient)
⋮----
"""
        Get a user by ID:
    """
⋮----
response = await client.get(f"/users/{new_user_response.id}")
⋮----
data = UserDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_update_user_by_id_from_host(client: AsyncClient)
⋮----
"""
        Update a user by ID:
    """
⋮----
# Update the user's email
⋮----
updated_data = UpdateUserDto(**data.model_dump())
response = await client.put(f"/users/{new_user_response.id}", json=updated_data.model_dump())
⋮----
new_user_updated_data = UserDto.model_validate(response.json())
⋮----
@pytest.mark.asyncio
async def test_delete_user_by_id_from_host(client: AsyncClient)
⋮----
"""
        Delete a user by ID:
    """
⋮----
response = await client.delete(f"/users/{new_user_response.id}")
⋮----
# Verify the user was deleted by attempting to get the user again
⋮----
@pytest.mark.asyncio
async def test_get_nonexistent_user_by_id_from_host(client: AsyncClient)
⋮----
"""
        Attempt to get a non-existent user by ID:
    """
response = await client.get("/users/68c3031f449ed3590028c778")
⋮----
@pytest.mark.asyncio
async def test_list_of_users_paginated_in_host(client: AsyncClient)
⋮----
"""
        List users with pagination:
    """
⋮----
# Create a single user and return the response
⋮----
response = await client.get("/users/?skip=0&limit=1")
⋮----
response = await client.get("/users/?skip=1&limit=1")
⋮----
response = await client.get("/users/?skip=2&limit=1")
```

## File: caddy/Caddyfile

```
{
	# Optional, to enable Caddy’s local CA
	local_certs
	email admin@example.com
}

demo.dev.xyz {
	reverse_proxy host.docker.internal:8000
}



netflix.demo.dev.xyz {
	reverse_proxy host.docker.internal:3000
}

fsrapp.netflix.com {
	reverse_proxy host.docker.internal:3000

}
```

## File: CONTRIBUTE.md

````markdown
# Contributing to Full-Stack FastAPI React Multi-Tenancy Template

Thank you for your interest in contributing to this project! We welcome contributions from the community and are pleased to have you here.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Submitting Changes](#submitting-changes)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment. We expect all contributors to:

- Be respectful and constructive in discussions
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members

## Getting Started

### Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.13+** - Backend runtime
- **Node.js 18+** - Frontend runtime
- **pnpm** - Frontend package manager
- **uv** - Python package manager (recommended)
- **Docker & Docker Compose** - For running dependencies
- **Git** - Version control

### First-Time Setup

1. **Fork the Repository**

   ```bash
   # Fork the repo on GitHub, then clone your fork
   git clone https://github.com/YOUR_USERNAME/full_stack_fastapi_react_template.git
   cd full_stack_fastapi_react_template
   ```

2. **Add Upstream Remote**
   ```bash
   git remote add upstream https://github.com/sajanv88/full_stack_fastapi_react_template.git
   git fetch upstream
   ```

## Development Setup

### Backend Setup

1. **Navigate to Backend Directory**

   ```bash
   cd backend
   ```

2. **Set Up Python Environment**

   ```bash
   # Using uv (recommended)
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate

   # Install dependencies
   uv pip install -e .
   ```

3. **Configure Environment Variables**

   ```bash
   # Copy example environment file
   cp .env.example .env

   # Edit .env with your configuration
   # Update MongoDB URI, Redis URI, SMTP settings, etc.
   ```

4. **Start Infrastructure Services**

   ```bash
   # From the root directory
   docker-compose -f dev.compose.yaml up -d
   ```

   This starts:

   - MongoDB (port 27012)
   - Redis (port 6372)
   - Fake SMTP Server (ports 1023, 1083)
   - Caddy (ports 80, 443)

5. **Run the Backend Server**

   ```bash
   # From backend directory
   uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. **Run Background Workers (Optional)**

   ```bash
   # Celery worker
   make worker

   # Flower (monitoring)
   make flower
   ```

### Frontend Setup

1. **Navigate to Frontend Directory**

   ```bash
   cd frontend/app
   ```

2. **Install Dependencies**

   ```bash
   pnpm install
   ```

3. **Start Development Server**

   ```bash
   pnpm dev
   ```

4. **Generate API Client (if backend changes)**
   ```bash
   # Make sure backend is running first
   pnpm generate:api
   ```

## Project Structure

### Backend Architecture (Clean Architecture)

```
backend/
├── api/
│   ├── common/           # Shared utilities and base classes
│   │   ├── base_repository.py
│   │   ├── security.py
│   │   └── utils.py
│   ├── core/             # Application core (config, DI)
│   │   ├── config.py
│   │   └── container.py
│   ├── domain/           # Business logic layer
│   │   ├── entities/     # Domain models
│   │   ├── dtos/         # Data transfer objects
│   │   └── interfaces/   # Repository interfaces
│   ├── infrastructure/   # External integrations
│   │   ├── persistence/  # Database implementations
│   │   ├── messaging/    # Celery, Redis
│   │   ├── externals/    # Third-party services
│   │   └── security/     # Auth implementations
│   ├── interfaces/       # API layer
│   │   ├── api_controllers/  # FastAPI endpoints
│   │   ├── middlewares/      # Request/response middleware
│   │   └── email_templates/  # Email templates
│   └── usecases/         # Application services
└── tests/                # Test suite
```

### Frontend Architecture

```
frontend/app/src/
├── api/              # Generated API client
├── components/       # Reusable React components
├── hooks/            # Custom React hooks
├── lib/              # Utility functions
└── assets/           # Static assets
```

## Development Workflow

### Creating a New Feature

1. **Create a Feature Branch**

   ```bash
   git checkout -y main
   git pull upstream main
   git checkout -b feature/your-feature-name
   ```

2. **Implement Your Changes**

   - Follow the project's architecture patterns
   - Write tests for new functionality
   - Update documentation as needed

3. **Keep Your Branch Updated**

   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

4. **Test Your Changes**

   ```bash
   # Backend tests
   cd backend
   pytest

   # Frontend tests (if applicable)
   cd frontend/app
   pnpm test
   ```

### Backend Development Guidelines

#### Adding a New Feature

1. **Define Domain Entity** (if needed)

   - Create in `api/domain/entities/`
   - Use Beanie models for MongoDB documents

2. **Create DTOs**

   - Add to `api/domain/dtos/`
   - Use Pydantic models for validation

3. **Define Repository Interface**

   - Add to `api/domain/interfaces/`
   - Follow existing patterns

4. **Implement Repository**

   - Create in `api/infrastructure/persistence/`
   - Inherit from `BaseRepository`

5. **Create Use Case Service**

   - Add to `api/usecases/`
   - Implement business logic

6. **Add API Controller**

   - Create in `api/interfaces/api_controllers/`
   - Use FastAPI router pattern

7. **Register Dependencies**

   - Update `api/core/container.py`
   - Use Punq for dependency injection

8. **Write Tests**
   - Add to `tests/` with appropriate structure
   - Test endpoints, services, and repositories

#### Example: Adding a New Endpoint

```python
# 1. Domain Entity (api/domain/entities/example.py)
from beanie import Document

class Example(Document):
    name: str
    description: str

    class Settings:
        name = "examples"

# 2. DTO (api/domain/dtos/example_dto.py)
from pydantic import BaseModel

class ExampleCreateDTO(BaseModel):
    name: str
    description: str

# 3. Repository Interface (api/domain/interfaces/example_repository.py)
from abc import ABC, abstractmethod

class IExampleRepository(ABC):
    @abstractmethod
    async def create(self, data: ExampleCreateDTO) -> Example:
        pass

# 4. Repository Implementation (api/infrastructure/persistence/example_repository.py)
from api.common.base_repository import BaseRepository

class ExampleRepository(BaseRepository[Example], IExampleRepository):
    async def create(self, data: ExampleCreateDTO) -> Example:
        example = Example(**data.model_dump())
        return await example.insert()

# 5. Use Case Service (api/usecases/example_service.py)
class ExampleService:
    def __init__(self, repository: IExampleRepository):
        self._repository = repository

    async def create_example(self, data: ExampleCreateDTO) -> Example:
        return await self._repository.create(data)

# 6. API Controller (api/interfaces/api_controllers/example_controller.py)
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/examples", tags=["Examples"])

@router.post("/", response_model=Example)
async def create_example(
    data: ExampleCreateDTO,
    service: ExampleService = Depends()
):
    return await service.create_example(data)
```

### Frontend Development Guidelines

1. **Component Development**

   - Use TypeScript for all components
   - Follow React best practices and hooks patterns
   - Utilize shadcn/ui components when possible

2. **API Integration**

   - Use the generated API client from `src/api/`
   - Regenerate client after backend changes: `pnpm generate:api`

3. **Styling**

   - Use Tailwind CSS for styling
   - Follow existing design patterns
   - Ensure responsive design (mobile-first)

4. **State Management**
   - Use React hooks for local state
   - Consider context for shared state

## Coding Standards

### Python (Backend)

- **Style**: Follow PEP 8
- **Type Hints**: Use type hints for all function signatures
- **Async**: Use async/await for I/O operations
- **Dependency Injection**: Use Punq container
- **Error Handling**: Use custom exceptions from `api.core.exceptions`
- **Logging**: Use structured logging from `api.common.logging`

```python
# Good example
async def get_user_by_id(self, user_id: str) -> User | None:
    """Retrieve a user by their ID.

    Args:
        user_id: The unique identifier of the user

    Returns:
        User object if found, None otherwise

    Raises:
        NotFoundException: If user does not exist
    """
    user = await User.get(user_id)
    if not user:
        raise NotFoundException(f"User {user_id} not found")
    return user
```

### TypeScript (Frontend)

- **Style**: Follow project ESLint configuration
- **Type Safety**: Avoid `any` types, use proper TypeScript types
- **Components**: Use functional components with hooks
- **Props**: Define explicit prop interfaces

```typescript
// Good example
interface UserCardProps {
  userId: string;
  onEdit?: (id: string) => void;
}

export const UserCard: React.FC<UserCardProps> = ({ userId, onEdit }) => {
  // Component implementation
};
```

### General Guidelines

- Write self-documenting code with clear variable names
- Add comments for complex logic
- Keep functions small and focused (single responsibility)
- Avoid deep nesting (max 3-4 levels)
- Use meaningful commit messages (see below)

## Testing Guidelines

### Backend Testing

1. **Test Structure**

   ```bash
   tests/
   ├── conftest.py           # Shared fixtures
   └── interfaces/           # API endpoint tests
       └── test_*.py
   ```

2. **Writing Tests**

   ```python
   import pytest
   from httpx import AsyncClient

   @pytest.mark.asyncio
   async def test_create_user(client: AsyncClient):
       response = await client.post(
           "/api/users",
           json={"email": "test@example.com", "password": "Test@123"}
       )
       assert response.status_code == 201
       assert "id" in response.json()
   ```

3. **Run Tests**

   ```bash
   # Run all tests
   pytest

   # Run specific test file
   pytest tests/interfaces/test_user_endpoint.py

   # Run with coverage
   pytest --cov=api
   ```

### Frontend Testing

- Write unit tests for utility functions
- Write integration tests for complex components
- Test user interactions and edge cases

## Submitting Changes

### Commit Message Format

Follow conventional commits specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

**Examples:**

```
feat(auth): add magic link authentication

Implement passwordless login via email magic links
- Add magic link generation endpoint
- Create email template for magic links
- Add token verification logic

Closes #123
```

```
fix(tenant): resolve subdomain routing issue

Fixed bug where subdomain routing was not working for
tenants with hyphenated names.

Fixes #456
```

### Pull Request Process

1. **Ensure Your Code is Ready**

   - All tests pass
   - Code follows style guidelines
   - Documentation is updated
   - Commits are clean and well-formatted

2. **Create Pull Request**

   - Push your branch to your fork
   - Open a PR against the `main` branch
   - Fill out the PR template completely

3. **PR Title Format**

   ```
   [Type] Brief description

   Example: [Feature] Add magic link authentication
   Example: [Fix] Resolve subdomain routing issue
   ```

4. **PR Description Should Include**

   - Summary of changes
   - Motivation and context
   - Type of change (feature, bugfix, etc.)
   - How to test the changes
   - Screenshots (for UI changes)
   - Related issues

5. **Review Process**
   - Address review comments promptly
   - Keep discussions focused and respectful
   - Make requested changes in new commits
   - Once approved, maintainers will merge

### What to Include in Your PR

✅ **Do Include:**

- Tests for new functionality
- Documentation updates
- Screenshots for UI changes
- Migration scripts (if database changes)
- Updated dependencies (if needed)

❌ **Don't Include:**

- Unrelated changes
- Commented-out code
- Debug statements or console.logs
- Personal configuration files
- Large binary files

## Reporting Issues

### Before Creating an Issue

1. **Search Existing Issues**

   - Check if the issue already exists
   - Comment on existing issues if you have additional information

2. **Verify the Bug**
   - Ensure it's reproducible
   - Test on the latest version
   - Check if it's environment-specific

### Creating a Good Issue

Use the following template:

```markdown
## Description

Clear description of the issue

## Steps to Reproduce

1. Go to '...'
2. Click on '...'
3. See error

## Expected Behavior

What should happen

## Actual Behavior

What actually happens

## Environment

- OS: [e.g., Ubuntu 22.04]
- Python version: [e.g., 3.13]
- Node version: [e.g., 20.x]
- Browser: [e.g., Chrome 120]

## Additional Context

Screenshots, error logs, etc.
```

### Issue Labels

- `bug`: Something isn't working
- `enhancement`: New feature or request
- `documentation`: Documentation improvements
- `good first issue`: Good for newcomers
- `help wanted`: Extra attention needed
- `question`: Further information requested

## Development Tips

### Debugging

**Backend:**

```python
# Use built-in debugger
import pdb; pdb.set_trace()

# Or use logging
from api.common.logging import logger
logger.debug("Debug message", extra={"data": data})
```

**Frontend:**

```typescript
// Use React DevTools
// Add debugger statement
debugger;

// Console logging
console.log("Debug:", data);
```

### Common Issues

**MongoDB Connection Issues:**

- Ensure Docker containers are running
- Check MongoDB port (27012) is not in use
- Verify `MONGO_URI` in `.env`

**Redis Connection Issues:**

- Ensure Redis container is running
- Check Redis port (6372) is not in use
- Verify `REDIS_URI` in `.env`

**CORS Issues:**

- Check backend CORS configuration
- Ensure frontend URL is in allowed origins

**API Client Out of Sync:**

- Regenerate API client: `pnpm generate:api`
- Ensure backend is running before generation

## Getting Help

- **Documentation**: Check the README.md for setup instructions
- **Issues**: Search existing issues or create a new one
- **Discussions**: Use GitHub Discussions for questions
- **Code Review**: Tag maintainers for review help

## Recognition

Contributors are recognized in:

- GitHub contributors page
- Release notes (for significant contributions)
- Project documentation

Thank you for contributing to this project! 🎉
````

## File: dev.compose.yaml

```yaml
version: "3.9"
services:
  mongodb:
    image: mongo:latest
    container_name: mongodatabase
    ports:
      - "27012:27017"
    volumes:
      - mongodb_data:/data/db
    # networks:
    #   - fastapi_network
  redis:
    image: redis:latest
    container_name: redis
    ports:
      - "6372:6379"
    # networks:
    #   - fastapi_network
  fake-smtp:
    image: haravich/fake-smtp-server
    container_name: fake_smtp
    ports:
      - "1023:1025"
      - "1083:1080"
  caddy:
    image: caddy:latest
    container_name: caddy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    restart: unless-stopped
    # networks:
    #   - fastapi_network
volumes:
  mongodb_data:
  redis_data:
  caddy_data:
  caddy_config:
# networks:
#   fastapi_network:
```

## File: frontend/app/.gitignore

```
# Logs
logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
lerna-debug.log*

node_modules
dist
dist-ssr
*.local

# Editor directories and files
.vscode/*
!.vscode/extensions.json
.idea
.DS_Store
*.suo
*.ntvs*
*.njsproj
*.sln
*.sw?
```

## File: frontend/app/components.json

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/index.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "iconLibrary": "lucide",
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  }
}
```

## File: frontend/app/eslint.config.js

```javascript
// Alternatively, use this for stricter rules
⋮----
// Optionally, add this for stylistic rules
```

## File: frontend/app/index.html

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Vite + React + TS</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

## File: frontend/app/package.json

```json
{
  "name": "app",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "generate:api": "openapi --input http://desktop.tail4fc230.ts.net:8086/openapi.json --output ./src/api --client fetch --name ApiClient --useOptions --useUnionTypes --exportCore true --exportServices true --exportModels true --exportSchemas false --indent 2 --postfixServices Service"
  },
  "dependencies": {
    "@hookform/resolvers": "^5.2.2",
    "@radix-ui/react-accordion": "^1.2.12",
    "@radix-ui/react-alert-dialog": "^1.1.15",
    "@radix-ui/react-aspect-ratio": "^1.1.8",
    "@radix-ui/react-avatar": "^1.1.11",
    "@radix-ui/react-checkbox": "^1.3.3",
    "@radix-ui/react-collapsible": "^1.1.12",
    "@radix-ui/react-context-menu": "^2.2.16",
    "@radix-ui/react-dialog": "^1.1.15",
    "@radix-ui/react-dropdown-menu": "^2.1.16",
    "@radix-ui/react-hover-card": "^1.1.15",
    "@radix-ui/react-label": "^2.1.8",
    "@radix-ui/react-menubar": "^1.1.16",
    "@radix-ui/react-navigation-menu": "^1.2.14",
    "@radix-ui/react-popover": "^1.1.15",
    "@radix-ui/react-progress": "^1.1.8",
    "@radix-ui/react-radio-group": "^1.3.8",
    "@radix-ui/react-scroll-area": "^1.2.10",
    "@radix-ui/react-select": "^2.2.6",
    "@radix-ui/react-separator": "^1.1.8",
    "@radix-ui/react-slider": "^1.3.6",
    "@radix-ui/react-slot": "^1.2.4",
    "@radix-ui/react-switch": "^1.2.6",
    "@radix-ui/react-tabs": "^1.1.13",
    "@radix-ui/react-toggle": "^1.1.10",
    "@radix-ui/react-toggle-group": "^1.1.11",
    "@radix-ui/react-tooltip": "^1.2.8",
    "@simplewebauthn/browser": "^13.2.2",
    "@tabler/icons-react": "^3.35.0",
    "@tailwindcss/vite": "^4.1.18",
    "@tanstack/react-table": "^8.21.3",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "cmdk": "^1.1.1",
    "date-fns": "^4.1.0",
    "embla-carousel-react": "^8.6.0",
    "input-otp": "^1.4.2",
    "lucide-react": "^0.562.0",
    "next-themes": "^0.4.6",
    "react": "^19.2.3",
    "react-day-picker": "^9.13.0",
    "react-dom": "^19.2.3",
    "react-hook-form": "^7.69.0",
    "react-resizable-panels": "^3.0.6",
    "react-router": "^7.11.0",
    "recharts": "^3.6.0",
    "sonner": "^2.0.7",
    "tailwind-merge": "^3.4.0",
    "tailwindcss": "^4.1.18",
    "vaul": "^1.1.2",
    "zod": "^4.2.1"
  },
  "devDependencies": {
    "@eslint/js": "^9.39.2",
    "@types/node": "^24.10.1",
    "@types/react": "^19.2.7",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^5.1.2",
    "eslint": "^9.39.2",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.4.26",
    "globals": "^16.5.0",
    "openapi-typescript-codegen": "^0.30.0",
    "tw-animate-css": "^1.4.0",
    "typescript-eslint": "^8.50.1",
    "vite": "^7.3.0"
  }
}
```

## File: frontend/app/public/vite.svg

```xml
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" role="img" class="iconify iconify--logos" width="31.88" height="32" preserveAspectRatio="xMidYMid meet" viewBox="0 0 256 257"><defs><linearGradient id="IconifyId1813088fe1fbc01fb466" x1="-.828%" x2="57.636%" y1="7.652%" y2="78.411%"><stop offset="0%" stop-color="#41D1FF"></stop><stop offset="100%" stop-color="#BD34FE"></stop></linearGradient><linearGradient id="IconifyId1813088fe1fbc01fb467" x1="43.376%" x2="50.316%" y1="2.242%" y2="89.03%"><stop offset="0%" stop-color="#FFEA83"></stop><stop offset="8.333%" stop-color="#FFDD35"></stop><stop offset="100%" stop-color="#FFA800"></stop></linearGradient></defs><path fill="url(#IconifyId1813088fe1fbc01fb466)" d="M255.153 37.938L134.897 252.976c-2.483 4.44-8.862 4.466-11.382.048L.875 37.958c-2.746-4.814 1.371-10.646 6.827-9.67l120.385 21.517a6.537 6.537 0 0 0 2.322-.004l117.867-21.483c5.438-.991 9.574 4.796 6.877 9.62Z"></path><path fill="url(#IconifyId1813088fe1fbc01fb467)" d="M185.432.063L96.44 17.501a3.268 3.268 0 0 0-2.634 3.014l-5.474 92.456a3.268 3.268 0 0 0 3.997 3.378l24.777-5.718c2.318-.535 4.413 1.507 3.936 3.838l-7.361 36.047c-.495 2.426 1.782 4.5 4.151 3.78l15.304-4.649c2.372-.72 4.652 1.36 4.15 3.788l-11.698 56.621c-.732 3.542 3.979 5.473 5.943 2.437l1.313-2.028l72.516-144.72c1.215-2.423-.88-5.186-3.54-4.672l-25.505 4.922c-2.396.462-4.435-1.77-3.759-4.114l16.646-57.705c.677-2.35-1.37-4.583-3.769-4.113Z"></path></svg>
```

## File: frontend/app/README.md

````markdown
# Frontend Application

A modern React TypeScript application built with Vite, featuring a comprehensive UI component library, authentication, multi-tenant support, and AI chat capabilities.

## 🎯 Overview

This frontend application provides a complete user interface for the full-stack template, featuring:

- **Modern React 19** with TypeScript for type safety
- **Vite** for fast development and optimized builds
- **Tailwind CSS 4** with shadcn/ui components for consistent design
- **React Router 7** for client-side navigation
- **React Hook Form** with Zod validation for forms
- **Auto-generated API clients** from backend OpenAPI specifications

## 🏗️ Project Structure

```
src/
├── api/                   # Auto-generated API client
│   ├── models/            # TypeScript models from backend
│   ├── services/          # API service classes
│   └── core/              # Core API utilities
├── components/
│   ├── features/         # Feature-specific components
│   │   ├── ai-chat/      # AI chat interface
│   │   ├── auth/         # Authentication components
│   │   ├── dashboard/    # Dashboard components
│   │   ├── profile/      # User profile management
│   │   ├── roles/        # Role management
│   │   ├── tenant/       # Tenant management
│   │   └── users/        # User management
│   ├── layouts/          # Page layout components
│   │   ├── default-layout.tsx
│   │   └── dashboard-sidebar-layout.tsx
│   ├── providers/        # React context providers
│   │   └── auth-provider.tsx
│   ├── shared/           # Reusable components
│   │   ├── footer.tsx
│   │   ├── loading.tsx
│   │   └── list-*.tsx
│   └── ui/               # shadcn/ui base components
├── hooks/                # Custom React hooks
├── lib/                  # Utility functions
│   ├── utils.ts         # General utilities
│   └── image-utils.ts   # Image processing utilities
└── assets/              # Static assets
```

## 🚀 Getting Started

### Prerequisites

- Node.js 22+
- pnpm (recommended) or npm
- Backend API running on `http://localhost:8000`

### Installation

1. **Install Dependencies**:

   ```bash
   pnpm install
   ```

2. **Generate API Client** (Backend must be running):

   ```bash
   pnpm run generate:api # You may have to change it to localhost:8000.. Check the package.json
   ```

3. **Start Development Server**:

   ```bash
   pnpm dev
   ```

   The application will be available at: http://localhost:3000

## 📜 Available Scripts

```bash
# Development
pnpm dev                    # Start development server (port 3000)
pnpm build                  # Build for production
pnpm preview                # Preview production build
pnpm lint                   # Run ESLint

# API Integration
pnpm run generate:api       # Generate TypeScript API client from backend
```

## ⚙️ Configuration

### Vite Configuration

The `vite.config.ts` includes:

- **Development Server**: Runs on port 3000
- **API Proxy**: Routes `/api/*` to backend at `http://localhost:8000`
- **Path Aliases**: `@/*` resolves to `src/*`
- **Build Output**: Builds to `../../backend/api/ui` for integrated deployment
- **Tailwind Integration**: Native Tailwind CSS 4 support

### TypeScript Configuration

- **Strict Mode**: Full TypeScript strict mode enabled
- **Path Mapping**: Absolute imports with `@/` prefix
- **Modern Target**: ES2022 for optimal performance

## 🎨 UI Components & Design System

### Component Library

- **shadcn/ui**: High-quality, accessible React components
- **Radix UI**: Unstyled, accessible primitives
- **Lucide React**: Beautiful SVG icons
- **Tailwind CSS**: Utility-first styling

### Theme System

- **Dark/Light Mode**: Built-in theme switching with `next-themes`
- **Responsive Design**: Mobile-first approach
- **Design Tokens**: Consistent spacing, colors, and typography

### Key Components

- **Layouts**: Dashboard with sidebar, default layout
- **Forms**: React Hook Form with Zod validation
- **Tables**: TanStack Table with sorting, filtering
- **Charts**: Recharts integration for data visualization
- **Notifications**: Sonner for toast notifications

## 🔐 Authentication & Authorization

### Auth Provider

The `AuthProvider` manages:

- User session state
- JWT token handling
- Automatic token refresh
- Protected route access

## 🌐 API Integration

### Auto-Generated Client

The API client is automatically generated from the backend's OpenAPI specification:

```typescript
import { ApiClient } from "@/api";

const apiClient = new ApiClient({
  HEADERS: {
    Authorization: `Bearer ${accessToken}`,
  },
});

// Type-safe API calls
const users = await apiClient.users.getUsersApiV1UsersGet();
```

### API Client Generation

When the backend API changes:

1. Ensure backend is running on `http://localhost:8000`
2. Run `pnpm run generate:api`
3. Updated TypeScript types and services are automatically generated

## 🏠 Feature Components

### Dashboard

- **Sidebar Navigation**: Role-based menu with icons
- **Responsive Layout**: Mobile-friendly sidebar collapse
- **Breadcrumb Navigation**: Context-aware navigation

### User Management

- **User List**: Paginated table with search and filters
- **User Profile**: Complete profile management with image upload
- **Role Assignment**: Assign roles to users within tenants

### Tenant Management

- **Tenant Search**: Smart search with autocomplete
- **Multi-tenancy**: Tenant-scoped data and permissions
- **Tenant Switching**: Easy switching between tenants

### AI Chat Interface

- **Real-time Streaming**: Live AI responses with typing indicators
- **Model Selection**: Choose from available AI models
- **Chat History**: Persistent conversation history
- **Mobile Optimized**: Touch-friendly chat interface

### Role & Permission Management

- **Dynamic Roles**: Create and edit roles with permissions
- **Permission Matrix**: Visual permission assignment
- **Role Hierarchy**: Support for role inheritance

## 🎨 Styling & Theming

### Tailwind CSS 4

- **Native Integration**: Direct Vite plugin support
- **Custom Configuration**: Extended theme with design tokens
- **Dark Mode**: Automatic dark/light mode switching
- **Responsive Design**: Mobile-first breakpoints

### Component Styling

```typescript
// Example component with Tailwind classes Shadcn UI https://ui.shadcn.com/docs/installation
function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
```

## 🧪 Development Workflow

### API-First Development

1. **Backend Changes**: Update backend API endpoints
2. **Generate Client**: Run `pnpm run generate:api`
3. **Type Safety**: TypeScript ensures type safety across the stack
4. **Frontend Updates**: Use updated types and services

### Component Development

1. **shadcn/ui**: Use existing components when possible
2. **Custom Components**: Build in `components/shared/` for reusability
3. **Feature Components**: Organize by feature in `components/features/`
4. **Type Safety**: Use TypeScript interfaces for all props

### Form Handling

```typescript
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";

const schema = z.object({
  name: z.string().min(1, "Name is required"),
  email: z.string().email("Invalid email"),
});

function MyForm() {
  const form = useForm({
    resolver: zodResolver(schema),
    defaultValues: { name: "", email: "" },
  });

  const onSubmit = (data) => {
    // Type-safe form data
    console.log(data); // { name: string, email: string }
  };

  return <Form {...form}>...</Form>;
}
```

## 📦 Build & Deployment

### Development Build

```bash
pnpm dev                    # Hot reload development server
```

### Production Build

```bash
pnpm build                  # TypeScript compilation + Vite build
pnpm preview                # Preview production build locally
```

### Build Output

- **Target Directory**: `../../backend/api/ui`
- **Optimized Assets**: Minified JavaScript, CSS, and assets
- **Static Files**: Ready for serving from any web server

## 🔧 Troubleshooting

### Common Issues

1. **API Client Generation Fails**:

   - Ensure backend is running on port 8000
   - Check backend OpenAPI endpoint: `http://localhost:8000/openapi.json`

2. **TypeScript Errors**:

   - Run `pnpm run generate:api` after backend changes
   - Clear TypeScript cache: Delete `node_modules/.cache`

3. **Styling Issues**:

   - Check Tailwind configuration
   - Verify component imports from `@/components/ui`

4. **Cloudflare DNS**:
   - Tenant contains subdomain for example: `test.demo.fsrapp.xyz` then clouldflare doesn't provide free ssl certificate.. [Read here](https://developers.cloudflare.com/ssl/edge-certificates/)

### Development Tips

- **Hot Reload**: Vite provides instant hot module replacement
- **Type Checking**: Use `tsc --noEmit` for type checking without building
- **Debugging**: React Developer Tools and browser DevTools
- **Performance**: Use React Profiler for performance optimization

## 🤝 Contributing

1. **Follow TypeScript**: All new code should be TypeScript
2. **Use shadcn/ui**: Prefer existing components over custom ones
3. **Responsive Design**: Ensure mobile compatibility
4. **Type Safety**: Maintain end-to-end type safety
5. **API Integration**: Regenerate API client after backend changes

---

This frontend application provides a solid foundation for building modern, type-safe React applications with comprehensive UI components and seamless backend integration.
````

## File: frontend/app/src/api/ApiClient.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BaseHttpRequest } from './core/BaseHttpRequest';
import type { OpenAPIConfig } from './core/OpenAPI';
import { FetchHttpRequest } from './core/FetchHttpRequest';
import { AccountService } from './services/AccountService';
import { AiService } from './services/AiService';
import { AppConfigurationService } from './services/AppConfigurationService';
import { AuditLogsService } from './services/AuditLogsService';
import { DashboardService } from './services/DashboardService';
import { FeaturesService } from './services/FeaturesService';
import { HealthService } from './services/HealthService';
import { ManageSecurityService } from './services/ManageSecurityService';
import { NotificationsService } from './services/NotificationsService';
import { PermissionsService } from './services/PermissionsService';
import { RolesService } from './services/RolesService';
import { SsoSettingsService } from './services/SsoSettingsService';
import { StorageSettingsService } from './services/StorageSettingsService';
import { StripeService } from './services/StripeService';
import { StripeBillingService } from './services/StripeBillingService';
import { StripeCheckoutService } from './services/StripeCheckoutService';
import { StripeInvoicesService } from './services/StripeInvoicesService';
import { StripePricingService } from './services/StripePricingService';
import { StripeProductsService } from './services/StripeProductsService';
import { TenantsService } from './services/TenantsService';
import { UsersService } from './services/UsersService';
type HttpRequestConstructor = new (config: OpenAPIConfig) => BaseHttpRequest;
export class ApiClient
⋮----
constructor(config?: Partial<OpenAPIConfig>, HttpRequest: HttpRequestConstructor = FetchHttpRequest)
```

## File: frontend/app/src/api/core/ApiError.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { ApiResult } from './ApiResult';
export class ApiError extends Error
⋮----
constructor(request: ApiRequestOptions, response: ApiResult, message: string)
```

## File: frontend/app/src/api/core/ApiRequestOptions.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ApiRequestOptions = {
  readonly method:
    | "GET"
    | "PUT"
    | "POST"
    | "DELETE"
    | "OPTIONS"
    | "HEAD"
    | "PATCH";
  readonly url: string;
  readonly path?: Record<string, any>;
  readonly cookies?: Record<string, any>;
  readonly headers?: Record<string, any>;
  readonly query?: Record<string, any>;
  readonly formData?: Record<string, any>;
  readonly body?: any;
  readonly mediaType?: string;
  readonly responseHeader?: string;
  readonly errors?: Record<number, string>;
};
```

## File: frontend/app/src/api/core/ApiResult.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ApiResult = {
  readonly url: string;
  readonly ok: boolean;
  readonly status: number;
  readonly statusText: string;
  readonly body: any;
};
```

## File: frontend/app/src/api/core/BaseHttpRequest.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { CancelablePromise } from './CancelablePromise';
import type { OpenAPIConfig } from './OpenAPI';
export abstract class BaseHttpRequest
⋮----
constructor(public readonly config: OpenAPIConfig)
public abstract request<T>(options: ApiRequestOptions): CancelablePromise<T>;
```

## File: frontend/app/src/api/core/CancelablePromise.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export class CancelError extends Error
⋮----
constructor(message: string)
public get isCancelled(): boolean
⋮----
export interface OnCancel {
  readonly isResolved: boolean;
  readonly isRejected: boolean;
  readonly isCancelled: boolean;
  (cancelHandler: () => void): void;
}
export class CancelablePromise<T> implements Promise<T>
⋮----
constructor(
    executor: (
      resolve: (value: T | PromiseLike<T>) => void,
      reject: (reason?: any) => void,
      onCancel: OnCancel
    ) => void
)
⋮----
const onResolve = (value: T | PromiseLike<T>): void =>
const onReject = (reason?: any): void =>
const onCancel = (cancelHandler: () => void): void =>
⋮----
public then<TResult1 = T, TResult2 = never>(
    onFulfilled?: ((value: T) => TResult1 | PromiseLike<TResult1>) | null,
    onRejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | null
): Promise<TResult1 | TResult2>
public catch<TResult = never>(
    onRejected?: ((reason: any) => TResult | PromiseLike<TResult>) | null
): Promise<T | TResult>
public finally(onFinally?: (() => void) | null): Promise<T>
public cancel(): void
```

## File: frontend/app/src/api/core/FetchHttpRequest.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiRequestOptions } from './ApiRequestOptions';
import { BaseHttpRequest } from './BaseHttpRequest';
import type { CancelablePromise } from './CancelablePromise';
import type { OpenAPIConfig } from './OpenAPI';
import { request as __request } from './request';
export class FetchHttpRequest extends BaseHttpRequest
⋮----
constructor(config: OpenAPIConfig)
/**
   * Request method
   * @param options The request options from the service
   * @returns CancelablePromise<T>
   * @throws ApiError
   */
public override request<T>(options: ApiRequestOptions): CancelablePromise<T>
```

## File: frontend/app/src/api/core/OpenAPI.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiRequestOptions } from "./ApiRequestOptions";
type Resolver<T> = (options: ApiRequestOptions) => Promise<T>;
type Headers = Record<string, string>;
export type OpenAPIConfig = {
  BASE: string;
  VERSION: string;
  WITH_CREDENTIALS: boolean;
  CREDENTIALS: "include" | "omit" | "same-origin";
  TOKEN?: string | Resolver<string> | undefined;
  USERNAME?: string | Resolver<string> | undefined;
  PASSWORD?: string | Resolver<string> | undefined;
  HEADERS?: Headers | Resolver<Headers> | undefined;
  ENCODE_PATH?: ((path: string) => string) | undefined;
};
```

## File: frontend/app/src/api/core/request.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import { ApiError } from './ApiError';
import type { ApiRequestOptions } from './ApiRequestOptions';
import type { ApiResult } from './ApiResult';
import { CancelablePromise } from './CancelablePromise';
import type { OnCancel } from './CancelablePromise';
import type { OpenAPIConfig } from './OpenAPI';
export const isDefined = <T>(value: T | null | undefined): value is Exclude<T, null | undefined> =>
export const isString = (value: any): value is string =>
export const isStringWithValue = (value: any): value is string =>
export const isBlob = (value: any): value is Blob =>
export const isFormData = (value: any): value is FormData =>
export const base64 = (str: string): string =>
⋮----
// @ts-ignore
⋮----
export const getQueryString = (params: Record<string, any>): string =>
⋮----
const append = (key: string, value: any) =>
const process = (key: string, value: any) =>
⋮----
const getUrl = (config: OpenAPIConfig, options: ApiRequestOptions): string =>
export const getFormData = (options: ApiRequestOptions): FormData | undefined =>
type Resolver<T> = (options: ApiRequestOptions) => Promise<T>;
export const resolve = async <T>(options: ApiRequestOptions, resolver?: T | Resolver<T>): Promise<T | undefined> =>
export const getHeaders = async (config: OpenAPIConfig, options: ApiRequestOptions): Promise<Headers> =>
export const getRequestBody = (options: ApiRequestOptions): any =>
export const sendRequest = async (
  config: OpenAPIConfig,
  options: ApiRequestOptions,
  url: string,
  body: any,
  formData: FormData | undefined,
  headers: Headers,
  onCancel: OnCancel
): Promise<Response> =>
export const getResponseHeader = (response: Response, responseHeader?: string): string | undefined =>
export const getResponseBody = async (response: Response): Promise<any> =>
export const catchErrorCodes = (options: ApiRequestOptions, result: ApiResult): void =>
/**
 * Request method
 * @param config The OpenAPI configuration object
 * @param options The request options from the service
 * @returns CancelablePromise<T>
 * @throws ApiError
 */
export const request = <T>(config: OpenAPIConfig, options: ApiRequestOptions): CancelablePromise<T> =>
```

## File: frontend/app/src/api/index.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
```

## File: frontend/app/src/api/models/AIAskRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AIAskRequestDto = {
  question: string;
  model_name?: string | null;
  session_id?: string | null;
};
```

## File: frontend/app/src/api/models/AIHistoriesDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AIHistoryDto } from "./AIHistoryDto";
export type AIHistoriesDto = {
  id: string;
  histories: Array<AIHistoryDto>;
};
```

## File: frontend/app/src/api/models/AIHistoryDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AIHistoryDto = {
  uid: string;
  query: string;
  response: string;
  timestamp: string;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/AIModelInfoDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AIModelInfoDto = {
  name: string;
  digest: string;
  size: string;
  created: string;
};
```

## File: frontend/app/src/api/models/AISessionByUserIdDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AIHistoriesDto } from "./AIHistoriesDto";
export type AISessionByUserIdDto = {
  id: string;
  session_id: string;
  created_at: string;
  history_id: string;
  tenant_id?: string | null;
  user_id: string;
  sessions: Array<AIHistoriesDto>;
};
```

## File: frontend/app/src/api/models/AppConfigurationDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TenantDto } from "./TenantDto";
import type { UserPreferenceDto } from "./UserPreferenceDto";
export type AppConfigurationDto = {
  is_multi_tenant_enabled: boolean;
  multi_tenancy_strategy: string;
  host_main_domain: string;
  is_user_logged_in?: boolean | null;
  user_preferences?: UserPreferenceDto | null;
  current_tenant?: TenantDto | null;
  environment: string;
  enabled_sso_providers?: Array<string> | null;
};
```

## File: frontend/app/src/api/models/AuditLogDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AuditLogDto = {
  entity: string;
  action:
    | "create"
    | "update"
    | "delete"
    | "read"
    | "login"
    | "logout"
    | "error"
    | "download";
  changes?: Record<string, any>;
  user_id?: string | null;
  timestamp?: string;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/AuditLogListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuditLogDto } from "./AuditLogDto";
export type AuditLogListDto = {
  total: number;
  logs: Array<AuditLogDto>;
  has_next: boolean;
  has_previous: boolean;
  limit: number;
  skip: number;
};
```

## File: frontend/app/src/api/models/AuthenticatorTransport.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How an authenticator communicates to the client/browser.
 *
 * Members:
 * `USB`: USB wired connection
 * `NFC`: Near Field Communication
 * `BLE`: Bluetooth Low Energy
 * `INTERNAL`: Direct connection (read: a platform authenticator)
 * `CABLE`: Cloud Assisted Bluetooth Low Energy
 * `HYBRID`: A combination of (often separate) data-transport and proximity mechanisms
 *
 * https://www.w3.org/TR/webauthn-2/#enum-transport
 */
export type AuthenticatorTransport =
  | "usb"
  | "nfc"
  | "ble"
  | "smart-card"
  | "internal"
  | "cable"
  | "hybrid";
```

## File: frontend/app/src/api/models/AvailableStorageProviderDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AvailableStorageProviderDto = {
  provider: "s3" | "azure_blob";
  is_enabled: boolean;
  region: string;
  aws_access_key?: string | null;
  aws_secret_key?: string | null;
  aws_bucket_name?: string | null;
  azure_connection_string?: string | null;
  azure_container_name?: string | null;
  updated_by_user_id?: string | null;
  id: string;
  created_at: string;
  updated_at: string;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/BillingRecordDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BillingRecordDto = {
  scope: "host" | "tenant";
  actor: "tenant" | "end_user";
  user_id?: string | null;
  payment_type: string;
  currency: string;
  amount?: number | null;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
  stripe_session_id?: string | null;
  product_id?: string | null;
  price_id?: string | null;
  status:
    | "pending"
    | "requires_payment_method"
    | "requires_action"
    | "active"
    | "succeeded"
    | "payment_failed"
    | "canceled"
    | "incomplete";
  current_period_end?: number | null;
  canceled_at?: number | null;
  cancellation_reason?: string | null;
  metadata?: Record<string, string> | null;
};
```

## File: frontend/app/src/api/models/BillingRecordListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BillingRecordDto } from "./BillingRecordDto";
export type BillingRecordListDto = {
  billing_records: Array<BillingRecordDto>;
  skip: number;
  limit: number;
  total: number;
  has_previous: boolean;
  has_next: boolean;
};
```

## File: frontend/app/src/api/models/Body_login_api_v1_account_login_post.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_login_api_v1_account_login_post = {
  grant_type?: string | null;
  username: string;
  password: string;
  scope?: string;
  client_id?: string | null;
  client_secret?: string | null;
};
```

## File: frontend/app/src/api/models/Body_passkey_login_api_v1_account_passkey_login_post.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_passkey_login_api_v1_account_passkey_login_post = {
  email: string;
  credential: Record<string, any>;
};
```

## File: frontend/app/src/api/models/Body_passkey_register_api_v1_account_passkey_register_post.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_passkey_register_api_v1_account_passkey_register_post = {
  email: string;
  credential: Record<string, any>;
};
```

## File: frontend/app/src/api/models/Body_update_profile_picture_api_v1_users**user_id**update_profile_picture_put.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_update_profile_picture_api_v1_users__user_id__update_profile_picture_put =
  {
    file: Blob;
  };
```

## File: frontend/app/src/api/models/ChangeEmailConfirmRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ChangeEmailConfirmRequestDto = {
  token: string;
};
```

## File: frontend/app/src/api/models/ChangeEmailRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ChangeEmailRequestDto = {
  new_email: string;
};
```

## File: frontend/app/src/api/models/ChangeEmailResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ChangeEmailResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/CheckoutRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CheckoutRequestDto = {
  price_id: string;
  tenant_id?: string | null;
  mode?: "payment" | "subscription" | "setup";
  email: string;
};
```

## File: frontend/app/src/api/models/CheckoutResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CheckoutResponseDto = {
  checkout_url: string;
};
```

## File: frontend/app/src/api/models/CreateNotificationBannerSettingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateNotificationBannerSettingDto = {
  is_enabled: boolean;
  message?: string | null;
};
```

## File: frontend/app/src/api/models/CreatePlanDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreatePlanDto = {
  /**
   * Must be a supported currency: https://docs.stripe.com/currencies
   */
  currency?: string;
  interval: 'month' | 'year';
  /**
   * The product whose pricing the created plan will represent. This must be the ID of an existing product.
   */
  product_id: string;
  amount: number;
};
⋮----
/**
   * Must be a supported currency: https://docs.stripe.com/currencies
   */
⋮----
/**
   * The product whose pricing the created plan will represent. This must be the ID of an existing product.
   */
```

## File: frontend/app/src/api/models/CreatePricingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RecurringDto } from './RecurringDto';
export type CreatePricingDto = {
  /**
   * Currency code, e.g., 'usd'
   */
  currency?: string;
  /**
   * A positive integer in cents (or 0 for a free price) representing how much to charge. One of unit_amount, unit_amount_decimal, or custom_unit_amount is required, unless billing_scheme=tiered.
   */
  unit_amount: number;
  /**
   * The ID of the product this price is associated with
   */
  product: string;
  /**
   * Whether the price is active or not
   */
  active?: boolean;
  recurring: RecurringDto;
};
⋮----
/**
   * Currency code, e.g., 'usd'
   */
⋮----
/**
   * A positive integer in cents (or 0 for a free price) representing how much to charge. One of unit_amount, unit_amount_decimal, or custom_unit_amount is required, unless billing_scheme=tiered.
   */
⋮----
/**
   * The ID of the product this price is associated with
   */
⋮----
/**
   * Whether the price is active or not
   */
```

## File: frontend/app/src/api/models/CreateProductDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateProductDto = {
  name: string;
  description: string;
  tax_code?: string | null;
  active?: boolean;
};
```

## File: frontend/app/src/api/models/CreateRoleDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PydanticObjectId } from "./PydanticObjectId";
export type CreateRoleDto = {
  name: string;
  description?: string | null;
  tenant_id?: PydanticObjectId | null;
};
```

## File: frontend/app/src/api/models/CreateRoleResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateRoleResponseDto = {
  id: string;
};
```

## File: frontend/app/src/api/models/CreateSSOSettingsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateSSOSettingsDto = {
  enabled: boolean;
  provider:
    | "google"
    | "github"
    | "discord"
    | "microsoft"
    | "linkedin"
    | "x"
    | "notion"
    | "gitlab"
    | "bitbucket"
    | "facebook";
  client_id: string;
  client_secret?: string | null;
  scopes?: Array<string> | null;
  redirect_uris?: Array<string> | null;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/CreateStripeSettingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateStripeSettingDto = {
  default_currency?: string;
  stripe_webhook_secret: string;
  mode?: "one_time" | "recurring" | "both";
  trial_period_days?: number;
  stripe_secret_key: string;
  tenant_id: string;
};
```

## File: frontend/app/src/api/models/CreateTenantDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Gender } from "./Gender";
export type CreateTenantDto = {
  name: string;
  subdomain: string;
  admin_email: string;
  admin_password: string;
  first_name: string;
  last_name: string;
  gender: Gender;
};
```

## File: frontend/app/src/api/models/CreateTenantResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateTenantResponseDto = {
  id: string;
};
```

## File: frontend/app/src/api/models/CreateUserDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Gender } from "./Gender";
import type { PydanticObjectId } from "./PydanticObjectId";
export type CreateUserDto = {
  first_name: string;
  last_name: string;
  email: string;
  gender: Gender;
  tenant_id?: PydanticObjectId | null;
  role_id?: PydanticObjectId | null;
  sso_provider_id?: string | null;
  password: string;
  sub_domain?: string | null;
};
```

## File: frontend/app/src/api/models/CreateUserResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateUserResponseDto = {
  id: string;
};
```

## File: frontend/app/src/api/models/DashboardMetricsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TimeSeriesDto } from "./TimeSeriesDto";
export type DashboardMetricsDto = {
  filter: "today" | "this_week" | "last_3_months" | "all";
  joined_users: number;
  total_users: number;
  timeseries: Array<TimeSeriesDto>;
};
```

## File: frontend/app/src/api/models/DownloadAuditRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DownloadAuditRequestDto = {
  action?:
    | "create"
    | "update"
    | "delete"
    | "read"
    | "login"
    | "logout"
    | "error"
    | "download"
    | null;
  total_records: number;
};
```

## File: frontend/app/src/api/models/DownloadResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DownloadResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/Feature.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Feature =
  | "chat"
  | "report"
  | "analytics"
  | "organization"
  | "teams"
  | "stripe";
```

## File: frontend/app/src/api/models/FeatureDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Feature } from "./Feature";
export type FeatureDto = {
  name: Feature;
  enabled: boolean;
};
```

## File: frontend/app/src/api/models/Gender.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Gender = "male" | "female" | "other" | "prefer_not_to_say";
```

## File: frontend/app/src/api/models/HasPasskeysDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HasPasskeysDto = {
  has_passkeys: boolean;
};
```

## File: frontend/app/src/api/models/HealthCheckResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HealthCheckResponseDto = {
  status: string;
};
```

## File: frontend/app/src/api/models/HTTPValidationError.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ValidationError } from "./ValidationError";
export type HTTPValidationError = {
  detail?: Array<ValidationError>;
};
```

## File: frontend/app/src/api/models/InvoiceDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type InvoiceDto = {
  id: string;
  amount_country: string;
  account_name: string;
  amount_due?: number;
  amount_paid?: number;
  amount_remaining?: number;
  amount_overpaid?: number;
  attempt_count?: number;
  attempted?: boolean;
  auto_advance?: boolean;
  billing_reason: string;
  collection_method: string;
  created: number;
  currency: string;
  customer: string;
  customer_name: string;
  status: string;
  total: number;
  receipt_number?: string | null;
};
```

## File: frontend/app/src/api/models/InvoiceListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InvoiceDto } from "./InvoiceDto";
export type InvoiceListDto = {
  invoices: Array<InvoiceDto>;
  has_more?: boolean;
};
```

## File: frontend/app/src/api/models/MagicLinkResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type MagicLinkResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/MeResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Gender } from "./Gender";
import type { RoleDto } from "./RoleDto";
import type { SubscriptionPlanDto } from "./SubscriptionPlanDto";
export type MeResponseDto = {
  id: string;
  first_name: string;
  last_name: string;
  email: string;
  gender: Gender;
  role_id?: string | null;
  is_active: boolean;
  activated_at?: string | null;
  image_url?: string | null;
  created_at: string;
  updated_at: string;
  tenant_id?: string | null;
  sso_provider_id?: string | null;
  role: RoleDto;
  subscription: SubscriptionPlanDto | null;
};
```

## File: frontend/app/src/api/models/NewSessionResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type NewSessionResponseDto = {
  session_id: string;
};
```

## File: frontend/app/src/api/models/NotificationBannerSettingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type NotificationBannerSettingDto = {
  is_enabled: boolean;
  message?: string | null;
  id?: string | null;
  tenant_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};
```

## File: frontend/app/src/api/models/PasswordResetConfirmRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PasswordResetConfirmRequestDto = {
  new_password: string;
};
```

## File: frontend/app/src/api/models/PasswordResetRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PasswordResetRequestDto = {
  email: string;
};
```

## File: frontend/app/src/api/models/PasswordResetResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PasswordResetResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/Permission.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Permission =
  | "full:access"
  | "user:read_and_write_only"
  | "user:delete_only"
  | "user:self_read_and_write_only"
  | "role:assign_or_remove_only"
  | "user:view_only"
  | "role:view_only"
  | "role:read_and_write_only"
  | "role:delete_only"
  | "role:permission_read_and_write_only"
  | "host:manage_tenants"
  | "manage:storage_settings"
  | "manage:tenant_settings"
  | "manage:billing"
  | "manage:payments_settings"
  | "manage:products_and_pricing"
  | "audit_logs:view_only"
  | "audit_logs:download";
```

## File: frontend/app/src/api/models/PermissionDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PermissionDto = Record<string, any>;
```

## File: frontend/app/src/api/models/PlanDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PlanDto = {
  id: string;
  active: boolean;
  amount: number;
  amount_decimal: string;
  billing_scheme: string;
  currency?: string;
  interval: string;
  interval_count: number;
  product: string;
  trial_period_days?: number | null;
  usage_type: string;
};
```

## File: frontend/app/src/api/models/PlanListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanDto } from "./PlanDto";
export type PlanListDto = {
  plans: Array<PlanDto>;
  has_more: boolean;
};
```

## File: frontend/app/src/api/models/PricingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RecurringDto } from "./RecurringDto";
export type PricingDto = {
  id: string;
  currency: string;
  unit_amount: number;
  product: string;
  active: boolean;
  recurring: RecurringDto;
  tax_behavior: string;
  type: string;
  unit_amount_decimal: string;
};
```

## File: frontend/app/src/api/models/PricingListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PricingDto } from "./PricingDto";
export type PricingListDto = {
  pricings: Array<PricingDto>;
  has_more: boolean;
};
```

## File: frontend/app/src/api/models/ProductDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProductDto = {
  id: string;
  name: string;
  description: string;
  tax_code?: string | null;
  active: boolean;
};
```

## File: frontend/app/src/api/models/ProductListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProductDto } from "./ProductDto";
export type ProductListDto = {
  products: Array<ProductDto>;
  has_more: boolean;
};
```

## File: frontend/app/src/api/models/PydanticObjectId.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PydanticObjectId = string;
```

## File: frontend/app/src/api/models/ReadSSOSettingsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReadSSOSettingsDto = {
  enabled: boolean;
  provider:
    | "google"
    | "github"
    | "discord"
    | "microsoft"
    | "linkedin"
    | "x"
    | "notion"
    | "gitlab"
    | "bitbucket"
    | "facebook";
  client_id: string;
  client_secret?: string | null;
  scopes?: Array<string> | null;
  redirect_uris?: Array<string> | null;
  id: string;
};
```

## File: frontend/app/src/api/models/RecurringDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RecurringDto = {
  /**
   * Billing interval, e.g., 'month'
   */
  interval?: 'day' | 'week' | 'month' | 'year';
  /**
   * Number of intervals between each billing cycle
   */
  interval_count?: number;
  /**
   * Configures how the quantity per period should be determined. Can be either metered or licensed. licensed automatically bills the quantity set when adding it to a subscription. metered aggregates the total usage based on usage records. Defaults to licensed.
   */
  usage_type?: 'licensed' | 'metered';
};
⋮----
/**
   * Billing interval, e.g., 'month'
   */
⋮----
/**
   * Number of intervals between each billing cycle
   */
⋮----
/**
   * Configures how the quantity per period should be determined. Can be either metered or licensed. licensed automatically bills the quantity set when adding it to a subscription. metered aggregates the total usage based on usage records. Defaults to licensed.
   */
```

## File: frontend/app/src/api/models/RegisteredPasskeyCredentialsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthenticatorTransport } from "./AuthenticatorTransport";
export type RegisteredPasskeyCredentialsDto = {
  credential_id: string;
  public_key: string;
  sigin_count: number;
  transports?: Array<AuthenticatorTransport>;
  created_at: string;
  last_used_at?: string | null;
};
```

## File: frontend/app/src/api/models/RoleDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Permission } from "./Permission";
export type RoleDto = {
  id: string;
  name: string;
  description: string | null;
  permissions?: Array<Permission>;
  created_at: string;
  updated_at: string;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/RoleListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RoleDto } from "./RoleDto";
export type RoleListDto = {
  roles: Array<RoleDto>;
  skip: number;
  limit: number;
  total: number;
  has_previous: boolean;
  has_next: boolean;
};
```

## File: frontend/app/src/api/models/SSOSettingsListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReadSSOSettingsDto } from "./ReadSSOSettingsDto";
export type SSOSettingsListDto = {
  items: Array<ReadSSOSettingsDto>;
};
```

## File: frontend/app/src/api/models/SSOSettingsResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SSOSettingsResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/StorageSettingsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StorageSettingsDto = {
  provider: "s3" | "azure_blob";
  is_enabled: boolean;
  region: string;
  aws_access_key?: string | null;
  aws_secret_key?: string | null;
  aws_bucket_name?: string | null;
  azure_connection_string?: string | null;
  azure_container_name?: string | null;
  updated_by_user_id?: string | null;
};
```

## File: frontend/app/src/api/models/StripeSettingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StripeSettingDto = {
  id: string;
  tenant_id: string;
  default_currency: string;
  mode: "one_time" | "recurring" | "both";
  trial_period_days: number;
};
```

## File: frontend/app/src/api/models/SubdomainAvailabilityDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SubdomainAvailabilityDto = {
  is_available: boolean;
};
```

## File: frontend/app/src/api/models/SubscriptionPlanDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SubscriptionPlanDto = {
  id: string;
  /**
   * e.g., {'basic': 0, 'pro': 1, 'enterprise': 2}
   */
  plan_level: Record<string, number>;
  /**
   * Indicates if the subscription is a trial
   */
  is_trial: boolean;
  /**
   * The tenant who owns the subscription
   */
  tenant_id: (string | null);
  /**
   * who it is applied to, If end_user, then user_id must be set
   */
  actor: string;
  /**
   * The end user who has this subscription, if applicable
   */
  user_id: (string | null);
  /**
   * Corresponding Stripe Price ID
   */
  plan_id: string;
};
⋮----
/**
   * e.g., {'basic': 0, 'pro': 1, 'enterprise': 2}
   */
⋮----
/**
   * Indicates if the subscription is a trial
   */
⋮----
/**
   * The tenant who owns the subscription
   */
⋮----
/**
   * who it is applied to, If end_user, then user_id must be set
   */
⋮----
/**
   * The end user who has this subscription, if applicable
   */
⋮----
/**
   * Corresponding Stripe Price ID
   */
```

## File: frontend/app/src/api/models/TenantDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FeatureDto } from "./FeatureDto";
export type TenantDto = {
  id: string | null;
  name: string;
  subdomain: string | null;
  is_active: boolean;
  custom_domain: string | null;
  custom_domain_status?: "active" | "failed" | "activation-progress";
  features?: Array<FeatureDto>;
  subscription_id: string | null;
};
```

## File: frontend/app/src/api/models/TenantListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TenantDto } from "./TenantDto";
export type TenantListDto = {
  tenants: Array<TenantDto>;
  skip: number;
  limit: number;
  total: number;
  has_previous: boolean;
  has_next: boolean;
};
```

## File: frontend/app/src/api/models/TimeSeriesDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TimeSeriesDto = {
  time_or_date: string;
  count: number;
};
```

## File: frontend/app/src/api/models/TokenSetDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TokenSetDto = {
  refresh_token: string;
  refresh_token_expires_in: number;
  access_token: string;
  token_type: string;
  expires_in: number;
};
```

## File: frontend/app/src/api/models/UpdatePlanDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UpdatePlanDto = {
  trial_period_days: number;
  active?: boolean;
  metadata?: Record<string, any> | null;
};
```

## File: frontend/app/src/api/models/UpdatePricingDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UpdatePricingDto = {
  active: boolean;
  metadata?: Record<string, any> | null;
  tax_behavior?: "exclusive" | "inclusive" | "unspecified" | null;
};
```

## File: frontend/app/src/api/models/UpdateRoleDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Permission } from "./Permission";
import type { PydanticObjectId } from "./PydanticObjectId";
export type UpdateRoleDto = {
  name: string;
  description?: string | null;
  tenant_id?: PydanticObjectId | null;
  permissions?: Array<Permission>;
};
```

## File: frontend/app/src/api/models/UpdateSSOSettingsDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UpdateSSOSettingsDto = {
  enabled?: boolean | null;
  provider?:
    | "google"
    | "github"
    | "discord"
    | "microsoft"
    | "linkedin"
    | "x"
    | "notion"
    | "gitlab"
    | "bitbucket"
    | "facebook"
    | null;
  client_id?: string | null;
  client_secret?: string | null;
  scopes?: Array<string> | null;
};
```

## File: frontend/app/src/api/models/UpdateTenantDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UpdateTenantDto = {
  is_active?: boolean | null;
  custom_domain?: string | null;
  subscription_id?: string | null;
};
```

## File: frontend/app/src/api/models/UpdateTenantResponseDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UpdateTenantResponseDto = {
  message: string;
};
```

## File: frontend/app/src/api/models/UpdateUserDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Gender } from "./Gender";
import type { PydanticObjectId } from "./PydanticObjectId";
export type UpdateUserDto = {
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  gender?: Gender | null;
  image_url?: string | null;
  role_id?: string | null;
  is_active?: boolean | null;
  tenant_id?: PydanticObjectId | null;
  sso_provider_id?: string | null;
};
```

## File: frontend/app/src/api/models/UserActivationRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UserActivationRequestDto = {
  token: string;
};
```

## File: frontend/app/src/api/models/UserDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Gender } from "./Gender";
export type UserDto = {
  id: string;
  first_name: string;
  last_name: string;
  email: string;
  gender: Gender;
  role_id?: string | null;
  is_active: boolean;
  activated_at?: string | null;
  image_url?: string | null;
  created_at: string;
  updated_at: string;
  tenant_id?: string | null;
  sso_provider_id?: string | null;
};
```

## File: frontend/app/src/api/models/UserListDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UserDto } from "./UserDto";
export type UserListDto = {
  users: Array<UserDto>;
  skip: number;
  limit: number;
  total: number;
  has_previous: boolean;
  has_next: boolean;
};
```

## File: frontend/app/src/api/models/UserPreferenceDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UserPreferenceDto = {
  user_id: string;
  preferences: Record<string, any>;
};
```

## File: frontend/app/src/api/models/UserProfileImageUpdateDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UserProfileImageUpdateDto = {
  image_url: string;
};
```

## File: frontend/app/src/api/models/UserResendActivationEmailRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UserResendActivationEmailRequestDto = {
  email: string;
  id: string;
  first_name: string;
  tenant_id?: string | null;
};
```

## File: frontend/app/src/api/models/UserRoleUpdateRequestDto.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UserRoleUpdateRequestDto = {
  role_id: string;
};
```

## File: frontend/app/src/api/models/ValidationError.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ValidationError = {
  loc: Array<string | number>;
  msg: string;
  type: string;
};
```

## File: frontend/app/src/api/services/AccountService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_login_api_v1_account_login_post } from '../models/Body_login_api_v1_account_login_post';
import type { Body_passkey_login_api_v1_account_passkey_login_post } from '../models/Body_passkey_login_api_v1_account_passkey_login_post';
import type { Body_passkey_register_api_v1_account_passkey_register_post } from '../models/Body_passkey_register_api_v1_account_passkey_register_post';
import type { ChangeEmailConfirmRequestDto } from '../models/ChangeEmailConfirmRequestDto';
import type { ChangeEmailRequestDto } from '../models/ChangeEmailRequestDto';
import type { ChangeEmailResponseDto } from '../models/ChangeEmailResponseDto';
import type { CreateUserDto } from '../models/CreateUserDto';
import type { HasPasskeysDto } from '../models/HasPasskeysDto';
import type { MagicLinkResponseDto } from '../models/MagicLinkResponseDto';
import type { MeResponseDto } from '../models/MeResponseDto';
import type { PasswordResetConfirmRequestDto } from '../models/PasswordResetConfirmRequestDto';
import type { PasswordResetRequestDto } from '../models/PasswordResetRequestDto';
import type { PasswordResetResponseDto } from '../models/PasswordResetResponseDto';
import type { TokenSetDto } from '../models/TokenSetDto';
import type { UserActivationRequestDto } from '../models/UserActivationRequestDto';
import type { UserResendActivationEmailRequestDto } from '../models/UserResendActivationEmailRequestDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class AccountService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Login
   * @returns TokenSetDto Successful Response
   * @throws ApiError
   */
public loginApiV1AccountLoginPost({
    formData,
  }: {
    formData: Body_login_api_v1_account_login_post,
}): CancelablePromise<TokenSetDto>
/**
   * Refresh Token
   * @returns TokenSetDto Successful Response
   * @throws ApiError
   */
public refreshTokenApiV1AccountRefreshPost({
    refreshToken,
  }: {
    refreshToken?: (string | null),
}): CancelablePromise<TokenSetDto>
/**
   * Logout
   * @returns any Successful Response
   * @throws ApiError
   */
public logoutApiV1AccountLogoutGet(): CancelablePromise<any>
/**
   * Register
   * @returns any Successful Response
   * @throws ApiError
   */
public registerApiV1AccountRegisterPost({
    requestBody,
  }: {
    requestBody: CreateUserDto,
}): CancelablePromise<any>
/**
   * Read Users Me
   * @returns MeResponseDto Successful Response
   * @throws ApiError
   */
public readUsersMeApiV1AccountMeGet(): CancelablePromise<MeResponseDto>
/**
   * Password Reset Request
   * @returns PasswordResetResponseDto Successful Response
   * @throws ApiError
   */
public passwordResetRequestApiV1AccountPasswordResetRequestPost({
    requestBody,
  }: {
    requestBody: PasswordResetRequestDto,
}): CancelablePromise<PasswordResetResponseDto>
/**
   * Password Reset Confirm
   * @returns PasswordResetResponseDto Successful Response
   * @throws ApiError
   */
public passwordResetConfirmApiV1AccountPasswordResetConfirmationPost({
    token,
    userId,
    requestBody,
    tenantId,
  }: {
    /**
     * The password reset token
     */
    token: string,
    /**
     * The user ID associated with the token
     */
    userId: string,
    requestBody: PasswordResetConfirmRequestDto,
    /**
     * The tenant ID associated with the user, if applicable
     */
    tenantId?: (string | null),
}): CancelablePromise<PasswordResetResponseDto>
⋮----
/**
     * The password reset token
     */
⋮----
/**
     * The user ID associated with the token
     */
⋮----
/**
     * The tenant ID associated with the user, if applicable
     */
⋮----
/**
   * Resend Activation Email
   * @returns any Successful Response
   * @throws ApiError
   */
public resendActivationEmailApiV1AccountResendActivationEmailPost({
    requestBody,
  }: {
    requestBody: UserResendActivationEmailRequestDto,
}): CancelablePromise<any>
/**
   * Activate Account
   * @returns any Successful Response
   * @throws ApiError
   */
public activateAccountApiV1AccountActivatePost({
    requestBody,
    tenantId,
  }: {
    requestBody: UserActivationRequestDto,
    /**
     * The tenant ID associated with the user, if applicable
     */
    tenantId?: (string | null),
}): CancelablePromise<any>
⋮----
/**
     * The tenant ID associated with the user, if applicable
     */
⋮----
/**
   * Change Email
   * @returns any Successful Response
   * @throws ApiError
   */
public changeEmailApiV1AccountChangeEmailRequestPatch({
    requestBody,
  }: {
    requestBody: ChangeEmailRequestDto,
}): CancelablePromise<any>
/**
   * Change Email Confirmation
   * @returns ChangeEmailResponseDto Successful Response
   * @throws ApiError
   */
public changeEmailConfirmationApiV1AccountChangeEmailConfirmationPatch({
    requestBody,
  }: {
    requestBody: ChangeEmailConfirmRequestDto,
}): CancelablePromise<ChangeEmailResponseDto>
/**
   * Passkey Register Options
   * @returns any Successful Response
   * @throws ApiError
   */
public passkeyRegisterOptionsApiV1AccountPasskeyRegisterOptionsPost({
    requestBody,
  }: {
    requestBody: string,
}): CancelablePromise<any>
/**
   * Passkey Register
   * @returns any Successful Response
   * @throws ApiError
   */
public passkeyRegisterApiV1AccountPasskeyRegisterPost({
    requestBody,
  }: {
    requestBody: Body_passkey_register_api_v1_account_passkey_register_post,
}): CancelablePromise<any>
/**
   * Passkey Login Options
   * @returns any Successful Response
   * @throws ApiError
   */
public passkeyLoginOptionsApiV1AccountPasskeyLoginOptionsPost({
    requestBody,
  }: {
    requestBody: string,
}): CancelablePromise<any>
/**
   * Passkey Login
   * @returns TokenSetDto Successful Response
   * @throws ApiError
   */
public passkeyLoginApiV1AccountPasskeyLoginPost({
    requestBody,
  }: {
    requestBody: Body_passkey_login_api_v1_account_passkey_login_post,
}): CancelablePromise<TokenSetDto>
/**
   * Has Passkeys
   * @returns HasPasskeysDto Successful Response
   * @throws ApiError
   */
public hasPasskeysApiV1AccountPasskeyHasPasskeysPost({
    requestBody,
  }: {
    requestBody: string,
}): CancelablePromise<HasPasskeysDto>
/**
   * Email Magic Link Login
   * @returns MagicLinkResponseDto Successful Response
   * @throws ApiError
   */
public emailMagicLinkLoginApiV1AccountEmailMagicLinkLoginPost({
    requestBody,
  }: {
    requestBody: string,
}): CancelablePromise<MagicLinkResponseDto>
/**
   * Email Magic Link Validate
   * @returns TokenSetDto Successful Response
   * @throws ApiError
   */
public emailMagicLinkValidateApiV1AccountEmailMagicLinkValidateGet({
    token,
    userId,
    tenantId,
  }: {
    /**
     * The magic link token
     */
    token: string,
    /**
     * The user ID associated with the token
     */
    userId: string,
    /**
     * The tenant ID associated with the user, if applicable
     */
    tenantId?: (string | null),
}): CancelablePromise<TokenSetDto>
⋮----
/**
     * The magic link token
     */
⋮----
/**
     * The user ID associated with the token
     */
⋮----
/**
     * The tenant ID associated with the user, if applicable
     */
⋮----
/**
   * Sso Provider Login
   * @returns void
   * @throws ApiError
   */
public ssoProviderLoginApiV1AccountSsoProviderNameLoginGet({
    providerName,
  }: {
    providerName: 'google' | 'github' | 'discord' | 'microsoft' | 'linkedin' | 'x' | 'notion' | 'gitlab' | 'bitbucket' | 'facebook',
}): CancelablePromise<void>
/**
   * Sso Provider Callback
   * @returns void
   * @throws ApiError
   */
public ssoProviderCallbackApiV1AccountSsoProviderNameCallbackGet({
    providerName,
  }: {
    providerName: 'google' | 'github' | 'discord' | 'microsoft' | 'linkedin' | 'x' | 'notion' | 'gitlab' | 'bitbucket' | 'facebook',
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/AiService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AIAskRequestDto } from '../models/AIAskRequestDto';
import type { AIHistoriesDto } from '../models/AIHistoriesDto';
import type { AIModelInfoDto } from '../models/AIModelInfoDto';
import type { AISessionByUserIdDto } from '../models/AISessionByUserIdDto';
import type { NewSessionResponseDto } from '../models/NewSessionResponseDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class AiService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Models
   * @returns AIModelInfoDto Successful Response
   * @throws ApiError
   */
public getModelsApiV1AiModelsGet(): CancelablePromise<Array<AIModelInfoDto>>
/**
   * Get History
   * @returns AISessionByUserIdDto Successful Response
   * @throws ApiError
   */
public getHistoryApiV1AiHistoryGet(): CancelablePromise<Array<AISessionByUserIdDto>>
/**
   * Create New Session
   * @returns NewSessionResponseDto Successful Response
   * @throws ApiError
   */
public createNewSessionApiV1AiNewSessionGet(): CancelablePromise<NewSessionResponseDto>
/**
   * Get Single Session
   * @returns AIHistoriesDto Successful Response
   * @throws ApiError
   */
public getSingleSessionApiV1AiSessionsSessionIdGet({
    sessionId,
  }: {
    sessionId: string,
}): CancelablePromise<Array<AIHistoriesDto>>
/**
   * Delete Session
   * @returns any Successful Response
   * @throws ApiError
   */
public deleteSessionApiV1AiSessionsSessionIdDelete({
    sessionId,
  }: {
    sessionId: string,
}): CancelablePromise<any>
/**
   * Set Preferred Model
   * @returns void
   * @throws ApiError
   */
public setPreferredModelApiV1AiSetModelPreferenceModelNamePut({
    modelName,
  }: {
    modelName: string,
}): CancelablePromise<void>
/**
   * Ask Ai
   * @returns any Successful Response
   * @throws ApiError
   */
public askAiApiV1AiAskPost({
    requestBody,
  }: {
    requestBody: AIAskRequestDto,
}): CancelablePromise<any>
```

## File: frontend/app/src/api/services/AppConfigurationService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppConfigurationDto } from '../models/AppConfigurationDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class AppConfigurationService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get App Configuration
   * @returns AppConfigurationDto Successful Response
   * @throws ApiError
   */
public getAppConfigurationApiV1AppConfigurationGet(): CancelablePromise<AppConfigurationDto>
```

## File: frontend/app/src/api/services/AuditLogsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuditLogListDto } from '../models/AuditLogListDto';
import type { DownloadAuditRequestDto } from '../models/DownloadAuditRequestDto';
import type { DownloadResponseDto } from '../models/DownloadResponseDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class AuditLogsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Audit Logs
   * @returns AuditLogListDto Successful Response
   * @throws ApiError
   */
public listAuditLogsApiV1AuditLogsGet({
    limit = 10,
    skip,
    action,
  }: {
    limit?: number,
    skip?: number,
    action?: ('create' | 'update' | 'delete' | 'read' | 'login' | 'logout' | 'error' | 'download' | null),
}): CancelablePromise<AuditLogListDto>
/**
   * Download Audit Logs
   * @returns DownloadResponseDto Successful Response
   * @throws ApiError
   */
public downloadAuditLogsApiV1AuditLogsDownloadPost({
    requestBody,
  }: {
    requestBody: DownloadAuditRequestDto,
}): CancelablePromise<DownloadResponseDto>
```

## File: frontend/app/src/api/services/DashboardService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DashboardMetricsDto } from '../models/DashboardMetricsDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class DashboardService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Dashboard Metrics
   * @returns DashboardMetricsDto Successful Response
   * @throws ApiError
   */
public getDashboardMetricsApiV1DashboardGet({
    filter = 'all',
  }: {
    filter?: 'today' | 'this_week' | 'last_3_months' | 'all',
}): CancelablePromise<DashboardMetricsDto>
```

## File: frontend/app/src/api/services/FeaturesService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Feature } from '../models/Feature';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class FeaturesService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Features
   * List all available features. Requires HOST_MANAGE_TENANTS permission.
   * @returns Feature Successful Response
   * @throws ApiError
   */
public listFeaturesApiV1FeaturesGet(): CancelablePromise<Array<Feature>>
```

## File: frontend/app/src/api/services/HealthService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HealthCheckResponseDto } from '../models/HealthCheckResponseDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class HealthService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Health Check
   * @returns HealthCheckResponseDto Successful Response
   * @throws ApiError
   */
public healthCheckApiV1HealthGet(): CancelablePromise<HealthCheckResponseDto>
```

## File: frontend/app/src/api/services/ManageSecurityService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RegisteredPasskeyCredentialsDto } from '../models/RegisteredPasskeyCredentialsDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class ManageSecurityService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Registered Passkeys
   * Endpoint to retrieve registered passkeys for the current user.
   * @returns RegisteredPasskeyCredentialsDto Successful Response
   * @throws ApiError
   */
public getRegisteredPasskeysApiV1SecurityPasskeysGet(): CancelablePromise<Array<RegisteredPasskeyCredentialsDto>>
/**
   * Delete Registered Passkey
   * Endpoint to delete a registered passkey for the current user.
   * @returns void
   * @throws ApiError
   */
public deleteRegisteredPasskeyApiV1SecurityPasskeysCredentialIdDelete({
    credentialId,
  }: {
    credentialId: string,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/NotificationsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateNotificationBannerSettingDto } from '../models/CreateNotificationBannerSettingDto';
import type { NotificationBannerSettingDto } from '../models/NotificationBannerSettingDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class NotificationsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Notification Banner
   * Get the current notification banner information.
   * @returns NotificationBannerSettingDto Successful Response
   * @throws ApiError
   */
public getNotificationBannerApiV1NotificationsBannerGet(): CancelablePromise<NotificationBannerSettingDto>
/**
   * Create Notification Banner
   * Create a new notification banner setting.
   * @returns any Successful Response
   * @throws ApiError
   */
public createNotificationBannerApiV1NotificationsBannerPost({
    requestBody,
  }: {
    requestBody: CreateNotificationBannerSettingDto,
}): CancelablePromise<any>
/**
   * Update Notification Banner
   * Update an existing notification banner setting.
   * @returns void
   * @throws ApiError
   */
public updateNotificationBannerApiV1NotificationsBannerIdPut({
    id,
    requestBody,
  }: {
    id: string,
    requestBody: CreateNotificationBannerSettingDto,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/PermissionsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionDto } from '../models/PermissionDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class PermissionsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Permissions
   * @returns PermissionDto Successful Response
   * @throws ApiError
   */
public getPermissionsApiV1PermissionsGet(): CancelablePromise<Array<PermissionDto>>
```

## File: frontend/app/src/api/services/RolesService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateRoleDto } from '../models/CreateRoleDto';
import type { CreateRoleResponseDto } from '../models/CreateRoleResponseDto';
import type { RoleDto } from '../models/RoleDto';
import type { RoleListDto } from '../models/RoleListDto';
import type { UpdateRoleDto } from '../models/UpdateRoleDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class RolesService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Roles
   * @returns RoleListDto Successful Response
   * @throws ApiError
   */
public listRolesApiV1RolesGet({
    skip,
    limit = 10,
  }: {
    skip?: number,
    limit?: number,
}): CancelablePromise<RoleListDto>
/**
   * Create Role
   * @returns CreateRoleResponseDto Successful Response
   * @throws ApiError
   */
public createRoleApiV1RolesPost({
    requestBody,
  }: {
    requestBody: CreateRoleDto,
}): CancelablePromise<CreateRoleResponseDto>
/**
   * Search Role By Name
   * @returns RoleDto Successful Response
   * @throws ApiError
   */
public searchRoleByNameApiV1RolesSearchByNameGet({
    name,
  }: {
    name: string,
}): CancelablePromise<Array<RoleDto>>
/**
   * Get Role
   * @returns RoleDto Successful Response
   * @throws ApiError
   */
public getRoleApiV1RolesRoleIdGet({
    roleId,
  }: {
    roleId: string,
}): CancelablePromise<RoleDto>
/**
   * Update Role
   * @returns RoleDto Successful Response
   * @throws ApiError
   */
public updateRoleApiV1RolesRoleIdPut({
    roleId,
    requestBody,
  }: {
    roleId: string,
    requestBody: UpdateRoleDto,
}): CancelablePromise<RoleDto>
/**
   * Delete Role
   * Deletes a role if it is not assigned to any users.
   * @returns any Successful Response
   * @throws ApiError
   */
public deleteRoleApiV1RolesRoleIdDelete({
    roleId,
  }: {
    roleId: string,
}): CancelablePromise<any>
```

## File: frontend/app/src/api/services/SsoSettingsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateSSOSettingsDto } from '../models/CreateSSOSettingsDto';
import type { ReadSSOSettingsDto } from '../models/ReadSSOSettingsDto';
import type { SSOSettingsListDto } from '../models/SSOSettingsListDto';
import type { SSOSettingsResponseDto } from '../models/SSOSettingsResponseDto';
import type { UpdateSSOSettingsDto } from '../models/UpdateSSOSettingsDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class SsoSettingsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List
   * @returns SSOSettingsListDto Successful Response
   * @throws ApiError
   */
public listApiV1SsosGet(): CancelablePromise<SSOSettingsListDto>
/**
   * Create Sso Settings
   * @returns SSOSettingsResponseDto Successful Response
   * @throws ApiError
   */
public createSsoSettingsApiV1SsosPost({
    requestBody,
  }: {
    requestBody: CreateSSOSettingsDto,
}): CancelablePromise<SSOSettingsResponseDto>
/**
   * Get Available Sso Providers
   * @returns any Successful Response
   * @throws ApiError
   */
public getAvailableSsoProvidersApiV1SsosAvailableProvidersGet(): CancelablePromise<Array<any>>
/**
   * Get Sso Settings By Id
   * @returns ReadSSOSettingsDto Successful Response
   * @throws ApiError
   */
public getSsoSettingsByIdApiV1SsosSsoIdGet({
    ssoId,
  }: {
    ssoId: string,
}): CancelablePromise<ReadSSOSettingsDto>
/**
   * Delete Sso Settings
   * @returns boolean Successful Response
   * @throws ApiError
   */
public deleteSsoSettingsApiV1SsosSsoIdDelete({
    ssoId,
  }: {
    ssoId: string,
}): CancelablePromise<boolean>
/**
   * Update Sso Settings
   * @returns void
   * @throws ApiError
   */
public updateSsoSettingsApiV1SsosSsoIdPatch({
    ssoId,
    requestBody,
  }: {
    ssoId: string,
    requestBody: UpdateSSOSettingsDto,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/StorageSettingsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AvailableStorageProviderDto } from '../models/AvailableStorageProviderDto';
import type { StorageSettingsDto } from '../models/StorageSettingsDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StorageSettingsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Storage Settings
   * @returns AvailableStorageProviderDto Successful Response
   * @throws ApiError
   */
public getStorageSettingsApiV1StorageGet(): CancelablePromise<Array<AvailableStorageProviderDto>>
/**
   * Get Available Providers
   * @returns string Successful Response
   * @throws ApiError
   */
public getAvailableProvidersApiV1StorageAvailableGet(): CancelablePromise<Array<Record<string, string>>>
/**
   * Configure Storage
   * @returns any Successful Response
   * @throws ApiError
   */
public configureStorageApiV1StorageConfigurePost({
    requestBody,
  }: {
    requestBody: StorageSettingsDto,
}): CancelablePromise<any>
/**
   * Reset Storage
   * @returns void
   * @throws ApiError
   */
public resetStorageApiV1StorageStorageIdResetPatch({
    storageId,
  }: {
    storageId: string,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/StripeBillingService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreatePlanDto } from '../models/CreatePlanDto';
import type { PlanDto } from '../models/PlanDto';
import type { PlanListDto } from '../models/PlanListDto';
import type { UpdatePlanDto } from '../models/UpdatePlanDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripeBillingService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List available plans
   * @returns PlanListDto Successful Response
   * @throws ApiError
   */
public listPlansApiV1BillingPlansGet(): CancelablePromise<PlanListDto>
/**
   * Create a new plan
   * @returns any Successful Response
   * @throws ApiError
   */
public createPlanApiV1BillingPlansPost({
    requestBody,
  }: {
    requestBody: CreatePlanDto,
}): CancelablePromise<any>
/**
   * Retrieve a plan details
   * @returns PlanDto Successful Response
   * @throws ApiError
   */
public getPlanApiV1BillingPlansPlanIdGet({
    planId,
  }: {
    planId: string,
}): CancelablePromise<PlanDto>
/**
   * Delete existing plan
   * @returns any Successful Response
   * @throws ApiError
   */
public deletePlanApiV1BillingPlansPlanIdDelete({
    planId,
  }: {
    planId: string,
}): CancelablePromise<any>
/**
   * Update a existing plan
   * @returns void
   * @throws ApiError
   */
public updatePlanApiV1BillingPlansPlanIdPatch({
    planId,
    requestBody,
  }: {
    planId: string,
    requestBody: UpdatePlanDto,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/StripeCheckoutService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BillingRecordListDto } from '../models/BillingRecordListDto';
import type { CheckoutRequestDto } from '../models/CheckoutRequestDto';
import type { CheckoutResponseDto } from '../models/CheckoutResponseDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripeCheckoutService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Create Host Checkout Session
   * @returns CheckoutResponseDto Successful Response
   * @throws ApiError
   */
public createHostCheckoutSessionApiV1CheckoutsHostTenantIdTenantPost({
    requestBody,
  }: {
    requestBody: CheckoutRequestDto,
}): CancelablePromise<CheckoutResponseDto>
/**
   * Create Tenant Checkout Session
   * @returns CheckoutResponseDto Successful Response
   * @throws ApiError
   */
public createTenantCheckoutSessionApiV1CheckoutsTenantSubscriptionPost({
    requestBody,
  }: {
    requestBody: CheckoutRequestDto,
}): CancelablePromise<CheckoutResponseDto>
/**
   * Host Checkout Success
   * @returns any Successful Response
   * @throws ApiError
   */
public hostCheckoutSuccessApiV1CheckoutsHostPaymentSuccessPost({
    sessionId,
    tenantId,
  }: {
    sessionId: string,
    tenantId: string,
}): CancelablePromise<any>
/**
   * Host Checkout Success
   * @returns any Successful Response
   * @throws ApiError
   */
public hostCheckoutSuccessApiV1CheckoutsHostPaymentCancelPost({
    tenantId,
  }: {
    tenantId: string,
}): CancelablePromise<any>
/**
   * Tenant Checkout Success
   * @returns any Successful Response
   * @throws ApiError
   */
public tenantCheckoutSuccessApiV1CheckoutsTenantPaymentSuccessPost({
    sessionId,
  }: {
    sessionId: string,
}): CancelablePromise<any>
/**
   * Tenant Checkout Canceled
   * @returns any Successful Response
   * @throws ApiError
   */
public tenantCheckoutCanceledApiV1CheckoutsTenantPaymentCancelPost(): CancelablePromise<any>
/**
   * List Checkout Records
   * @returns BillingRecordListDto Successful Response
   * @throws ApiError
   */
public listCheckoutRecordsApiV1CheckoutsAllGet({
    skip,
    limit = 100,
  }: {
    skip?: number,
    limit?: number,
}): CancelablePromise<BillingRecordListDto>
```

## File: frontend/app/src/api/services/StripeInvoicesService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InvoiceListDto } from '../models/InvoiceListDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripeInvoicesService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List invoices
   * @returns InvoiceListDto Successful Response
   * @throws ApiError
   */
public listInvoicesApiV1InvoicesGet(): CancelablePromise<InvoiceListDto>
```

## File: frontend/app/src/api/services/StripePricingService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreatePricingDto } from '../models/CreatePricingDto';
import type { PricingListDto } from '../models/PricingListDto';
import type { UpdatePricingDto } from '../models/UpdatePricingDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripePricingService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Pricing List
   * @returns PricingListDto Successful Response
   * @throws ApiError
   */
public getPricingListApiV1PricesGet(): CancelablePromise<PricingListDto>
/**
   * Create Pricing
   * @returns any Successful Response
   * @throws ApiError
   */
public createPricingApiV1PricesPost({
    requestBody,
  }: {
    requestBody: CreatePricingDto,
}): CancelablePromise<any>
/**
   * Update Pricing
   * @returns void
   * @throws ApiError
   */
public updatePricingApiV1PricesPriceIdPatch({
    priceId,
    requestBody,
  }: {
    priceId: string,
    requestBody: UpdatePricingDto,
}): CancelablePromise<void>
```

## File: frontend/app/src/api/services/StripeProductsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateProductDto } from '../models/CreateProductDto';
import type { ProductDto } from '../models/ProductDto';
import type { ProductListDto } from '../models/ProductListDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripeProductsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Products
   * @returns ProductListDto Successful Response
   * @throws ApiError
   */
public listProductsApiV1ProductsGet({
    active = true,
  }: {
    /**
     * Filter active products, defaults to True
     */
    active?: boolean,
}): CancelablePromise<ProductListDto>
⋮----
/**
     * Filter active products, defaults to True
     */
⋮----
/**
   * Create a Product
   * @returns any Successful Response
   * @throws ApiError
   */
public createProductApiV1ProductsPost({
    requestBody,
  }: {
    requestBody: CreateProductDto,
}): CancelablePromise<any>
/**
   * Get Product by ID
   * @returns ProductDto Successful Response
   * @throws ApiError
   */
public getProductByIdApiV1ProductsProductIdGet({
    productId,
  }: {
    productId: string,
}): CancelablePromise<ProductDto>
/**
   * Update a Product
   * @returns void
   * @throws ApiError
   */
public updateProductApiV1ProductsProductIdPatch({
    productId,
    requestBody,
  }: {
    productId: string,
    requestBody: CreateProductDto,
}): CancelablePromise<void>
/**
   * Delete a Product
   * @returns any Successful Response
   * @throws ApiError
   */
public deleteProductApiV1ProductsProductIdDelete({
    productId,
  }: {
    productId: string,
}): CancelablePromise<any>
```

## File: frontend/app/src/api/services/StripeService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateStripeSettingDto } from '../models/CreateStripeSettingDto';
import type { StripeSettingDto } from '../models/StripeSettingDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class StripeService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * Get Stripe Settings
   * @returns StripeSettingDto Successful Response
   * @throws ApiError
   */
public getStripeSettingsApiV1ConfigurationsStripeGet(): CancelablePromise<StripeSettingDto>
/**
   * Configure Stripe Setting
   * @returns any Successful Response
   * @throws ApiError
   */
public configureStripeSettingApiV1ConfigurationsStripePost({
    requestBody,
  }: {
    requestBody: CreateStripeSettingDto,
}): CancelablePromise<any>
```

## File: frontend/app/src/api/services/TenantsService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateTenantDto } from '../models/CreateTenantDto';
import type { CreateTenantResponseDto } from '../models/CreateTenantResponseDto';
import type { FeatureDto } from '../models/FeatureDto';
import type { SubdomainAvailabilityDto } from '../models/SubdomainAvailabilityDto';
import type { TenantDto } from '../models/TenantDto';
import type { TenantListDto } from '../models/TenantListDto';
import type { UpdateTenantDto } from '../models/UpdateTenantDto';
import type { UpdateTenantResponseDto } from '../models/UpdateTenantResponseDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class TenantsService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Tenants
   * @returns TenantListDto Successful Response
   * @throws ApiError
   */
public listTenantsApiV1TenantsGet({
    skip,
    limit = 10,
  }: {
    skip?: number,
    limit?: number,
}): CancelablePromise<TenantListDto>
/**
   * Create Tenant
   * @returns CreateTenantResponseDto Successful Response
   * @throws ApiError
   */
public createTenantApiV1TenantsPost({
    requestBody,
  }: {
    requestBody: CreateTenantDto,
}): CancelablePromise<CreateTenantResponseDto>
/**
   * Delete Tenant
   * @returns any Successful Response
   * @throws ApiError
   */
public deleteTenantApiV1TenantsIdDelete({
    id,
  }: {
    id: string,
}): CancelablePromise<any>
/**
   * Search By Name
   * @returns TenantDto Successful Response
   * @throws ApiError
   */
public searchByNameApiV1TenantsSearchByNameNameGet({
    name,
  }: {
    name: string,
}): CancelablePromise<TenantDto>
/**
   * Search By Subdomain
   * @returns TenantDto Successful Response
   * @throws ApiError
   */
public searchBySubdomainApiV1TenantsSearchBySubdomainSubdomainGet({
    subdomain,
  }: {
    subdomain: string,
}): CancelablePromise<TenantDto>
/**
   * Check Subdomain Availability
   * @returns SubdomainAvailabilityDto Successful Response
   * @throws ApiError
   */
public checkSubdomainAvailabilityApiV1TenantsCheckSubdomainSubdomainGet({
    subdomain,
  }: {
    subdomain: string,
}): CancelablePromise<SubdomainAvailabilityDto>
/**
   * Update Tenant Dns Record
   * When bringing your own domain you must map the DNS records for your custom domain to point to our main domain.
   * You can do this by adding a CNAME record in your domain's DNS settings.
   * Here is an example of how to set it up:
   * Type: CNAME
   * Name: app
   * Value: demo.dev.xyz
   * @returns UpdateTenantResponseDto Successful Response
   * @throws ApiError
   */
public updateTenantDnsRecordApiV1TenantsTenantIdUpdateDnsPost({
    tenantId,
    requestBody,
  }: {
    tenantId: string,
    requestBody: UpdateTenantDto,
}): CancelablePromise<UpdateTenantResponseDto>
/**
   * Check Dns Status
   * @returns UpdateTenantResponseDto Successful Response
   * @throws ApiError
   */
public checkDnsStatusApiV1TenantsTenantIdCheckDnsGet({
    tenantId,
  }: {
    tenantId: string,
}): CancelablePromise<UpdateTenantResponseDto>
/**
   * Update Tenant
   * @returns void
   * @throws ApiError
   */
public updateTenantApiV1TenantsTenantIdPut({
    tenantId,
    requestBody,
  }: {
    tenantId: string,
    requestBody: UpdateTenantDto,
}): CancelablePromise<void>
/**
   * Get Tenant Features
   * @returns FeatureDto Successful Response
   * @throws ApiError
   */
public getTenantFeaturesApiV1TenantsTenantIdFeaturesGet({
    tenantId,
  }: {
    tenantId: string,
}): CancelablePromise<Array<FeatureDto>>
/**
   * Update Tenant Feature
   * @returns UpdateTenantResponseDto Successful Response
   * @throws ApiError
   */
public updateTenantFeatureApiV1TenantsTenantIdUpdateFeaturePatch({
    tenantId,
    requestBody,
  }: {
    tenantId: string,
    requestBody: FeatureDto,
}): CancelablePromise<UpdateTenantResponseDto>
/**
   * Activate Trial Plan
   * @returns UpdateTenantResponseDto Successful Response
   * @throws ApiError
   */
public activateTrialPlanApiV1TenantsTenantIdActivateTrialPlanPatch({
    tenantId,
  }: {
    tenantId: string,
}): CancelablePromise<UpdateTenantResponseDto>
```

## File: frontend/app/src/api/services/UsersService.ts

```typescript
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_update_profile_picture_api_v1_users__user_id__update_profile_picture_put } from '../models/Body_update_profile_picture_api_v1_users__user_id__update_profile_picture_put';
import type { CreateUserDto } from '../models/CreateUserDto';
import type { CreateUserResponseDto } from '../models/CreateUserResponseDto';
import type { UpdateUserDto } from '../models/UpdateUserDto';
import type { UserDto } from '../models/UserDto';
import type { UserListDto } from '../models/UserListDto';
import type { UserProfileImageUpdateDto } from '../models/UserProfileImageUpdateDto';
import type { UserRoleUpdateRequestDto } from '../models/UserRoleUpdateRequestDto';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class UsersService
⋮----
constructor(public readonly httpRequest: BaseHttpRequest)
/**
   * List Users
   * @returns UserListDto Successful Response
   * @throws ApiError
   */
public listUsersApiV1UsersGet({
    skip,
    limit = 10,
  }: {
    skip?: number,
    limit?: number,
}): CancelablePromise<UserListDto>
/**
   * Create User
   * @returns CreateUserResponseDto Successful Response
   * @throws ApiError
   */
public createUserApiV1UsersPost({
    requestBody,
  }: {
    requestBody: CreateUserDto,
}): CancelablePromise<CreateUserResponseDto>
/**
   * Get User
   * @returns UserDto Successful Response
   * @throws ApiError
   */
public getUserApiV1UsersUserIdGet({
    userId,
  }: {
    userId: string,
}): CancelablePromise<UserDto>
/**
   * Update User
   * @returns UserDto Successful Response
   * @throws ApiError
   */
public updateUserApiV1UsersUserIdPut({
    userId,
    requestBody,
  }: {
    userId: string,
    requestBody: UpdateUserDto,
}): CancelablePromise<UserDto>
/**
   * Delete User
   * @returns any Successful Response
   * @throws ApiError
   */
public deleteUserApiV1UsersUserIdDelete({
    userId,
  }: {
    userId: string,
}): CancelablePromise<any>
/**
   * Get Profile Image
   * @returns UserProfileImageUpdateDto Successful Response
   * @throws ApiError
   */
public getProfileImageApiV1UsersProfileImageKeyGet({
    imageKey,
  }: {
    imageKey: string,
}): CancelablePromise<UserProfileImageUpdateDto>
/**
   * Update Profile Picture
   * @returns UserProfileImageUpdateDto Successful Response
   * @throws ApiError
   */
public updateProfilePictureApiV1UsersUserIdUpdateProfilePicturePut({
    userId,
    formData,
  }: {
    userId: string,
    formData: Body_update_profile_picture_api_v1_users__user_id__update_profile_picture_put,
}): CancelablePromise<UserProfileImageUpdateDto>
/**
   * Patch User
   * @returns UserDto Successful Response
   * @throws ApiError
   */
public patchUserApiV1UsersUserIdAssignRolePatch({
    userId,
    requestBody,
  }: {
    userId: string,
    requestBody: UserRoleUpdateRequestDto,
}): CancelablePromise<UserDto>
/**
   * Remove User Role
   * @returns UserDto Successful Response
   * @throws ApiError
   */
public removeUserRoleApiV1UsersUserIdRemoveRolePatch({
    userId,
    requestBody,
  }: {
    userId: string,
    requestBody: UserRoleUpdateRequestDto,
}): CancelablePromise<UserDto>
```

## File: frontend/app/src/App.tsx

```typescript
import { Routes, Route, Navigate } from "react-router";
import { DefaultLayout } from "@/components/layouts/default-layout";
import { Login } from "@/components/features/auth/login";
import { Dashboard } from "@/components/features/dashboard/dashboard";
import { DashboardLayout } from "@/components/layouts/dashboard-layout";
import { Profile } from "@/components/features/profile/profile";
import { Roles } from "@/components/features/roles/roles";
import { Users } from "@/components/features/users/users";
import Register from "@/components/features/auth/register";
import { UsersProvider } from "@/components/providers/users-provider";
import { RolesProvider } from "@/components/providers/roles-provider";
import { AIChat } from "@/components/features/ai-chat/ai-chat";
import { TenantsProvider } from "@/components/providers/tenant-provider";
import { Tenants } from "@/components/features/tenant/tenant";
import { SettingsProvider } from "@/components/providers/settings-provider";
import { Settings } from "@/components/features/settings/settings";
import { useAuthContext } from "./components/providers/auth-provider";
import { AIChatProvider } from "@/components/providers/ai-chat-provider";
import { PasswordResetRequest } from "@/components/features/auth/password-reset-request";
import { PasswordResetConfirmation } from "@/components/features/auth/password_reset_confirmation";
import { Activation } from "@/components/features/auth/activation";
import { TenantSetting } from "@/components/features/tenant/tenant-setting";
import { MagicLinkLoginValidate } from "@/components/features/auth/magic-link-login-validate";
import { NonActiveTenantView } from "@/components/shared/non-active-tenant-view";
import { StripeProvider } from "@/components/providers/stripe-provider";
import { ConfigureStripe } from "@/components/features/billings/stripe/configure-stripe";
import { BillingOverview } from "@/components/features/billings/stripe/billing-overview";
import { Billing } from "@/components/features/billings/stripe/billing";
import { Invoices } from "@/components/features/billings/stripe/invoices";
import { Products } from "@/components/features/billings/stripe/products";
import { CheckoutOverview } from "@/components/features/billings/stripe/checkout-overview";
import { AppSettings } from "@/components/features/settings/app-settings";
import { AuditLogProvider } from "@/components/providers/audit-log-provider";
import { AuditLogs } from "@/components/features/audit-logs/audit-logs";
import { SettingsLayout } from "./components/features/settings/settings-layout";
```

## File: frontend/app/src/assets/react.svg

```xml
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" role="img" class="iconify iconify--logos" width="35.93" height="32" preserveAspectRatio="xMidYMid meet" viewBox="0 0 256 228"><path fill="#00D8FF" d="M210.483 73.824a171.49 171.49 0 0 0-8.24-2.597c.465-1.9.893-3.777 1.273-5.621c6.238-30.281 2.16-54.676-11.769-62.708c-13.355-7.7-35.196.329-57.254 19.526a171.23 171.23 0 0 0-6.375 5.848a155.866 155.866 0 0 0-4.241-3.917C100.759 3.829 77.587-4.822 63.673 3.233C50.33 10.957 46.379 33.89 51.995 62.588a170.974 170.974 0 0 0 1.892 8.48c-3.28.932-6.445 1.924-9.474 2.98C17.309 83.498 0 98.307 0 113.668c0 15.865 18.582 31.778 46.812 41.427a145.52 145.52 0 0 0 6.921 2.165a167.467 167.467 0 0 0-2.01 9.138c-5.354 28.2-1.173 50.591 12.134 58.266c13.744 7.926 36.812-.22 59.273-19.855a145.567 145.567 0 0 0 5.342-4.923a168.064 168.064 0 0 0 6.92 6.314c21.758 18.722 43.246 26.282 56.54 18.586c13.731-7.949 18.194-32.003 12.4-61.268a145.016 145.016 0 0 0-1.535-6.842c1.62-.48 3.21-.974 4.76-1.488c29.348-9.723 48.443-25.443 48.443-41.52c0-15.417-17.868-30.326-45.517-39.844Zm-6.365 70.984c-1.4.463-2.836.91-4.3 1.345c-3.24-10.257-7.612-21.163-12.963-32.432c5.106-11 9.31-21.767 12.459-31.957c2.619.758 5.16 1.557 7.61 2.4c23.69 8.156 38.14 20.213 38.14 29.504c0 9.896-15.606 22.743-40.946 31.14Zm-10.514 20.834c2.562 12.94 2.927 24.64 1.23 33.787c-1.524 8.219-4.59 13.698-8.382 15.893c-8.067 4.67-25.32-1.4-43.927-17.412a156.726 156.726 0 0 1-6.437-5.87c7.214-7.889 14.423-17.06 21.459-27.246c12.376-1.098 24.068-2.894 34.671-5.345a134.17 134.17 0 0 1 1.386 6.193ZM87.276 214.515c-7.882 2.783-14.16 2.863-17.955.675c-8.075-4.657-11.432-22.636-6.853-46.752a156.923 156.923 0 0 1 1.869-8.499c10.486 2.32 22.093 3.988 34.498 4.994c7.084 9.967 14.501 19.128 21.976 27.15a134.668 134.668 0 0 1-4.877 4.492c-9.933 8.682-19.886 14.842-28.658 17.94ZM50.35 144.747c-12.483-4.267-22.792-9.812-29.858-15.863c-6.35-5.437-9.555-10.836-9.555-15.216c0-9.322 13.897-21.212 37.076-29.293c2.813-.98 5.757-1.905 8.812-2.773c3.204 10.42 7.406 21.315 12.477 32.332c-5.137 11.18-9.399 22.249-12.634 32.792a134.718 134.718 0 0 1-6.318-1.979Zm12.378-84.26c-4.811-24.587-1.616-43.134 6.425-47.789c8.564-4.958 27.502 2.111 47.463 19.835a144.318 144.318 0 0 1 3.841 3.545c-7.438 7.987-14.787 17.08-21.808 26.988c-12.04 1.116-23.565 2.908-34.161 5.309a160.342 160.342 0 0 1-1.76-7.887Zm110.427 27.268a347.8 347.8 0 0 0-7.785-12.803c8.168 1.033 15.994 2.404 23.343 4.08c-2.206 7.072-4.956 14.465-8.193 22.045a381.151 381.151 0 0 0-7.365-13.322Zm-45.032-43.861c5.044 5.465 10.096 11.566 15.065 18.186a322.04 322.04 0 0 0-30.257-.006c4.974-6.559 10.069-12.652 15.192-18.18ZM82.802 87.83a323.167 323.167 0 0 0-7.227 13.238c-3.184-7.553-5.909-14.98-8.134-22.152c7.304-1.634 15.093-2.97 23.209-3.984a321.524 321.524 0 0 0-7.848 12.897Zm8.081 65.352c-8.385-.936-16.291-2.203-23.593-3.793c2.26-7.3 5.045-14.885 8.298-22.6a321.187 321.187 0 0 0 7.257 13.246c2.594 4.48 5.28 8.868 8.038 13.147Zm37.542 31.03c-5.184-5.592-10.354-11.779-15.403-18.433c4.902.192 9.899.29 14.978.29c5.218 0 10.376-.117 15.453-.343c-4.985 6.774-10.018 12.97-15.028 18.486Zm52.198-57.817c3.422 7.8 6.306 15.345 8.596 22.52c-7.422 1.694-15.436 3.058-23.88 4.071a382.417 382.417 0 0 0 7.859-13.026a347.403 347.403 0 0 0 7.425-13.565Zm-16.898 8.101a358.557 358.557 0 0 1-12.281 19.815a329.4 329.4 0 0 1-23.444.823c-7.967 0-15.716-.248-23.178-.732a310.202 310.202 0 0 1-12.513-19.846h.001a307.41 307.41 0 0 1-10.923-20.627a310.278 310.278 0 0 1 10.89-20.637l-.001.001a307.318 307.318 0 0 1 12.413-19.761c7.613-.576 15.42-.876 23.31-.876H128c7.926 0 15.743.303 23.354.883a329.357 329.357 0 0 1 12.335 19.695a358.489 358.489 0 0 1 11.036 20.54a329.472 329.472 0 0 1-11 20.722Zm22.56-122.124c8.572 4.944 11.906 24.881 6.52 51.026c-.344 1.668-.73 3.367-1.15 5.09c-10.622-2.452-22.155-4.275-34.23-5.408c-7.034-10.017-14.323-19.124-21.64-27.008a160.789 160.789 0 0 1 5.888-5.4c18.9-16.447 36.564-22.941 44.612-18.3ZM128 90.808c12.625 0 22.86 10.235 22.86 22.86s-10.235 22.86-22.86 22.86s-22.86-10.235-22.86-22.86s10.235-22.86 22.86-22.86Z"></path></svg>
```

## File: frontend/app/src/components/features/ai-chat/ai-chat-history.tsx

```typescript
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Menu } from 'lucide-react'
import { DropdownMenu, DropdownMenuItem, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { NavLink, useNavigate, useSearchParams } from "react-router"
import { useAIChat } from "@/components/providers/ai-chat-provider"
import { ScrollArea } from "@/components/ui/scroll-area"
import { toast } from "sonner"
interface AIChatHistoryProps {
    mobile?: boolean
}
export function AIChatHistory(
⋮----
function getTitle(historyId: string)
async function onDelete(sessionId: string)
⋮----
navigate(`?session_id=$
⋮----
"bg-primary/5": history.session_id === searchParams.get("session_id")
⋮----
<DropdownMenuItem onClick=
```

## File: frontend/app/src/components/features/ai-chat/ai-chat.tsx

```typescript
import React, { useState, useRef, useEffect, useTransition } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Loading } from '@/components/shared/loading'
import { cn, getApiClient, getTenant } from '@/lib/utils'
import { Send, Bot, User, Sparkles, Copy, RefreshCw, Trash2, InfoIcon } from 'lucide-react'
import { useAuthContext } from '@/components/providers/auth-provider'
import { ListLocalAIModels } from '@/components/shared/list-local-ai-models'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useAppConfig } from '@/components/providers/app-config-provider'
import { toast } from 'sonner'
import { AIChatHistory } from './ai-chat-history'
import { NavLink, useSearchParams } from 'react-router'
import { useAIChat } from '@/components/providers/ai-chat-provider'
import { AIAskRequestDto, AIModelInfoDto } from '@/api'
interface Message {
    id: string
    content: string
    role: 'user' | 'assistant'
    timestamp: Date
    isStreaming?: boolean
}
interface ChatState {
    messages: Message[]
    isLoading: boolean
    error: string | null
}
⋮----
// Auto-resize textarea
const adjustTextareaHeight = () =>
const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
// Auto-scroll to bottom when new messages arrive or content changes
const scrollToBottom = () =>
// fetch histories
async function fetchHistories(sessionId: string)
async function fetchAvailableModels()
// Focus input on mount
⋮----
function handleScroll()
⋮----
// Also scroll when streaming content changes
⋮----
// Load preferred model from user preferences
⋮----
async function fetchSelectedSessionHistory()
⋮----
const generateId = ()
const sendMessage = async () =>
⋮----
// Reset textarea height
⋮----
// Mark streaming as complete
⋮----
const clearChat = () =>
const copyMessage = (content: string) =>
const retryLastMessage = () =>
⋮----
// Adjust textarea height after setting content
⋮----
// Remove the last assistant message if it exists
⋮----
const handleKeyPress = (e: React.KeyboardEvent) =>
const onSelectModelEvent = async (model: AIModelInfoDto) =>
⋮----
{/* Page Header */}
⋮----
{/* Mobile Model Selection */}
⋮----
{/* Chat Container */}
{/** Mobile view chat history */}
⋮----
{/* Chat Messages */}
⋮----
setInput(suggestion)
setTimeout(adjustTextareaHeight, 0)
⋮----
{/* Message actions */}
⋮----
{/* Invisible section for scrolling to bottom */}
⋮----
{/* Error Display */}
⋮----
{/* Input Area */}
```

## File: frontend/app/src/components/features/audit-logs/audit-logs.tsx

```typescript
import { useAuditLogsContext } from "@/components/providers/audit-log-provider";
import { PageHeader } from "@/components/shared/page-header";
import { IconDownload } from "@tabler/icons-react";
import { AuditTable } from "./audit-table";
⋮----
async function downloadAuditLogs()
```

## File: frontend/app/src/components/features/audit-logs/audit-table.tsx

```typescript
import { AuditLogType } from "@/components/providers/audit-log-provider";
import AdvanceTable from "@/components/shared/advance-table";
import { IResponseData } from "@/components/shared/iresponse-data.inteface";
import { createColumnHelper } from "@tanstack/react-table";
⋮----
interface AuditTableProps {
    logs: IResponseData<AuditLogType>;
    loading?: boolean;
    errorMsg?: string;
}
export function AuditTable(
```

## File: frontend/app/src/components/features/auth/activation.tsx

```typescript
import { Loading } from "@/components/shared/loading";
import { getApiClient } from "@/lib/utils";
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
export function Activation()
⋮----
async function activate()
```

## File: frontend/app/src/components/features/auth/login.tsx

```typescript
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NavLink } from "react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Logo } from "@/components/shared/logo";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { TenantSelection } from "../tenant/tenant-selection";
import { useAuthContext } from "@/components/providers/auth-provider";
import { PasskeyLogin } from "@/components/features/auth/passkey-login";
import { Separator } from "@/components/ui/separator";
import {
    Shield,
} from "lucide-react";
import { MagicLinkLogin } from "./magic-link-login";
import { SSOLoginDialog } from "./sso-login-dialog";
⋮----
type LoginFormInputs = z.infer<typeof loginSchema>;
⋮----
const onSubmit = async (data: LoginFormInputs) =>
⋮----
<form onSubmit=
⋮----
{/* Other Login Options */}
⋮----
{/* Separator */}
⋮----
{/* Security Notice */}
```

## File: frontend/app/src/components/features/auth/magic-link-login-validate.tsx

```typescript
import { Loading } from "@/components/shared/loading";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { getApiClient } from "@/lib/utils";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
export function MagicLinkLoginValidate()
⋮----
async function validateMagicLink()
```

## File: frontend/app/src/components/features/auth/magic-link-login.tsx

```typescript
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
    Form,
    FormField,
    FormItem,
    FormLabel,
    FormControl,
    FormMessage,
    FormDescription
} from "@/components/ui/form";
import { toast } from "sonner";
import { Mail, Send, CheckCircle, ArrowLeft } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { getApiClient } from "@/lib/utils";
⋮----
type MagicLinkFormData = z.infer<typeof magicLinkSchema>;
⋮----
async function sendEmailLink(email: string)
const onSubmit = async (data: MagicLinkFormData) =>
const handleResendEmail = async () =>
const backToLogin = () =>
const closeDialog = () =>
⋮----
<form onSubmit=
```

## File: frontend/app/src/components/features/auth/passkey-login.tsx

```typescript
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import {
    Fingerprint,
    Shield,
    Mail
} from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { getApiClient } from "@/lib/utils";
import { startRegistration, startAuthentication, WebAuthnError } from "@simplewebauthn/browser";
import { useNavigate } from "react-router";
import { useAuthContext } from "@/components/providers/auth-provider";
⋮----
type PasskeyEmailForm = z.infer<typeof passkeyEmailSchema>;
⋮----
const handlePasskeyClick = () =>
async function loginFlow(email: string)
async function registerFlow(email: string)
const handleEmailSubmit = async (data: PasskeyEmailForm) =>
const handleDialogClose = () =>
⋮----
{/* Passkey Authentication */}
⋮----
{/* Email Dialog for Passkey */}
⋮----
<form onSubmit=
```

## File: frontend/app/src/components/features/auth/password_reset_confirmation.tsx

```typescript
import { useState } from 'react';
import { NavLink, useNavigate, useSearchParams } from 'react-router'
import z from 'zod';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Logo } from "@/components/shared/logo";
import { getApiClient } from "@/lib/utils";
import { toast } from "sonner";
⋮----
type PasswordResetConfirmationFormInputs = z.infer<typeof passwordResetConfirmationSchema>;
⋮----
const onSubmit = async (data: PasswordResetConfirmationFormInputs) =>
⋮----
<form onSubmit=
```

## File: frontend/app/src/components/features/auth/password-reset-request.tsx

```typescript
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NavLink } from "react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Logo } from "@/components/shared/logo";
import { getApiClient } from "@/lib/utils";
import { toast } from "sonner";
⋮----
type passwordResetSchemaInput = z.infer<typeof passwordResetSchema>;
⋮----
const onSubmit = async (data: passwordResetSchemaInput) =>
⋮----
<form onSubmit=
```

## File: frontend/app/src/components/features/auth/register.tsx

```typescript
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { NavLink } from "react-router";
import { Logo } from "@/components/shared/logo";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { Label } from "@/components/ui/label";
import { useSubdomainCheck } from "@/hooks/use-subdomain-check";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useAuthContext } from "@/components/providers/auth-provider";
import { getTenant } from "@/lib/utils";
// Gender enum matching the backend
⋮----
// Zod schema matching the SignupDto validation
⋮----
type SignupFormInputs = z.infer<typeof signupSchema>;
⋮----
const onSubmit = async (data: SignupFormInputs) =>
⋮----
<form onSubmit=
{/* First Name */}
⋮----
{/* Last Name */}
⋮----
{/* Email */}
⋮----
{/* Gender */}
⋮----
{/* Password */}
⋮----
{/* Confirm Password */}
```

## File: frontend/app/src/components/features/auth/sso-login-dialog.tsx

```typescript
import { useAppConfig } from "@/components/providers/app-config-provider";
import { providerConfig } from "@/components/features/sso-configurations/sso-provider-configuration";
import { Button } from "@/components/ui/button";
import { IconBrandAuth0 } from "@tabler/icons-react";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
```

## File: frontend/app/src/components/features/billings/stripe/add-a-new-plan.tsx

```typescript
import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Plus, RefreshCw, BadgeEuro, Calendar, Package } from "lucide-react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { getApiClient } from "@/lib/utils";
import { ProductDto, CreatePlanDto } from "@/api";
⋮----
type PlanForm = z.infer<typeof planSchema>;
interface AddANewPlanProps {
    onPlanCreated?: () => void;
}
⋮----
const fetchProducts = async () =>
⋮----
const formatPreviewPrice = (amountInCents: number, currency: string) =>
const onSubmit = async (data: PlanForm) =>
const handleOpenChange = (open: boolean) =>
⋮----
<form onSubmit=
⋮----
{/* Product Selection */}
⋮----
{/* Amount and Currency */}
⋮----
{/* Billing Interval */}
⋮----
{/* Price Preview */}
⋮----
{/* Info Box */}
```

## File: frontend/app/src/components/features/billings/stripe/billing-overview.tsx

```typescript
import { useEffect, useState } from "react";
import { useStripeProvider } from "@/components/providers/stripe-provider";
import { useAuthContext } from "@/components/providers/auth-provider";
import { formatPrice, getApiClient } from "@/lib/utils";
import { PlanDto, ProductDto } from "@/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
    CreditCard,
    Calendar,
    TrendingUp,
    Package,
    RefreshCw,
    CheckCircle2,
    XCircle,
    Zap,
} from "lucide-react";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
import { ConfigureStripeNow } from "@/components/features/billings/stripe/configure-stripe-now";
import { AddANewPlan } from "./add-a-new-plan";
import { toast } from "sonner";
⋮----
const fetchPlans = async () =>
⋮----
// This is to handle cases where there is no tenant (Host Mode)
⋮----
const handleTrialToggle = async (planId: string, currentTrialDays: number | null | undefined) =>
const formatInterval = (interval: string, count: number) =>
⋮----
// This check is to prompt user to configure Stripe if there is a configuration error for the tenant
⋮----
{/* Header */}
⋮----
{/* Available Plans */}
⋮----
{/* Trial Toggle */}
⋮----
{/* Quick Stats */}
```

## File: frontend/app/src/components/features/billings/stripe/billing.tsx

```typescript
import { StripeProvider } from "@/components/providers/stripe-provider";
import { Tabs, TabsTrigger } from "@/components/ui/tabs";
import { TabsContent, TabsList } from "@radix-ui/react-tabs";
import { NavLink, Outlet, useLocation } from "react-router";
export function Billing();
```

## File: frontend/app/src/components/features/billings/stripe/checkout-canceled.tsx

```typescript
export function CheckoutCanceled();
```

## File: frontend/app/src/components/features/billings/stripe/checkout-overview.tsx

```typescript
import { useEffect, useState } from "react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { formatDateWithHourAndMinute, formatPrice, getApiClient } from "@/lib/utils";
import { BillingRecordDto } from "@/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
    ShoppingCart,
    RefreshCw,
    CheckCircle2,
    Clock,
    XCircle,
    AlertTriangle,
    User,
    Calendar,
    CreditCard,
    TrendingUp,
    Package,
    ArrowLeft,
    ArrowRight,
} from "lucide-react";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
import { useLocation, useNavigate } from "react-router";
⋮----
const fetchCheckoutRecords = async (skip: number = 0) =>
⋮----
const handlePrevious = () =>
const handleNext = () =>
const getStatusBadge = (status: string) =>
const getScopeBadge = (scope: string) =>
const getPaymentTypeBadge = (type: string) =>
⋮----
{/* Header */}
⋮----
<Button onClick=
⋮----
{/* Summary Cards */}
⋮----
{/* Records Table */}
⋮----
{/* Pagination */}
```

## File: frontend/app/src/components/features/billings/stripe/checkout-success.tsx

```typescript
export function CheckoutSuccess();
```

## File: frontend/app/src/components/features/billings/stripe/configure-stripe-now.tsx

```typescript
import { Alert, AlertDescription } from "@/components/ui/alert";
import { NavLink } from "react-router";
import { IconReceiptEuro } from "@tabler/icons-react";
export function ConfigureStripeNow();
```

## File: frontend/app/src/components/features/billings/stripe/configure-stripe.tsx

```typescript
import { useStripeProvider } from "@/components/providers/stripe-provider";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
    CreditCard,
    Key,
    Webhook,
    DollarSign,
    Calendar,
    Settings,
    CheckCircle2,
    AlertTriangle,
    Eye,
    EyeOff,
    RefreshCw
} from "lucide-react";
import { IconExternalLink } from "@tabler/icons-react";
import { useState } from "react";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { useAuthContext } from "@/components/providers/auth-provider";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
⋮----
type StripeConfigForm = z.infer<typeof stripeConfigSchema>;
⋮----
const onSubmit = async (data: StripeConfigForm) =>
⋮----
{/* Header Section */}
⋮----
{/* Current Configuration Display */}
⋮----
{/* API Credentials Section */}
⋮----
{/* Payment Configuration Section */}
⋮----

⋮----
{/* Security Notice */}
⋮----
{/* Action Buttons */}
⋮----
{/* Help Section */}
```

## File: frontend/app/src/components/features/billings/stripe/invoices.tsx

```typescript
import { useEffect, useState } from "react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { formatDate, formatPrice, getApiClient } from "@/lib/utils";
import { InvoiceDto } from "@/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
    Calendar,
    User,
    FileText,
    RefreshCw,
    Download,
    CheckCircle2,
    Clock,
    XCircle,
    AlertCircle,
} from "lucide-react";
import { IconCurrencyEuro, IconReceiptEuro } from "@tabler/icons-react"
import { useStripeProvider } from "@/components/providers/stripe-provider";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
import { ConfigureStripeNow } from "./configure-stripe-now";
⋮----
const fetchInvoices = async () =>
⋮----
// This is to handle cases where there is no tenant (Host Mode)
⋮----
const getStatusBadge = (status: string) =>
⋮----
// This check is to prompt user to configure Stripe if there is a configuration error for the tenant
⋮----
{/* Header */}
⋮----
{/* Summary Cards */}
⋮----
{/* Invoices Table */}
⋮----
Paid:
```

## File: frontend/app/src/components/features/billings/stripe/products.tsx

```typescript
import { useEffect, useState } from "react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { getApiClient } from "@/lib/utils";
import { ProductDto, CreateProductDto } from "@/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import {
    Package,
    Plus,
    RefreshCw,
    Edit,
    Trash2,
    CheckCircle2,
    XCircle,
    Tag,
} from "lucide-react";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
import { useStripeProvider } from "@/components/providers/stripe-provider";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { ConfigureStripeNow } from "./configure-stripe-now";
⋮----
type ProductForm = z.infer<typeof productSchema>;
⋮----
const fetchProducts = async () =>
⋮----
// This is to handle cases where there is no tenant (Host Mode)
⋮----
const openCreateDialog = () =>
const openEditDialog = (product: ProductDto) =>
const onSubmit = async (data: ProductForm) =>
const handleDelete = async () =>
⋮----
// This check is to prompt user to configure Stripe if there is a configuration error for the tenant
⋮----
{/* Header */}
⋮----
<form onSubmit=
⋮----

⋮----
onCheckedChange=
⋮----
{/* Filter */}
⋮----
{/* Products Grid */}
⋮----
onClick=
⋮----
{/* Delete Confirmation Dialog */}
```

## File: frontend/app/src/components/features/dark-mode/dark-mode.tsx

```typescript
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTheme } from "@/components/providers/theme-provider";
export function DarkMode();
```

## File: frontend/app/src/components/features/dashboard/all-cards.tsx

```typescript
import { IconTrendingDown, IconTrendingUp } from "@tabler/icons-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
export function AllCards();
```

## File: frontend/app/src/components/features/dashboard/dashboard.tsx

```typescript
import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
} from "recharts";
import { DashboardMetricsDto } from "@/api";
import { Loading } from "@/components/shared/loading";
import { getApiClient } from "@/lib/utils";
import { AllCards } from "./all-cards";
import { useAuthContext } from "@/components/providers/auth-provider";
⋮----
async function fetchData()
⋮----
// window.location.reload();
// await refreshCurrentUser();
⋮----
stroke="#4f46e5" // Indigo-600
```

## File: frontend/app/src/components/features/profile/profile.tsx

```typescript
import React, { useState, useRef, useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
⋮----
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage
} from '@/components/ui/form'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import {
    User,
    Mail,
    Camera,
    Save,
    Upload,
    CheckCircle,
    AlertCircle,
    UserCircle,
    Shield,
    Calendar,
    Edit3,
    X,
} from 'lucide-react'
import { useAuthContext } from '@/components/providers/auth-provider'
import { toast } from 'sonner'
import { getApiClient, getTenant } from '@/lib/utils'
import { useAppConfig } from '@/components/providers/app-config-provider'
import { ManageSecurity } from '@/components/shared/manage-security'
// Form validation schema
⋮----
type ProfileFormData = z.infer<typeof profileSchema>
⋮----
// Update form values when user data is available or when entering edit mode
⋮----
// Reset form values when entering edit mode
⋮----
const handleImageUpload = async (event: React.ChangeEvent<HTMLInputElement>) =>
⋮----
// Validate file type
⋮----
// Validate file size (5MB limit)
⋮----
// Show preview
⋮----
// Upload image
⋮----
const onSubmit = async (data: ProfileFormData) =>
const getGenderDisplay = (gender: string) =>
const getUserInitials = (firstName?: string, lastName?: string) =>
⋮----
{/* Header */}
⋮----
<Button onClick=
⋮----
{/* Profile Image Card */}
⋮----
{/* Profile Information Card */}
⋮----
<form onSubmit=
⋮----
{/* Account Status Card */}
```

## File: frontend/app/src/components/features/roles/create-new-role-dialog.tsx

```typescript
import { Button } from "@/components/ui/button";
import { DialogHeader, DialogFooter, Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
    Form, FormField, FormLabel, FormControl, FormItem, FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useState } from "react";
import { toast } from "sonner";
import { useRoles } from "@/components/providers/roles-provider";
import { Textarea } from "@/components/ui/textarea";
⋮----
type CreateNewRoleFormInputs = z.infer<typeof createNewRoleSchema>;
interface CreateNewRoleDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
const onSubmit = async (data: CreateNewRoleFormInputs) =>
function onDismissDialog(flag: boolean)
⋮----
<form onSubmit=
{/* Name */}
⋮----
{/* Description */}
```

## File: frontend/app/src/components/features/roles/role-delete-dialog.tsx

```typescript
import { useRoles } from "@/components/providers/roles-provider";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
interface DeleteNewRoleDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
function onDismissHandler(flag: boolean)
```

## File: frontend/app/src/components/features/roles/role-edit-dialog.tsx

```typescript
import { Button } from "@/components/ui/button";
import { DialogHeader, DialogFooter, Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
    Form, FormField, FormLabel, FormControl, FormItem, FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useState } from "react";
import { toast } from "sonner";
import { useRoles } from "@/components/providers/roles-provider";
import { Textarea } from "@/components/ui/textarea";
⋮----
type EditRoleFormInputs = z.infer<typeof editNewRoleSchema>;
interface RoleEditDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
const onSubmit = async (data: EditRoleFormInputs) =>
function onDismissDialog(flag: boolean)
⋮----
<form onSubmit=
{/* Name */}
⋮----
{/* Description */}
```

## File: frontend/app/src/components/features/roles/role-manage-permission-dialog.tsx

```typescript
import { Permission } from "@/api";
import { useRoles } from "@/components/providers/roles-provider";
import { ListPermission } from "@/components/shared/list-permission";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { useState } from "react";
import { toast } from "sonner";
interface RoleManagePermissionDialogProps {
    open: boolean;
    onDismiss: () => void;
}
export function RoleManagePermissionDialog(
⋮----
function onDismissHandler(flag: boolean)
⋮----
async function onSaveChanges()
```

## File: frontend/app/src/components/features/roles/role-table.tsx

```typescript
import { Permission } from "@/api";
import { useAuthContext } from "@/components/providers/auth-provider";
import { RolesType, useRoles } from "@/components/providers/roles-provider";
import AdvanceTable from "@/components/shared/advance-table";
import { IResponseData } from "@/components/shared/iresponse-data.inteface";
import { ActionOption, TableActions } from "@/components/shared/table-actions";
import { Badge } from "@/components/ui/badge";
import { createColumnHelper } from "@tanstack/react-table";
⋮----
// Add "Clone" option only for non admin users
⋮----
interface RoleTableProps {
    roleResponse: IResponseData<RolesType>;
    loading?: boolean;
}
export function RoleTable(
```

## File: frontend/app/src/components/features/roles/roles.tsx

```typescript
import { useRoles } from "@/components/providers/roles-provider";
import { PageHeader } from "@/components/shared/page-header";
import { useState } from "react";
import { RoleTable } from "@/components/features/roles/role-table";
import { CreateNewRoleDialog } from "@/components/features/roles/create-new-role-dialog";
import { RoleDeleteDialog } from "@/components/features/roles/role-delete-dialog";
import { RoleEditDialog } from "@/components/features/roles/role-edit-dialog";
import { RoleManagePermissionDialog } from "@/components/features/roles/role-manage-permission-dialog";
import { useAuthContext } from "@/components/providers/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { InfoIcon } from 'lucide-react'
⋮----
const onCreateNewRoleDismissHandler = () =>
const onRoleEditDismissHandler = () =>
```

## File: frontend/app/src/components/features/settings/app-settings.tsx

```typescript
import { useLocation } from "react-router";
import { NotificationBanner } from "@/components/features/settings/notification-banner";
import { SSOProviderConfiguration } from "@/components/features/sso-configurations/sso-provider-configuration";
import SSOConfigurationProvider from "@/components/providers/sso-configuration-provider";
export function AppSettings();
```

## File: frontend/app/src/components/features/settings/notification-banner.tsx

```typescript
import { useEffect, useState } from "react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { extractErrorMessage, getApiClient } from "@/lib/utils";
import { NotificationBannerSettingDto } from "@/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import {
    Bell,
    RefreshCw,
    Save,
    AlertCircle,
    CheckCircle2,
    Info,
} from "lucide-react";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
import { useAppConfig } from "@/components/providers/app-config-provider";
⋮----
type NotificationForm = z.infer<typeof notificationSchema>;
⋮----
const fetchNotificationBanner = async () =>
⋮----
// If no notification exists yet, keep defaults
⋮----
const onSubmit = async (data: NotificationForm) =>
⋮----
// Update existing
⋮----
// Create new
⋮----
{/* Header */}
⋮----
{/* Configuration Form */}
⋮----
{/* Enable/Disable Toggle */}
⋮----
onCheckedChange=
⋮----
{/* Message Input */}
⋮----
{/* Preview */}
⋮----
{/* Info Box */}
⋮----
{/* Action Buttons */}
⋮----
{/* Information Cards */}
```

## File: frontend/app/src/components/features/settings/settings-layout.tsx

```typescript
import { Outlet } from "react-router";
export function SettingsLayout();
```

## File: frontend/app/src/components/features/settings/settings.tsx

```typescript
import { StorageFormData, useSettings } from "@/components/providers/settings-provider";
import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Cloud, Database, Settings as SettingsIcon, CheckCircle, AlertCircle } from "lucide-react";
import { useAuthContext } from "@/components/providers/auth-provider";
import { AvailableStorageProviderDto } from "@/api";
import { ShowScreenLoader } from "@/components/shared/show-screen-loader";
type StorageProvider = AvailableStorageProviderDto["provider"];
⋮----
// Get existing storage configuration for a provider
const getStorageConfig = (provider: StorageProvider): AvailableStorageProviderDto | null =>
// Update form data when switching tabs
const handleTabChange = (provider: string) =>
// Handle form submission
const handleSubmit = async (e: React.FormEvent) =>
// Update form field
const updateField = (field: keyof StorageFormData, value: any) =>
⋮----
{/* Header */}
⋮----
{/* Storage Provider Tabs */}
⋮----
{/* AWS S3 Configuration */}
⋮----
{/* Enable/Disable Toggle */}
⋮----
{/* Region */}
⋮----
{/* Bucket Name */}
⋮----
{/* Access Key */}
⋮----
{/* Secret Key */}
⋮----
className="mt-2" onClick=
⋮----
{/* Azure Blob Storage Configuration */}
⋮----
{/* Enable/Disable Toggle */}
⋮----
{/* Region */}
⋮----
{/* Container Name */}
⋮----
{/* Connection String */}
⋮----
{/* Storage Status Overview */}
⋮----
{/* AWS S3 Status */}
⋮----
{/* Azure Blob Status */}
```

## File: frontend/app/src/components/features/sso-configurations/create-new-sso-dialog.tsx

```typescript
import { useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '@/components/ui/dialog';
import {
    Form,
    FormControl,
    FormDescription,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { providerConfig, ssoFormSchema, SSOFormValues } from './sso-provider-configuration';
import { useSSOConfiguration } from '@/components/providers/sso-configuration-provider';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import {
    IconPlus,
    IconLoader2,
} from "@tabler/icons-react"
⋮----
async function handleCreateOrUpdate(data: SSOFormValues)
⋮----
// Create new provider
⋮----
function handleDialogClose(flag: boolean)
⋮----
<Button onClick=
⋮----
onSubmit=
```

## File: frontend/app/src/components/features/sso-configurations/delete-sso-configuration-dialog.tsx

```typescript
import { useSSOConfiguration } from '@/components/providers/sso-configuration-provider';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useEffect, useState } from 'react';
import { providerConfig } from './sso-provider-configuration';
interface DeleteSSOConfigurationDialogProps {
    deleteSSOProviderId?: string;
}
⋮----
async function confirmDelete()
```

## File: frontend/app/src/components/features/sso-configurations/sso-configuration-edit-dialog.tsx

```typescript
import { ReadSSOSettingsDto } from '@/api';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    Form,
    FormControl,
    FormDescription,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
    IconLoader2,
} from "@tabler/icons-react"
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ssoFormSchema, SSOFormValues } from './sso-provider-configuration';
import { useSSOConfiguration } from '@/components/providers/sso-configuration-provider';
import { Switch } from '@/components/ui/switch';
interface SSOConfigurationEditDialogProps {
    editSSOProvider: ReadSSOSettingsDto;
    onClose?: () => void;
}
⋮----
function handleDialogClose()
async function handleUpdate(data: SSOFormValues)
⋮----
onSubmit=
```

## File: frontend/app/src/components/features/sso-configurations/sso-provider-configuration.tsx

```typescript
import { useSSOConfiguration } from '@/components/providers/sso-configuration-provider';
import { Button } from '@/components/ui/button';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { zodResolver } from '@hookform/resolvers/zod';
import {
    IconBrandGithub,
    IconBrandGoogle,
    IconBrandDiscord,
    IconBrandX,
    IconBrandLinkedin,
    IconBrandFacebook,
    IconBrandBitbucket,
    IconBrandGitlab,
    IconBrandNotion,
    IconBrandAzure,
    IconKey,
    IconTrash,
    IconBrandOauth,
    IconCircleCheck,
    IconAlertCircle,
} from "@tabler/icons-react"
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { useState } from 'react';
import type { ReadSSOSettingsDto } from '@/api';
import DeleteSSOConfigurationDialog from '@/components/features/sso-configurations/delete-sso-configuration-dialog';
import SSOConfigurationEditDialog from '@/components/features/sso-configurations/sso-configuration-edit-dialog';
import CreateNewSSODialog from '@/components/features/sso-configurations/create-new-sso-dialog';
import { cn } from '@/lib/utils';
import { CopyText } from '@/components/shared/copy-text';
⋮----
export type SSOFormValues = z.infer<typeof ssoFormSchema>;
// Provider display configuration
⋮----
const handleEdit = (provider: ReadSSOSettingsDto) =>
const handleDelete = (provider: ReadSSOSettingsDto) =>
const handleToggleEnabled = async (provider: ReadSSOSettingsDto) =>
async function onUpdateProviderClose()
⋮----
{/* Header */}
⋮----
{/* Summary Cards */}
⋮----
{/* Providers List */}
```

## File: frontend/app/src/components/features/tenant/create-new-tenant-dialog.tsx

```typescript
import { useTenants } from "@/components/providers/tenant-provider";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogHeader } from "@/components/ui/dialog";
import z from "zod";
import { Gender } from "@/components/features/auth/register";
import { useEffect, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { useSubdomainCheck } from "@/hooks/use-subdomain-check";
import { Alert, AlertDescription } from "@/components/ui/alert";
⋮----
type TenantFormInputs = z.infer<typeof tenantSchema>;
interface CreateNewTenantDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
async function onSubmit(data: TenantFormInputs)
function onDismissDialog(flag: boolean)
⋮----
<form onSubmit=
{/* First Name */}
⋮----
{/* Last Name */}
⋮----
{/* Email */}
⋮----
{/* Gender */}
⋮----
{/* Password */}
⋮----
{/* Confirm Password */}
```

## File: frontend/app/src/components/features/tenant/manage-feature.tsx

```typescript
import { FeatureDto, Feature } from "@/api";
import { useAuthContext } from "@/components/providers/auth-provider";
import { useTenants } from "@/components/providers/tenant-provider";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn, getApiClient } from "@/lib/utils";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
    MessageSquare,
    FileBarChart,
    TrendingUp,
    Building2,
    Users,
    Loader2,
    CheckCircle,
    XCircle,
    Currency
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
interface ManageFeatureTenantDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
async function fetchFeatures()
async function updateFeature(featureName: Feature, enabled: boolean)
const getFeatureIcon = (featureName: Feature) =>
const getFeatureDescription = (featureName: string) =>
function onCloseDialog(flag: boolean)
⋮----
<div className=
```

## File: frontend/app/src/components/features/tenant/manage-tenant-subscription.tsx

```typescript
import { useAuthContext } from "@/components/providers/auth-provider";
import { useTenants } from "@/components/providers/tenant-provider";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { getApiClient } from "@/lib/utils";
import { toast } from "sonner";
interface ManageTenantSubscriptionProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
function onDismissHandler(flag: boolean)
async function enableTrialPlan()
```

## File: frontend/app/src/components/features/tenant/tenant-delete-dialog.tsx

```typescript
import { useTenants } from "@/components/providers/tenant-provider";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
interface DeleteTenantDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
function onDismissHandler(flag: boolean)
```

## File: frontend/app/src/components/features/tenant/tenant-selection.tsx

```typescript
import { useState, useEffect, useRef } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { X, Loader2 } from 'lucide-react'
import { TenantDto } from '@/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn, getApiClient, getTenant, setTenant } from '@/lib/utils'
import { useAppConfig } from '@/components/providers/app-config-provider'
interface TenantSearchProps {
    onTenantSelect?: (tenant: TenantDto | null) => void
    placeholder?: string
    className?: string
}
⋮----
// Debounce search query
⋮----
// Search tenants when debounced query changes
⋮----
const searchTenants = async (query: string) =>
const clearSelection = () =>
```

## File: frontend/app/src/components/features/tenant/tenant-setting.tsx

```typescript
import { useAppConfig } from "@/components/providers/app-config-provider";
import { useAuthContext } from "@/components/providers/auth-provider";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import {
    Form,
    FormField,
    FormItem,
    FormLabel,
    FormControl,
    FormMessage,
    FormDescription
} from "@/components/ui/form";
import { toast } from "sonner";
import { getApiClient } from "@/lib/utils";
import { TenantDto, UpdateTenantDto } from "@/api";
import { Building2, Globe, CheckCircle, Clock, XCircle, Plus, AlertTriangle } from "lucide-react";
import { NavLink } from "react-router";
⋮----
// Basic domain validation regex
⋮----
type TenantFormData = z.infer<typeof tenantFormSchema>;
⋮----
const checkCustomDomainStatus = async () =>
⋮----
}, 2000); // Slight delay to ensure backend has processed the status update
⋮----
const getCustomDomainStatusBadge = (status: TenantDto["custom_domain_status"]) =>
⋮----
}, 2000); // Slight delay to ensure backend has processed the status update
⋮----
{/* Tenant Information Card */}
⋮----
{/* Custom Domain Card */}
⋮----

⋮----
<NavLink to={`http://${current_tenant.custom_domain}`} target="_blank" rel="noopener noreferrer">
https://{current_tenant.custom_domain}
⋮----
{/* Add Custom Domain Button or Form */}
⋮----
onClick=
⋮----
<form onSubmit=
⋮----
{/* DNS Configuration Instructions */}
```

## File: frontend/app/src/components/features/tenant/tenant-table.tsx

```typescript
import { useAuthContext } from "@/components/providers/auth-provider";
import { TenantsType, useTenants } from "@/components/providers/tenant-provider";
import AdvanceTable from "@/components/shared/advance-table";
import { IResponseData } from "@/components/shared/iresponse-data.inteface";
import { ActionOption, TableActions } from "@/components/shared/table-actions";
import { createColumnHelper } from "@tanstack/react-table";
import { NavLink } from "react-router";
import { IconExternalLink, IconCheck, IconCross, IconFidgetSpinner } from '@tabler/icons-react'
import { cn } from "@/lib/utils";
import { TenantDto } from "@/api";
⋮----
<NavLink to={`https://${subdomain}`} target="_blank" className="hover:underline">
{`https://${subdomain}`}
⋮----
<NavLink to={`https://${customDomain}`} target="_blank" className=" hover:underline">
{`https://${customDomain}`}
⋮----
```

## File: frontend/app/src/components/features/tenant/tenant.tsx

```typescript
import { useAuthContext } from "@/components/providers/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { useState } from "react";
import { TenantTable } from "@/components/features/tenant/tenant-table";
import { useTenants } from "@/components/providers/tenant-provider";
import { CreateNewTenantDialog } from "@/components/features/tenant/create-new-tenant-dialog";
import { TenantDeleteDialog } from "@/components/features/tenant/tenant-delete-dialog";
import { ManageFeature } from "@/components/features/tenant/manage-feature";
import { ManageTenantSubscription } from "@/components/features/tenant/manage-tenant-subscription";
⋮----
const onTenantDismissHandler = () =>
```

## File: frontend/app/src/components/features/users/create-new-user-dialog.tsx

```typescript
import { Button } from "@/components/ui/button";
import { DialogHeader, DialogFooter, Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
    Form, FormField, FormLabel, FormControl, FormItem, FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Gender } from "@/components/features/auth/register";
import { useForm } from "react-hook-form";
import { useState } from "react";
import { toast } from "sonner";
import { useUsers } from "@/components/providers/users-provider";
⋮----
type CreateUserFormInputs = z.infer<typeof createUserSchema>;
interface CreateNewUserDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
const onSubmit = async (data: CreateUserFormInputs) =>
function onDismissDialog(flag: boolean)
⋮----
<form onSubmit=
{/* First Name */}
⋮----
{/* Last Name */}
⋮----
{/* Email */}
⋮----
{/* Gender */}
⋮----
{/* Password */}
```

## File: frontend/app/src/components/features/users/user-delete-dialog.tsx

```typescript
import { useUsers } from "@/components/providers/users-provider";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
interface DeleteNewUserDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
function onDismissHandler(flag: boolean)
```

## File: frontend/app/src/components/features/users/user-edit-dialog.tsx

```typescript
import { Button } from "@/components/ui/button";
import { DialogHeader, DialogFooter, Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
    Form, FormField, FormLabel, FormControl, FormItem, FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Gender } from "@/components/features/auth/register";
import { useForm } from "react-hook-form";
import { useState } from "react";
import { toast } from "sonner";
import { useUsers } from "@/components/providers/users-provider";
⋮----
type EditUserFormInputs = z.infer<typeof editUserSchema>;
interface UserEditDialogProps {
    open: boolean;
    onDismiss: () => void;
}
⋮----
const onSubmit = async (data: EditUserFormInputs) =>
function onDismissDialog(flag: boolean)
⋮----
<form onSubmit=
{/* First Name */}
⋮----
{/* Last Name */}
⋮----
{/* Gender */}
```

## File: frontend/app/src/components/features/users/user-table.tsx

```typescript
import { useAuthContext } from "@/components/providers/auth-provider";
import { UsersType, useUsers } from "@/components/providers/users-provider";
import AdvanceTable from "@/components/shared/advance-table";
import { IResponseData } from "@/components/shared/iresponse-data.inteface";
import { ActionOption, TableActions } from "@/components/shared/table-actions";
import { useProfileImage } from "@/hooks/use-profile-image";
import { createColumnHelper } from "@tanstack/react-table";
import { useEffect, useState } from "react";
⋮----
// Add "Resend Activation Email" option only for inactive users
⋮----
interface UserTableProps {
    userResponse: IResponseData<UsersType>;
    loading?: boolean;
}
export function UserTable(
```

## File: frontend/app/src/components/features/users/users.tsx

```typescript
import { PageHeader } from "@/components/shared/page-header";
import { UserTable } from "./user-table";
import { CreateNewUserDialog } from "./create-new-user-dialog";
import { useState } from "react";
import { useUsers } from "@/components/providers/users-provider";
import { UserEditDialog } from "@/components/features/users/user-edit-dialog";
import { UserDeleteDialog } from "@/components/features/users/user-delete-dialog";
import { useAuthContext } from "@/components/providers/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { InfoIcon } from 'lucide-react'
import { ManageUserRoles } from "@/components/shared/manage-user-roles";
⋮----
const onCreateNewUserDismissHandler = () =>
const onUserEditDismissHandler = () =>
```

## File: frontend/app/src/components/layouts/dashboard-layout.tsx

```typescript
import {
    SidebarTrigger, SidebarProvider,
} from "@/components/ui/sidebar";
import { Outlet, useNavigate } from 'react-router';
import { DarkMode } from "@/components/features/dark-mode/dark-mode";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { useAuthContext } from "@/components/providers/auth-provider";
import { Toaster } from "sonner";
import { SimpleFooter } from "@/components/shared/simple-footer";
import { DashboardSidebar } from "@/components/layouts/dashboard-sidebar-layout";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { Badge } from "@/components/ui/badge";
import { useEffect, useMemo } from "react";
import { Loading } from "@/components/shared/loading";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Banner } from "@/components/shared/banner";
⋮----
// Redirect to NonActiveTenantView
```

## File: frontend/app/src/components/layouts/dashboard-sidebar-layout.tsx

```typescript
import { NavLink, useLocation, useNavigate } from 'react-router';
import {
    Sidebar,
    SidebarContent,
    SidebarHeader,
    SidebarMenu,
    SidebarMenuItem,
    SidebarMenuButton,
    SidebarFooter,
    SidebarSeparator,
} from "@/components/ui/sidebar";
import { Button } from "../ui/button";
import { Logo } from "@/components/shared/logo";
import {
    LayoutDashboard,
    Users,
    Shield,
    User,
    MessageSquare,
    LogOut,
    ChevronRight,
    Tent,
    Cog,
    HardDriveIcon,
} from 'lucide-react';
import { IconSettingsBolt, IconCurrencyEuro, IconNotification, IconLogs, IconBrandAuth0 } from "@tabler/icons-react"
import { useAuthContext } from '../providers/auth-provider';
import { useMemo } from 'react';
import { cn, getApiClient } from '@/lib/utils';
import { toast } from 'sonner';
import { useAppConfig } from '../providers/app-config-provider';
⋮----
type LinkType = typeof navLinks[number];
⋮----
function isLinkActive(href: string)
async function logout()
⋮----
}, 2000); // Small delay to ensure toast is shown before navigation
⋮----
isActive=
⋮----
```

## File: frontend/app/src/components/layouts/default-layout.tsx

```typescript
import { DarkMode } from "@/components/features/dark-mode/dark-mode";
import { Footer } from "@/components/shared/footer";
import { Outlet } from "react-router";
import { Toaster } from "sonner";
import { Logo } from "../shared/logo";
export function DefaultLayout();
```

## File: frontend/app/src/components/providers/ai-chat-provider.tsx

```typescript
import { AISessionByUserIdDto } from "@/api";
import { extractErrorMessage, getApiClient } from "@/lib/utils";
import { createContext, useContext, useEffect, useState } from "react"
import { useAuthContext } from "@/components/providers/auth-provider";
import { toast } from "sonner";
import { useFeatureCheck } from "@/hooks/use-feature-check";
import { FeatureDisabledNotice } from "@/components/shared/feature-disabled";
type AIChatProviderState = {
    fetchAllSessions: () => Promise<void>;
    sessions: AISessionByUserIdDto[];
    onDeleteSession: (sessionId: string) => Promise<void>;
    getAIChatNewSession: () => Promise<string>;
}
⋮----
interface AIChatProviderProps {
    children: React.ReactNode;
}
export function AIChatProvider(
⋮----
async function fetchAllSessions()
async function onDeleteSession(sessionId: string)
async function getAIChatNewSession()
⋮----
export function useAIChat()
```

## File: frontend/app/src/components/providers/app-config-provider.tsx

```typescript
import { AppConfigurationDto, NotificationBannerSettingDto, TenantDto } from "@/api";
import { getApiClient, setTenant } from "@/lib/utils";
import { createContext, useContext, useEffect, useState, useCallback } from "react"
import { useAuthContext } from "./auth-provider";
⋮----
interface Configuration extends AppConfigurationDto {
    reloadAppConfig: () => Promise<void>;
    shouldShowTenantSelection?: boolean;
    redirectToTenantDomain: (tenant: TenantDto) => void;
    notificationBannerSetting: NotificationBannerSettingDto
}
⋮----
interface AppConfigProviderProps {
    children: React.ReactNode;
}
export function AppConfigProvider(
⋮----
const fetchConfig = async () =>
⋮----
export function useAppConfig()
```

## File: frontend/app/src/components/providers/audit-log-provider.tsx

```typescript
import { AuditLogListDto } from "@/api";
import { createContext, useContext, useEffect, useState } from "react";
import { useAuthContext } from "./auth-provider";
import { extractErrorMessage, getApiClient } from "@/lib/utils";
import { IResponseData } from "../shared/iresponse-data.inteface";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { NoPermissionToAccessResource } from "@/components/shared/no-permission-access-resource";
export type AuditLogType = AuditLogListDto["logs"][0];
export type AuditLogActionType = AuditLogType["action"];
interface AuditLogsProviderProps {
    children: React.ReactNode;
}
interface AuditLogsContextProps {
    data: IResponseData<AuditLogType>;
    canDownloadAuditLogs: boolean
    triggerDownloadAuditLogs: () => Promise<void>;
}
⋮----
export function AuditLogProvider(
⋮----
async function fetchAuditLogs()
async function triggerDownloadAuditLogs()
⋮----
export function useAuditLogsContext()
```

## File: frontend/app/src/components/providers/auth-provider.tsx

```typescript
import { CreateUserDto, Gender, Permission, TokenSetDto, type MeResponseDto } from "@/api";
import { useProfileImage } from "@/hooks/use-profile-image";
import {
    getApiClient,
    getTenant,
} from "@/lib/utils";
import { createContext, useContext, useEffect, useState, useCallback } from "react"
import { useLocation, useNavigate } from "react-router";
import { toast } from "sonner";
⋮----
export type UpdateProfileType = {
    firstName: string;
    lastName: string;
    gender: Gender;
    imageUrl: string | null;
}
type AuthProviderState = {
    isLoggedIn: boolean;
    user: MeResponseDto | null;
    can: (action: Permission) => boolean;
    onUpdateProfile: (data: UpdateProfileType) => Promise<void>;
    refreshCurrentUser: () => Promise<void>;
    accessToken: string,
    login: (data: { email: string; password: string }) => Promise<void>
    register: (data: CreateUserDto) => Promise<any>,
    resendActivationEmail: () => Promise<void>;
}
⋮----
export function AuthProvider(
⋮----
async function resendActivationEmail()
function can(action: Permission)
async function onUpdateProfile(data: UpdateProfileType)
async function refreshCurrentUser()
⋮----
await fetchUser(true); // Force refresh
⋮----
async function login(data:
async function register(data: CreateUserDto)
// Todo: Refactor to auto refresh token using web worker
⋮----
// navigate("/dashboard");
⋮----
export function useAuthContext()
```

## File: frontend/app/src/components/providers/roles-provider.tsx

```typescript
import { CreateRoleDto, RoleListDto, UpdateRoleDto } from '@/api';
import { createContext, useContext, useEffect, useState } from 'react';
import { IResponseData } from '@/components/shared/iresponse-data.inteface';
import { useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { getApiClient } from '@/lib/utils';
import { useAuthContext } from '@/components/providers/auth-provider';
import { NoPermissionToAccessResource } from '@/components/shared/no-permission-access-resource';
export type RoleResponse = RoleListDto
export type RolesType = RoleResponse["roles"][0]
type ActionType = 'edit' | 'delete' | 'clone' | 'manage_permissions'
type Action = {
    type: ActionType;
    role: RolesType;
}
interface RolesProviderState {
    roleResponse: IResponseData<RolesType>;
    refreshRoles: () => void;
    onCreateNewRole: (params: CreateRoleDto) => Promise<void>;
    loading: boolean;
    selectedRole?: Action;
    onSelectRole: (action?: Action) => void;
    onUpdateRole: (roleId: string, params: UpdateRoleDto) => Promise<void>;
    onDeleteRole: () => Promise<void>;
    error: string | null
}
⋮----
interface RolesProviderProps {
    children: React.ReactNode;
}
export function RolesProvider(
⋮----
async function fetchRoles()
async function refreshRoles()
async function onCreateNewRole(params: CreateRoleDto)
async function onUpdateRole(role_id: string, params: CreateRoleDto)
async function onSelectRole(action?: Action)
async function onDeleteRole()
```

## File: frontend/app/src/components/providers/settings-provider.tsx

```typescript
import { AvailableStorageProviderDto, StorageSettingsDto } from "@/api";
import { getApiClient } from "@/lib/utils";
import { useContext, createContext, useEffect, useState } from "react";
import { toast } from "sonner";
import { useAuthContext } from "./auth-provider";
import { NoPermissionToAccessResource } from "@/components/shared/no-permission-access-resource";
import { useAppConfig } from "./app-config-provider";
export interface StorageFormData extends Omit<AvailableStorageProviderDto, 'id' | 'created_at' | 'updated_at'> { }
interface SettingsContextProps {
    storages: AvailableStorageProviderDto[]
    availableStorages: Array<Record<string, string>>
    onConfigureStorage: (settings: StorageFormData) => Promise<void>;
    loading: boolean;
    onResetStorage: (storageId: string) => Promise<void>;
}
⋮----
interface SettingsProviderProps {
    children: React.ReactNode;
}
export function SettingsProvider(
⋮----
async function fetchSettings()
async function onResetStorage(storageId: string)
async function onConfigureStorage(settings: StorageFormData)
⋮----
export function useSettings()
```

## File: frontend/app/src/components/providers/sso-configuration-provider.tsx

```typescript
import { CreateSSOSettingsDto, SSOSettingsListDto, UpdateSSOSettingsDto } from "@/api";
import { createContext, useContext, useState, useEffect } from "react"
import { useAuthContext } from "./auth-provider";
import { getApiClient } from "@/lib/utils";
import { toast } from "sonner";
import { NoPermissionToAccessResource } from "../shared/no-permission-access-resource";
import { useAppConfig } from "./app-config-provider";
type ProviderType = SSOSettingsListDto["items"]
interface SSOConfigurationContextProps {
    onRefreshSSOConfigurations: () => Promise<void>;
    providers: ProviderType;
    availableProviders: string[];
    onCreateSSOConfiguration: (params: CreateSSOSettingsDto) => Promise<void>;
    onEnableOrDisableSSOLogin: (ssoId: string, enabled: boolean) => Promise<void>;
    onDeleteSSOConfiguration: (ssoId: string) => Promise<void>;
    onUpdateSSOConfiguration: (ssoId: string, params: UpdateSSOSettingsDto) => Promise<void>;
}
⋮----
interface SSOConfigurationProviderProps {
    children: React.ReactNode;
}
export default function SSOConfigurationProvider(
⋮----
async function fetchAllSSOProviders()
⋮----
// setAvailableProviders(availableProvidersResponse);
⋮----
async function refreshSSOConfigurations()
async function createSSOConfiguration(params: CreateSSOSettingsDto)
async function enableOrDisableSSOLogin(ssoId: string, enabled: boolean)
async function deleteSSOConfiguration(ssoId: string)
async function updateSSOConfiguration(ssoId: string, params: UpdateSSOSettingsDto)
```

## File: frontend/app/src/components/providers/stripe-provider.tsx

```typescript
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { CreateStripeSettingDto, StripeSettingDto } from "@/api"
import { useAuthContext } from "./auth-provider";
import { getApiClient } from "@/lib/utils";
import { toast } from "sonner";
import { FeatureDisabledNotice } from "@/components/shared/feature-disabled";
import { useFeatureCheck } from "@/hooks/use-feature-check";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { NoPermissionToAccessResource } from "@/components/shared/no-permission-access-resource";
interface StripeProviderState {
    onConfigureStripe: (data: CreateStripeSettingDto) => Promise<void>;
    configuredStripeSetting: StripeSettingDto | null;
    loading: boolean;
    onRefreshStripeSetting: () => Promise<void>;
    showConfigureStripe: boolean;
    stripeConfigurationError: boolean;
}
⋮----
interface StripeProviderProps {
    children: React.ReactNode;
}
export function StripeProvider(
⋮----
async function fetchConfiguredStripeSetting()
⋮----
export function useStripeProvider()
```

## File: frontend/app/src/components/providers/tenant-provider.tsx

```typescript
import { CreateTenantDto, TenantListDto, UpdateTenantDto } from '@/api';
import { createContext, useContext, useEffect, useState } from 'react';
import { IResponseData } from '@/components/shared/iresponse-data.inteface';
import { getApiClient } from '@/lib/utils';
import { useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { useAuthContext } from '@/components/providers/auth-provider';
import { NoPermissionToAccessResource } from '@/components/shared/no-permission-access-resource';
import { useAppConfig } from '@/components/providers/app-config-provider';
export type TenantResponse = TenantListDto
export type TenantsType = TenantResponse["tenants"][0]
type ActionType = 'edit' | 'delete' | 'manage_features' | "manage_subscription";
type Action = {
    type: ActionType;
    tenant: TenantsType;
}
interface TenantsProviderState {
    tenantResponse: IResponseData<TenantsType>;
    refreshTenants: () => void;
    isLoading: boolean;
    selectedTenant?: Action;
    onSelectTenant: (action?: Action) => void;
    onCreateNewTenant: (newTenant: CreateTenantDto) => Promise<void>;
    onDeleteTenant: () => Promise<void>;
    onUpdateTenant: (tenantId: string, updatedTenant: UpdateTenantDto) => Promise<void>;
}
⋮----
interface TenantsProviderProps {
    children: React.ReactNode;
}
export function TenantsProvider(
⋮----
async function fetchTenants()
const refreshTenants = async () =>
const onSelectTenant = (action?: Action) =>
async function onUpdateTenant(tenantId: string, updatedTenant: UpdateTenantDto)
const onCreateNewTenant = async (newTenant: CreateTenantDto) =>
const onDeleteTenant = async () =>
⋮----
export function useTenants()
```

## File: frontend/app/src/components/providers/theme-provider.tsx

```typescript
import { createContext, useContext, useEffect, useState } from "react"
type Theme = "dark" | "light" | "system"
type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    storageKey?: string
}
type ThemeProviderState = {
    theme: Theme
    setTheme: (theme: Theme) => void
}
⋮----
export function ThemeProvider({
    children,
    defaultTheme = "system",
    storageKey = "vite-ui-theme",
    ...props
}: ThemeProviderProps)
export const useTheme = () =>
```

## File: frontend/app/src/components/providers/users-provider.tsx

```typescript
import { CreateUserDto, UserListDto, UpdateUserDto } from '@/api';
import { createContext, useContext, useEffect, useState } from 'react';
import { IResponseData } from '@/components/shared/iresponse-data.inteface';
import { getApiClient } from '@/lib/utils';
import { useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { useAuthContext } from '@/components/providers/auth-provider';
import { NoPermissionToAccessResource } from '@/components/shared/no-permission-access-resource';
export type UserResponse = UserListDto
export type UsersType = UserResponse["users"][0]
type ActionType = 'edit' | 'delete' | 'resend_email' | 'manage_roles';
type Action = {
    type: ActionType;
    user: UsersType;
}
interface UsersProviderState {
    userResponse: IResponseData<UsersType>;
    refreshUsers: () => void;
    onCreateNewUser: (params: CreateUserDto) => Promise<void>;
    loading: boolean;
    selectedUser?: Action;
    onSelectUser: (action?: Action) => void;
    onUpdateUser: (userId: string, params: UpdateUserDto) => Promise<void>;
    onDeleteUser: () => Promise<void>;
    userError: string | null;
}
⋮----
interface UsersProviderProps {
    children: React.ReactNode;
}
export function UsersProvider(
⋮----
async function fetchUsers()
async function refreshUsers()
async function onCreateNewUser(params: CreateUserDto)
async function onUpdateUser(user_id: string, params: UpdateUserDto)
async function onSelectUser(action?: Action)
async function onDeleteUser()
```

## File: frontend/app/src/components/shared/advance-table.tsx

```typescript
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    ColumnDef,
    flexRender,
    getCoreRowModel,
    useReactTable,
} from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import {
    Pagination,
    PaginationContent,
    PaginationItem,
} from "@/components/ui/pagination";
import { Button } from "@/components/ui/button";
import {
    ChevronLeftIcon,
    ChevronRightIcon,
    ArrowLeftIcon,
    ArrowRightIcon
} from "lucide-react";
import { useSearchParams } from "react-router";
import { cn } from "@/lib/utils";
import type { IResponseData } from "@/components/shared/iresponse-data.inteface";
import { Loading } from "@/components/shared/loading";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, SelectGroup } from "@/components/ui/select";
import { IconFilter } from "@tabler/icons-react"
interface Props<T> {
    data: IResponseData<T>;
    columns: ColumnDef<T, any>[];
    hasBottomBorder?: boolean;
    loading?: boolean;
    errorMsg?: string;
    dropdownActions?: {
        options: Array<{ name: string; value: string }>;
    }
}
⋮----
function onDropdownChange(value: string)
⋮----
const renderEmptyState = () =>
⋮----
{flexRender(
                                                    cell.column.columnDef.cell,
                                                    cell.getContext(),
                                                )}
                                            </span>
                                        </TableCell>
                                    );
⋮----
disabled=
aria-disabled=
```

## File: frontend/app/src/components/shared/banner.tsx

```typescript
import { Info } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "../ui/alert";
interface BannerProps {
    isEnabled: boolean;
    message?: string | null;
}
export function Banner(
```

## File: frontend/app/src/components/shared/copy-text.tsx

```typescript
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { toast } from "sonner";
⋮----
async function handleCopy()
⋮----
// Check if clipboard API is available
⋮----
// Fallback for older browsers or non-secure contexts
```

## File: frontend/app/src/components/shared/feature-disabled.tsx

```typescript
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Info } from "lucide-react";
import { useAppConfig } from "../providers/app-config-provider";
export function FeatureDisabledNotice(
```

## File: frontend/app/src/components/shared/footer.tsx

```typescript
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import {
    Github,
    Twitter,
    Linkedin,
    Mail,
    Heart,
    Code,
    Zap,
    Shield,
    Globe,
    ExternalLink,
    Coffee
} from 'lucide-react'
import { Logo } from './logo'
⋮----
{/* Main Footer Content */}
⋮----
{/* Brand Section */}
⋮----
{/* Tech Stack */}
⋮----
{/* Social Links */}
⋮----
{/* Product Links */}
⋮----
{/* Company Links */}
⋮----
{/* Support Links */}
⋮----
{/* Bottom Section */}
⋮----
{/* Status Indicator */}
```

## File: frontend/app/src/components/shared/iresponse-data.inteface.ts

```typescript
export interface IResponseData<T> {
  items: T[];
  skip: number;
  limit: number;
  total: number;
  hasPrevious: boolean;
  hasNext: boolean;
}
```

## File: frontend/app/src/components/shared/list-local-ai-models.tsx

```typescript
import { AIModelInfoDto } from "@/api";
import { useEffect, useState } from "react";
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { InfoIcon, Brain, Cpu, Zap, Sparkles } from 'lucide-react'
interface ListLocalAIModelsProps {
    onModelSelect: (model: AIModelInfoDto) => void;
    selectedModel?: AIModelInfoDto;
    avilableModels: AIModelInfoDto[];
}
⋮----
const getModelIcon = (modelName: string) =>
const formatModelSize = (size?: string) =>
```

## File: frontend/app/src/components/shared/list-permission.tsx

```typescript
import { PermissionDto } from "@/api";
import { getApiClient } from "@/lib/utils";
import { useEffect, useState, useTransition } from "react";
import { CheckIcon, ChevronsUpDownIcon, InfoIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command"
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { useAuthContext } from "../providers/auth-provider";
interface ListPermissionProps {
    value?: string[]
    onChange?: (permissions: string[]) => void
    placeholder?: string
    multiSelect?: boolean
    maxSelections?: number
}
⋮----
async function fetchPermissions()
⋮----
const handlePermissionSelect = (permissionName: string) =>
⋮----
// Remove permission if already selected
⋮----
// Add permission if not selected and within limits
⋮----
return; // Don't add if max selections reached
⋮----
// Single select mode
⋮----
setOpen(false); // Close popover for single select
⋮----
const handleRemovePermission = (permissionName: string) =>
const getDisplayText = () =>
⋮----
{/* Selected permissions badges (for multi-select) */}
⋮----
<button
```

## File: frontend/app/src/components/shared/loading.tsx

```typescript
import { cn } from "@/lib/utils"
interface LoadingProps {
    variant?: "spinner" | "dots" | "pulse" | "skeleton"
    size?: "sm" | "md" | "lg"
    text?: string
    className?: string
}
⋮----
<div className=
```

## File: frontend/app/src/components/shared/logo-variants.tsx

```typescript
import { cn } from '@/lib/utils'
interface LogoProps {
    className?: string
    size?: 'sm' | 'md' | 'lg'
    showText?: boolean
    variant?: 'default' | 'compact' | 'icon-only'
}
⋮----
<div className=
⋮----
{/* Modern geometric logo */}
⋮----
{/* Inner geometric shape */}
⋮----
{/* Logo symbol - stylized "FS" for Full Stack */}
⋮----
{/* Animated corner dots */}
⋮----
{/* Rotating border effect */}
⋮----
{/* Text content */}
⋮----
<span className=
⋮----
// Alternative simple version without dependencies
```

## File: frontend/app/src/components/shared/logo.tsx

```typescript
import { cn } from '@/lib/utils'
interface LogoProps {
    className?: string
    size?: 'sm' | 'md' | 'lg'
    showText?: boolean
}
⋮----
<div className=
{/* Modern geometric logo */}
⋮----
{/* Inner geometric shape */}
⋮----
{/* Logo symbol - stylized "FS" for Full Stack */}
⋮----
{/* Subtle animation dots */}
⋮----
{/* Text content */}
⋮----
<span className=
```

## File: frontend/app/src/components/shared/manage-security.tsx

```typescript
import { Dialog, DialogContent, DialogHeader, DialogTrigger, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { getApiClient } from "@/lib/utils";
import { useAuthContext } from "../providers/auth-provider";
import { useEffect, useState } from "react";
import { RegisteredPasskeyCredentialsDto } from "@/api";
import {
    Table,
    TableBody,
    TableCaption,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { Trash } from "lucide-react";
interface PassKeyManagementProps {
    accessToken: string;
    shouldRefresh?: boolean;
}
⋮----
async function fetchPasskeys()
async function onDeletePasskey(credentialId: string)
⋮----
// Refresh the list
⋮----
function onOpen(isOpen: boolean)
```

## File: frontend/app/src/components/shared/manage-user-roles.tsx

```typescript
import { RoleDto } from "@/api";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getApiClient } from "@/lib/utils";
import { useState, useEffect } from "react";
import { Search, Users, CheckCircle } from "lucide-react";
import { toast } from "sonner";
import { useAuthContext } from "../providers/auth-provider";
interface ManageUserRolesProps {
    open: boolean;
    onDismiss: () => void;
    userId: string;
}
⋮----
async function fetchRoles()
const handleSearch = () =>
const handleRoleSelect = (role: RoleDto) =>
const handleAssignRole = async () =>
⋮----
// Add your role assignment logic here
⋮----
// Search on Enter key press
const handleKeyPress = (e: React.KeyboardEvent) =>
// Reset state when dialog closes
⋮----
{/* Search Input */}
⋮----
onChange=
⋮----
{/* Search Results */}
⋮----
onClick=
⋮----
{/* Action Buttons */}
```

## File: frontend/app/src/components/shared/no-permission-access-resource.tsx

```typescript
import { IconAlertCircle } from "@tabler/icons-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
interface NoPermissionProps {
    message?: string;
}
export function NoPermissionToAccessResource(
```

## File: frontend/app/src/components/shared/non-active-tenant-view.tsx

```typescript
import { useEffect } from "react";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { useNavigate } from "react-router";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
    AlertTriangle,
    Building2,
    Mail,
    Phone,
    RefreshCw,
    ArrowLeft,
    Clock,
    Shield
} from "lucide-react";
import { Logo } from "./logo";
⋮----
// Redirect to the dashboard or any other page
⋮----
const handleRefresh = () =>
const handleGoBack = () =>
⋮----
{/* Logo */}
⋮----
{/* Main Card */}
⋮----
{/* Tenant Information */}
⋮----
{/* Status Alert */}
⋮----
{/* Next Steps */}
⋮----
{/* Contact Information */}
⋮----
{/* Action Buttons */}
⋮----
{/* Footer */}
```

## File: frontend/app/src/components/shared/page-header.tsx

```typescript
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
interface PageHeaderProps {
  title: string;
  subtitle?: string;
  cta?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    icon?: React.ReactNode;
  };
}
```

## File: frontend/app/src/components/shared/show-screen-loader.tsx

```typescript
import { Loading } from "./loading";
interface ShowScreenLoaderProps {
    message?: string;
}
export function ShowScreenLoader(
```

## File: frontend/app/src/components/shared/simple-footer.tsx

```typescript
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import {
    Heart,
} from 'lucide-react'
import { Logo } from './logo'
export function SimpleFooter()
⋮----
{/* Brand */}
```

## File: frontend/app/src/components/shared/table-actions.tsx

```typescript
import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuGroup,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { CogIcon } from 'lucide-react'
import { useAuthContext } from "@/components/providers/auth-provider";
export type ActionOption<D> = {
    label: string;
    data: D;
    onClick: (value: D) => void;
    disabled?: boolean;
};
interface TableActionsProps<D> {
    options: ActionOption<D>[];
    resource: 'user' | 'role' | 'tenant';
}
⋮----
<DropdownMenuItem key=
```

## File: frontend/app/src/components/ui/accordion.tsx

```typescript
import { ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
function Accordion({
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Root>);
function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>);
function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>);
```

## File: frontend/app/src/components/ui/alert-dialog.tsx

```typescript
import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
function AlertDialog({
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Root>)
⋮----
className=
⋮----
function AlertDialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof AlertDialogPrimitive.Description>)
```

## File: frontend/app/src/components/ui/alert.tsx

```typescript
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/aspect-ratio.tsx

```typescript
function AspectRatio({
  ...props
}: React.ComponentProps<typeof AspectRatioPrimitive.Root>);
```

## File: frontend/app/src/components/ui/avatar.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/badge.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
⋮----
function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
VariantProps<typeof badgeVariants> &
⋮----
className=
```

## File: frontend/app/src/components/ui/breadcrumb.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { ChevronRight, MoreHorizontal } from "lucide-react"
import { cn } from "@/lib/utils"
function Breadcrumb(
⋮----
className=
⋮----
{children ?? <ChevronRight />}
    </li>
  )
}
function BreadcrumbEllipsis({
  className,
  ...props
}: React.ComponentProps<"span">)
```

## File: frontend/app/src/components/ui/button-group.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"
⋮----
className=
```

## File: frontend/app/src/components/ui/button.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/calendar.tsx

```typescript
import {
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from "lucide-react"
import { DayButton, DayPicker, getDefaultClassNames } from "react-day-picker"
import { cn } from "@/lib/utils"
import { Button, buttonVariants } from "@/components/ui/button"
⋮----
className=
```

## File: frontend/app/src/components/ui/card.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/carousel.tsx

```typescript
import useEmblaCarousel, {
  type UseEmblaCarouselType,
} from "embla-carousel-react"
import { ArrowLeft, ArrowRight } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
type CarouselApi = UseEmblaCarouselType[1]
type UseCarouselParameters = Parameters<typeof useEmblaCarousel>
type CarouselOptions = UseCarouselParameters[0]
type CarouselPlugin = UseCarouselParameters[1]
type CarouselProps = {
  opts?: CarouselOptions
  plugins?: CarouselPlugin
  orientation?: "horizontal" | "vertical"
  setApi?: (api: CarouselApi) => void
}
type CarouselContextProps = {
  carouselRef: ReturnType<typeof useEmblaCarousel>[0]
  api: ReturnType<typeof useEmblaCarousel>[1]
  scrollPrev: () => void
  scrollNext: () => void
  canScrollPrev: boolean
  canScrollNext: boolean
} & CarouselProps
⋮----
function useCarousel()
function Carousel({
  orientation = "horizontal",
  opts,
  setApi,
  plugins,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & CarouselProps)
⋮----
className=
⋮----
function CarouselNext({
  className,
  variant = "outline",
  size = "icon",
  ...props
}: React.ComponentProps<typeof Button>)
```

## File: frontend/app/src/components/ui/chart.tsx

```typescript
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent"
⋮----
import { cn } from "@/lib/utils"
// Format: { THEME_NAME: CSS_SELECTOR }
⋮----
export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode
    icon?: React.ComponentType
  } & (
    | { color?: string; theme?: never }
    | { color?: never; theme: Record<keyof typeof THEMES, string> }
  )
>
interface ChartContextProps {
  config: ChartConfig
}
⋮----
function useChart()
⋮----
className=
⋮----
<div className=
⋮----
return <div className=
⋮----
// Helper to extract item config from a payload.
```

## File: frontend/app/src/components/ui/checkbox.tsx

```typescript
import { CheckIcon } from "lucide-react";
import { cn } from "@/lib/utils";
function Checkbox({
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>);
```

## File: frontend/app/src/components/ui/collapsible.tsx

```typescript
function Collapsible({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.Root>);
function CollapsibleTrigger({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>);
```

## File: frontend/app/src/components/ui/command.tsx

```typescript
import { Command as CommandPrimitive } from "cmdk"
import { SearchIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
⋮----
className=
```

## File: frontend/app/src/components/ui/context-menu.tsx

```typescript
import { CheckIcon, ChevronRightIcon, CircleIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function ContextMenu({
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.Root>)
⋮----
function ContextMenuRadioGroup({
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.RadioGroup>)
⋮----
className=
⋮----
function ContextMenuRadioItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof ContextMenuPrimitive.RadioItem>)
```

## File: frontend/app/src/components/ui/dialog.tsx

```typescript
import { XIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function Dialog({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Root>)
function DialogTrigger({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Trigger>)
function DialogPortal({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Portal>)
function DialogClose({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Close>)
⋮----
className=
⋮----
function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>)
```

## File: frontend/app/src/components/ui/drawer.tsx

```typescript
import { Drawer as DrawerPrimitive } from "vaul"
import { cn } from "@/lib/utils"
function Drawer({
  ...props
}: React.ComponentProps<typeof DrawerPrimitive.Root>)
function DrawerTrigger({
  ...props
}: React.ComponentProps<typeof DrawerPrimitive.Trigger>)
function DrawerPortal({
  ...props
}: React.ComponentProps<typeof DrawerPrimitive.Portal>)
function DrawerClose({
  ...props
}: React.ComponentProps<typeof DrawerPrimitive.Close>)
function DrawerOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DrawerPrimitive.Overlay>)
⋮----
className=
```

## File: frontend/app/src/components/ui/dropdown-menu.tsx

```typescript
import { CheckIcon, ChevronRightIcon, CircleIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function DropdownMenu({
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Root>)
⋮----
return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        className={cn(
          "bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 max-h-(--radix-dropdown-menu-content-available-height) min-w-[8rem] origin-(--radix-dropdown-menu-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border p-1 shadow-md",
          className
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
}
function DropdownMenuGroup({
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Group>)
⋮----
className=
```

## File: frontend/app/src/components/ui/empty.tsx

```typescript
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
⋮----
className=
⋮----
className={cn(emptyMediaVariants({ variant, className }))}
      {...props}
    />
  )
}
function EmptyTitle(
```

## File: frontend/app/src/components/ui/field.tsx

```typescript
import { useMemo } from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
⋮----
className=
```

## File: frontend/app/src/components/ui/form.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import {
  Controller,
  FormProvider,
  useFormContext,
  useFormState,
  type ControllerProps,
  type FieldPath,
  type FieldValues,
} from "react-hook-form"
import { cn } from "@/lib/utils"
import { Label } from "@/components/ui/label"
⋮----
type FormFieldContextValue<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
> = {
  name: TName
}
⋮----
const FormField = <
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
>({
  ...props
}: ControllerProps<TFieldValues, TName>) =>
const useFormField = () =>
type FormItemContextValue = {
  id: string
}
⋮----
className=
⋮----
function FormControl(
function FormDescription(
```

## File: frontend/app/src/components/ui/hover-card.tsx

```typescript
import { cn } from "@/lib/utils";
function HoverCard({
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Root>);
```

## File: frontend/app/src/components/ui/input-group.tsx

```typescript
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
⋮----
className=
⋮----
// Variants based on alignment.
⋮----
// Focus state.
⋮----
// Error state.
⋮----
if ((e.target as HTMLElement).closest("button"))
```

## File: frontend/app/src/components/ui/input-otp.tsx

```typescript
import { OTPInput, OTPInputContext } from "input-otp"
import { MinusIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function InputOTP({
  className,
  containerClassName,
  ...props
}: React.ComponentProps<typeof OTPInput> & {
  containerClassName?: string
})
⋮----
containerClassName=
⋮----
className=
```

## File: frontend/app/src/components/ui/input.tsx

```typescript
import { cn } from "@/lib/utils"
function Input(
⋮----
className=
```

## File: frontend/app/src/components/ui/item.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"
function ItemGroup(
function ItemSeparator({
  className,
  ...props
}: React.ComponentProps<typeof Separator>)
⋮----
className=
```

## File: frontend/app/src/components/ui/kbd.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/label.tsx

```typescript
import { cn } from "@/lib/utils";
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>);
```

## File: frontend/app/src/components/ui/menubar.tsx

```typescript
import { CheckIcon, ChevronRightIcon, CircleIcon } from "lucide-react"
import { cn } from "@/lib/utils"
⋮----
function MenubarPortal({
  ...props
}: React.ComponentProps<typeof MenubarPrimitive.Portal>)
⋮----
className=
⋮----
function MenubarRadioItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof MenubarPrimitive.RadioItem>)
⋮----
function MenubarSubContent({
  className,
  ...props
}: React.ComponentProps<typeof MenubarPrimitive.SubContent>)
```

## File: frontend/app/src/components/ui/navigation-menu.tsx

```typescript
import { cva } from "class-variance-authority"
import { ChevronDownIcon } from "lucide-react"
import { cn } from "@/lib/utils"
⋮----
className=
⋮----
function NavigationMenuTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Trigger>)
function NavigationMenuContent({
  className,
  ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Content>)
```

## File: frontend/app/src/components/ui/pagination.tsx

```typescript
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  MoreHorizontalIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button, buttonVariants } from "@/components/ui/button"
⋮----
className=
⋮----
function PaginationNext({
  className,
  ...props
}: React.ComponentProps<typeof PaginationLink>)
function PaginationEllipsis({
  className,
  ...props
}: React.ComponentProps<"span">)
```

## File: frontend/app/src/components/ui/popover.tsx

```typescript
import { cn } from "@/lib/utils";
function Popover({
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Root>);
function PopoverTrigger({
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Trigger>);
```

## File: frontend/app/src/components/ui/progress.tsx

```typescript
import { cn } from "@/lib/utils";
```

## File: frontend/app/src/components/ui/radio-group.tsx

```typescript
import { CircleIcon } from "lucide-react";
import { cn } from "@/lib/utils";
function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>);
function RadioGroupItem({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item>);
```

## File: frontend/app/src/components/ui/resizable.tsx

```typescript
import { GripVerticalIcon } from "lucide-react"
⋮----
import { cn } from "@/lib/utils"
```

## File: frontend/app/src/components/ui/scroll-area.tsx

```typescript
import { cn } from "@/lib/utils";
function ScrollArea({
  className,
  children,
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.Root>);
function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>);
```

## File: frontend/app/src/components/ui/select.tsx

```typescript
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function Select({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Root>)
function SelectGroup({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Group>)
function SelectValue({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Value>)
function SelectTrigger({
  className,
  size = "default",
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger> & {
  size?: "sm" | "default"
})
⋮----
className=
⋮----
function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>)
function SelectItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item>)
function SelectSeparator({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Separator>)
function SelectScrollUpButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollUpButton>)
function SelectScrollDownButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollDownButton>)
```

## File: frontend/app/src/components/ui/separator.tsx

```typescript
import { cn } from "@/lib/utils";
function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>);
```

## File: frontend/app/src/components/ui/sheet.tsx

```typescript
import { XIcon } from "lucide-react"
import { cn } from "@/lib/utils"
function Sheet(
function SheetTrigger({
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Trigger>)
function SheetClose({
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Close>)
function SheetPortal({
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Portal>)
function SheetOverlay({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Overlay>)
⋮----
className=
```

## File: frontend/app/src/components/ui/sidebar.tsx

```typescript
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { PanelLeftIcon } from "lucide-react"
import { useIsMobile } from "@/hooks/use-mobile"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
⋮----
type SidebarContextProps = {
  state: "expanded" | "collapsed"
  open: boolean
  setOpen: (open: boolean) => void
  openMobile: boolean
  setOpenMobile: (open: boolean) => void
  isMobile: boolean
  toggleSidebar: () => void
}
⋮----
function useSidebar()
⋮----
// This is the internal state of the sidebar.
// We use openProp and setOpenProp for control from outside the component.
⋮----
// This sets the cookie to keep the sidebar state.
⋮----
// Helper to toggle the sidebar.
⋮----
// Adds a keyboard shortcut to toggle the sidebar.
⋮----
const handleKeyDown = (event: KeyboardEvent) =>
⋮----
// We add a state so that we can do data-state="expanded" or "collapsed".
// This makes it easier to style the sidebar with Tailwind classes.
⋮----
className=
⋮----
{/* This is what handles the sidebar gap on desktop */}
⋮----
// Adjust the padding for floating and inset variants.
⋮----
// Increases the hit area of the button on mobile.
⋮----
// Increases the hit area of the button on mobile.
⋮----
// Random width between 50 to 90%.
```

## File: frontend/app/src/components/ui/skeleton.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/slider.tsx

```typescript
import { cn } from "@/lib/utils"
function Slider({
  className,
  defaultValue,
  value,
  min = 0,
  max = 100,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root>)
className=
```

## File: frontend/app/src/components/ui/sonner.tsx

```typescript
import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Toaster as Sonner, type ToasterProps } from "sonner";
```

## File: frontend/app/src/components/ui/spinner.tsx

```typescript
import { Loader2Icon } from "lucide-react"
import { cn } from "@/lib/utils"
function Spinner(
⋮----
className=
```

## File: frontend/app/src/components/ui/switch.tsx

```typescript
import { cn } from "@/lib/utils";
```

## File: frontend/app/src/components/ui/table.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/tabs.tsx

```typescript
import { cn } from "@/lib/utils"
function Tabs({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>)
⋮----
className=
```

## File: frontend/app/src/components/ui/textarea.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
className=
```

## File: frontend/app/src/components/ui/toggle-group.tsx

```typescript
import { type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { toggleVariants } from "@/components/ui/toggle"
⋮----
function ToggleGroup({
  className,
  variant,
  size,
  spacing = 0,
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root> &
  VariantProps<typeof toggleVariants> & {
    spacing?: number
})
⋮----
className=
```

## File: frontend/app/src/components/ui/toggle.tsx

```typescript
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
```

## File: frontend/app/src/components/ui/tooltip.tsx

```typescript
import { cn } from "@/lib/utils"
⋮----
function TooltipContent({
  className,
  sideOffset = 0,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>)
```

## File: frontend/app/src/hooks/use-debounce.ts

```typescript
import { useState, useEffect } from "react";
export function useDebounce<T>(value: T, delay: number): T;
```

## File: frontend/app/src/hooks/use-feature-check.ts

```typescript
import { Feature } from "@/api";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { useAuthContext } from "@/components/providers/auth-provider";
import { useCallback } from "react";
export function useFeatureCheck();
```

## File: frontend/app/src/hooks/use-mobile.ts

```typescript
export function useIsMobile()
⋮----
const onChange = () =>
```

## File: frontend/app/src/hooks/use-profile-image.ts

```typescript
import { getApiClient } from "@/lib/utils";
import { useCallback } from "react";
export function useProfileImage(accessToken: string);
```

## File: frontend/app/src/hooks/use-subdomain-check.ts

```typescript
import { useEffect, useState } from "react";
import { useDebounce } from "./use-debounce";
import { getApiClient } from "@/lib/utils";
import { useAppConfig } from "@/components/providers/app-config-provider";
import { ApiError, SubdomainAvailabilityDto } from "@/api";
export function useSubdomainCheck()
⋮----
const debouncedText = useDebounce(subdomain, 800); // 800ms debounce
⋮----
async function checkSubdomainAvailability()
```

## File: frontend/app/src/index.css

```css
@theme inline {
:root {
.dark {
@layer base {
⋮----
* {
body {
⋮----
/* Chart */
```

## File: frontend/app/src/lib/image-utils.ts

```typescript
export const validateImageFile = (file: File):
⋮----
const maxSize = 5 * 1024 * 1024 // 5MB
⋮----
export const createImagePreview = (file: File): Promise<string> =>
export const compressImage = (
    file: File,
    maxWidth: number = 400,
    maxHeight: number = 400,
    quality: number = 0.8
): Promise<Blob> =>
⋮----
// Calculate new dimensions
⋮----
// Draw and compress
⋮----
export const formatFileSize = (bytes: number): string =>
```

## File: frontend/app/src/lib/utils.ts

```typescript
import { ApiClient, type TenantDto } from "@/api"
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
export function cn(...inputs: ClassValue[])
export function setTenant(tenant: TenantDto | null)
export function getTenant(): TenantDto | null
export function formatPrice(amount: number, currency: string)
export function formatDate(timestamp: number)
export function formatDateWithHourAndMinute(timestamp: number | null | undefined)
export function getApiClient(accessToken?: string)
⋮----
// console.log("Getting API client with access token:", accessToken);
⋮----
export function extractErrorMessage(error: unknown): string
```

## File: frontend/app/src/main.tsx

```typescript
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { ThemeProvider } from './components/providers/theme-provider.tsx'
⋮----
import { BrowserRouter } from "react-router";
import { AuthProvider } from './components/providers/auth-provider.tsx'
import { AppConfigProvider } from '@/components/providers/app-config-provider.tsx'
```

## File: frontend/app/src/vite-env.d.ts

```typescript
/// <reference types="vite/client" />
```

## File: frontend/app/src/worker.ts

```typescript

```

## File: frontend/app/tsconfig.app.json

```json
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.app.tsbuildinfo",
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,

    /* Bundler mode */
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": false,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",

    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true,

    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "exclude": ["src/api/*"]
}
```

## File: frontend/app/tsconfig.json

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ],
  "compilerOptions": {
    "baseUrl": ".",
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

## File: frontend/app/tsconfig.node.json

```json
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.node.tsbuildinfo",
    "target": "esnext",
    "lib": ["esnext"],
    "module": "esnext",
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    /* Bundler mode */
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,

    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["vite.config.ts"]
}
```

## File: frontend/app/vite.config.ts

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
// https://vite.dev/config/
```

## File: fsrapp.code-workspace

```
{
	"folders": [
		{
			"name": "backend",
			"path": "backend"
		},
		{
			"name": "app",
			"path": "frontend/app"
		},
		{
			"path": "caddy"
		}
	],
	"settings": {}
}
```

## File: infra/helm/Chart.yaml

```yaml
apiVersion: v2
name: fastapi-react-app
description: A Helm chart for FastAPI React Full-Stack Application
type: application
version: 0.1.0
appVersion: "1.0.0"
keywords:
  - fastapi
  - react
  - fullstack
  - web
maintainers:
  - name: sajankumarv
    email: sajankumarv@example.com
```

## File: infra/helm/templates/\_helpers.tpl

```
{{/*
Expand the name of the chart.
*/}}
{{- define "fastapi-react-app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "fastapi-react-app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "fastapi-react-app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "fastapi-react-app.labels" -}}
helm.sh/chart: {{ include "fastapi-react-app.chart" . }}
{{ include "fastapi-react-app.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "fastapi-react-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fastapi-react-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "fastapi-react-app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "fastapi-react-app.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
```

## File: infra/helm/templates/deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "fastapi-react-app.fullname" . }}
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
spec:
  {{- if not .Values.autoscaling.enabled }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "fastapi-react-app.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      labels:
        {{- include "fastapi-react-app.selectorLabels" . | nindent 8 }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      serviceAccountName: {{ include "fastapi-react-app.serviceAccountName" . }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: 8000
              protocol: TCP
          env:
            {{- toYaml .Values.env | nindent 12 }}
          livenessProbe:
            {{- toYaml .Values.livenessProbe | nindent 12 }}
          readinessProbe:
            {{- toYaml .Values.readinessProbe | nindent 12 }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
```

## File: infra/helm/templates/hpa.yaml

```yaml
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "fastapi-react-app.fullname" . }}
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "fastapi-react-app.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    {{- if .Values.autoscaling.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
    {{- end }}
    {{- if .Values.autoscaling.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetMemoryUtilizationPercentage }}
    {{- end }}
{{- end }}
```

## File: infra/helm/templates/ingress.yaml

```yaml
{{- if .Values.ingress.enabled -}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "fastapi-react-app.fullname" . }}
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- if .Values.ingress.className }}
  ingressClassName: {{ .Values.ingress.className }}
  {{- end }}
  {{- if .Values.ingress.tls }}
  tls:
    {{- range .Values.ingress.tls }}
    - hosts:
        {{- range .hosts }}
        - {{ . | quote }}
        {{- end }}
      secretName: {{ .secretName }}
    {{- end }}
  {{- end }}
  rules:
    {{- range .Values.ingress.hosts }}
    - host: {{ .host | quote }}
      http:
        paths:
          {{- range .paths }}
          - path: {{ .path }}
            pathType: {{ .pathType }}
            backend:
              service:
                name: {{ include "fastapi-react-app.fullname" $ }}
                port:
                  number: {{ $.Values.service.port }}
          {{- end }}
    {{- end }}
{{- end }}
```

## File: infra/helm/templates/mongodb.yaml

```yaml
{{- if .Values.mongodb.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "fastapi-react-app.fullname" . }}-mongodb
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
    app.kubernetes.io/component: mongodb
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "fastapi-react-app.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: mongodb
  template:
    metadata:
      labels:
        {{- include "fastapi-react-app.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: mongodb
    spec:
      containers:
        - name: mongodb
          image: mongo:7.0
          ports:
            - containerPort: 27017
              name: mongodb
          {{- if .Values.mongodb.persistence.enabled }}
          volumeMounts:
            - name: mongodb-storage
              mountPath: /data/db
          {{- end }}
      {{- if .Values.mongodb.persistence.enabled }}
      volumes:
        - name: mongodb-storage
          persistentVolumeClaim:
            claimName: {{ include "fastapi-react-app.fullname" . }}-mongodb-pvc
      {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: mongodb
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
    app.kubernetes.io/component: mongodb
spec:
  ports:
    - port: 27017
      targetPort: 27017
      name: mongodb
  selector:
    {{- include "fastapi-react-app.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: mongodb
{{- if .Values.mongodb.persistence.enabled }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "fastapi-react-app.fullname" . }}-mongodb-pvc
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
    app.kubernetes.io/component: mongodb
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: {{ .Values.mongodb.persistence.size }}
{{- end }}
{{- end }}
```

## File: infra/helm/templates/secret.yaml

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: app-secrets
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
type: Opaque
data:
  # Base64 encoded secrets - Replace with your actual values
  jwt-secret: {{ "your-jwt-secret-key" | b64enc | quote }}
  refresh-token-secret: {{ "your-refresh-token-secret" | b64enc | quote }}
  smtp-user: {{ "your-email@gmail.com" | b64enc | quote }}
  smtp-password: {{ "your-app-password" | b64enc | quote }}
  smtp-mail-from: {{ "your-email@gmail.com" | b64enc | quote }}
```

## File: infra/helm/templates/service.yaml

```yaml
apiVersion: v1
kind: Service
metadata:
  name: { { include "fastapi-react-app.fullname" . } }
  labels: { { - include "fastapi-react-app.labels" . | nindent 4 } }
spec:
  type: { { .Values.service.type } }
  ports:
    - port: { { .Values.service.port } }
      targetPort: { { .Values.service.targetPort } }
      protocol: TCP
      name: http
  selector: { { - include "fastapi-react-app.selectorLabels" . | nindent 4 } }
```

## File: infra/helm/templates/serviceaccount.yaml

```yaml
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "fastapi-react-app.serviceAccountName" . }}
  labels:
    {{- include "fastapi-react-app.labels" . | nindent 4 }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
```

## File: infra/helm/values.yaml

```yaml
# Default values for fastapi-react-app
replicaCount: 2
image:
  repository: sajanv88/fastapi-react-app
  pullPolicy: IfNotPresent
  tag: "latest"
imagePullSecrets: []
nameOverride: ""
fullnameOverride: ""
serviceAccount:
  create: true
  annotations: {}
  name: ""
podAnnotations: {}
podSecurityContext:
  fsGroup: 2000
securityContext:
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: false
  runAsNonRoot: true
  runAsUser: 1000
service:
  type: ClusterIP
  port: 80
  targetPort: 8000
ingress:
  enabled: true
  className: ""
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: your-app.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: fastapi-react-tls
      hosts:
        - your-app.example.com
resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 250m
    memory: 256Mi
autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80
nodeSelector: {}
tolerations: []
affinity: {}
# Environment variables
env:
  - name: MONGO_URI
    value: "mongodb://mongodb:27017"
  - name: MONGO_DB_NAME
    value: "fastapi_app"
  - name: JWT_SECRET
    valueFrom:
      secretKeyRef:
        name: app-secrets
        key: jwt-secret
  - name: REFRESH_TOKEN_SECRET
    valueFrom:
      secretKeyRef:
        name: app-secrets
        key: refresh-token-secret
  - name: SMTP_HOST
    value: "smtp.gmail.com"
  - name: SMTP_PORT
    value: "587"
  - name: SMTP_USER
    valueFrom:
      secretKeyRef:
        name: app-secrets
        key: smtp-user
  - name: SMTP_PASSWORD
    valueFrom:
      secretKeyRef:
        name: app-secrets
        key: smtp-password
  - name: SMTP_MAIL_FROM
    valueFrom:
      secretKeyRef:
        name: app-secrets
        key: smtp-mail-from
  - name: SMTP_MAIL_FROM_NAME
    value: "FastAPI React App"
  - name: BASE_URL
    value: "https://your-app.example.com"
# Health checks
livenessProbe:
  httpGet:
    path: /api/v1/users/
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /api/v1/users/
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
# MongoDB dependency
mongodb:
  enabled: true
  auth:
    enabled: false
  persistence:
    enabled: true
    size: 8Gi
```

## File: infra/README.md

````markdown
# FastAPI React App - Kubernetes Deployment

This Helm chart deploys a full-stack FastAPI React application to Kubernetes.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.2.0+
- Docker registry access (public registry used by default)

## Installation

### 1. Update values.yaml

Edit `values.yaml` to configure:

- Docker image repository and tag
- Domain name for ingress
- Secret values (JWT, SMTP credentials)

### 2. Install the Chart

```bash
# Install with default values
helm install fastapi-react-app ./infra/helm

# Install with custom values
helm install fastapi-react-app ./infra/helm -f custom-values.yaml

# Install in specific namespace
kubectl create namespace fastapi-app
helm install fastapi-react-app ./infra/helm -n fastapi-app
```

### 3. Update Secrets

Before deploying, update the secrets in `templates/secret.yaml` or create them separately:

```bash
kubectl create secret generic app-secrets \
  --from-literal=jwt-secret="your-jwt-secret" \
  --from-literal=refresh-token-secret="your-refresh-secret" \
  --from-literal=smtp-user="your-email@gmail.com" \
  --from-literal=smtp-password="your-app-password" \
  --from-literal=smtp-mail-from="your-email@gmail.com"
```

## Configuration

### Key Values

| Parameter               | Description             | Default                      |
| ----------------------- | ----------------------- | ---------------------------- |
| `image.repository`      | Docker image repository | `sajanv88/fastapi-react-app` |
| `image.tag`             | Docker image tag        | `latest`                     |
| `replicaCount`          | Number of replicas      | `2`                          |
| `service.type`          | Kubernetes service type | `ClusterIP`                  |
| `ingress.enabled`       | Enable ingress          | `true`                       |
| `ingress.hosts[0].host` | Domain name             | `your-app.example.com`       |
| `mongodb.enabled`       | Deploy MongoDB          | `true`                       |

### Environment Variables

The chart automatically configures:

- MongoDB connection
- JWT secrets
- SMTP configuration
- Base URL for frontend

## Features

✅ **Production Ready**

- Security contexts and non-root containers
- Resource limits and requests
- Health checks (liveness/readiness probes)
- Horizontal Pod Autoscaling (HPA)

✅ **Scalable**

- Multiple replicas with load balancing
- Autoscaling based on CPU/memory
- Persistent storage for MongoDB

✅ **Secure**

- Secrets management for sensitive data
- HTTPS with cert-manager integration
- Network policies ready

✅ **Observable**

- Health check endpoints
- Resource monitoring
- Structured logging

## Deployment Steps

### 1. Build and Push Docker Image

```bash
# Build the image
docker build -f backend/Dockerfile -t sajanv88/fastapi-react-app:v1.0.0 .

# Push to registry
docker push sajanv88/fastapi-react-app:v1.0.0
```

### 2. Deploy to Kubernetes

```bash
# Deploy with specific image tag
helm install fastapi-react-app ./infra/helm \
  --set image.tag=v1.0.0 \
  --set ingress.hosts[0].host=your-domain.com
```

### 3. Verify Deployment

```bash
# Check pods
kubectl get pods

# Check services
kubectl get svc

# Check ingress
kubectl get ingress

# View logs
kubectl logs -l app.kubernetes.io/name=fastapi-react-app
```

## Upgrade

```bash
# Upgrade with new image
helm upgrade fastapi-react-app ./infra/helm \
  --set image.tag=v1.1.0

# Upgrade with new values
helm upgrade fastapi-react-app ./infra/helm -f new-values.yaml
```

## Uninstall

```bash
helm uninstall fastapi-react-app
```

## Customization

### Custom Values File

Create `custom-values.yaml`:

```yaml
image:
  repository: your-registry/your-app
  tag: v1.0.0

ingress:
  hosts:
    - host: your-app.com
      paths:
        - path: /
          pathType: Prefix

resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 500m
    memory: 512Mi

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 20
```

### External MongoDB

To use external MongoDB, disable the built-in one:

```yaml
mongodb:
  enabled: false

env:
  - name: MONGO_URI
    value: "mongodb://external-mongo:27017"
```

## Monitoring

The application exposes health check endpoints:

- Liveness: `/api/v1/users/`
- Readiness: `/api/v1/users/`

Integrate with Prometheus for metrics collection.

## Support

For issues and questions, please check the application logs:

```bash
kubectl logs -l app.kubernetes.io/name=fastapi-react-app --tail=100
```
````

## File: LICENSE

```
MIT License

Copyright (c) 2025 Sajan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## File: README.md

````markdown
# Full-Stack FastAPI React Multi-Tenancy Template

A modern full-stack Multi-tenant application template featuring a FastAPI backend with Clean Architecture, React frontend with TypeScript, and comprehensive tooling for development and deployment.

## ✨ Key Features

This full-stack template provides enterprise-ready features out of the box:

### 🔐 User Management

- **Authentication & Authorization**: JWT-based authentication with refresh tokens
- **User Registration**: Self-registration with email verification
- **Profile Management**: Complete user profile with image upload support
- **Account Management**: User activation, deactivation, and role assignment
- **Password Management**: Secure password reset and update functionality
- **Passkeys Management**: Passkeys and management
- **Magic link**: Passwordless login via Magic link

### 🏢 Tenant Management

- **Multi-tenancy Support**: Isolated data and resources per tenant
- **Tenant Search**: Advanced search functionality by name and subdomain
- **Tenant Administration**: Create, update, and manage tenant configurations
- **Subdomain Routing**: Custom subdomain support for each tenant
- **Custom Domain Routing**: Bring your own domain

### 👥 Role Management

- **Role-Based Access Control (RBAC)**: Granular permission system
- **Dynamic Roles**: Create and manage custom roles with specific permissions
- **Role Assignment**: Assign roles to users within tenant contexts
- **Permission Matrix**: Fine-grained control over feature access

### ☁️ Cloud Storage Integration

- **Azure Blob Storage**: Native integration with Azure cloud storage
- **AWS S3 Compatible**: Full S3 API compatibility for file storage
- **File Upload Management**: Secure file upload with validation and processing
- **Storage Abstraction**: Seamless switching between storage providers

### 🤖 AI Chat Interface

- **Local AI Models**: Integration with locally installed AI models
- **Real-time Streaming**: Live response streaming for better user experience
- **Chat History**: Persistent conversation history and management
- **Model Selection**: Dynamic switching between different AI models
- **Responsive Design**: Mobile-optimized chat interface

### 💳 Stripe Payment Integration

- **Payment Gateway Configuration**: Easy Stripe API and webhook setup
- **Product Management**: Create and manage products with CRUD operations
- **Billing Plans**: Flexible plan creation with monthly/yearly intervals
- **Trial Periods**: Toggle trial periods for any plan (default 14 days)
- **Invoice Management**: Comprehensive invoice listing and tracking
- **Checkout Sessions**: Billing record management with pagination
- **Multi-Currency Support**: Accept payments in multiple currencies
- **Subscription Management**: Handle one-time and recurring payments

### 🎛️ Feature Management

- **Feature Toggles**: Enable/disable features per tenant
- **Dynamic Feature Control**: Real-time feature activation/deactivation
- **Visual Feature Dashboard**: Intuitive interface for feature management (Comming soon!)
- **Granular Permissions**: Control feature access at tenant level

### 🏠 Tenant Experience

- **Custom Branding**: Tenant-specific configurations and settings (Coming soon!)
- **Inactive Tenant View**: Professional inactive account messaging
- **DNS Configuration**: Step-by-step custom domain setup
- **Tenant Settings**: Comprehensive tenant information management

### Coolify Integration

- **Domain Mapping**: Assign domain easily with Coolify app
- **Deployment**: Auto deploy when new domain added.
- **Toggle on/off**: Via env

## Watch Demo

![DEMO](/demo.gif)
![Multi-tenancy DNS Demo](/dns.gif)
[Check out deployed DEMO Version](https://demo.fsrapp.xyz/)

### Login as Host

Use the following credentials to log in as the **host administrator**:

| Username         | Password  |
| ---------------- | --------- |
| admin@fsrapp.xyz | Test@123! |

---

### Login as Tenant

To log in as a **tenant admin**:

1. On the login page, search a **dentally** tenant from the search field.
2. Then enter the credentials below:

| Username          | Password      |
| ----------------- | ------------- |
| admin@dentally.nl | Dentally@123! |

### Login as a Tenant User

1. First, create a new user while logged in as the **Tenant Admin**.
2. After creating the user, **log out** from the admin account.
3. Finally, **log in** again using the newly created user's credentials.

## 🏗️ Architecture Overview

This project follows a **Clean Architecture** pattern with clear separation between backend and frontend, designed for scalability, maintainability, and testability.

### Technology Stack

**Backend:**

- **FastAPI** - Modern Python web framework
- **MongoDB** - NoSQL database with Beanie ODM
- **Redis** - Caching and message broker
- **Celery** - Asynchronous task processing
- **JWT** - Authentication and authorization
- **LangChain** - AI/ML integration capabilities

**Frontend:**

- **React 18** - Modern UI library
- **TypeScript** - Type-safe JavaScript
- **Vite** - Fast build tool
- **Tailwind CSS** - Utility-first CSS framework
- **shadcn/ui** - High-quality React components
- **React Hook Form** - Form handling with validation

**Infrastructure:**

- **Docker** - Containerization
- **Docker Compose** - Development environment orchestration
- **Helm** - Kubernetes deployment charts

## 📁 Project Structure

```
full_stack_fastapi_react_template/
├── backend/                    # FastAPI backend application
│   ├── api/                   # Clean Architecture layers
│   │   ├── common/           # Shared utilities and constants
│   │   ├── core/             # Core business logic
│   │   ├── domain/           # Domain models and entities
│   │   ├── infrastructure/   # External services integration
│   │   ├── interfaces/       # API controllers and routes
│   │   └── usecases/         # Application use cases
│   ├── tests/                # Test suite
│   ├── .env.example          # Environment variables template
│   ├── Dockerfile            # Container configuration
│   ├── Makefile              # Development commands
│   ├── pyproject.toml        # Python dependencies and config
│   └── requirements.txt      # Python dependencies (fallback)
│
├── frontend/                  # React frontend application
│   └── app/                  # Main application code
│       ├── src/
│       │   ├── api/          # Auto-generated API client
│       │   ├── components/   # React components
│       │   │   ├── features/ # Feature-specific components
│       │   │   ├── layouts/  # Layout components
│       │   │   ├── providers/# Context providers
│       │   │   ├── shared/   # Reusable components
│       │   │   └── ui/       # shadcn/ui components
│       │   ├── hooks/        # Custom React hooks
│       │   ├── lib/          # Utility functions
│       │   └── assets/       # Static assets
│       ├── public/           # Public static files
│       ├── package.json      # Node.js dependencies
│       ├── vite.config.ts    # Vite configuration
│       └── components.json   # shadcn/ui configuration
│
├── infra/                     # Infrastructure and deployment
│   └── helm/                 # Kubernetes Helm charts
│       ├── templates/        # Kubernetes manifests
│       ├── Chart.yaml        # Helm chart metadata
│       └── values.yaml       # Default configuration values
│
└── dev.compose.yaml          # Docker Compose for development
```

## 🔧 Backend Architecture

The backend follows **Clean Architecture** principles with clear layer separation:

### Layer Structure

- **Domain Layer** (`domain/`): Core business entities and rules
- **Use Cases Layer** (`usecases/`): Application-specific business logic
- **Interface Adapters** (`interfaces/`): API controllers, presenters
- **Infrastructure Layer** (`infrastructure/`): External services, databases, message queues
- **Common Layer** (`common/`): Shared utilities, constants, and configurations

### Key Features

- **Authentication & Authorization**: JWT-based with refresh tokens, Passkey and Magic Link
- **Database**: MongoDB with Beanie ODM for async operations
- **Caching**: Redis for performance optimization
- **Background Tasks**: Celery with Redis broker
- **Email**: SMTP integration with async mail sending
- **AI Integration**: LangChain for AI/ML capabilities
- **Stripe Integration**: Complete payment gateway with products, plans, and subscriptions
- **API Documentation**: Auto-generated OpenAPI/Swagger docs
- **Testing**: Comprehensive test suite with pytest

### Dependencies Management

- **uv**: Fast Python package installer and resolver
- **pyproject.toml**: Modern Python dependency specification
- **Virtual Environment**: Isolated Python environment in `.venv/`

## 🎨 Frontend Architecture

The frontend is built with modern React patterns and TypeScript for type safety:

### Component Organization

- **Features** (`components/features/`): Business logic components
  - **Auth**: Login, registration, password reset, passkey, and magic link authentication
  - **Profile**: User profile management with image upload
  - **Chat**: AI chat interface with streaming responses
  - **Tenant**: Tenant management, settings, and feature toggles
  - **Billings/Stripe**: Complete payment gateway integration
    - Stripe configuration and settings
    - Product management (CRUD operations)
    - Billing plan creation and management
    - Invoice listing and tracking
    - Checkout session overview
    - Trial period management
- **Layouts** (`components/layouts/`): Page layout components (dashboard, default)
- **Providers** (`components/providers/`): React context providers for global state
- **Shared** (`components/shared/`): Reusable components across features
- **UI** (`components/ui/`): Low-level shadcn/ui components

### Key Features

- **Type Safety**: Full TypeScript integration
- **Component Library**: shadcn/ui for consistent design system
- **Form Handling**: React Hook Form with Zod validation
- **Routing**: React Router for client-side navigation
- **State Management**: React Context for global state
- **Styling**: Tailwind CSS with responsive design
- **API Integration**: Auto-generated client from OpenAPI specs
- **Payment Integration**: Complete Stripe payment gateway components
- **Feature Management**: Dynamic feature toggle interface
- **Professional UI**: Modern card-based layouts with proper loading states

### Build System

- **Vite**: Fast development server and build tool
- **ESLint**: Code linting and formatting
- **TypeScript**: Compile-time type checking
- **pnpm**: Efficient package management

## 🐳 Development Environment

### Stripe Payment Integration Setup

To use the Stripe payment features, you need to configure your Stripe account:

1. **Get Stripe API Keys**:

   - Sign up at [Stripe Dashboard](https://dashboard.stripe.com)
   - Get your API keys from Developers → API keys
   - Get your webhook secret from Developers → Webhooks

2. **Configure Stripe in Application**:

   - Navigate to Stripe Configuration page in the app
   - Enter your Stripe Secret Key
   - Enter your Webhook Secret
   - Set default currency (e.g., USD, EUR)
   - Choose payment mode (one-time, recurring, or both)
   - Set trial period days (optional)

3. **Create Products and Plans**:
   - Navigate to Products page to create your offerings
   - Use "Add a Plan" to create pricing plans
   - Toggle trial periods for any plan
   - Monitor billing through the Billing Overview dashboard

### Prerequisites

- Python 3.13+
- Node.js 22+
- Docker & Docker Compose
- pnpm (recommended) or npm
- Git

### Getting Started

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/sajanv88/full_stack_fastapi_react_template.git
   cd full_stack_fastapi_react_template
   ```

### Environment Setup

1. **Backend Environment**:

   ```bash
   cp backend/.env.example backend/.env
   ```

   Edit `backend/.env` with your configuration:

   ```bash
        # MongoDB settings
        MONGO_URI=mongodb://127.0.0.1:27012
        MONGO_DB_NAME=full_stack_fastapi_react_template

        # JWT settings
        JWT_SECRET=your_jwt_secret_key
        REFRESH_TOKEN_SECRET=your_refresh_jwt_secret_key

        # Configure your default app admin credentials.
        ADMIN_EMAIL=admin@example.com
        ADMIN_PASSWORD=Test@123!

        # SMTP Email settings
        SMTP_HOST="localhost"
        SMTP_PORT=1023
        SMTP_USER=""
        SMTP_PASSWORD=""
        SMTP_MAIL_FROM=noreply@example.com
        SMTP_MAIL_FROM_NAME="Full-Stack Fast API"
        SMTP_STARTTLS=False
        SMTP_SSL_TLS=False
        SMTP_USE_CREDENTIALS=False
        SMTP_VALIDATE_CERTS=False

        # Redis settings

        REDIS_URI="redis://localhost:6372/0"

        # Celery settings
        CELERY_BROKER_URL="redis://localhost:6372/0"
        CELERY_RESULT_BACKEND="redis://localhost:6372/0"

        # App configuration
        APP_TITLE="Full-Stack FastAPI React Template"
        APP_VERSION="0.1.0"

        API_ENDPOINT_BASE="http://localhost:8000/api"

        # Configure Multi-Tenancy
        MULTI_TENANCY_STRATEGY="header"

        # Host main domain name
        HOST_MAIN_DOMAIN="fsrapp.com"

        # Environment
        FASTAPI_ENV="development"  # Options: "development", "production"

        # Ollama settings
        OLLAMA_HOST="http://localhost:11434"

        # Coolify settings for deployment
         COOLIFY_ENABLED="false" # Set to "true" to enable Coolify integration
         COOLIFY_API_URL="https://{replace_with_your_coolify_instance_endpoint}/api/v1"
         COOLIFY_API_KEY="{replace_with_your_coolify_api_key}" # Read here https://coolify.io/docs/api-reference/authorization
         COOLIFY_APPLICATION_ID="{replace_with_your_coolify_application_id}" # Read here https://coolify.io/docs/api-reference/api/operations/get-application-by-uuid


         # Default aws s3 settings for file uploads this belongs to the Host
         AWS_REGION="eu-central-1"
         AWS_ACCESS_KEY_ID="Ab..."
         AWS_SECRET_ACCESS_KEY="xxe..."
         AWS_S3_BUCKET_NAME="fs.."

         # Stripe credentials settings
         STRIPE_API_KEY="rk_test_5..."
         STRIPE_PUBLISHABLE_KEY="pk_test_5..."
         STRIPE_SECRET_KEY="sk_test_$..."

   ```

2. **Start Infrastructure Services**:

   ```bash
   docker-compose -f dev.compose.yaml up -d
   ```

   This starts:

   - MongoDB (port 27012)
   - Redis (port 6372)
   - Fake SMTP Server (ports 1023, 1083)
   - Caddy proxy for local development ssl. Useful for testing passkey login in local development.

3. **Backend Development**:

   ```bash
   cd backend
   uv sync                      # Install dependencies
   uv run fastapi dev api        # Start development server
   ```

4. **Frontend Development**:
   ```bash
   cd frontend/app
   pnpm install              # Install dependencies
   pnpm dev                  # Start development server
   ```

### Available Services

- **Backend API**: http://localhost:8000
- **Frontend App**: http://localhost:5173
- **API Documentation**: http://localhost:8000/docs
- **MongoDB**: localhost:27012
- **Redis**: localhost:6372
- **Fake SMTP UI**: http://localhost:1083

### Available Features in UI

Once you have the application running, you can access the following features:

**Authentication & User Management:**

- Login with email/password
- Passkey authentication (WebAuthn)
- Magic link authentication
- User registration and profile management
- Password reset functionality

**Tenant Management:**

- Tenant settings and configuration
- Custom domain DNS setup
- Feature toggle management
- Inactive tenant view with support information

**Stripe Billing & Payments:**

- Stripe configuration interface
- Product management (create, edit, delete)
- Billing plan creation with trial periods
- Billing overview dashboard
- Invoice listing and management
- Checkout session tracking

**AI Chat:**

- Real-time AI chat interface
- Chat history management
- Model selection

**Dashboard:**

- Overview of key metrics
- Quick access to all features
- Responsive design for mobile and desktop

## 🔄 Integration & Workflow

### API Client Generation

The frontend automatically generates TypeScript API clients from the backend's OpenAPI specification:

```bash
cd frontend/app
pnpm run generate:api
```

This creates type-safe API clients in `src/api/` directory.

### Development Workflow

1. **Backend First**: Define API endpoints and models
2. **Generate Client**: Update frontend API client
3. **Frontend Integration**: Use generated clients in React components
4. **Type Safety**: TypeScript ensures end-to-end type safety

### Background Tasks

Start Celery worker for background processing:

```bash
cd backend
make worker          # Start Celery worker (It is required for post tenant creation process)
make flower          # Start Flower monitoring UI
```

## 🚀 Deployment

### Docker Deployment

Each service includes production-ready Dockerfiles:

- **Backend**: Multi-stage build with Python optimization
- **Frontend**: Static build served by backend.

### Kubernetes Deployment

Helm charts provided in `infra/helm/` for Kubernetes deployment:

```bash
cd infra/helm
helm install my-app . -f values.yaml
```

## 🔧 Configuration

### Backend Configuration

Environment variables in `backend/.env`:

- Database connections (MongoDB, Redis)
- JWT secrets and security settings
- SMTP email configuration
- Admin user credentials
- Feature flags and API settings

### Frontend Configuration

Configuration in `frontend/app/`:

- `vite.config.ts`: Build and development settings
- `components.json`: shadcn/ui component configuration
- `tsconfig.json`: TypeScript compiler options

## 🧪 Testing

### Backend Testing

```bash
cd backend
uv run pytest                # Run all tests
uv run pytest tests/interfaces/  # Run API tests
```

### Frontend Testing

```bash
cd frontend/app
pnpm test                    # Run component tests
pnpm build                   # Test production build
```

## 📚 Additional Resources

- **Backend API Docs**: Visit `/docs` endpoint when running
- **Component Storybook**: shadcn/ui component documentation
- **Architecture Decisions**: See individual README files in `backend/` and `frontend/app/`

## 🤝 Contributing

1. Follow the established folder structure
2. Maintain Clean Architecture principles in backend
3. Use TypeScript for all frontend code
4. Add tests for new features
5. Update API client after backend changes

---

This template provides a solid foundation for building modern, scalable full-stack applications with clear separation of concerns and comprehensive tooling.
````
