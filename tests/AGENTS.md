# Tests Module - Agent Guidance

## Overview

The `tests/` directory contains a comprehensive test suite organized by test type and scope. Each subdirectory serves a specific testing purpose following industry best practices.


```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/app --cov-report=html

```

## Directory Structure

```
tests/
├── unit/              # Unit tests - isolated component testing
├── e2e/                # End-to-end tests - full flow validation
├── integration/        # Integration tests - module interaction testing
├── contract/           # Contract tests - API contract validation
├── performance/        # Performance tests - benchmarks and profiling
├── benchmarks/         # Micro-benchmarks - fine-grained performance metrics
├── benchmark/          # Benchmark scenarios (pytest-benchmark based)
├── load_testing/       # Load testing - stress and capacity testing
├── core/               # Core functionality tests
├── fixtures/           # Shared test fixtures
├── factories/          # Test data factories
└── helpers/            # Test utility functions
```

Root-level support files: `conftest.py` (global fixtures), `pytest_plugin.py` (custom pytest plugin), `perf_baseline_r1_opus.json` (performance baseline).

---

## Unit Tests (`tests/unit/`)

### Purpose
Isolated tests that verify individual components, functions, and classes in complete isolation from external dependencies (database, API calls, etc.).

### Run Command
```bash
uv run pytest tests/unit/ -v
```

### Structure
```
tests/unit/
├── conftest.py                    # Unit test fixtures
├── modules/                       # Module-specific unit tests
│   ├── api_keys/                  # API key rotation & validation
│   ├── auth/                      # Authentication service tests
│   ├── ai/                        # AI provider & token tests
│   ├── audit_logs/                # Audit logging tests
│   ├── billing/                   # Billing service tests
│   ├── checkouts/                 # Checkout flow tests
│   ├── dashboard/                 # Dashboard tests
│   └── health/                    # Health check tests
├── core/                          # Core infrastructure tests
│   ├── utils/                     # Utility function tests
│   │   ├── test_bitarray.py       # Bit array operations
│   │   ├── test_gcra_rate_limit.py # Rate limiting algorithm
│   │   ├── test_pool_metrics.py   # Connection pool metrics
│   │   └── test_rate_limit_utils.py
│   ├── worker/                    # Background worker tests
│   ├── test_cache_warmer.py       # Cache warming logic
│   ├── test_email_provider.py     # Email provider
│   ├── test_env_validator.py      # Environment validation
│   ├── test_log_sanitizer.py      # Log sanitization
│   ├── test_redis_key_registry.py # Redis key management
│   ├── test_session_pipeline.py   # Session pipeline
│   ├── test_templates.py          # Template rendering
│   └── test_xfetch_lua.py         # Lua script execution
├── middleware/                    # Middleware tests
│   ├── test_attack_detection.py  # Attack detection
│   ├── test_ip_cache.py           # IP caching
│   ├── test_ip_filter.py          # IP filtering
│   ├── test_middleware_order.py   # Middleware ordering
│   ├── test_rate_limit_blacklist.py # Rate limit blacklist
│   ├── test_rate_limit_middleware.py # Rate limiting
│   ├── test_tenant_l1_cache.py    # Tenant L1 cache
│   ├── test_tenant_optimization.py # Tenant optimization
│   ├── test_tenant_orjson.py      # Tenant JSON optimization
│   └── test_query_tracking.py     # Query tracking
└── scripts/                       # Script tests
    └── test_create_module.py      # Module generator
```

### Key Patterns
- Mock all external dependencies
- Use `@pytest.fixture` for isolated test data
- Test both success and failure paths
- Focus on business logic validation

### The C-3 session oracle (`test_isomorphism_validator_c3.py`)

`scripts/perf-validation/isomorphism_validator.py` runs in CI (the
"Run isomorphism tests" step of `.github/workflows/tests.yml`), and
`tests/unit/scripts/test_isomorphism_validator_c3.py` pins its C-3
gates as a unit test so a drift is caught without waiting for CI.

C-3 has TWO closed worlds of session-holders, and both are enumerated
exhaustively:

- **HOLDERS** — the session is injected as a parameter:
  `db: Annotated[AsyncSession, Depends(async_get_db)]`. FastAPI creates
  it before the dependency body runs. Pinned in `C3_DB_CALLERS`.
- **OPENERS** — the session is opened by the function itself:
  `async with get_db_context() as X:`. Pinned in `C3_SESSION_OPENERS`.

A member of either class that is not pinned fails a gate, so the
enumeration is a closed world in the C-1 sense ("no unclassified path
reader"). The two classes are NOT interchangeable: `get_db_context` is
the `async with` form of the same lifecycle as `async_get_db`, and a
gate that models only one of them silently stops covering the other.

The 401-path property is "no except handler READS the session". A
`rollback` is the one permitted use, and that is a measured fact rather
than a preference: a rollback on a session that never connected performs
zero pool checkouts and zero statements, where a query on the same kind
of session performs one of each (`C3_ALLOWED_HANDLER_SESSION_CALLS`).
Permitted uses are LOGGED on every run, not hidden.

❌ DON'T: read a session in an except handler. The rejection path must
not touch the database, and the C-3 e2e
(`tests/e2e/feature_validation/test_dxra_three_401_shapes.py`) proves it
at the HTTP level by counting pool checkouts.

❌ DON'T: treat a green C-3 enumeration as "the session handling is
right". It proves the SET is complete and that no handler reads a
session. It does not prove the session is deferred — the e2e does that,
by measuring zero checkouts on the 401 path.

---

## End-to-End Tests (`tests/e2e/`)

### Purpose
Full-stack tests that validate complete user workflows from request to response, including all integrations (database, cache, external APIs).

### Run Command
```bash
uv run pytest tests/e2e/ -v
```

### Structure
```
tests/e2e/
├── conftest.py                    # E2E test fixtures
├── modules/                       # Module E2E tests
│   ├── auth/                      # Authentication flows
│   │   ├── test_mfa.py            # Multi-factor auth
│   │   └── test_flow_latency.py   # Auth latency
│   ├── billing/                   # Billing flows
│   ├── files/                     # File upload/download
│   ├── storage/                   # Storage operations
│   └── webhooks/                  # Webhook delivery
├── core/                          # Core E2E tests
│   ├── cache/                     # Cache operations
│   │   └── test_hit_ratio.py      # Cache hit ratio
│   ├── db/                        # Database operations
│   │   └── test_profiler.py       # Query profiling
│   ├── events/                    # Event system
│   │   └── test_pipeline.py       # Event pipeline
│   ├── resilience/                # Resilience patterns
│   │   └── test_circuit_breaker.py # Circuit breaker
│   ├── security/                  # Security validation
│   │   ├── test_auto_ban_e2e.py  # Auto-ban logic
│   │   ├── test_ip_filtering.py  # IP filtering
│   │   ├── test_log_sanitization.py # Log sanitization
│   │   ├── test_security_middleware.py # Security middleware
│   │   └── test_webhook_apikey_security.py # Webhook security
│   └── tenancy/                   # Multi-tenancy
│       └── test_isolation.py     # Tenant isolation
├── feature_validation/           # Feature validation tests
│   ├── test_auth_rate_limiting_e2e.py
│   ├── test_file_upload_quota_e2e.py
│   ├── test_session_management_e2e.py
│   └── test_token_blacklist_e2e.py
├── phase_tests/                   # Phase-specific tests
│   ├── test_phase2_db_circuit_breaker.py
│   ├── test_phase3_job_monitoring.py
│   └── test_phase4_env_validator.py
├── performance/                   # Performance E2E tests
│   ├── perf_validation_r4.py
│   ├── test_concurrent_load.py
│   └── test_opus_optimizations_e2e.py
├── static_analysis/               # Static code analysis
├── compatibility/                  # API compatibility tests
├── cache_validation/              # Cache validation
├── migrations/                     # Migration tests
│   └── test_p0_migration.py
└── utilities/                      # Utility tests
    ├── test_code_improvements_e2e.py
    ├── test_module_generator_e2e.py
    └── test_r1_opus_e2e.py
```

### Key Patterns
- Use real database and Redis connections
- Test complete user journeys
- Validate external service integrations
- Include security and isolation tests

---

## Integration Tests (`tests/integration/`)

### Purpose
Tests that verify interaction between multiple components/modules, focusing on data flow, API contracts, and module-to-module communication.

### Run Command
```bash
uv run pytest tests/integration/ -v
```

### Structure
```
tests/integration/
├── conftest.py                    # Integration fixtures
├── conftest_rls.py                # RLS-specific fixtures
├── modules/                       # Module integration tests
│   ├── ai/                        # AI module integration
│   │   ├── test_api.py            # AI API tests
│   │   └── test_session_management_api.py
│   ├── api_keys/                  # API key integration
│   │   └── test_caching.py        # Key caching
│   ├── audit_logs/                # Audit log integration
│   │   ├── test_audit_api.py
│   │   ├── test_audit_export_api.py
│   │   └── test_audit_logs.py
│   ├── auth/                      # Auth integration
│   │   ├── test_auth_lockout.py
│   │   └── test_auth_token_refresh.py
│   ├── billing/                   # Billing integration
│   │   ├── test_api.py
│   │   ├── test_billing_history_api.py
│   │   ├── test_cancellation_api.py
│   │   ├── test_checkout_api.py
│   │   ├── test_products_api.py
│   │   └── test_webhook_integration.py
│   ├── feature_flags/             # Feature flags
│   │   └── test_api.py
│   ├── files/                     # Files integration
│   │   ├── test_file_upload_integration.py
│   │   ├── test_files_api.py
│   │   ├── test_files_crud_api.py
│   │   └── test_files_signed_url.py
│   ├── magic_link/                # Magic link flow
│   │   └── test_flow.py
│   ├── notifications/             # Notifications
│   │   ├── test_api.py
│   │   └── test_notification_preferences.py
│   ├── passkey/                   # Passkey integration
│   │   └── test_login_flow.py
│   ├── rbac/                      # RBAC integration
│   │   ├── test_rbac_assignment.py
│   │   ├── test_rbac_files_permissions.py
│   │   ├── test_rbac_roles.py
│   │   ├── test_rbac_seeding.py
│   │   └── test_rbac_storage_permissions.py
│   ├── rate_limits/               # Rate limits
│   │   └── test_integration.py
│   ├── sso/                       # SSO integration
│   │   ├── test_sso_apple_flow.py
│   │   ├── test_sso_connect_flow.py
│   │   ├── test_sso_github_flow.py
│   │   ├── test_sso_google_flow.py
│   │   ├── test_sso_microsoft_flow.py
│   │   └── test_sso_settings_flow.py
│   ├── storage_settings/          # Storage settings
│   │   └── test_api.py
│   ├── users/                     # User integration
│   │   ├── test_user_caching.py
│   │   ├── test_user_preferences_flow.py
│   │   ├── test_user_tenant_email.py
│   │   └── test_user_uniqueness.py
│   └── webhooks/                  # Webhooks
│       └── test_api.py
├── core/                          # Core integration tests
│   ├── cache/                     # Cache operations
│   │   └── test_operations.py
│   ├── db/                        # Database operations
│   │   ├── test_queries.py
│   │   └── test_rls_isolation.py
│   ├── events/                    # Event system
│   │   ├── test_bus.py
│   │   └── test_system.py
│   ├── resilience/                # Resilience
│   │   ├── test_circuit_breaker.py
│   │   └── test_db_circuit_breaker_integration.py
│   ├── tenancy/                   # Multi-tenancy
│   │   ├── test_isolation.py
│   │   ├── test_tenant_api.py
│   │   ├── test_tenant_migration.py
│   │   ├── test_tenant_optimization_e2e.py
│   │   ├── test_tenant_permissions.py
│   │   └── test_tenant_whitelist.py
│   └── test_service_layer.py
├── performance/                   # Perf-harness verification
│   └── test_baseline_runner_e2e.py # scripts/run_perf_baseline.sh refusals
└── test_frontend_compatibility.py
├── test_isomorphism_flows.py
├── test_p1_verification.py
├── test_profile_image_flow.py
└── test_env_validation_startup.py
```

### Key Patterns
- Test module-to-module interactions
- Verify API contracts between services
- Validate data flow through multiple layers
- Use realistic database operations

---

## Contract Tests (`tests/contract/`)

### Purpose
Tests that validate API contracts and external service integrations, ensuring backward compatibility and adherence to specifications.

### Run Command
```bash
uv run pytest tests/contract/ -v
```

### Structure
```
tests/contract/
├── conftest.py                    # Contract fixtures
├── stripe/                        # Stripe integration
│   ├── test_checkout_session.py
│   └── test_webhooks.py
├── test_audit_isolation.py        # Audit isolation contract
├── test_magic_link_api.py         # Magic link API contract
├── test_passkey_api.py            # Passkey API contract
├── test_rbac_security.py          # RBAC security contract
├── test_sso_api.py                # SSO API contract
├── test_sso_settings_api.py      # SSO settings contract
├── test_tenant_middleware.py      # Tenant middleware contract
└── test_user_preferences_api.py   # User preferences contract
```

### Key Patterns
- Validate API response schemas
- Test external service webhook contracts
- Ensure backward compatibility
- Verify security contract implementations

---

## Performance Tests (`tests/performance/`)

### Purpose
Comprehensive performance testing including benchmarks, load testing, and performance regression detection.

### Run Command
```bash
uv run pytest tests/performance/ -v
uv run pytest tests/performance/ -v --benchmark-only
```

### Structure
```
tests/performance/
├── conftest.py                    # Performance fixtures
├── benchmarks_r4.py               # R4 benchmarks
├── benchmarks_r4_verified/        # Verified benchmark results
│   ├── bench_file_quota.py
│   ├── bench_session_manager.py
│   └── bench_token_blacklist.py
├── baseline_metrics_r4.py         # R4 baseline metrics
├── baseline_pre_rls.py            # Pre-RLS baseline
├── baseline_r2_hybrid.py          # R2 hybrid baseline
├── baseline_r4_verified.py        # Verified baselines
├── locustfile.py                  # Locust load test config
├── simple_locust.py               # Simple Locust config
├── test_benchmarks.py             # General benchmarks
├── test_equivalence.py            # Performance equivalence
├── test_optimizations_r4/         # R4 optimization tests
│   ├── test_cache_bytes.py
│   ├── test_cache_warming.py
│   ├── test_direct_dicts.py
│   ├── test_dns_async.py
│   ├── test_event_bus_parallel.py
│   ├── test_fastcrud_cache.py
│   ├── test_ip_lru_cache.py
│   ├── test_redis_pool.py
│   ├── test_returning_clause.py
│   ├── test_webhooks_hmac.py
│   └── test_worker_config.py
├── test_pool_behavior.py          # Connection pool behavior
├── test_rate_limit_behavior.py    # Rate limit performance
├── test_rbac_perf.py               # RBAC performance
├── test_resilience_benchmarks.py   # Resilience benchmarks
├── test_rls_performance.py         # RLS performance
└── test_tenant_performance.py     # Tenant performance
```

### Key Patterns
- Measure baseline performance metrics
- Track performance regressions
- Profile memory and CPU usage
- Test under various load conditions

---

## Benchmarks (`tests/benchmarks/`)

### Purpose
Micro-benchmarks for fine-grained performance measurement of individual functions and components.

### Run Command
```bash
uv run pytest tests/benchmarks/ --benchmark-only
```

### Structure
```
tests/benchmarks/
├── conftest.py                    # Benchmark fixtures
├── baseline_cache.py              # Cache baselines
├── baseline_metrics.py            # Metric baselines
├── baseline_storage.py            # Storage baselines
├── bench_cache_invalidation.py    # Cache invalidation
├── bench_r1_opus.py               # R1 Opus benchmarks
├── bench_tenant_context.py        # Tenant context benchmarks
├── memory_baseline.py             # Memory baselines
├── memory_compare.py              # Memory comparison
├── middleware_profiler.py         # Middleware profiling
├── query_baseline.py              # Query baselines
├── query_compare.py               # Query comparison
├── test_ai_perf.py                # AI performance
├── test_auth_perf.py              # Auth performance
└── test_passkey_lookup.py         # Passkey lookup perf
```

### Key Patterns
- Isolate individual function performance
- Compare implementation alternatives
- Track memory allocation
- Measure microsecond-level differences

### The A-1 specification harness (`test_attack_scan_subset.py`)

`tests/benchmarks/test_attack_scan_subset.py` is not only a ceiling file: it is
the house's SPECIFICATION for the A-1 attack-scan branch-pruning diff, which
**does not exist in production** -- `docs/adr/0009-branch-pruning-proof-basis.md`
says so in its "Routing" section (no `select_branches`, no memoisation, no
compiled alternation in `src/`).

Two things live there, and both are load-bearing:

- **The anti-thrash memo contract** (hit rate at least 99 % post-warmup, zero
  cold compiles, bounded cache) is green BY CONSTRUCTION -- the file says so
  itself -- so it constrains a future diff, it does not certify shipped code.
- **The selection thresholds** (ADR-0009's automatic fallback: the yield cap,
  the saturation break, and the 12-of-17 branch rule) are specified there as
  an executable reference selector, with one test per condition. **The numbers
  are read back out of the ADR by a test**, so editing ADR-0009 without editing
  the harness turns the harness red. The literal set and the group-to-literal
  table are PARAMETERS of that selector, so the 15-to-31 literal expansion can
  be re-run against it when it exists.

❌ DON'T: treat a green `test_attack_scan_subset.py` as evidence that branch
pruning has landed. It is the specification of a diff. The differential oracle
(`tests/unit/middleware/test_attack_pruning_differential.py`) is what proves
pruning SOUND.

---

## Load Testing (`tests/load_testing/`)

### Purpose
Stress testing and capacity planning using Locust to simulate concurrent users and traffic patterns.

### Run Command
```bash
uv run locust -f tests/load_testing/locustfile.py --host=http://localhost:8000
```

### Structure
```
tests/load_testing/
├── __init__.py
├── config.py                      # Load test configuration
├── locustfile.py                  # Locust test scenarios
└── utils.py                       # Load test utilities
```

### Key Patterns
- Define user behavior scenarios
- Configure spawn rates and peak loads
- Measure throughput and response times
- Identify breaking points

---

## Core Tests (`tests/core/`)

### Purpose
Tests for core application infrastructure and configuration.

### Run Command
```bash
uv run pytest tests/core/ -v
```

### Structure
```
tests/core/
├── __init__.py
├── test_core_config.py            # Core configuration
└── test_fail_open_metrics.py      # Fail-open metrics
```

---

## Fixtures (`tests/fixtures/`)

### Purpose
Shared pytest fixtures for test data and common test utilities.

### Run Command
Used by other test directories automatically via `conftest.py`.

### Structure
```
tests/fixtures/
├── __init__.py
├── feature_flags.py               # Feature flag fixtures
├── memory_profiler.py             # Memory profiling fixtures
└── query_counter.py               # Query counting fixtures
```

---

## Factories (`tests/factories/`)

### Purpose
Factory classes for generating test data with consistent attributes.

### Run Command
Used by test files via imports.

### Structure
```
tests/factories/
├── __init__.py
├── api_key.py                     # API key factory
├── base.py                        # Base factory
├── post.py                        # Post factory
├── tenant.py                      # Tenant factory
├── tier.py                        # Tier factory
└── user.py                        # User factory
```

### Usage Example
```python
from tests.factories import UserFactory

# Create a test user
user = await UserFactory.create_async()
```

---

## Helpers (`tests/helpers/`)

### Purpose
Utility functions and mock objects for test support.

### Run Command
Used by test files via imports.

### Structure
```
tests/helpers/
├── __init__.py
├── generators.py                  # Data generators
└── mocks.py                       # Mock objects
```

---

## Quick Reference

| Test Type | Command | Purpose |
|-----------|---------|---------|
| All tests | `uv run pytest` | Run full suite |
| Unit | `uv run pytest tests/unit/ -v` | Component isolation |
| E2E | `uv run pytest tests/e2e/ -v` | Full flow validation |
| Integration | `uv run pytest tests/integration/ -v` | Module interaction |
| Contract | `uv run pytest tests/contract/ -v` | API contracts |
| Performance | `uv run pytest tests/performance/ -v` | Performance validation |
| Benchmarks | `uv run pytest tests/benchmarks/ --benchmark-only` | Micro-benchmarks |
| Load | `uv run locust -f tests/load_testing/locustfile.py` | Load testing |

---

## Best Practices

### Test Organization
- Name test files with `test_*.py` prefix
- Use descriptive test class names: `TestUserCreation`, `TestAuthLogin`
- Group related tests in classes
- Use descriptive test function names: `test_create_user_success`

### Async Testing
- Use `async def` for all database and API tests
- Always `await` async operations
- Use `AsyncSession` for database operations

### Fixtures
- Create reusable fixtures in `conftest.py`
- Use fixtures for authentication, database sessions
- Clean up resources in fixtures

### Assertions
- Assert status codes, response shapes, critical fields
- Test both success and error cases
- Validate error messages and codes

### Performance
- Set performance baselines before changes
- Track regressions with CI/CD
- Use `--benchmark-compare` for trend analysis
