# Security Requirements Checklist

**Purpose**: Validate security requirements quality for credentials, isolation, and permissions
**Created**: 2026-01-07
**Feature**: 007-storage-rbac-audit (Storage, RBAC & Audit Log Enhancements)
**Focus**: Security | **Depth**: Standard

---

## Credential Protection

- [ ] CHK001 - Is the encryption algorithm explicitly specified (Fernet/AES-128-CBC)? [Clarity, Spec §FR-002]
- [ ] CHK002 - Are encryption key management requirements defined (rotation, storage, access control)? [Gap]
- [ ] CHK003 - Is the masking format for sensitive fields in responses specified (e.g., last 4 chars only)? [Clarity, plan.md]
- [ ] CHK004 - Are requirements defined for secure credential transmission (HTTPS only, no logging)? [Gap]
- [ ] CHK005 - Is key derivation or key wrapping required for per-tenant encryption? [Gap]

## Tenant Isolation

- [ ] CHK006 - Are tenant_id FK requirements specified for all new tables (StorageSettings, File)? [Completeness, data-model.md]
- [ ] CHK007 - Are CRUD filtering requirements by tenant_id documented for all queries? [Completeness, Assumptions §8]
- [ ] CHK008 - Is cross-tenant access explicitly forbidden with 404 (not 403) response? [Clarity, Spec §US1.3]
- [ ] CHK009 - Are tenant isolation requirements for signed URLs specified (tenant-scoped paths)? [Gap]
- [ ] CHK010 - Are requirements defined for tenant data cleanup on tenant deletion? [Gap, Exception Flow]

## Permission Enforcement

- [ ] CHK011 - Are all 8 new permissions mapped to specific endpoints/operations? [Completeness, Spec §FR-011-018]
- [ ] CHK012 - Is MANAGE_STORAGE_SETTINGS required for both create AND update operations? [Clarity]
- [ ] CHK013 - Are permission inheritance requirements through roles documented? [Consistency, Spec §US3.4]
- [ ] CHK014 - Is USER_SELF_READ_AND_WRITE_ONLY self-access logic explicitly defined? [Clarity, Spec §FR-011]
- [ ] CHK015 - Are requirements for combining permissions (AND/OR logic) specified? [Gap]

## File Security

- [ ] CHK016 - Are file type validation requirements specified (MIME type checking vs extension)? [Clarity, Spec §FR-004]
- [ ] CHK017 - Is content-type sniffing attack prevention addressed? [Gap, Security]
- [ ] CHK018 - Are antivirus/malware scanning requirements specified for uploads? [Gap]
- [ ] CHK019 - Is file path traversal prevention documented for local storage? [Gap, Security]
- [ ] CHK020 - Are signed URL tampering prevention requirements defined? [Gap]

## Audit & Compliance

- [ ] CHK021 - Are audit log export permissions (AUDIT_LOGS_DOWNLOAD) enforced at endpoint level? [Completeness, Spec §FR-022]
- [ ] CHK022 - Is self-logging of export events to audit trail specified? [Completeness, Spec §FR-023]
- [ ] CHK023 - Are audit log retention requirements defined? [Gap, Compliance]
- [ ] CHK024 - Are PII handling requirements for audit log exports specified? [Gap, Privacy]

---

## Summary

| Category | Items |
|----------|-------|
| Credential Protection | 5 |
| Tenant Isolation | 5 |
| Permission Enforcement | 5 |
| File Security | 5 |
| Audit & Compliance | 4 |
| **Total** | **24** |
