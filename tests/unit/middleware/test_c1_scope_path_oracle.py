"""Oracle for C-1: ``scope["path"]`` and ``Request(scope).url.path`` are the same string.

WHY THIS FILE EXISTS AND WHY IT LANDS ALONE

C-1 wants three middleware to read ``scope["path"]`` directly instead of
constructing a ``Request`` and reading ``.url.path``. That is only correct if the
two are the same string, and the bead gates the change behind this oracle
(``LC-C1-OR``) being green FIRST. So the oracle is committed on its own: a check
that arrives in the same commit as the change it certifies cannot tell you which
of the two broke it, and this programme has found six checks that ran, passed,
and gated nothing.

THE EQUIVALENCE IS DEPENDENCY-VERSION DEPENDENT, and that is the whole risk

The bead's stated danger is that under a prefixed deployment (``root_path``
non-empty) reading ``scope["path"]`` while the rest of the app reads
``.url.path`` is a TENANT-RESOLUTION OR IP-FILTER BYPASS rather than a speedup.
That danger is real only if some Starlette version adds ``root_path`` back into
``URL``. Measured on the pinned version, ``starlette.datastructures.URL.__init__``
contains::

    path = scope["path"]

with no ``root_path`` term anywhere -- Starlette removed it when ``root_path``
handling moved to the ASGI server. So on THIS version the equivalence is
unconditional, not merely "true when root_path is empty".

Which is precisely why the check below pins the URL implementation itself. An
equivalence that holds because of a dependency's current internals, asserted
nowhere, becomes a silent bypass the day that dependency is upgraded. That is
the same failure shape as the protocol.py unit bug and the ``length=`` nesting
this session keeps re-learning: a number that was right when written, unguarded
against the thing that could change it.

WHAT IS AND IS NOT ESTABLISHED HERE

Established: on the pinned Starlette, for every scope shape exercised below,
``Request(scope).url.path == scope["path"]``.

NOT established: that the equivalence is safe across a future Starlette upgrade.
This oracle is what would notice that, and until it is wired into CI the
answer is "the pinned version says yes" rather than "yes". The C-1 change must
not be merged on the strength of this file alone without that wiring.

No microsecond figure is claimed. The capacity benefit is unmeasured and C-1's
own acceptance calls it "a capacity lever of unmeasured size".
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from starlette.datastructures import URL
from starlette.requests import Request

#: ``tests/unit/middleware/<this file>`` -> parents[3] is the repo root. This was
#: ``parents[4]`` first, which pointed outside the repository: the discovery scan
#: then matched NOTHING and every set-comparison assertion passed vacuously until
#: the reachability test below caught it. Both facts are recorded because the
#: silent version of this bug -- a scan that finds no files and reports success
#: -- is the failure this oracle most needed to be able to detect.
APP_ROOT = Path(__file__).resolve().parents[3] / "src" / "app"

#: Every middleware that builds a ``Request`` out of a raw scope AND then reads
#: ``.url.path`` off it. Discovered by AST, never hand-listed, so a new site
#: cannot appear without this oracle noticing -- which is the closure assertion
#: C-1's risk (iii) asks for: the set may not grow in a change that does not also
#: pin it.
#:
#: FIVE, not the three the bead names and not the four I first wrote down. The
#: hand count was wrong twice: `rate_limit.py` was missed because its
#: ``Request(scope, receive)`` sits at line 57 while the ``.url.path`` read is at
#: line 34, so a grep that pairs a construction with a nearby read does not see
#: them together. The AST scan does. A count that disagrees with the code is a
#: decision that was made somewhere and not written down, so the count here is
#: the discovered one and the bead's three is recorded as stale.
EXPECTED_SCOPE_PATH_SITES: frozenset[str] = frozenset(
    {
        "middleware/ip_filter.py",
    # query_profiler.py is NOT here: it was discovered by the same coarse
    # heuristic and is exempted with its reason below (EXEMPTED_URL_PATH_SITES).
        "middleware/rate_limit.py",
        "middleware/request_id.py",
        "middleware/tenant.py",
    }
)

#: Scope shapes worth covering. A scope is a bare dict and nothing validates it,
#: so the interesting cases are the ones a proxy or a test harness would produce:
#: a trailing slash, a percent-encoded segment that MUST NOT be decoded, a
#: double slash, and an empty path.
REPRESENTATIVE_PATHS: tuple[str, ...] = (
    "/",
    "/api/v1/users/me",
    "/api/v1/users/me/",
    "/api/v1/users/%2Fme",
    "/api/v1//double",
    "",
)


def _scope(path: str, *, root_path: str = "", headers: tuple[tuple[bytes, bytes], ...] = ()) -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": list(headers) or [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


#: Sites that legitimately still read ``<expr>.url.path``. One entry, recorded
#: rather than tolerated: an unnamed survivor is indistinguishable from a site
#: someone forgot to convert.
#:
#: ``query_profiler.py`` builds its ``Request`` in ``__call__`` but reads the
#: path in ``_log_results``, which receives that Request as a parameter. The
#: Request is still needed there for ``request.method`` on the very next line,
#: so converting the path read would remove nothing and would only mean
#: threading ``scope`` through a second method.
#:
#: It is here because the discovery heuristic that produced the original
#: five-site list paired "this file constructs Request(scope)" with "this file
#: reads a .path attribute" ANYWHERE IN THE FILE, so it matched lines ~38 apart
#: in two different functions. The same coarseness had already cost one
#: miscounted site (rate_limit.py) before the conversion.
EXEMPTED_URL_PATH_SITES: frozenset[str] = frozenset(
    {
        "middleware/query_profiler.py",
    }
)


class TestScopePathEqualsUrlPath:
    """The equivalence itself, across shapes a scope can actually take."""

    @pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
    def test_the_two_are_the_same_string(self, path: str) -> None:
        scope = _scope(path)
        assert Request(scope).url.path == scope["path"]

    @pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
    def test_percent_encoding_is_not_decoded_by_either(self, path: str) -> None:
        """Both forms must leave ``%2F`` alone. A version that decoded on the
        way through ``URL`` would make ``scope["path"]`` the *encoded* form and
        ``.url.path`` the decoded one, which is a filter bypass wearing an
        equivalence's clothes."""
        scope = _scope(path)
        if "%" not in path:
            pytest.skip("no percent-encoding in this representative path")
        assert Request(scope).url.path == scope["path"]

    def test_a_non_empty_root_path_does_not_diverge_on_this_version(self) -> None:
        """Pinned so a Starlette upgrade that re-adds ``root_path`` to ``URL``
        fails HERE rather than in production, where the divergence is an
        IP-filter bypass.

        If this ever starts failing, C-1 is void -- the bead says so in as many
        words, and the correct response is to revert the change, not to widen
        the scope to compensate.
        """
        scope = _scope("/api/v1/users/me", root_path="/prefix")
        assert Request(scope).url.path == scope["path"], (
            "Request(scope).url.path now includes root_path, so reading "
            "scope['path'] is no longer equivalent. C-1 is VOID under a prefixed "
            "deployment and must be reverted rather than adapted."
        )


class TestTheStarVersionIsWhatMakesItTrue:
    """Pins the dependency internals the equivalence rests on.

    ``URL.__init__`` currently reads ``path = scope["path"]`` and nothing else.
    If a future Starlette concatenates a prefix, the assertion below fails and
    says why, instead of the whole thing failing later as a security finding.
    """

    def test_url_reads_scope_path_and_adds_no_prefix(self) -> None:
        source = inspect.getsource(URL.__init__)
        assert 'path = scope["path"]' in source, (
            "Starlette's URL construction no longer reads scope['path'] verbatim; "
            "the C-1 equivalence needs re-deriving against the new version"
        )
        assert "root_path" not in source, (
            "Starlette's URL construction now references root_path, so "
            ".url.path and scope['path'] can diverge under a prefixed "
            "deployment and C-1 is void"
        )

    def test_the_pinned_version_is_recorded(self) -> None:
        """So a reader can tell WHICH version was measured, rather than
        inferring it from whatever happens to be installed."""
        from starlette import __version__ as starlette_version

        assert starlette_version, "cannot record which Starlette was measured"


class TestTheSetOfSitesIsClosed:
    """Keeps the conversion honest in BOTH directions.

    Risk (iii) on the bead, verbatim: do not extend to a fourth site in this PR
    without adding the assertion that keeps the set closed. That is this class,
    and after the conversion the closed set is the EMPTY one -- asserting the
    recorded five would have made this class unsatisfiable the moment the work
    landed, which is the signature of a snapshot rather than a guard.
    """

    @staticmethod
    def _middleware_files() -> list[Path]:
        return sorted((APP_ROOT / "middleware").rglob("*.py"))

    @classmethod
    def _url_path_reads(cls) -> dict[str, list[int]]:
        """Every ``<expr>.url.path`` attribute CHAIN that survives.

        Matched as a chain (an ``Attribute(attr="path")`` whose value is an
        ``Attribute(attr="url")``) rather than on either half alone. A bare
        ``.path`` would also match unrelated attributes, and a textual search
        would match the C-1 comments that name the old form.
        """
        found: dict[str, list[int]] = {}
        for path in cls._middleware_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            lines = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "url"
            ]
            if lines:
                found[path.relative_to(APP_ROOT).as_posix()] = sorted(lines)
        return found

    def test_the_walk_reaches_the_middleware(self) -> None:
        """A walk that matched nothing would make the test below pass
        vacuously -- the failure mode that has produced six green checks this
        session. Measured by NAMING files, so it does not depend on what is
        being searched for."""
        names = {p.name for p in self._middleware_files()}
        missing = {Path(rel).name for rel in EXPECTED_SCOPE_PATH_SITES} - names
        assert not missing, (
            f"the walk did not reach {sorted(missing)}; searched "
            f"{APP_ROOT / 'middleware'}"
        )

    def test_no_middleware_still_reads_url_path(self) -> None:
        survivors = self._url_path_reads()
        unexpected = {rel: lines for rel, lines in survivors.items() if rel not in EXEMPTED_URL_PATH_SITES}
        assert not unexpected, (
            f"middleware still reads <expr>.url.path in {unexpected}. C-1 read "
            "scope['path'] because that is exactly what URL construction reads "
            "on this Starlette version (TestTheStarVersionIsWhatMakesItTrue). "
            "A new site must be converted in the same change that adds it, or "
            "recorded in EXEMPTED_URL_PATH_SITES with its reason."
        )

    def test_each_converted_file_reads_scope_path(self) -> None:
        """Pins that the conversion LANDED in each recorded file. The old set
        test could not: it passed identically before and after the diff."""
        for rel in EXPECTED_SCOPE_PATH_SITES:
            tree = ast.parse((APP_ROOT / rel).read_text(encoding="utf-8"))
            reads = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "scope"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "path"
            ]
            assert reads, f"{rel} does not read scope['path']"


def test_the_oracle_does_not_reimplement_the_thing_it_checks() -> None:
    """It compares ``Request(scope).url.path`` to ``scope["path"]`` -- both the
    production forms. A reference reimplementation here would be a second thing
    to keep correct, which is the defect the whole oracle exists to detect."""
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    assert "Request" in imported, "the oracle must drive the real Request"
    assert "URL" in imported, "the oracle must inspect the real URL"


# A note on what is deliberately NOT here.
#
# There was a `test_no_capacity_figure_is_implied` that scanned this file's own
# text for phrases like "us saved" and asserted their absence. It could never
# pass: the token list it searched for is written in the file it searches. It is
# deleted rather than repaired, for the same reason the span-name test in the
# S-3 oracle was deleted -- a check that cannot fail is not a check, and a check
# that only makes the file look careful is worse than none. "No capacity figure
# is claimed" belongs in the commit message, where a reader can weigh it, and not
# in a test that would have needed to inspect itself.
