from fastcrud import FastCRUD

from .models import Tenant
from .schemas import TenantCreate, TenantResponse, TenantUpdate, TenantUpdateInternal

CRUDTenant = FastCRUD[Tenant, TenantCreate, TenantUpdate, TenantUpdateInternal, None, TenantResponse]
crud_tenants = CRUDTenant(Tenant)

