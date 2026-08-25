"""Path sanitization and formatting utilities."""

import hashlib
import re


def sanitize_path(path: str, max_len: int = 64) -> str:
    """Sanitize and normalize an endpoint path for rate limiting or metrics tracking.

    Strips API version prefixes (e.g., api/v1/), collapses UUIDs/integers to {},
    and replaces slashes with underscores.

    R3 FIX: any residual non-UUID/numeric segment is user-controlled; an
    unbounded value let attackers rotate suffixes to mint fresh rate-limit
    buckets and grow Redis keys without limit. Results are now capped with a
    deterministic digest so distinct overlong paths stay distinguishable while
    cardinality stays bounded.
    """
    normalized = path.strip("/")
    # Strip API version prefix (e.g., api/v1/)
    normalized = re.sub(r"^api/v\d+/", "", normalized)
    # Collapse numeric/UUID path segments to {}
    normalized = re.sub(r"/[0-9a-fA-F-]{36}|/\d+", "/{}", normalized)
    normalized = normalized.replace("/", "_")
    if len(normalized) > max_len:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        normalized = f"{normalized[: max_len - 17]}~{digest}"
    return normalized
