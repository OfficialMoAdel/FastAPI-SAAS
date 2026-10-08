# --------- Builder Stage ---------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# Set environment variables for uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (for better layer caching)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the project source code
COPY . .

# Install the project in non-editable mode
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --------- Final Stage ---------
FROM python:3.13-slim-bookworm

# System dependencies for runtime (PostgreSQL client, etc)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for security
RUN useradd -m -s /bin/bash app

# Set the working directory
WORKDIR /code

# Copy the virtual environment from the builder stage
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Copy application source code (required for standalone docker build)
COPY --from=builder --chown=app:app /app/src /code/src

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/code"

# The app writes rotating file logs to <repo-root>/logs, which resolves to
# /code/logs given WORKDIR /code and PYTHONPATH=/code (AppSettings.LOG_DIR,
# core/config/app.py). The image never created it, and /code is root-owned, so
# the non-root `app` user could not create it either - every container exited at
# startup with:
#
#     PermissionError: [Errno 13] Permission denied: '/code/logs'
#
# Owned by app BEFORE dropping privileges, so the runtime user can write to it.
RUN mkdir -p /code/logs && chown -R app:app /code/logs

# Switch to the non-root user
USER app

# Expose port
EXPOSE 8000

# -------- replace with comment to run with gunicorn --------
CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
# CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]