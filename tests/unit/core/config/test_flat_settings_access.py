"""H-7: the flat `settings.X` delegation must not be how code reads config.

`Settings.__getattr__` delegates an unknown attribute to whichever component
model owns that field, so both `settings.SECRET_KEY` and
`settings.crypt.SECRET_KEY` work. The flat form is invisible to static
analysis — mypy resolves it to `Any` — which is the defect. The project's own
rule is that required config must fail fast; a typo in a flat key instead
raises `AttributeError` at the first request that reads it.

Two properties are pinned here:

1. RATCHET: production code contains no flat access. Adding one fails.
2. COUNTER-TEST: every field in the delegation map resolves through its dotted
   path to the SAME object. This is what catches a codemod that migrated a
   name to the wrong component — a test that only counted call sites would
   happily accept `settings.crypt.EVENT_HANDLER_TIMEOUT_SECONDS`.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
APP = REPO_ROOT / "src" / "app"

# `settings.X` / `_pkg.settings.X` where X is UPPER_SNAKE — the delegation
# surface. Dotted access (`settings.crypt.SECRET_KEY`) does not match, because
# the component segment is lower-case.


def _statically_visible() -> set[str]:
    """Names on `Settings` that a type checker can already resolve.

    A name is a hole only if it is NOT a field, NOT a property and NOT a
    computed field — because only then does it fall through to `__getattr__`.
    Asking the class is principled; a hardcoded list would rot the moment
    someone adds another property.

    `SECRET_KEY` is the case that proves the rule matters. It is a property
    that asserts non-None before returning, so it is both statically visible
    AND better typed than the dotted `settings.crypt.SECRET_KEY`, which is
    `SecretStr | None`. Migrating it made mypy WORSE — 7 new `union-attr`
    errors — and was reverted.
    """
    from src.app.core.config import Settings

    visible = set(Settings.model_fields)
    visible |= {n for n in dir(Settings) if isinstance(getattr(Settings, n, None), property)}
    visible |= set(getattr(Settings, "__pydantic_computed_fields__", {}) or {})
    return visible


def _flat_sites() -> list[tuple[str, int, str]]:
    """Every flat `settings.<UPPER_SNAKE>` access that is NOT statically visible."""
    import re

    pattern = re.compile(r"\b(?:_pkg\.|_state\.)?settings\.([A-Z][A-Z0-9_]*)\b")
    visible = _statically_visible()
    found: list[tuple[str, int, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                if match.group(1) in visible:
                    continue  # resolves statically; not a hole
                found.append((str(path.relative_to(REPO_ROOT)), lineno, match.group(0)))
    return found


class TestNoFlatConfigAccess:
    def test_production_code_has_no_flat_settings_access(self) -> None:
        """RATCHET: the count is zero and must stay zero.

        Falsifiability: add `settings.SECRET_KEY` anywhere in src/app and this
        fails with the file and line, so the regression names itself.
        """
        sites = _flat_sites()
        assert sites == [], (
            f"{len(sites)} flat config access(es) reintroduced; static analysis "
            f"cannot resolve them:\n"
            + "\n".join(f"  {path}:{line}: {expr}" for path, line, expr in sites)
        )

    def test_the_scanner_detects_a_flat_access(self) -> None:
        """COUNTER-TEST: the ratchet above is not vacuously true.

        A scan that finds nothing because its pattern is wrong passes forever.
        This feeds it a known-flat string and requires it to match.
        """
        import re

        pattern = re.compile(r"\b(?:_pkg\.|_state\.)?settings\.[A-Z][A-Z0-9_]*\b")
        assert pattern.search("key = settings.SECRET_KEY")
        assert pattern.search("if _pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS:")
        # And it must NOT match the dotted form, or every legitimate call site
        # would be reported.
        assert not pattern.search("key = settings.crypt.SECRET_KEY")
        assert not pattern.search("settings.events.EVENT_BUS_USE_BATCH_ENQUEUE")


class TestDottedSurfaceIsComplete:
    """The counter-test: every delegated field resolves through its component."""

    def test_every_delegated_field_resolves_dotted(self) -> None:
        """Walk the whole delegation map; nothing may be unreachable.

        Asserted as identity with the flat read, so a mis-mapped component
        cannot pass.
        """
        from src.app.core.config import settings

        delegation = settings._delegation_map
        assert delegation, "the delegation map is empty; the counter-test cannot fail"

        mismatched: list[str] = []
        for field, component in delegation.items():
            try:
                dotted = getattr(getattr(settings, component), field)
            except AttributeError:
                mismatched.append(f"{field} -> settings.{component}.{field} (missing)")
                continue
            flat = getattr(settings, field)
            if dotted != flat:
                mismatched.append(f"{field}: settings.{component}.{field} != settings.{field}")

        assert not mismatched, f"delegation map entries that do not round-trip: {mismatched}"

    @pytest.mark.parametrize(
        ("field", "component"),
        [
            ("EVENT_HANDLER_TIMEOUT_SECONDS", "events"),
            ("EVENT_BUS_USE_LUA_IDEMPOTENCY", "r1_unified"),
            ("XFETCH_USE_LUA_SCRIPT", "r1_unified"),
            ("EVENT_BUS_USE_BATCH_ENQUEUE", "r1_unified"),
        ],
    )
    def test_the_migrated_keys_live_where_the_codemod_put_them(self, field: str, component: str) -> None:
        """Each key this migration moved is named explicitly.

        The ratchet above proves no flat access remains; this proves the flat
        access it removed was pointed at the right component, which a count
        alone cannot establish.
        """
        from src.app.core.config import settings

        assert settings._delegation_map.get(field) == component
        assert getattr(getattr(settings, component), field) == getattr(settings, field)


class TestFlatAccessorIsVisible:
    """The fallback must be loud, or the next migration has no worklist."""

    # SECRET_KEY is deliberately NOT the subject here: it is a @property on
    # Settings (config/__init__.py:235) that returns self.crypt.SECRET_KEY, so
    # mypy can already see it and it never reaches __getattr__. Using it would
    # have tested nothing while appearing to pass.
    SUBJECT = "EVENT_HANDLER_TIMEOUT_SECONDS"

    def test_flat_access_emits_a_deprecation_naming_the_dotted_path(self) -> None:
        """The fallback warns once, naming the exact dotted replacement."""
        from src.app.core.config import _FLAT_ACCESSOR_WARNED, settings

        _FLAT_ACCESSOR_WARNED.discard(self.SUBJECT)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                getattr(settings, self.SUBJECT)

            deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            assert deprecations, "the flat accessor did not warn; it is invisible again"
            message = str(deprecations[0].message)
            assert f"settings.events.{self.SUBJECT}" in message, (
                f"the warning must name the exact dotted replacement, got: {message}"
            )
        finally:
            _FLAT_ACCESSOR_WARNED.discard(self.SUBJECT)

    def test_the_warning_is_emitted_once_per_key_not_once_per_access(self) -> None:
        """COUNTER-TEST: a deprecation that fires on every read is noise.

        The previous instance-level "warn once" flag silently failed to stick
        on a pydantic v2 model, so one key produced 28 warnings in a single
        process — which would flood CI and train everyone to filter the
        category out.
        """
        from src.app.core.config import _FLAT_ACCESSOR_WARNED, settings

        _FLAT_ACCESSOR_WARNED.discard(self.SUBJECT)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                for _ in range(5):
                    getattr(settings, self.SUBJECT)

            deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            assert len(deprecations) == 1, (
                f"expected exactly one DeprecationWarning for 5 accesses, got {len(deprecations)}"
            )
        finally:
            _FLAT_ACCESSOR_WARNED.discard(self.SUBJECT)

    def test_secret_key_is_a_property_not_a_delegation_hole(self) -> None:
        """`settings.SECRET_KEY` never reaches __getattr__, by design.

        It is a declared property returning the crypt component's field, so
        static analysis resolves it. Recording that here stops someone
        "migrating" it and, more importantly, stops anyone assuming the flat
        form is uniformly invisible.
        """
        from src.app.core.config import Settings

        assert isinstance(getattr(Settings, "SECRET_KEY"), property)

    def test_a_typo_still_raises_and_points_somewhere_useful(self) -> None:
        """An unknown key must fail, not resolve to something plausible."""
        from src.app.core.config import settings

        with pytest.raises(AttributeError):
            settings.THIS_SETTING_DOES_NOT_EXIST  # noqa: B018


class TestProviderDefaultIsNotASilentFallback:
    """`AI_PROVIDER` never existed; the getattr that read it always fell back."""

    def test_ai_provider_is_not_a_settings_field(self) -> None:
        """Records WHY the factory uses a constant, so it is not "restored".

        `factory.py` used `getattr(settings, "AI_PROVIDER", DEFAULT_PROVIDER)`.
        `AI_PROVIDER` is declared in no config component, so the getattr always
        took the default and an operator's `AI_PROVIDER=openai` was silently
        ignored. If a future change ADDS the setting, this test fails and the
        factory can be pointed at it deliberately.
        """
        from src.app.core.config import settings

        assert "AI_PROVIDER" not in settings._delegation_map, (
            "AI_PROVIDER is now a real setting; the AI factory's provider default "
            "should be wired to it rather than to DEFAULT_PROVIDER"
        )

    def test_the_factory_docstring_does_not_promise_a_setting_that_is_absent(self) -> None:
        from src.app.modules.ai import factory

        doc = (factory.AIProviderFactory.__doc__ or "") + (
            factory.AIProviderFactory.get_provider.__doc__ or ""
        )
        for text in (doc,):
            if "AI_PROVIDER" in text:
                assert "no AI_PROVIDER setting" in text or "There is no AI_PROVIDER" in text, (
                    "a docstring still promises settings.AI_PROVIDER, which does not exist"
                )
