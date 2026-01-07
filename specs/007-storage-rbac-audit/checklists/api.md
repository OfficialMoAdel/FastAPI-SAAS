# API Contracts Requirements Checklist

**Purpose**: Validate API contract requirements quality for OpenAPI specs and error handling
**Created**: 2026-01-07
**Feature**: 007-storage-rbac-audit (Storage, RBAC & Audit Log Enhancements)
**Focus**: API Contracts | **Depth**: Standard

---

## Request/Response Schemas

- [ ] CHK001 - Are all request body schemas fully defined in OpenAPI contracts? [Completeness, contracts/]
- [ ] CHK002 - Are response schemas for success cases documented for all endpoints? [Completeness, contracts/]
- [ ] CHK003 - Are nullable fields explicitly marked in schemas? [Clarity, contracts/files.yaml]
- [ ] CHK004 - Are field constraints (min/max length, patterns) specified for all string fields? [Completeness]
- [ ] CHK005 - Are enum values for provider type consistent across all contracts (s3/azure/local)? [Consistency]

## Error Handling

- [ ] CHK006 - Are error response formats specified for all 4xx/5xx status codes? [Completeness, contracts/]
- [ ] CHK007 - Is 413 (file too large) error with size limits documented? [Completeness, contracts/files.yaml]
- [ ] CHK008 - Is 507 (quota exceeded) error response format defined? [Completeness, contracts/files.yaml]
- [ ] CHK009 - Is 400 (export limit exceeded) response with count and guidance defined? [Clarity, contracts/audit-export.yaml]
- [ ] CHK010 - Are validation error formats consistent across all endpoints? [Consistency]

## Endpoint Coverage

- [ ] CHK011 - Are all CRUD operations for storage settings documented (GET/POST/PATCH)? [Completeness, contracts/storage-settings.yaml]
- [ ] CHK012 - Are all file operations documented (upload/download/delete/info/url/list)? [Completeness, contracts/files.yaml]
- [ ] CHK013 - Are both export endpoints documented (export + preview count)? [Completeness, contracts/audit-export.yaml]
- [ ] CHK014 - Are X-Tenant-ID header requirements consistent across all endpoints? [Consistency]
- [ ] CHK015 - Are Authorization header requirements specified for all protected endpoints? [Completeness]

## Content Types

- [ ] CHK016 - Is multipart/form-data specified for file upload requests? [Clarity, contracts/files.yaml]
- [ ] CHK017 - Are streaming response content types documented (application/octet-stream, application/x-ndjson, text/csv)? [Completeness]
- [ ] CHK018 - Are Content-Disposition headers documented for download responses? [Gap]
- [ ] CHK019 - Are request content type requirements documented for JSON endpoints? [Completeness]

## Versioning & Compatibility

- [ ] CHK020 - Is API versioning strategy documented (/api/v1/ prefix)? [Gap]
- [ ] CHK021 - Are backward compatibility requirements for contract changes specified? [Gap]

---

## Summary

| Category | Items |
|----------|-------|
| Request/Response Schemas | 5 |
| Error Handling | 5 |
| Endpoint Coverage | 5 |
| Content Types | 4 |
| Versioning & Compatibility | 2 |
| **Total** | **21** |
