"""Unit test for the shared AST import-edge helper.

The helper underpins every import-edge assertion in the suite, so a bug in it
inverts the meaning of those tests: an assertion built on a helper that
reports no edges passes for the wrong reason, forever. This file runs the
helper against SYNTHETIC modules so its correctness is established before any
production assertion trusts it — including the resolutions that are easy to
get wrong (``from pkg import submodule``, relative imports, TYPE_CHECKING
blocks, function-local imports).

The relative-import expectations below were checked against the REAL import
system: the same statements were written into a package on disk and executed,
and the resulting ImportError is what pinned the expected dotted names.
"""

from pathlib import Path

import pytest

from tests.helpers.import_edges import (
    dotted_module_name,
    imports_in_source,
    registered_names,
)


class TestPlainImports:
    def test_absolute_import(self) -> None:
        assert imports_in_source("import os") == {"os"}
        assert imports_in_source("import a.b.c") == {"a.b.c"}

    def test_from_import_names_the_submodule(self) -> None:
        """``from a.b import c`` loads ``a.b.c`` — the module Python loads."""
        assert imports_in_source("from a.b import c") == {"a.b.c"}

    def test_multiple_aliases_each_become_a_target(self) -> None:
        assert imports_in_source("from pkg import a, b") == {"pkg.a", "pkg.b"}

    def test_star_import_is_the_bare_package(self) -> None:
        assert imports_in_source("from pkg import *") == {"pkg"}


class TestNestedAndConditionalImports:
    def test_import_inside_a_function_is_still_an_edge(self) -> None:
        """A lazy import is a real edge; the helper must not scope to module top."""
        src = """
def f():
    from lazy.thing import helper
    return helper
"""
        assert imports_in_source(src) == {"lazy.thing.helper"}

    def test_type_checking_block_is_an_edge(self) -> None:
        """TYPE_CHECKING imports still describe the module's dependencies."""
        src = """
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from types import Only
"""
        assert "types.Only" in imports_in_source(src)

    def test_try_except_import_is_an_edge(self) -> None:
        src = """
try:
    from new.dep import thing
except ImportError:
    from old.dep import thing
"""
        assert imports_in_source(src) == {"new.dep.thing", "old.dep.thing"}


class TestRelativeImports:
    def test_dot_import_resolves_against_the_current_package(self) -> None:
        """``from . import x`` in ``pkg.sub.mod`` loads ``pkg.sub.x``.

        A single dot means the CURRENT package, not the parent. Pinned by
        executing the same statement in a real package: it raised
        "cannot import name 'x' from 'pkg.sub'" when ``x`` lived in ``pkg``.
        """
        assert imports_in_source("from . import x", module="pkg.sub.mod") == {"pkg.sub.x"}

    def test_dot_module_resolves_against_the_current_package(self) -> None:
        assert imports_in_source("from .sibling import thing", module="pkg.sub.mod") == {
            "pkg.sub.sibling.thing"
        }

    def test_double_dot_climbs_one_level(self) -> None:
        """``from .. import x`` in ``pkg.sub.mod`` loads ``pkg.x``."""
        assert imports_in_source("from .. import x", module="pkg.sub.mod") == {"pkg.x"}

    def test_relative_import_without_module_name_keeps_the_bare_name(self) -> None:
        """With no module context it cannot climb, so it does not invent a package."""
        assert imports_in_source("from . import x") == {"x"}


class TestDottedModuleName:
    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("app/core/tenancy/context.py", "app.core.tenancy.context"),
            ("app/core/tenancy/__init__.py", "app.core.tenancy"),
            ("app/main.py", "app.main"),
        ],
    )
    def test_maps_paths_to_dotted_names(self, tmp_path: Path, rel: str, expected: str) -> None:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        assert dotted_module_name(target, tmp_path) == expected


class TestRegisteredNames:
    def test_reads_list_and_dict(self, tmp_path: Path) -> None:
        mod = tmp_path / "reg.py"
        mod.write_text(
            'ARQ_FUNCTIONS = ["a_task", "b_task"]\nARQ_CRONJOBS = {"0 * * * *": "nightly"}\n',
            encoding="utf-8",
        )
        assert registered_names(mod, "ARQ_FUNCTIONS") == {"a_task", "b_task"}
        assert registered_names(mod, "ARQ_CRONJOBS") == {"0 * * * *"}

    def test_reads_annotated_assignment(self, tmp_path: Path) -> None:
        mod = tmp_path / "reg2.py"
        mod.write_text('ARQ_FUNCTIONS: list = ["only"]\n', encoding="utf-8")
        assert registered_names(mod, "ARQ_FUNCTIONS") == {"only"}

    def test_dotted_references_are_unparsed(self, tmp_path: Path) -> None:
        """A registration may name the function by reference, not literal."""
        mod = tmp_path / "reg3.py"
        mod.write_text("ARQ_FUNCTIONS = [pkg.mod.task]\n", encoding="utf-8")
        assert registered_names(mod, "ARQ_FUNCTIONS") == {"pkg.mod.task"}

    def test_absent_constant_is_empty(self, tmp_path: Path) -> None:
        mod = tmp_path / "reg4.py"
        mod.write_text("OTHER = [1]\n", encoding="utf-8")
        assert registered_names(mod, "ARQ_FUNCTIONS") == set()


class TestFalsifiability:
    """The helper must be able to say NO — one that always says yes is useless."""

    def test_absent_import_is_absent(self) -> None:
        assert imports_in_source("x = 1\ny = 2\n") == set()

    def test_removing_an_edge_removes_it_from_the_result(self) -> None:
        """The property a real assertion depends on: delete the line, lose the edge."""
        with_edge = imports_in_source("import a.b\n")
        without_edge = imports_in_source("")
        assert "a.b" in with_edge
        assert "a.b" not in without_edge

    def test_similar_target_does_not_false_match(self) -> None:
        """``a.bc`` must not satisfy an assertion about ``a.b``."""
        found = imports_in_source("import a.bc\n")
        assert "a.b" not in found
        assert "a.bc" in found
