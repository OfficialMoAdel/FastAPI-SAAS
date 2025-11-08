# Benav Labs FastAPI Boilerplate

> **Batteries-included FastAPI starter** with Pydantic v2, SQLAlchemy 2.0, PostgreSQL, Redis, ARQ jobs, rate-limiting and a minimal admin. Production-ready defaults, optional modules, and clear docs.

<p align="left">
  <a href="https://fastapi.tiangolo.com">FastAPI</a>
  <a href="https://docs.pydantic.dev">Pydantic v2</a>
  <a href="https://docs.sqlalchemy.org/en/20/">SQLAlchemy 2.0</a>
  <a href="https://www.postgresql.org">PostgreSQL</a>
  <a href="https://redis.io">Redis</a>
  <a href="https://arq-docs.helpmanual.io">ARQ</a>
</p>

**Docs:**

* 📚 [https://benavlabs.github.io/FastAPI-boilerplate/](https://benavlabs.github.io/FastAPI-boilerplate/)
* 🧠 DeepWiki: [https://deepwiki.com/benavlabs/FastAPI-boilerplate](https://deepwiki.com/benavlabs/FastAPI-boilerplate)
* 💬 Discord: [https://discord.com/invite/TEmPs22gqB](https://discord.com/invite/TEmPs22gqB)

---

## TL;DR - Quickstart

Use the template on GitHub, create your repo, then:

```bash
# Clone your new repository
git clone https://github.com/<you>/FastAPI-boilerplate
cd FastAPI-boilerplate

# In the scripts/ folder, you can find scripts to run FastAPI-Boilerplate locally, with uvicorn workers, and in production with nginx.
# NOTE: For now, only local scripts are updated.

# Running locally with Uvicorn:

# Copy Dockerfile and Docker Compose files:
cp scripts/local_with_uvicorn/Dockerfile Dockerfile
cp scripts/local_with_uvicorn/docker-compose.yml docker-compose.yml

# Copy and create your environment file
cp scripts/local_with_uvicorn/env src/.env
# If you want, modify in the minimal environment variables as described in the docs.

# Run everything using Docker:
docker compose up

# Open the API documentation
open http://127.0.0.1:8000/docs
```

> Full setup (from-scratch, .env examples, PostgreSQL & Redis, gunicorn, nginx) lives in the docs.

---

## Features

* ⚡️ Fully async FastAPI + SQLAlchemy 2.0
* 🧱 Pydantic v2 models & validation
* 🔐 JWT auth (access + refresh), cookies for refresh
* 👮 Rate limiter + tiers (free/pro/etc.)
* 🧰 FastCRUD for efficient CRUD & pagination
* 🧑‍💼 **CRUDAdmin**: minimal admin panel (optional)
* 🚦 ARQ background jobs (Redis)
* 🧊 Redis caching (server + client-side headers)
* 🐳 One-command Docker Compose
* 🚀 NGINX & Gunicorn recipes for prod

---

## When to use it

* You want a pragmatic starter with auth, CRUD, jobs, caching and rate-limits.
* You value **sensible defaults** with the freedom to opt-out of modules.
* You prefer **docs over boilerplate** in README - depth lives in the site.

Not a fit if you need a monorepo microservices scaffold - see the docs for pointers.

---

## What's inside (high-level)

* **App**: FastAPI app factory, env-aware docs exposure
* **Auth**: JWT access/refresh, logout via token blacklist
* **DB**: Postgres + SQLAlchemy 2.0, Alembic migrations
* **CRUD**: FastCRUD generics (get, get_multi, create, update, delete, joins)
* **Caching**: decorator-based endpoints cache; client cache headers
* **Queues**: ARQ worker (async jobs), Redis connection helpers
* **Rate limits**: per-tier + per-path rules
* **Admin**: CRUDAdmin views for common models (optional)

> The full tree and deep dives are in **Project Structure**, **Database**, **CRUD Operations**, **API**, **Caching**, **Background Tasks**, **Rate Limiting**, and **Production** sections of the docs.

---

## Configuration (minimal)

Create `src/.env` and set **app**, **database**, **JWT**, and **environment** settings. See the docs for a copy-pasteable example and production guidance.

[https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/](https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/)

* `ENVIRONMENT=local|staging|production` controls API docs exposure
* Set `ADMIN_*` to enable the first admin user

---

## Common tasks

```bash
# run locally with reload (without Docker)
uv sync && uv run uvicorn src.app.main:app --reload

# run Alembic migrations
cd src && uv run alembic revision --autogenerate && uv run alembic upgrade head

# enqueue a background job (example endpoint)
curl -X POST 'http://127.0.0.1:8000/api/v1/tasks/task?message=hello'
```

More examples (superuser creation, tiers, rate limits, admin usage) - **docs**.

---

## Contributing

Issues and PRs are welcome. Please read **CONTRIBUTING.md** and follow the style of existing modules (type hints, async/await, explicit None checks, and paginated responses).

---

## License

MIT - see `LICENSE.md`.

---

<p align="center">
  <a href="https://benav.io">
    <img src="https://github.com/benavlabs/fastcrud/raw/main/docs/assets/benav_labs_banner.png" alt="Powered by Benav Labs - benav.io" width="420"/>
  </a>
</p>
