#!/bin/bash
# Rollback Script
# Quickly rollback to previous version

set -e

# Configuration
PREVIOUS_VERSION=${1:-v1.0.0}
PORT=${2:-8000}

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🔄 FastAPI SaaS Rollback${NC}"
echo "Rolling back to: $PREVIOUS_VERSION"
echo "Port: $PORT"
echo ""

# Confirmation
read -p "Are you sure you want to rollback to $PREVIOUS_VERSION? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rollback cancelled"
    exit 0
fi

# Step 1: Stop current application
echo -e "${BLUE}Step 1: Stop current application${NC}"
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Stopping application on port $PORT..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
    sleep 2
    echo -e "${GREEN}✓${NC} Application stopped"
else
    echo "No application running on port $PORT"
fi
echo ""

# Step 2: Checkout previous version
echo -e "${BLUE}Step 2: Checkout previous version${NC}"
echo "Fetching tags..."
git fetch --tags
echo "Checking out $PREVIOUS_VERSION..."
git checkout $PREVIOUS_VERSION
echo -e "${GREEN}✓${NC} Checked out $PREVIOUS_VERSION"
echo ""

# Step 3: Install dependencies
echo -e "${BLUE}Step 3: Install dependencies${NC}"
uv sync
echo -e "${GREEN}✓${NC} Dependencies installed"
echo ""

# Step 4: Rollback database (optional)
echo -e "${BLUE}Step 4: Database rollback${NC}"
read -p "Rollback database migrations? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -n "$DATABASE_URL" ]; then
        echo "Rolling back last migration..."
        uv run alembic downgrade -1
        echo -e "${GREEN}✓${NC} Database rolled back"
    else
        echo -e "${RED}❌ DATABASE_URL not set${NC}"
        exit 1
    fi
else
    echo "Skipping database rollback"
fi
echo ""

# Step 5: Start application
echo -e "${BLUE}Step 5: Start application${NC}"
nohup uv run uvicorn src.app.main:app \
    --host 0.0.0.0 \
    --port $PORT \
    --workers 4 \
    > rollback.log 2>&1 &

APP_PID=$!
echo "Application started (PID: $APP_PID)"
echo "Waiting for application to be ready..."
sleep 5
echo ""

# Step 6: Health check
echo -e "${BLUE}Step 6: Health check${NC}"
for i in {1..10}; do
    if curl -s http://localhost:$PORT/api/v1/health > /dev/null; then
        echo -e "${GREEN}✓${NC} Application is healthy"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${RED}❌ Health check failed${NC}"
        kill $APP_PID 2>/dev/null || true
        exit 1
    fi
    echo "Waiting... ($i/10)"
    sleep 2
done
echo ""

# Summary
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}✅ Rollback completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Application Details:"
echo "  Version: $PREVIOUS_VERSION"
echo "  Port: $PORT"
echo "  PID: $APP_PID"
echo "  Logs: rollback.log"
echo ""
echo "To stop: kill $APP_PID"
echo ""

exit 0
