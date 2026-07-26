"""Redis-backed runtime configuration store for security settings."""
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils import cache

logger = get_logger(__name__)

RUNTIME_CONFIG_KEY = "runtime_config:security"
RUNTIME_CONFIG_TTL = 86400 * 30  # 30 days

async def get_runtime_config() -> dict | None:
    if cache.client is None:
        return None
    try:
        data = await cache.client.get(RUNTIME_CONFIG_KEY)
        return json_loads(data) if data else None
    except Exception as e:
        logger.warning("Failed to read runtime config", error=str(e))
        return None

async def set_runtime_config(config: dict) -> bool:
    if cache.client is None:
        return False
    try:
        await cache.client.setex(
            RUNTIME_CONFIG_KEY,
            RUNTIME_CONFIG_TTL,
            json_dumps(config, as_str=True),
        )
        return True
    except Exception as e:
        logger.error("Failed to persist runtime config", error=str(e))
        return False

async def update_runtime_config(updates: dict) -> dict | None:
    """Atomically merge updates into the runtime config using a lock."""
    if cache.client is None:
        return None
    try:
        async with cache.client.lock("lock:runtime_config", timeout=10):
            current = await get_runtime_config() or {}
            current.update(updates)
            await set_runtime_config(current)
            return current
    except Exception as e:
        logger.error("Failed to update runtime config", error=str(e))
        return None
