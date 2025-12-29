# Phase 3 Core Services - Data Models

**Date:** 2024  
**Feature:** Audit Logging, Notifications, Webhooks, Feature Flags  
**Branch:** `003-core-services`

---

## 📋 Table of Contents
1. [Overview](#overview)
2. [Entity Relationship Diagram](#entity-relationship-diagram)
3. [Table Schemas](#table-schemas)
4. [SQLAlchemy Models](#sqlalchemy-models)
5. [Alembic Migrations](#alembic-migrations)
6. [Indexes & Performance](#indexes--performance)

---

## 🎯 Overview

### New Tables
This specification introduces **5 new tables** for core services:

| Table | Purpose | Multi-tenant | Estimated Rows |
|-------|---------|--------------|----------------|
| `audit_logs` | Immutable action tracking | ✅ Yes | High (millions) |
| `notifications` | Email delivery tracking | ✅ Yes | Medium (thousands) |
| `webhook_endpoints` | Tenant webhook subscriptions | ✅ Yes | Low (hundreds) |
| `webhook_deliveries` | Webhook delivery attempts | ✅ Yes | Medium (thousands) |
| `email_template_overrides` | Tenant template customizations | ✅ Yes | Low (hundreds) |

### Modified Tables
- `tenants` - Add `feature_flags` JSONB column

### Design Principles
- **Multi-tenancy:** All tables have `tenant_id` FK to `tenants.id`
- **UUID Primary Keys:** Use integer PK (existing pattern) + UUID column for external references
- **Timestamps:** All tables use `created_at`/`updated_at` with timezone support
- **JSONB:** Flexible fields use PostgreSQL JSONB for queryability
- **Immutability:** Audit logs are append-only (no UPDATE/DELETE)
- **Soft Deletes:** Where needed, use `deleted_at` pattern

---

## 🔗 Entity Relationship Diagram

```
┌─────────────┐
│   tenants   │
│  (existing) │
│─────────────│
│ id (PK)     │
│ name        │
│ slug        │
│ is_active   │
│+feature_flags│◄─── NEW JSONB COLUMN
└──────┬──────┘
       │
       │ 1:N
       │
       ├──────────────┬──────────────┬──────────────┬──────────────┐
       │              │              │              │              │
       ▼              ▼              ▼              ▼              ▼
┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│    users    │ │audit_logs│ │notificat'│ │ webhook_ │ │email_temp│
│ (existing)  │ │  (NEW)   │ │  (NEW)   │ │endpoints │ │overrides │
│─────────────│ │──────────│ │──────────│ │  (NEW)   │ │  (NEW)   │
│ id (PK)     │ │ id (PK)  │ │ id (PK)  │ │ id (PK)  │ │ id (PK)  │
│ tenant_id   │ │tenant_id │ │tenant_id │ │tenant_id │ │tenant_id │
│ email       │ │user_id(FK)│ │user_id   │ │ url      │ │template_ │
└──────┬──────┘ │ action   │ │template  │ │ secret   │ │  name    │
       │        │entity_typ│ │ status   │ │event_type│ │ content  │
       │        │entity_id │ │to_email  │ │is_active │ └──────────┘
       │        │details   │ │sent_at   │ └────┬─────┘
       │        │ip_address│ └──────────┘      │
       │        └──────────┘                   │ 1:N
       │                                        │
       └────────────────────────────────────────┼─────────────┐
                                                │             │
                                                ▼             │
                                         ┌──────────┐         │
                                         │ webhook_ │         │
                                         │deliveries│         │
                                         │  (NEW)   │         │
                                         │──────────│         │
                                         │ id (PK)  │         │
                                         │webhook_  │◄────────┘
                                         │endpoint  │
                                         │  _id(FK) │
                                         │ payload  │
                                         │signature │
                                         │ status   │
                                         │attempt_  │
                                         │  count   │
                                         └──────────┘
```

---

## 📊 Table Schemas

### 1. audit_logs

**Purpose:** Immutable tracking of all write operations across the system

**PostgreSQL DDL:**
```sql
CREATE TABLE audit_logs (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    
    -- Action details
    action VARCHAR(100) NOT NULL,  -- e.g., "user.created", "post.updated", "role.deleted"
    entity_type VARCHAR(50),       -- e.g., "user", "post", "role"
    entity_id INTEGER,             -- ID of the affected entity
    
    -- Flexible payload
    details JSONB NOT NULL DEFAULT '{}',
    
    -- Request metadata
    ip_address INET,
    user_agent TEXT,
    
    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT audit_logs_tenant_fk FOREIGN KEY (tenant_id) 
        REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT audit_logs_user_fk FOREIGN KEY (user_id) 
        REFERENCES users(id) ON DELETE SET NULL
);

-- Indexes
CREATE INDEX idx_audit_logs_tenant_created ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id) 
    WHERE entity_type IS NOT NULL;
CREATE INDEX idx_audit_logs_details_gin ON audit_logs USING GIN (details);
CREATE INDEX idx_audit_logs_uuid ON audit_logs(uuid);

-- Immutability constraint (prevent updates/deletes)
CREATE RULE audit_logs_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
CREATE RULE audit_logs_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;
```

**Column Details:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Internal primary key |
| `uuid` | UUID | NO | gen_random_uuid() | External reference UUID |
| `tenant_id` | INTEGER | NO | - | Tenant isolation |
| `user_id` | INTEGER | YES | - | User who performed action (NULL if system) |
| `action` | VARCHAR(100) | NO | - | Action type (e.g., "user.created") |
| `entity_type` | VARCHAR(50) | YES | - | Type of entity affected |
| `entity_id` | INTEGER | YES | - | ID of affected entity |
| `details` | JSONB | NO | {} | Flexible payload with action context |
| `ip_address` | INET | YES | - | Client IP address |
| `user_agent` | TEXT | YES | - | Client user agent string |
| `created_at` | TIMESTAMPTZ | NO | NOW() | When action occurred |

**JSONB `details` Schema:**
```json
{
  "changes": {
    "before": {"email": "old@example.com"},
    "after": {"email": "new@example.com"}
  },
  "metadata": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "api_version": "v1"
  }
}
```

---

### 2. notifications

**Purpose:** Track email notification delivery status and history

**PostgreSQL DDL:**
```sql
CREATE TYPE notification_status AS ENUM ('pending', 'sent', 'failed');

CREATE TABLE notifications (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    
    -- Email details
    template_name VARCHAR(100) NOT NULL,  -- e.g., "welcome", "password_reset"
    to_email VARCHAR(255) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    
    -- Delivery tracking
    status notification_status NOT NULL DEFAULT 'pending',
    sent_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    
    -- Retry tracking
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP WITH TIME ZONE,
    
    -- Template context (for debugging)
    context JSONB DEFAULT '{}',
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT notifications_tenant_fk FOREIGN KEY (tenant_id) 
        REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT notifications_user_fk FOREIGN KEY (user_id) 
        REFERENCES users(id) ON DELETE SET NULL
);

-- Indexes
CREATE INDEX idx_notifications_tenant_created ON notifications(tenant_id, created_at DESC);
CREATE INDEX idx_notifications_status ON notifications(status) WHERE status = 'pending';
CREATE INDEX idx_notifications_user ON notifications(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_notifications_next_retry ON notifications(next_retry_at) 
    WHERE next_retry_at IS NOT NULL;
CREATE INDEX idx_notifications_uuid ON notifications(uuid);

-- Trigger for updated_at
CREATE TRIGGER update_notifications_updated_at
    BEFORE UPDATE ON notifications
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

**Column Details:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Internal primary key |
| `uuid` | UUID | NO | gen_random_uuid() | External reference UUID |
| `tenant_id` | INTEGER | NO | - | Tenant isolation |
| `user_id` | INTEGER | YES | - | Recipient user (NULL if non-user) |
| `template_name` | VARCHAR(100) | NO | - | Template identifier |
| `to_email` | VARCHAR(255) | NO | - | Recipient email address |
| `subject` | VARCHAR(255) | NO | - | Email subject line |
| `status` | ENUM | NO | 'pending' | Delivery status |
| `sent_at` | TIMESTAMPTZ | YES | - | When successfully sent |
| `error_message` | TEXT | YES | - | Error if failed |
| `attempt_count` | INTEGER | NO | 0 | Number of delivery attempts |
| `next_retry_at` | TIMESTAMPTZ | YES | - | When to retry if pending |
| `context` | JSONB | YES | {} | Template variables |
| `created_at` | TIMESTAMPTZ | NO | NOW() | Record creation |
| `updated_at` | TIMESTAMPTZ | NO | NOW() | Last update |

---

### 3. webhook_endpoints

**Purpose:** Store tenant webhook subscriptions and configuration

**PostgreSQL DDL:**
```sql
CREATE TABLE webhook_endpoints (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    
    -- Endpoint configuration
    url TEXT NOT NULL,  -- Full webhook URL
    secret VARCHAR(255) NOT NULL,  -- For HMAC signature (hashed)
    description TEXT,
    
    -- Event subscriptions (array of event types)
    event_types TEXT[] NOT NULL DEFAULT '{}',  
    -- e.g., ['user.created', 'user.updated', 'post.created']
    
    -- Status
    is_active BOOLEAN NOT NULL DEFAULT true,
    
    -- Metadata
    metadata JSONB DEFAULT '{}',  -- Custom headers, timeout settings, etc.
    
    -- Statistics
    total_deliveries INTEGER NOT NULL DEFAULT 0,
    successful_deliveries INTEGER NOT NULL DEFAULT 0,
    failed_deliveries INTEGER NOT NULL DEFAULT 0,
    last_delivery_at TIMESTAMP WITH TIME ZONE,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,
    
    CONSTRAINT webhook_endpoints_tenant_fk FOREIGN KEY (tenant_id) 
        REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT webhook_endpoints_url_check CHECK (url ~ '^https?://'),
    CONSTRAINT webhook_endpoints_event_types_check CHECK (array_length(event_types, 1) > 0)
);

-- Indexes
CREATE INDEX idx_webhook_endpoints_tenant ON webhook_endpoints(tenant_id) 
    WHERE deleted_at IS NULL;
CREATE INDEX idx_webhook_endpoints_active ON webhook_endpoints(is_active) 
    WHERE is_active = true AND deleted_at IS NULL;
CREATE INDEX idx_webhook_endpoints_event_types ON webhook_endpoints USING GIN (event_types);
CREATE INDEX idx_webhook_endpoints_uuid ON webhook_endpoints(uuid);

-- Trigger for updated_at
CREATE TRIGGER update_webhook_endpoints_updated_at
    BEFORE UPDATE ON webhook_endpoints
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

**Column Details:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Internal primary key |
| `uuid` | UUID | NO | gen_random_uuid() | External reference UUID |
| `tenant_id` | INTEGER | NO | - | Tenant isolation |
| `url` | TEXT | NO | - | Webhook endpoint URL |
| `secret` | VARCHAR(255) | NO | - | Secret for HMAC (hashed) |
| `description` | TEXT | YES | - | Human-readable description |
| `event_types` | TEXT[] | NO | {} | Subscribed event types |
| `is_active` | BOOLEAN | NO | true | Enable/disable endpoint |
| `metadata` | JSONB | YES | {} | Custom configuration |
| `total_deliveries` | INTEGER | NO | 0 | Total delivery attempts |
| `successful_deliveries` | INTEGER | NO | 0 | Successful deliveries |
| `failed_deliveries` | INTEGER | NO | 0 | Failed deliveries |
| `last_delivery_at` | TIMESTAMPTZ | YES | - | Last delivery timestamp |
| `created_at` | TIMESTAMPTZ | NO | NOW() | Record creation |
| `updated_at` | TIMESTAMPTZ | NO | NOW() | Last update |
| `deleted_at` | TIMESTAMPTZ | YES | - | Soft delete timestamp |

---

### 4. webhook_deliveries

**Purpose:** Track individual webhook delivery attempts with retry logic

**PostgreSQL DDL:**
```sql
CREATE TYPE webhook_delivery_status AS ENUM ('pending', 'delivered', 'failed');

CREATE TABLE webhook_deliveries (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    webhook_endpoint_id INTEGER NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    
    -- Event data
    event_type VARCHAR(100) NOT NULL,  -- e.g., "user.created"
    payload JSONB NOT NULL,
    
    -- Security
    signature VARCHAR(255) NOT NULL,  -- HMAC-SHA256 signature
    delivery_timestamp BIGINT NOT NULL,  -- Unix timestamp for signature
    
    -- Delivery tracking
    status webhook_delivery_status NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP WITH TIME ZONE,
    
    -- Response tracking
    response_status_code INTEGER,
    response_body TEXT,
    response_headers JSONB,
    
    -- Error tracking
    error_message TEXT,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMP WITH TIME ZONE,
    failed_at TIMESTAMP WITH TIME ZONE,
    
    CONSTRAINT webhook_deliveries_endpoint_fk FOREIGN KEY (webhook_endpoint_id) 
        REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    CONSTRAINT webhook_deliveries_attempt_check CHECK (attempt_count >= 0 AND attempt_count <= 5)
);

-- Indexes
CREATE INDEX idx_webhook_deliveries_endpoint ON webhook_deliveries(webhook_endpoint_id);
CREATE INDEX idx_webhook_deliveries_status ON webhook_deliveries(status) 
    WHERE status = 'pending';
CREATE INDEX idx_webhook_deliveries_next_retry ON webhook_deliveries(next_retry_at) 
    WHERE next_retry_at IS NOT NULL;
CREATE INDEX idx_webhook_deliveries_created ON webhook_deliveries(created_at DESC);
CREATE INDEX idx_webhook_deliveries_event_type ON webhook_deliveries(event_type);
CREATE INDEX idx_webhook_deliveries_uuid ON webhook_deliveries(uuid);
```

**Column Details:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Internal primary key |
| `uuid` | UUID | NO | gen_random_uuid() | External reference UUID |
| `webhook_endpoint_id` | INTEGER | NO | - | Parent endpoint |
| `event_type` | VARCHAR(100) | NO | - | Event that triggered webhook |
| `payload` | JSONB | NO | - | Event data |
| `signature` | VARCHAR(255) | NO | - | HMAC-SHA256 signature |
| `delivery_timestamp` | BIGINT | NO | - | Unix timestamp |
| `status` | ENUM | NO | 'pending' | Delivery status |
| `attempt_count` | INTEGER | NO | 0 | Number of attempts |
| `next_retry_at` | TIMESTAMPTZ | YES | - | When to retry |
| `response_status_code` | INTEGER | YES | - | HTTP status from endpoint |
| `response_body` | TEXT | YES | - | Response body |
| `response_headers` | JSONB | YES | - | Response headers |
| `error_message` | TEXT | YES | - | Error if failed |
| `created_at` | TIMESTAMPTZ | NO | NOW() | Record creation |
| `delivered_at` | TIMESTAMPTZ | YES | - | Successful delivery time |
| `failed_at` | TIMESTAMPTZ | YES | - | Final failure time |

---

### 5. email_template_overrides

**Purpose:** Allow tenants to customize default email templates

**PostgreSQL DDL:**
```sql
CREATE TABLE email_template_overrides (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    
    -- Template identification
    template_name VARCHAR(100) NOT NULL,  -- e.g., "welcome", "password_reset"
    
    -- Overrides
    subject_override VARCHAR(255),
    content TEXT NOT NULL,  -- Full Jinja2 template content
    
    -- Metadata
    is_active BOOLEAN NOT NULL DEFAULT true,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT email_template_overrides_tenant_fk FOREIGN KEY (tenant_id) 
        REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT email_template_overrides_unique UNIQUE (tenant_id, template_name)
);

-- Indexes
CREATE INDEX idx_email_template_overrides_tenant ON email_template_overrides(tenant_id);
CREATE INDEX idx_email_template_overrides_template ON email_template_overrides(template_name);
CREATE INDEX idx_email_template_overrides_active ON email_template_overrides(is_active) 
    WHERE is_active = true;
CREATE INDEX idx_email_template_overrides_uuid ON email_template_overrides(uuid);

-- Trigger for updated_at
CREATE TRIGGER update_email_template_overrides_updated_at
    BEFORE UPDATE ON email_template_overrides
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

**Column Details:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Internal primary key |
| `uuid` | UUID | NO | gen_random_uuid() | External reference UUID |
| `tenant_id` | INTEGER | NO | - | Tenant isolation |
| `template_name` | VARCHAR(100) | NO | - | Template identifier |
| `subject_override` | VARCHAR(255) | YES | - | Custom subject line |
| `content` | TEXT | NO | - | Custom Jinja2 template |
| `is_active` | BOOLEAN | NO | true | Enable/disable override |
| `created_at` | TIMESTAMPTZ | NO | NOW() | Record creation |
| `updated_at` | TIMESTAMPTZ | NO | NOW() | Last update |

---

### 6. tenants (Modified)

**Purpose:** Add feature flags JSONB column to existing table

**PostgreSQL DDL:**
```sql
ALTER TABLE tenants 
ADD COLUMN feature_flags JSONB NOT NULL DEFAULT '{}';

-- Index for JSONB queries
CREATE INDEX idx_tenants_feature_flags ON tenants USING GIN (feature_flags);

-- Example feature flags structure:
COMMENT ON COLUMN tenants.feature_flags IS 
'Feature flags in format: {"advanced_analytics": true, "api_access": false, "webhook_limit": 10}';
```

**Feature Flags Schema:**
```json
{
  "advanced_analytics": true,
  "api_access": false,
  "custom_branding": true,
  "webhook_limit": 10,
  "email_templates": true,
  "audit_log_retention_days": 90,
  "max_users": 100
}
```

---

## 🐍 SQLAlchemy Models

### Base Configuration

```python
# src/app/core/db/models.py (additions)
from sqlalchemy import ARRAY, Enum as SQLEnum, text
from sqlalchemy.dialects.postgresql import INET, JSONB
import enum

class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

class WebhookDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
```

### 1. AuditLog Model

```python
# src/app/modules/audit/models.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    Index,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Action details
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Flexible payload
    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    # Request metadata
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="audit_logs")
    user: Mapped[Optional["User"]] = relationship("User", back_populates="audit_logs")

    # Indexes
    __table_args__ = (
        Index("idx_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index(
            "idx_audit_logs_entity",
            "entity_type",
            "entity_id",
            postgresql_where=text("entity_type IS NOT NULL"),
        ),
        Index("idx_audit_logs_details_gin", "details", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, tenant_id={self.tenant_id})>"
```

### 2. Notification Model

```python
# src/app/modules/notifications/models.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base
from src.app.core.db.models import NotificationStatus


class Notification(Base):
    __tablename__ = "notifications"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Email details
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    # Delivery tracking
    status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(NotificationStatus, name="notification_status"),
        nullable=False,
        server_default="pending",
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Template context
    context: Mapped[dict] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'{}'::jsonb"),
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="notifications")
    user: Mapped[Optional["User"]] = relationship("User", back_populates="notifications")

    # Indexes
    __table_args__ = (
        Index("idx_notifications_tenant_created", "tenant_id", "created_at"),
        Index(
            "idx_notifications_status",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "idx_notifications_next_retry",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Notification(id={self.id}, template={self.template_name}, status={self.status})>"
```

### 3. WebhookEndpoint Model

```python
# src/app/modules/webhooks/models.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Endpoint configuration
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)  # Hashed
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Event subscriptions
    event_types: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Metadata
    metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'{}'::jsonb"),
    )

    # Statistics
    total_deliveries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_deliveries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failed_deliveries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delivery_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="webhook_endpoints")
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery",
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_webhook_endpoints_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_webhook_endpoints_active",
            "is_active",
            postgresql_where=text("is_active = true AND deleted_at IS NULL"),
        ),
        Index(
            "idx_webhook_endpoints_event_types",
            "event_types",
            postgresql_using="gin",
        ),
    )

    def __repr__(self) -> str:
        return f"<WebhookEndpoint(id={self.id}, url={self.url}, active={self.is_active})>"
```

### 4. WebhookDelivery Model

```python
# src/app/modules/webhooks/models.py (continued)
class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    webhook_endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Event data
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Security
    signature: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_timestamp: Mapped[int] = mapped_column(Integer, nullable=False)

    # Delivery tracking
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        SQLEnum(WebhookDeliveryStatus, name="webhook_delivery_status"),
        nullable=False,
        server_default="pending",
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Response tracking
    response_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_headers: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    endpoint: Mapped["WebhookEndpoint"] = relationship(
        "WebhookEndpoint",
        back_populates="deliveries",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_webhook_deliveries_status",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "idx_webhook_deliveries_next_retry",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
        Index("idx_webhook_deliveries_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<WebhookDelivery(id={self.id}, event={self.event_type}, status={self.status})>"
```

### 5. EmailTemplateOverride Model

```python
# src/app/modules/notifications/models.py (continued)
class EmailTemplateOverride(Base):
    __tablename__ = "email_template_overrides"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Template identification
    template_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Overrides
    subject_override: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="email_template_overrides",
    )

    # Constraints
    __table_args__ = (
        Index(
            "idx_email_template_overrides_active",
            "is_active",
            postgresql_where=text("is_active = true"),
        ),
        UniqueConstraint("tenant_id", "template_name", name="uq_tenant_template"),
    )

    def __repr__(self) -> str:
        return f"<EmailTemplateOverride(id={self.id}, template={self.template_name})>"
```

### 6. Tenant Model (Modified)

```python
# src/app/modules/tenants/models.py (additions)
from sqlalchemy.dialects.postgresql import JSONB

class Tenant(Base):
    # ... existing fields ...
    
    # NEW: Feature flags
    feature_flags: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    
    # NEW: Relationships
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    webhook_endpoints: Mapped[list["WebhookEndpoint"]] = relationship(
        "WebhookEndpoint",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    email_template_overrides: Mapped[list["EmailTemplateOverride"]] = relationship(
        "EmailTemplateOverride",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
```

---

## 🔄 Alembic Migrations

### Migration 1: Add Core Services Tables

```python
# migrations/versions/003_add_core_services_tables.py
"""Add core services tables

Revision ID: 003_core_services
Revises: 002_rbac_foundation
Create Date: 2024-12-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '003_core_services'
down_revision = '002_rbac_foundation'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create ENUM types
    op.execute("CREATE TYPE notification_status AS ENUM ('pending', 'sent', 'failed')")
    op.execute("CREATE TYPE webhook_delivery_status AS ENUM ('pending', 'delivered', 'failed')")
    
    # Create update_updated_at_column function if not exists
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)
    
    # 1. Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid')
    )
    
    # Indexes for audit_logs
    op.create_index('idx_audit_logs_tenant_created', 'audit_logs', ['tenant_id', 'created_at'])
    op.create_index('idx_audit_logs_user', 'audit_logs', ['user_id'], postgresql_where=sa.text('user_id IS NOT NULL'))
    op.create_index('idx_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('idx_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id'], postgresql_where=sa.text('entity_type IS NOT NULL'))
    op.create_index('idx_audit_logs_details_gin', 'audit_logs', ['details'], postgresql_using='gin')
    op.create_index('idx_audit_logs_uuid', 'audit_logs', ['uuid'])
    
    # Immutability rules for audit_logs
    op.execute("""
        CREATE RULE audit_logs_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
        CREATE RULE audit_logs_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;
    """)
    
    # 2. Create notifications table
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('template_name', sa.String(length=100), nullable=False),
        sa.Column('to_email', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('pending', 'sent', 'failed', name='notification_status'), server_default='pending', nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid')
    )
    
    # Indexes and trigger for notifications
    op.create_index('idx_notifications_tenant_created', 'notifications', ['tenant_id', 'created_at'])
    op.create_index('idx_notifications_status', 'notifications', ['status'], postgresql_where=sa.text("status = 'pending'"))
    op.create_index('idx_notifications_user', 'notifications', ['user_id'], postgresql_where=sa.text('user_id IS NOT NULL'))
    op.create_index('idx_notifications_next_retry', 'notifications', ['next_retry_at'], postgresql_where=sa.text('next_retry_at IS NOT NULL'))
    op.create_index('idx_notifications_uuid', 'notifications', ['uuid'])
    
    op.execute("""
        CREATE TRIGGER update_notifications_updated_at
        BEFORE UPDATE ON notifications
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    # 3. Create webhook_endpoints table
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('secret', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('event_types', postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column('total_deliveries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('successful_deliveries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_deliveries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_delivery_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.CheckConstraint("url ~ '^https?://'", name='webhook_endpoints_url_check'),
        sa.CheckConstraint('array_length(event_types, 1) > 0', name='webhook_endpoints_event_types_check')
    )
    
    # Indexes and trigger for webhook_endpoints
    op.create_index('idx_webhook_endpoints_tenant', 'webhook_endpoints', ['tenant_id'], postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_webhook_endpoints_active', 'webhook_endpoints', ['is_active'], postgresql_where=sa.text('is_active = true AND deleted_at IS NULL'))
    op.create_index('idx_webhook_endpoints_event_types', 'webhook_endpoints', ['event_types'], postgresql_using='gin')
    op.create_index('idx_webhook_endpoints_uuid', 'webhook_endpoints', ['uuid'])
    
    op.execute("""
        CREATE TRIGGER update_webhook_endpoints_updated_at
        BEFORE UPDATE ON webhook_endpoints
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    # 4. Create webhook_deliveries table
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('webhook_endpoint_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('signature', sa.String(length=255), nullable=False),
        sa.Column('delivery_timestamp', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'delivered', 'failed', name='webhook_delivery_status'), server_default='pending', nullable=False),
        sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('response_status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('response_headers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['webhook_endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.CheckConstraint('attempt_count >= 0 AND attempt_count <= 5', name='webhook_deliveries_attempt_check')
    )
    
    # Indexes for webhook_deliveries
    op.create_index('idx_webhook_deliveries_endpoint', 'webhook_deliveries', ['webhook_endpoint_id'])
    op.create_index('idx_webhook_deliveries_status', 'webhook_deliveries', ['status'], postgresql_where=sa.text("status = 'pending'"))
    op.create_index('idx_webhook_deliveries_next_retry', 'webhook_deliveries', ['next_retry_at'], postgresql_where=sa.text('next_retry_at IS NOT NULL'))
    op.create_index('idx_webhook_deliveries_created', 'webhook_deliveries', ['created_at'])
    op.create_index('idx_webhook_deliveries_event_type', 'webhook_deliveries', ['event_type'])
    op.create_index('idx_webhook_deliveries_uuid', 'webhook_deliveries', ['uuid'])
    
    # 5. Create email_template_overrides table
    op.create_table(
        'email_template_overrides',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('uuid', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('template_name', sa.String(length=100), nullable=False),
        sa.Column('subject_override', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.UniqueConstraint('tenant_id', 'template_name', name='uq_tenant_template')
    )
    
    # Indexes and trigger for email_template_overrides
    op.create_index('idx_email_template_overrides_tenant', 'email_template_overrides', ['tenant_id'])
    op.create_index('idx_email_template_overrides_template', 'email_template_overrides', ['template_name'])
    op.create_index('idx_email_template_overrides_active', 'email_template_overrides', ['is_active'], postgresql_where=sa.text('is_active = true'))
    op.create_index('idx_email_template_overrides_uuid', 'email_template_overrides', ['uuid'])
    
    op.execute("""
        CREATE TRIGGER update_email_template_overrides_updated_at
        BEFORE UPDATE ON email_template_overrides
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)
    
    # 6. Add feature_flags to tenants table
    op.add_column('tenants', sa.Column('feature_flags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_index('idx_tenants_feature_flags', 'tenants', ['feature_flags'], postgresql_using='gin')


def downgrade() -> None:
    # Drop in reverse order
    op.drop_index('idx_tenants_feature_flags', table_name='tenants', postgresql_using='gin')
    op.drop_column('tenants', 'feature_flags')
    
    op.drop_table('email_template_overrides')
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
    op.drop_table('notifications')
    
    # Drop audit_logs rules first
    op.execute("DROP RULE IF EXISTS audit_logs_no_update ON audit_logs")
    op.execute("DROP RULE IF EXISTS audit_logs_no_delete ON audit_logs")
    op.drop_table('audit_logs')
    
    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS webhook_delivery_status")
    op.execute("DROP TYPE IF EXISTS notification_status")
```

---

## 📊 Indexes & Performance

### Query Performance Optimization

**Common Queries & Their Indexes:**

1. **Audit Log Queries:**
   ```sql
   -- Query: Get audit logs for tenant by date range
   SELECT * FROM audit_logs 
   WHERE tenant_id = ? AND created_at BETWEEN ? AND ?
   ORDER BY created_at DESC;
   -- Uses: idx_audit_logs_tenant_created
   
   -- Query: Search audit logs by action type
   SELECT * FROM audit_logs 
   WHERE action = 'user.created' AND tenant_id = ?;
   -- Uses: idx_audit_logs_action + idx_audit_logs_tenant_created
   
   -- Query: JSONB search in details
   SELECT * FROM audit_logs 
   WHERE details @> '{"user_id": "123"}';
   -- Uses: idx_audit_logs_details_gin
   ```

2. **Notification Queries:**
   ```sql
   -- Query: Get pending notifications for retry
   SELECT * FROM notifications 
   WHERE status = 'pending' AND next_retry_at <= NOW();
   -- Uses: idx_notifications_status + idx_notifications_next_retry
   
   -- Query: Get notification history for user
   SELECT * FROM notifications 
   WHERE user_id = ? AND tenant_id = ? 
   ORDER BY created_at DESC LIMIT 50;
   -- Uses: idx_notifications_user + idx_notifications_tenant_created
   ```

3. **Webhook Queries:**
   ```sql
   -- Query: Get active endpoints for event type
   SELECT * FROM webhook_endpoints 
   WHERE tenant_id = ? 
     AND is_active = true 
     AND ? = ANY(event_types)
     AND deleted_at IS NULL;
   -- Uses: idx_webhook_endpoints_event_types (GIN index)
   
   -- Query: Get delivery history for endpoint
   SELECT * FROM webhook_deliveries 
   WHERE webhook_endpoint_id = ? 
   ORDER BY created_at DESC LIMIT 100;
   -- Uses: idx_webhook_deliveries_endpoint + idx_webhook_deliveries_created
   ```

### Performance Estimates

| Table | 10K Tenants | 100K Users | Est. Rows | Index Size |
|-------|-------------|------------|-----------|------------|
| audit_logs | 1M actions/day | 10M actions/day | 100M+ | ~5GB |
| notifications | 100 emails/day/tenant | - | 1M | ~100MB |
| webhook_endpoints | 5 per tenant | - | 50K | ~10MB |
| webhook_deliveries | 1000 events/day | - | 10M | ~500MB |
| email_template_overrides | 2 per tenant | - | 20K | ~5MB |

### Partitioning Strategy (Future)

**For audit_logs (when > 100M rows):**
```sql
-- Partition by created_at (monthly)
CREATE TABLE audit_logs_2024_01 PARTITION OF audit_logs
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE audit_logs_2024_02 PARTITION OF audit_logs
FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- etc.
```

---

## ✅ Summary

### Tables Created
- ✅ `audit_logs` - Immutable action tracking with JSONB details
- ✅ `notifications` - Email delivery tracking with retry logic
- ✅ `webhook_endpoints` - Webhook subscriptions with event filtering
- ✅ `webhook_deliveries` - Delivery attempts with response tracking
- ✅ `email_template_overrides` - Tenant template customization

### Tables Modified
- ✅ `tenants` - Added `feature_flags` JSONB column

### Relationships
- All tables have `tenant_id` FK for multi-tenancy
- Audit logs and notifications reference `user_id` (nullable)
- Webhook deliveries reference webhook endpoints (cascade delete)
- All use UUID for external references

### Next Steps
1. ✅ Data models complete → Move to API contracts
2. Create OpenAPI specs in `contracts/` directory
3. Create `quickstart.md` for developers
4. Update `plan.md` with Phase 1 summary
</parameter>
</invoke>