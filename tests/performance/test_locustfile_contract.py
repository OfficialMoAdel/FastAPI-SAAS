"""H-7 verification: a teardown measurement is not a handler measurement.

`/account/logout` used to be fired from `AuthenticatedUser.on_stop`, so every
logout sample in a run was taken while the event loop, the DB pool and the
Redis pool were all closing. That produced the 208.47 ms logout p95 in the
2026-09-22 baseline, which cannot be attributed to anything: the `logout`
handler performs no bcrypt and no batched I/O.

Three separate contracts, three separate tests, per the bead:

1. `on_stop` issues NO measured request at all. Not "no logout" - no request.
2. The logout task uses ``catch_response``, so a phase that quietly answered
   401 or 429 for every call cannot report a zero-failure summary.
3. Shutdown-burst samples are EXCLUDED from the reported percentiles, not
   merely reported elsewhere. The bead is explicit that relocating them is not
   good enough, and this file holds the line on that.

Plus the locator: the harness's request path must resolve to a route that
actually exists in the application, resolved against the real route table
rather than compared to a string literal in a comment.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Importing `locust` runs `gevent.monkey.patch_all()` as an import side effect
# (locust/__init__.py:16), which rebinds `ssl.SSLContext` to
# `gevent.ssl.SSLContext` for the WHOLE interpreter - not just for locust.
#
# `urllib3.util.ssl_` binds `from ssl import SSLContext` into its OWN module
# global when *it* is imported, while CPython's setter re-reads the name from
# `ssl`'s globals at CALL time:
#
#   super(SSLContext, SSLContext).minimum_version.__set__(self, value)
#                                                  ^ ssl.py
#
# So if anything imported urllib3 BEFORE this module, `create_urllib3_context()`
# builds a context from the pre-patch class and that super() resolves back to
# the stdlib property, which calls itself: RecursionError at import time. That
# is order-dependent, which is why collecting this file alone passed while the
# full suite died during collection and aborted the entire run (exit 2).
#
# `LOCUST_SKIP_MONKEY_PATCH` is locust's own supported switch for this. It is
# also the honest scope for a unit contract test: the four contracts below
# assert pure-Python logic (a teardown that fires no request, catch_response,
# burst samples kept out of the percentiles, a locator that resolves against
# the real route table). None of them depend on gevent having replaced socket,
# threading or ssl, so the patch buys nothing here and costs the other 8693
# tests a monkey-patched interpreter. The real harness still gets the patch -
# it runs `python -m locust` in its own process (scripts/run_perf_baseline.sh).
os.environ["LOCUST_SKIP_MONKEY_PATCH"] = "1"

from tests.performance import locustfile  # noqa: E402
from tests.performance.locustfile import (  # noqa: E402
    AuthenticatedUser,
    FastAPISaaSUser,
    MetricsCollector,
)

REPO_ROOT = inspect.getfile(locustfile)
LOCUSTFILE = REPO_ROOT


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.outcome: str | None = None
        self.failure_message: str | None = None

    def success(self) -> None:
        self.outcome = "success"

    def failure(self, message: str) -> None:
        self.outcome = "failure"
        self.failure_message = message

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _RecordingClient:
    """A Locust client stand-in that records calls instead of making them.

    ``post`` returns the context manager ``catch_response=True`` produces, so
    the code under test uses its real ``with`` shape.
    """

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []
        self.responses: list[_FakeResponse] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = _FakeResponse(self.status_code)
        self.responses.append(response)
        return response

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self.post(url, **kwargs)


class _ForbiddenClient:
    """A client that fails the test if anything at all is issued to it."""

    def __getattr__(self, name: str) -> Any:
        def _boom(*args: object, **_kwargs: object) -> None:
            pytest.fail(f"on_stop issued a request: {name}{args}")

        return _boom


def _bare(cls: type, **attrs: object) -> Any:
    """An instance with no Locust environment: ``__init__`` never runs."""
    factory: Any = cls
    instance = factory.__new__(cls)
    for key, value in attrs.items():
        setattr(instance, key, value)
    return instance


def _function_node(name: str, cls: type) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(inspect.getsource(cls))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{cls.__name__}.{name} not found")


def _method_name(call: ast.Call) -> str:
    """The method name of a call ``_client_calls`` already proved is one."""
    func = call.func
    assert isinstance(func, ast.Attribute), f"expected self.client.<method>(...), got {ast.dump(func)}"
    return func.attr


def _client_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Every ``self.client.<anything>(...)`` call inside ``node``."""
    found: list[ast.Call] = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
            continue
        owner = inner.func.value
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id == "self":
            found.append(inner)
    return found


# --------------------------------------------------------------------------
# 1. on_stop issues no measured request
# --------------------------------------------------------------------------


class TestOnStopMeasuresNothing:
    def test_on_stop_contains_no_client_call(self):
        calls = _client_calls(_function_node("on_stop", AuthenticatedUser))
        assert calls == [], f"on_stop still reaches self.client: {[ast.dump(c) for c in calls]}"

    def test_on_stop_issues_nothing_at_runtime(self):
        """The static check above would pass if ``on_stop`` had no ``client``
        attribute at all. Prove the hook runs and still sends nothing."""
        user = _bare(AuthenticatedUser, access_token="a-token", client=_ForbiddenClient())
        user.on_stop()

    def test_on_stop_runs_with_no_token_too(self):
        user = _bare(AuthenticatedUser, access_token="", client=_ForbiddenClient())
        user.on_stop()


# --------------------------------------------------------------------------
# 2. the logout task records its outcome
# --------------------------------------------------------------------------


class TestTheLogoutTaskRecordsItsOutcome:
    def test_the_post_uses_catch_response(self):
        posts = [
            c
            for c in _client_calls(_function_node("logout_account", FastAPISaaSUser))
            if _method_name(c) == "post"
        ]
        assert len(posts) == 1, "logout_account must issue exactly one measured request"
        kwargs = {kw.arg: kw.value for kw in posts[0].keywords}
        assert "catch_response" in kwargs, "without catch_response a non-2xx is neither success nor failure"
        assert isinstance(kwargs["catch_response"], ast.Constant)
        assert kwargs["catch_response"].value is True

    @pytest.mark.parametrize("status_code", [200, 204])
    def test_a_success_status_is_marked_a_success(self, status_code: int):
        user = _bare(FastAPISaaSUser, access_token="a-token", client=_RecordingClient(status_code))
        user.logout_account()
        assert user.client.responses[0].outcome == "success"

    @pytest.mark.parametrize("status_code", [401, 429, 500])
    def test_a_non_success_status_is_marked_a_failure(self, status_code: int):
        """Without this the harness reports a beautiful zero-failure summary
        for a phase in which every single call was rejected."""
        user = _bare(FastAPISaaSUser, access_token="a-token", client=_RecordingClient(status_code))
        user.logout_account()
        response = user.client.responses[0]
        if status_code == 401:
            # The handler behaved correctly and the harness was out of date.
            assert response.outcome == "success"
            assert user.access_token == ""
        else:
            assert response.outcome == "failure"
            assert str(status_code) in (response.failure_message or "")

    def test_no_token_means_no_request(self):
        user = _bare(FastAPISaaSUser, access_token="", client=_RecordingClient(200))
        user.logout_account()
        assert user.client.calls == []

    def test_logout_is_a_task_and_not_only_a_teardown_hook(self):
        tasks = [
            name
            for name in vars(FastAPISaaSUser)
            if getattr(getattr(FastAPISaaSUser, name, None), "locust_task_weight", None) is not None
        ]
        assert "logout_account" in tasks, "logout_account is not registered as a weighted Locust task"


# --------------------------------------------------------------------------
# 3. burst samples are excluded from the percentiles, not relocated
# --------------------------------------------------------------------------


class TestShutdownBurstSamplesAreExcluded:
    #: A value no steady-state sample can produce, so its appearance in any
    #: reported percentile is unambiguous.
    BURST_MS = 9999.0

    def _collector_with_burst(self, steady: int = 20) -> MetricsCollector:
        collector = MetricsCollector()
        for i in range(steady):
            collector.record_request("/account/logout", float(i + 1), True)
        with collector.shutdown_burst():
            for _ in range(3):
                collector.record_request("/account/logout", self.BURST_MS, True)
        return collector

    def test_burst_samples_do_not_enter_the_steady_population(self):
        collector = self._collector_with_burst()
        assert len(collector.request_times["/account/logout"]) == 20
        assert max(collector.request_times["/account/logout"]) == 20.0
        assert collector.burst_times["/account/logout"] == [self.BURST_MS] * 3

    def test_no_reported_percentile_is_pulled_toward_the_burst(self):
        collector = self._collector_with_burst()
        for percentile in (50, 95, 99):
            value = collector.get_percentile("/account/logout", percentile)
            assert value <= 20.0, f"p{percentile}={value} was pulled toward the {self.BURST_MS} ms burst"
        assert collector.get_percentile("/account/logout", 99) == 20.0, "n=20: p99 IS the maximum of the steady samples"

    def test_the_burst_value_appears_nowhere_in_the_summary(self, caplog: pytest.LogCaptureFixture):
        collector = self._collector_with_burst()
        with caplog.at_level("INFO", logger="perf-test"):
            collector.log_summary()
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert str(int(self.BURST_MS)) not in text, "a burst sample reached the reported summary"
        assert "/account/logout" in text

    def test_the_withheld_samples_are_still_accounted_for(self, caplog: pytest.LogCaptureFixture):
        """Excluded from the percentiles, not discarded: a run that silently
        dropped them would hide that it ever made the calls."""
        collector = self._collector_with_burst()
        with caplog.at_level("INFO", logger="perf-test"):
            collector.log_summary()
        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "WITHHELD" in text
        assert "/account/logout=3" in text

    def test_a_burst_failure_is_counted_separately(self):
        collector = MetricsCollector()
        with collector.shutdown_burst():
            collector.record_request("/account/logout", self.BURST_MS, False)
        assert collector.error_counts == {}
        assert collector.burst_error_counts["/account/logout"] == 1

    def test_the_separation_is_load_bearing(self):
        """Fold the burst into the steady population the way ``on_stop`` used
        to, and the p99 becomes the burst. This is the number the 2026-09-22
        baseline published."""
        separated = self._collector_with_burst()
        folded = MetricsCollector()
        for i in range(20):
            folded.record_request("/account/logout", float(i + 1), True)
        folded.request_times["/account/logout"].extend([self.BURST_MS] * 3)
        assert folded.get_percentile("/account/logout", 99) == self.BURST_MS
        assert separated.get_percentile("/account/logout", 99) == 20.0

    def test_the_burst_context_manager_restores_the_previous_phase(self):
        collector = MetricsCollector()
        with collector.shutdown_burst():
            with collector.shutdown_burst():
                pass
            collector.record_request("/x", 1.0, True)
        collector.record_request("/x", 2.0, True)
        assert collector.request_times["/x"] == [2.0]
        assert collector.burst_times["/x"] == [1.0]

    def test_the_burst_context_manager_clears_the_flag_when_the_body_raises(self):
        collector = MetricsCollector()
        with pytest.raises(RuntimeError), collector.shutdown_burst():
            raise RuntimeError("teardown blew up")
        collector.record_request("/x", 2.0, True)
        assert collector.request_times["/x"] == [2.0]


# --------------------------------------------------------------------------
# 4. the locator resolves to a route that exists
# --------------------------------------------------------------------------


def _render_f_string(node: ast.JoinedStr) -> str:
    """Render the request path an f-string builds, resolving module globals.

    An f-string mixes ``Constant`` parts (the literal path) with
    ``FormattedValue`` parts naming module constants. Dropping the latter would
    quietly produce ``/account/logout`` instead of ``/api/v1/account/logout``
    and the locator test would then pass against the wrong path.
    """
    out: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            inner = value.value
            if not isinstance(inner, ast.Name):
                raise AssertionError(f"cannot statically resolve {ast.dump(inner)}")
            resolved = getattr(locustfile, inner.id)
            out.append(f"{resolved}")
        else:
            raise AssertionError(f"cannot statically resolve {ast.dump(value)}")
    return "".join(out)


class TestTheLocatorResolvesToARealRoute:
    @pytest.fixture(scope="class")
    def app_routes(self) -> set[str]:
        from src.app.main import app

        return {getattr(route, "path", "") for route in app.routes}

    def test_the_harness_request_path_is_served_by_the_application(self, app_routes: set[str]):
        node = _function_node("logout_account", FastAPISaaSUser)
        post = next(c for c in _client_calls(node) if _method_name(c) == "post")
        assert isinstance(post.args[0], ast.JoinedStr)
        path = _render_f_string(post.args[0])
        assert path == f"{locustfile.API_V1}/account/logout"
        assert path in app_routes, f"the harness measures {path}, which no route serves"

    def test_the_prefix_is_actually_part_of_the_path(self):
        """A regression guard on the renderer above: if it drops the
        ``FormattedValue`` half, the path becomes ``/account/logout`` and the
        locator test would compare the wrong string against the route table."""
        node = _function_node("logout_account", FastAPISaaSUser)
        post = next(c for c in _client_calls(node) if _method_name(c) == "post")
        assert isinstance(post.args[0], ast.JoinedStr)
        assert any(isinstance(v, ast.FormattedValue) for v in post.args[0].values)

    def test_it_is_a_post_route(self):
        from src.app.main import app

        methods = {
            m
            for route in app.routes
            if getattr(route, "path", "") == f"{locustfile.API_V1}/account/logout"
            for m in (getattr(route, "methods", None) or set())
        }
        assert "POST" in methods


# --------------------------------------------------------------------------
# 5. no docstring in the harness asserts a latency it never measured
# --------------------------------------------------------------------------

# Each pattern is keyed to the SHAPE of the defect - a column of "current"
# figures, a target line, a restated delta - rather than to any particular
# number, so re-adding the claim in different words still fails. The two
# structural patterns (a markdown column, a "Target:" line) are the load-bearing
# ones; the two prose patterns are best-effort and can be defeated by careful
# rewording, which is why the replacement text in the harness names the REMOVED
# claim by what it was ("the DB-batching figure this docstring used to carry")
# instead of quoting its numbers back.
UNSOURCED_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\|\s*current\s+p\d{2}\s*\|", re.IGNORECASE),
        "a 'Current pNN' column asserts measurements the harness never took",
    ),
    (
        re.compile(r"^\s*Target:\s*p\d{2}\s*<\s*\d", re.IGNORECASE | re.MULTILINE),
        "a 'Target: pNN < N' line promises a number nothing enforces",
    ),
    (
        re.compile(r"currently\s+~?\d+\s*ms", re.IGNORECASE),
        "'currently ~NNms' restates a measurement that was never taken",
    ),
    (
        re.compile(r"~\d+\s*ms\s+to\s+<", re.IGNORECASE),
        "'~NNms to <NNms' asserts a delta that was never measured",
    ),
)


def _docstrings() -> list[tuple[str, str]]:
    """Every docstring in the harness, with the qualified name it belongs to."""
    tree = ast.parse(inspect.getsource(locustfile))
    found: list[tuple[str, str]] = []
    if ast.get_docstring(tree) is not None:
        found.append(("<module>", ast.get_docstring(tree) or ""))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            text = ast.get_docstring(node)
            if text is not None:
                found.append((node.name, text))
    return found


class TestNoUnsourcedLatencyClaims:
    """The harness must not state the answer it is trying to measure.

    Every figure that was in these docstrings came from a Windows run, and the
    two existing Locust baselines are not a same-instrument A/B (the harness
    changed in commit 4cd00654, fourteen hours after the baseline CSV was
    stamped), so none of them can be restated as a current measurement or a
    target. The place to put one once a real Linux measurement exists is the
    note at the top of the file, with its protocol attached.
    """

    def test_no_docstring_asserts_an_unsourced_latency(self):
        offenders = [
            f"{owner}: {why} -> {pattern.pattern}"
            for owner, text in _docstrings()
            for pattern, why in UNSOURCED_CLAIM_PATTERNS
            if pattern.search(text)
        ]
        assert not offenders, "the harness restates a latency it cannot source:\n" + "\n".join(offenders)

    def test_the_docstring_scan_reaches_more_than_the_module_docstring(self):
        """A scan that only read one docstring would miss all three tasks."""
        owners = {owner for owner, _ in _docstrings()}
        assert len(owners) > 5, f"only found {len(owners)} docstrings; the scan is not walking the tree"
        assert {"logout_account", "get_current_user", "ai_chat_completion", "refresh_auth_token"} <= owners

    @pytest.mark.parametrize(("pattern", "_why"), UNSOURCED_CLAIM_PATTERNS, ids=range(len(UNSOURCED_CLAIM_PATTERNS)))
    def test_each_pattern_would_catch_the_claim_it_exists_for(self, pattern, _why):
        """A rule that cannot fail is not a rule.

        Each sample below is the shape of claim this file used to carry, so a
        pattern that matched nothing would leave the suite quietly vacuous.
        """
        samples = {
            r"\|\s*current\s+p\d{2}\s*\|": "| Current p95 |",
            r"^\s*Target:\s*p\d{2}\s*<\s*\d": "Target: p95 < 15ms",
            r"currently\s+~?\d+\s*ms": "it is currently ~50ms",
            r"~\d+\s*ms\s+to\s+<": "reduce ~200ms to <100ms",
        }
        sample = samples[pattern.pattern]
        assert pattern.search(sample), f"{pattern.pattern} does not match its own sample {sample!r}"


# --------------------------------------------------------------------------
# 6. the import itself must not depend on what ran before it
# --------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestTheImportIsNotOrderDependent:
    """The failure this file used to die with, reproduced deliberately.

    Importing `locust` patches the interpreter; anything that imported
    urllib3 first then left `create_urllib3_context()` recursing forever.
    Because that happens at COLLECTION time it did not fail one test - it
    aborted the whole suite with exit 2, and it only reproduced in a full run,
    so running this file on its own could never have caught it. Each test
    below therefore drives a fresh interpreter with the offending order.
    """

    @staticmethod
    def _import_urllib3_then_locustfile(skip_patch: bool) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(_REPO_ROOT), env.get("PYTHONPATH", ""))))
        if skip_patch:
            env["LOCUST_SKIP_MONKEY_PATCH"] = "1"
        else:
            env.pop("LOCUST_SKIP_MONKEY_PATCH", None)
        return subprocess.run(
            [sys.executable, "-c", "import urllib3.util.ssl_; from tests.performance import locustfile"],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_urllib3_imported_first_no_longer_kills_the_import(self):
        result = self._import_urllib3_then_locustfile(skip_patch=True)
        assert result.returncode == 0, (
            "importing urllib3 before the locustfile must not fail; "
            f"this is what aborted the whole suite (exit 2).\n{result.stderr[-2000:]}"
        )

    def test_importing_this_module_leaves_ssl_unpatched(self):
        """Why the switch above is load-bearing, asserted on this interpreter.

        A guard that quietly stopped working would leave the previous test
        green for the wrong reason, so the state it depends on is checked
        directly rather than inferred from the subprocess.
        """
        import ssl

        assert ssl.SSLContext.__module__ != "gevent.ssl", (
            "importing this module monkey-patched ssl for the entire test session; "
            "LOCUST_SKIP_MONKEY_PATCH is no longer taking effect"
        )
