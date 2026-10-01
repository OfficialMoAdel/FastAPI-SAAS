"""The inbound/outbound Stripe adapter boundary, as a ratchet.

``AGENTS.md`` claims outbound integrations live behind ``src/app/clients/``.
The *inbound* half of that boundary was unenforced: six modules under
``src/app/modules/billing/webhook/`` imported the Stripe SDK, five of them for
nothing but a type annotation. The type is now aliased once as ``StripeEvent``
in ``src/app/clients/billing/stripe.py`` and re-exported from the package.

The rule this file pins (ADR-0007):

    exactly two modules in src/ may ``import stripe`` —
    ``src.app.clients.billing.stripe`` (outbound gateway) and
    ``src.app.modules.billing.webhook.client`` (the inbound trust boundary,
    which must call ``Webhook.construct_event`` and catch
    ``SignatureVerificationError``) — and only the gateway may define the
    ``stripe.Event`` alias.

This is a ratchet rather than a one-shot assertion because the leak is
reintroducible by a single line that lints clean: ``import stripe`` is not a
banned-api cell and no import-linter contract covers it. A test that only
asserts the current count would therefore pass forever, and the boundary would
rot back silently — the exact failure mode where the prose claims a gate and
the code has none.

The class names below are small and deliberate: they mirror
``tests/core/test_source_text_ratchet.py``, which uses the same "prove it
bites" structure for the same reason.
"""


from __future__ import annotations

import ast
from pathlib import Path
from typing import get_type_hints

SRC = Path(__file__).resolve().parents[2] / "src"

# The two sanctioned owners. Keyed by import path so a moved file is a
# failure, not a silent re-baseline.
ALLOWED_STRIPE_IMPORTERS: dict[str, str] = {
    "src.app.clients.billing.stripe": "outbound payment gateway (adapter)",
    "src.app.modules.billing.webhook.client": "inbound webhook signature verification (trust boundary)",
}

# Where the event type may be named as ``stripe.Event``. The alias's own
# definition is not a violation — it is the one place it is spelled.
ALLOWED_EVENT_ALIAS_OWNERS: frozenset[str] = frozenset({"src.app.clients.billing.stripe"})

# The alias callers must import instead. A webhook module that reaches the
# type any other way has re-opened the hole the alias closed.
EXPECTED_ALIAS: str = "StripeEvent"


def _module_name(path: Path) -> str:
    """``src/app/clients/billing/stripe.py`` -> ``src.app.clients.billing.stripe``.

    ``src/`` is the import root (``root_packages = src`` in the import-linter
    config), so ``src`` is part of the module path, not a prefix to strip —
    dropping it yields ``app.*``, which matches nothing. Getting this wrong
    makes every assertion below vacuously true against an empty set, so it is
    pinned by ``TestCountsAreNonEmpty``.
    """
    relative = path.relative_to(SRC.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _imports_stripe(tree: ast.Module) -> bool:
    """True if the module imports the vendor SDK under any spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "stripe" or alias.name.startswith("stripe.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            # ``from stripe import X`` / ``from stripe.v2 import X``
            if node.module and (node.module == "stripe" or node.module.startswith("stripe.")):
                return True
    return False


def _stripe_importer_modules() -> set[str]:
    """Every module in src/ that imports the Stripe SDK."""
    found = set()
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a syntax error fails CI elsewhere
            continue
        if _imports_stripe(tree):
            found.add(_module_name(path))
    return found


def _event_type_named_modules() -> set[str]:
    """Modules naming ``stripe.Event`` as an attribute (i.e. ``stripe.Event``)."""
    found = set()
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "Event"
                and isinstance(node.value, ast.Name)
                and node.value.id == "stripe"
            ):
                found.add(_module_name(path))
                break
    return found


def _resolved_event_annotation(func: object) -> object:
    """The ``event`` parameter's annotation, with string form resolved.

    The handlers use ``from __future__ import annotations``, so their
    annotations are the *string* ``'StripeEvent'``. Comparing that string
    would only prove the module spells the right name — not that the alias
    IS the SDK class. ``get_type_hints`` resolves it through the function's
    own module globals, which turns "spelled the alias" into "the alias is
    stripe.Event".
    """
    from typing import get_type_hints

    return get_type_hints(func)["event"]


class TestWalkerIsNotSilentlyEmpty:
    """Guards the detector itself: a walker that finds nothing looks "clean".

    Every assertion in this file is a set comparison, and a set comparison
    against an empty result set passes. If ``SRC`` were misresolved, or the
    glob silently matched no files, the ratchet would report a perfect
    boundary over zero files. These tests fail closed on that.
    """

    def test_the_tree_walker_actually_finds_files(self) -> None:
        files = _python_files()
        assert len(files) > 100, f"walker found only {len(files)} files under {SRC}"
        assert any(_module_name(p) == "src.app.clients.billing.stripe" for p in files)

    def test_the_sdk_import_set_is_not_empty(self) -> None:
        """If this is empty the ratchet is inert, not satisfied."""
        actual = _stripe_importer_modules()
        assert len(actual) == 2, f"expected the 2 sanctioned importers, found {sorted(actual)}"

    def test_the_alias_is_found_exactly_once(self) -> None:
        """``stripe.Event`` must be spelled in the alias module and nowhere else."""
        named = _event_type_named_modules()
        assert named == set(ALLOWED_EVENT_ALIAS_OWNERS), (
            f"expected the alias owner only, found {sorted(named)}"
        )


class TestRatchetHolds:
    def test_only_the_two_sanctioned_modules_import_the_sdk(self) -> None:
        """The ratchet: a third importer fails here instead of rotting back."""
        actual = _stripe_importer_modules()
        assert actual == set(ALLOWED_STRIPE_IMPORTERS), (
            "the Stripe SDK must be imported by exactly "
            f"{sorted(ALLOWED_STRIPE_IMPORTERS)}, but src/ imports it from "
            f"{sorted(actual)}.\n"
            "If a new module needs the event type, import "
            f"{EXPECTED_ALIAS} from src.app.clients.billing instead of "
            "`import stripe`. If it genuinely needs SDK calls, the trust "
            "boundary moved and ADR-0007 must be amended deliberately."
        )

    def test_no_feature_module_names_the_event_type_directly(self) -> None:
        """``stripe.Event`` as a spelled attribute is the leak itself.

        The alias's own module defines it once; everywhere else it must be
        ``StripeEvent``. This catches the annotation creeping back even if the
        import is laundered (e.g. ``import stripe as s``).
        """
        offenders = _event_type_named_modules() - ALLOWED_EVENT_ALIAS_OWNERS
        assert not offenders, (
            f"{sorted(offenders)} name the vendor event type directly; import "
            f"{EXPECTED_ALIAS} from src.app.clients.billing instead."
        )

    def test_the_alias_is_importable_from_the_adapter_package(self) -> None:
        """The alias must be reachable without touching the SDK.

        If this fails the ratchet is telling callers to use a name that does
        not exist, and they would go back to ``import stripe``.
        """
        from src.app.clients.billing import StripeEvent
        from src.app.clients.billing.stripe import StripeEvent as from_module

        assert StripeEvent is from_module
        assert "StripeEvent" in __import__(
            "src.app.clients.billing", fromlist=["__all__"]
        ).__all__

    def test_the_webhook_pipeline_annotates_through_the_alias(self) -> None:
        """All eight inbound surfaces spell the type via the adapter.

        Pinned by behaviour, not by grepping for the old string: each
        annotation must RESOLVE to the SDK's own class, which is what makes
        the alias pass-by-reference rather than a structural stand-in. A
        test asserting the literal string ``'StripeEvent'`` would pass even if
        the alias were shadowed by a same-named Protocol.
        """
        import stripe

        from src.app.modules.billing.webhook import (
            dispatch,
            handle_webhook_event,
            verify_webhook_signature,
        )
        from src.app.modules.billing.webhook.handlers import (
            checkout,
            invoices,
            refunds,
            subscriptions,
        )

        assert get_type_hints(verify_webhook_signature)["return"] is stripe.Event
        assert _resolved_event_annotation(handle_webhook_event) is stripe.Event
        assert _resolved_event_annotation(dispatch.handle_webhook_event) is stripe.Event

        handlers = [
            checkout.handle_checkout_session_completed,
            invoices.handle_invoice_payment_succeeded,
            invoices.handle_invoice_payment_failed,
            refunds.handle_charge_refunded,
            subscriptions.handle_subscription_updated,
            subscriptions.handle_subscription_deleted,
        ]
        assert len(handlers) == 6
        for handler in handlers:
            assert _resolved_event_annotation(handler) is stripe.Event, (
                f"{handler.__module__}.{handler.__name__} drifted"
            )


class TestRatchetBites:
    """Proven, not asserted: the detector must flag a real violation.

    A ratchet nobody has seen fail is indistinguishable from a ratchet that
    cannot fail. Each case feeds the same detector used above a synthetic
    module and asserts it is caught.
    """

    def test_detects_a_plain_sdk_import(self) -> None:
        module = ast.parse("import stripe\n")
        assert _imports_stripe(module) is True

    def test_detects_a_from_import(self) -> None:
        module = ast.parse("from stripe import StripeClient\n")
        assert _imports_stripe(module) is True

    def test_detects_a_renamed_sdk_import(self) -> None:
        """``import stripe as s`` is the same leak; renaming must not hide it."""
        module = ast.parse("import stripe as s\n\nx = s.Event\n")
        assert _imports_stripe(module) is True

    def test_detects_a_function_level_deferred_import(self) -> None:
        """Deferred imports are how this leak is actually reintroduced.

        The AST walk has no scope awareness, so a function-level import is
        detected exactly like a module-level one.
        """
        module = ast.parse("def f():\n    import stripe\n    return stripe.Event\n")
        assert _imports_stripe(module) is True

    def test_ignores_an_unrelated_httpx_import(self) -> None:
        """Negative control: the detector is not simply matching everything."""
        module = ast.parse("import httpx\nfrom src.app.core import logger\n")
        assert _imports_stripe(module) is False

    def test_ignores_a_module_named_stripe_client(self) -> None:
        """Prefix matching must not catch a local module whose name starts with ``stripe``."""
        module = ast.parse("import stripe_client\nfrom stripe_helpers import x\n")
        assert _imports_stripe(module) is False


class TestFacadeExports:
    def test_webhook_package_still_reexports_the_verifier(self) -> None:
        """The alias refactor must not drop the public webhook surface.

        Tests and the router import ``verify_webhook_signature`` from the
        package facade; the seam moves the type, not the function.
        """
        import src.app.modules.billing.webhook as webhook_pkg
        from src.app.modules.billing.webhook.client import verify_webhook_signature

        assert webhook_pkg.verify_webhook_signature is verify_webhook_signature
