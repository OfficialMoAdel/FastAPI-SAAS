"""R2-17: one background-task failure produces exactly one log line.

``create_safe_task`` already installs ``_handle_task_result``, which logs an
unhandled exception. ``users/service.py`` added a SECOND done-callback purely to
attach ``action="user.created"``, so every failed ``user.created`` publish was
logged twice — once without context and once with it. The context is now a
parameter of the single handler.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from src.app.core.utils import background_task as bt


@pytest.mark.asyncio
async def test_a_failing_task_is_logged_exactly_once_with_its_context() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def boom() -> None:
        raise RuntimeError("publish failed")

    with patch.object(bt.logger, "error", side_effect=lambda event, **kw: calls.append((event, kw))):
        task = bt.create_safe_task(boom(), name="user.created", log_context={"action": "user.created"})
        with pytest.raises(RuntimeError, match="publish failed"):
            await task
        # Done-callbacks are scheduled with call_soon; let them run.
        await asyncio.sleep(0)

    assert len(calls) == 1, f"a failure must be logged once, got {len(calls)}: {calls}"
    event, fields = calls[0]
    assert event == "Unhandled exception in background task"
    assert fields["action"] == "user.created", "the caller's context must survive"
    assert fields["task_name"] == "user.created"
    assert fields["exc_info"] is True


@pytest.mark.asyncio
async def test_a_successful_task_logs_nothing() -> None:
    calls: list[str] = []

    async def fine() -> None:
        return None

    with patch.object(bt.logger, "error", side_effect=lambda event, **kw: calls.append(event)):
        task = bt.create_safe_task(fine(), log_context={"action": "user.created"})
        await task
        await asyncio.sleep(0)

    assert calls == []


@pytest.mark.asyncio
async def test_a_cancelled_task_logs_nothing() -> None:
    """Cancellation is not a failure and must not page anyone."""
    calls: list[str] = []

    async def slow() -> None:
        await asyncio.sleep(10)

    with patch.object(bt.logger, "error", side_effect=lambda event, **kw: calls.append(event)):
        task = bt.create_safe_task(slow())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    assert calls == []


@pytest.mark.asyncio
async def test_the_task_is_dropped_from_the_strong_reference_set() -> None:
    """The registry exists to stop GC; it must not leak completed tasks."""

    async def fine() -> None:
        return None

    task = bt.create_safe_task(fine())
    await task
    await asyncio.sleep(0)

    assert task not in bt._active_tasks
