from pydantic import BaseModel

from .._base.schemas import BaseCreate, BaseRead, BaseUpdate


class PermissionBase(BaseModel):
    """Base schema for Permission."""

    slug: str
    description: str | None = None


class PermissionRead(BaseRead, PermissionBase):
    """Read schema for Permission with id and timestamps."""

    pass


# Backward compatibility alias
PermissionOut = PermissionRead


class RoleBase(BaseModel):
    """Base schema for Role."""

    name: str
    description: str | None = None


class RoleCreate(BaseCreate, RoleBase):
    """Create schema for Role."""

    pass


class RoleUpdate(BaseUpdate):
    """Update schema for Role with optional fields."""

    name: str | None = None
    description: str | None = None


class RoleRead(BaseRead, RoleBase):
    """Read schema for Role with id, timestamps, and system flag."""

    is_system: bool


# Backward compatibility alias
RoleOut = RoleRead


class RoleWithPermissions(RoleRead):
    """Role with its assigned permissions."""

    permissions: list[PermissionRead] = []


# User Role Assignment Schemas
class UserRoleAssignment(BaseModel):
    """Schema for assigning a role to a user."""

    user_id: int
    role_id: int


class UserRoleRead(BaseRead):
    """Schema for user role assignment response."""

    user_id: int
    role_id: int
    tenant_id: int


# Backward compatibility alias
UserRoleOut = UserRoleRead


class UserRolesResponse(BaseModel):
    """Schema for listing user's roles."""

    user_id: int
    roles: list[RoleRead] = []
