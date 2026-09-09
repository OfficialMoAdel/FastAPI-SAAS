"""RBAC module for role-based access control."""

from src.app.modules.rbac.crud import (
    CRUDAssociations,
    CRUDPermission,
    CRUDRole,
    crud_associations,
    crud_permissions,
    crud_roles,
)
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.router import get_role_service
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
from src.app.modules.rbac.service import RoleService

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
    # CRUD
    "CRUDRole",
    "CRUDPermission",
    "CRUDAssociations",
    "crud_roles",
    "crud_permissions",
    "crud_associations",
]
