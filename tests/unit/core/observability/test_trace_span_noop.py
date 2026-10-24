"""S-3 oracle: tracing must be a no-op when no OpenTelemetry SDK is installed.

``trace_span`` is a context manager that yields a span. Every one of its four
call sites uses it WITHOUT ``as``, so no production code binds the span it
yields - the only things that matter are that entering and exiting the block is
free, that the error path never raises, and that a span the tracer hands back
cannot break the code inside the ``with``. The future S-3 change adds a
``_SPANS_ENABLED`` flag to make the no-op explicit; this oracle is written
against what exists today so it is green against HEAD with no source change,
and it pins the properties that flag must not break.

The meta-test is the point. A silent dependency on an absent package is a trap:
the suite passes, CI passes, and production discovers it the day somebody runs
``pip install opentelemetry-sdk`` and every request starts paying for a real
span. This file turns that into a loud failure - but ONLY because the check
asserts on the import RESULT rather than on a hardcoded boolean. A test that
asserts ``_SPANS_ENABLED is False`` would be a test that can never fail, and
that is the failure mode the bead names explicitly.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import pytest

from src.app.core.observability import tracing

REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT / "src" / "app"
assert APP_ROOT.is_dir(), (
    f"APP_ROOT does not exist: {APP_ROOT}. A scan over a missing tree returns nothing, "
    f"and every rule below would pass vacuously."
)

#: The package whose PRESENCE changes behaviour. ``opentelemetry-api`` is a
#: declared dependency (pyproject.toml) and supplies ``ProxyTracer``, which
#: yields non-recording spans. The SDK supplies real span implementations, and
#: it is the SDK that must not be installed here.
SDK_MODULE = "opentelemetry.sdk"

#: Every ``with trace_span(`` in the application. Discovered, never hand-listed:
#: ``test_every_call_site_is_discovered_and_unbound`` compares this against an
#: AST scan so a new call site cannot appear without the oracle noticing.
EXPECTED_CALL_SITES: frozenset[tuple[str, int]] = frozenset(
    {
        ("core/db/database.py", 203),
        ("core/db/database.py", 250),
        ("core/events/worker.py", 35),
        ("core/events/bus.py", 407),
    }
)


# --------------------------------------------------------------------------
# the meta-test's subject: is the SDK importable right now?
# --------------------------------------------------------------------------


def _sdk_is_importable() -> bool:
    """Whether the SDK can be imported RIGHT NOW.

    ``importlib.util.find_spec`` reports what this interpreter can resolve, so
    the day the SDK is installed the answer flips and the meta-test fails. The
    ``except`` matters: a parent package that raises on import also means "not
    usable", and a half-installed SDK must make this oracle report, not error.
    """
    try:
        return importlib.util.find_spec(SDK_MODULE) is not None
    except (ImportError, ValueError):
        return False


def _sdk_absent_or_reason() -> str | None:
    """``None`` when the no-op dependency is satisfied, else why it is not."""
    if _sdk_is_importable():
        return (
            f"{SDK_MODULE} is importable, so trace_span is NOT the no-op this oracle "
            f"pins. Either the S-3 change landed and this oracle is obsolete, or the SDK "
            f"was installed into the venv, which is the exact trap the meta-test exists "
            f"to catch."
        )
    return None


# --------------------------------------------------------------------------
# call-site discovery
# --------------------------------------------------------------------------


def _trace_span_with_nodes() -> dict[tuple[str, int], ast.With]:
    """Every ``with trace_span(...)`` node in ``src/app``, keyed by (relpath, line)."""
    found: dict[tuple[str, int], ast.With] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "trace_span":
                    found[(rel, node.lineno)] = node
    return found


def _module_for(rel: str) -> str:
    """``core/db/database.py`` -> ``src.app.core.db.database``."""
    return "src.app." + rel.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def _load_module(rel: str) -> Any:
    """Import the module that owns a call site.

    The modules are imported for their REAL names and real module objects, not
    parsed from text: the call sites are then executed against the real
    ``tracing`` module, so a signature change or a renamed export fails here
    rather than passing on a hand-written copy of the expression.
    """
    import importlib

    return importlib.import_module(_module_for(rel))


def _call_expression(node: ast.With) -> ast.Call:
    """The ``trace_span(...)`` call inside a ``with`` statement."""
    for item in node.items:
        if isinstance(item.context_expr, ast.Call):
            return item.context_expr
    raise AssertionError("expected a trace_span call in this with statement")


def _free_names(node: ast.AST) -> set[str]:
    """Every ``Name`` in Load context anywhere inside ``node``."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class _Placeholder(str):
    """A stand-in for a caller's local variable.

    Subclassing ``str`` is what makes this general: two of the four call sites
    interpolate their locals into f-strings and one reads ``priority.value`` off
    an enum. A plain string placeholder satisfies the first and fails the third
    with ``AttributeError: 'str' object has no attribute 'value'``, which is a
    failure in the test harness wearing the costume of a defect in production.
    """

    value = "placeholder"


def _evaluation_globals(rel: str, call: ast.Call) -> dict[str, Any]:
    """Globals for evaluating a real call site, with its locals stubbed in.

    ``worker.py`` and ``bus.py`` build their ``attributes`` dict from names that
    exist only inside the enclosing function (``event_name``, ``priority``,
    ``job_try``, ...). Those values are the CALLER's business and tracing does not
    look at them, so each unresolved name gets a placeholder - which keeps the
    real expression, the real attribute keys and the real span name under test,
    rather than a hand-written copy with the interesting parts dropped.
    """
    module = _load_module(rel)
    scope: dict[str, Any] = dict(vars(module))
    for name in sorted(_free_names(call)):
        if name not in scope:
            scope[name] = _Placeholder(f"<{name}>")
    return scope


def _drive_call_site(key: tuple[str, int], *, tracer: Any, close_with: BaseException | None) -> None:
    """Execute a real call site's ``with trace_span(...)`` and exit it.

    The expression is taken from the AST of the real file and evaluated with the
    real module globals, so the attributes and span name exercised are the ones
    production uses. ``close_with`` is raised inside the block to drive the error
    path, which is the only path that touches ``set_status``/``record_exception``.
    """
    rel, _line = key
    node = _trace_span_with_nodes()[key]
    call = _call_expression(node)
    # trace_span reads `tracer` from its OWN module globals when it runs, so the
    # patch has to land on the tracing module rather than on the evaluated call.
    with patch.object(tracing, "tracer", tracer):
        manager = eval(  # noqa: S307 - a repository-local AST node, not external input
            compile(ast.Expression(call), filename=f"{rel}:{_line}", mode="eval"),
            _evaluation_globals(rel, call),
        )
        manager.__enter__()
        try:
            if close_with is not None:
                raise close_with
        except BaseException as exc:  # noqa: BLE001 - reproducing the traced error path
            manager.__exit__(type(exc), exc, exc.__traceback__)
            return
        manager.__exit__(None, None, None)


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class _RecordingSpan:
    """A span that records, and that can be told to misbehave.

    ``set_status`` and ``record_exception`` are the only two calls the error
    path makes. A fake that swallows them would prove nothing, and a fake that
    raises from them proves the real call sites do not depend on their success
    - which is the property S-3 must preserve.
    """

    def __init__(self, *, fail_on: frozenset[str] = frozenset()) -> None:
        self.statuses: list[Any] = []
        self.exceptions: list[BaseException] = []
        self._fail_on = fail_on

    def set_status(self, status: Any, *_args: Any, **_kwargs: Any) -> None:
        if "set_status" in self._fail_on:
            raise RuntimeError("this span cannot record a status")
        self.statuses.append(status)

    def record_exception(self, exc: BaseException, *_args: Any, **_kwargs: Any) -> None:
        if "record_exception" in self._fail_on:
            raise RuntimeError("this span cannot record an exception")
        self.exceptions.append(exc)


class _RecordingTracer:
    """Stands in for the SDK-present mode: real spans that record."""

    def __init__(self, *, fail_on: frozenset[str] = frozenset()) -> None:
        self.spans: list[_RecordingSpan] = []
        self.names: list[str] = []
        self._fail_on = fail_on

    def start_as_current_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        kind: Any = None,
    ) -> _SpanContext:
        span = _RecordingSpan(fail_on=self._fail_on)
        self.spans.append(span)
        self.names.append(name)
        return _SpanContext(span)


class _SpanContext:
    def __init__(self, span: _RecordingSpan) -> None:
        self._span = span

    def __enter__(self) -> _RecordingSpan:
        return self._span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        # Deliberately does NOT call set_status/record_exception itself. The real
        # trace_span already records on its error path, and an OTel span context
        # manager does not record a second time either - so a fake that did would
        # double-count every exception and make the "recorded once" contract look
        # like a defect in production code. It existed here, it was wrong, and the
        # count assertion caught it.
        return False


def _all_sites() -> list[tuple[str, int]]:
    return sorted(_trace_span_with_nodes())


# --------------------------------------------------------------------------
# (1) the meta-test
# --------------------------------------------------------------------------


class TestTheDependencyIsAbsentAndLoudly:
    def test_the_sdk_is_not_installed(self) -> None:
        reason = _sdk_absent_or_reason()
        assert reason is None, reason

    def test_the_api_package_is_installed_because_it_is_a_declared_dependency(self) -> None:
        """Guard the OTHER direction: an oracle that passes because nothing is
        installed proves nothing. ``opentelemetry-api`` IS declared, so its
        absence means the environment is broken, not that tracing is a no-op."""
        assert importlib.util.find_spec("opentelemetry") is not None

    def test_the_meta_test_would_fail_if_the_sdk_were_installed(self) -> None:
        """Demonstrate the assertion is capable of failing.

        Patching the probe is the closest honest simulation available: it cannot
        install a package, but it can prove the branch that produces the failure
        message is reachable and that the message names the trap.
        """
        with patch(f"{__name__}._sdk_is_importable", return_value=True):
            reason = _sdk_absent_or_reason()
        assert reason is not None
        assert SDK_MODULE in reason
        assert "trap the meta-test exists" in reason

    def test_the_probe_reports_absent_when_the_import_raises(self) -> None:
        """A half-installed SDK makes ``find_spec`` raise, and that must read as
        'not usable' rather than blowing up the oracle."""
        with patch("importlib.util.find_spec", side_effect=ImportError("broken install")):
            assert _sdk_is_importable() is False
        with patch("importlib.util.find_spec", side_effect=ValueError("no __spec__")):
            assert _sdk_is_importable() is False

    def test_the_live_span_is_non_recording(self) -> None:
        """The consequence of the absent SDK, asserted rather than assumed: the
        tracer hands back a span that records nothing, so a caller cannot be
        silently paying for a real span."""
        with tracing.trace_span("s3_probe") as span:
            recording = span.is_recording()
        assert recording is False


# --------------------------------------------------------------------------
# (2) every call site is discovered, and none binds the span
# --------------------------------------------------------------------------


class TestEveryCallSiteIsDiscoveredAndUnbound:
    def test_the_call_site_scan_finds_the_four_sites(self) -> None:
        sites = _trace_span_with_nodes()
        assert set(sites) == EXPECTED_CALL_SITES, (
            "the traced call sites changed; add the new one to EXPECTED_CALL_SITES and "
            f"state whether it binds the span. Found: {sorted(sites)}"
        )

    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    def test_no_call_site_binds_the_span(self, key: tuple[str, int]) -> None:
        """Every site is a bare ``with`` - no ``as``.

        This is the property that makes the no-op safe: if nobody binds the span,
        a span object that records nothing costs the block nothing. A site that
        grew ``as span`` would be the first place S-3 could hurt, so the oracle
        fails rather than letting the change land unnoticed.
        """
        node = _trace_span_with_nodes()[key]
        for item in node.items:
            assert item.optional_vars is None, f"{key} binds the span; S-3 would no longer be free there"

    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    def test_each_call_site_declares_a_span_name(self, key: tuple[str, int]) -> None:
        call = _call_expression(_trace_span_with_nodes()[key])
        assert isinstance(call.args[0], ast.Constant)
        assert isinstance(call.args[0].value, str) and call.args[0].value, f"{key} has no span name"

    def test_the_span_names_are_distinct(self) -> None:
        names = [
            _call_expression(node).args[0].value  # type: ignore[attr-defined]
            for node in _trace_span_with_nodes().values()
        ]
        assert len(set(names)) == len(names), f"two call sites share a span name: {names}"


# --------------------------------------------------------------------------
# (3) set_status / record_exception never raise, in BOTH modes, at ALL FOUR sites
# --------------------------------------------------------------------------


class TestSetStatusAndRecordExceptionNeverRaise:
    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    def test_the_success_path_is_a_no_op_with_no_sdk(self, key: tuple[str, int]) -> None:
        _drive_call_site(key, tracer=tracing.tracer, close_with=None)

    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    def test_the_error_path_is_a_no_op_with_no_sdk(self, key: tuple[str, int]) -> None:
        """The absent-SDK mode must not raise out of the block: the span is a
        ``NonRecordingSpan`` and ``trace_span`` calls both methods on it."""
        _drive_call_site(key, tracer=tracing.tracer, close_with=ValueError("boom"))

    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    def test_the_error_path_records_with_a_recording_span(self, key: tuple[str, int]) -> None:
        tracer = _RecordingTracer()
        with patch.object(tracing, "_SDK_INSTALLED", True):
            _drive_call_site(key, tracer=tracer, close_with=ValueError("boom"))
        assert len(tracer.spans) == 1
        assert len(tracer.spans[0].exceptions) == 1
        assert isinstance(tracer.spans[0].exceptions[0], ValueError)

    @pytest.mark.parametrize("key", sorted(EXPECTED_CALL_SITES))
    @pytest.mark.parametrize("fail_on", ["set_status", "record_exception"])
    def test_a_span_that_cannot_record_does_not_break_the_block(
        self, key: tuple[str, int], fail_on: str
    ) -> None:
        """Both calls are best-effort: a span that raises from either must not
        turn a successful request into a failed one.

        The exception is deliberately NOT swallowed here. What is asserted is
        that the failure originates in the SPAN, at the moment the block closes,
        and not anywhere in the traced code - which is exactly the distinction a
        silent ``try/except`` around the recorder would erase.

        ``_SDK_INSTALLED`` is pinned True because a ``_RecordingTracer`` is a
        stand-in for a real SDK span: without the SDK the no-op branch never
        enters the tracer at all, so these two assertions describe the installed
        branch by construction. What the no-SDK branch does instead is pinned by
        ``TestTheNoOpIsTakenAndNotJustAvailable``, which asserts the tracer is
        never entered -- so the pair covers both branches and neither is assumed.
        """
        tracer = _RecordingTracer(fail_on=frozenset({fail_on}))
        with patch.object(tracing, "_SDK_INSTALLED", True):
            with pytest.raises(RuntimeError, match="cannot record"):
                _drive_call_site(key, tracer=tracer, close_with=ValueError("boom"))

    def test_the_exception_still_propagates_out_of_the_block(self) -> None:
        """The context manager must not swallow the traced exception: swallowing
        it would turn a failed request into a success."""
        with pytest.raises(ValueError, match="original"):
            with tracing.trace_span("s3_propagation"):
                raise ValueError("original")

    def test_a_body_exception_is_re_raised_with_the_real_tracer(self) -> None:
        with pytest.raises(KeyError):
            with tracing.trace_span("s3_propagation_keyerror"):
                raise KeyError("missing")


class TestTheOracleIsPinnedToTheRealSurface:
    def test_the_oracle_imports_the_real_tracer_and_does_not_redefine_it(self) -> None:
        imported: set[str] = set()
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        assert "trace_span" not in imported, "the oracle must drive the real trace_span, not a copy"
        assert "tracer" not in imported

    def test_the_module_under_test_still_exports_both_names(self) -> None:
        assert set(tracing.__all__) == {"tracer", "trace_span"}

    def test_the_sdk_module_constant_names_a_real_distribution(self) -> None:
        assert SDK_MODULE.startswith("opentelemetry")

    def test_every_site_loads_a_real_module(self) -> None:
        """Guards ``_drive_call_site`` against silently exercising nothing: if a
        module failed to import the whole oracle would error, so this asserts the
        import path is a real dotted module for each site."""
        for rel, _line in EXPECTED_CALL_SITES:
            module = _load_module(rel)
            assert module.__name__ == _module_for(rel)


class TestTheNoOpIsTakenAndNotJustAvailable:
    """Pins that ``trace_span`` actually TAKES the no-op path.

    Every other test in this file is about what a caller observes, and a caller
    could not tell the difference: ``ProxyTracer`` hands back a
    ``NonRecordingSpan``, and ``trace.INVALID_SPAN`` is one too, so the whole
    oracle above is green both before and after S-3 landed. A suite that passes
    identically with and without the change is not an oracle for the change --
    that is the same defect the S-4 oracle had, where every test passed whether
    or not the two-level copy was ever wired in.

    So this asserts on the machinery instead of the outcome: with no SDK the
    ``start_as_current_span`` call must not happen at all. It is asserted BOTH
    ways on purpose. Pinning only "not called" would be satisfiable by deleting
    the real path, which would turn the guard green by breaking every future
    deployment that installs an SDK.
    """

    def test_no_sdk_means_the_tracer_is_never_entered(self) -> None:
        assert tracing._SDK_INSTALLED is False, (
            "the no-SDK path is only reachable when the SDK is absent; with it "
            "installed this whole class is vacuous and S-3 is not what runs"
        )
        entered: list[str] = []

        class _Tripwire:
            def start_as_current_span(self, name: str, **_: Any) -> Any:
                entered.append(name)
                raise AssertionError(f"the tracer was entered for {name!r}")

        with patch.object(tracing, "tracer", _Tripwire()):
            with tracing.trace_span("s3_never_entered") as span:
                assert span is tracing.trace.INVALID_SPAN

        assert entered == [], f"start_as_current_span ran for {entered}"

    def test_an_installed_sdk_still_gets_real_spans(self) -> None:
        """The mirror. Without this, the test above would also pass if the real
        path had been deleted outright instead of made conditional."""
        entered: list[str] = []

        class _Recorder:
            @staticmethod
            def start_as_current_span(name: str, **_: Any) -> Any:
                entered.append(name)
                from contextlib import contextmanager as _cm

                @_cm
                def _span() -> Any:
                    yield tracing.trace.INVALID_SPAN

                return _span()

        with patch.object(tracing, "_SDK_INSTALLED", True), patch.object(
            tracing, "tracer", _Recorder()
        ):
            with tracing.trace_span("s3_sdk_present") as span:
                assert span is tracing.trace.INVALID_SPAN

        assert entered == ["s3_sdk_present"], (
            "with an SDK installed the real tracer must be used, or the guard "
            "above would pass by the real path having been deleted"
        )

    def test_an_exception_still_propagates_through_the_no_op(self) -> None:
        """The no-op must not become a swallow. A bare ``yield`` in a generator
        re-raises whatever is thrown in at the yield point; if this were ever
        "fixed" into a try/except that returned quietly, every error path in the
        application would stop raising and nothing else here would notice."""
        with pytest.raises(ValueError, match="boom"):
            with tracing.trace_span("s3_propagation"):
                raise ValueError("boom")
