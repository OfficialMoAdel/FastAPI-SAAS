"""S-12 — the tier-deletion referential guard must be tenant-blind.

`delete_tier` guards on two counts: users referencing the tier, and rate limits
referencing it. Those two queries answered DIFFERENT questions:

  * `users` is in migration 08's TENANT_TABLES -> RLS-enforced, so a plain
    count sees only the CURRENT tenant's users.
  * `rate_limit` is NOT in TENANT_TABLES -> its count is global.

So a tier used by another tenant's users but carrying no rate limits reported
`users_count=0 / rl_count=0` and passed the guard.

That is not a cosmetic guard failure. Verified against the live schema,
`users.tier_id` is declared `ON DELETE SET NULL` — so the delete SUCCEEDS and
silently orphans every cross-tenant user by nulling their tier_id. No
exception, no warning, and a superuser on a shared installation is precisely
the case where cross-tenant references exist.

These tests pin the scope (a bypass is used, with a reason) rather than the
counts themselves.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVICE = Path("src/app/modules/tiers/service.py")


def _delete_guard_ast() -> ast.AST:
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_tier":
            return node
    raise AssertionError("delete_tier not found")


class TestGuardRunsUnderAnAuditedBypass:
    def test_both_counts_are_inside_a_bypass(self) -> None:
        """Both queries must execute inside one bypass scope.

        Verified structurally: an awaited `db.execute` that is NOT inside an
        `async with` for the bypass is the pre-fix shape — that count is
        tenant-scoped while the other is global.
        """
        fn = _delete_guard_ast()
        executed: list[int] = []  # bypass depth at each db.execute call

        class Walk(ast.NodeVisitor):
            depth = 0

            def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
                is_bypass = "bypass_rls" in ast.dump(node)
                self.depth += 1 if is_bypass else 0
                self.generic_visit(node)
                self.depth -= 1 if is_bypass else 0

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "execute":
                    executed.append(self.depth)
                self.generic_visit(node)

        Walk().visit(fn)

        assert executed, "no db.execute() call found in delete_tier"
        assert all(d > 0 for d in executed), (
            f"reference counts at bypass depth {executed} — a depth-0 count is "
            "tenant-scoped while the others are global"
        )

    def test_bypass_supplies_an_audit_reason(self) -> None:
        """A bypass without a reason is rejected by rls.bypass_rls itself."""
        src = SERVICE.read_text(encoding="utf-8")
        assert "bypass_rls(self.db, reason=" in src, "the bypass must pass reason= for the audit trail"
        assert "Tier deletion referential check" in src, "the reason must say what it is for"
