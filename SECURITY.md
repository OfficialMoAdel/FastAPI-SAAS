# Security Guidelines

## Webhook Signature Generation

### Critical Constraint: stdlib json MUST be used

**Location:** `src/app/modules/webhooks/service.py::_generate_signature()`

**Issue:** The webhook HMAC-SHA256 signature is calculated from the exact byte representation of the JSON payload. Different JSON libraries produce different formatting, which results in different signatures.

**Why This Matters:**

```python
# stdlib json.dumps produces:
{"key": "value"}  # space after colon

# orjson.dumps produces:
{"key":"value"}   # no space after colon
```

Different bytes → Different HMAC signature → Webhook consumers fail signature validation

**Impact of Change:**
- Changing to orjson or any other JSON library would **BREAK ALL existing webhook integrations**
- Webhook consumers would fail signature validation
- This would be a **breaking change** requiring all consumers to update their signature verification

**Enforcement:**
- Tests verify stdlib json is used (see `tests/unit/test_webhook_service.py`)
- Code comments document this constraint
- This is **intentional and correct behavior**

**Related Tests:**
- `test_webhook_signature_uses_stdlib_json` - Verifies json import, not orjson
- `test_signature_has_spaces_after_colons` - Verifies output format compatibility

**References:**
- Task: FastAPI_saas-wpv5.16
- Analysis: Opus security gap analysis
- Module: `src/app/modules/webhooks/`

---

## Reporting Security Issues

If you discover a security vulnerability, please email security@example.com instead of using the issue tracker.
