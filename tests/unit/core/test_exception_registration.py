"""Error-tree registration audit.

Starlette resolves exception handlers by walking ``type(exc).__mro__`` and
returning the first registered class (starlette/_exception_handler.py
``_lookup_exception_handler``). Therefore an unregistered subclass of a
registered ancestor is still handled — the audit rule is:

    Every concrete AppException subclass must be REACHABLE: some class in
    its MRO (excluding AppException and object) must declare its own
    ``http_status`` (or override ``resolve_http_status``) — the global
    catch-all is NOT enough (it returns 500 for domain errors that should
    carry a domain status/code).

Since candidate 3 (the HTTP-mapping seam) the mapping is always declared
beside the exception, and pass 6 deleted the core mapping table entirely. The
rule is deliberately written against ``cls.__dict__`` rather than
``getattr``, so a class that silently inherits ``AppException``'s 500 default
still fails — that is the whole point of the audit.

If this test fails, a new exception class was added without a mapping.
Declare ``http_status``/``response_code`` on the class, override
``resolve_http_status()``/``resolve_response_code()`` for per-instance values,
or inherit from a class that does one of those.
"""

from collections.abc import Iterator

import pytest

from src.app.shared.exceptions import AppException

# Modules that define AppException subclasses. Keep in sync with
# ``rg -n "AppException" src/``. Imported before the walk so every class
# exists in the runtime hierarchy (__subclasses__ only sees imported classes).
_EXCEPTION_MODULES = [
    "src.app.shared.exceptions",
    "src.app.core.exceptions.cache_exceptions",
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
    """Classes with a handler registered for them *specifically*.

    Since candidate 3 pass 6 there is no domain mapping table, so the only
    bespoke registration inside the ``AppException`` tree is the framework
    handler for ``CacheException`` (cache tree -> deliberate 500). The
    framework handlers also register Starlette / pydantic / SQLAlchemy classes,
    which are outside this tree and so irrelevant to the walk.
    """
    classes: set[type] = set()
    classes.add(__import__("src.app.core.exceptions.cache_exceptions", fromlist=["CacheException"]).CacheException)
    return classes


# Two ways a class can state its mapping (candidate 3): a literal attribute
# (`http_status = 507`) or an override for per-instance values
# (`AuthError.resolve_http_status`, which derives it from `self.status_code`).
_STATUS_DECLARATIONS = ("http_status", "resolve_http_status")
_CODE_DECLARATIONS = ("response_code", "resolve_response_code")


def _declares_own_status(cls: type) -> bool:
    """True when the class states its own status.

    Checks ``cls.__dict__`` rather than ``getattr`` on purpose: the point is
    that the class (or an ancestor below AppException) states its mapping,
    not that it silently inherits the 500 default from ``AppException``.
    """
    return any(name in cls.__dict__ for name in _STATUS_DECLARATIONS)


def _is_reachable(cls: type, registered: set[type]) -> bool:
    """True when some class in ``cls``'s MRO (below AppException) maps it."""
    for mro_cls in cls.__mro__:
        if mro_cls in (AppException, object):
            break
        if mro_cls in registered or _declares_own_status(mro_cls):
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
            "AppException subclasses with no mapping fall through to the 500 catch-all: "
            f"{[f'{cls.__module__}.{cls.__name__}' for cls in unreachable]}. "
            "Declare http_status/response_code on the class, override "
            "resolve_http_status()/resolve_response_code() for per-instance values, "
            "or inherit from a class that does one of those."
        )

    def test_migrated_classes_declare_a_complete_mapping(self) -> None:
        """Candidate 3: a class that declares ``http_status`` must declare a code too.

        Partial declarations are the failure mode this guards: an ``http_status``
        without a ``response_code`` would present the inherited
        ``INTERNAL_ERROR`` on, say, a 404 — a silently wrong envelope.

        A mapping may be a literal (``http_status = 507``) or a per-instance
        override (``AuthError.resolve_http_status``, deriving both from
        ``self.status_code``).
        """
        _import_exception_modules()
        incomplete: list[str] = []
        for cls in _walk_subclasses(AppException):
            if not _declares_own_status(cls):
                continue
            label = f"{cls.__module__}.{cls.__name__}"
            status = cls.__dict__.get("http_status")
            code = cls.__dict__.get("response_code")
            if not any(name in cls.__dict__ for name in _CODE_DECLARATIONS):
                incomplete.append(f"{label} (states a status but no response_code)")
            if status is not None and not isinstance(status, int):
                incomplete.append(f"{label} (http_status is not an int)")
            if code is not None and not isinstance(code, str):
                incomplete.append(f"{label} (response_code is not a str)")
        assert not incomplete, f"incomplete HTTP mappings: {incomplete}"

    def test_declaring_a_mapping_twice_is_impossible(self) -> None:
        """There is exactly ONE source of truth, enforced by the type system.

        This replaces the old "a class must not also hold a table row" test.
        That invariant mattered while a core table existed and could silently
        win over the class attributes (Starlette picks the most specific
        registered class). Candidate 3 pass 6 deleted the table, so the failure
        mode is now unreachable rather than merely guarded — this test records
        *why* it is unreachable, and fails if a mapping table is reintroduced.
        """
        import src.app.core.errors as core_errors

        for name in ("build_exception_table", "_HandlerSpec", "_make_table_handler"):
            assert not hasattr(core_errors, name), (
                f"core.errors.{name} is back — a mapping table in core re-introduces "
                "the second source of truth candidate 3 removed, and with it the "
                "core -> modules import that the H-5 contract forbids"
            )

    def test_core_errors_imports_no_feature_module(self) -> None:
        """Candidate 3's central claim, pinned at unit speed.

        The H-5 import-linter contract enforces this in CI, but that gate needs
        import-linter installed and a graph build. This checks the same
        invariant in milliseconds against the module's own source, so a
        regression fails fast and locally. It is the durable replacement for
        the per-family "no longer a table row" assertions deleted in pass 6.
        """
        import ast
        import inspect

        import src.app.core.errors as core_errors

        tree = ast.parse(inspect.getsource(core_errors))
        forbidden = ("src.app.modules", "src.app.api", "src.app.middleware")
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue  # relative import: not a cross-layer edge
                imported = [node.module]
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            else:
                continue
            for name in imported:
                if any(name == pkg or name.startswith(f"{pkg}.") for pkg in forbidden):
                    offenders.append(f"line {node.lineno}: {name}")
        assert not offenders, (
            "core.errors imports a feature module — the H-5 layering violation "
            f"candidate 3 eliminated: {offenders}"
        )

    def test_preference_storage_error_declares_its_own_mapping(self) -> None:
        """P1-#4 renamed it off the 500 catch-all; pass 5 moved it off the table.

        It used to be the last feature-module row in core, which is exactly why
        ``core.errors`` imported ``modules.user_preferences.service``. The
        mapping now lives on the class, and the payload contract is unchanged.
        """
        from src.app.modules.user_preferences.service import PreferenceStorageError

        assert PreferenceStorageError.http_status == 400
        assert PreferenceStorageError.response_code == "INVALID_INPUT"
        exc = PreferenceStorageError("upload", "boom")
        assert exc.http_details() == {"operation": "upload", "reason": "boom"}

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
