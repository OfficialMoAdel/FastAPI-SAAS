# Welcome to FastAPI SaaS

A production-ready, modular FastAPI starter to speed up your development.

## What is this Boilerplate?

This is a comprehensive, modular foundation for building scalable SaaS applications. It leverages the latest Python ecosystem to provide a stable, efficient, and secure starting point.

## Core Principles

- **Modularity**: Every feature is isolated in its own module, following a consistent structure.
- **Type Safety**: Strict typing with Mypy and Pydantic v2.
- **Async First**: Non-blocking I/O throughout the entire stack.
- **Tenancy Awareness**: Multi-tenancy is baked into the core services.

## Architecture Highlights

### The Module Pattern
Each module in `src/app/modules/` follows a standardized structure:
- `models.py`: Database schema definitions.
- `schemas.py`: Data validation and serialization.
- `service.py`: Business logic and orchestration.
- `router.py`: API endpoint definitions.
- `crud.py`: Data access layer using FastCRUD.

### The Base Pattern
The `_base` module provides abstract classes that standardize common operations:
- `BaseService`: Handles standard CRUD orchestration, logging, and exceptions.
- `BaseCreate`, `BaseRead`, `BaseUpdate`: Standardized Pydantic schemas.

## Technical Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com)
- **Validation**: [Pydantic V2](https://docs.pydantic.dev/)
- **ORM**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/)
- **Database**: [PostgreSQL](https://www.postgresql.org)
- **Cache/Queue**: [Redis](https://redis.io)

## Getting Started

[Follow the Installation Guide →](getting-started/index.md)
