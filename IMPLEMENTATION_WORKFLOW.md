# Implementation Workflow (v2.0 - Expert Reviewed)

**Project:** Backend Migration from MongoDB to PostgreSQL
**Based on:** MIGRATION_PLAN.md
**Strategy:** Systematic | **Depth:** Deep | **Parallel Execution:** Enabled
**Generated:** 2026-01-02
**Last Updated:** 2026-01-02 (Expert Review Applied)

---

## Table of Contents

1. [Workflow Overview](#workflow-overview)
2. [Phase Dependencies](#phase-dependencies)
3. [Parallel Execution Groups](#parallel-execution-groups)
4. [Sprint 0: Foundation (Security & Architecture)](#sprint-0-foundation-security--architecture)
5. [Detailed Task Breakdown](#detailed-task-breakdown)
6. [Sprint Planning](#sprint-planning)
7. [Quality Gates](#quality-gates)
8. [Risk Assessment](#risk-assessment)
9. [Milestone Definitions](#milestone-definitions)
10. [Security Requirements](#security-requirements)
11. [Performance Requirements](#performance-requirements)
12. [Testing Requirements](#testing-requirements)

---

## Workflow Overview

This workflow provides a systematic approach to migrating 82 new files and modifying 10 existing files across 7 phases. The implementation is designed for parallel execution where dependencies allow.

### Key Metrics

| Metric | Value |
|--------|-------|
| Total Files | 92 |
| New Modules | 13 |
| Modified Modules | 5 |
| Database Tables | 19 |
| New Endpoints | 45+ |
| Estimated Sprints | 8-10 |

---

## Phase Dependencies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DEPENDENCY CHAIN                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Phase 1: Authentication
    │
    ├──► Phase 2: User Management
    │       (User model changes needed for SSO provider ID)
    │
    └──► Phase 6: RBAC Enhancements
            (Auth module modifications needed)

Phase 3: Billing & Payments
    │
    └──► Phase 6: RBAC Enhancements
            (MANAGE_BILLING permission needed)

Phase 4: File Storage
    │
    └──► Phase 6: RBAC Enhancements
            (MANAGE_STORAGE_SETTINGS permission needed)

Phase 5: AI Features
    │       (No dependencies)
    │
    └──► Can run in parallel with any other phase

Phase 7: Audit Logs Enhancements
    │
    └──► Can run after Phase 1 (auth changes)
```

---

## Parallel Execution Groups

### Group A: Authentication Foundation (Phase 1)
**Can execute in parallel:**
- Passkey module (models, schemas, crud)
- Magic Link module (models, schemas, crud)
- SSO Settings module (models, schemas, crud)

**Sequential within group:**
- Auth module modifications (depends on all above)

### Group B: User Enhancements (Phase 2)
**Can execute in parallel:**
- User model modifications
- User Preferences module

**Note:** Must start after Group A completes `sso_provider_id` field

### Group C: Billing Infrastructure (Phase 3)
**Can execute in parallel:**
- Stripe module
- Subscription Plans module
- Products module
- Pricing module

**Sequential within group:**
- Products must complete before Pricing (foreign key dependency)

### Group D: Storage Infrastructure (Phase 4)
**Can execute in parallel:**
- Storage Settings module
- Files module (except externals)

**Sequential:**
- Files externals (S3, Azure, Local) must complete after Files module

### Group E: AI Features (Phase 5)
**Can execute in parallel:**
- AI Chat module
- AI Settings module

### Group F: RBAC Enhancements (Phase 6)
**Must run after:**
- Phase 1 Auth modifications
- Phase 3 Billing (for MANAGE_BILLING permission)

### Group G: Audit Enhancements (Phase 7)
**Must run after:**
- Phase 1 Auth modifications
- Database schema for audit logs exists

---

## Sprint 0: Foundation (Security & Architecture)

**Duration:** 1 week (Before Sprint 1)
**Priority:** CRITICAL - Must complete before any module implementation

### Task 0.1: Security Foundation

**Duration:** 3 days
**Assignee:** Security Engineer
**Files:**
- `src/app/core/security/encryption.py`
- `src/app/core/security/tokens.py`
- `src/app/core/security/rate_limits.py`

**Steps:**

1. **Credential Encryption Utilities**
   ```python
   # src/app/core/security/encryption.py
   from cryptography.fernet import Fernet
   from sqlalchemy.orm import attributes

   class CredentialEncryption:
       """Encryption for sensitive credential data (passkeys, OAuth tokens)."""

       def __init__(self):
           self.key = settings.ENCRYPTION_KEY  # 32-byte key
           self.fernet = Fernet(self.key)

       def encrypt_credential(self, credential_data: bytes) -> bytes:
           """Encrypt passkey credential data before storage."""
           return self.fernet.encrypt(credential_data)

       def decrypt_credential(self, encrypted_data: bytes) -> bytes:
           """Decrypt passkey credential data for use."""
           return self.fernet.decrypt(encrypted_data)

       def encrypt_column(self, value: str) -> str:
           """Encrypt a string value for database storage."""
           return self.fernet.encrypt(value.encode()).decode()

       def decrypt_column(self, encrypted_value: str) -> str:
           """Decrypt a database column value."""
           return self.fernet.decrypt(encrypted_value.encode()).decode()
   ```

2. **Magic Link Token Generation**
   ```python
   # src/app/core/security/tokens.py
   import secrets

   class TokenGenerator:
       """Secure token generation for magic links and verification."""

       MAGIC_LINK_LENGTH = 32  # 32 bytes = 43 base64 chars

       @staticmethod
       def generate_magic_link_token() -> str:
           """Generate cryptographically secure magic link token."""
           return secrets.token_urlsafe(MagicLinkTokenGenerator.MAGIC_LINK_LENGTH)

       @staticmethod
       def generate_sso_state() -> str:
           """Generate OAuth state parameter for CSRF protection."""
           state = secrets.token_urlsafe(16)
           # Store in Redis with 10-minute expiration
           await redis.setex(f"sso_state:{state}", 600, "valid")
           return state

       @staticmethod
       async def validate_sso_state(state: str) -> bool:
           """Validate SSO state parameter (one-time use, 10-min expiry)."""
           exists = await redis.exists(f"sso_state:{state}")
           if exists:
               await redis.delete(f"sso_state:{state}")  # Consume
               return True
           return False
   ```

3. **Rate Limiting Configuration**
   ```python
   # src/app/core/security/rate_limits.py
   RATE_LIMITS = {
       "auth_login": {"limit": 5, "period": 60},      # 5 attempts/minute
       "auth_magic_link": {"limit": 3, "period": 60},   # 3/minute
       "auth_passkey": {"limit": 10, "period": 60},       # 10/minute
       "sso_callback": {"limit": 20, "period": 60},       # 20/minute
       "file_upload": {"limit": 10, "period": 300},      # 10/5 minutes
       "api_general": {"limit": 100, "period": 60},      # 100/minute
   }
   ```

4. **Password Policy**
   ```python
   PASSWORD_POLICY = {
       "min_length": 12,
       "require_uppercase": True,
       "require_lowercase": True,
       "require_digit": True,
       "require_special": True,
       "special_chars": "!@#$%^&*()_+-=[]{}|;':\",./<>?",
       "max_age_days": 90,
       "prevent_reuse": 12,  # Number of previous passwords
   }
   ```

**Acceptance Criteria:**
- [ ] Credential encryption works for passkey data
- [ ] Magic link tokens are 32-byte entropy
- [ ] SSO state parameter prevents CSRF attacks
- [ ] Rate limits configured for all auth endpoints
- [ ] Password policy enforced on user creation/updates

---

### Task 0.2: Architecture Foundation

**Duration:** 3 days
**Assignee:** Lead Developer
**Files:**
- `src/app/modules/_base/`
- `src/app/modules/storage/interfaces.py`

**Steps:**

1. **Create Base Module Pattern**
   ```python
   # src/app/modules/_base/__init__.py
   """Base module pattern for all feature modules."""

   from .models import Base
   from .schemas import BaseCreate, BaseRead, BaseUpdate, BaseList
   from .service import BaseService

   __all__ = ["Base", "BaseCreate", "BaseRead", "BaseUpdate", "BaseList", "BaseService"]

   # src/app/modules/_base/models.py
   from sqlalchemy.orm import DeclarativeBase

   class Base(DeclarativeBase):
       """Base class for all SQLAlchemy models."""
       pass

   # src/app/modules/_base/schemas.py
   from pydantic import BaseModel
   from datetime import datetime
   from typing import Generic, TypeVar, Optional

   T = TypeVar("T", bound=Base)

   class BaseCreate(BaseModel):
       """Base schema for creating resources."""

   class BaseRead(BaseModel):
       """Base schema for reading resources."""
       id: int
       created_at: datetime
       updated_at: Optional[datetime] = None

   class BaseUpdate(BaseModel):
       """Base schema for updating resources."""

   class BaseList(BaseModel):
       """Base schema for paginated list responses."""
       items: list[BaseRead]
       total: int
       page: int
       per_page: int

   # src/app/modules/_base/service.py
   from fastcrud import FastCRUD

   class BaseService(T):
       """Base service with common CRUD operations."""

       def __init__(self, model: type[T]):
           self.crud = FastCRUD(model)
   ```

2. **Create Storage Provider Interface**
   ```python
   # src/app/modules/storage/interfaces.py
   from abc import ABC, abstractmethod
   from typing import AsyncGenerator, BinaryIO
   from dataclasses import dataclass

   @dataclass
   class StorageResult:
       """Result of a storage operation."""
       success: bool
       path: str
       size: int | None = None
       error: str | None = None

   class StorageProvider(ABC):
       """Abstract interface for storage providers."""

       @abstractmethod
       async def upload(
           self,
           file: BinaryIO,
           path: str,
           mime_type: str
       ) -> StorageResult:
           """Upload a file to storage."""
           pass

       @abstractmethod
       async def download(self, path: str) -> AsyncGenerator[bytes, None]:
           """Download a file from storage."""
           pass

       @abstractmethod
       async def delete(self, path: str) -> StorageResult:
           """Delete a file from storage."""
           pass

       @abstractmethod
       async def exists(self, path: str) -> bool:
           """Check if a file exists."""
           pass
   ```

3. **Create Async Convention Documentation**
   ```python
   # src/app/core/async_conventions.py
   """
   Async/Await Conventions for This Project

   1. All I/O operations MUST be async (database, network, file)
   2. Never mix sync and async code in the same function
   3. Use SQLAlchemy 2.0 async syntax:
      - session.execute() -> await session.execute()
      - session.commit() -> await session.commit()
   4. Service layer methods are async by default
   5. Router handlers are async by default

   Example:
   async def get_user(user_id: int, db: AsyncSession) -> User:
       result = await db.execute(select(User).where(User.id == user_id))
       return result.scalar_one_or_none()
   """

   ```

**Acceptance Criteria:**
- [ ] `_base` module created and imported by all modules
- [ ] Storage provider interface defined
- [ ] Async conventions documented and followed
- [ ] All new modules use base patterns

---

### Task 0.3: Testing Foundation

**Duration:** 1 day
**Assignee:** QA Engineer
**Files:**
- `tests/conftest.py`
- `tests/fixtures/`

**Steps:**

1. **Create Test Fixtures**
   ```python
   # tests/conftest.py
   import pytest
   from httpx import AsyncClient, ASGITransport
   from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
   from app.main import app
   from app.core.db.database import get_db

   @pytest.fixture(scope="session")
   def anyio_backend():
       return "asyncio"

   @pytest.fixture
   async def db_session():
       engine = create_async_engine(settings.DATABASE_URL)
       async with engine.begin() as conn:
           await conn.run_sync(Base.metadata.create_all)
       async with AsyncSession(engine) as session:
           yield session
           await session.rollback()

   @pytest.fixture
   async def client(db_session):
       async def override_get_db():
           yield db_session

       app.dependency_overrides[get_db] = override_get_db

       async with AsyncClient(
           transport=ASGITransport(app=app),
           base_url="http://test"
       ) as ac:
           yield ac

   @pytest.fixture
   def auth_headers(client):
       """Create authenticated headers for testing."""
       async def _get_headers(user):
           token = create_test_jwt_token(user)
           return {"Authorization": f"Bearer {token}"}
       return _get_headers
   ```

2. **Create Auth Test Helpers**
   ```python
   # tests/fixtures/auth.py
   import pytest
   from passlib.context import CryptContext

   pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

   @pytest.fixture
   def test_user_data():
       return {
           "email": "test@example.com",
           "password": "SecureP@ss123!",
           "username": "testuser",
       }

   @pytest.fixture
   def hashed_password():
       return pwd_context.hash("SecureP@ss123!")
   ```

**Acceptance Criteria:**
- [ ] Async test fixtures working
- [ ] Database fixtures support transactions
- [ ] Auth helpers available for all tests
- [ ] Pytest can run all tests

---

## Detailed Task Breakdown

### Sprint 1: Authentication Foundation (Week 1-2)

#### Task 1.1: Passkey Module - Models & Database (SECURED)
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/passkey/models.py`
- `src/app/modules/passkey/crud.py`
- Database migration script

**Steps:**
1. **Create SQLAlchemy Model with Encrypted Columns**
   ```python
   # src/app/modules/passkey/models.py
   from sqlalchemy import Integer, String, DateTime, ForeignKey, Index, Boolean
   from sqlalchemy.orm import Mapped, mapped_column, relationship
   from datetime import datetime, timezone
   from app.modules._base import Base

   class PasskeyCredential(Base):
       """Passkey credentials with encrypted sensitive data."""

       __tablename__ = "passkey_credentials"

       id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
       user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

       # 🔐 ENCRYPTED COLUMNS - Use hybrid properties for encryption
       _credential_id_encrypted: Mapped[bytes] = mapped_column("credential_id", Bytes, nullable=False)
       _public_key_encrypted: Mapped[bytes] = mapped_column("public_key", Bytes, nullable=False)

       counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
       device_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "platform_authenticator", "cross_platform"
       device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

       transports: Mapped[str | None] = mapped_column(String(255), nullable=True)  # JSON array

       created_at: Mapped[datetime] = mapped_column(
           DateTime(timezone=True),
           default=lambda: datetime.now(timezone.utc),
           nullable=False
       )
       last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
       is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

       # Relationships
       user: Mapped["User"] = relationship(back_populates="passkey_credentials", lazy="selectin")

       # Indexes
       __table_args__ = (
           Index("idx_passkey_credentials_user_id", "user_id"),
           Index("idx_passkey_credentials_active", "user_id", "is_active"),
       )

       # 🔐 Hybrid properties for encryption
       @hybrid_property
       def credential_id(self) -> bytes:
           """Decrypt credential ID on access."""
           from app.core.security.encryption import encryption
           return encryption.decrypt_credential(self._credential_id_encrypted)

       @credential_id.setter
       def credential_id(self, value: bytes):
           """Encrypt credential ID on assignment."""
           from app.core.security.encryption import encryption
           self._credential_id_encrypted = encryption.encrypt_credential(value)

       @hybrid_property
       def public_key(self) -> bytes:
           """Decrypt public key on access."""
           from app.core.security.encryption import encryption
           return encryption.decrypt_credential(self._public_key_encrypted)

       @public_key.setter
       def public_key(self, value: bytes):
           """Encrypt public key on assignment."""
           from app.core.security.encryption import encryption
           self._public_key_encrypted = encryption.encrypt_credential(value)
   ```

2. **Write Alembic Migration**
   ```python
   # migration file
   def upgrade():
       op.create_table(
           "passkey_credentials",
           sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
           sa.Column("user_id", sa.Integer(), nullable=False),
           sa.Column("credential_id", sa.LargeBinary(length=256), nullable=False),  # Encrypted
           sa.Column("public_key", sa.LargeBinary(length=512), nullable=False),  # Encrypted
           sa.Column("counter", sa.Integer(), nullable=False, default=0),
           sa.Column("device_type", sa.String(50), nullable=False),
           sa.Column("device_name", sa.String(255), nullable=True),
           sa.Column("transports", sa.String(255), nullable=True),
           sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
           sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
           sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
           sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
           sa.PrimaryKeyConstraint("id"),
           sa.Index("idx_passkey_credentials_user_id", "user_id"),
           sa.Index("idx_passkey_credentials_active", "user_id", "is_active"),
       )
   ```

3. **Implement CRUD with Encryption**
   ```python
   # src/app/modules/passkey/crud.py
   from app.modules._base import BaseService

   class PasskeyService(BaseService):
       async def create_credential(
           self,
           user_id: int,
           credential_id: bytes,
           public_key: bytes,
           device_type: str,
           device_name: str | None = None,
       ) -> PasskeyCredential:
           """Create passkey credential with encryption."""
           credential = self.model(
               user_id=user_id,
               credential_id=credential_id,  # Auto-encrypted via setter
               public_key=public_key,         # Auto-encrypted via setter
               device_type=device_type,
               device_name=device_name,
           )
           self.db.add(credential)
           await self.db.commit()
           await self.db.refresh(credential)
           return credential
   ```

**Acceptance Criteria:**
- [ ] Model inherits from `_base.Base`
- [ ] Sensitive columns (credential_id, public_key) are encrypted at rest
- [ ] All columns have proper types and constraints
- [ ] Indexes created for query optimization
- [ ] Migration runs successfully
- [ ] Unit tests for model creation with encryption

---

#### Task 1.2: Passkey Module - Schemas & CRUD
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/passkey/schemas.py`
- `src/app/modules/passkey/crud.py`

**Steps:**
1. Create Pydantic schemas (PasskeyCreate, PasskeyRead, PasskeyUpdate)
2. Implement CRUD operations using fastcrud
3. Add schema validation for passkey data
4. Write unit tests for CRUD operations

**Acceptance Criteria:**
- [ ] All schemas inherit from BaseModel
- [ ] Proper validation annotations on all fields
- [ ] CRUD operations work with async database
- [ ] Unit tests pass

---

#### Task 1.3: Passkey Module - Service & Router
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/passkey/service.py`
- `src/app/modules/passkey/router.py`

**Steps:**
1. Implement passkey registration flow (WebAuthn)
2. Implement passkey verification/Challenge flow
3. Implement passkey login flow
4. Create all 5 endpoints
5. Add authentication dependencies

**Acceptance Criteria:**
- [ ] WebAuthn protocol implemented correctly
- [ ] All endpoints documented with OpenAPI
- [ ] Authentication required on protected endpoints
- [ ] Integration tests pass

---

#### Task 1.4: Magic Link Module - Complete
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/magic_link/models.py`
- `src/app/modules/magic_link/schemas.py`
- `src/app/modules/magic_link/crud.py`
- `src/app/modules/magic_link/service.py`
- `src/app/modules/magic_link/router.py`

**Steps:**
1. Create model with token expiration logic
2. Create schemas for magic link requests
3. Implement token generation and validation
4. Integrate with email provider
5. Create send and verify endpoints

**Acceptance Criteria:**
- [ ] Token expires after configurable time (default 15 min)
- [ ] Token can only be used once
- [ ] Email integration works
- [ ] Security: tokens are cryptographically secure

---

#### Task 1.5: SSO Settings Module - Complete
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/sso_settings/models.py`
- `src/app/modules/sso_settings/schemas.py`
- `src/app/modules/sso_settings/service.py`
- `src/app/modules/sso_settings/router.py`

**Steps:**
1. Create SSO Settings model (host-level configuration)
2. Create schemas for provider configuration
3. Implement provider settings CRUD
4. Create endpoints for managing SSO providers

**Acceptance Criteria:**
- [ ] Supports Google, GitHub, Microsoft, Apple
- [ ] Client secrets are handled securely
- [ ] Providers can be enabled/disabled

---

#### Task 1.6: SSO Module - Connection Management
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/sso/models.py`
- `src/app/modules/sso/schemas.py`
- `src/app/modules/sso/crud.py`
- `src/app/modules/sso/service.py`
- `src/app/modules/sso/router.py`

**Steps:**
1. Create UserSSOConnection model
2. Implement OAuth flow handlers for each provider
3. Create callback handler
4. Implement connect/disconnect endpoints
5. Add security validation for OAuth callbacks

**Acceptance Criteria:**
- [ ] OAuth flow works for all 4 providers
- [ ] User can connect multiple providers
- [ ] User can disconnect provider
- [ ] Duplicate connections prevented

---

#### Task 1.7: Auth Module Enhancements
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/auth/schemas.py`
- `src/app/modules/auth/service.py`

**Steps:**
1. Add new schema classes for passkey, magic link, SSO
2. Update auth service to handle new flows
3. Add new service methods for each authentication type
4. Update token refresh logic if needed

**Acceptance Criteria:**
- [ ] All new auth flows integrate with existing JWT system
- [ ] Token blacklist updated for all auth methods
- [ ] Audit logging integrated

---

### Sprint 2: User Management (Week 2)

#### Task 2.1: User Model Modifications
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/users/models.py`
- Database migration script

**Steps:**
1. Add image_url column (String, nullable, max 500)
2. Add activated_at column (DateTime, nullable)
3. Add sso_provider_id column (String, nullable, max 100)
4. Add gender column (String, nullable, max 20)
5. Add preferences column (JSONB, default '{}')
6. Write and run Alembic migration

**Acceptance Criteria:**
- [ ] All new columns added successfully
- [ ] Migration is reversible (downgrade works)
- [ ] Existing data preserved

---

#### Task 2.2: User Preferences Module
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/user_preferences/models.py`
- `src/app/modules/user_preferences/schemas.py`
- `src/app/modules/user_preferences/crud.py`
- `src/app/modules/user_preferences/service.py`
- `src/app/modules/user_preferences/router.py`

**Steps:**
1. Create UserPreferences model with user_id FK
2. Create schemas with default preferences structure
3. Implement CRUD operations
4. Create endpoints: GET /user/me/preferences, PATCH /user/me/preferences
5. Add default preferences initialization on user creation

**Acceptance Criteria:**
- [ ] User preferences auto-created on first access
- [ ] Preferences validated against schema
- [ ] Tenant isolation maintained

---

#### Task 2.3: User Router Updates
**Duration:** 1 day
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/users/router.py`

**Steps:**
1. Add PATCH /user/me/image endpoint
2. Update GET /user/me to include new fields
3. Update PATCH /user/{username} to handle new fields

**Acceptance Criteria:**
- [ ] Profile image upload works
- [ ] New user fields returned in user/me endpoint
- [ ] New fields can be updated via PATCH

---

### Sprint 3: Billing & Payments (Week 3)

#### Task 3.1: Stripe Module - Core
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/stripe/models.py`
- `src/app/modules/stripe/schemas.py`
- `src/app/modules/stripe/crud.py`
- `src/app/modules/stripe/service.py`

**Steps:**
1. Create StripeSettings model (tenant-level)
2. Create BillingRecord model
3. Implement Stripe API integration
4. Create service methods for:
   - Create customer
   - Create payment intent
   - Handle webhook events
   - Manage subscriptions

**Acceptance Criteria:**
- [ ] Stripe API keys stored securely
- [ ] Customer creation works
- [ ] Payment intents created successfully
- [ ] Webhook signature verification implemented

---

#### Task 3.2: Stripe Module - Router
**Duration:** 1 day
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/stripe/router.py`

**Steps:**
1. Create GET /stripe/settings endpoint
2. Create POST /stripe/settings endpoint
3. Create POST /stripe/webhook endpoint
4. Add Stripe signature validation

**Acceptance Criteria:**
- [ ] Settings managed per tenant
- [ ] Webhook handles all relevant events
- [ ] Signature validation prevents spoofing

---

#### Task 3.3: Subscription Plans Module
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/subscription_plans/models.py`
- `src/app/modules/subscription_plans/schemas.py`
- `src/app/modules/subscription_plans/crud.py`
- `src/app/modules/subscription_plans/service.py`
- `src/app/modules/subscription_plans/router.py`

**Steps:**
1. Create SubscriptionPlan model
2. Create schemas for plan CRUD
3. Implement service methods
4. Create all 5 endpoints for plan management

**Acceptance Criteria:**
- [ ] Plans stored per tenant
- [ ] Plans have monthly/yearly pricing
- [ ] Features stored as JSONB array
- [ ] Plans can be marked as popular

---

#### Task 3.4: Products Module
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/products/models.py`
- `src/app/modules/products/schemas.py`
- `src/app/modules/products/crud.py`
- `src/app/modules/products/service.py`
- `src/app/modules/products/router.py`

**Steps:**
1. Create Product model
2. Create schemas
3. Implement CRUD
4. Create endpoints

**Acceptance Criteria:**
- [ ] Products stored per tenant
- [ ] Products can be activated/deactivated
- [ ] Basic CRUD operations work

---

#### Task 3.5: Pricing Module
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/pricing/models.py`
- `src/app/modules/pricing/schemas.py`
- `src/app/modules/pricing/crud.py`
- `src/app/modules/pricing/service.py`
- `src/app/modules/pricing/router.py`

**Steps:**
1. Create Pricing model with product_id FK
2. Create schemas
3. Implement CRUD
4. Create endpoints

**Acceptance Criteria:**
- [ ] Pricing tiers linked to products
- [ ] Supports monthly/yearly/one_time intervals
- [ ] Features stored as JSONB

---

#### Task 3.6: Billing History Endpoints
**Duration:** 1 day
**Assignee:** Backend Developer
**Files:**
- Modify existing billing module or create new router

**Steps:**
1. Create GET /billing/history endpoint
2. Create GET /billing/invoices endpoint
3. Create POST /billing/checkout endpoint

**Acceptance Criteria:**
- [ ] Billing history filtered by user/tenant
- [ ] Pagination implemented
- [ ] Checkout creates Stripe session

---

### Sprint 4: File Storage (Week 4)

#### Task 4.1: Storage Settings Module
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/storage_settings/models.py`
- `src/app/modules/storage_settings/schemas.py`
- `src/app/modules/storage_settings/crud.py`
- `src/app/modules/storage_settings/service.py`
- `src/app/modules/storage_settings/router.py`

**Steps:**
1. Create StorageSettings model
2. Create schemas with provider-specific fields
3. Implement CRUD
4. Create GET/PATCH endpoints

**Acceptance Criteria:**
- [ ] Supports S3, Azure, Local providers
- [ ] Credentials encrypted at rest
- [ ] File type restrictions enforced

---

#### Task 4.2: Files Module - Core
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/files/models.py`
- `src/app/modules/files/schemas.py`
- `src/app/modules/files/crud.py`
- `src/app/modules/files/service.py`

**Steps:**
1. Create File model
2. Create schemas for upload/download
3. Implement CRUD operations
4. Add file validation logic

**Acceptance Criteria:**
- [ ] Files linked to user and tenant
- [ ] File size limits enforced
- [ ] MIME type validation

---

#### Task 4.3: Files Module - Router
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/files/router.py`

**Steps:**
1. Create POST /files/upload endpoint
2. Create GET /files/{file_id} endpoint
3. Create DELETE /files/{file_id} endpoint
4. Create GET /files endpoint (list)
5. Create GET /files/{file_id}/info endpoint

**Acceptance Criteria:**
- [ ] File upload handles multipart forms
- [ ] File download streams correctly
- [ ] File deletion removes from storage

---

#### Task 4.4: Storage Externals (S3, Azure, Local)
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/files/externals/s3_storage.py`
- `src/app/modules/files/externals/azure_storage.py`
- `src/app/modules/files/externals/local_storage.py`

**Steps:**
1. Implement S3Storage class (boto3)
2. Implement AzureStorage class (azure-storage-blob)
3. Implement LocalStorage class (file system)
4. Add storage provider abstraction

**Acceptance Criteria:**
- [ ] Upload works for all providers
- [ ] Download works for all providers
- [ ] Delete works for all providers
- [ ] Provider can be switched at runtime

---

### Sprint 5: AI Features (Week 5)

#### Task 5.1: AI Chat Module - Models & Schemas
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/ai_chat/models.py`
- `src/app/modules/ai_chat/schemas.py`

**Steps:**
1. Create AIChatSession model
2. Create AIChatMessage model
3. Create schemas for chat operations

**Acceptance Criteria:**
- [ ] Sessions linked to user and tenant
- [ ] Messages include role (user/assistant/system)
- [ ] Token count tracking

---

#### Task 5.2: AI Chat Module - Service & Router
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/ai_chat/crud.py`
- `src/app/modules/ai_chat/service.py`
- `src/app/modules/ai_chat/router.py`

**Steps:**
1. Implement chat session CRUD
2. Implement message history retrieval
3. Create all 6 endpoints
4. Integrate with LangChain/Ollama

**Acceptance Criteria:**
- [ ] Sessions can be listed, created, deleted
- [ ] Messages stored with role and content
- [ ] Integration with Ollama works

---

#### Task 5.3: AI Settings Module
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/ai_settings/models.py`
- `src/app/modules/ai_settings/schemas.py`
- `src/app/modules/ai_settings/service.py`
- `src/app/modules/ai_settings/router.py`

**Steps:**
1. Create AISettings model
2. Create schemas for settings
3. Create GET/PATCH endpoints
4. Add model selection (Ollama, OpenAI)

**Acceptance Criteria:**
- [ ] Settings per user
- [ ] Model selection works
- [ ] Temperature/tokens configurable

---

### Sprint 6: RBAC Enhancements (Week 6)

#### Task 6.1: Permission Enum Extensions
**Duration:** 1 day
**Assignee:** Backend Developer
**Files:**
- `src/app/core/rbac/permissions.py`

**Steps:**
1. Add missing permission values
2. Update Permission class

**Acceptance Criteria:**
- [ ] All 8 new permissions added
- [ ] Permissions follow naming convention
- [ ] Values are unique

---

#### Task 6.2: RBAC Service Enhancements
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/rbac/service.py`
- `src/app/core/rbac/dependencies.py`

**Steps:**
1. Add check_self_access method
2. Update permission checking logic
3. Add self-access permission dependency

**Acceptance Criteria:**
- [ ] USER_SELF_READ_AND_WRITE_ONLY works correctly
- [ ] Users can only access their own resources when permission active
- [ ] All existing permissions still work

---

#### Task 6.3: User Role Assignment Endpoint
**Duration:** 1 day
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/rbac/router.py`

**Steps:**
1. Add POST /roles/{role_id}/users/{user_id}
2. Add DELETE /roles/{role_id}/users/{user_id}

**Acceptance Criteria:**
- [ ] Role assignment idempotent
- [ ] Role removal works
- [ ] Proper permissions required

---

### Sprint 7: Audit Logs Enhancements (Week 7)

#### Task 7.1: Export & Download Endpoints
**Duration:** 2 days
**Assignee:** Backend Developer
**Files:**
- `src/app/modules/audit_logs/router.py`

**Steps:**
1. Add GET /audit-logs/export endpoint
2. Add GET /audit-logs/download endpoint
3. Implement JSONL streaming response
4. Add file storage for downloaded logs

**Acceptance Criteria:**
- [ ] Export supports date filtering
- [ ] JSONL format valid
- [ ] Large exports stream correctly
- [ ] Download secured by permissions

---

### Sprint 8: Integration & Testing (Week 8)

#### Task 8.1: API Integration
**Duration:** 3 days
**Assignee:** Backend Developer
**Files:**
- `src/app/api/v1/__init__.py`
- `src/app/main.py`

**Steps:**
1. Register all new routers in API v1
2. Update main.py imports
3. Verify all routes load correctly
4. Test API documentation

**Acceptance Criteria:**
- [ ] All routes visible in /docs
- [ ] No import circular dependencies
- [ ] Router prefixes don't conflict

---

#### Task 8.2: End-to-End Testing
**Duration:** 3 days
**Assignee:** QA Engineer
**Files:**
- `tests/`

**Steps:**
1. Write integration tests for each module
2. Write authentication flow tests
3. Write permission tests
4. Test file upload/download flows

**Acceptance Criteria:**
- [ ] 80% code coverage
- [ ] All auth flows tested
- [ ] All RBAC permissions tested
- [ ] No critical bugs

---

#### Task 8.3: Performance Testing
**Duration:** 2 days
**Assignee:** QA Engineer
**Files:**
- `tests/performance/`

**Steps:**
1. Create load tests for high-traffic endpoints
2. Test database query performance
3. Test file upload performance
4. Optimize slow queries

**Acceptance Criteria:**
- [ ] API responds < 200ms for 95% of requests
- [ ] File upload < 5 seconds for 10MB files
- [ ] No memory leaks under load

---

## Sprint Planning

| Sprint | Focus | Duration | Parallel Tasks |
|--------|-------|----------|----------------|
| **0** | Foundation (Security & Architecture) | 1 week | 3 (sequential) |
| 1 | Authentication Foundation | 2 weeks | 4-5 |
| 2 | User Management | 1 week | 2-3 |
| 3 | Billing & Payments | 2 weeks | 3-4 |
| 4 | File Storage | 1.5 weeks | 3-4 |
| 5 | AI Features | 1 week | 2 |
| 6 | RBAC Enhancements | 1 week | 2-3 |
| 7 | Audit Enhancements | 0.5 week | 1 |
| 8 | Integration & Testing | 2 weeks | 3 |

**Total Estimated Time:** 12 weeks (including Sprint 0)

---

## Quality Gates

### Code Quality
- [ ] All code passes Ruff linting
- [ ] Type hints on all public functions
- [ ] No TODO comments in production code
- [ ] Docstrings on all classes and functions
- [ ] Max function complexity < 24 (mccabe)

### Testing
- [ ] Unit tests for all new modules
- [ ] Integration tests for all endpoints
- [ ] Authentication flow tests
- [ ] RBAC permission tests
- [ ] 80% code coverage minimum

### Security
- [ ] No hardcoded secrets
- [ ] SQL injection prevention (SQLAlchemy handles this)
- [ ] XSS prevention (FastAPI handles output encoding)
- [ ] CSRF protection on state-changing endpoints
- [ ] Rate limiting on auth endpoints
- [ ] Audit logging on sensitive operations

### Performance
- [ ] Database indexes on all foreign keys
- [ ] Pagination on all list endpoints
- [ ] Streaming responses for large files
- [ ] Connection pooling configured
- [ ] Query optimization verified

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Stripe API complexity | Medium | High | Use official Stripe SDK, start with simple payments |
| WebAuthn implementation | Low | High | Use py_webauthn library, follow official docs |
| File storage scalability | Medium | Medium | Use S3 for production, implement chunked uploads |
| AI model integration | Low | Medium | Start with Ollama (local), add OpenAI later |
| Migration script failures | Medium | High | Test migrations on staging first, have rollback plan |
| Circular imports | Medium | Low | Use lazy imports in __init__.py |

---

## Milestone Definitions

### Milestone 1: Authentication Complete
**Criteria:**
- [ ] Passkey login works
- [ ] Magic link login works
- [ ] SSO login works for all providers
- [ ] All auth flows integrate with existing JWT system
- [ ] Token blacklist works for all auth methods

### Milestone 2: User Management Complete
**Criteria:**
- [ ] User model has all new fields
- [ ] User preferences CRUD works
- [ ] Profile image upload works
- [ ] User endpoints return all fields

### Milestone 3: Billing Complete
**Criteria:**
- [ ] Stripe integration works
- [ ] Subscription plans can be created/managed
- [ ] Products and pricing work
- [ ] Billing history displays correctly
- [ ] MANAGE_BILLING permission works

### Milestone 4: Storage Complete
**Criteria:**
- [ ] Storage settings can be configured
- [ ] File upload works for all providers
- [ ] File download works
- [ ] File deletion works
- [ ] MANAGE_STORAGE_SETTINGS permission works

### Milestone 5: AI Features Complete
**Criteria:**
- [ ] Chat sessions can be created
- [ ] Chat messages stored and retrieved
- [ ] Ollama integration works
- [ ] User AI settings persist

### Milestone 6: All Permissions Working
**Criteria:**
- [ ] All new permissions in database
- [ ] USER_SELF_READ_AND_WRITE_ONLY works
- [ ] All management permissions work
- [ ] Audit permissions work

### Milestone 7: Release Ready
**Criteria:**
- [ ] All tests passing
- [ ] 80% code coverage
- [ ] Performance benchmarks met
- [ ] Security audit passed
- [ ] Documentation complete
- [ ] Deployment verified

---

## Rollback Procedures

### Database Rollback
```bash
# Rollback one migration
alembic downgrade -1

# Rollback to specific revision
alembic downgrade <revision>
```

### Code Rollback
```bash
# Git revert last commit
git revert HEAD

# Or checkout previous version
git checkout <previous-version>
```

### Feature Toggle
All new features should be behind feature flags for quick disabling:
```python
from app.feature_flags import feature_flags

if feature_flags.is_enabled("passkey_auth"):
    # Passkey routes
    pass
```

---

## Sign-off Checklist

- [ ] Code reviewed by at least 1 developer
- [ ] Tests passing locally
- [ ] Tests passing in CI
- [ ] Security review completed
- [ ] Performance review completed
- [ ] Documentation updated
- [ ] Migration tested on staging
- [ ] Stakeholder sign-off obtained

---

**Document Version:** 2.0 (Expert Reviewed)
**Last Updated:** 2026-01-02
**Expert Review Applied:** Architecture, Security, Testing, Performance
**Status:** READY FOR IMPLEMENTATION
