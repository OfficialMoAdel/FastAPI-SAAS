"""Simulate an absent optional dependency, reproducibly.

Several modules in this codebase import their optional vendor SDK
function-locally so that importing the module never requires the SDK. The
guard those imports sit behind is then environment-dependent: on a machine
with the package installed the guard is inert, and a test that asserts the
guard fires will ``skip`` — which is how the most important assertions in the
storage suite came to be dead on exactly the machines they were written for.

``without_package()`` puts the interpreter into the *other* state on demand,
so both branches are exercised regardless of what is installed. Two
mechanisms are required together:

- deleting ``<package>`` from ``sys.modules``, because an already-imported
  module is served from that cache and never reaches the import machinery;
- a ``sys.meta_path`` finder that raises for it, because deleting the cache
  entry alone only forces a real (and successful) re-import from disk.

Raising ``ModuleNotFoundError`` from ``find_spec`` is what makes a genuinely
absent package indistinguishable from a simulated one — which is the whole
point, since the code under test must not be able to tell.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager


class _MissingModuleFinder:
    """Meta-path finder that refuses one top-level package and its children."""

    def __init__(self, package: str) -> None:
        self._package = package

    def _refuses(self, fullname: str) -> bool:
        return fullname == self._package or fullname.startswith(f"{self._package}.")

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if self._refuses(fullname):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None

    # Python still consults the legacy hook on some code paths; returning None
    # for everything keeps the finder a no-op there rather than a second veto.
    def find_module(self, fullname, path=None):  # noqa: ARG002
        return None


@contextmanager
def without_package(package: str) -> Iterator[None]:
    """Context manager: inside it, ``import package`` raises ModuleNotFoundError."""
    finder = _MissingModuleFinder(package)
    saved = {k: v for k, v in sys.modules.items() if k == package or k.startswith(f"{package}.")}
    for key in saved:
        del sys.modules[key]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


@contextmanager
def without_boto3() -> Iterator[None]:
    """``without_package("boto3")``, named for the one dependency under test."""
    with without_package("boto3"):
        yield


__all__ = ["without_boto3", "without_package"]
