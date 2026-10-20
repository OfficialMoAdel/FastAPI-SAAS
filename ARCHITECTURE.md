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
> Cross-module imports must go through the package root:
> `from src.app.modules.users import UserService`.
>
> This is the **aspiration**; it is not the current state of the tree, and
> the two differ by 194 live import statements. Read the "Decision —
> SOFTENED" note below before relying on it. It is kept here as the stated
> direction, not as a description of what lint enforces.

**Correct:**
```python
from src.app.modules.users import UserService  # Uses the public facade
```

**Incorrect:**
```python
from src.app.modules.users.service import UserService  # Bypasses the facade
```


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

| Component | Location | Description |
|-----------|----------|-------------|
| Config | `config/` (package) | Pydantic Settings, split by domain: `app.py`, `auth.py`, `cache.py`, `security.py`, `database.py`, … |
| Database | `db/database.py` | Async SQLAlchemy engine |
| Security | `security/` | JWT, hashing, session management |
| Events | `src.app.core.events` (package) | Internal event bus: `bus.py` holds `EventBus`; `worker.py`, `notifications.py`, `arq_adapter.py` wire it up |
| Setup | `setup.py` | Startup/shutdown phases and resource pools — **not** the app factory |

---

## Scalability

- **Stateless API**: No in-memory state; safe for horizontal scaling.
- **External State**: PostgreSQL (data), Redis (cache, tasks, rate limits).
- **Async I/O**: Non-blocking operations throughout.

---

## Adding a New Module

1. Create `src/app/modules/<name>/` with the standard structure.
2. Define public exports in `__init__.py` using lazy imports.
3. Register the router in the matching **group aggregator**, not in
   `src/app/api/v1/__init__.py` — the former flat include list there is now
   split across `src/app/api/v1/core.py` (everything except billing/admin),
   `src/app/api/v1/billing.py`, and `src/app/api/v1/admin_group.py`. Edit
   the group file, not the composer; include order across the three groups
   preserves the historical OpenAPI path order.
4. Add models to `src/app/models/__init__.py`. That single registry import
   is what wires every table into `Base.metadata` for Alembic
   autogenerate and for both the web app and the ARQ worker —
   `src/migrations/env.py` imports `src.app.models` and reads
   `Base.metadata`; it does not scan the modules tree.

---

## Doc Pointer Integrity

Every rooted `src/…`, `tests/…`, `scripts/…`, `docs/…` and `.github/…`
path in this file is a claim that the path exists, and is enforced in CI:

```bash
uv run python scripts/checks/doc_paths.py
```

If a pointer here fails, fix it or add a reasoned `ALLOWLIST` entry in the
script. A documented path that does not exist is worse than an undocumented
one: it is a claim the reader will trust and spend time verifying.

The same gate covers two other claim forms, because a doc makes the same
kind of promise in three spellings and all three are promises:

* **backticked dotted module paths** — `` `src.app.core.config` `` is a claim
  that the name is importable. Resolved against a module file *or* a
  package `__init__.py`, so a package and a file are both accepted and the
  difference between them stays checkable.
* **symbol references** — `` `core/errors.py::setup_exception_handlers` ``
  and `` `src.app.core.rbac.dependencies::requires_permission` `` claim that
  the file exists *and* binds that name.

Allow-lists for these are `MODULE_ALLOWLIST` and `SYMBOL_ALLOWLIST`, and each
entry needs a reason. A **future** module named in a doc is a legitimate
thing to write down — this document contains one — but it has to be
allow-listed with a stated intention, which is what stops an intention and a
forgotten module from looking identical to the next reader.
