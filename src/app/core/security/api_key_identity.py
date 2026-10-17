"""API-key identity: who a presented key belongs to, and whether it still grants.

ADR-0023 (``FastAPI_saas-hfps``). Admission runs before any auth dependency
(``core/guard/ordering.py`` places RateLimit ahead of routing), so at the moment
the rate limiter picks a budget, nothing has validated the ``X-API-Key`` the
caller presented. ``FastAPI_saas-g4my`` closed that hole by giving keyed traffic
its own named budget, ``settings.rate_limit.API_KEY_LIMIT``, keyed by SOURCE IP
- correct, because a presented key is unverified at the edge and keying on it
would let a brute-force attempt mint an unbounded number of buckets.

That left the owner's tier invisible. This module is the answer: the identity is
already cached, and **every** API-key request already reads it. So the cache-key
FORMAT and the grant rules live here, in ``core``, and are resolved once per
request by admission, which binds the result on ``request.state`` for the
authentication path to consume.

Three things are deliberately owned here and nowhere else:

* **the cache-key format** (``api_key_cache_key``). ``api_keys/service.py``
  re-exports ``API_KEY_CACHE_PREFIX`` / ``API_KEY_CACHE_TTL`` from this module
  rather than defining them again, so a rename cannot leave one writer behind -
  S7 (a truncated digest) and the S-01 orphan-entry fix both depend on this name
  being computed the same way by every caller.
* **the grant rules** (``read_cached_grant``). "Is this cached payload still a
  grant?" has three answers, and the service needs the REASON, not a boolean:
  a revoked or expired entry is deleted on the spot, a not-yet-valid one is
  only refused. Two implementations of this rule would eventually disagree.
* **the resolution itself** (``resolve_api_key_identity``), which is a cache
  read and nothing else. It never touches the database: a miss is a miss, and
  the authoritative decision stays with
  ``APIKeyService.validate_api_key``, which still runs on the request's own
  terms (including the tenant-liveness probe) and still owns minting, revoking
  and rotation.

What this module does NOT do, on purpose: it does not validate the key. It
answers "the cache says this key belongs to that owner, and the cache entry is
still a grant". A caller that gets an identity from here has spent one Redis GET
on an entry every API-key request was already paying for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from src.app.core.cache.client import cache_client
from src.app.core.json_utils import json_loads
from src.app.core.security.tokens import token_digest_candidates

# The cache key prefix and TTL are MOVED here, not copied. The service
# re-exports both names so that every existing importer keeps working and there
# is exactly one definition of the format.
API_KEY_CACHE_PREFIX = "api_key"
API_KEY_CACHE_TTL = 300  # 5 minutes

#: Why a cached payload is not a grant. ``""`` means it IS one.
Refusal = Literal["", "revoked", "expired", "not_yet_valid"]

__all__ = [
    "API_KEY_CACHE_PREFIX",
    "API_KEY_CACHE_TTL",
    "ApiKeyIdentity",
    "CachedGrant",
    "Refusal",
    "api_key_cache_key",
    "read_cached_grant",
    "resolve_api_key_identity",
]


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    """The owner of a presented API key, as far as the cache knows.

    ``tier_id`` / ``tier_name`` are ``None`` for a cache entry written before
    ADR-0023, and for an owner with no tier. That is NOT the same as an absent
    identity: the key is valid either way. The distinction matters to admission,
    which meters an untiered identity on the per-source-IP budget rather than
    through ``resolve_rate_limits``' authenticated fallback (50/hour) - a 20x
    tightening presented as a feature.
    """

    user_id: int
    tenant_id: Any
    key_id: int
    scopes: list[str]
    tier_id: int | None
    tier_name: str | None

    def as_key_info(self) -> dict[str, Any]:
        """The shape ``APIKeyService.validate_api_key`` returns."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "scopes": self.scopes,
            "key_id": self.key_id,
        }


class CachedGrant(NamedTuple):
    """A cached payload read as either an identity or a refusal reason."""

    identity: ApiKeyIdentity | None
    refusal: Refusal


def api_key_cache_key(api_key_hash: str) -> str:
    """The Redis name of a key-digest entry.

    The FULL digest, never a prefix of it (S7): a truncated digest is
    forgeable, and a truncated key name would make two distinct keys share one
    entry.
    """
    return f"{API_KEY_CACHE_PREFIX}:{api_key_hash}"


def read_cached_grant(payload: dict[str, Any]) -> CachedGrant:
    """Read a cached API-key payload as an identity, or refuse with a reason.

    The three refusals are not interchangeable, because the caller acts on them
    differently:

    * ``revoked`` - the entry is a tombstone written by ``revoke_api_key``. The
      caller deletes it and refuses. This branch is the whole point of the
      tombstone: the only writer of a live grant hard-codes ``is_active: True``,
      so a deleted entry would keep authenticating for a full TTL.
    * ``expired`` - same, delete and refuse.
    * ``not_yet_valid`` - a rotation grace period. Refuse WITHOUT deleting: the
      entry becomes a grant on its own, and deleting it would only buy a
      database read.

    A payload with no ``is_active`` at all - written by a deploy older than the
    tombstone field - grants. Every such entry carries the TTL, so the exposure
    is bounded by one TTL from deploy; refusing would lock out every key in
    flight at deploy time.

    A payload with no tier grants too, carrying ``tier_id``/``tier_name`` as
    ``None``. See :class:`ApiKeyIdentity`.
    """
    if payload.get("is_active") is False:
        return CachedGrant(None, "revoked")

    expires_at = payload.get("expires_at")
    if expires_at:
        if datetime.fromisoformat(expires_at) < datetime.now(UTC):
            return CachedGrant(None, "expired")

    valid_from = payload.get("valid_from")
    if valid_from and datetime.fromisoformat(valid_from) > datetime.now(UTC):
        return CachedGrant(None, "not_yet_valid")

    return CachedGrant(
        ApiKeyIdentity(
            user_id=payload["user_id"],
            tenant_id=payload["tenant_id"],
            key_id=payload["key_id"],
            scopes=payload.get("scopes") or [],
            tier_id=payload.get("tier_id"),
            tier_name=payload.get("tier_name"),
        ),
        "",
    )


async def resolve_api_key_identity(api_key: str) -> ApiKeyIdentity | None:
    """Resolve a presented key to its owner from the cache, or ``None``.

    Probes BOTH digest candidates in the order
    :func:`src.app.core.security.tokens.token_digest_candidates` returns them:
    the current peppered HMAC first, then the pre-I2 bare SHA-256 that keys
    minted before that change still hash to. A refusal on the first candidate is
    final - the second is the same key read under an older digest, so honouring
    one and ignoring the other would make a revoke depend on which digest hit.

    Costs one Redis GET on a warm entry. This is not an extra cost: the
    authentication path reads the same entry on the same request, and consumes
    this result instead (ADR-0023).
    """
    current_digest, legacy_digest = token_digest_candidates(api_key)
    for digest in (current_digest, legacy_digest):
        cached = await cache_client.get(api_key_cache_key(digest))
        if cached is None:
            continue
        grant = read_cached_grant(json_loads(cached))
        if grant.identity is not None:
            return grant.identity
        return None
    return None
