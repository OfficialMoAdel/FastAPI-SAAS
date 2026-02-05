# MFA Module - Agent Guidance

## Package Identity

Time-based One-Time Password (TOTP) multi-factor authentication module. RFC 6238 compliant implementation supporting Google Authenticator, Authy, Microsoft Authenticator, and other TOTP apps. Provides backup codes for account recovery.

## Setup & Run

```bash
# Run MFA tests
uv run pytest tests/unit/modules/mfa/ -v

# Test MFA setup flow
curl -X POST http://localhost:8000/api/v1/mfa/setup \
  -H "Authorization: Bearer <token>"

# Enable MFA with verification token
curl -X POST http://localhost:8000/api/v1/mfa/enable \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"totp_secret": "<secret>", "totp_token": "123456"}'

# Verify MFA token
curl -X POST http://localhost:8000/api/v1/mfa/verify \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"totp_token": "123456"}'

# Complete MFA login
curl -X POST http://localhost:8000/api/v1/mfa/complete-login \
  -H "Content-Type: application/json" \
  -d '{"mfa_token": "<pending_token>", "totp_token": "123456"}'
```

## Patterns & Conventions

### DO: Use MFAService for MFA logic

```python
from src.app.modules.mfa.service import MFAService, get_mfa_service

mfa_service = get_mfa_service()

# Generate TOTP setup
setup = await mfa_service.generate_totp_secret(user_id, db)
# Returns: {totp_secret, otpauth_url, qr_code_base64}

# Enable MFA
result = await mfa_service.enable_mfa(user_id, request, db)
# Returns: {status, backup_codes, backup_codes_remaining}

# Verify MFA code
result = await mfa_service.verify_totp_token(user_id, request, db)
# Returns: {valid, message, backup_codes_remaining}

# Check if MFA enabled
is_enabled = await mfa_service.is_mfa_enabled(user_id, db)
```

### DON'T: Implement MFA logic in routers

```python
# BAD - MFA logic in router
@router.post("/verify")
async def verify_mfa(...):
    # TOTP verification, backup code checking here - don't do this
    totp = pyotp.TOTP(secret)
    if totp.verify(token):
        ...
```

### DO: Handle MFA login flow properly

```python
# In AuthService.login() - password auth succeeds
if mfa_enabled:
    # Return MFA pending token instead of full auth
    return {
        "mfa_required": "true",
        "mfa_token": "<5-min-token>",
        "expires_in": "300"
    }

# User then calls /mfa/complete-login with MFA pending token + code
@router.post("/complete-login")
async def complete_mfa_login(request: MFACompleteLoginRequest, ...):
    # Validate pending token
    token_data = await mfa_token_manager.validate_token(request.mfa_token)
    # Verify MFA code
    result = await mfa_service.verify_totp_token(user_id, request, db)
    # Return full access tokens
```

### DO: Use backup codes for recovery

```python
# Backup codes are one-time use
result = await mfa_service.verify_totp_token(
    user_id=user_id,
    request=MFAVerifyRequest(backup_code="BC-ABCDE-FGHIJ"),
    db=db
)
# If valid: code is removed from list automatically
```

### DO: Handle rate limiting

```python
# MFA verify is rate limited (5 attempts per minute)
from src.app.modules.mfa.router import check_mfa_rate_limit

@app.post("/verify")
async def verify_mfa(request: Request, ...):
    await check_mfa_rate_limit(request, identifier=str(user_id))
    # Continue with verification...
```

## Module Structure

```
src/app/modules/mfa/
├── __init__.py           # Exports MFAService, get_mfa_service
├── router.py             # /mfa endpoints (setup, enable, verify, etc.)
├── service.py            # MFAService class
├── models.py             # MFAMethod SQLAlchemy model
├── schemas.py            # Request/response schemas
└── crud.py               # FastCRUD operations
```

## API Endpoints

| Method | Endpoint | Auth Required | Description |
|--------|----------|---------------|-------------|
| POST | /mfa/setup | ✓ Yes | Generate TOTP secret and provisioning URI |
| POST | /mfa/enable | ✓ Yes | Enable MFA after token verification |
| POST | /mfa/verify | ✓ Yes | Verify TOTP token or backup code |
| POST | /mfa/disable | ✓ Yes | Disable MFA (requires token) |
| GET | /mfa/status | ✓ Yes | Get MFA status and backup codes count |
| POST | /mfa/backup-codes | ✓ Yes | Regenerate backup codes |
| POST | /mfa/complete-login | ✗ No | Complete MFA login flow |

## Data Flow

### MFA Setup Flow
```
1. User calls POST /mfa/setup
   ↓
2. Server generates TOTP secret + provisioning URI
   ↓
3. User scans QR code with authenticator app
   ↓
4. User calls POST /mfa/enable with secret + verification token
   ↓
5. Server verifies token, enables MFA, returns backup codes
```

### MFA Login Flow
```
1. User calls POST /auth/login with credentials
   ↓
2. Server validates password, checks if MFA enabled
   ↓
3. If MFA enabled:
   - Generate MFA pending token (5-min expiry)
   - Return {mfa_required: true, mfa_token: "..."}
   ↓
4. User calls POST /mfa/complete-login with mfa_token + totp_token
   ↓
5. Server validates pending token + MFA code
   ↓
6. Return full access tokens (same as normal login)
```

## Security Considerations

- **TOTP secrets**: Encrypted with XOR using SECRET_KEY before storage
- **Backup codes**: Stored as SHA-256 hashes, one-time use
- **Rate limiting**: 5 attempts per minute per user
- **MFA pending tokens**: 5-minute expiry, one-time use, stored in Redis
- **Token tolerance**: ±30 seconds (TOTP_SKEW=1) for clock drift
- **Lockout**: After 5 failed attempts, user must wait 1 minute

## Configuration

```python
# TOTP Settings (in src/app/modules/mfa/service.py)
TOTP_INTERVAL = 30      # 30 seconds per code
TOTP_DIGITS = 6         # 6-digit codes
TOTP_SKEW = 1           # ±1 interval tolerance
BACKUP_CODE_COUNT = 10  # Number of backup codes
BACKUP_CODE_LENGTH = 10 # Characters after prefix
BACKUP_CODE_PREFIX = "BC-"

# Rate Limiting (in src/app/modules/mfa/router.py)
MFA_VERIFY_LIMIT = 5    # attempts
MFA_VERIFY_PERIOD = 60  # seconds

# Token Expiry (in src/app/core/security/__init__.py)
MFA_PENDING_EXPIRY_SECONDS = 300  # 5 minutes
```

## Dependencies

```toml
[project.dependencies]
pyotp = ">=2.9.0"      # TOTP implementation
```

## Touch Points

- **MFA service**: `modules/mfa/service.py::MFAService`
- **TOTP library**: `pyotp.TOTP`
- **Auth integration**: `modules/auth/service.py::AuthService.login()`
- **Pending tokens**: `src.app.core.security::MFAPendingTokenManager`
- **Rate limiting**: `src.app.core.utils.rate_limit::RateLimiter`
- **User model**: `modules/users/models.py::User.mfa_methods` relationship

## JIT Index Hints

```bash
# Find MFA endpoints
rg -n "router\.(post|get)" src/app/modules/mfa/

# Find TOTP verification
rg -n "verify_totp_token|verify.*token" src/app/modules/mfa/

# Find backup code handling
rg -n "backup_code|backup_codes" src/app/modules/mfa/

# Find MFA login flow
rg -n "mfa_pending|complete.*login|MFAPendingTokenManager" src/app/

# Find rate limiting
rg -n "check_mfa_rate_limit|MFA_VERIFY_LIMIT" src/app/modules/mfa/

# Find encryption methods
rg -n "_encrypt_secret|_decrypt_secret" src/app/modules/mfa/
```

## Common Gotchas

- **TOTP codes change every 30 seconds**: Users must enter code quickly
- **Clock drift**: ±30 second tolerance configured, but large drift causes failures
- **Backup codes are one-time use**: Once used, they cannot be reused
- **MFA pending tokens expire in 5 minutes**: User must complete login quickly
- **Rate limiting applies to verification**: 5 attempts per minute, then locked out
- **Backup codes format**: Must be BC-XXXXX-XXXXX or XXXXX-XXXXX format
- **Secret storage**: Encrypted in database, never returned after enablement

## Testing

```bash
# Run all MFA tests
uv run pytest tests/unit/modules/mfa/ -v

# Test specific methods
uv run pytest tests/unit/modules/mfa/test_service.py::TestEnableMFA -v
uv run pytest tests/unit/modules/mfa/test_service.py::TestVerifyTotpToken -v

# Run with coverage
uv run pytest tests/unit/modules/mfa/ --cov=src/app/modules/mfa --cov-report=term-missing
```

## Pre-PR Checks

```bash
uv run pytest tests/unit/modules/mfa/ -v && \
uv run ruff check src/app/modules/mfa/ && \
uv run mypy src/app/modules/mfa/
```
