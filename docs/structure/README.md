# Project Structure Documentation

A comprehensive guide to the FastAPI SaaS  architecture, directory organization, and component relationships.

## Overview

This document provides an in-depth analysis of the FastAPI SaaS  project structure, enabling developers to understand the modular architecture, navigate the codebase efficiently, and implement new features following established patterns. The project implements a production-ready SaaS foundation with async SQLAlchemy 2.0, Pydantic v2, JWT authentication, RBAC, rate limiting, Redis caching, background jobs via ARQ, and PostgreSQL as the primary database.

The architecture follows a domain-driven design approach with clear separation of concerns across layers: presentation (API routers), business logic (service classes), data access (CRUD operations), and infrastructure (middleware, security, database). This organization facilitates scalability, maintainability, and testability while providing sensible defaults for common SaaS requirements such as multi-tenancy, authentication, authorization, and event handling.

## Directory Structure

The project uses a structured directory layout that separates concerns and promotes modularity. Each major directory serves a specific purpose and contains related components. The `src/app` directory houses the main application code, with subdirectories organized by architectural layer and domain module. This structure supports both small and large-scale applications while maintaining clarity about component responsibilities and relationships.

```
D:\project\FastAPI_saas\
├── .github/                    # GitHub Actions CI/CD workflows
├── .factory/                   # Factory commands and automation
├── .opencode/                  # OpenCode system configuration
├── scripts/                    # Deployment and utility scripts
├── src/
│   ├── app/                    # Main application package
│   │   ├── admin/              # CRUDAdmin panel configuration
│   │   ├── api/                # API layer (v1 routers)
│   │   ├── core/               # Core infrastructure (config, db, security)
│   │   ├── crud/               # CRUD operations utilities
│   │   ├── logs/               # Logging configuration
│   │   ├── middleware/         # HTTP middleware components
│   │   ├── models/             # Base model classes
│   │   ├── modules/            # Domain modules (auth, users, tenants, etc.)
│   │   ├── schemas/            # Shared Pydantic schemas
│   │   ├── templates/          # Email templates
│   │   ├── main.py             # FastAPI application entry point
│   │   ├── audit.py            # Audit logging integration
│   │   ├── email_provider.py   # Email service provider
│   │   ├── feature_flags.py    # Feature flag service
│   │   ├── signatures.py       # Function signatures
│   │   └── templates.py        # Template rendering
│   ├── migrations/             # Alembic database migrations
│   └── scripts/                # Utility scripts
├── tests/                      # Test suite (unit, integration, contract, performance)
├── docs/                       # Project documentation
├── scripts/                    # Docker deployment configurations
├── migrations/                 # Database migration files
├── pyproject.toml              # Project configuration and dependencies
├── alembic.ini                 # Alembic migration configuration
├── setup.py                    # Deployment setup script
└── README.md                   # Project overview
```

## Core Infrastructure (`src/app/core/`)

The core directory contains foundational infrastructure that all other components depend upon. These modules provide cross-cutting concerns such as configuration management, database connectivity, security utilities, tenancy handling, and background job processing. Understanding these components is essential for working with any part of the application, as they establish the patterns and conventions used throughout the codebase.

### Configuration Management (`core/config.py`)

The configuration system uses Pydantic Settings with environment variable support, providing a type-safe approach to managing application configuration. This design enables easy configuration across different environments (local, staging, production) while maintaining strict type checking and validation. The configuration is organized into nested classes that group related settings, making it straightforward to understand and modify configuration options.

The `AppSettings` class serves as the root configuration container, composing all other settings classes. `CryptSettings` manages cryptographic keys and JWT configuration, ensuring sensitive values are handled securely through `SecretStr` types. `DatabaseSettings` supports PostgreSQL, MySQL, and SQLite databases with connection pooling configuration. Redis settings are split into cache, queue, and rate limiter configurations to support different Redis use cases within the application.

Additional settings classes handle CRUDAdmin panel configuration, email provider settings, webhook delivery parameters, and CORS origins. The configuration system supports environment-specific behavior through the `ENVIRONMENT` variable, enabling different configurations for local development, staging, and production environments without code changes.

### Database Layer (`core/db/`)

The database layer implements async SQLAlchemy 2.0 patterns with careful attention to connection management and session handling. The `database.py` module provides async engine and session factory configuration, implementing best practices for production database connectivity including connection pooling and proper cleanup. This async-first approach ensures the application can handle concurrent requests efficiently without blocking on database operations.

The `models.py` module defines essential mixin classes that provide common functionality across all database models. The `UUIDMixin` implements UUID v7 primary keys using the `uuid6` library, providing time-ordered UUIDs that are ideal for distributed systems. The `TimestampMixin` automatically handles `created_at` and `updated_at` timestamps using timezone-aware datetime values. The `SoftDeleteMixin` implements soft deletion through `deleted_at` and `is_deleted` fields, enabling data recovery and audit trails without permanent data loss.

The `tenant_crud.py` and `tenant_mixin.py` modules provide multi-tenancy support through database-level tenant isolation. The `TenantMixin` adds `tenant_id` foreign key and creates indexes for efficient tenant-scoped queries. Specialized CRUD operations in `tenant_crud.py` ensure all tenant operations are properly scoped and validated. The `token_blacklist.py` module handles JWT token invalidation for logout functionality, storing blacklisted tokens in the database for validation during authentication checks.

### Security Module (`core/security/`)

The security module provides cryptographic utilities and security-related functions used throughout the application. The `encryption.py` module implements password hashing using bcrypt with configurable work factors, ensuring secure password storage that resists brute-force attacks. Token encryption and decryption utilities protect sensitive data within JWT payloads and refresh tokens.

The `session_manager.py` module handles session lifecycle management, including session creation, validation, and cleanup. It integrates with the authentication system to maintain secure session state and support concurrent session handling. The `url_validator.py` module provides URL validation for webhook endpoints and redirect URLs, preventing SSRF attacks and ensuring URL safety.

### Tenancy Module (`core/tenancy/`)

The tenancy module implements multi-tenant architecture patterns, providing tenant context management and isolation mechanisms. Tenant context is propagated through the request lifecycle using context variables, enabling automatic tenant scoping for database queries and authorization checks. This design ensures data isolation between tenants while minimizing boilerplate code in individual modules.

The tenancy module also handles tenant resolution from various sources including subdomains, headers, and JWT claims. This flexibility supports different multi-tenancy models such as shared database with tenant ID filtering, schema-per-tenant, or database-per-tenant architectures. The current implementation focuses on shared database with tenant ID scoping, which provides a good balance of simplicity and isolation for most SaaS applications.

### Worker Module (`core/worker/`)

The worker module implements background job processing using ARQ (Async Redis Queue), enabling asynchronous task execution for long-running operations such as webhook delivery, email sending, and report generation. The worker configuration integrates with Redis queue settings and provides patterns for task definition, scheduling, and error handling.

Background jobs are defined as async functions with proper serialization of arguments and return values. The worker supports retry logic with exponential backoff, job scheduling with cron-like expressions, and result storage for task completion status. This architecture enables the application to handle burst traffic and long-running operations without blocking request handlers.

### Events Module (`core/events.py`)

The events module implements an event bus pattern for inter-module communication and loose coupling. Events are published to Redis pub/sub channels and consumed by interested modules, enabling features like audit logging, webhook triggering, and notification delivery without direct dependencies between modules. This publish-subscribe pattern supports extensibility and maintains separation of concerns.

## API Layer (`src/app/api/`)

The API layer implements the presentation layer of the application, exposing functionality through REST endpoints with OpenAPI documentation. The API is organized into versioned routers under `/api/v1/`, enabling API evolution without breaking existing clients. Each module contributes its router to the main API router, with consistent patterns for request handling, response formatting, and error handling.

### API Router Structure

The API module serves as the entry point for all HTTP requests, routing requests to appropriate module routers based on the URL path. The main `api/__init__.py` creates an APIRouter with the `/api` prefix and includes the v1 router. The `api/v1/__init__.py` composes all module routers into a single v1 router, applying consistent tagging for OpenAPI documentation.

Each module within `api/v1/` follows a consistent structure with a router module defining endpoints, dependency injection for authentication and authorization, and response schemas. The router pattern uses FastAPI's declarative syntax with type hints for automatic request validation and response serialization. All endpoints return Pydantic models, ensuring consistent response formats and enabling OpenAPI schema generation.

### API Endpoints by Module

The API exposes endpoints across multiple modules, each handling a specific domain of functionality. The authentication module (`auth/`) provides login, logout, token refresh, and password management endpoints. The users module handles user CRUD operations and user-specific endpoints like the current user profile. The tenants module manages multi-tenant organization data, including tenant creation, membership management, and tenant settings.

Additional modules expose endpoints for tier management (`tiers/`), rate limit configuration (`rate_limits/`), role-based access control (`roles/`), audit log viewing (`audit-logs/`), webhook management (`webhooks/`), feature flag configuration (`feature-flags/`), notification management (`notifications/`), background task monitoring (`tasks/`), health checks (`health/`), content management (`posts/`), and API key management (`api-keys/`). Each module follows consistent patterns for error handling, pagination, and response formatting.

## Domain Modules (`src/app/modules/`)

The modules directory contains domain-specific implementations organized by business capability. Each module is self-contained with its own router, service, CRUD, models, and schemas, following a vertical slice architecture that groups related functionality. This organization makes it easy to understand, modify, and test individual features without affecting unrelated code.

### Module Structure Convention

Each module follows a consistent internal structure that promotes maintainability and consistency across modules. The `router.py` file defines API endpoints using FastAPI's Router class, handling HTTP request/response mapping and input validation. The `service.py` file implements business logic and coordinates between the API layer and data access layer, encapsulating complex operations and ensuring transactional consistency.

The `crud.py` file (or use of FastCRUD) handles database operations, providing a consistent interface for create, read, update, and delete operations. The `models.py` file (when needed) defines SQLAlchemy models specific to the module, extending base mixins with module-specific fields. The `schemas.py` file defines Pydantic models for request validation and response serialization, ensuring type safety and input validation at the API boundary.

### Core Modules

The authentication module (`auth/`) implements JWT-based authentication with access and refresh tokens, password hashing, account lockout after failed attempts, and password reset functionality. The module integrates with the security module for cryptographic operations and with the audit module for login tracking. Token management includes refresh token rotation and blacklisting for logout support.

The users module (`users/`) handles user account management including creation, retrieval, update, and soft deletion. The module implements user-scoped operations and integrates with the tenant module for multi-tenant user membership. User profiles support avatar management, preference storage, and activity tracking through the audit system.

The tenants module (`tenants/`) implements multi-tenant organization management, supporting tenant creation, membership management, and tenant-scoped settings. Tenant isolation is enforced at the database level through the tenant mixin and at the API level through the tenant middleware. The module supports tenant hierarchies, invitation-based membership, and role assignment within tenant contexts.

### Authorization and Security Modules

The RBAC module (`rbac/`) implements role-based access control with permissions, roles, and assignments. The module supports role hierarchies where roles inherit permissions from parent roles, and permission checking at both route and operation levels. Integration with the authentication module enables permission-aware authorization for all protected endpoints.

The rate limits module (`rate_limits/`) provides configurable rate limiting with tenant and user granularity. Rate limits are enforced through middleware with Redis-backed counters for distributed rate limiting. The module supports different rate limit strategies including fixed window, sliding window, and token bucket algorithms.

The API keys module (`api_keys/`) enables API key authentication for service-to-service communication and developer access. API keys support automatic rotation, scoped permissions, and usage tracking. Keys can be restricted to specific IP ranges and have configurable expiration dates.

### Observability and Event Modules

The audit logs module (`audit_logs/`) implements immutable audit trail recording for all significant system events. The module captures user, tenant, action, resource, and timestamp information for compliance and debugging purposes. Audit events are published through the event bus for asynchronous processing and webhook delivery.

The webhooks module (`webhooks/`) provides webhook delivery infrastructure for event notifications to external systems. The module implements webhook registration, payload construction, delivery with retry logic, and response status tracking. Webhooks support HMAC signature verification for security and configurable retry schedules with exponential backoff.

The notifications module (`notifications/`) handles email notification delivery with template support and delivery tracking. The module integrates with the templates system for consistent email formatting and with the event bus for triggered notifications. Delivery status tracking enables retry of failed notifications and analytics on notification success rates.

The feature flags module (`feature_flags/`) implements feature toggles for progressive feature rollout and A/B testing. Flags support boolean and multivariate configurations with targeting rules based on user, tenant, and percentage rollouts. The module provides fast, Redis-cached flag evaluation for minimal latency impact.

### Support Modules

The health module (`health/`) provides health check endpoints for load balancers and orchestrators. The module exposes both liveness probes (is the application running?) and readiness probes (is the application ready to serve traffic?). Health checks verify database connectivity, Redis connectivity, and dependent service availability.

The tasks module (`tasks/`) monitors background job status and provides job management capabilities. The module integrates with the worker module for job submission and status retrieval. Operators can view queued jobs, check job results, and trigger manual retries for failed jobs.

The tiers module (`tiers/`) implements subscription tier management for SaaS pricing models. Tiers define feature access, rate limits, and resource quotas. The module integrates with the rate limits and feature flags modules to enforce tier-based restrictions.

The posts module (`posts/`) provides content management capabilities for blog posts, announcements, or user-generated content. The module supports rich content, categories, tags, and publishing workflows. Content can be scheduled for future publication and supports draft/preview modes.

## Middleware Layer (`src/app/middleware/`)

Middleware components provide cross-cutting functionality that applies to all or most API requests. The middleware stack is applied in a specific order to ensure proper request processing and response handling. Understanding the middleware order is crucial for debugging request processing issues and implementing new middleware correctly.

### Middleware Order

Middleware is applied from outermost (first in the request pipeline) to innermost (last before route handlers). The `TenantMiddleware` runs first, extracting tenant context from the request and establishing the tenant context variable. This ensures tenant isolation throughout the request lifecycle. The `RequestIDMiddleware` adds a unique request identifier for logging and tracing, enabling correlation of logs across services and requests.

The `RateLimitMiddleware` enforces rate limits based on the client identity (user, tenant, or IP). Rate limiting protects the API from abuse and ensures fair resource allocation. The `RequestBodyLimitMiddleware` enforces maximum request body sizes, preventing denial-of-service attacks through large request bodies. The `SecurityHeadersMiddleware` adds HTTP security headers to all responses, protecting against common web vulnerabilities.

The `GZipMiddleware` compresses responses for bandwidth optimization, reducing response sizes for text-based content. The `CORSMiddleware` handles cross-origin requests, enabling browser-based clients to access the API. The `TrustedHostMiddleware` (if enabled) validates the Host header to prevent host header attacks.

### Middleware Details

The TenantMiddleware extracts tenant information from various sources including subdomains, custom headers, JWT claims, and URL paths. The middleware stores the resolved tenant ID in a context variable that is accessible throughout the request. If no tenant can be resolved, the middleware can either reject the request or assign a default tenant based on the endpoint.

The RateLimitMiddleware implements distributed rate limiting using Redis counters. The middleware supports multiple rate limit configurations including per-user limits, per-tenant limits, and global limits. Rate limit responses include standard headers (X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset) for client-side rate limit management.

## Templates (`src/app/templates/`)

The templates directory contains Jinja2 email templates used by the notifications module. Templates support dynamic content through variables passed during rendering, enabling personalized emails with tenant and user information. Template organization by module keeps related templates together and simplifies template management.

## Shared Components

### Schemas (`src/app/schemas/`)

The schemas directory contains shared Pydantic models used across multiple modules. Common schemas include pagination parameters and responses, error responses, and base schemas that are extended by module-specific schemas. Shared schemas reduce duplication and ensure consistency in response formats across the API.

### Models (`src/app/models/`)

The models directory contains base SQLAlchemy model classes that are extended by module-specific models. These base classes include the UUIDMixin, TimestampMixin, and SoftDeleteMixin defined in core/db/models.py, providing common functionality across all database entities.

### CRUD (`src/app/crud/`)

The crud directory contains shared CRUD utilities and base classes for database operations. The integration with FastCRUD provides a consistent interface for common database operations across all modules. Custom CRUD implementations extend the base functionality with module-specific operations.

## Configuration Files

### pyproject.toml

The `pyproject.toml` file defines project metadata, dependencies, and tool configurations. Key dependencies include FastAPI for the web framework, Pydantic for data validation, SQLAlchemy for database ORM, asyncpg for PostgreSQL async driver, Redis for caching and queues, ARQ for background jobs, and Alembic for migrations. The file also configures development tools including ruff for linting, mypy for type checking, and pytest for testing.

### alembic.ini

The `alembic.ini` file configures the Alembic migration tool, specifying the database URL template, migration script location, and version table name. Migration scripts are located in the `migrations/versions/` directory and follow the standard Alembic migration pattern.

### setup.py

The `setup.py` script automates deployment configuration for different environments. The script copies deployment files (Dockerfile, docker-compose.yml, .env.example) to the project root and supports three deployment modes: local (development with Uvicorn auto-reload), staging (Gunicorn with Uvicorn workers), and production (NGINX reverse proxy).

## Development Conventions

### Code Style

The project uses ruff for formatting and linting, with configuration in pyproject.toml. All code must pass `ruff check` without errors before commit. Type hints are required for all function signatures, and the project uses mypy for strict type checking. Code must pass `mypy src/` without type errors.

### Async Patterns

All I/O operations must use async/await, including database queries, HTTP client calls, and Redis operations. The project uses `asyncio.gather()` for concurrent operations where appropriate. Async functions are preferred over sync functions even when both options are available, to maintain consistent async behavior throughout the application.

### Import Conventions

The project uses absolute imports from the `src.app.` root, never relative imports. This convention ensures clarity about import sources and avoids issues with circular imports and module resolution. Import organization follows the pattern: standard library, third-party, and application imports.

### Error Handling

Errors are handled through FastAPI's HTTPException for expected errors, with custom exception handlers for consistent error response formats. Error responses follow a standard schema with error code, message, and details. Logging is implemented for all errors with appropriate context for debugging.

## Testing Structure (`tests/`)

The test suite is organized by test type: unit tests for individual components, integration tests for module interactions, contract tests for API specification compliance, and performance tests for scalability validation. Tests use pytest with async fixtures for database operations. The conftest.py file provides shared fixtures for database sessions, test clients, and test data.

## Documentation Structure (`docs/`)

The documentation directory contains user guides, API documentation, and architecture references. Documentation follows the MkDocs structure with markdown files organized by topic. The docs directory is structured to support both narrative documentation (getting started, user guides) and reference documentation (API endpoints, configuration options).

## Component Relationships

Understanding component relationships is essential for making changes that don't introduce unexpected side effects. The following relationships define how components interact and depend on each other.

The main.py application imports and composes the API router and middleware stack. The API router imports module routers, which import service classes and dependencies. Service classes use CRUD operations for database access and may publish events through the event bus. The middleware stack depends on core services for configuration, security, and tenant resolution.

Database models inherit from base mixins and may reference other models through foreign keys. The tenant mixin establishes the tenant isolation pattern that all tenant-scoped models follow. Event models support the event bus pattern for inter-module communication.

## Extension Points

The project provides several extension points for customization and enhancement. New modules can be added by following the established module pattern and including the router in the API composition. New middleware can be added to the middleware stack in main.py. Custom authentication providers can replace the JWT authentication by implementing the auth service interface.

Feature flags provide a non-code extension point for feature rollout. New features can be wrapped with feature flag checks without deploying new code. Webhook events provide integration points for external systems, enabling the application to participate in broader workflows.

## Summary

The FastAPI SaaS  implements a production-ready architecture with clear separation of concerns and extensibility patterns. The project structure supports both small and large applications while maintaining consistency in code organization and conventions. Understanding the directory structure, component relationships, and extension points enables efficient navigation and modification of the codebase.

Key takeaways include the importance of the core module as a foundation for all other components, the role of middleware in cross-cutting concerns, the module structure convention for maintaining consistency, and the configuration system for environment-specific behavior. The project provides sensible defaults for common SaaS requirements while remaining flexible enough to support custom requirements.
