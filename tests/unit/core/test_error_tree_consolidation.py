"""The error tree is one tree, and it still translates.

The frozen FastCRUD HTTP-exception shim under `core/exceptions/` was deleted
once its last importer migrated. The user guide used to teach it from six
files, and `core/AGENTS.md` contradicted itself about it.

Deleting a shim and discovering a broken mapping later would be a poor trade,
so what is pinned here is the REPLACEMENT path: every domain exception in
`shared/exceptions.py` still maps to the HTTP status the rewritten
documentation claims.

The "nothing references it any more" half of this bead is a text scan over the
markdown docs. It lives here rather than in a CI gate because the guide is
the one surface where a stale import is most costly: an agent or reader
following `from src.app.core.exceptions import HTTPException` writes code that
cannot import, and may then invent a path — which is how doc rot becomes
code rot.

The status table is asserted rather than derived from the classes, so a class
that silently changes `http_status` fails here instead of quietly changing an
API contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.app.shared.exceptions import (
    AppException,
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    TenantContextError,
    UnauthorizedError,
    UnprocessableRequestError,
    ValidationError,
)

# tests/unit/core/<this file> -> repo root is parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]

# Import paths of the exception modules that were deleted once their last
# importer migrated. A doc naming one of these teaches an import that raises
# ModuleNotFoundError, and an agent that hits that may invent a substitute
# path — the mechanism by which doc rot turns into architectural drift.
#
# ``core/exceptions/cache_exceptions.py`` is deliberately NOT listed: it
# survives for the Redis CacheException tree, which is a separate concern and
# is documented as live.
FORBIDDEN_REFERENCE_TOKENS = (
    "core.exceptions.http_exceptions",
    "core/exceptions/http_exceptions",
    "modules._base.exceptions",
    "modules/_base/exceptions",
    "_base.exceptions",
    "_base/exceptions",
)

# The status each domain error is documented and tested to return.
EXPECTED_STATUS = {
    ModelNotFoundError: 404,
    ValidationError: 400,
    DuplicateError: 409,
    ForbiddenError: 403,
    UnauthorizedError: 401,
    TenantContextError: 403,
    RateLimitExceededError: 429,
    UnprocessableRequestError: 422,
    ServiceUnavailableError: 503,
}


@pytest.mark.parametrize(("exc_type", "expected"), list(EXPECTED_STATUS.items()))
def test_domain_exception_maps_to_its_documented_status(
    exc_type: type[AppException], expected: int
) -> None:
    """Every domain error resolves to the status the docs promise.

    This is the counter-test for deleting the HTTP-shaped shim: the
    replacement path is proven, not assumed.
    """
    exc = exc_type("boom")
    assert exc.resolve_http_status() == expected


def test_every_domain_exception_is_translatable() -> None:
    """No `AppException` subclass is left without a status.

    The repo already enforces reachability in
    `tests/unit/core/test_exception_registration.py`, which walks every
    exception module's MRO and checks `cls.__dict__`. This is the narrower
    companion: it covers only `shared.exceptions`, the module the rewritten
    documentation tells readers to import from, so a status change there
    cannot slip past unnoticed even if that audit is ever loosened.
    """
    import src.app.shared.exceptions as shared

    undeclared = []
    for name in dir(shared):
        candidate = getattr(shared, name)
        if not isinstance(candidate, type) or not issubclass(candidate, AppException):
            continue
        if candidate is AppException:
            continue
        if not isinstance(getattr(candidate, "http_status", None), int):
            undeclared.append(name)

    assert undeclared == [], f"domain exceptions without an int http_status: {undeclared}"


def test_docs_teach_the_domain_tree() -> None:
    """The user guide points at `shared.exceptions`, not the dead module.

    The deletion was half the fix; the other half is that a reader following
    the docs reaches a real import.
    """
    guide = (REPO_ROOT / "docs" / "user-guide" / "api" / "exceptions.md").read_text(encoding="utf-8")
    assert "from src.app.shared.exceptions import" in guide

    # The deleted module's names are listed here, not read from a gate, so this
    # test stands alone: a deleted checker must not be able to delete the
    # assertion that the docs still teach a deleted module.
    stale = [token for token in FORBIDDEN_REFERENCE_TOKENS if token in guide]
    assert stale == [], f"the guide still teaches a deleted module: {stale}"


def test_docs_documented_statuses_match_the_code() -> None:
    """Each `### Name (status)` heading in the guide matches the real mapping.

    Documentation that says `ModelNotFoundError (404)` while the class returns
    500 is the exact drift this bead exists to stop.
    """
    guide = (REPO_ROOT / "docs" / "user-guide" / "api" / "exceptions.md").read_text(encoding="utf-8")
    headings = re.findall(r"^### (\w+) \((\d{3})\)$", guide, re.MULTILINE)
    assert headings, "no status headings found in the guide; the format changed?"

    by_name = {exc_type.__name__: status for exc_type, status in EXPECTED_STATUS.items()}
    mismatched = [
        (name, documented) for name, documented in headings if by_name.get(name) != int(documented)
    ]
    assert mismatched == [], f"guide headings disagree with the code: {mismatched}"
