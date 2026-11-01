"""The module generator must emit code that passes the repo's own gates.

WHY THIS FILE EXISTS

``src/scripts/templates/`` is the product of a boilerplate: every feature module
an operator adds starts as its output. It had drifted from ``BaseService`` in
three ways at once, and nothing caught it because no test ever rendered the
templates and checked the result:

1. ``service.py.j2``'s list method called ``get_multi`` (returns a **list**)
   while declaring ``-> dict[str, Any]``. ``router.py.j2`` then called
   ``result.get("data")`` on that list, so every generated list endpoint raised
   ``AttributeError`` on its first request.
2. ``create_*`` / ``update_*`` returned ``BaseService``'s honest union
   (``ReadSchemaT | dict | None``) where the template declared
   ``{{ ModelName }}Read`` — two more mypy errors per generated module.
3. The get endpoint reached through to ``service.crud.get(...)`` with a
   ``# type: ignore[arg-type]``, duplicating the read contract in the HTTP layer
   and leaving ``BaseService.get`` — the canonical read — with zero callers.

Measured before the fix: ``mypy src/app/modules/<generated>/`` reported 3 errors
and the list endpoint crashed at runtime. Both are pinned below.

The last test is the real gate: it generates a module and runs ``mypy`` on it,
because a template can look right and still produce code that fails the type
check the repo's Definition of Done requires.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "src" / "scripts" / "templates"
MODULES_DIR = REPO_ROOT / "src" / "app" / "modules"

#: Prefix for the throwaway package the mypy gate generates.
PROBE_PREFIX = "zz_gate_"


@pytest.fixture(autouse=True, scope="module")
def _sweep_stale_probe_modules():
    """Remove probe packages left behind by an INTERRUPTED run.

    The gate generates a real package inside ``src/app/modules`` (mypy has to
    resolve ``src.app.modules.<name>``) and deletes it in a ``finally``. A
    ``finally`` cannot run when the process is killed — a cancelled CI job, a
    timeout, ``pytest -x`` interrupted mid-test — so a stale probe can survive
    into the next run, where it counts as a real module to every other gate
    (module structure, doc paths, ``mypy src/``).

    Sweeping by prefix at module setup makes the gate self-healing: an
    interrupted run cannot poison the tree, and no unrelated test has to know
    this probe exists.
    """
    for stale in MODULES_DIR.glob(f"{PROBE_PREFIX}*"):
        shutil.rmtree(stale, ignore_errors=True)
    yield
    for stale in MODULES_DIR.glob(f"{PROBE_PREFIX}*"):
        shutil.rmtree(stale, ignore_errors=True)


def _template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


class TestTheListEndpointShapeMatches:
    """Service returns a dict, router reads a dict. They must agree."""

    def test_the_service_list_uses_the_paginated_dict_fetch(self) -> None:
        """``get_multi`` returns a list; ``get_page_with_total`` returns the dict.

        This is the exact mismatch that made generated list endpoints crash.
        """
        service = _template("service.py.j2")

        assert "self.get_page_with_total(" in service, (
            "the list method must use the paginated dict fetch; get_multi "
            "returns a list that the router then calls .get('data') on"
        )
        assert "return await self.get_multi(" not in service, (
            "get_multi returns a list, which contradicts the declared -> dict"
        )

    def test_the_list_method_declares_the_dict_it_returns(self) -> None:
        service = _template("service.py.j2")
        assert ") -> dict[str, Any]:" in service

    def test_the_list_method_keeps_the_pagination_clamps(self) -> None:
        """``get_page_with_total`` bypasses ``get_multi``'s guards.

        ``get_multi`` clamps page/per_page and caps per_page at MAX_PER_PAGE.
        Routing the template through ``get_page_with_total`` therefore has to
        carry the caps across, or a generated endpoint gains a free way to ask
        for the whole table (and a deep OFFSET is O(offset) in PostgreSQL).

        The caps are carried by ``PaginationParams`` — the repo's ONE policy —
        rather than by hand. The hand-rolled clamps this test used to pin
        bounded ``items_per_page`` only, so ``?page=1000000000`` still issued an
        OFFSET of ~2e10 and bypassed the repo-wide ``_MAX_OFFSET`` ceiling. The
        policy floors page/per_page, caps per_page, AND rejects a deep offset.
        """
        service = _template("service.py.j2")

        assert "from src.app.core.pagination import DEFAULT_PER_PAGE, PaginationParams" in service
        assert "pagination = PaginationParams(page=page, per_page=items_per_page)" in service
        assert "offset=pagination.offset," in service
        assert "limit=pagination.limit," in service

    def test_the_list_method_orders_the_page_deterministically(self) -> None:
        """Without an ORDER BY, consecutive pages may duplicate or skip rows.

        ``fetch_page_with_total`` forwards ``sort_columns`` to ``crud.select``,
        which emits no ``ORDER BY`` at all when it is None — so PostgreSQL is
        free to return rows in any order per query and the router's
        ``has_next`` stops meaning anything.
        """
        service = _template("service.py.j2")

        assert 'sort_columns=["created_at", "id"],' in service
        assert 'sort_orders=["desc", "desc"],' in service

    def test_the_router_builds_the_envelope_from_the_effective_page_size(self) -> None:
        """Echoing the requested params makes the envelope lie when they clamp.

        ``?per_page=500`` returned MAX_PER_PAGE rows while reporting
        ``per_page=500``, and ``has_next`` then said False for a page with rows
        still behind it. The dependency's values are the effective ones.
        """
        router = _template("router.py.j2")

        assert "pagination: Annotated[PaginationParams, Depends(get_pagination_params)]" in router
        assert '"page": pagination.page,' in router
        assert '"per_page": pagination.per_page,' in router
        assert "pagination.page * pagination.per_page < total" in router

    def test_the_router_reads_the_dict_keys_the_service_provides(self) -> None:
        router = _template("router.py.j2")
        assert 'result.get("data", [])' in router
        assert 'result.get("total_count", 0)' in router


class TestTheTypedReturnsAreNarrowed:
    """``select_schema`` is always configured, so the union collapses."""

    def test_create_and_update_cast_the_union(self) -> None:
        service = _template("service.py.j2")

        assert 'cast("{{ ModelName }}Read", await self.create(' in service, (
            "create returns ReadSchemaT | dict | None; the generated module must "
            "narrow it or fail mypy"
        )
        assert 'cast("{{ ModelName }}Read", await self.update(' in service, (
            "update returns ReadSchemaT | dict | None; the generated module must "
            "narrow it or fail mypy"
        )

    def test_the_cast_is_imported(self) -> None:
        service = _template("service.py.j2")
        assert "from typing import Any, cast" in service


class TestTheCanonicalReadHasACaller:
    """``BaseService.get`` is the canonical read; the router should use it."""

    def test_the_get_endpoint_uses_the_service_read(self) -> None:
        router = _template("router.py.j2")

        assert "await service.get(id)" in router, (
            "the get endpoint must call the canonical read rather than "
            "re-implementing it against service.crud"
        )

    def test_the_get_endpoint_no_longer_reaches_through_to_crud(self) -> None:
        router = _template("router.py.j2")

        # Match the STATEMENT forms, not prose: a comment that mentions the old
        # shape must not trip the gate (and must not have to be reworded to
        # avoid tripping it).
        assert "await service.crud.get(" not in router, (
            "the HTTP layer must not duplicate the read contract"
        )
        assert "schema_to_select={{ ModelName }}Read," not in router, (
            "the get endpoint must not re-declare the select schema"
        )
        assert "type: ignore[arg-type]" not in router, (
            "a reach-through that needs a type: ignore is the signal to use the seam"
        )

    def test_the_get_endpoint_still_404s_on_a_missing_row(self) -> None:
        """Narrowing must not drop the not-found branch."""
        router = _template("router.py.j2")
        assert "if result is None:" in router
        assert 'raise ModelNotFoundError("{{ ModelName }}", id)' in router


class TestTheServiceFileStaysDependsFree:
    """The DI factory belongs to the router, where the session dep is resolvable."""

    def test_the_service_template_emits_no_factory(self) -> None:
        service = _template("service.py.j2")

        assert "get_" not in service or "def get_{{ module_name }}_service" not in service, (
            "the DI factory must live in router.py; service.py must not "
            "re-export a factory the router wraps in Depends"
        )
        assert "Depends" not in service, (
            "service.py must stay free of FastAPI imports"
        )

    def test_the_router_hosts_the_factory_with_the_session_dep(self) -> None:
        router = _template("router.py.j2")

        assert "def get_{{ module_name }}_service(" in router, (
            "the factory must be defined in router.py"
        )
        assert "Depends(async_get_db)" in router, (
            "the factory must take the session through Depends(async_get_db) "
            "instead of a lambda-default Depends"
        )
        assert "Depends(get_{{ module_name }}_service)" in router, (
            "the ServiceDep alias must reference the named factory"
        )
        assert "Depends(lambda" not in router, (
            "a lambda-default dependency hides the session from FastAPI's "
            "dependency graph and defeats override_for"
        )


class TestTheTemplatesSurviveAMypyRun:
    """The end-to-end gate: generate a real module, then type-check it.

    Structural assertions above can all pass while the *combination* still
    fails to type-check. This is the only test that proves the output is
    acceptable to the gate the repo actually runs.
    """

    @pytest.mark.slow
    def test_a_generated_module_passes_mypy(self) -> None:
        from src.scripts.create_module import create_module_context, generate_module

        # Unique name: this writes inside the package tree (mypy must resolve
        # `src.app.modules.<name>`), so two concurrent runs must not collide.
        module_name = f"{PROBE_PREFIX}{uuid4().hex[:8]}"
        target = MODULES_DIR / module_name

        try:
            context = create_module_context(module_name, "name:str")
            generate_module(context, output_dir=str(target))

            assert target.exists(), "the generator produced no module"

            result = subprocess.run(
                [sys.executable, "-m", "mypy", str(target)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=240,
                env={**os.environ, "MYPY_CACHE_DIR": str(target / ".mypy_cache")},
            )

            assert result.returncode == 0, (
                "a generated module must pass the repo's own mypy gate.\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        finally:
            shutil.rmtree(target, ignore_errors=True)

        assert not target.exists(), "the generated probe module leaked into the tree"
