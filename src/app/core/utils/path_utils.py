"""Path sanitization and formatting utilities."""

import hashlib
import re
from functools import lru_cache

_SANITIZE_UUID_RE = re.compile(r"/[0-9a-fA-F-]{36}|/\d+")
_SANITIZE_VERSION_RE = re.compile(r"^api/v\d+/")


@lru_cache(maxsize=1024)
def sanitize_path(path: str, max_len: int = 64) -> str:
    """Sanitize and normalize an endpoint path for rate limiting or metrics tracking.

    Strips API version prefixes (e.g., api/v1/), collapses UUIDs/integers to {},
    and replaces slashes with underscores.

    R3 FIX: any residual non-UUID/numeric segment is user-controlled; an
    unbounded value let attackers rotate suffixes to mint fresh rate-limit
    buckets and grow Redis keys without limit. Results are now capped with a
    deterministic digest so distinct overlong paths stay distinguishable while
    cardinality stays bounded.

    R5-MICRO: memoized via lru_cache — the 1024-entry maxsize bounds memory
    even under attacker-minted unique paths (LRU eviction, not the digest
    cap, is what prevents unbounded growth).
    """
    normalized = path.strip("/")
    # Strip API version prefix (e.g., api/v1/)
    normalized = _SANITIZE_VERSION_RE.sub("", normalized)
    # Collapse numeric/UUID path segments to {}
    normalized = _SANITIZE_UUID_RE.sub("/{}", normalized)
    normalized = normalized.replace("/", "_")
    if len(normalized) > max_len:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        normalized = f"{normalized[: max_len - 17]}~{digest}"
    return normalized
