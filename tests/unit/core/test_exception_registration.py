"""P1-#4 — Error-tree registration audit (plan candidate #4).

Starlette resolves exception handlers by walking ``type(exc).__mro__`` and
returning the first registered class (starlette/_exception_handler.py
``_lookup_exception_handler``). Therefore an unregistered subclass of a
registered ancestor is still handled — the audit rule is:

    Every concrete AppException subclass must be REACHABLE: some class in
    its MRO (excluding AppException and object) must have a handler
    registered on the application — a table row, a framework handler, or
    the global catch-all is NOT enough (the catch-all returns 500 for
    domain errors that should carry a domain status/code).

If this test fails, the new exception class was added without a row (or a
registered parent). Either register it in ``build_exception_table`` or make
it inherit from a class that already has a row.
"""

from collections.abc import Iterator

import pytest

from src.app.core.errors import build_exception_table
from src.app.shared.exceptions import AppException

# Modules that define AppException subclasses. Keep in sync with
# ``rg -n "AppException" src/``. Imported before the walk so every class
# exists in the runtime hierarchy (__subclasses__ only sees imported classes).
_EXCEPTION_MODULES = [
    "src.app.shared.exceptions",
    "src.app.core.exceptions.cache_exceptions",
    "src.app.core.exceptions.http_exceptions",
    "src.app.modules.ai.providers",
    "src.app.modules.ai.unified_errors",
    "src.app.modules.auth.errors",
    "src.app.modules.billing.exceptions",
    "src.app.modules.storage.errors",
    "src.app.modules.user_preferences.service",
]


def _import_exception_modules() -> None:
    import importlib

    for module_name in _EXCEPTION_MODULES:
        importlib.import_module(module_name)


def _walk_subclasses(base: type) -> Iterator[type]:
    """Yield every descendant class of ``base`` (recursive)."""
    for subclass in base.__subclasses__():
        yield subclass
        yield from _walk_subclasses(subclass)


def _registered_classes() -> set[type]:
    """Classes with a dedicated handler: table rows + framework handlers.

    The table classes are the domain rows. The framework handlers register
    ``CacheException`` (cache tree -> deliberate 500) plus Starlette /
    pydantic / SQLAlchemy classes outside the AppException tree.
    """
    classes: set[type] = {spec.exc_class for spec in build_exception_table()}
    classes.add(__import__("src.app.core.exceptions.cache_exceptions", fromlist=["CacheException"]).CacheException)
    return classes


def _is_reachable(cls: type, registered: set[type]) -> bool:
    """True when some class in ``cls``'s MRO (below AppException) is registered."""
    for mro_cls in cls.__mro__:
        if mro_cls in (AppException, object):
            break
        if mro_cls in registered:
            return True
    return False


class TestExceptionRegistrationAudit:
    """Every AppException subclass must resolve to a real handler, not the 500 catch-all."""

    def test_every_appexception_subclass_is_reachable(self) -> None:
        _import_exception_modules()
        registered = _registered_classes()
        unreachable = [
            cls
            for cls in _walk_subclasses(AppException)
            if not _is_reachable(cls, registered)
        ]
        assert not unreachable, (
            "Unregistered AppException subclasses fall through to the 500 catch-all: "
            f"{[f'{cls.__module__}.{cls.__name__}' for cls in unreachable]}. "
            "Add a row to build_exception_table() in src/app/core/errors.py "
            "or inherit from a registered class."
        )

    def test_table_classes_are_importable_and_resolvable(self) -> None:
        _import_exception_modules()
        for spec in build_exception_table():
            assert isinstance(spec.exc_class, type)
            # Status may be a literal or a callable resolving from the exc.
            if callable(spec.status):
                assert callable(spec.code)
            if callable(spec.code):
                assert callable(spec.status) or isinstance(spec.status, int)

    def test_table_has_no_duplicate_rows(self) -> None:
        seen: dict[type, str] = {}
        for spec in build_exception_table():
            assert spec.exc_class not in seen, (
                f"{spec.exc_class} registered twice in build_exception_table()"
            )
            seen[spec.exc_class] = spec.log_message

    def test_preference_storage_error_is_registered_with_details(self) -> None:
        """P1-#4: the renamed class must have its own row (was 500 catch-all)."""
        from src.app.modules.user_preferences.service import PreferenceStorageError

        rows = [spec for spec in build_exception_table() if spec.exc_class is PreferenceStorageError]
        assert len(rows) == 1
        row = rows[0]
        assert callable(row.details)
        exc = PreferenceStorageError("upload", "boom")
        assert row.details(exc) == {"operation": "upload", "reason": "boom"}

    def test_ai_provider_error_covers_subclass_tree(self) -> None:
        """P1-#4: AI subclasses must be reachable via the AIProviderError row."""
        _import_exception_modules()
        from src.app.modules.ai.providers import AIProviderError

        registered = _registered_classes()
        ai_classes = [cls for cls in _walk_subclasses(AIProviderError) if cls is not AIProviderError]
        assert ai_classes, "AI subclass tree unexpectedly empty — check imports"
        for cls in ai_classes:
            assert _is_reachable(cls, registered), f"{cls.__name__} unreachable (no registered MRO ancestor)"

    @pytest.mark.parametrize("module_name", _EXCEPTION_MODULES)
    def test_exception_modules_importable(self, module_name: str) -> None:
        import importlib

        importlib.import_module(module_name)
