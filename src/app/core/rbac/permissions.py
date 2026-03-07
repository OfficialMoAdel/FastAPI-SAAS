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
    FEATURE_FLAGS_MANAGE = "feature_flags:manage"

    # Notification Management
    NOTIFICATIONS_MANAGE = "notifications:manage"

    # Webhooks Management
    WEBHOOKS_READ = "webhooks:read"
    WEBHOOKS_MANAGE = "webhooks:manage"

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
