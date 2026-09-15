"""API-level dependencies (core).

H-9 note: the historical ``rate_limiter_dependency`` was removed when rate
limiting collapsed into one engine (architecture candidate: "Collapse rate
limiting into one engine"). The ASGI ``RateLimitMiddleware`` — which resolves
tier policy through the same ``resolve_rate_limits`` engine (L1 → Redis
policy cache → DB → settings) — already covers every path, so the
Depends-level re-resolution double-limited magic-link and task endpoints on
each request. Endpoint-specific brute-force budgets (auth login, MFA,
passkey, AI chat) use the engine's ``check_security_budget``; nothing needs
a general-purpose Depends limiter. Do not reintroduce one; extend
``core/security/rate_policy.py`` instead.
"""

# Re-exported request-context dependencies live in api/deps/__init__.py.
