"""Security budgets are deliberately GLOBAL — this pins that decision.

``check``/``probe`` take no tenant argument at all, so every budget key is
installation-wide and no code path could scope one. That is a documented
decision (see the ``budgets`` module docstring), not an oversight:

  1. the two edge doors run BEFORE TenantMiddleware and cannot supply one;
  2. scoping the IP-keyed login budgets would let an attacker mint a fresh
     brute-force budget by varying the tenant header;
  3. ``build_tenant_key`` prefixes keys, so scoping needs a coordinated
     Redis key migration (``rate_policy.py`` records this).

A parameter that looks like a guarantee and is never supplied is worse than
no parameter, so this file makes the posture explicit and enforced:

  - every production door is enumerated, and the set is pinned;
  - every door's Redis key is pinned in its CURRENT (global) form;
  - enabling tenant scope is made a loud, deliberate act.

P1-9 removed the ``tenant_id`` parameter that every door accepted and none
supplied. Reintroducing it is now a signature change the tests below reject
outright, so the migration conversation cannot be skipped by threading one
argument through one door.
"""

import ast
import re
from pathlib import Path

import pytest

#: The src/ package root, derived from the imported package so the depth of
#: this test file cannot silently change the meaning of every path below.
import src.app as _app_pkg
from src.app.core.security.budgets import BUDGETS, SecurityAction, _leg_key
from src.app.core.security.rate_policy import build_rate_limit_key

SRC = Path(_app_pkg.__file__).resolve().parents[1]

#: Every production file that calls check()/probe() on the seam. Pinned so a
#: new door cannot be added without deciding its scope.
EXPECTED_DOORS: dict[str, str] = {
    "app/core/security/request_admission.py": "probe",
    "app/api/deps/ai_rate_limit.py": "probe",
    "app/modules/auth/rate_limit.py": "check",
    "app/modules/auth/routers/magic_link.py": "check",
    "app/modules/auth/routers/mfa.py": "check",
    "app/modules/auth/routers/passkey.py": "check",
    "app/modules/auth/services/mfa.py": "check",
}


def _discover_doors() -> dict[str, list[ast.Call]]:
    """Scan ALL of src/ for seam calls, so a NEW door is discovered here.

    The expected list alone would be a self-fulfilling check; this is what
    makes "a new door must decide its scope" enforceable.
    """
    found: dict[str, list[ast.Call]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.endswith("budgets.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        imports = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            and node.module.endswith("core.security.budgets")
            for alias in node.names
        }
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in imports & {"check", "probe"}
        ]
        if calls:
            found[rel] = calls
    return found


class TestDoorInventory:
    def test_no_new_doors_without_a_scope_decision(self) -> None:
        discovered = set(_discover_doors())
        assert discovered == set(EXPECTED_DOORS), (
            "the security-budget door set changed — decide its tenant scope "
            f"first. added={sorted(discovered - set(EXPECTED_DOORS))} "
            f"removed={sorted(set(EXPECTED_DOORS) - discovered)}"
        )

    def test_no_production_door_passes_tenant_id(self) -> None:
        """The core invariant: global scope is real, not aspirational.

        If this starts failing, someone threaded tenant_id through. That is
        not automatically wrong — but it changes every live Redis key, so it
        must come with the coordinated migration, and this failure is the
        signal to have that conversation.
        """
        offenders = [
            f"{entry}:{call.lineno}"
            for entry, calls in _discover_doors().items()
            for call in calls
            if any(kw.arg == "tenant_id" for kw in call.keywords)
        ]
        assert not offenders, (
            f"{offenders} now pass tenant_id — every live budget key changes "
            "shape. This needs the coordinated Redis key migration described "
            "in the budgets module docstring."
        )

    def test_discovery_finds_every_door_and_call(self) -> None:
        """Guard against this file going vacuous the way its predecessors did.

        _discover_doors resolves the imported names from the AST, so a door
        that is renamed, aliased, or added is still found. Without this, a
        broken discovery helper would make every assertion above pass
        silently — the exact failure mode of the tests this round replaced.
        """
        discovered = _discover_doors()
        assert discovered, "discovery found no doors at all — it is broken"
        total = sum(len(c) for c in discovered.values())
        assert len(discovered) == len(EXPECTED_DOORS)
        assert total >= len(EXPECTED_DOORS), (
            f"expected at least one call per door, found {total} calls across "
            f"{len(discovered)} files"
        )
        for entry, calls in discovered.items():
            assert calls, f"{entry} has no resolved call despite being listed"


class TestKeysAreGlobal:
    def test_a_budget_key_carries_no_tenant_prefix(self) -> None:
        rendered = "user:42"
        action = SecurityAction.AUTH
        definition = BUDGETS[action]
        leg = definition.legs[0]

        key = _leg_key(leg, rendered)
        assert not key.startswith("tenant:"), f"unexpected tenant prefix: {key}"
        assert "budget:" + leg.budget in key

    def test_leg_key_accepts_no_tenant_argument(self) -> None:
        """P1-9: the parameter is REMOVED, so scoping is not one argument away.

        Before this change ``_leg_key`` took ``tenant_id`` and every door
        omitted it. That is how a green contract test came to assert tenant
        scoping "works" while no production call site used it. With the
        parameter gone, enabling tenant scope is a signature change that
        cannot happen by accident.
        """
        import inspect

        assert "tenant_id" not in inspect.signature(_leg_key).parameters
        with pytest.raises(TypeError):
            _leg_key(BUDGETS[SecurityAction.AUTH].legs[0], "user:42", tenant_id=999)

    def test_build_rate_limit_key_is_identity_for_this_path(self) -> None:
        """Budgets key on the user branch, never the anonymous one."""
        key = build_rate_limit_key("budget:auth", user_id="user:42", tenant_id=None)
        assert key.startswith("rate_limit:user:")
        assert "anonymous" not in key


class TestDocumentedDecision:
    def test_module_docstring_states_the_global_decision(self) -> None:
        """The decision must be discoverable where a reader will look."""
        doc = (SRC / "app/core/security/budgets.py").read_text(encoding="utf-8")
        assert "DELIBERATELY GLOBAL" in doc, (
            "the budgets module no longer documents that tenant scope is "
            "deliberately unused — a reader would assume it is an oversight"
        )
        for reason in ("RateLimit", "brute force", "key migration"):
            assert reason in doc, f"docstring omits the reason: {reason!r}"

    def test_ip_keyed_budgets_are_the_ones_that_must_stay_global(self) -> None:
        """The three brute-force budgets are why scoping is not a free change."""
        from src.app.core.security.budgets import KeyKind

        ip_keyed = {
            str(a)
            for a, d in BUDGETS.items()
            if any(leg.kind is KeyKind.IP for leg in d.legs)
        }
        assert {SecurityAction.AUTH, SecurityAction.PASSKEY_LOGIN, SecurityAction.REFRESH} <= {
            type(a)(s) for s in ip_keyed for a in [next(x for x in BUDGETS if str(x) == s)]
        }, f"expected AUTH/PASSKEY_LOGIN/REFRESH to be IP-keyed, got {ip_keyed}"


def test_no_source_string_assertions_in_this_file() -> None:
    """This file must assert behaviour, not source text.

    Added as a standing guard so the round-1 getsource pattern does not
    spread to the files written to replace it.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    # Build the needles at runtime so this guard does not match itself.
    assert "inspect" + ".getsource" not in text
    assert not re.search(r"\.count\(\s*['\"]", text), "counting source text proves nothing"
