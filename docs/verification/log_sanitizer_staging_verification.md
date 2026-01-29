# Log Sanitizer Staging Verification Checklist

This document provides a comprehensive checklist for manually verifying the log sanitizer in staging/development environments.

## Prerequisites

- Application running in development mode: `uv run uvicorn src.app.main:app --reload`
- Access to console output and log files (`src/app/logs/app.log`)
- API testing tool (curl, httpx, Postman, etc.)

## Verification Steps

### 1. JWT Token Sanitization

**Test**: Make API request with JWT in Authorization header

```bash
curl -X GET http://localhost:8000/api/v1/health \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.signature"
```

**Expected Results**:
- ✅ Console logs show `[JWT_TOKEN]` or `[REDACTED]` instead of actual JWT
- ✅ File logs (`app.log`) show sanitized version
- ✅ No `eyJhbGciOiJIUzI1NiJ9` visible in any logs

**Status**: [ ] PASS / [ ] FAIL

---

### 2. Email Address Sanitization

**Test**: Make request with email in body

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "testuser@example.com", "password": "test123"}'
```

**Expected Results**:
- ✅ Email partially masked: `t***@example.com`
- ✅ Domain preserved for debugging
- ✅ Full email `testuser@example.com` not visible

**Status**: [ ] PASS / [ ] FAIL

---

### 3. Password Sanitization

**Test**: Login request with password

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@test.com", "password": "mysecret123"}'
```

**Expected Results**:
- ✅ Password never appears in logs
- ✅ If "password" field logged, shows `password=[REDACTED]`
- ✅ No `mysecret123` visible anywhere

**Status**: [ ] PASS / [ ] FAIL

---

### 4. API Key Sanitization

**Test**: Request with API key header

```bash
curl -X GET http://localhost:8000/api/v1/data \
  -H "X-API-Key: sk_live_1234567890abcdefghij"
```

**Expected Results**:
- ✅ API key shows as `[STRIPE_KEY]` or `[REDACTED]`
- ✅ No `sk_live_1234567890abcdefghij` visible

**Status**: [ ] PASS / [ ] FAIL

---

### 5. Database Connection String Sanitization

**Test**: Trigger database error or check startup logs

**Expected Results**:
- ✅ Database URLs show: `postgresql://[USER]:[REDACTED]@host/db`
- ✅ No actual passwords visible in connection strings
- ✅ Works for PostgreSQL, MySQL, Redis, MongoDB

**Status**: [ ] PASS / [ ] FAIL

---

### 6. Credit Card Sanitization

**Test**: Request with credit card number

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -d '{"card": "4532-1234-5678-9010", "amount": 100}'
```

**Expected Results**:
- ✅ Card number shows as `[CARD_NUMBER]`
- ✅ No `4532-1234-5678-9010` visible

**Status**: [ ] PASS / [ ] FAIL

---

### 7. AWS Keys Sanitization

**Test**: Request with AWS credentials

```bash
curl -X POST http://localhost:8000/api/v1/config \
  -H "Content-Type: application/json" \
  -d '{"aws_access_key": "AKIAIOSFODNN7EXAMPLE"}'
```

**Expected Results**:
- ✅ AWS key shows as `[AWS_ACCESS_KEY]`
- ✅ No `AKIAIOSFODNN7EXAMPLE` visible

**Status**: [ ] PASS / [ ] FAIL

---

### 8. Multiple Sensitive Data in Single Request

**Test**: Request with multiple sensitive fields

```bash
curl -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.sig" \
  -H "X-API-Key: sk_test_abc123" \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@company.com", "password": "secret123"}'
```

**Expected Results**:
- ✅ JWT sanitized
- ✅ API key sanitized
- ✅ Email partially masked
- ✅ Password not visible

**Status**: [ ] PASS / [ ] FAIL

---

### 9. Exception Messages with Sensitive Data

**Test**: Trigger an error that might log sensitive data

```bash
curl -X GET http://localhost:8000/api/v1/nonexistent \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.error.sig"
```

**Expected Results**:
- ✅ Exception logs don't contain JWT
- ✅ Error messages are sanitized
- ✅ Stack traces don't leak sensitive data

**Status**: [ ] PASS / [ ] FAIL

---

### 10. Nested Sensitive Data

**Test**: Request with nested sensitive data

```bash
curl -X POST http://localhost:8000/api/v1/complex \
  -H "Content-Type: application/json" \
  -d '{
    "user": {
      "email": "nested@example.com",
      "auth": {
        "token": "eyJhbGciOiJIUzI1NiJ9.nested.sig",
        "api_key": "sk_live_nested123"
      }
    }
  }'
```

**Expected Results**:
- ✅ Nested JWT sanitized
- ✅ Nested API key sanitized
- ✅ Nested email partially masked

**Status**: [ ] PASS / [ ] FAIL

---

### 11. Unicode Handling

**Test**: Request with unicode characters and sensitive data

```bash
curl -X POST http://localhost:8000/api/v1/users \
  -H "Content-Type: application/json" \
  -d '{"name": "用户", "password": "secret123"}'
```

**Expected Results**:
- ✅ Unicode characters preserved
- ✅ Password still sanitized
- ✅ No encoding errors

**Status**: [ ] PASS / [ ] FAIL

---

### 12. Performance Check

**Test**: Monitor application performance

**Actions**:
1. Note application startup time
2. Make 100 requests and measure average response time
3. Check CPU and memory usage

**Expected Results**:
- ✅ Startup time unchanged (< 5s difference)
- ✅ No visible slowdown in request handling
- ✅ CPU usage normal
- ✅ Memory usage stable

**Status**: [ ] PASS / [ ] FAIL

---

### 13. Log File Verification

**Test**: Check log files directly

**Actions**:
1. Open `src/app/logs/app.log`
2. Search for sensitive patterns

**Expected Results**:
- ✅ No JWT tokens (search for `eyJ`)
- ✅ No API keys (search for `sk_live`, `sk_test`)
- ✅ No passwords (search for common passwords)
- ✅ Emails partially masked
- ✅ JSON format valid and parseable

**Status**: [ ] PASS / [ ] FAIL

---

### 14. Console Output Verification

**Test**: Review console output

**Expected Results**:
- ✅ Colored output works (development mode)
- ✅ Sensitive data sanitized in console
- ✅ Logs are readable and useful for debugging
- ✅ No errors from sanitization process

**Status**: [ ] PASS / [ ] FAIL

---

### 15. Edge Cases

**Test**: Various edge cases

**Actions**:
1. Empty request body
2. Very long log messages (>10KB)
3. Malformed JSON
4. Special characters in fields

**Expected Results**:
- ✅ No crashes or errors
- ✅ Sanitization still works
- ✅ Application remains stable

**Status**: [ ] PASS / [ ] FAIL

---

## Summary

**Total Tests**: 15
**Passed**: ___
**Failed**: ___
**Pass Rate**: ___%

## Issues Found

| Issue # | Description | Severity | Status |
|---------|-------------|----------|--------|
| 1       |             |          |        |
| 2       |             |          |        |
| 3       |             |          |        |

## Sign-off

**Verified By**: _______________
**Date**: _______________
**Environment**: Development / Staging / Production
**Application Version**: _______________

## Notes

- All sensitive data patterns are sanitized correctly
- No performance degradation observed
- Application functions normally
- Ready for production deployment

## Next Steps

- [ ] Create bug tickets for any issues found
- [ ] Update documentation if needed
- [ ] Deploy to production
- [ ] Monitor production logs for first 24 hours
