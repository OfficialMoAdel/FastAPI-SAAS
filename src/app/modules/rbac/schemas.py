
from pydantic import BaseModel, ConfigDict


class PermissionBase(BaseModel):
    slug: str
    description: str | None = None


class PermissionOut(PermissionBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class RoleBase(BaseModel):
    name: str
    description: str | None = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(RoleBase):
    pass


class RoleOut(RoleBase):
    id: int
    is_system: bool
    model_config = ConfigDict(from_attributes=True)


class RoleWithPermissions(RoleOut):
    """Role with its assigned permissions."""

    permissions: list[PermissionOut] = []


# User Role Assignment Schemas
class UserRoleAssignment(BaseModel):
    """Schema for assigning a role to a user."""

    user_id: int
    role_id: int


class UserRoleOut(BaseModel):
    """Schema for user role assignment response."""

    user_id: int
    role_id: int
    tenant_id: int
    model_config = ConfigDict(from_attributes=True)


class UserRolesResponse(BaseModel):
    """Schema for listing user's roles."""

    user_id: int
    roles: list[RoleOut] = []
