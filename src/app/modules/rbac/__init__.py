"""RBAC module for role-based access control."""

from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import (
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    RoleWithPermissions,
    UserRoleAssignment,
    UserRoleRead,
    UserRolesResponse,
)
from src.app.modules.rbac.service import RoleService, get_role_service

__all__ = [
    # Models
    "Permission",
    "Role",
    "RolePermission",
    "UserRole",
    # Schemas
    "PermissionRead",
    "RoleCreate",
    "RoleRead",
    "RoleUpdate",
    "RoleWithPermissions",
    "UserRoleAssignment",
    "UserRoleRead",
    "UserRolesResponse",
    # Service
    "RoleService",
    "get_role_service",
]
