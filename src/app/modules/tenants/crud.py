from fastcrud import FastCRUD

from .models import Tenant
from .schemas import TenantCreate, TenantDelete, TenantResponse, TenantUpdate, TenantUpdateInternal

CRUDTenant = FastCRUD[Tenant, TenantCreate, TenantUpdate, TenantUpdateInternal, TenantDelete, TenantResponse]
crud_tenants = CRUDTenant(Tenant)
