from src.app.core.db.tenant_crud import TenantAwareCRUD

from .models import User
from .schemas import UserCreateInternal, UserDelete, UserRead, UserUpdate, UserUpdateInternal

CRUDUser = TenantAwareCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]
crud_users = CRUDUser(User)

