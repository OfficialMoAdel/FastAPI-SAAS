# Deployment Environment Provisioning

`src/.env` is a deployment artifact, not a tracked source file. It is ignored by
`.gitignore` (the `.env` pattern and the explicit `src/.env` entry), so a clean
`git archive`, release tarball, or checkout does not contain it. The application
does not fall back to a committed configuration file: if the required settings
are absent from the process environment, boot fails closed with Pydantic
validation errors for `POSTGRES_USER` and `POSTGRES_PASSWORD`.

This page is the operational contract for getting the file onto a target host.

## What the application actually loads

The configuration package calls `load_dotenv()` without a path and configures
`env_file=".env"`. Both resolve relative to the process working directory. It
does **not** read `src/.env` directly.

- **Docker Compose:** `docker-compose.yml` reads `./src/.env` through
  `env_file:` for the application, worker, and database services, and mounts
  that same file at `/code/.env` for the application and worker containers.
  Compose requires `./src/.env` to exist on the host before `docker compose up`.
- **Non-Docker:** the file must be provisioned as `.env` in the directory the
  process is launched from, or its variables must be exported into the process
  environment by the service manager. Launching `uv run uvicorn
  src.app.main:app` from the repository root does not load `src/.env`.

## Provisioning on a clean target

1. Obtain the environment file from your secret store or deployment system.
   Do not copy it from a source archive; it is deliberately absent there.
2. Fill every required value from `src/.env.example`, especially
   `SECRET_KEY`, `ADMIN_PASSWORD`, and the `POSTGRES_*` and `REDIS_*` settings.
3. Choose the location for the runtime you use:

   **Docker Compose**

   ```bash
   # On the target checkout, before docker compose up
   install -m 600 /path/to/provisioned.env src/.env
   docker compose up -d
   ```

   **Non-Docker, launched from the repository root**

   ```bash
   install -m 600 /path/to/provisioned.env .env
   uv run uvicorn src.app.main:app
   ```

   **Non-Docker with an injected process environment**

   Let your process manager or orchestrator inject the variables. Starting from
   a POSIX shell that already has the values in a file:

   ```bash
   set -a
   . /path/to/provisioned.env
   set +a
   uv run uvicorn src.app.main:app
   ```

4. Restrict permissions on the provisioned file (`0600` or the platform
   equivalent). Never commit it. The tracked template is `src/.env.example`;
   real values stay outside version control.

## Verify the target

The repository includes a smoke test that proves the contract without opening a
database or Redis connection:

```bash
uv run pytest tests/unit/test_deployment_env_provisioning.py -q
```

It asserts that importing the application fails closed with no environment file
and succeeds when a provisioned `.env` is present in the working directory.

For a manual check, run the import from the directory containing the
provisioned file:

```bash
uv run python -c "import src.app.main; print('BOOT_OK')"
```

`BOOT_OK` means configuration construction succeeded. It does not prove the
database or Redis are reachable; start the service and use its health endpoint
for that.

## Related runbooks

- `docs/POSTGRES_PASSWORD_ROTATION_RUNBOOK.md` for rotating the leaked
  development database password.
- `docs/HISTORY_PURGE_RUNBOOK.md` for the separate git-history purge.
