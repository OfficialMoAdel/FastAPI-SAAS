"""Path sanitization and formatting utilities."""

import re


def sanitize_path(path: str) -> str:
    """Sanitize and normalize an endpoint path for rate limiting or metrics tracking.

    Strips API version prefixes (e.g., api/v1/), collapses UUIDs/integers to {},
    and replaces slashes with underscores.
    """
    normalized = path.strip("/")
    # Strip API version prefix (e.g., api/v1/)
    normalized = re.sub(r"^api/v\d+/", "", normalized)
    # Collapse numeric/UUID path segments to {}
    normalized = re.sub(r"/[0-9a-fA-F-]{36}|/\d+", "/{}", normalized)
    return normalized.replace("/", "_")
