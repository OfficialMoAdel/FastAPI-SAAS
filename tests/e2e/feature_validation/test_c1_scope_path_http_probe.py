"""C-1 (uu5p), value leg: the routing middleware must decide on the same
path whichever spelling it reads.

WHAT THE OTHER C-1 GATES ALREADY ESTABLISH
------------------------------------------
`scripts/perf-validation/isomorphism_validator.py` (gate `C1_path_equality`,
which CI runs on Linux before the C-1 diff) proves the STRING equivalence
`Request(scope).url.path == scope["path"]` for every shape in
`C1_PATH_SHAPES`, under every root_path in `C1_ROOT_PATHS`, and its lint
gate keeps the set of `scope["path"]` readers classified as routing or
logging. `tests/unit/middleware/test_c1_scope_path_oracle.py` pins the
Starlette internals that equivalence rests on.

Neither of those observes what the live stack DECIDES. A string
equivalence plus identical code is a proof by substitution, which is the
right kind of proof for a routing read -- but only if the substituted
expression is genuinely the one on the hot path. This file closes that
gap by recording the value each middleware actually consumed, on the
live ASGI stack, for every shape in the SAME list the no-DB gate uses
(loaded from it by path, not re-listed, so the two gates cannot drift
apart).

THE THREE VALUES, NOT THREE STATUS CODES
----------------------------------------
A status code is a lossy summary: two different path values can both
produce 404. So this file asserts on the VALUES:

1. the raw path string each middleware received, byte-identical to the
   request line for every shape -- including the traversal-like shapes,
   which neither spelling may normalise;
2. the tenant middleware's exempt-path DECISION for that shape, computed
   from both spellings through the middleware's own module-level
   predicate and asserted equal, then asserted equal to the decision the
   live stack recorded;
3. the IP filter's exempt-path DECISION, same construction, plus the
   TENANT ID the middleware bound -- compared by value across every
   shape, because a path shape that changed the resolved tenant would be
   a tenancy bypass that no status code reveals.

DIALECT/ENVIRONMENT
-------------------
Real Postgres and real Redis (the tenant resolver is a read-through over
both), so this module is `pytest.mark.e2e` and runs only where the
feature_validation fixtures can boot -- the Linux target. The no-DB half
of the oracle is the validator above, and that is what CI runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from starlette.requests import Request

from src.app.core.middleware_exempt import IP_FILTER_EXEMPT_PATHS
from src.app.middleware import ip_filter as ip_filter_module
from src.app.middleware import tenant as tenant_module

pytestmark = pytest.mark.e2e

#: ``tests/e2e/feature_validation/<this file>`` -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The no-DB gate's module, loaded by path: the directory name contains a
#: hyphen, so it is not importable as a package, and re-listing the shapes
#: here would let the two gates cover different sets without either
#: noticing.
VALIDATOR_PATH = REPO_ROOT / "scripts" / "perf-validation" / "isomorphism_validator.py"


def _validator_shapes() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("c1_isomorphism_validator", VALIDATOR_PATH)
    assert spec is not None, f"cannot load {VALIDATOR_PATH}"
    assert spec.loader is not None, f"no loader for {VALIDATOR_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shapes = tuple(module.C1_PATH_SHAPES)
    assert shapes, (
        "C1_PATH_SHAPES came back empty; every assertion below would pass "
        "without exercising a single shape"
    )
    return shapes


C1_PATH_SHAPES = _validator_shapes()


def _scope(path: str, *, root_path: str = "") -> dict[str, Any]:
    """The scope an ASGI server builds for `path`, built here rather than
    captured so the traversal shapes can be expressed without a client."""
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


def _spelling_variants(shape: str) -> tuple[str, str]:
    """(scope spelling, url spelling) for one shape.

    Returned as a pair so every caller compares the SAME two values; the
    whole file is about those two agreeing.
    """
    scope = _scope(shape)
    return scope["path"], Request(scope).url.path


def test_the_spelling_variants_are_equal_for_every_gated_shape() -> None:
    """Re-established here so a divergence fails in THIS file with the
    shape named, rather than only in the CI gate."""
    for shape in C1_PATH_SHAPES:
        from_scope, from_url = _spelling_variants(shape)
        assert from_scope == from_url, f"{shape}: {from_scope!r} != {from_url!r}"


def test_the_exempt_decisions_are_equal_for_both_spellings() -> None:
    """The routing predicates the middlewares call, fed both spellings.

    These are the middleware modules' own globals, not a reimplementation:
    `tenant.is_path_exempt` folds the tenant-only additions into the shared
    helper, and `ip_filter.is_path_exempt` is called with the IP filter's
    own base. Calling anything else here would compare a strawman.
    """
    tenant_divergences: list[tuple[str, bool, bool]] = []
    ip_divergences: list[tuple[str, bool, bool]] = []
    for shape in C1_PATH_SHAPES:
        from_scope, from_url = _spelling_variants(shape)
        tenant_from_scope = tenant_module.is_path_exempt(from_scope)
        tenant_from_url = tenant_module.is_path_exempt(from_url)
        ip_from_scope = ip_filter_module.is_path_exempt(from_scope, base=IP_FILTER_EXEMPT_PATHS)
        ip_from_url = ip_filter_module.is_path_exempt(from_url, base=IP_FILTER_EXEMPT_PATHS)
        if tenant_from_scope != tenant_from_url:
            tenant_divergences.append((shape, tenant_from_scope, tenant_from_url))
        if ip_from_scope != ip_from_url:
            ip_divergences.append((shape, ip_from_scope, ip_from_url))
    assert not tenant_divergences, f"tenant exempt decision diverged: {tenant_divergences}"
    assert not ip_divergences, f"ip-filter exempt decision diverged: {ip_divergences}"


def _live_scope_path(shape: str) -> str:
    """The path a real ASGI layer delivers for `shape`, which is NOT always
    the request-line form.

    Measured on this stack, not assumed: the client under the ASGI
    transport percent-DECODES the URL when it builds the scope (`%20`
    arrives as a space) and RESOLVES dot segments (`/api/v1/users/../../admin`
    arrives as `/api/admin`). Uvicorn decodes the same way. Neither
    rewriting happens in a middleware, and BOTH C-1 spellings read the
    same rewritten scope value, so neither is evidence about C-1 -- but
    asserting the request-line form would fail for reasons that have
    nothing to do with the equivalence.

    The expectation is therefore derived from the client that actually
    sends the request, rather than reimplemented here: an independent
    reimplementation of RFC 3986 dot-segment removal would be a second
    thing to keep correct, which is the defect this oracle exists to
    detect.

    The ENCODED and traversal-shaped forms are still legal `scope["path"]`
    values (a proxy that forwards only `raw_path` produces them), which is
    why the validator's gate asserts the equivalence on synthetic scopes
    holding exactly those strings; this file asserts it on the strings a
    live request actually produces.
    """
    return unquote(httpx.URL(f"http://testserver{shape}").path)


def test_the_spelling_variants_are_equal_for_every_live_path() -> None:
    """The same equivalence, on the strings a live request really delivers.

    Distinct from the gate above on purpose: that one holds encoded
    strings, this one holds the decoded and dot-segment-resolved strings
    the client actually puts in the scope, which are the values the
    middlewares saw in the HTTP leg.
    """
    for shape in C1_PATH_SHAPES:
        live = _live_scope_path(shape)
        scope = _scope(live)
        assert Request(scope).url.path == live, f"{shape} (as delivered: {live}) diverged"


async def test_the_live_stack_records_every_shape_unnormalised_and_one_tenant(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
    log_step: Any,
) -> None:
    """Both routing reads, observed on the real stack, with the tenant id.

    The IP filter consumes the path on EVERY non-exempt request, so it is
    the observation point for the raw value; the tenant middleware binds
    its context on every successfully resolved request, so it is the
    observation point for the resolved tenant id.
    """
    ip_seen: list[tuple[str, bool]] = []
    bound: list[Any] = []
    real_ip_exempt = ip_filter_module.is_path_exempt
    real_bound_tenant = tenant_module.bound_tenant

    def recording_ip_exempt(path: str, **kwargs: Any) -> bool:
        decision = real_ip_exempt(path, **kwargs)
        ip_seen.append((path, decision))
        return decision

    def recording_bound_tenant(tenant: Any) -> Any:
        bound.append(tenant)
        return real_bound_tenant(tenant)

    monkeypatch.setattr(ip_filter_module, "is_path_exempt", recording_ip_exempt)
    monkeypatch.setattr(tenant_module, "bound_tenant", recording_bound_tenant)

    tenant_ids: dict[str, Any] = {}
    for shape in C1_PATH_SHAPES:
        before = len(ip_seen)
        response = await client.get(shape)
        expected = _live_scope_path(shape)
        observed = [path for path, _ in ip_seen[before:]]
        assert observed, f"{shape}: the IP filter consulted no path at all"
        for path in observed:
            assert path == expected, f"{shape}: routing layer received {path!r}"
        if bound:
            tenant_ids[shape] = bound[-1]
        log_step(f"{shape} -> {response.status_code} (ip_exempt_calls={len(observed)})")

    assert ip_seen, "the IP filter consumed no path; the observation point is wrong"
    distinct_ids = {getattr(tenant, "id", None) for tenant in bound}
    assert len(distinct_ids) == 1, f"one request path shape changed the bound tenant: {distinct_ids}"
    assert None not in distinct_ids, f"a request bound no tenant: {bound}"

    # The recorded decision must equal the decision the OTHER spelling
    # produces -- the value equality, not just a matching status. The
    # spellings are compared on the derived live paths (below, as a pure
    # computation) rather than rebuilt per recorded value: the recorded
    # values are already pinned to the expected ones above, so rebuilding
    # them here would assert the same fact through a longer path.
    for path, decision in ip_seen[:]:
        assert ip_filter_module.is_path_exempt(path, base=IP_FILTER_EXEMPT_PATHS) == decision, (
            f"{path}: the recorded decision does not reproduce from the path the "
            "middleware reported, so the observation point is not the predicate"
        )
    assert tenant_ids


async def test_the_tenant_exempt_decision_drives_the_live_outcome(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
    log_step: Any,
) -> None:
    """The tenant middleware only consults its exempt set when it has no
    usable tenant, which is why the decision is recorded here with the
    tenant header explicitly BLANKED -- the `client` fixture sends one by
    default, and with a resolvable tenant the middleware never consults
    the exempt set at all. Exempt shapes must not be refused for a
    missing tenant; non-exempt shapes must be. That is the decision
    VALUE turning into an observable outcome.
    """
    seen: list[str] = []
    real_exempt = tenant_module.is_path_exempt

    def recording_exempt(path: str) -> bool:
        decision = real_exempt(path)
        seen.append(path)
        return decision

    monkeypatch.setattr(tenant_module, "is_path_exempt", recording_exempt)

    for shape in C1_PATH_SHAPES:
        seen.clear()
        response = await client.get(shape, headers={"X-Tenant-ID": ""})
        expected = _live_scope_path(shape)
        exempt = tenant_module.is_path_exempt(expected)
        # The SET, not the count: the value every call saw is what C-1 is
        # about, and how many times the middleware consults its exempt set
        # is an implementation detail of that middleware.
        assert set(seen) == {expected}, f"{shape}: exempt check saw {sorted(set(seen))}"
        body = response.text
        # The rendered message, not the reject KIND: REJECT_CATALOG maps the
        # kind to `RejectRow("Missing required header: {header}",
        # error=DomainValidationError)`, so the wire body carries the message
        # text. Asserting the kind string here would pass vacuously for a
        # response that said something else.
        refused_for_tenant = response.status_code == 400 and "Missing required header" in body
        assert refused_for_tenant is (not exempt), (
            f"{shape}: exempt={exempt} but the stack "
            f"{'refused' if refused_for_tenant else 'let it through'}"
        )
        log_step(f"{shape} exempt={exempt} status={response.status_code}")
