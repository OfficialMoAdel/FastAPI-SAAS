"""S-41 — the retry enqueue must happen AFTER the attempt is committed.

`_handle_failure` used to call `_spawn_enqueue(...)` itself, which runs BEFORE
`deliver()`'s `await db.commit()`. The code relied on "delay is large enough
that commit will finish first" — true for the 60s+ retry schedule, but NOT for
the circuit-open path, where `override_delay` collapses the defer to
`max(1, ceil(recovery_in))` ≈ 1 second.

If the commit lost that race, the ARQ retry re-read the row from the database,
saw the PRE-attempt `attempt_count` / `next_retry_at` / `status`, and re-POSTed
the same payload to the customer. A duplicate webhook delivery manufactured by
our own bookkeeping, not by the receiver.

`_handle_failure` now RETURNS the enqueue arguments and `deliver()` spawns them
only after the commit. Asserted structurally: the spawn must appear after the
commit in the source, and `_handle_failure` must not spawn at all.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

DELIVERY = Path("src/app/modules/webhooks/delivery.py")


def _fn(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(DELIVERY.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


class TestSpawnIsDeferredUntilAfterCommit:
    def test_handle_failure_never_spawns(self) -> None:
        """The enqueue moved out of _handle_failure entirely."""
        fn = _fn("_handle_failure")
        spawns = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and "_spawn_enqueue" in ast.dump(n.func)
        ]
        assert not spawns, f"_handle_failure still spawns the enqueue at line(s) {spawns}"

    def test_handle_failure_returns_the_enqueue_arguments(self) -> None:
        fn = _fn("_handle_failure")
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert returns, "_handle_failure must return the enqueue arguments"
        # It must return a 3-tuple of (delivery_id, defer, tenant_id) or None.
        has_tuple = any(isinstance(n.value, ast.Tuple) and len(n.value.elts) == 3 for n in returns)
        assert has_tuple or any(n.value is None for n in returns)

    def test_every_spawn_in_deliver_is_preceded_by_a_commit(self) -> None:
        """The property that matters.

        `deliver()` has TWO enqueue paths: the circuit-open early return
        (which already committed first) and the main path (S-41). Asserting
        against only the first would have passed pre-fix, so this requires
        EVERY spawn to be preceded by a `db.commit()`.
        """
        fn = _fn("deliver")
        commits: list[int] = []
        spawns: list[int] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Await) and "attr='commit'" in ast.dump(n.value):
                commits.append(n.lineno)
            if isinstance(n, ast.Call) and "_spawn_enqueue" in ast.dump(n.func):
                spawns.append(n.lineno)

        assert commits, "no db.commit() found in deliver()"
        assert len(spawns) >= 2, f"expected both enqueue paths, found {len(spawns)}"
        for spawn in spawns:
            prior = [c for c in commits if c < spawn]
            assert prior, f"enqueue at line {spawn} has no preceding db.commit()"

    def test_return_annotation_exposes_the_contract(self) -> None:
        """Callers need the shape, so it is annotated rather than implicit."""
        from src.app.modules.webhooks.delivery import _handle_failure

        sig = inspect.signature(_handle_failure)
        assert sig.return_annotation is not inspect.Signature.empty, (
            "_handle_failure must annotate its new return contract"
        )


class TestRetryStillScheduled:
    def test_schedule_is_preserved(self) -> None:
        """The behaviour must be unchanged — only the timing moved."""
        src = DELIVERY.read_text(encoding="utf-8")
        body = src[src.index("async def _handle_failure") :]
        assert "next_retry_at" in body, "retry scheduling was lost"
        assert "WebhookDeliveryStatus.PENDING" in body
        assert "WebhookDeliveryStatus.FAILED" in body, "the dead-delivery branch was lost"
