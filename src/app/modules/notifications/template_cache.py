"""Redis cache for tenant email-template content.

Owns one key family: ``build_tenant_key("email_template:<name>", tenant_id)``
and nothing else. The four call sites that used to build that key by hand
(``NotificationService.get_template_content`` read + write, and the two
template-override write paths' invalidation) go through here, so the key
grammar, the TTL and the fail-soft read policy live in one place.
"""

from uuid import UUID

from structlog import get_logger

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.redis_provider import get_redis
from src.app.core.utils.cache import build_tenant_key

logger = get_logger(__name__)

# 1 hour. The cache is fail-soft in both directions: a Redis miss or error
# falls through to the database/file lookup, and a failed write is logged and
# dropped (the next read just re-resolves the content).
TEMPLATE_CACHE_TTL_SECONDS: int = 3600


def template_cache_key(tenant_id: UUID | None, template_name: str) -> str:
    """The one key grammar for this family."""
    return build_tenant_key(f"email_template:{template_name}", tenant_id)


async def get_cached_template(tenant_id: UUID | None, template_name: str) -> tuple[str, str] | None:
    """Read cached (subject, html_content), or None on miss/unavailable Redis."""
    redis_client = get_redis()
    if not redis_client:
        return None

    try:
        cached = await redis_client.get(template_cache_key(tenant_id, template_name))
        if cached:
            return tuple(json_loads(cached))
    except Exception as e:
        logger.debug("Cache get failed", error=e)
    return None


async def store_cached_template(
    tenant_id: UUID | None, template_name: str, data: tuple[str, str]
) -> None:
    """Write (subject, html_content) to the cache; a failure is not fatal."""
    redis_client = get_redis()
    if not redis_client:
        return

    try:
        await redis_client.set(
            template_cache_key(tenant_id, template_name),
            json_dumps(data, as_str=True),
            ex=TEMPLATE_CACHE_TTL_SECONDS,
        )
    except Exception as e:
        logger.debug("Cache set failed", error=e)


async def invalidate_template_cache(tenant_id: UUID | None, template_name: str) -> None:
    """Drop the cached content for one template (override written or deleted)."""
    redis_client = get_redis()
    if not redis_client:
        return
    await redis_client.delete(template_cache_key(tenant_id, template_name))
