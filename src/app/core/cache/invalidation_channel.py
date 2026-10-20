"""Reusable cross-worker L1 cache invalidation channel.

Implements ADR-0004 cross-worker L1 invalidation over Redis pub/sub with:
- background listener task lifecycle (start, stop)
- message decoding (utf-8, string or bytes)
- leak guard: guaranteed unsubscribe and pubsub close on exit/cancellation (M-3 fix)
- publisher helper
"""

import asyncio
from collections.abc import Callable, Sequence

from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis

logger = get_logger(__name__)


class InvalidationChannel:
    """Manages a Redis pub/sub channel for cross-process L1 cache invalidation."""

    def __init__(
        self,
        channel: str,
        on_message: Callable[[str], None],
        logger_name: str | None = None,
    ) -> None:
        self.channel = channel
        self.on_message = on_message
        self._logger = get_logger(logger_name or __name__)
        self._task: asyncio.Task[None] | None = None

    async def publish(self, payload: str | None = None) -> None:
        """Publish an invalidation message to the channel."""
        redis_client = get_redis()
        if redis_client is None:
            return
        try:
            msg = payload if payload is not None else "*"
            await redis_client.publish(self.channel, msg)
        except Exception as e:
            self._logger.warning("l1_invalidation_publish_failed", channel=self.channel, error=str(e))

    async def publish_many(self, payloads: Sequence[str]) -> None:
        """Publish a BATCH of invalidation messages in ONE round trip.

        The batch is a single Redis pipeline, so the whole fan-out costs one
        round trip instead of one per payload. The ORDER the payloads are
        given in is the order the subscribers receive them: Redis executes a
        pipeline's commands in submission order on one connection, so the
        per-subscriber FIFO a serial fan-out had is preserved.

        The trailing ``execute()`` MUST stay INSIDE the ``async with``:
        redis-py 5.3.1's ``Pipeline.__aexit__`` awaits ``reset()``, which
        clears the command stack, so a dedented ``execute()`` would send an
        EMPTY batch and silently invalidate nothing.

        A failure is logged as a BATCH — one warning naming the batch rather
        than one per payload. That is coarser logging, not a behaviour
        change, and it is the delta this seam's batch form was accepted with.
        """
        if not payloads:
            return
        redis_client = get_redis()
        if redis_client is None:
            return
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                for payload in payloads:
                    pipe.publish(self.channel, payload)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - deliberate: a publish failure must not fail the request
            self._logger.warning(
                "l1_invalidation_publish_failed",
                channel=self.channel,
                error=str(e),
                payload_count=len(payloads),
            )

    def start(self) -> None:
        """Start the background subscriber listener (idempotent)."""
        redis_client = get_redis()
        if redis_client is None or not hasattr(redis_client, "pubsub"):
            self._logger.warning(
                "l1_invalidation_listener_not_started", channel=self.channel, reason="redis_unavailable"
            )
            return
        if self._task is not None and not self._task.done():
            return

        async def _listen() -> None:
            pubsub = redis_client.pubsub()
            try:
                await pubsub.subscribe(self.channel)
                async for msg in pubsub.listen():
                    if not isinstance(msg, dict) or msg.get("type") != "message":
                        continue
                    data = msg.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="ignore")
                    if data is not None:
                        self.on_message(str(data))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._logger.warning("l1_invalidation_listener_exited", channel=self.channel, error=str(e))
            finally:
                # Guaranteed cleanup: unsubscribe and close connection to prevent leaks
                try:
                    await pubsub.unsubscribe(self.channel)
                    await pubsub.close()
                except Exception:
                    pass

        self._task = asyncio.create_task(_listen())

    async def stop(self) -> None:
        """Stop and cancel the background subscriber listener (idempotent, awaitable)."""
        if self._task is None or self._task.done():
            self._task = None
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            # M4: never swallow cancellation — the caller may itself be in a
            # cancelled scope, and masking it breaks propagation (plus it hid
            # real shutdown hangs behind a clean return).
            raise
        except Exception as exc:
            # H2: log-and-continue — a non-cancellation error while joining
            # the listener (e.g. an exception the task itself raised) must
            # not fail shutdown, but must not vanish either.
            self._logger.debug("invalidation listener join failed", error=str(exc))
        self._task = None


__all__ = ["InvalidationChannel"]
