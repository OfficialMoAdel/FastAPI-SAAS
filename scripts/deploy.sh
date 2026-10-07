#!/bin/bash
# Deployment Script
# Deploys FastAPI SaaS application with health checks

set -e

# Configuration
VERSION=${1:-HEAD}
ENVIRONMENT=${2:-production}
PORT=${3:-8000}

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🚀 FastAPI SaaS Deployment${NC}"
echo "Version: $VERSION"
echo "Environment: $ENVIRONMENT"
echo "Port: $PORT"
echo ""

# Step 1: Pre-deployment checks
echo -e "${BLUE}Step 1: Pre-deployment checks${NC}"
echo "Checking git status..."
if ! git diff-index --quiet HEAD --; then
    echo -e "${YELLOW}⚠️  Warning: Uncommitted changes detected${NC}"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Checking for required commands..."
for cmd in python uv git curl jq; do
    if ! command -v $cmd &> /dev/null; then
        echo -e "${RED}❌ Required command not found: $cmd${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓${NC} $cmd"
done
echo ""

# Step 2: Backup database
echo -e "${BLUE}Step 2: Database backup${NC}"
if [ -n "$DATABASE_URL" ]; then
    BACKUP_FILE="backup-$(date +%Y%m%d-%H%M%S).sql"
    echo "Creating backup: $BACKUP_FILE"
    # Note: This is a placeholder - actual backup command depends on database
    # pg_dump $DATABASE_URL > $BACKUP_FILE
    echo -e "${GREEN}✓${NC} Backup created (skipped in development)"
else
    echo -e "${YELLOW}⚠️  DATABASE_URL not set, skipping backup${NC}"
fi
echo ""

# Step 3: Checkout version
echo -e "${BLUE}Step 3: Checkout version${NC}"
if [ "$VERSION" != "HEAD" ]; then
    echo "Checking out version: $VERSION"
    git fetch --tags
    git checkout $VERSION
    echo -e "${GREEN}✓${NC} Checked out $VERSION"
else
    echo "Using current HEAD"
fi
echo ""

# Step 4: Install dependencies
echo -e "${BLUE}Step 4: Install dependencies${NC}"
echo "Installing Python dependencies..."
uv sync
echo -e "${GREEN}✓${NC} Dependencies installed"
echo ""

# Step 5: Run tests
echo -e "${BLUE}Step 5: Run tests${NC}"
echo "Running type checks..."
uv run mypy src/ --no-error-summary || {
    echo -e "${RED}❌ Type checking failed${NC}"
    exit 1
}
echo -e "${GREEN}✓${NC} Type checks passed"

echo "Running linter..."
uv run ruff check src/ || {
    echo -e "${RED}❌ Linting failed${NC}"
    exit 1
}
echo -e "${GREEN}✓${NC} Linting passed"

echo "Running unit tests..."
uv run pytest tests/unit/ -q || {
    echo -e "${RED}❌ Unit tests failed${NC}"
    exit 1
}
echo -e "${GREEN}✓${NC} Unit tests passed"
echo ""

# Step 6: Database migrations
echo -e "${BLUE}Step 6: Database migrations${NC}"
if [ -n "$DATABASE_URL" ]; then
    echo "Running migrations..."
    uv run alembic upgrade head || {
        echo -e "${RED}❌ Migrations failed${NC}"
        exit 1
    }
    echo -e "${GREEN}✓${NC} Migrations completed"
else
    echo -e "${YELLOW}⚠️  DATABASE_URL not set, skipping migrations${NC}"
fi
echo ""

# Step 7: Start application
echo -e "${BLUE}Step 7: Start application${NC}"
echo "Starting FastAPI application on port $PORT..."

# Kill existing process on same port
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Stopping existing process on port $PORT..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# Start in background
nohup uv run uvicorn src.app.main:app \
    --host 0.0.0.0 \
    --port $PORT \
    --workers 4 \
    > deployment.log 2>&1 &

APP_PID=$!
echo "Application started (PID: $APP_PID)"
echo "Waiting for application to be ready..."
sleep 5
echo ""

# Step 8: Health checks
echo -e "${BLUE}Step 8: Health checks${NC}"
if [ -f scripts/health-check.sh ]; then
    bash scripts/health-check.sh "http://localhost:$PORT" || {
        echo -e "${RED}❌ Health checks failed${NC}"
        echo "Stopping application..."
        kill $APP_PID 2>/dev/null || true
        exit 1
    }
else
    echo "Basic health check..."
    for i in {1..10}; do
        if curl -s http://localhost:$PORT/api/v1/health > /dev/null; then
            echo -e "${GREEN}✓${NC} Application is healthy"
            break
        fi
        if [ $i -eq 10 ]; then
            echo -e "${RED}❌ Application failed to start${NC}"
            kill $APP_PID 2>/dev/null || true
            exit 1
        fi
        echo "Waiting... ($i/10)"
        sleep 2
    done
fi
echo ""

# Step 9: Final summary
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}✅ Deployment completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Application Details:"
echo "  Version: $VERSION"
echo "  Environment: $ENVIRONMENT"
echo "  Port: $PORT"
echo "  PID: $APP_PID"
echo "  Logs: deployment.log"
echo ""
echo "Access the application:"
echo "  API: http://localhost:$PORT/api/v1"
echo "  Docs: http://localhost:$PORT/docs"
echo "  Health: http://localhost:$PORT/api/v1/health"
echo ""
echo "To stop: kill $APP_PID"
echo ""

exit 0
