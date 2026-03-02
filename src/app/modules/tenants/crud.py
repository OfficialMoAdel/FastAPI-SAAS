from fastcrud import FastCRUD

from src.app.modules.tenants.models import Tenant
from src.app.modules.tenants.schemas import (
    TenantCreate,
    TenantDelete,
    TenantResponse,
    TenantUpdate,
    TenantUpdateInternal,
)

CRUDTenant = FastCRUD[Tenant, TenantCreate, TenantUpdate, TenantUpdateInternal, TenantDelete, TenantResponse]
crud_tenants = CRUDTenant(Tenant)
