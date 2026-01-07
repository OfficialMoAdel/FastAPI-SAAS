# Project Structure

The FastAPI SaaS  follows a **Modular Monolith** architecture. This design promotes high cohesion within modules and low coupling between them, making the codebase easier to maintain and scale.

## Root Directory

```text
FastAPI-saas/
├── src/
│   └── app/
│       ├── api/              # API versioning and global routers
│       ├── core/             # Infrastructure and cross-cutting concerns
│       ├── middleware/       # Global FastAPI middleware
│       ├── modules/          # Feature-specific modules (The core of the app)
│       └── main.py           # Application entry point
├── tests/                    # Comprehensive test suite
├── docs/                     # MkDocs documentation
├── migrations/               # Alembic database migrations
├── pyproject.toml            # Dependency and tool configuration
└── .env                      # Environment variables (local)
```

## Module Structure

Each feature in `src/app/modules/` is a self-contained unit:

```text
modules/feature_name/
├── models.py      # SQLAlchemy database models
├── schemas.py     # Pydantic data models (validation/serialization)
├── service.py     # Business logic and service layer
├── router.py      # FastAPI path operations (endpoints)
└── crud.py        # (Optional) Custom CRUD operations if FastCRUD isn't enough
```

### 1. Models (`models.py`)
Defines the database schema using SQLAlchemy 2.0. All models should inherit from `src.app.core.db.models.Base`.

### 2. Schemas (`schemas.py`)
Defines the data structures for requests and responses using Pydantic v2. Usually inherits from `BaseCreate`, `BaseRead`, or `BaseUpdate` from the `_base` module.

### 3. Service (`service.py`)
The service layer contains the business logic. It orchestrates operations between models, external services, and the event bus. Most services inherit from `BaseService`.

### 4. Router (`router.py`)
Defines the API endpoints. Routers are kept thin, delegating logic to the service layer.

## Core Services (`src/app/core/`)

- **`db/`**: Database connection, session management, and base model classes.
- **`security/`**: JWT handling, hashing, and permission logic.
- **`events/`**: Internal event bus for cross-module communication.
- **`config.py`**: Pydantic-based settings management.

## API Organization

Routes are aggregated in `src/app/api/v1/api.py`. Each module's router is included here with appropriate prefixes and tags, ensuring a consistent URL structure (e.g., `/api/v1/users/...`).
