# Phase 1: Data Model

## Conceptual Schema

### 1. Permission (Global)
*Defines what CAN be done in the system.*
- `id`: Integer (PK)
- `slug`: String (Unique, e.g., "users:create")
- `description`: String

### 2. Role (Tenant-Scoped)
*Defines a bucket of permissions.*
- `id`: Integer (PK)
- `tenant_id`: UUID (FK -> tenants.id)
- `name`: String (e.g., "Manager")
- `description`: String
- `is_system`: Boolean (Default False) - Prevents deletion/renaming of default roles.
- **Constraints**: Unique(`tenant_id`, `name`)

### 3. RolePermission (Association)
*Links Roles to Permissions.*
- `role_id`: Integer (FK -> roles.id)
- `permission_id`: Integer (FK -> permissions.id)
- **PK**: Composite (`role_id`, `permission_id`)

### 4. UserRole (Tenant-Scoped Association)
*Assigns Roles to Users within a Tenant.*
- `user_id`: UUID (FK -> users.id)
- `tenant_id`: UUID (FK -> tenants.id)
- `role_id`: Integer (FK -> roles.id)
- **PK**: Composite (`user_id`, `tenant_id`, `role_id`)

## Validation Rules

1. **Tenant Isolation**: A User can only be assigned a Role that belongs to the same Tenant (or a Global System Role if applicable, but strictly scoped here to Tenant).
2. **System Roles**: Roles with `is_system=True` cannot be deleted or have their `name` updated.
3. **Uniqueness**: Role names must be unique within a specific tenant.
