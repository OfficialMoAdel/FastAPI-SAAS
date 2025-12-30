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

