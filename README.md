<h1 align="center">FastAPI SaaS </h1>
<p align="center" markdown=1>
  <i><b>A production-ready, modular FastAPI starter</b> with multi-tenancy, authentication, and comprehensive base patterns.</i>
</p>

<p align="center">
  <a href="https://fastapi.tiangolo.com">
      <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI">
  </a>
  <a href="https://www.postgresql.org">
      <img src="https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  </a>
  <a href="https://redis.io">
      <img src="https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=fff&style=for-the-badge" alt="Redis">
  </a>
</p>

## Overview

This boilerplate is a highly opinionated, production-ready foundation for building SaaS applications. It follows a modular architecture that separates concerns while providing robust base patterns for rapid development.

## Core Features

- ⚡ **Modern Stack**: Fully async FastAPI, SQLAlchemy 2.0, and Pydantic v2.
- 🧱 **Modular Architecture**: Each feature is a self-contained module (router, service, models, schemas).
- 🔐 **Advanced Auth**: JWT (access/refresh) with secure cookie support.
- 🏢 **Multi-Tenancy**: Built-in support for tenant isolation and awareness.
- 🚦 **Infrastructure**: Integrated rate limiting, background jobs (ARQ), and caching (Redis).
- 🧰 **Developer Efficiency**: `FastCRUD` for rapid data operations and a standardized `BaseService` pattern.
- 🚀 **Deployment Ready**: Standardized configurations for local, staging, and production.

## Project Structure

The project follows a modular monolith approach:

- `src/app/core/`: Essential infrastructure (DB, security, events, config).
- `src/app/modules/`: Feature-specific logic.
- `src/app/modules/_base/`: Standardized patterns for all modules.
- `src/app/api/`: API versioning and routing aggregation.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended)
- PostgreSQL and Redis

### Quick Start

1. **Clone and Setup**:
   ```bash
   git clone <your-repo>
   cd fastapi-saas
   uv sync
   ```

2. **Configure Environment**:
   Create a `.env` file in the root directory:
   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your_password
   SECRET_KEY=your_secret_key
   ADMIN_PASSWORD=admin_password
   ```

3. **Run Application**:
   ```bash
   uv run uvicorn src.app.main:app --reload
   ```

## Documentation

Full documentation is available in the `docs/` directory or can be served via MkDocs:

```bash
uv run mkdocs serve
```

## Testing & Quality

- **Tests**: `uv run pytest`
- **Linting**: `uv run ruff check`
- **Type Checking**: `uv run mypy src/`

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
