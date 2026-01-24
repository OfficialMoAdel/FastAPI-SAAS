#!/bin/bash
# Health Check Script
# Validates API health and readiness

set -e

# Configuration
API_URL="${1:-http://localhost:8000}"
TIMEOUT=10

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🏥 Running health checks against $API_URL..."
echo ""

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper function for testing
test_endpoint() {
    local name=$1
    local endpoint=$2
    local expected_status=${3:-200}
    
    echo -n "Testing $name... "
    
    response=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT "$API_URL$endpoint" 2>/dev/null || echo "000")
    
    if [ "$response" = "$expected_status" ]; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP $response)"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC} (Expected HTTP $expected_status, got $response)"
        ((TESTS_FAILED++))
        return 1
    fi
}

# Test endpoint with JSON validation
test_json_endpoint() {
    local name=$1
    local endpoint=$2
    local json_key=$3
    
    echo -n "Testing $name... "
    
    response=$(curl -s --max-time $TIMEOUT "$API_URL$endpoint" 2>/dev/null)
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time $TIMEOUT "$API_URL$endpoint" 2>/dev/null || echo "000")
    
    if [ "$http_code" != "200" ]; then
        echo -e "${RED}✗ FAIL${NC} (HTTP $http_code)"
        ((TESTS_FAILED++))
        return 1
    fi
    
    if echo "$response" | jq -e ".$json_key" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP 200, JSON valid)"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC} (JSON key '$json_key' not found)"
        ((TESTS_FAILED++))
        return 1
    fi
}

echo "=== Core Health Checks ==="
test_endpoint "Health endpoint" "/api/v1/health"
test_endpoint "Ready endpoint" "/api/v1/ready"
echo ""

echo "=== API Documentation ==="
test_endpoint "OpenAPI spec" "/openapi.json"
test_endpoint "Swagger UI" "/docs" 200
test_endpoint "ReDoc" "/redoc" 200
echo ""

echo "=== Public Endpoints ==="
test_json_endpoint "App configuration" "/api/v1/app_configuration" "app_name"
echo ""

echo "=== Authentication Endpoints ==="
# These should return 400 (missing tenant) or 401 (unauthenticated), not 500
test_endpoint "Login endpoint exists" "/api/v1/account/login" 400
test_endpoint "Register endpoint exists" "/api/v1/account/register" 400
echo ""

echo "=== New Compatibility Endpoints ==="
test_endpoint "Products endpoint exists" "/api/v1/products" 400
test_endpoint "Prices endpoint exists" "/api/v1/prices" 400
test_endpoint "Dashboard endpoint exists" "/api/v1/dashboard" 400
test_endpoint "Permissions endpoint exists" "/api/v1/permissions" 400
test_endpoint "Security endpoint exists" "/api/v1/security/passkeys" 400
echo ""

# Summary
echo "========================================="
echo -e "Tests Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests Failed: ${RED}$TESTS_FAILED${NC}"
echo "========================================="

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All health checks passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some health checks failed!${NC}"
    exit 1
fi
