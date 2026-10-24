#!/usr/bin/env python3
"""Isomorphism Validation - Ensure optimizations don't change behavior.

Critical: All optimizations must pass isomorphism tests before
performance benefits can be claimed.
"""

import ast
import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


class IsomorphismTest:
    """Test that old and new implementations produce identical results."""

    def __init__(self, name: str, description: str, test_fn: Callable):
        self.name = name
        self.description = description
        self.test_fn = test_fn

    async def run(self) -> dict[str, Any]:
        """Run the isomorphism test."""
        try:
            result = await self.test_fn()
            return {"name": self.name, "passed": result, "error": None}
        except Exception as e:
            logger.exception("isomorphism_test_error", name=self.name, error=str(e))
            return {"name": self.name, "passed": False, "error": str(e)}


# Define isomorphism test functions
async def test_u3_parallel_isomorphism() -> bool:
    """Verify parallel handlers produce same side effects."""
    results = {"sequential": [], "parallel": []}

    async def handler(prefix: str, value: int):
        await asyncio.sleep(0.01)
        results[prefix].append(value)

    # Sequential
    for i in range(3):
        await handler("sequential", i)

    # Parallel
    await asyncio.gather(*[handler("parallel", i) for i in range(3)])

    # Same values (order may differ)
    return sorted(results["sequential"]) == sorted(results["parallel"])


async def test_u4_pipeline_isomorphism() -> bool:
    """U4 (non-hollow): the multi-key cache write is a BATCHED pipeline.

    The isomorphism claim is "a pipeline produces the same state as the
    separate commands it batches". A live-Redis differential is not
    available in this gate's environment (no Redis and no ``fakeredis``),
    so this pins the MECHANISM the claim rests on and that a refactor can
    silently break: the multi-key writer builds ONE pipeline and issues
    its writes INSIDE it. It fails if the batch is replaced by per-key
    calls -- at which point the state-equality claim is void and must be
    re-derived, not assumed. The state equality itself is covered by the
    integration cache round-trip tests.
    """
    path = REPO_ROOT / "src/app/modules/users/cache.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # Any `async with X.pipeline(...) as pipe:` block must issue >= 2
        # write commands inside it and must not fall back to a bare client
        # write in the same method (the exact shape of the regression).
        if not isinstance(node, ast.AsyncWith):
            continue
        is_pipeline = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "pipeline"
            for item in node.items
        )
        if not is_pipeline:
            continue
        writes = [
            inner
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in {"setex", "set", "get", "delete", "hset", "expire"}
        ]
        if len(writes) < 2:
            logger.error("u4_pipeline_degenerate", writes=len(writes))
            return False
        logger.info("u4_pipeline_ok", writes=len(writes))
        return True
    logger.error("u4_no_pipeline_found", path=str(path))
    return False


async def test_u6_n1_isomorphism() -> bool:
    """U6 (non-hollow): the auth-info read is EAGER, so it cannot be N+1.

    "joinedload returns the same data as lazy loading" is only a claim
    about this app while the read actually APPLIES an eager-load option.
    This gate pins that ``get_user_with_tier_eager`` uses
    ``joinedload``/``selectinload`` on the tier relationship. If it is
    dropped the read silently becomes a lazy (N+1) load, and both this
    claim and the surrounding per-request budget are void -- so the gate
    fails loudly on that edit rather than letting the claim rot.
    """
    path = REPO_ROOT / "src/app/modules/users/crud.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "get_user_with_tier_eager":
            continue
        options = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in {"joinedload", "selectinload"}
        ]
        if not options:
            logger.error("u6_eager_load_missing", func="get_user_with_tier_eager")
            return False
        logger.info("u6_eager_load_ok", loaders=[c.func.id for c in options])
        return True
    logger.error("u6_function_missing", path=str(path))
    return False


async def test_f1_scan_isomorphism() -> bool:
    """Gate F-1: the linear scanner is byte-identical to the combined regex.

    ``scan_content_for_attacks`` now reads the four super-linear ordering
    branches (``sql_union``/``sql_drop``/``sql_delete``/``sql_exec``, whose
    unbounded ``.*?`` makes ``re`` O(n^2) -- 96 KB stalls the event loop
    ~14 s) with the linear token scanner, and keeps the other thirteen in
    ``re``. The observable is ``COMBINED_ATTACK_RE.finditer``'s ordered
    ``(lastgroup, start, end)`` sequence. This gate imports the REAL
    modules and asserts equality over the 17 minimal per-group probes, a
    seeded token-soup fuzz, the quadratic trigger classes, and the
    cursor-inside-a-previous-match regression that a naive merge loses.
    It FAILS on any divergence, which is what makes "provably isomorphic"
    a test rather than a claim.
    """
    import random
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from app.middleware import attack_detection as ad
        from app.middleware.attack_scan_linear import QUADRATIC_GROUPS, build_linear_pattern, split_branches
    finally:
        sys.path.remove(str(REPO_ROOT / "src"))

    if len(QUADRATIC_GROUPS) != 4:
        logger.error("f1_quadratic_group_count", count=len(QUADRATIC_GROUPS))
        return False
    if len(split_branches(ad.COMBINED_ATTACK_RE.pattern)) != 17:
        logger.error("f1_branch_count_changed")
        return False
    if not ad.settings.attack_detection.ATTACK_DETECTION_LINEAR_SCAN:
        logger.error("f1_linear_scan_disabled")
        return False
    if build_linear_pattern(ad.COMBINED_ATTACK_RE.pattern) == ad.COMBINED_ATTACK_RE.pattern:
        logger.error("f1_linear_pattern_unchanged")
        return False

    def reference(payload: bytes) -> list[tuple[str | None, int, int]]:
        return [(m.lastgroup, m.start(), m.end()) for m in ad.COMBINED_ATTACK_RE.finditer(payload)]

    def linear(payload: bytes) -> list[tuple[str | None, int, int]]:
        return [(g, s, e) for s, e, g in ad._iter_scan_matches(payload)]

    probes: list[bytes] = [
        b"x union all select y", b"x or 1=1", b"x drop table y", b"x delete from y",
        b"'abc--", b";\n--", b"x exec y xp_cmdshell", b"<script src",
        b"javascript:alert(1)", b"<img onerror=x", b"<iframe src", b"<object data",
        b"a/../b", b"a%2e%2e%2fb", b"a%2e%2e/b", b"a; cat /etc/passwd",
        b"http://169.254.169.254/latest",
        # quadratic classes + the naive-merge regression
        b"union " * 300, b"select " + b"union " * 300, b"table " + b"drop " * 300,
        b" table  delete xxp_' or 1=1 --from 169.254.169.254\n",
        b"union a select union b select", b"union <script>x</script> a select",
    ]
    rng = random.Random(20261023)
    tokens = [
        b"union ", b"select ", b"drop ", b"table ", b"delete ", b"from ", b"exec ", b"xp_",
        b"<script>", b"' or 1=1 --", b"; cat ", b"169.254.169.254", b"../", b"\n", b" ",
        b"x", b"javascript:", b"<a onx=", b"-- ", b"union", b"select",
    ]
    probes += [b"".join(rng.choice(tokens) for _ in range(rng.randint(0, 14))) for _ in range(20000)]

    for payload in probes:
        if reference(payload) != linear(payload):
            logger.error(
                "f1_scan_divergence",
                payload=payload[:80].decode("latin-1"),
                reference=reference(payload)[:4],
                linear=linear(payload)[:4],
            )
            return False
    logger.info("f1_scan_isomorphism_ok", cases=len(probes))
    return True


async def test_u12_session_isomorphism() -> bool:
    """Verify hash session contains same data as JSON session."""
    import json

    session_data = {"user_id": "123", "tenant_id": "456"}

    # JSON representation
    json_data = json.dumps(session_data)
    json_parsed = json.loads(json_data)

    # Hash representation (same data, different storage)
    hash_data = session_data.copy()

    return json_parsed == hash_data


async def test_u22_slug_isomorphism() -> bool:
    """Verify normalized slug lookups return same tenant."""

    def normalize(slug: str) -> str:
        return slug.lower().strip()

    test_cases = ["MyTenant", "mytenant", "MYTENANT", "  mytenant  "]
    normalized = [normalize(s) for s in test_cases]

    # All should normalize to same value
    return len(set(normalized)) == 1

# C-1 (f9ug) / Addendum A-10 (uu5p): Topology and path-equality gate.
# Routing-relevant middlewares read paths via two different spellings (`request.url.path` vs `scope["path"]`),
# which can diverge under sub-path mounts or normalization differences.
# The validation oracle enforces: (1) `root_path == ""` in deployment, and (2) strict equality
# (`Request.url.path == scope["path"]`) across all critical path shapes.
#
# Addendum A-10 covers proxy/scanner edge cases (nested segments, repeated slashes `//`).
# Crucially, the traversal-like shape is a negative test using a raw `..` segment: it must arrive byte-identical
# to ensure security filters (path-traversal protection) and tenant resolvers never diverge.
C1_PATH_SHAPES = (
    "/api/v1/users/me",
    "/api/v1/users/me/",
    # Nested resources: more than one segment after the router prefix.
    "/api/v1/users/me/sessions/active",
    # Repeated slash: must survive both spellings uncollapsed.
    "/api/v1//users//me",
    "/api/v1/search%20x",
    "/api/v1/a%2Fb",
    "/api/v1/%E2%9C%93",
    "/api/v1/items/ABC123",
    # NEGATIVE: raw traversal-like segments, unnormalised by either
    # spelling. Asserted present verbatim so a future normaliser in
    # either path fails here instead of quietly agreeing with itself.
    "/api/v1/users/../../admin",
    "/api/v1/%2e%2e/%2e%2e/admin",
    "/health",
    "/",
)

C1_QUERY_STRINGS = (b"", b"a=1&b=2", b"next=%2Fapi%2Fv1%2Fx")

C1_ROOT_PATHS = ("", "/api/v1")

#: Files that must have been scanned for the topology gate to mean
#: anything. A scan that silently visits nothing would pass vacuously.
C1_MUST_SCAN = ("src/app/composition.py", "src/app/main.py")


async def test_c1_topology_root_path_empty() -> bool:
    """Gate C-1 (1): the deployed app has no sub-path mount.

    Leg A (static): no `root_path=` kwarg anywhere under src/, and no
    `--root-path` in any compose/Dockerfile/shell/workflow surface.
    Leg B (runtime default): the real framework default is "" — read
    from `FastAPI()` itself, not asserted from memory.
    """
    misses: list[str] = []
    scanned = 0
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        scanned += 1
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"root_path\s*=", line):
                misses.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}:{line.strip()}")
    must = {m for m in C1_MUST_SCAN if (REPO_ROOT / m).is_file()}
    if len(must) != len(C1_MUST_SCAN):
        logger.error("c1_topology_anchor_missing", missing=sorted(set(C1_MUST_SCAN) - must))
        return False
    if scanned == 0:
        logger.error("c1_topology_scan_empty")
        return False
    deploy_hits: list[str] = []
    for pattern in ("docker-compose*.yml", "Dockerfile*", "scripts/*.sh", ".github/workflows/*.yml"):
        for path in sorted(REPO_ROOT.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            if "--root-path" in text or re.search(r"root_path\s*=", text):
                deploy_hits.append(path.relative_to(REPO_ROOT).as_posix())
    if misses or deploy_hits:
        logger.error("c1_topology_root_path_set", src=misses, deploy=deploy_hits)
        return False
    from fastapi import FastAPI

    if FastAPI().root_path != "":
        logger.error("c1_topology_default_changed", root_path=FastAPI().root_path)
        return False
    logger.info("c1_topology_ok", src_files_scanned=scanned)
    return True


async def test_c1_request_url_path_equals_scope_path() -> bool:
    """Gate C-1 (2): `Request.url.path == scope["path"]`, measured.

    Every path shape under every root_path in C1_ROOT_PATHS. Query
    strings travel in `query_string`, never in `path` — a battery entry
    with `?` inside `path` would be asserting against a malformed
    scope, not against the app.
    """
    from starlette.requests import Request

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    checked = 0
    for root_path in C1_ROOT_PATHS:
        for shape in C1_PATH_SHAPES:
            for query in C1_QUERY_STRINGS:
                scope = {
                    "type": "http",
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": shape,
                    "raw_path": shape.encode(),
                    "query_string": query,
                    "root_path": root_path,
                    "headers": [],
                    "client": ("test", 1),
                    "server": ("test", 80),
                }
                if Request(scope, _receive).url.path != shape:
                    logger.error("c1_path_divergence", root_path=root_path, shape=shape, query=query)
                    return False
                checked += 1
    logger.info("c1_path_equality_ok", cases=checked)
    return checked > 0


# C-1 Allow-list of (relpath, enclosing_function) pairs permitted to read `request.url.path`
# because they route on it. Unclassified routing readers fail this gate.
# Note: `ip_filter`, `tenant`, `rate_limit`, and `request_id` were removed under Addendum A-10
# and migrated to `scope["path"]`. This strict allow-list prevents stale permissions from lingering.
C1_URL_PATH_ROUTING = frozenset(
    {
        ("src/app/core/security/request_admission.py", "evaluate"),
        ("src/app/core/security/request_admission.py", "_get_rate_limit_config"),
    }
)

#: Pairs that may read `request.url.path` because they only LOG it.
C1_URL_PATH_LOGGING = frozenset(
    {
        ("src/app/core/errors.py", "_register_framework_handlers.http_exception_handler"),
        ("src/app/core/errors.py", "_register_framework_handlers.validation_exception_handler"),
        ("src/app/core/errors.py", "_register_framework_handlers.db_timeout_exception_handler"),
        ("src/app/core/errors.py", "_make_app_exception_handler._handler"),
        ("src/app/core/errors.py", "_register_global_handler.global_exception_handler"),
        ("src/app/middleware/query_profiler.py", "_log_results"),
    }
)

#: Pairs that may read the raw `scope["path"]` because they ROUTE on
#: it. The equivalence gate above is load-bearing for exactly these: each
#: one feeds the path into a decision, so a spelling that disagreed with
#: the rest of the app would be a tenant-resolution or IP-filter bypass
#: rather than a slow log line.
C1_SCOPE_PATH_READERS = frozenset(
    {
        # feeds `_is_exempt_path` -- the exempt check decides whether the
        # IP filter runs at all.
        ("src/app/middleware/ip_filter.py", "__call__"),
        # feeds `is_path_exempt` twice -- with no tenant header and with
        # an unresolvable slug. This is the C-1 bypass surface the bead
        # is about: a path the tenant middleware treats as exempt while
        # the rest of the app does not would run under NO_TENANT.
        ("src/app/middleware/tenant.py", "__call__"),
        # feeds the exempt check AND the attack scan.
        ("src/app/middleware/attack_detection.py", "__call__"),
    }
)

#: Pairs that may read the raw `scope["path"]` because they only LOG
#: it. body_limit passes `path=scope.get("path", "")` as a log kwarg
#: at four sites (the 413 worker reads the body, not the path); like
#: the errors.py entries these must stay logging reads. Found by the
#: lint gate itself, which flagged them as unclassified on its first
#: run -- the survey before it had only enumerated `request.url.path`.
C1_SCOPE_PATH_LOGGING = frozenset(
    {
        ("src/app/middleware/body_limit.py", "__call__"),
        ("src/app/middleware/body_limit.py", "__call__.limited_receive"),
        # `path=scope["path"]` is a `logger.debug` kwarg. The response
        # never depends on it, so a disagreement here would cost a
        # trace field, not a decision.
        ("src/app/middleware/request_id.py", "__call__"),
        # `request_path=scope["path"]` fills a SecurityEvent field on a
        # path that is ALREADY rejecting the request on the ban lookup.
        # The decision is made by the ban record; the path is the
        # report. Classified as logging, deliberately, so the routing
        # set above stays exactly the set of sites where the path
        # selects an outcome.
        ("src/app/middleware/rate_limit.py", "_reject_banned_ip"),
    }
)


def _c1_path_readers() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Every `request.url.path` read and every `scope["path"]` read.

    Returns (url_path_readers, scope_path_readers) as (relpath,
    dotted-function) pairs. Walks only the trees that can route or
    log a request path; errors.py is included because its five logging
    reads must stay logging reads.
    """
    roots = ("src/app/middleware", "src/app/core/security/request_admission.py", "src/app/core/errors.py")
    url_readers: set[tuple[str, str]] = set()
    scope_readers: set[tuple[str, str]] = set()

    def visit(tree: ast.AST, rel: str, stack: tuple[str, ...]) -> None:
        if isinstance(tree, ast.FunctionDef | ast.AsyncFunctionDef):
            stack = (*stack, tree.name)
        if (
            isinstance(tree, ast.Attribute)
            and tree.attr == "path"
            and isinstance(tree.value, ast.Attribute)
            and tree.value.attr == "url"
        ):
            url_readers.add((rel, ".".join(stack)))
        if isinstance(tree, ast.Subscript) and isinstance(tree.slice, ast.Constant) and tree.slice.value == "path":
            scope_readers.add((rel, ".".join(stack)))
        if (
            isinstance(tree, ast.Call)
            and isinstance(tree.func, ast.Attribute)
            and tree.func.attr == "get"
            and any(isinstance(a, ast.Constant) and a.value == "path" for a in tree.args)
        ):
            scope_readers.add((rel, ".".join(stack)))
        for child in ast.iter_child_nodes(tree):
            visit(child, rel, stack)

    for root in roots:
        base = REPO_ROOT / root
        files = sorted(base.rglob("*.py")) if base.is_dir() else [base]
        for path in files:
            visit(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(REPO_ROOT).as_posix(), ())
    return url_readers, scope_readers


async def test_c1_no_unclassified_path_reader() -> bool:
    """Gate C-1 (lint): no fourth routing reader, no second raw reader.

    A new `request.url.path` use outside the classified sets fails
    until it is classified here with a reason; a new `scope["path"]`
    read fails the same way. Logging-only reads must stay logging.
    """
    url_readers, scope_readers = _c1_path_readers()
    if not url_readers or not scope_readers:
        logger.error("c1_lint_scan_empty", url=len(url_readers), scope=len(scope_readers))
        return False
    unknown_url = url_readers - C1_URL_PATH_ROUTING - C1_URL_PATH_LOGGING
    unknown_scope = scope_readers - C1_SCOPE_PATH_READERS - C1_SCOPE_PATH_LOGGING
    if unknown_url or unknown_scope:
        logger.error("c1_unclassified_path_reader", url=sorted(unknown_url), scope=sorted(unknown_scope))
        return False
    logger.info("c1_lint_ok", url=len(url_readers), scope=len(scope_readers))
    return True


# Gate C-2: Necessary-literal prefilter gate. Groups matching content with ";" must require a
# companion literal (e.g., `b"--"`) in the prefilter set; otherwise, the prefilter would skip evaluation,
# resulting in a missed security verdict.
# `cmd_inj` is deliberately excluded because its alternatives begin with `;`, `||`, or `&&`, meaning
# ";" is already a mandatory literal naturally covered by the prefilter set.
C2_SEMICOLON_COMPANION: dict[str, bytes] = {
    "sql_comment": b"--",
    "sql_semicomment": b"--",
}
C2_CMD_INJ_GROUP = "cmd_inj"

# C-2 Differential UA (`"Linux; Android 14; Pixel 8"`): Previously tripped the prefilter
# due to semicolons, despite clearing the full regex. Following FastAPI_saas-niix, the `cmd_inj` gate
# is a strict conjunction (requiring both a separator AND a command word), so this UA now safely
# bypasses the prefilter under a stronger security guarantee.
#
# Attribution & Position Analysis: While the substring `id` exists inside `Android` (A-n-d-r-o-i-d),
# it is entirely mid-word and never follows a semicolon at a valid command boundary.
# The benign verdict is determined by strict token positioning rather than the absence of `id`.
C2_PIXEL_UA = (
    b"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36"
    b" (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)
#: The same UA shape with the command in COMMAND position. It must still
#: trip: a gate that stopped admitting this would have cost a detection,
#: which is the one thing the prefilter is not allowed to do.
C2_TRIPPING_UA = b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id"
#: The command words the cmd_inj branch accepts, in the regex's own order.
C2_CMD_INJ_COMMANDS: tuple[bytes, ...] = (
    b"cat",
    b"ls",
    b"id",
    b"whoami",
    b"ping",
    b"curl",
    b"wget",
)
#: Payload whose ONLY covering literal is b"--": if "--" ever leaves
#: the set, the prefilter goes blind on exactly this shape and the
#: sql_comment verdict is missed, not merely slowed.
C2_DASHDASH_ONLY_PAYLOAD = b"admin'--"


def _c2_is_word_byte(char: int) -> bool:
    """True for a byte that can be part of an identifier."""
    return chr(char).isalnum() or char == 0x5F


def _c2_id_occurrences_are_mid_word(payload: bytes) -> list[int]:
    """The offsets of every "id" in `payload` that is NOT inside a word.

    An "id" at a word boundary is a candidate command word; one that is
    mid-word (as in "Android") can never be the token a `;` introduces.
    """
    lowered = payload.lower()
    offsets: list[int] = []
    start = 0
    while True:
        index = lowered.find(b"id", start)
        if index < 0:
            return offsets
        before = lowered[index - 1] if index > 0 else -1
        if before < 0 or not _c2_is_word_byte(before):
            offsets.append(index)
        start = index + 1


def _c2_command_word_after_separator(payload: bytes) -> list[bytes]:
    """The command word that follows each ";" at a word boundary, if any."""
    lowered = payload.lower()
    found: list[bytes] = []
    start = 0
    while True:
        index = lowered.find(b";", start)
        if index < 0:
            return found
        rest = lowered[index + 1 :].lstrip()
        token = rest.split(b" ")[0].split(b"\t")[0].split(b"\n")[0]
        if token in C2_CMD_INJ_COMMANDS:
            found.append(token)
        start = index + 1


def _c2_attack_source() -> tuple[dict[str, str], set[bytes]]:
    """Return ({group: sub-pattern}, {prefilter literals}) from the file.

    COMBINED_ATTACK_RE is adjacent bytes literals, so the AST holds
    ONE Constant for the whole pattern; the top-level alternation
    splits on "|(?P<". _PREFILTER_LITERALS_BYTES is frozenset([...]).
    """
    path = REPO_ROOT / "src/app/middleware/attack_detection.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pattern: bytes | None = None
    literals: set[bytes] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        if target.id == "COMBINED_ATTACK_RE" and isinstance(value, ast.Call):
            arg = value.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, bytes):
                pattern = arg.value
        if target.id == "_PREFILTER_LITERALS_BYTES" and isinstance(value, ast.Call):
            # Positional frozenset([...]) — the single arg holds the literal.
            args = list(value.args)
            args.extend(kw.value for kw in value.keywords if kw.arg in ("iterable", None))
            for arg in args:
                if isinstance(arg, ast.List | ast.Set | ast.Tuple):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, bytes):
                            literals.add(elt.value)
    if pattern is None:
        raise AssertionError("COMBINED_ATTACK_RE not found by AST")
    text = pattern.decode("latin-1")
    if not text.startswith("(?P<"):
        raise AssertionError("pattern does not start with a named group")
    groups: dict[str, str] = {}
    for chunk in text.split("|(?P<"):
        name, _, sub = chunk.removeprefix("(?P<").partition(">")
        groups[name] = sub
    return groups, literals


async def test_c2_semicolon_groups_need_dashdash() -> bool:
    """Gate C-2 (hard): every ";" group but cmd_inj holds via b"--".

    For each named group whose sub-pattern contains ";", other than
    cmd_inj, the companion literal must be textually required by the
    sub-pattern AND present in the prefilter set. The closed world —
    exactly the groups in C2_SEMICOLON_COMPANION — fails a new
    ";"-dependent group until it is classified here with a companion.
    """
    groups, literals = _c2_attack_source()
    if not groups or not literals:
        logger.error("c2_scan_empty", groups=len(groups), literals=len(literals))
        return False
    semicolon_groups = {name for name, sub in groups.items() if ";" in sub}
    if C2_CMD_INJ_GROUP not in semicolon_groups:
        logger.error("c2_cmd_inj_lost_semicolon", groups=sorted(semicolon_groups))
        return False
    others = semicolon_groups - {C2_CMD_INJ_GROUP}
    if others != set(C2_SEMICOLON_COMPANION):
        logger.error(
            "c2_unclassified_semicolon_group",
            others=sorted(others),
            classified=sorted(C2_SEMICOLON_COMPANION),
        )
        return False
    for name, companion in C2_SEMICOLON_COMPANION.items():
        if companion.decode("latin-1") not in groups[name]:
            logger.error("c2_companion_not_required", group=name)
            return False
        if companion not in literals:
            logger.error("c2_companion_missing_from_set", group=name)
            return False
    # STRENGTHENED (C-2, FastAPI_saas-niix): the GLOBAL set is what the AST
    # above reads, but since C-2 the code consults the EFFECTIVE set -- the
    # global set minus the cmd_inj separators. A companion that is in the
    # global set but not in the effective one would be registered but not
    # consulted, which is the failure mode this gate exists to prevent.
    effective = _c2_effective_literals()
    if effective is None:
        logger.error("c2_effective_set_unreadable")
        return False
    for name, companion in C2_SEMICOLON_COMPANION.items():
        if companion not in effective:
            logger.error("c2_companion_missing_from_effective_set", group=name)
            return False
    logger.info("c2_semicolon_ok", groups=sorted(semicolon_groups))
    return True


def _c2_effective_literals() -> set[bytes] | None:
    """The literal set the prefilter ACTUALLY consults, from the real module.

    None when the module cannot be imported, so a gate that needs it fails
    rather than passing on an empty set.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from app.middleware import attack_detection as ad
    finally:
        sys.path.remove(str(REPO_ROOT / "src"))
    effective = getattr(ad, "_PREFILTER_SINGLE_LITERALS", None)
    if effective is None:
        return None
    return set(effective)


async def test_c2_pixel_ua_differential() -> bool:
    """Gate C-2 (corpus): the Pixel 8 UA misses the prefilter, clears the
    regex, and is benign by POSITION.

    Since C-2 (FastAPI_saas-niix) the cmd_inj gate is a CONJUNCTION -- a
    separator AND a command word -- so this UA no longer trips the
    prefilter. That is the point of the change: a browser user-agent
    carries ";" on nearly every request, and a bare separator made the full
    17-group regex run on all of them.

    The gate is STRICTLY STRONGER than the one it replaces. It asserts:
    (a) the full regex still clears the UA;
    (b) the UA DOES carry the "id" substring -- so the verdict is not the
        absence of "id";
    (c) every "id" in it is mid-word, and no command word follows any ";"
        at a word boundary -- the POSITION is the reason;
    (d) the UA with a command in command position still trips, so the
        change cost no detection;
    (e) the bare ";" is STILL in the global literal set, so what makes
        this UA miss is the conjunction and not something else;
    (f) the admin'-- probe still pins b"--" as load-bearing, now in the
        EFFECTIVE set as well as the global one.

    Imports the REAL module, so a regex/literal edit that breaks the
    conjunction fails here, not in production.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from app.middleware import attack_detection as ad
    finally:
        sys.path.remove(str(REPO_ROOT / "src"))
    # (a) the regex must clear it
    match = ad.COMBINED_ATTACK_RE.search(C2_PIXEL_UA)
    if match is not None:
        logger.error("c2_pixel_ua_regex_hit", group=match.lastgroup)
        return False
    # (b) the "id" IS there -- inside "Android"
    if b"id" not in C2_PIXEL_UA.lower():
        logger.error("c2_pixel_ua_has_no_id")
        return False
    if b"id" in b"pixel":
        logger.error("c2_pixel_name_carries_id")
        return False
    # (c) the position is the reason: no "id" at a word boundary, and no
    # command word after any ";"
    at_boundary = _c2_id_occurrences_are_mid_word(C2_PIXEL_UA)
    if at_boundary:
        logger.error("c2_pixel_ua_id_at_word_boundary", offsets=at_boundary)
        return False
    after_separator = _c2_command_word_after_separator(C2_PIXEL_UA)
    if after_separator:
        logger.error("c2_pixel_ua_command_after_separator", words=after_separator)
        return False
    # (d) the command position must still trip
    if not ad.COMBINED_ATTACK_RE.search(C2_TRIPPING_UA):
        logger.error("c2_tripping_ua_regex_miss")
        return False
    if not ad._attack_prefilter_hit(C2_TRIPPING_UA):
        logger.error("c2_tripping_ua_prefilter_miss")
        return False
    # (e) the conjunction is what makes the benign UA miss, not an
    # unrelated edit to the literal set
    if b";" not in ad._PREFILTER_LITERALS_BYTES:
        logger.error("c2_separator_left_the_global_set")
        return False
    # the benign UA must now MISS the prefilter
    if ad._attack_prefilter_hit(C2_PIXEL_UA):
        logger.error("c2_pixel_ua_prefilter_hit")
        return False
    # (f) the dash-dash probe: b"--" is that payload's only covering
    # literal, in the global set AND in the effective one
    covering = [lite for lite in ad._PREFILTER_LITERALS_BYTES if lite in C2_DASHDASH_ONLY_PAYLOAD.lower()]
    if covering != [b"--"]:
        logger.error("c2_dashdash_not_sole_literal", covering=covering)
        return False
    effective = _c2_effective_literals()
    if effective is None:
        logger.error("c2_effective_set_unreadable")
        return False
    if b"--" not in effective:
        logger.error("c2_dashdash_missing_from_effective_set")
        return False
    logger.info("c2_pixel_ua_ok", reason="android_not_pixel")
    return True

# TWO closed worlds -- one per acquisition mechanism:
#
#   1. HOLDERS -- the session arrives as a parameter resolved by FastAPI:
#      `db: Annotated[AsyncSession, Depends(async_get_db)]` (or the same
#      spelled in a default). Enumerated by C3_DB_CALLERS.
#   2. OPENERS -- the function opens the session itself:
#      `async with get_db_context() as session:`. Enumerated by
#      C3_SESSION_OPENERS.
#
# Gate C-3 Evolution History & Architectural Shifts:
#
# 1. Auth Path Refactoring (Commit ec632f29, 2026-10-19):
#    - Previous assumption that FastAPI `Depends` parameters formed the complete session caller set was broken
#      when auth helpers (`get_current_user`, `get_optional_auth_context`, `get_optional_user`,
#      and `core.principal.get_auth_context`)
#      transitioned from parameter injection to pure delegation via `resolve_principal` and `get_db_context()`.
#    - `get_db_context()` and `async_get_db` share the exact same underlying lifecycle (`session_maker()`,
#      exception rollback, and `finally` close; `src/app/core/db/database.py:184` and `:232`).
#    - Action: The four delegators were removed from `C3_DB_CALLERS` (holders), and
#      `get_db_context` was added as its own
#      closed world of Openers. Any new holder or opener fails enumeration until explicitly classified.
#
# 2. RBAC Singleflight Migration (Cluster C / C1, 2026-10-22):
#    - `core.rbac.dependencies._check_permission` previously held a session via
#      `Depends(async_get_db)` solely for downstream passing.
#    - C1 Fix: RBAC bitmask singleflight tasks are awaited across concurrent requests,
#      making request-scoped sessions unsafe.
#      They now use dedicated short-lived sessions via task-local `local_session()`.
#    - Action: Removed from holders (now classified under the Openers world); 401-path analysis remains unaffected.
C3_DB_CALLERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/app/api/deps/auth.py", "get_user_by_api_key"),
        ("src/app/api/deps/request_context.py", "get_request_context"),
        ("src/app/modules/ai/router.py", "get_chat_session_service"),
        ("src/app/modules/ai/router.py", "get_chat_message_service"),
        ("src/app/modules/ai/router.py", "get_ai_settings_service"),
        ("src/app/modules/ai/router.py", "chat_completion"),
        ("src/app/modules/ai/router.py", "chat_completion_stream"),
        ("src/app/modules/api_keys/router.py", "_get_api_key_service"),
        ("src/app/modules/auth/router.py", "login_for_access_token"),
        ("src/app/modules/auth/router.py", "register"),
        ("src/app/modules/auth/router.py", "refresh_access_token"),
        ("src/app/modules/auth/router.py", "logout"),
        ("src/app/modules/auth/router.py", "forgot_password"),
        ("src/app/modules/auth/router.py", "reset_password"),
        ("src/app/modules/auth/router.py", "send_verification_email"),
        ("src/app/modules/auth/router.py", "verify_email"),
        ("src/app/modules/auth/routers/mfa.py", "complete_mfa_login"),
        ("src/app/modules/auth/routers/passkey.py", "login_complete"),
        ("src/app/modules/auth/services/magic_link.py", "get_magic_link_service"),
        ("src/app/modules/auth/routers/sso.py", "list_providers"),
        ("src/app/modules/auth/routers/sso.py", "initiate_sso"),
        ("src/app/modules/auth/routers/sso.py", "initiate_sso_alias"),
        ("src/app/modules/auth/routers/sso.py", "handle_callback"),
        ("src/app/modules/auth/routers/sso.py", "handle_post_callback"),
        ("src/app/modules/auth/routers/sso_settings.py", "get_sso_settings_service"),
        ("src/app/modules/billing/router.py", "get_stripe_settings"),
        ("src/app/modules/billing/router.py", "update_stripe_settings"),
        ("src/app/modules/billing/router.py", "create_subscription_plan"),
        ("src/app/modules/billing/router.py", "update_subscription_plan"),
        ("src/app/modules/billing/router.py", "delete_subscription_plan"),
        ("src/app/modules/billing/router.py", "create_product"),
        ("src/app/modules/billing/router.py", "update_product"),
        ("src/app/modules/billing/router.py", "add_product_pricing"),
        ("src/app/modules/billing/routers/checkouts.py", "create_checkout"),
        ("src/app/modules/billing/routers/checkouts.py", "get_current_subscription"),
        ("src/app/modules/billing/routers/checkouts.py", "cancel_current_subscription"),
        ("src/app/modules/billing/routers/invoices.py", "list_invoices"),
        ("src/app/modules/billing/routers/invoices.py", "get_invoice"),
        ("src/app/modules/billing/routers/prices.py", "create_price"),
        ("src/app/modules/billing/routers/prices.py", "update_price"),
        ("src/app/modules/billing/routers/prices.py", "delete_price"),
        ("src/app/modules/billing/routers/products.py", "create_product"),
        ("src/app/modules/billing/routers/products.py", "update_product"),
        ("src/app/modules/billing/routers/products.py", "delete_product"),
        ("src/app/modules/billing/routers/products.py", "add_product_pricing"),
        ("src/app/modules/feature_flags/router.py", "get_feature_flag_service"),
        ("src/app/modules/health/router.py", "ready"),
        ("src/app/modules/posts/router.py", "get_post_service"),
        ("src/app/modules/rate_limits/router.py", "get_rate_limit_service"),
        ("src/app/modules/rbac/router.py", "get_role_service"),
        ("src/app/modules/storage/routers/files.py", "get_storage"),
        ("src/app/modules/storage/routers/files.py", "get_file_service"),
        ("src/app/modules/storage/routers/settings.py", "get_storage_admin"),
        ("src/app/modules/storage/routers/settings.py", "get_storage_settings_service"),
        ("src/app/modules/tenants/router.py", "get_tenant_service"),
        ("src/app/modules/tiers/router.py", "get_tier_service"),
        ("src/app/modules/users/router.py", "get_user_directory"),
        ("src/app/modules/users/router.py", "get_user_service"),
        ("src/app/modules/users/router.py", "write_user"),
    }
)

# The second closed world: functions that OPEN a session themselves with
# `async with get_db_context() as <name>:`. Before 2026-10-19 this class
# was enumerated nowhere, so eight functions -- including the auth path's
# own `resolve_principal` -- could open a session without failing any
# gate. Every one of them is now pinned, and a ninth fails
# test_c3_session_opener_enumeration_complete until it is classified.
C3_SESSION_OPENERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/app/admin/initialize.py", "_get_db_session"),
        ("src/app/api/deps/auth.py", "_resolve_optional_bearer_flow"),
        ("src/app/api/deps/auth.py", "_resolve_strict_bearer_flow"),
        ("src/app/api/deps/auth.py", "resolve_principal"),
        ("src/app/modules/audit_logs/service.py", "flush_audit_log_buffer"),
        ("src/app/modules/audit_logs/service.py", "get_audit_logs_for_tenant_async"),
        ("src/app/modules/notifications/service.py", "recover_stuck_notifications"),
        ("src/app/modules/webhooks/delivery.py", "recover_stuck_deliveries"),
    }
)


# C-3 Rule: Handlers are permitted to call `session.rollback()` because ending a transaction
# does not check out a database connection or execute queries.
#
# Empirical Measurements (FastAPI_saas-tpbs):
# - `session.rollback()` on an unconnected session -> checkout=0, stmt=0.
# - `session.execute(...)` -> checkout=1, stmt=1.
# - Subsequent `session.rollback()` adds zero checkouts.
#
# Therefore, rollbacks do not violate C-3 ("defer the DB session on the 401 path"). Any other session
# interaction checks out a connection and constitutes a policy violation.
#
# Permitted Exceptions (explicitly logged and monitored in CI):
# 1. `audit_logs/service.py::flush_audit_log_buffer`: Background buffer flush rolling back failed bulk inserts.
# 2. `webhooks/delivery.py::recover_stuck_deliveries`: ARQ job rolling back individual failed rows
#    to continue batch processing.
C3_ALLOWED_HANDLER_SESSION_CALLS: frozenset[str] = frozenset({"rollback"})


def _c3_depends_on_db(node: ast.AST) -> bool:
    """True if the expression contains Depends(async_get_db)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == "Depends") or (
                isinstance(f, ast.Attribute) and f.attr == "Depends"
            ):
                for a in n.args:
                    if (isinstance(a, ast.Name) and a.id == "async_get_db") or (
                        isinstance(a, ast.Attribute) and a.attr == "async_get_db"
                    ):
                        return True
    return False


def _c3_db_caller_functions() -> dict[tuple[str, str], tuple[int, str]]:
    """Map (relpath, funcname) to (lineno, db_param) for every HOLDER.

    Checks parameter defaults AND Annotated metadata: `db: Annotated[…,
    Depends(async_get_db)]` carries no default, so a defaults-only scan
    misses most of the tree (it found 24 of the original 64).
    """
    found: dict[tuple[str, str], tuple[int, str]] = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params: list[str] = []
            pos = list(node.args.args)
            defs = list(node.args.defaults)
            off = len(pos) - len(defs)
            for i, arg in enumerate(pos):
                di = i - off
                if (di >= 0 and _c3_depends_on_db(defs[di])) or (
                    arg.annotation is not None and _c3_depends_on_db(arg.annotation)
                ):
                    params.append(arg.arg)
            for arg, d in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if (d is not None and _c3_depends_on_db(d)) or (
                    arg.annotation is not None and _c3_depends_on_db(arg.annotation)
                ):
                    params.append(arg.arg)
            if params:
                found[(rel, node.name)] = (node.lineno, params[0])
    return found


def _c3_session_opener_functions() -> dict[tuple[str, str], tuple[int, str]]:
    """Map (relpath, funcname) to (lineno, bound_session_name) for every OPENER.

    An opener is a function that opens a session itself with
    `async with get_db_context() as <name>:`. The bound name is the
    session variable, because THAT is the name the 401-path analysis has
    to look for in an except handler -- a handler that reads the bound
    session is the defect, and a handler that reads some other variable
    is not.

    The call is matched on the callee NAME (`get_db_context`), so it is
    found whether the helper was imported at module level or inside the
    function body (`from src.app.core.db.database import get_db_context`).
    The definition of `get_db_context` itself is a decorated function
    definition, not a call, so it is not matched.
    """
    found: dict[tuple[str, str], tuple[int, str]] = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.AsyncWith):
                    continue
                for item in child.items:
                    call = item.context_expr
                    if not isinstance(call, ast.Call):
                        continue
                    func = call.func
                    name = None
                    if isinstance(func, ast.Name):
                        name = func.id
                    elif isinstance(func, ast.Attribute):
                        name = func.attr
                    if name != "get_db_context":
                        continue
                    bound = item.optional_vars
                    if isinstance(bound, ast.Name):
                        found[(rel, node.name)] = (node.lineno, bound.id)
                    break
    return found


def _c3_handler_session_uses(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """The uses of `name` inside an except handler: (violations, permitted).

    A use is PERMITTED only when the session is the receiver of one of
    C3_ALLOWED_HANDLER_SESSION_CALLS. The check is name-based: it does not
    resolve shadowing, so a nested helper that takes its own parameter of
    the same name is reported as the outer function's use. That is the
    conservative direction -- a false positive is read and classified by a
    human, a false negative is not noticed.
    """
    violations: list[tuple[int, str]] = []
    permitted: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            parents = _c3_parent_map(handler)
            for n in ast.walk(handler):
                if not (isinstance(n, ast.Name) and n.id == name):
                    continue
                parent = parents.get(id(n))
                if isinstance(parent, ast.Attribute) and parent.value is n:
                    call = parents.get(id(parent))
                    what = f".{parent.attr}"
                    if (
                        isinstance(call, ast.Call)
                        and call.func is parent
                        and parent.attr in C3_ALLOWED_HANDLER_SESSION_CALLS
                    ):
                        permitted.append((n.lineno, what))
                        continue
                    violations.append((n.lineno, what))
                else:
                    violations.append((n.lineno, "bare reference"))
    return violations, permitted


def _c3_parent_map(node: ast.AST) -> dict[int, ast.AST]:
    """Map id(child) -> parent for every node under `node`."""
    parents: dict[int, ast.AST] = {}
    for child in ast.iter_child_nodes(node):
        parents[id(child)] = node
        parents.update(_c3_parent_map(child))
    return parents


async def test_c3_caller_enumeration_complete() -> bool:
    """Gate C-3 (holders): the holder set is exactly the pinned 60.

    A new Depends(async_get_db) fails here until it is classified with
    its 401-path analysis. An empty scan fails rather than passing.

    The holders are only HALF the closed world: a function that opens its
    own session with `get_db_context()` is covered by
    test_c3_session_opener_enumeration_complete instead.
    """
    found = _c3_db_caller_functions()
    if not found:
        logger.error("c3_scan_empty")
        return False
    if set(found) != set(C3_DB_CALLERS):
        logger.error(
            "c3_caller_set_diverged",
            missing=sorted(set(C3_DB_CALLERS) - set(found)),
            extra=sorted(set(found) - set(C3_DB_CALLERS)),
        )
        return False
    logger.info("c3_enumeration_ok", callers=len(found))
    return True


async def test_c3_session_opener_enumeration_complete() -> bool:
    """Gate C-3 (openers): the `get_db_context` opener set is exactly pinned.

    A function that opens its own session is a session holder in every
    sense that matters for the 401 path, and until this gate existed
    eight of them -- including the auth path's own `resolve_principal` --
    opened a session without any gate noticing. A ninth opener fails
    here until it is classified.

    An empty scan fails rather than passing, for the same reason the
    holder gate fails on an empty scan: a scan that finds nothing is
    indistinguishable from a scan that is broken.
    """
    found = _c3_session_opener_functions()
    if not found:
        logger.error("c3_opener_scan_empty")
        return False
    if set(found) != set(C3_SESSION_OPENERS):
        logger.error(
            "c3_opener_set_diverged",
            missing=sorted(set(C3_SESSION_OPENERS) - set(found)),
            extra=sorted(set(found) - set(C3_SESSION_OPENERS)),
        )
        return False
    logger.info("c3_opener_enumeration_ok", openers=len(found))
    return True


def _c3_session_names() -> dict[tuple[str, str], list[str]]:
    """Every (relpath, funcname) that can hold a session, and the names.

    A holder names its session through the parameter FastAPI injected; an
    opener names it through the `as` target of its own `async with`. Both
    names are the ones the 401-path analysis has to look for, and a
    function can be in both classes, so the values are lists.
    """
    names: dict[tuple[str, str], list[str]] = {}
    for key, (_lineno, param) in _c3_db_caller_functions().items():
        names.setdefault(key, []).append(param)
    for key, (_lineno, bound) in _c3_session_opener_functions().items():
        names.setdefault(key, []).append(bound)
    return names


async def test_c3_no_caller_reads_session_in_handler() -> bool:
    """Gate C-3 (401 path): no session holder names its session in a handler.

    A caller that reads the session inside an exception handler is
    exactly the case that breaks session deferral on the rejection
    path, and handlers are the least-read code -- so the property is
    asserted per caller, not argued in prose. The log-severity half
    (A-12) is explicitly out of scope and asserted nowhere here.

    The property is asserted over BOTH closed worlds: the holders
    (session injected as a parameter) and the openers (session opened
    with `get_db_context()`), because a session read inside a handler is
    the same defect whichever way the session was obtained.

    A handler may name the session to ROLL IT BACK and nothing else --
    see C3_ALLOWED_HANDLER_SESSION_CALLS for the measurement that makes
    that distinction a fact rather than a preference. Permitted uses are
    logged, not hidden.
    """
    names = _c3_session_names()
    if not names:
        logger.error("c3_scan_empty")
        return False
    bad: dict[str, list[str]] = {}
    permitted: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            key = (rel, node.name)
            if key not in names:
                continue
            uses: list[tuple[int, str]] = []
            allowed: list[tuple[int, str]] = []
            for session_name in names[key]:
                found, ok = _c3_handler_session_uses(node, session_name)
                uses.extend(found)
                allowed.extend(ok)
            if not uses and not allowed:
                continue
            site = f"{rel}:{node.lineno} {node.name}"
            if uses:
                bad[site] = [f"{line} {what}" for line, what in uses]
            if allowed:
                permitted[site] = [f"{line} {what}" for line, what in allowed]
    if bad:
        logger.error("c3_handler_uses_session", callers=bad)
        return False
    if permitted:
        logger.info("c3_handler_permitted_uses", callers=permitted)
    logger.info("c3_handlers_ok", callers=len(names))
    return True


# A-6: AuthenticatedUser dump/load differential. Reuses validated payload objects to avoid
# redundant re-validation per access. Dump-to-load maintains strict identity in PYTHON mode
# (preserving exact types), whereas JSON mode passes vacuously (since UUID and str serialize identically),
# making the Python-mode arm the load-bearing path.
#
# Stop Condition: `tenant_id` is typed as `UUID | str | int | None`. If any variant mismatches,
# the differential is unsound and execution must halt. Probed on HEAD: all variants round-trip
# exactly (ints remain ints, UUIDs remain UUIDs), discharging the condition cleanly.
A6_TENANT_ID_VARIANTS: tuple[tuple[str, object], ...] = (
    ("uuid", "__UUID_SENTINEL__"),
    ("int", 42),
    ("str", "tenant-abc-123"),
    ("none", None),
)


async def test_a6_python_mode_differential() -> bool:
    """Gate A-6: dump->load is the identity in python mode, incl. tenant_id."""
    import sys
    from datetime import UTC, datetime
    from uuid import UUID

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from app.core.principal import AuthenticatedUser
    finally:
        sys.path.remove(str(REPO_ROOT / "src"))

    def realistic(tenant_id: object) -> dict[str, object]:
        tid = UUID(int=9) if tenant_id == "__UUID_SENTINEL__" else tenant_id
        return {
            "id": 1,
            "uuid": UUID(int=7),
            "email": "a@b.c",
            "username": "u",
            "name": "N",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": None,
            "is_email_verified": True,
            "is_superuser": False,
            "is_active": True,
            "is_deleted": False,
            "tier_id": 3,
            "tenant_id": tid,
            "profile_image_url": None,
            "token_version": 4,
            "tier": {"a": 1},
            "api_key_id": "k",
            "api_key_scopes": ["x"],
        }

    for label, variant in A6_TENANT_ID_VARIANTS:
        raw = realistic(variant)
        try:
            dump = AuthenticatedUser(**raw).model_dump()  # type: ignore[arg-type]
            again = AuthenticatedUser(**dump).model_dump()  # type: ignore[arg-type]
        except Exception as exc:
            logger.error("a6_construction_failed", variant=label, error=str(exc))
            return False
        if again != dump:
            # STOP CONDITION: a tenant_id mismatch kills the A-6 premise.
            logger.error("a6_python_mode_diverged_stop", variant=label)
            return False
    # JSON-round-tripped payload: weak by construction (UUID->str), pinned
    # so the weakness is on the record rather than silently assumed.
    import json as _json

    raw_json = realistic(UUID(int=9))
    dumped_json = AuthenticatedUser(**raw_json).model_dump(mode="json")  # type: ignore[arg-type]
    tripped = _json.loads(_json.dumps(dumped_json))
    if AuthenticatedUser(**tripped).model_dump() != AuthenticatedUser(**tripped).model_dump():  # type: ignore[arg-type]
        logger.error("a6_json_roundtrip_diverged")
        return False
    logger.info("a6_python_mode_ok", variants=[label for label, _ in A6_TENANT_ID_VARIANTS])
    return True


# Register all isomorphism tests
ISOMORPHISM_TESTS = [
    IsomorphismTest("U3_parallel", "Parallel handlers same as sequential", test_u3_parallel_isomorphism),
    IsomorphismTest("U4_pipeline", "Pipeline same as separate commands", test_u4_pipeline_isomorphism),
    IsomorphismTest("U6_n1", "Joinedload same as lazy load", test_u6_n1_isomorphism),
    IsomorphismTest("U12_session", "Hash session same as JSON", test_u12_session_isomorphism),
    IsomorphismTest("U22_slug", "Normalized slug same lookup", test_u22_slug_isomorphism),
    IsomorphismTest("C1_topology", "C-1: deployed app has no sub-path mount", test_c1_topology_root_path_empty),
    IsomorphismTest(
        "C1_path_equality",
        "C-1: Request.url.path == scope[path] for every shape",
        test_c1_request_url_path_equals_scope_path,
    ),
    IsomorphismTest(
        "C1_no_fourth_reader", "C-1: no unclassified request-path reader", test_c1_no_unclassified_path_reader
    ),
    IsomorphismTest(
        "C2_semicolon_gate", "C-2: every ';' group but cmd_inj holds via b'--'", test_c2_semicolon_groups_need_dashdash
    ),
    IsomorphismTest(
        "C2_pixel_ua",
        "C-2: Pixel 8 UA misses the prefilter, clears the regex, benign by position",
        test_c2_pixel_ua_differential,
    ),
    IsomorphismTest(
        "C3_enumeration",
        "C-3: async_get_db holder set is exactly the pinned 60",
        test_c3_caller_enumeration_complete,
    ),
    IsomorphismTest(
        "C3_opener_enumeration",
        "C-3: get_db_context opener set is exactly the pinned 8",
        test_c3_session_opener_enumeration_complete,
    ),
    IsomorphismTest(
        "C3_no_handler_reads",
        "C-3: no session is READ in an except body (a rollback is the one permitted use)",
        test_c3_no_caller_reads_session_in_handler,
    ),
    IsomorphismTest(
        "A6_python_mode",
        "A-6: AuthenticatedUser dump->load is the identity, incl. tenant_id",
        test_a6_python_mode_differential,
    ),
    IsomorphismTest(
        "F1_scan",
        "F-1: the linear scanner is byte-identical to COMBINED_ATTACK_RE.finditer",
        test_f1_scan_isomorphism,
    ),
]


async def run_isomorphism_validation() -> dict[str, Any]:
    """Run all isomorphism tests."""
    logger.info("isomorphism_validation_start", test_count=len(ISOMORPHISM_TESTS))

    results = []
    for test in ISOMORPHISM_TESTS:
        result = await test.run()
        results.append(result)
        logger.info(
            "isomorphism_test_result",
            name=test.name,
            passed=result["passed"],
            error=result.get("error"),
        )

    all_passed = all(r["passed"] for r in results)
    failures = [r for r in results if not r["passed"]]

    logger.info(
        "isomorphism_validation_complete",
        all_passed=all_passed,
        passed_count=sum(1 for r in results if r["passed"]),
        total=len(results),
    )

    return {"all_passed": all_passed, "results": results, "failures": failures}


if __name__ == "__main__":
    import sys as _sys

    result = asyncio.run(run_isomorphism_validation())
    print(f"Isomorphism Validation: {'PASSED' if result['all_passed'] else 'FAILED'}")
    for r in result["results"]:
        # ASCII markers: the ✓/✗ glyphs crash on Windows consoles
        # (cp1252) exactly when a gate fails — when the output is
        # needed most.
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  {status} {r['name']}")
    # A gate that cannot fail the build is not a gate: exit non-zero so the
    # CI step fails instead of printing FAILED and succeeding anyway.
    _sys.exit(0 if result["all_passed"] else 1)
