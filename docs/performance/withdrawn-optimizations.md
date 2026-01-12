# Withdrawn Performance Optimizations

This document maintains a record of performance optimization recommendations that were proposed but subsequently withdrawn. Keeping this history prevents regression into known anti-patterns and documents the architectural reasoning behind these decisions.

!!! warning "Status: Withdrawn"
    These recommendations are **NOT** to be implemented. They are documented here solely for historical context and architectural alignment.

## W1: Bloom Filter for Cache

!!! failure "Recommendation"
    Implement Bloom filters to reduce Redis cache misses.

**Reason for Withdrawal:**
Redis `GET` operations are already sub-millisecond. The overhead of maintaining a Bloom filter (memory + CPU for hashing) outweighs the cost of a cache miss (which just results in a fast DB index lookup). This adds complexity without measurable benefit for our current scale.

??? note "Technical Context"
    Bloom filters are probabilistic data structures great for "possibly in set" vs "definitely not in set" checks. However, in our architecture, a "not in cache" result simply triggers a database lookup, which is efficient enough. Adding a Bloom filter layer would introduce:
    *   Additional memory usage.
    *   Synchronization challenges between the filter and the actual cache.
    *   Development complexity for negligible read-path optimization.

## W2: Connection Pool Tuning

!!! failure "Recommendation"
    Aggressively tune SQLAlchemy connection pool size and overflow parameters.

**Reason for Withdrawal:**
Premature optimization. The default `asyncpg` and SQLAlchemy pool settings are robust for current load. Tuning these without production load data risks resource exhaustion or instability. We will rely on metrics (when available) to drive any future tuning.

??? note "Technical Context"
    The default pool size (usually 5-10) with some overflow is generally sufficient for thousands of concurrent requests in an async environment, as connections are returned to the pool immediately after use. Increasing the pool size arbitrarily consumes database memory and connection slots, potentially degrading overall database performance.

## W3: Async Stripe Integration

!!! failure "Recommendation"
    Refactor Stripe integration to be fully async.

**Reason for Withdrawal:**
Already implemented. The current billing module uses the async Stripe client where applicable. No further refactoring is required.

## W4: Query Prefetching

!!! failure "Recommendation"
    Implement complex lookahead query prefetching.

**Reason for Withdrawal:**
Speculative complexity. `selectinload` handles our relationship loading needs efficiently (solving N+1 problems). Custom prefetching logic would couple the service layer too tightly to specific data shapes, harming maintainability.

??? note "Technical Context"
    SQLAlchemy's `selectinload` strategy is highly optimized for async loading of related collections. It emits a second `SELECT ... WHERE IN (...)` query, which is very fast. Manual prefetching often involves constructing complex joins or separate queries that are harder to read and maintain than the ORM's built-in capabilities.

## W5: Response Compression

!!! failure "Recommendation"
    Implement Gzip/Brotli compression middleware in FastAPI.

**Reason for Withdrawal:**
Infrastructure concern. Response compression is better handled by the reverse proxy (Nginx/Traefik) or CDN (Cloudflare). Handling it in Python consumes CPU cycles better spent on application logic.

## W6: Lazy Loading

!!! failure "Recommendation"
    Enable lazy loading for all relationships.

**Reason for Withdrawal:**
Anti-pattern in Async. Implicit lazy loading triggers I/O, which fails in async contexts if not carefully managed (often leading to `Greenlet` errors or sync I/O blocking). We strictly enforce **eager loading** (`selectinload`/`joinedload`) at the service layer to ensure data is fully available before leaving the transaction scope.

??? note "Technical Context"
    In `SQLAlchemy` async mode, accessing a relationship that wasn't loaded raises an `MissingGreenlet` or `DetachedInstanceError`. "Lazy loading" implies an implicit DB query on attribute access, which is synchronous by nature. To avoid this, we must explicitly load all necessary data using options like `options(selectinload(User.items))` during the initial query.
