"""CRUD operations for RBAC module."""

from typing import Any

from fastcrud import FastCRUD

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole
from src.app.modules.rbac.schemas import PermissionRead, RoleCreate, RoleRead, RoleUpdate

CRUDRole = TenantAwareCRUD[Role, RoleCreate, RoleUpdate, RoleUpdate, Any, RoleRead]
crud_roles = CRUDRole(Role)

CRUDPermission = FastCRUD[Permission, Any, Any, Any, Any, PermissionRead]
crud_permissions = CRUDPermission(Permission)

CRUDRolePermission = FastCRUD[RolePermission, Any, Any, Any, Any, Any]
crud_role_permissions = CRUDRolePermission(RolePermission)

CRUDUserRole = TenantAwareCRUD[UserRole, Any, Any, Any, Any, Any]
crud_user_roles = CRUDUserRole(UserRole)
