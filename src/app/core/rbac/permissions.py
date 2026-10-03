from enum import Enum


class Permission(str, Enum):
    # User Management (Example/Placeholder for now as referenced in specs)
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"

    # Role Management (US1)
    ROLES_READ = "roles:read"
    ROLES_MANAGE = "roles:manage"  # Includes Create, Update, Delete

    # Role Assignment (US2)
    ROLES_ASSIGN = "roles:assign"

    # Feature Flags Management
    FEATURE_FLAGS_READ = "feature_flags:read"
    FEATURE_FLAGS_MANAGE = "feature_flags:manage"

    # Notification Management
    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_MANAGE = "notifications:manage"

    # Webhooks Management
    WEBHOOKS_READ = "webhooks:read"
    WEBHOOKS_CREATE = "webhooks:create"
    WEBHOOKS_MANAGE = "webhooks:manage"

    # Tenant Management
    TENANT_UPDATE = "tenant:update"

    # === Phase 6: New Permissions ===

    # User Self-Access (Phase 6)
    USER_SELF_READ_AND_WRITE_ONLY = "users:self"
    USER_DELETE_ONLY = "users:delete"
    USER_ROLE_ASSIGN_OR_REMOVE_ONLY = "users:role_assign"

    # Billing & Products (Phase 6)
    MANAGE_BILLING = "billing:manage"
    MANAGE_PRODUCTS = "products:manage"

    # Storage Settings (Phase 4)
    MANAGE_STORAGE_SETTINGS = "storage_settings:manage"

    # File Operations (Phase 4)
    FILES_READ = "files:read"
    FILES_WRITE = "files:write"
    FILES_DELETE = "files:delete"

    # Audit Logs (Phase 7)
    AUDIT_LOGS_VIEW_ONLY = "audit_logs:read"
    AUDIT_LOGS_DOWNLOAD = "audit_logs:export"

    # Dashboard / Analytics (run-3 finding users.dashboard.metrics_missing_authorization)
    # Tenant-wide revenue, MRR, ARPU, subscription and login aggregates.
    DASHBOARD_READ = "dashboard:read"

    # Background task queue (S-38: the /tasks routes carried no permission gate).
    #
    # Enqueuing an ARQ job is a privileged, resource-consuming action, yet the
    # router declared only the ambient auth dependency — so any authenticated
    # principal on any tier could enqueue jobs and read job status.
    #
    # This is a NEW slug, so no role holds it until an operator grants it.
    # That is the fail-closed direction and is deliberate: the routes now 403
    # rather than letting every authenticated caller enqueue work.
    # `reconcile_permission_catalog` self-heals the catalog row on boot.
    TASKS_MANAGE = "tasks:manage"
