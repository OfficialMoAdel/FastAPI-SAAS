from uuid import UUID

from pydantic import BaseModel

from src.app.modules._base.schemas import BaseCreate, BaseList, BaseUpdate


class PermissionBase(BaseModel):
    """Base schema for Permission."""

    slug: str
    description: str | None = None


class PermissionRead(PermissionBase):
    """Read schema for Permission with id."""

    id: int

    model_config = {"from_attributes": True}



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


class RoleRead(RoleBase):
    """Read schema for Role with id and system flag."""

    id: int
    is_system: bool

    model_config = {"from_attributes": True}



class RoleWithPermissions(RoleRead):
    """Role with its assigned permissions."""

    permissions: list[PermissionRead] = []


# User Role Assignment Schemas
class UserRoleAssignment(BaseModel):
    """Schema for assigning a role to a user."""

    user_id: int
    role_id: int


class UserRoleRead(BaseModel):
    """Schema for user role assignment response.

    C5: this was ``BaseRead``, which requires ``id: int`` and
    ``created_at: datetime``. ``UserRole`` is a pure ASSOCIATION table
    whose primary key is the composite ``(user_id, tenant_id, role_id)``
    and which has no ``id`` and no ``created_at`` column at all — so the
    schema could never validate a real ``UserRole`` row. It is now a plain
    ``BaseModel`` carrying exactly the columns the table has.

    The composite key IS the identity here, so all three parts are
    required: a ``UserRole`` is only identified by the triple.
    """

    user_id: int
    role_id: int
    tenant_id: UUID

    model_config = {"from_attributes": True}



class UserRolesResponse(BaseModel):
    """Schema for listing user's roles."""

    user_id: int
    roles: list[RoleRead] = []


class PermissionList(BaseList[PermissionRead]):
    """Standardized paginated permission list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass
