"""S-18 / S-19 — webhook handlers must not commit before their compensating work.

S-18  `handle_invoice_payment_succeeded` created the BillingRecord with
      FastCRUD's default `commit=True`, so the invoice was DURABLY recorded
      before `lock_subscription_by_id` and the `past_due -> active` recovery.
      A failure after that point left the invoice marked paid while the
      subscription stayed past_due — and the retry then took the
      `existing_record["status"] == "succeeded"` early return, so the recovery
      could never run. Permanently stuck, with no error.

S-19  Both subscription handlers ran `crud_user_subscriptions.update` with the
      default `commit=True` before `downgrade_to_free_tier`, which itself
      commits. A downgrade failure left a `canceled` subscription with the user
      still on the paid tier until Stripe's next redelivery.

Both fixes are `commit=False`, matching the sibling `update` in the invoice
handler that already did this.

Structural assertions: a `commit=True`-shaped default on the write that must
participate in the handler's transaction.
"""

from __future__ import annotations

import ast
from pathlib import Path

INVOICES = Path("src/app/modules/billing/webhook/handlers/invoices.py")
SUBSCRIPTIONS = Path("src/app/modules/billing/webhook/handlers/subscriptions.py")


def _calls(path: Path, func: str) -> list[ast.Call]:
    """Every call to `<func>` in `path`, as AST nodes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == func
    ]


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


class TestS18InvoiceRecordJoinsTheTransaction:
    def test_create_does_not_commit(self) -> None:
        creates = [c for c in _calls(INVOICES, "create") if _kw(c, "object") is not None]
        assert creates, "billing-record create not found"
        for c in creates:
            commit = _kw(c, "commit")
            assert commit is not None, "create() must pass commit explicitly"
            assert ast.literal_eval(commit) is False, "create() must not commit before the re-lock"

    def test_the_sibling_update_already_did_this(self) -> None:
        """Control: proves the codebase's own convention, and that we matched it."""
        updates = [c for c in _calls(INVOICES, "update") if _kw(c, "object") is not None]
        assert any(
            _kw(c, "commit") is not None and ast.literal_eval(_kw(c, "commit")) is False for c in updates
        ), "expected an update() in this handler to already pass commit=False"

    def test_create_precedes_the_relock(self) -> None:
        """Ordering is the point: the insert must not be durable before it.

        Scoped to ONE function via AST line numbers — `lock_subscription_by_id`
        also appears in `handle_invoice_payment_failed`, so a naive
        source.index() would compare across two handlers.
        """
        src = INVOICES.read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_invoice_payment_succeeded"
        )
        lines = src.splitlines()
        create_line = next(
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "create"
            and "billing_records" in ast.dump(n.func)
        )
        lock_line = next(
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call) and "lock_subscription_by_id" in ast.dump(n.func)
        )
        assert create_line < lock_line, (
            f"insert at line {create_line} should precede the re-lock at {lock_line}: {lines[lock_line - 1].strip()}"
        )


class TestS19SubscriptionUpdateJoinsTheTransaction:
    def test_every_subscription_update_avoids_the_default_commit(self) -> None:
        updates = [
            c
            for c in _calls(SUBSCRIPTIONS, "update")
            if _kw(c, "object") is not None and _kw(c, "id") is not None
        ]
        assert len(updates) >= 2, f"expected both handlers, found {len(updates)}"
        for c in updates:
            commit = _kw(c, "commit")
            assert commit is not None, "update() must pass commit explicitly"
            assert ast.literal_eval(commit) is False, "update() must not commit before the downgrade"

    def test_a_downgrade_follows_the_first_update(self) -> None:
        """Scoped to one handler, for the same reason as the invoice case."""
        src = SUBSCRIPTIONS.read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_subscription_deleted"
        )
        update_line = next(
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "update"
        )
        downgrade_line = next(
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call) and "downgrade_to_free_tier" in ast.dump(n.func)
        )
        assert update_line < downgrade_line
