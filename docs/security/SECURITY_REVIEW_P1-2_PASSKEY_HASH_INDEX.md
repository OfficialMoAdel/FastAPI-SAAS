# Security Review: Passkey Credential Hash Index (P1-2)

> **Status**: APPROVED
> **Reviewer**: AI Security Analysis (Claude)
> **Date**: 2026-01-16
> **Ticket**: FastAPI_saas-3992

---

## Executive Summary

**APPROVED** - The proposed optimization to add a SHA256 hash column for passkey credential lookup is security-safe and recommended for implementation.

---

## Scope

### Current Implementation
- **Location**: `src/app/modules/passkey/service.py:447-458`
- **Behavior**: O(n) full table scan - decrypts ALL credentials to find match
- **Performance**: Linear degradation as credentials grow

### Proposed Change
1. Add `credential_id_hash` column (SHA256 of decrypted credential_id)
2. Create B-tree index on `credential_id_hash`
3. Query by hash for O(log n) lookup

---

## Security Analysis

### 1. Data Classification

| Data Element | Classification | Rationale |
|--------------|----------------|-----------|
| `credential_id` | **PUBLIC** | Sent in every WebAuthn authentication request |
| `credential_id_hash` | **PUBLIC** | Hash of public data |
| `public_key` | CONFIDENTIAL | Encrypted at rest |
| `counter` | INTERNAL | Replay prevention mechanism |

**Conclusion**: The credential_id is already transmitted in plaintext during authentication. Storing its hash does not expose new information.

### 2. Cryptographic Assessment

| Property | Analysis | Status |
|----------|----------|--------|
| **Hash Algorithm** | SHA256 (256-bit output) | APPROVED |
| **Pre-image Resistance** | 2^256 operations to reverse | SECURE |
| **Collision Resistance** | 2^128 operations for collision | SECURE |
| **Input Entropy** | credential_id is 16+ bytes CSPRNG | HIGH |

**Why SHA256 is sufficient**:
- SHA256 is NIST-approved and widely deployed
- SHA512 provides no practical security benefit for this use case
- Input is already high-entropy random data

### 3. Attack Surface Analysis

#### 3.1 Rainbow Table Attack
- **Risk**: NONE
- **Rationale**: credential_id has ~128 bits entropy (CSPRNG generated)
- **Pre-computation**: Infeasible - 2^128 entries required

#### 3.2 Timing Attack
- **Risk**: LOW (mitigated)
- **Rationale**: Database index lookup is constant-time
- **Recommendation**: Use `secrets.compare_digest()` for any byte comparison

#### 3.3 Oracle Attack
- **Risk**: LOW (accepted)
- **Rationale**: Attacker can determine if credential exists via hash
- **Mitigation**: credential_id is already public in WebAuthn protocol
- **Note**: Auth endpoints already have rate limiting

#### 3.4 Hash Collision
- **Risk**: NEGLIGIBLE
- **Rationale**: SHA256 collision probability is ~2^-128
- **Impact**: Even if collision occurred, subsequent decryption verification would fail

### 4. Salting Analysis

| Factor | Decision | Rationale |
|--------|----------|-----------|
| **Salt Required?** | NO | Input is already high-entropy random bytes |
| **Benefit of Salt** | None | Cannot improve security of random input |
| **Cost of Salt** | Complexity | Would require per-credential salt storage |

**Conclusion**: Salting is not required and would add unnecessary complexity.

---

## Implementation Requirements

### MUST Have
1. Use SHA256 for hashing (not MD5, SHA1)
2. Hash the **decrypted** credential_id, not the encrypted value
3. Create database index on hash column
4. Backfill existing credentials with computed hashes

### SHOULD Have
1. Use timing-safe comparison where applicable
2. Add migration with proper null handling
3. Verify hash on lookup matches decrypted value (defense in depth)

### MAY Have
1. Add telemetry for lookup performance monitoring
2. Consider compound index if tenant-scoped queries common

---

## Migration Strategy

```sql
-- Phase 1: Add nullable column
ALTER TABLE passkey_credentials
ADD COLUMN credential_id_hash BYTEA;

-- Phase 2: Backfill (application-level - requires decryption)
-- Run migration script to compute hashes

-- Phase 3: Add index
CREATE INDEX idx_passkey_credential_id_hash
ON passkey_credentials (credential_id_hash);

-- Phase 4: Make non-nullable (after backfill complete)
ALTER TABLE passkey_credentials
ALTER COLUMN credential_id_hash SET NOT NULL;
```

---

## Rollback Plan

1. Remove index: `DROP INDEX idx_passkey_credential_id_hash;`
2. Revert service code to use decryption-based lookup
3. Column can remain (no security impact) or be dropped

---

## Approval

| Role | Status | Notes |
|------|--------|-------|
| Security Review | APPROVED | No security concerns identified |
| Performance | APPROVED | O(n) → O(log n) improvement |
| Implementation | READY | Proceed with P1-2 task |

---

## References

- [WebAuthn Specification - Credential ID](https://www.w3.org/TR/webauthn-2/#credential-id)
- [NIST SP 800-107 - SHA-256](https://csrc.nist.gov/publications/detail/sp/800-107/rev-1/final)
- Task: FastAPI_saas-ajpi (P1-2 Implementation)
- Parent: FastAPI_saas-15v (PERF-OPT-R1-OPUS)
