<h1 align="center"> Benav Labs FastAPI boilerplate</h1>
<p align="center" markdown=1>
  <i><b>Batteries-included FastAPI starter</b> with production-ready defaults, optional modules, and clear docs.</i>
</p>

<p align="center">
  <a href="https://benavlabs.github.io/FastAPI-boilerplate">
    <img src="docs/assets/FastAPI-boilerplate.png" alt="Purple Rocket with FastAPI Logo as its window." width="25%" height="auto">
  </a>
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
  <a href="https://deepwiki.com/benavlabs/FastAPI-boilerplate">
      <img src="https://img.shields.io/badge/DeepWiki-1F2937?style=for-the-badge&logoColor=white" alt="DeepWiki">
  </a>
</p>

**Docs:**

* 📚 [https://benavlabs.github.io/FastAPI-boilerplate/](https://benavlabs.github.io/FastAPI-boilerplate/)
* 🧠 DeepWiki: [https://deepwiki.com/benavlabs/FastAPI-boilerplate](https://deepwiki.com/benavlabs/FastAPI-boilerplate)
* 💬 Discord: [https://discord.com/invite/TEmPs22gqB](https://discord.com/invite/TEmPs22gqB)

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

## When to use it

* You want a pragmatic starter with auth, CRUD, jobs, caching and rate-limits.
* You value **sensible defaults** with the freedom to opt-out of modules.
* You prefer **docs over boilerplate** in README - depth lives in the site.

Not a fit if you need a monorepo microservices scaffold - see the docs for pointers.

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

## TL;DR - Quickstart

Use the template on GitHub, create your repo, then:

```bash
git clone https://github.com/<you>/FastAPI-boilerplate
cd FastAPI-boilerplate
```

The `scripts/` folder contains ready-to-use configurations for different deployment scenarios. Pick your path:

### Option 1: Local development with Uvicorn

Best for: **Development and testing**

```bash
cp scripts/local_with_uvicorn/Dockerfile Dockerfile
cp scripts/local_with_uvicorn/docker-compose.yml docker-compose.yml
cp scripts/local_with_uvicorn/.env.example src/.env
```

For local development, the example environment values work fine. You can modify them later if needed.

```bash
docker compose up
```

Your API will be running at http://127.0.0.1:8000 with auto-reload enabled. Open http://127.0.0.1:8000/docs to see the interactive documentation.

### Option 2: Staging with Gunicorn managing Uvicorn workers

Best for: **Staging environments and load testing**

```bash
cp scripts/gunicorn_managing_uvicorn_workers/Dockerfile Dockerfile
cp scripts/gunicorn_managing_uvicorn_workers/docker-compose.yml docker-compose.yml
cp scripts/gunicorn_managing_uvicorn_workers/.env.example src/.env
```

⚠️ **Recommended**: Change `SECRET_KEY` and passwords in the `.env` file for staging/testing environments.

```bash
docker compose up
```

### Option 3: Production with NGINX

Best for: **Production deployments**

```bash
cp scripts/production_with_nginx/Dockerfile Dockerfile
cp scripts/production_with_nginx/docker-compose.yml docker-compose.yml
cp scripts/production_with_nginx/.env.example src/.env
```

🚨 **CRITICAL**: You MUST change `SECRET_KEY`, all passwords, and sensitive values in the `.env` file before deploying!

```bash
docker compose up
```

Access your application via http://localhost (NGINX proxies to the FastAPI app).

> Full setup (from-scratch, .env examples, PostgreSQL & Redis, gunicorn, nginx) lives in the docs.

## Configuration (minimal)

Create `src/.env` and set **app**, **database**, **JWT**, and **environment** settings. See the docs for a copy-pasteable example and production guidance.

[https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/](https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/)

* `ENVIRONMENT=local|staging|production` controls API docs exposure
* Set `ADMIN_*` to enable the first admin user

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

## Contributing

Read [contributing](CONTRIBUTING.md).

## References

This project was inspired by a few projects, it's based on them with things changed to the way I like (and pydantic, sqlalchemy updated)

- [`Full Stack FastAPI and PostgreSQL`](https://github.com/tiangolo/full-stack-fastapi-postgresql) by @tiangolo himself
- [`FastAPI Microservices`](https://github.com/Kludex/fastapi-microservices) by @kludex which heavily inspired this boilerplate
- [`Async Web API with FastAPI + SQLAlchemy 2.0`](https://github.com/rhoboro/async-fastapi-sqlalchemy) for sqlalchemy 2.0 ORM examples
- [`FastaAPI Rocket Boilerplate`](https://github.com/asacristani/fastapi-rocket-boilerplate/tree/main) for docker compose

## License

[`MIT`](LICENSE.md)

## Contact

Benav Labs – [benav.io](https://benav.io), [discord server](https://discord.com/invite/TEmPs22gqB)

<hr>
<a href="https://benav.io">
  <img src="https://github.com/benavlabs/fastcrud/raw/main/docs/assets/benav_labs_banner.png" alt="Powered by Benav Labs - benav.io"/>
</a>
