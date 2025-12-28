from typing import Optional

from pydantic import BaseModel, ConfigDict


class PermissionBase(BaseModel):
    slug: str
    description: Optional[str] = None


class PermissionOut(PermissionBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(RoleBase):
    pass


class RoleOut(RoleBase):
    id: int
    is_system: bool
    model_config = ConfigDict(from_attributes=True)
