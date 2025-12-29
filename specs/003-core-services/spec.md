# Feature Specification: Core Services Integration

**Feature Branch**: `003-core-services`  
**Created**: 2025-12-29  
**Status**: Draft  
**Input**: Phase 3 Core Services: Audit Logging, Notifications, Webhooks, and Feature Flags integration

## Clarifications

### Session 2025-12-29

- Q: If the event bus queue fills up during extreme load, should we drop oldest events, reject new events with backpressure, or implement priority queuing? → A: Option C - Implement priority queuing (critical events like audit logs and security alerts processed first, less critical events like webhooks can be delayed or dropped under extreme load)

### Session 2025-01-03

- Q: How aggressively should the system retry failed webhook deliveries before giving up? → A: Option B - Standard retry schedule: 5 retries over 1 hour with exponential backoff (1 minute, 5 minutes, 15 minutes, 30 minutes, 1 hour) following industry standard practices
- Q: How should email templates be stored and managed for multi-tenant deployments? → A: Option B - File-based with database overrides (default templates stored in version-controlled files, tenants can customize templates via UI with overrides stored in database)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Activity Audit Trail (Priority: P1)

As a **system administrator** or **compliance officer**, I need every significant action in the system (user creation, data modification, deletion) to be automatically logged with complete context (who, what, when, where) so I can track system usage, investigate security incidents, and meet compliance requirements.

**Why this priority**: Audit logging is foundational for security, compliance, and debugging. It must be in place before other services can be tracked. Without it, the system lacks accountability.

**Independent Test**: Can be fully tested by performing any write operation (create user, update post, delete resource) and verifying an audit log entry is created with correct actor, action, resource, and timestamp. Delivers immediate security and compliance value.

**Acceptance Scenarios**:

1. **Given** a user creates a new account, **When** the registration completes, **Then** an audit log entry is created recording the registration event with user ID, timestamp, IP address, and action details
2. **Given** an admin deletes a user, **When** the deletion executes, **Then** an audit log entry captures the admin's identity, deleted user ID, timestamp, and reason (if provided)
3. **Given** a user updates their profile, **When** the update succeeds, **Then** the audit log records which fields changed, old values, new values, and who made the change
4. **Given** an unauthorized access attempt occurs, **When** the system rejects the request, **Then** the audit log records the failed attempt with IP, attempted resource, and timestamp
5. **Given** the audit logging system is temporarily unavailable, **When** a write operation occurs, **Then** the operation completes successfully (logging failure doesn't block user actions)

---

### User Story 2 - Transactional Email Notifications (Priority: P1)

As a **user**, I need to receive timely email notifications for important account events (welcome, password reset, security alerts) so I stay informed about my account activity and can take action when needed.

**Why this priority**: Email notifications are critical for user onboarding and security. Users expect immediate confirmation emails for account creation and password resets. This is a baseline expectation for any modern application.

**Independent Test**: Can be tested by triggering account creation or password reset and verifying email delivery with correct content, personalization, and branding. Delivers immediate user value and security.

**Acceptance Scenarios**:

1. **Given** a new user completes registration, **When** their account is created, **Then** they receive a welcome email with their username and next steps
2. **Given** a user requests a password reset, **When** the request is processed, **Then** they receive an email with a secure reset link valid for 1 hour
3. **Given** a user's email is already in the notification queue, **When** they request another reset, **Then** only one email is sent (deduplication prevents spam)
4. **Given** an email delivery fails, **When** the system detects the failure, **Then** the notification is marked as failed and retried up to 3 times with exponential backoff
5. **Given** a tenant administrator, **When** viewing the admin panel, **Then** they can see the delivery status of all notifications sent to their users

---

### User Story 3 - Webhook Event Subscriptions (Priority: P2)

As a **tenant administrator**, I need to configure webhook endpoints to receive real-time notifications when specific events occur in my tenant (user created, subscription changed, payment processed) so I can integrate the platform with my existing systems and automate workflows.

**Why this priority**: Webhooks enable integration and automation for power users. While not required for basic functionality, they unlock significant value for enterprise customers and SaaS integrations.

**Independent Test**: Can be tested by configuring a webhook endpoint for "user.created" events, creating a test user, and verifying the webhook receives a POST request with event data. Delivers integration value independently.

**Acceptance Scenarios**:

1. **Given** a tenant admin configures a webhook URL for "user.created" events, **When** a new user is created in that tenant, **Then** the system sends a POST request to the webhook URL with event details in JSON format
2. **Given** a webhook delivery fails with a 500 error, **When** the system detects the failure, **Then** it retries delivery up to 5 times with exponential backoff over 1 hour (1 minute, 5 minutes, 15 minutes, 30 minutes, 1 hour)
3. **Given** a webhook endpoint is consistently failing, **When** it fails 10 consecutive deliveries, **Then** the system automatically disables the endpoint and notifies the tenant admin
4. **Given** a tenant has multiple webhook endpoints configured, **When** an event occurs, **Then** all matching endpoints receive the event payload independently (parallel delivery)
5. **Given** a webhook delivery succeeds, **When** the endpoint responds with 2xx status, **Then** the delivery is marked as successful and no retry is attempted

---

### User Story 4 - Tenant-Level Feature Flags (Priority: P2)

As a **tenant administrator**, I need to toggle specific features on or off for my organization (beta features, premium capabilities, experimental functionality) so I can control my users' experience and opt into new capabilities at my own pace.

**Why this priority**: Feature flags enable gradual rollouts, A/B testing, and customer-specific feature access. Critical for SaaS platforms with tiered pricing or beta programs.

**Independent Test**: Can be tested by toggling a feature flag for a tenant and verifying users in that tenant see/don't see the feature accordingly. Delivers immediate control value.

**Acceptance Scenarios**:

1. **Given** a tenant with feature "advanced_analytics" disabled, **When** a user in that tenant accesses the analytics page, **Then** they see a message indicating the feature is not available for their plan
2. **Given** a super admin enables "beta_notifications" for a specific tenant, **When** users in that tenant log in, **Then** they see the new notification center interface
3. **Given** feature flags are stored per tenant, **When** a user switches between tenants, **Then** they see different features based on each tenant's configuration
4. **Given** a feature flag change is made, **When** the change is saved, **Then** the new configuration takes effect immediately without requiring application restart
5. **Given** a feature flag controls API access, **When** a disabled feature's API endpoint is called, **Then** the system returns 403 Forbidden with a clear message about feature availability

---

### User Story 5 - Event-Driven Module Decoupling (Priority: P3)

As a **developer**, I need a central event bus that allows modules to publish and subscribe to events (user.created, post.updated, payment.completed) so modules remain loosely coupled and new functionality can be added without modifying existing code.

**Why this priority**: The event bus is infrastructure that enables the other features (audit logging subscribes to all events, notifications subscribe to specific events, webhooks subscribe to configured events). It's P3 because it's internal architecture, not direct user value.

**Independent Test**: Can be tested by publishing a custom event and verifying all registered subscribers receive the event with correct data. Delivers architectural value by enabling clean module boundaries.

**Acceptance Scenarios**:

1. **Given** a module publishes a "user.created" event, **When** the event is dispatched, **Then** all subscribers (audit log, email notifications, webhooks) receive the event with user data
2. **Given** an event subscriber throws an exception, **When** processing the event, **Then** other subscribers still receive the event (isolation of failures)
3. **Given** an async event subscriber is slow, **When** processing the event, **Then** the original operation completes without waiting (fire-and-forget for async subscribers)
4. **Given** a module needs to listen for multiple event types, **When** registering subscriptions, **Then** it can subscribe to multiple events with different handlers
5. **Given** an event with sensitive data, **When** logging the event for debugging, **Then** sensitive fields (passwords, tokens) are masked

---

### Edge Cases

- **What happens when the email service is unavailable?** Notifications are queued in the database and retried automatically when the service recovers (up to 3 retry attempts with exponential backoff)
- **What happens when a webhook endpoint becomes unreachable?** The system retries delivery up to 5 times with exponential backoff, then marks the endpoint as failed and alerts the tenant admin
- **What happens if audit logging fails due to database issues?** The write operation still completes successfully (audit logging is fire-and-forget and doesn't block business operations)
- **What happens if two events are published simultaneously?** Each event is processed independently by its subscribers in the order received (event bus handles concurrency)
- **What happens if a tenant disables a feature that users are currently using?** Active sessions continue working until refresh, then users see the feature disabled message (graceful degradation)
- **What happens if the event bus queue fills up?** The system implements priority queuing where CRITICAL events (audit logs, security alerts) are guaranteed processing, NORMAL events (email notifications) are processed when queue space allows, and LOW priority events (webhooks) may be delayed or dropped to prevent memory exhaustion. Queue size limits are configurable per priority level.
- **What happens if a webhook payload exceeds size limits?** The payload is truncated with a warning field indicating data was omitted, or delivery fails with a clear error message
- **What happens if feature flag configuration becomes corrupted?** The system falls back to safe defaults (all optional features disabled, core features enabled)

## Requirements *(mandatory)*

### Functional Requirements

#### Event Bus

- **FR-001**: System MUST provide a central event dispatcher that accepts event publications and delivers them to registered subscribers
- **FR-002**: Event dispatcher MUST support both synchronous (blocking) and asynchronous (fire-and-forget) event delivery modes
- **FR-003**: Event subscribers MUST be able to register for specific event types using a pattern-based naming convention (e.g., "user.*", "post.created")
- **FR-004**: Events MUST include metadata: event type, timestamp, actor (user/system), tenant ID, and payload data
- **FR-005**: System MUST isolate subscriber failures so one failing subscriber doesn't prevent others from receiving events
- **FR-006**: Events MUST be serializable to JSON for storage and webhook delivery
- **FR-006a**: Events MUST be classified by priority level (CRITICAL for audit logs and security events, NORMAL for notifications, LOW for webhooks) to enable priority-based queue processing during high load

#### Audit Logging

- **FR-007**: System MUST automatically create audit log entries for all write operations (POST, PUT/PATCH, DELETE) across all API endpoints
- **FR-008**: Audit log entries MUST capture: actor (user ID or "system"), action (create/update/delete), resource type, resource ID, timestamp, IP address, user agent, and tenant ID
- **FR-009**: Audit log entries MUST include change tracking for updates: field names, old values, and new values
- **FR-010**: Audit logging MUST be asynchronous (fire-and-forget) to avoid blocking the main request/response cycle
- **FR-011**: System MUST provide read-only API endpoints for querying audit logs with filters (date range, user, action type, resource type)
- **FR-012**: Audit logs MUST be retained for a minimum of 90 days and support configurable retention policies per tenant
- **FR-013**: Audit log entries MUST be immutable (cannot be modified or deleted after creation)

#### Notification System

- **FR-014**: System MUST send transactional emails for key events: user registration (welcome), password reset, email verification, security alerts
- **FR-015**: Email templates MUST support variable substitution (user name, verification links, timestamps) using a template engine
- **FR-016**: System MUST track notification delivery status: pending, sent, failed, delivered (if email provider supports delivery confirmation)
- **FR-017**: Failed notification deliveries MUST be automatically retried up to 3 times with exponential backoff (1 minute, 5 minutes, 15 minutes)
- **FR-018**: System MUST prevent duplicate notifications for the same event within a configurable time window (e.g., 5 minutes)
- **FR-019**: System MUST support email provider abstraction to allow switching between SMTP, SendGrid, AWS SES, etc., via configuration
- **FR-020**: Default email templates MUST be stored as Jinja2 files in version control (e.g., `src/app/templates/email/`), and tenants MUST be able to override default templates with custom versions stored in the database
- **FR-020a**: Template variables MUST include: `tenant_name`, `user_name`, `action_details`, `timestamp`, `support_email` for personalization
- **FR-021**: System MUST provide a notification history view for administrators showing all notifications sent to users in their tenant

#### Webhooks

- **FR-022**: Tenant administrators MUST be able to configure webhook endpoints (URL, secret key, enabled/disabled status, subscribed events)
- **FR-023**: System MUST deliver webhook payloads via HTTP POST with JSON body containing event type, timestamp, tenant ID, and event-specific data
- **FR-024**: Webhook deliveries MUST include HMAC signature in headers for recipient verification using the configured secret key
- **FR-025**: System MUST retry failed webhook deliveries up to 5 times with exponential backoff over 1 hour (delays: 1 minute, 5 minutes, 15 minutes, 30 minutes, 1 hour)
- **FR-026**: System MUST automatically disable webhook endpoints that fail 10 consecutive deliveries and notify the tenant administrator
- **FR-027**: Webhook delivery history MUST be retained for 30 days showing: endpoint, event type, delivery status, response code, retry count, timestamp
- **FR-028**: System MUST enforce webhook payload size limits (e.g., 1MB max) and timeout limits (e.g., 30 seconds max response time)
- **FR-029**: Tenant administrators MUST be able to test webhook endpoints with a sample payload before enabling

#### Feature Flags

- **FR-030**: System MUST store feature flag configurations per tenant using flexible key-value storage (JSONB column in tenants table or separate feature_flags table)
- **FR-031**: Feature flags MUST support boolean toggles (enabled/disabled) and string values (for variant configurations)
- **FR-032**: System MUST provide API endpoints for administrators to query and update feature flag configurations for their tenant
- **FR-033**: Feature flag changes MUST take effect immediately without requiring application restart or cache invalidation
- **FR-034**: System MUST provide a default feature flag configuration that applies to all new tenants
- **FR-035**: API endpoints controlled by feature flags MUST return 403 Forbidden with a descriptive message when accessed with the feature disabled
- **FR-036**: Frontend applications MUST be able to query which features are enabled for the current user's tenant via a dedicated API endpoint

### Non-Functional Requirements

- **NFR-001**: Audit log writes MUST not increase API response time by more than 10ms (asynchronous processing)
- **NFR-002**: Email notifications MUST be queued and processed within 30 seconds of the triggering event
- **NFR-003**: Webhook deliveries MUST be attempted within 5 seconds of the triggering event
- **NFR-004**: The event bus MUST handle at least 1000 events per second without dropping events
- **NFR-005**: Feature flag lookups MUST complete in under 5ms (cached in application memory)

### Key Entities

- **AuditLog**: Represents a single audit trail entry capturing who did what, when, and where. Attributes: ID, tenant_id, actor_id (user ID or "system"), action (create/update/delete), resource_type, resource_id, changes (JSONB), ip_address, user_agent, timestamp. Immutable after creation.

- **Notification**: Represents an email notification to be sent or already sent. Attributes: ID, tenant_id, recipient_email, template_name, template_variables (JSONB), status (pending/sent/failed), delivery_attempts, last_attempt_at, sent_at, error_message, created_at. Mutable for retry tracking.

- **WebhookEndpoint**: Represents a tenant's configured webhook subscription. Attributes: ID, tenant_id, url, secret_key (encrypted), events (array of event patterns), is_enabled, failure_count, last_success_at, last_failure_at, created_by, created_at, updated_at. Mutable for status tracking.

- **WebhookDelivery**: Represents a single webhook delivery attempt. Attributes: ID, webhook_endpoint_id, event_type, payload (JSONB), status (pending/success/failed), response_code, response_body (truncated), retry_count, delivered_at, created_at. Immutable record of delivery.

- **EmailTemplateOverride**: Represents tenant-specific email template customizations. Attributes: ID, tenant_id (nullable for system-wide defaults), template_type (welcome_email, password_reset, etc.), subject, html_body (Jinja2 template), text_body (Jinja2 template), is_active, created_at, updated_at. Unique constraint on (tenant_id, template_type).

- **FeatureFlag**: (Embedded in Tenant model as JSONB column or separate table) Represents feature toggle configuration. Attributes: feature_key, value (boolean or string), enabled_at, updated_by. Flexible schema for new flags.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of write operations (POST/PUT/DELETE) across all API endpoints generate audit log entries within 30 seconds
- **SC-002**: Audit log queries (filtered by date, user, or resource) return results in under 500ms for datasets up to 100,000 records
- **SC-003**: Email notifications are delivered to users within 2 minutes of the triggering event 99% of the time (excluding email provider delays)
- **SC-004**: Webhook deliveries succeed on first attempt 95% of the time, and reach 99% success rate after automatic retries
- **SC-005**: Feature flag configuration changes take effect within 5 seconds across all application instances
- **SC-006**: The event bus processes events without any event loss under normal load (up to 500 concurrent requests)
- **SC-007**: Failed webhook endpoints are automatically disabled and tenant administrators are notified within 1 minute of the 10th consecutive failure
- **SC-008**: Email notification templates can be updated by non-technical administrators without code changes or deployment
- **SC-009**: System maintains operational performance with audit logging enabled (API response times increase by less than 10ms on average)
- **SC-010**: Webhook signature verification catches 100% of tampered payloads during security testing

## Assumptions

1. **Email Provider**: The project has access to an SMTP server or third-party email service (SendGrid, AWS SES, Mailgun) with API credentials configured via environment variables
2. **Webhook Recipient Behavior**: Webhook recipients respond within 30 seconds and return standard HTTP status codes (2xx for success, 4xx for client errors, 5xx for server errors)
3. **Event Volume**: The system will handle up to 1000 events per second initially, with horizontal scaling possible if volume increases
4. **Audit Log Storage**: PostgreSQL can handle the audit log volume (estimated 1-10 million records per month) with appropriate indexing and partitioning
5. **Template Language**: Jinja2 is acceptable for email template rendering (commonly used in Python ecosystems)
6. **Async Processing**: ARQ (already in stack) or similar background job processor is available for async event handling
7. **Security**: HMAC-SHA256 is sufficient for webhook signature verification
8. **Default Features**: New tenants start with all core features enabled and optional features disabled unless specified otherwise during tenant creation
9. **Event Retention**: Events processed through the event bus are ephemeral (not persisted beyond audit logging) unless explicitly stored by subscribers
10. **Multi-Tenancy**: All events, audit logs, notifications, and webhooks are tenant-scoped to prevent cross-tenant data leakage

## Dependencies

- **PostgreSQL Database**: Required for storing audit logs, notifications, webhook configurations, and delivery history
- **Email Service Provider**: SMTP server or third-party API (SendGrid, AWS SES, Mailgun) for sending transactional emails
- **Background Job Processor**: ARQ (already in project) or similar for async event processing and notification delivery
- **Template Engine**: Jinja2 for email template rendering with variable substitution
- **HMAC Library**: Python's `hmac` module for webhook signature generation and verification
- **HTTP Client**: `httpx` or `aiohttp` for async webhook delivery with timeout and retry support

## Integration Points

1. **Existing API Endpoints**: All write operations (POST/PUT/DELETE) across all modules must publish events to the event bus
2. **Authentication Module**: User registration, password reset, and email verification flows trigger notification events
3. **Tenant Context**: All events, audit logs, and webhooks must include and respect the current tenant context from `TenantMiddleware`
4. **Admin Panel (CRUDAdmin)**: Webhook configuration UI and notification history views integrate with admin interface
5. **Background Jobs (ARQ)**: Notification delivery and webhook delivery leverage existing ARQ task queue
6. **Database Migrations**: New tables for audit logs, notifications, webhook endpoints, and webhook deliveries require Alembic migrations

## Out of Scope

- **Advanced Analytics Dashboard**: Detailed charts and graphs for audit logs, notification metrics, or webhook statistics (can be added in future phases)
- **Webhook Payload Transformation**: Custom payload formatting per endpoint (all webhooks receive standardized JSON format)
- **Multi-Channel Notifications**: SMS, push notifications, in-app notifications (this phase focuses only on email)
- **Event Replay/Reprocessing**: Ability to replay historical events through the event bus (events are ephemeral)
- **Advanced Template Editor UI**: Rich text editor with live preview for template customization (basic CRUD interface only in this phase)
- **Webhook Rate Limiting**: Per-endpoint rate limits to prevent abuse (can be added if needed)
- **Feature Flag UI**: Graphical interface for managing feature flags (admin API only in this phase)
- **A/B Testing Framework**: Statistical analysis and variant assignment for feature flags (future enhancement)

## Open Questions

None. All critical decisions have been clarified.
