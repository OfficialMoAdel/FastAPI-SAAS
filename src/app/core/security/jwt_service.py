"""JWT token lifecycle service — issuance, verification policy, and revocation.

Authority contract (P2-#9, deletion test):
    This module OWNS encoding (``jwt.encode`` via ``_jwt_encode``), claim
    shaping for issuance, and the policy layer around verification (token
    type, blacklist, token-version). It must NOT duplicate cryptographic
    decode: every decode routes through
    ``jwt_utils.decode_verified_token`` (the ONE decode path), imported
    via fn-scope seams. If ``jwt_utils`` vanished, this module could absorb
    its duties without touching callers — that is the fold direction;
    until then the boundary is: **encode + policy here, decode + key
    management in ``jwt_utils``**. Issuance itself has a single door:
    ``auth.services.tokens.TokenIssuer.issue_session`` (stable ``sid`` +
    RESERVED_CLAIMS protection live there and must not move).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID, uuid4

import jwt
from cachetools import TTLCache
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jwt.exceptions import PyJWTError as JWTError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenData
from src.app.core.security.pending_token_store import PendingTokenStore
from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)

# P2.21: In-memory short-TTL cache for DB token lookups to prevent thundering herds on Redis miss
# H3 FIX: ttl=5 for negative (not blacklisted) poisons revocation for 5s. Cache only
# positive (blacklisted=True) entries; negative results are not cached (fail-closed per call).
_db_fallback_cache: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=300)
_in_flight_db_checks: dict[str, asyncio.Task[bool]] = {}
_db_fallback_lock = asyncio.Lock()

# P6: singleflight for token_version DB lookups on Redis miss — collapses a
# cache-miss herd for the same user into ONE SELECT (mirrors the
# _in_flight_db_checks pattern above).
_in_flight_token_version: dict[str, asyncio.Task[int | None]] = {}
_token_version_lock = asyncio.Lock()

_jwt_logger = get_logger(__name__)


# PII: a user subject (username OR email) is personal data, and this module's
# events name the subject it is deciding on. The log sanitizer does NOT cover
# it: ``LogSanitizer._SENSITIVE_KEY_RE`` matches password/secret/token/auth/
# credential, NOT ``user``/``username``/``email``, and a bare subject with no
# "@" trips no SENSITIVE_PATTERNS entry either — so ``user=alice_hunt`` was
# rendered verbatim. An email subject was only shape-masked to
# ``a***@corp.example.com``, which still identifies the person and their
# employer. Both are the sites this helper exists for.
#
# HMAC, not bare SHA-256: a subject is low-entropy, so an unsalted digest is
# reversible by rainbow table — exactly the reason ``security/tokens.py``
# peppers its digests. The domain string is distinct from that module's, so a
# log reference can never be correlated with a stored token digest.
_LOG_SUBJECT_DOMAIN = "saas-log-subject-v1"


@lru_cache(maxsize=1)
def _log_subject_pepper() -> bytes:
    """Pepper for :func:`subject_log_ref`, bound to the log-subject domain."""
    return f"{_LOG_SUBJECT_DOMAIN}:{settings.SECRET_KEY.get_secret_value()}".encode()


def subject_log_ref(subject: str | None) -> str:
    """Stable, non-reversible log reference for a user subject.

    Same subject -> same reference within a deployment (so an incident can
    still be followed across events), without the log ever holding the
    identifier. Empty/None is labelled rather than hashed, so "no subject"
    stays greppable.
    """
    if not subject:
        return "anonymous"
    return hmac.new(_log_subject_pepper(), subject.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


class TokenVersionLookup(Protocol):
    """Protocol for resolving user token_version without core importing modules (PR-12 / DIP)."""

    async def __call__(self, username_or_email: str) -> int | None: ...


class UserTenantLookup(Protocol):
    """Protocol for resolving user tenant_id without core importing modules (PR-12 / DIP)."""

    async def __call__(self, username_or_email: str, db: AsyncSession) -> Any | None: ...


_token_version_lookup: Callable[[str], Awaitable[int | None]] | None = None
_user_tenant_lookup: Callable[[str, AsyncSession], Awaitable[Any | None]] | None = None


def register_token_version_lookup(lookup: Callable[[str], Awaitable[int | None]]) -> None:
    """Register the persistence provider for user token_version lookups (DIP seam)."""
    global _token_version_lookup
    _token_version_lookup = lookup


def register_user_tenant_lookup(lookup: Callable[[str, AsyncSession], Awaitable[Any | None]]) -> None:
    """Register the persistence provider for user tenant_id lookups (DIP seam)."""
    global _user_tenant_lookup
    _user_tenant_lookup = lookup


def reset_jwt_security_lookups() -> None:
    """Reset registered security lookups (test isolation seam).

    TEST-ONLY: guarded to non-production environments to prevent accidental
    unwiring in production (P0 fail-open fix).
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_jwt_security_lookups is test-only and blocked in production")
    global _token_version_lookup, _user_tenant_lookup
    _token_version_lookup = None
    _user_tenant_lookup = None


def ensure_jwt_lookups_registered() -> None:
    """Assert DIP seams are wired; call once at startup after imports (P0).

    Raises ServiceUnavailableError when either lookup is missing so the app
    fails fast instead of silently skipping token_version enforcement.
    """
    if _token_version_lookup is None or _user_tenant_lookup is None:
        from src.app.shared.exceptions import ServiceUnavailableError

        missing = []
        if _token_version_lookup is None:
            missing.append("token_version")
        if _user_tenant_lookup is None:
            missing.append("user_tenant")
        _jwt_logger.error("jwt_security_lookups_unwired", missing=missing)
        raise ServiceUnavailableError(f"JWT security lookups unwired: {', '.join(missing)}")


@lru_cache(maxsize=1)
def _get_signing_key() -> str:
    """Get the key for signing tokens (private key for RS256, secret for HS256)."""
    from src.app.core.security.jwt_utils import _is_symmetric_algorithm

    if not _is_symmetric_algorithm():
        return str(settings.crypt.JWT_PRIVATE_KEY.get_secret_value())  # type: ignore[union-attr]
    return str(settings.SECRET_KEY.get_secret_value())


def clear_signing_key_cache() -> None:
    """Clear signing key cache (call on key rotation). H3: prevents lru_cache staleness."""
    _get_signing_key.cache_clear()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/account/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Export settings constants for module-level access
ACCESS_TOKEN_EXPIRE_MINUTES = settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES
MFA_PENDING_EXPIRY_SECONDS = settings.auth.MFA_PENDING_EXPIRY_SECONDS


async def _jwt_encode(payload: dict, key: str, algorithm: str) -> str:
    """Encode JWT, inlining for symmetric algorithms."""
    from src.app.core.security.jwt_utils import _is_symmetric_algorithm

    if _is_symmetric_algorithm():
        return jwt.encode(payload, key, algorithm=algorithm)
    return await asyncio.to_thread(jwt.encode, payload, key, algorithm)


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create access token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.crypt.ACCESS_TOKEN_EXPIRE_MINUTES)

    # SECURITY FIX: Add JTI (JWT ID) for precise blacklist tracking
    # SECURITY FIX: Include token_version for session invalidation on password change
    token_version = to_encode.pop("token_version", 0)
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.ACCESS,
            "aud": settings.crypt.JWT_AUDIENCE,
            "iss": settings.crypt.JWT_ISSUER,
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "iat": datetime.now(UTC).timestamp(),  # Issued at
            "token_version": token_version,
        }
    )
    # A3: stable session id shared with the refresh token; copied from input data
    # when present (rotation), minted fresh at login. Kept separate from jti so
    # blacklist identity stays per-token while session identity stays per-login.
    if "sid" not in to_encode:
        to_encode["sid"] = str(uuid4())

    # Use _jwt_encode to inline symmetric algorithms or offload asymmetric algorithms
    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.crypt.ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create refresh token with JTI (JWT ID) for blacklist tracking.

    SECURITY FIX: Includes JTI (JWT ID) to enable precise blacklist checking
    without race conditions. Also includes absolute_exp for session lifetime limits.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.crypt.REFRESH_TOKEN_EXPIRE_DAYS)

    # A2: honor caller-provided sid/absolute_exp/iat (rotation carries them
    # forward); only mint fresh values for a brand-new session.
    # A3: same rule for sid — copied verbatim through rotation.
    token_version = to_encode.pop("token_version", 0)
    if "sid" not in to_encode:
        to_encode["sid"] = str(uuid4())
    if "absolute_exp" not in to_encode:
        absolute_exp_dt = datetime.now(UTC) + timedelta(days=settings.crypt.MAX_SESSION_DURATION_DAYS)
        to_encode["absolute_exp"] = absolute_exp_dt.timestamp()
    if "iat" not in to_encode:
        to_encode["iat"] = datetime.now(UTC).timestamp()
    to_encode.update(
        {
            "exp": expire,
            "token_type": TokenType.REFRESH,
            "aud": settings.crypt.JWT_AUDIENCE,
            "iss": settings.crypt.JWT_ISSUER,
            "jti": str(uuid4()),  # Unique token ID for blacklist
            "token_version": token_version,
        }
    )

    encoded_jwt: str = await _jwt_encode(to_encode, _get_signing_key(), settings.crypt.ALGORITHM)
    return encoded_jwt


async def _check_db_fallback(jti: str | None, token: str) -> bool | None:
    """Helper to perform DB fallback check for token blacklisting.

    SECURITY FIX: the singleflight task is shared across concurrent requests,
    so it must NOT run on the winner's request-scoped session (the winner's
    request may commit/close mid-task, leaving waiters with a closed session
    during exactly the Redis outage this path exists for). The lookup uses its
    own short-lived session; blacklist entries are global jti/token strings,
    so no tenant context is needed. (M-5: the dead ``db`` parameter is gone.)
    """
    try:
        cache_key = jti if jti else token
        # Fast path: positive cache (blacklisted=True only; negative not cached — H3)
        if cache_key in _db_fallback_cache:
            return _db_fallback_cache[cache_key]

        async with _db_fallback_lock:
            if cache_key in _db_fallback_cache:
                return _db_fallback_cache[cache_key]
            if cache_key in _in_flight_db_checks:
                task = _in_flight_db_checks[cache_key]
            else:

                async def _check_db() -> bool:
                    from src.app.core.db.database import local_session

                    async with local_session() as session:
                        _is_bl = False
                        if jti:
                            _is_bl = await crud_token_blacklist.exists(session, token=f"jti:{jti}")
                        if not _is_bl:
                            _is_bl = await crud_token_blacklist.exists(session, token=token)
                        return _is_bl

                task = asyncio.create_task(_check_db())
                _in_flight_db_checks[cache_key] = task

        try:
            is_blacklisted = await task
            # H3: only cache positive (True) to avoid poisoning revocation; negative stays uncached
            if is_blacklisted:
                _db_fallback_cache[cache_key] = True
            return is_blacklisted
        finally:
            # S-25 FIX: pop only if the registered task is STILL OURS. Every
            # waiter runs this finally, so an unconditional pop could remove a
            # NEWLY registered task belonging to an unrelated later request
            # (owner A completes -> pops -> C registers task_C -> A's late
            # waiter pops task_C). Correctness held — each task does its own DB
            # read — but the singleflight the P6/H3 guard was added to collapse
            # silently degraded to N concurrent SELECTs, worst precisely when
            # Redis is down and this path is the only revocation oracle.
            async with _db_fallback_lock:
                if _in_flight_db_checks.get(cache_key) is task:
                    del _in_flight_db_checks[cache_key]
    except Exception as e:
        _jwt_logger.error("DB fallback failed during token verification. Failing closed.", error=str(e))
        return None


async def _load_token_version(cache_key: str, username_or_email: str) -> int | None:
    """P6: one DB lookup for the user's token_version; backfills Redis (nx).

    Returns the stored token_version, or None when the user does not exist.
    Only the singleflight winner runs this; concurrent waiters await its task.

    SECURITY FIX: uses its own short-lived session instead of the winner's
    request session. The task is shared across concurrent requests; running it
    on the winner's request-scoped session meant waiters' awaits could execute
    on a session that was mid-commit, rolled back, or already closed
    (SessionClosedError / stale transaction state), rejecting valid tokens.

    P0 FIX (fail-closed): when the lookup is unwired, raise instead of
    returning None so verify_token rejects the token explicitly rather than
    silently skipping version enforcement.
    """
    if _token_version_lookup is None:
        from src.app.shared.exceptions import ServiceUnavailableError

        _jwt_logger.error("token_version_lookup_unwired", user_ref=subject_log_ref(username_or_email))
        raise ServiceUnavailableError("Token version service unavailable")
    version = await _token_version_lookup(username_or_email)
    if version is None:
        return None
    try:
        from src.app.core.cache.client import cache_client

        await cache_client.set_nx(cache_key, str(version), ttl=300)
    except Exception as e:
        _jwt_logger.warning(
            "token_version_cache_write_error", error=str(e), user_ref=subject_log_ref(username_or_email)
        )
    return version


async def is_token_revoked(token: str, jti: str | None, db: AsyncSession) -> bool:
    """Single revocation oracle — the ONE place that decides fail-open/fail-closed.

    Returns True when the token must be treated as revoked:

    - Redis blacklist says True  -> revoked.
    - Redis blacklist says False -> not revoked.
    - Redis unavailable (None)   -> the DB is authoritative via the
      thundering-herd-protected fallback; a DB error fails CLOSED (revoked)
      so a possibly-revoked token is never accepted during an outage.

    Every caller (``verify_token``, the auth dependencies) routes through this
    function so the failure semantics live in one module instead of leaking
    across each seam.
    """
    cache_result = await is_token_blacklisted(token, jti=jti)
    if cache_result is True:
        return True
    if cache_result is False:
        return False
    # Redis unavailable — fall back to the DB (authoritative).
    _jwt_logger.warning(
        "Redis unavailable for token blacklist check, falling back to DB. Restore Redis ASAP for optimal security."
    )
    db_result = await _check_db_fallback(jti, token)
    # Fail closed: an error (None) or a hit (True) both mean revoked.
    return db_result is None or db_result is True


async def verify_token(token: str, expected_token_type: TokenType, db: AsyncSession) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    SECURITY FIX: Eliminates race condition by decoding token FIRST (local operation),
    then checking blacklist using JTI (JWT ID). This ensures blacklist check happens
    atomically relative to the token content.
    """
    from src.app.core.security.jwt_utils import decode_verified_token

    payload = await decode_verified_token(token)
    if payload is None:
        return None

    username_or_email: str | None = payload.get("sub")
    token_type: str | None = payload.get("token_type")
    tier_id: int | None = payload.get("tier_id")
    jti: str | None = payload.get("jti")

    if username_or_email is None or token_type != expected_token_type:
        return None

    if await is_token_revoked(token, jti, db):
        return None

    token_version: int | None = payload.get("token_version")
    if token_version is not None:
        tenant_id = payload.get("tenant_id")
        from src.app.core.cache.client import cache_client
        from src.app.core.utils.cache import build_tenant_key

        # Qwen-F1: normalize the subject exactly like session_invalidation
        # (username.lower().strip()) — legacy mixed-case DB rows authenticate
        # with a verbatim `sub`, but invalidation writes the lowercased key.
        # Without this, a stale version survives ≤300 s after password change.
        normalized_subject = username_or_email.lower().strip()
        cache_key = build_tenant_key(f"user:{normalized_subject}:token_version", tenant_id)

        cached_version = None
        try:
            cached_value = await cache_client.get_strict(cache_key)
            if cached_value is not None:
                cached_version = int(cached_value)
        except Exception as e:
            _jwt_logger.warning(
                "token_version_cache_read_error", error=str(e), user_ref=subject_log_ref(username_or_email)
            )

        if cached_version is not None:
            if cached_version != token_version:
                return None
        else:
            # P6: singleflight — concurrent Redis misses for the same user
            # share one DB lookup instead of stampeding SELECT users.
            async with _token_version_lock:
                task = _in_flight_token_version.get(cache_key)
                if task is None:
                    task = asyncio.create_task(_load_token_version(cache_key, username_or_email))
                    _in_flight_token_version[cache_key] = task
            try:
                db_version = await task
            except Exception as e:
                # Fail closed: an unloadable token_version rejects the token.
                _jwt_logger.error(
                    "token_version lookup failed, rejecting", error=str(e), user_ref=subject_log_ref(username_or_email)
                )
                return None
            finally:
                # S-25 FIX: see the sibling `_check_db_fallback` — pop only the
                # task this coroutine actually awaited.
                async with _token_version_lock:
                    if _in_flight_token_version.get(cache_key) is task:
                        del _in_flight_token_version[cache_key]
            if db_version != token_version:
                return None

    absolute_exp: float | None = payload.get("absolute_exp")
    if absolute_exp is not None and absolute_exp < datetime.now(UTC).timestamp():
        _jwt_logger.info("Token absolute expiration reached")
        return None

    return TokenData(
        username_or_email=username_or_email,
        jti=jti,
        tier_id=tier_id,
        absolute_exp=absolute_exp,
        exp=payload.get("exp"),
        token_version=token_version or 0,
        sid=payload.get("sid") or jti,  # A3: fall back to jti for pre-sid tokens
        iat=payload.get("iat"),  # A2: original login iat
    )


async def _resolve_tenant_id_for_token(payload: dict[str, Any], db: AsyncSession) -> Any | None:
    """Helper to resolve tenant_id for a token from payload, context, or DB."""
    tenant_id = payload.get("tenant_id")
    if tenant_id:
        return tenant_id

    from src.app.core.tenancy.context import get_current_tenant

    tenant = get_current_tenant()
    if tenant:
        return tenant.id

    sub = payload.get("sub")
    if sub and _user_tenant_lookup is not None:
        return await _user_tenant_lookup(sub, db)

    if sub:
        _jwt_logger.warning("user_tenant_lookup_unwired", user_ref=subject_log_ref(sub))
    return None


#: Postgres SQLSTATE for a unique-violation. The duplicate-token case the
#: IntegrityError handler exists for is a 23505 on
#: ``ix_token_blacklist_token``; an FK (23503), not-null (23502) or check
#: (23514) violation on the same flush is a genuine fault wearing the same
#: exception class.
_UNIQUE_VIOLATION_SQLSTATE = "23505"

#: The message fragment a driver emits for a unique violation when it exposes no
#: SQLSTATE. sqlite3 has none, so this is the only signal available there. It is
#: deliberately narrow: SQLite's other two constraint failures read "FOREIGN KEY
#: constraint failed" and "NOT NULL constraint failed", so neither can match.
_UNIQUE_VIOLATION_WITHOUT_SQLSTATE = "UNIQUE constraint failed"


def _is_duplicate_blacklist_violation(exc: IntegrityError) -> bool:
    """True only when the flush failed because the blacklist row already exists.

    ``IntegrityError`` is the SQLAlchemy base for EVERY constraint violation,
    so catching it wholesale cannot tell the idempotent case from an FK or
    not-null fault. Measured (bead FastAPI_saas-68yg): a production-shaped
    pair whose claims named a tenant absent from the database failed with
    ``got 0 rows`` and the log "Token already blacklisted in DB", because an
    FK violation rolled back the savepoint and the handler called it a
    duplicate. ``token_blacklist`` has exactly one unique index
    (``ix_token_blacklist_token``, ``02_authentication.py``), so a unique
    violation here is unambiguously the duplicate case.

    DIALECT PORTABILITY, and why the SQLSTATE alone was not enough. The first
    version of this predicate read ``orig.sqlstate`` and nothing else, which is
    a PostgreSQL-only signal: asyncpg sets it, and 23505 is the unique-violation
    code. ``sqlite3`` has no SQLSTATE at all, so on SQLite the predicate
    returned False for a genuine duplicate, the handler classified it as a
    real fault, and a REPEAT LOGOUT RAISED instead of being idempotent. That
    regression shipped and was caught by the S-2 oracle
    (``tests/performance/test_rollback_verification.py``), whose eight failures
    all carried the log line this branch emits. It survived my own tests
    because they ran on Postgres while the oracle ran on SQLite -- a guard
    exercised on one dialect says nothing about the other.

    So the SQLSTATE is used when the driver supplies one, and only then. Where
    it is absent, the driver names the violation in the message, and the only
    message naming a constraint is the unique one: SQLite says "UNIQUE
    constraint failed" and "FOREIGN KEY constraint failed" / "NOT NULL
    constraint failed" for the other two, so this marker cannot match them.
    """
    original = getattr(exc, "orig", None)
    if original is None:
        return False
    # Annotated, not inferred: getattr returns Any, and comparing two Any
    # values yields Any, which cannot be returned from a function declared to
    # return bool without tripping mypy's no-any-return.
    sqlstate: str | None = getattr(original, "sqlstate", None)
    if sqlstate is not None:
        return sqlstate == _UNIQUE_VIOLATION_SQLSTATE
    return _UNIQUE_VIOLATION_WITHOUT_SQLSTATE in str(original)


async def blacklist_tokens(
    access_token: str | None,
    refresh_token: str | None,
    db: AsyncSession,
    access_payload: dict[str, Any] | None = None,
    refresh_payload: dict[str, Any] | None = None,
) -> None:
    """Blacklist both access and refresh tokens."""
    tokens_for_cache: list[tuple[str, datetime]] = []
    db_rows_staged = False
    tokens_and_payloads = [
        (access_token, access_payload),
        (refresh_token, refresh_payload),
    ]

    for token, payload in tokens_and_payloads:
        if not token:
            continue
        from src.app.core.security.jwt_utils import decode_verified_token

        try:
            if not payload:
                payload = await decode_verified_token(token)
            if payload is None:
                continue
            exp_timestamp = payload.get("exp")
            jti = payload.get("jti")
            tenant_id = payload.get("tenant_id")

            if exp_timestamp is not None:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                try:
                    if not tenant_id:
                        tenant_id = await _resolve_tenant_id_for_token(payload, db)
                        if not tenant_id:
                            _jwt_logger.warning("Could not determine tenant_id for token blacklisting")
                            # P0 FIX (fail-closed): still blacklist in Redis even without
                            # tenant_id so the token is revoked on the hot path.
                            if jti:
                                jti_key = f"jti:{jti}"
                                tokens_for_cache.append((jti_key, expires_at))
                            else:
                                tokens_for_cache.append((token, expires_at))
                            continue

                    if isinstance(tenant_id, str):
                        tenant_id = UUID(tenant_id)

                    db.add(TokenBlacklist(token=token, expires_at=expires_at, tenant_id=tenant_id))
                    db_rows_staged = True

                    if jti:
                        jti_key = f"jti:{jti}"
                        db.add(TokenBlacklist(token=jti_key, expires_at=expires_at, tenant_id=tenant_id))
                        cache_key = jti_key
                    else:
                        cache_key = token
                    tokens_for_cache.append((cache_key, expires_at))
                except Exception as e:
                    _jwt_logger.warning("Error preparing token for blacklist", error=str(e))
        except JWTError as e:
            _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))

    if db_rows_staged:
        try:
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError as exc:
                if _is_duplicate_blacklist_violation(exc):
                    # No outer rollback here, and the absence is deliberate.
                    # The savepoint opened by `async with db.begin_nested()`
                    # above rolls ITSELF back on the IntegrityError, so the
                    # staged rows are already undone. A further db.rollback()
                    # unwinds the CALLER's whole transaction and expires its
                    # identity map; the commit below then commits an empty
                    # transaction. Bead C-32 (FastAPI_saas-m3r3) enumerated both
                    # production callers -- logout and refresh rotation -- and
                    # found neither holds pending work at this point, so the
                    # old rollback was correct only by the accident that both
                    # call sites happen to be clean, never by construction.
                    #
                    # What it did cost, measured: a repeat logout is a no-op by
                    # contract, and this line made it unwind the surrounding
                    # transaction to get there. tests/performance/
                    # test_rollback_verification.py counts bare ROLLBACKs
                    # against a fixed ceiling for exactly this reason.
                    _jwt_logger.info("Token already blacklisted in DB (IntegrityError during flush)")
                else:
                    # A constraint failure that is NOT the duplicate index. This
                    # used to be swallowed as idempotency: the savepoint
                    # rollback discarded the staged rows, the commit below had
                    # nothing to commit, and the token was never persisted
                    # while the caller was told the logout succeeded. Redis
                    # still records it, so revocation works, but the missing DB
                    # row is a real fault that must surface rather than look
                    # like a repeat logout.
                    await db.rollback()
                    _jwt_logger.error(
                        "DB blacklist flush hit a constraint that is not the duplicate-token index",
                        error=str(exc),
                    )
                    raise

            try:
                await db.commit()
            except Exception as e:
                await db.rollback()
                # A4 FIX: previously this early-returned BEFORE the Redis write,
                # leaving the token blacklisted NOWHERE while the user believed
                # they were logged out. Fall through so the cache blacklist (the
                # hot-path check) still records it.
                _jwt_logger.error(
                    "Failed to commit blacklisted tokens to DB; still blacklisting in cache",
                    error=str(e),
                )
        finally:
            # The Redis write is the hot-path revocation oracle, so it runs on
            # every path out of this block — including the re-raise above.
            # Written here rather than after the block precisely so a
            # constraint failure cannot skip it.
            if tokens_for_cache:
                await add_tokens_to_blacklist(tokens_for_cache)
    elif tokens_for_cache:
        await add_tokens_to_blacklist(tokens_for_cache)


async def blacklist_token(token: str, db: AsyncSession, payload: dict[str, Any] | None = None) -> None:
    """Blacklist a single token after validating its signature."""
    try:
        if not payload:
            from src.app.core.security.jwt_utils import decode_verified_token

            payload = await decode_verified_token(token)
        if payload is None:
            return
        exp_timestamp = payload.get("exp")
        jti = payload.get("jti")
        tenant_id = payload.get("tenant_id")

        if exp_timestamp is not None:
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)
            cache_key = f"jti:{jti}" if jti else token
            try:
                if not tenant_id:
                    tenant_id = await _resolve_tenant_id_for_token(payload, db)
                if tenant_id:
                    if isinstance(tenant_id, str):
                        tenant_id = UUID(tenant_id)

                    # PERF-R1-B: persist ONE row inside ONE transaction.
                    #
                    # This used to write two rows — the raw JWT string and
                    # ``jti:{jti}`` — each through its own
                    # crud_token_blacklist.create() call, and create() commits
                    # internally (it must NOT be wrapped in begin_nested(): the
                    # block closes the nested transaction on exit and the
                    # db.refresh() inside create() then raises
                    # InvalidRequestError on the closed transaction under load).
                    # So every refresh paid two remote COMMITs (WAL fsync on the
                    # Postgres VM) plus two post-commit SELECTs from
                    # db.refresh(). Measured 2026-09-22 with
                    # scripts/profile_refresh.py: 6.41 ms of a 15.45 ms request.
                    #
                    # The second row is redundancy, not defence in depth:
                    #   * hot path — is_token_blacklisted() resolves the member
                    #     via _extract_blacklist_member(token, jti), which keys
                    #     on ``jti:{jti}`` whenever a jti claim exists; Redis is
                    #     the primary revocation oracle.
                    #   * DB fallback — _check_db_fallback(jti, token) probes
                    #     ``token = 'jti:{jti}'`` FIRST, so with a jti present
                    #     the jti row is the row that can ever match anyway.
                    # A jti-less (legacy) token still gets its raw-token row,
                    # which is exactly the member that path looks up.
                    #
                    # db.add()+commit() instead of create(): the inserted row is
                    # never read back, so the refresh SELECT create() issues is
                    # pure overhead (expire_on_commit=False means the instance
                    # stays usable regardless).
                    db.add(
                        TokenBlacklist(
                            token=cache_key, expires_at=expires_at, tenant_id=tenant_id
                        )
                    )
                    await db.commit()
                else:
                    # Fail-closed: still blacklist in Redis even without tenant_id
                    _jwt_logger.warning(
                        "Could not determine tenant_id for token blacklisting, using Redis fallback"
                    )
            except IntegrityError as exc:
                # C-2: AsyncSession.rollback() is always awaitable.
                if _is_duplicate_blacklist_violation(exc):
                    await db.rollback()
                    _jwt_logger.info("Token already blacklisted in DB (IntegrityError)")
                else:
                    # Same narrow as blacklist_tokens: an FK or not-null
                    # violation is not a repeat logout.
                    await db.rollback()
                    _jwt_logger.error(
                        "DB blacklist insert hit a constraint that is not the duplicate-token index",
                        error=str(exc),
                    )
                    raise
            except Exception as e:
                # S-24 FIX: the inner try caught ONLY IntegrityError, so any
                # other DB fault (OperationalError, TimeoutError, a driver
                # error) propagated straight past the Redis write below. That
                # contradicted the "runs on EVERY path" comment on the next
                # statement and, worse, meant a logout/refresh could leave the
                # token blacklisted NOWHERE while the caller saw an error.
                #
                # `blacklist_tokens` (plural) already got this right by logging
                # the commit failure and falling through to the Redis write;
                # mirror that here. The Redis write is the hot-path oracle, so
                # losing it is what actually breaks revocation.
                await db.rollback()
                _jwt_logger.error(
                    "DB blacklist insert failed; still writing the Redis revocation",
                    error=str(e),
                )
            # B1: the Redis cache write runs on EVERY path (fresh insert,
            # IntegrityError retry, tenant-less fallback) so the hot-path
            # check always sees the revocation, and it is LOUD —
            # add_token_to_blacklist raises ServiceUnavailableError on write
            # failure so logout/refresh surface 5xx instead of silently
            # leaving a "revoked" token valid for up to its natural expiry.
            await add_token_to_blacklist(cache_key, expires_at)
    except JWTError as e:
        _jwt_logger.warning("Skipping blacklist for invalid token", error=str(e))


def get_mfa_pending_token_manager(redis: Redis) -> PendingTokenStore:
    """Get PendingTokenStore configured for MFA pending tokens."""
    return PendingTokenStore(
        redis=redis, prefix="mfa_pending", ttl_seconds=settings.auth.MFA_PENDING_EXPIRY_SECONDS, use_hash=True
    )
