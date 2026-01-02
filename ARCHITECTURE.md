# FastAPI SaaS - Architecture Guide

## Overview

This project follows a **Modular Monolith** architecture built on **FastAPI** and **Async SQLAlchemy**.

```
src/app/
├── api/            # API versioning & routing
├── core/           # Shared infrastructure (DB, config, security)
├── middleware/     # HTTP middleware stack
├── modules/        # Feature modules (vertical slices)
│   ├── auth/
│   ├── users/
│   ├── tenants/
│   └── ...
└── main.py         # Application entry point
```

---

## Module Structure

Each module in `src/app/modules/` is a self-contained feature with the following structure:

```
modules/<feature>/
├── __init__.py    # Public interface (lazy imports)
├── router.py      # FastAPI routes (Presentation)
├── service.py     # Business logic (Service layer)
├── schemas.py     # Pydantic DTOs
├── models.py      # SQLAlchemy models (Persistence)
└── crud.py        # Optional: FastCRUD instance
```

### Module Boundaries

> [!IMPORTANT]
> **Never import directly from another module's internal files.**

**Correct:**
```python
from src.app.modules.users import user_service  # Uses public interface
```

**Incorrect:**
```python
from src.app.modules.users.service import UserService  # Bypasses public interface
```

For cross-module communication, prefer the **Event Bus** (`core.events`).

---

## Layers

| Layer | Location | Responsibility |
|-------|----------|----------------|
| Presentation | `router.py` | HTTP routes, request/response handling |
| Service | `service.py` | Business logic, orchestration |
| Persistence | `models.py` | SQLAlchemy ORM models |
| DTOs | `schemas.py` | Pydantic validation & serialization |

---

## Core Infrastructure (`core/`)

| Component | File | Description |
|-----------|------|-------------|
| Config | `config.py` | Pydantic Settings |
| Database | `db/database.py` | Async SQLAlchemy engine |
| Security | `security/` | JWT, hashing, session management |
| Events | `events.py` | Internal event bus for decoupling |
| Setup | `setup.py` | Application factory & lifespan |

---

## Scalability

- **Stateless API**: No in-memory state; safe for horizontal scaling.
- **External State**: PostgreSQL (data), Redis (cache, tasks, rate limits).
- **Async I/O**: Non-blocking operations throughout.

---

## Adding a New Module

1. Create `src/app/modules/<name>/` with the standard structure.
2. Define public exports in `__init__.py` using lazy imports.
3. Register the router in `src/app/api/v1/__init__.py`.
4. Add models to `src/app/models/__init__.py` for Alembic discovery.
