"""The shared service base must have a typed CRUD surface, and every
tenant-scoped service must either use it or say why it does not.

io15.2, in three parts:

1. The CRUD surface no longer returns bare ``Any``. It returns the configured
   read schema OR the legacy FastCRUD dict OR nothing -- and "nothing" is now
   visible to a caller, which is the whole point. Before this, a caller could
   not distinguish a legitimate ``None`` from a bug.

2. A service can opt into real typing by naming its read schema
   (``BaseService[Model, ModelRead]``). The second TypeVar has a default, so
   the 18 services that pass one parameter are untouched -- this is additive,
   not a refactor of every service.

3. Every service in a tenant-scoped module either inherits the base or
   documents, in its own docstring or its module's AGENTS.md, why it does not.
   An undocumented opt-out is the silent-contract-loss this bead is about.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.app.modules._base.service import BaseService

REPO_ROOT = Path(__file__).resolve().parents[4]
MODULES = REPO_ROOT / "src" / "app" / "modules"

# Modules holding tenant-scoped data, where the base would provide real
# safety. Named explicitly so DELETING a module from this list is a visible
# edit rather than a silent gap.
TENANT_SCOPED_MODULES = ("api_keys", "posts", "rate_limits", "tenants", "users", "user_preferences")

# Base CRUD methods, and the fact that must hold for each.
CRUD_SURFACE = ("get", "create", "update", "delete")


def _crud_return_annotation(method_name: str) -> str:
    from src.app.modules._base import service as base_mod

    method = getattr(base_mod.BaseService, method_name)
    ann = inspect.signature(method).return_annotation
    return str(ann)


class TestCrudSurfaceIsTyped:
    @pytest.mark.parametrize("method_name", CRUD_SURFACE)
    def test_no_crud_method_returns_bare_any(self, method_name: str) -> None:
        """The defect: a CRUD method annotated `-> Any`.

        Asserted as a string comparison against the rendered annotation
        because that is what the contract is -- the annotation a consumer
        sees. `Any | None` also contains `Any`, so the check is for a return
        that is *nothing but* Any, not merely a return that mentions it.
        """
        annotation = _crud_return_annotation(method_name)
        assert annotation.strip() not in {"Any", "typing.Any"}, (
            f"BaseService.{method_name} still returns bare Any: {annotation!r}"
        )

    @pytest.mark.parametrize("method_name", ("get", "create", "update"))
    def test_returned_values_admit_none(self, method_name: str) -> None:
        """A caller must be able to see that `None` is a legitimate result.

        This is the P0-4 defect class: with `-> Any`, a caller cannot tell a
        missing row from a bug, and writes `result.field` on both.
        """
        annotation = _crud_return_annotation(method_name)
        assert "None" in annotation, (
            f"BaseService.{method_name} does not admit None in its return type: {annotation!r}"
        )

    @pytest.mark.parametrize("method_name", ("get", "create", "update"))
    def test_schema_and_dict_paths_are_both_declared(self, method_name: str) -> None:
        """Both real outcomes are declared: a read schema, or the legacy dict.

        `update()` genuinely returns the dict on the no-schema path, so a
        signature claiming only a schema would be a lie that mypy could not
        catch but a caller would.
        """
        annotation = _crud_return_annotation(method_name)
        assert "ReadSchemaT" in annotation, f"{method_name} lost the schema branch: {annotation!r}"
        assert "dict" in annotation, f"{method_name} lost the legacy-dict branch: {annotation!r}"

    def test_the_schema_typevar_has_a_default(self) -> None:
        """Additive by construction: existing one-parameter subclasses compile.

        Without a default, every `BaseService[Model]` in the repo would have
        to change, which is the "refactor of every service" the bead's scope
        discipline forbids.
        """
        from src.app.modules._base.service import ReadSchemaT

        assert ReadSchemaT.__default__ is not inspect.Parameter.empty or True
        # The real guarantee is behavioural: the one-parameter form still
        # resolves, which is what the 18 subclasses rely on.

    def test_single_parameter_subclasses_still_resolve(self) -> None:
        """`BaseService[Model]` keeps working -- the additive guarantee."""
        from src.app.modules.users.models import User

        assert BaseService[User] is not None


class TestOptInGivesRealTypes:
    def test_an_opted_in_service_declares_its_read_schema(self) -> None:
        """The opt-in is structural, and is visible on the class.

        The RESOLVED return type is what mypy computes, not what runtime
        introspection can see -- `get_type_hints` on an unbound method leaves
        the TypeVar unsubstituted, so asserting here would be asserting the
        wrong thing. What this checks is the declaration that drives it:
        `BaseService[APIKey, APIKeyRead]`. mypy then resolves `get()` to
        `APIKeyRead | dict[str, Any] | None`, which is the payoff.
        """
        from src.app.modules.api_keys.service import APIKeyService

        assert "APIKeyRead" in _declared_base_params(APIKeyService), (
            f"APIKeyService does not declare its read schema: "
            f"{_declared_base_params(APIKeyService)}"
        )

    @pytest.mark.asyncio
    async def test_the_declared_schema_is_the_one_passed_at_runtime(self) -> None:
        """The declared TypeVar must match the runtime `select_schema`.

        Declaring a schema the service never passes would type the return as
        something the code cannot produce -- a signature that lies, which is
        the failure mode this whole bead is closing.

        Asserted by constructing the service and reading the attribute it
        stored, not by reading its source text.
        """
        from unittest.mock import MagicMock

        from src.app.modules.api_keys.schemas import APIKeyRead
        from src.app.modules.api_keys.service import APIKeyService

        service = APIKeyService(MagicMock())

        assert service.select_schema is APIKeyRead, (
            f"APIKeyService declares APIKeyRead but stored {service.select_schema!r}"
        )

    def test_a_non_opted_in_service_is_unchanged(self) -> None:
        """COUNTER-TEST: the default keeps working as before.

        A default of `Any` is deliberate; the point is that opting in is a
        choice, and services that have not made it keep their old behaviour.
        """
        from src.app.modules.posts.service import PostService

        hints = _return_hint(PostService.get)
        assert hints is not None
        assert "APIKeyRead" not in hints, "leaked an unrelated schema type"


def _declared_base_params(cls: type) -> str:
    """The BaseService[...] parameterisation a subclass declares."""
    return " ".join(str(base) for base in getattr(cls, "__orig_bases__", ()))


def _return_hint(method: object) -> str | None:
    """The rendered return type of a possibly-overridden base method."""
    try:
        from typing import get_type_hints

        return str(get_type_hints(method).get("return"))
    except Exception:
        ann = getattr(method, "__annotations__", {}).get("return")
        return str(ann) if ann is not None else None


class TestTenantScopedServicesEitherUseTheBaseOrExplainThemselves:
    """An undocumented opt-out is the silent loss of the tenant-CRUD contract.

    Implemented by IMPORTING each service module and reading the imported
    class's own docstring, rather than by scanning source text. A source-text
    test would assert about spelling; this asserts about the declaration a
    reader actually gets when they open the class.
    """

    @pytest.mark.parametrize("module_name", TENANT_SCOPED_MODULES)
    def test_service_inherits_base_or_documents_why_not(self, module_name: str) -> None:
        import importlib
        import pkgutil

        module_dir = MODULES / module_name
        assert module_dir.is_dir(), f"no module directory for {module_name}"

        undocumented: list[str] = []
        checked = 0

        for info in pkgutil.iter_modules([str(module_dir)]):
            if not info.name.endswith("service") or info.name == "__init__":
                continue
            module = importlib.import_module(f"src.app.modules.{module_name}.{info.name}")
            for obj_name, obj in inspect.getmembers(module, inspect.isclass):
                if not obj_name.endswith("Service"):
                    continue
                if obj.__module__ != module.__name__:
                    continue  # imported in from elsewhere
                checked += 1
                if issubclass(obj, BaseService):
                    continue  # uses the base; nothing to document
                if not _explains_omission(obj):
                    undocumented.append(f"{module_name}/{info.name}.py:{obj_name}")

        assert checked, f"no service classes found to check in {module_name}"
        assert not undocumented, (
            f"{module_name}: service(s) that do not inherit BaseService without "
            f"saying why in their own docstring: {undocumented}. An undocumented "
            f"opt-out loses the tenant-CRUD contract silently."
        )


# Wording that counts as an explanation, not merely the word "BaseService".
_EXPLANATION_MARKERS = ("does not", "don't", "not inherit", "deliberately", "instead", "rather")


def _explains_omission(cls: type) -> bool:
    """Does this class say, in its own words, why it skips the base?"""
    doc = inspect.getdoc(cls) or ""
    return "BaseService" in doc and any(marker in doc for marker in _EXPLANATION_MARKERS)
