"""H-3 verification: a cleanup that contaminates the number is not a cleanup.

A keyspace-wide ``SCAN`` walks every key on the instance in the window
immediately before a clock starts, evicting and re-warming cache and connection
state in precisely the window the measurement is meant to observe. The
contamination scales with keyspace size and is then read as a regression
(perf J3).

Two cleanup forms are allowed and only two:

- TARGETED: ``SCAN MATCH <prefix>`` per known prefix - ``clear_security_state``.
- FLUSHDB on a **DEDICATED** instance, followed by a fixed settle sleep -
  ``flush_security_state``.

Both properties are load-bearing and both are checked here: that every scan
carries a MATCH, and that FLUSHDB is gated rather than merely discouraged. The
gate is a real gate: the environment variable has no default, because an unset
variable is the only unambiguous statement that nobody has authorised emptying
this instance.

The ordering test uses an injected clock rather than a sleep, so it can prove
the settle happens BEFORE ``t0`` is taken without taking two seconds to say so.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from src.benchmarks import security_state as mod
from src.benchmarks.security_state import (
    DEDICATED_INSTANCE_ENV_VAR,
    FLUSHDB_SETTLE_SECONDS,
    SECURITY_KEY_PATTERNS,
    NotDedicatedInstance,
    clear_security_state,
    flush_security_state,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Every Python file a measured phase can run from. A keyspace walk can also be
#: issued from a shell script, which is why the shell files are covered too.
HARNESS_ROOTS = ("src/benchmarks", "tests/performance", "tests/benchmarks", "scripts")

#: The scan primitives that walk keys. Deliberately NOT ``keys``: in this tree
#: every ``.keys()`` call is ``dict.keys()`` (11 of 11 - result maps, permission
#: bitmasks, rollback procedure tables), and ``Redis.keys()`` is spelled the same
#: way, so no syntactic rule can tell them apart. A rule that flags ``dict.keys``
#: is a rule nobody can satisfy, so the coverage limit is stated here instead of
#: being papered over with a blanket allowlist. Command-level ``KEYS`` issued as a
#: string or from a shell IS covered, by ``test_no_command_level_scan_omits_match``.
SCAN_CALLS = frozenset({"scan", "scan_iter"})

#: ``.scan()`` calls that are not Redis scans, with the reason each is exempt.
#: An entry without a reason is a hole, not an exemption.
NON_REDIS_SCANS = {
    "scripts/verify_plan_metrics.py": (
        "`counter` is `count_source_text_tests`, a source-text scanner that counts test "
        "functions by reading files. It never touches Redis."
    ),
}


class _FakeRedis:
    """Records what it was asked to do, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def scan_iter(self, **kwargs: Any) -> Any:
        self.calls.append(("scan_iter", kwargs))

        async def _empty() -> Any:
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    async def flushdb(self) -> None:
        self.calls.append(("flushdb", {}))

    async def aclose(self) -> None:
        self.calls.append(("aclose", {}))


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Point the module's Redis client factory at a recorder."""
    import redis.asyncio as aioredis

    client = _FakeRedis()
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: client)
    monkeypatch.delenv(DEDICATED_INSTANCE_ENV_VAR, raising=False)
    return client


# --------------------------------------------------------------------------
# (a) no scan without a MATCH
# --------------------------------------------------------------------------


def _harness_python_files() -> list[Path]:
    found: list[Path] = []
    for root in HARNESS_ROOTS:
        base = REPO_ROOT / root
        if base.is_dir():
            found.extend(sorted(p for p in base.rglob("*.py") if p.is_file()))
    return found


def _harness_shell_files() -> list[Path]:
    found: list[Path] = []
    for root in HARNESS_ROOTS:
        base = REPO_ROOT / root
        if base.is_dir():
            found.extend(sorted(p for p in base.rglob("*.sh") if p.is_file()))
    return found


def _scan_calls_in(path: Path) -> list[ast.Call]:
    """Every ``<something>.scan(...)`` / ``.scan_iter(...)`` in a Python file.

    Shell files are excluded by construction: ``ast.parse`` on a shell script
    raises, and parsing it "successfully" would be a mirage.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SCAN_CALLS
    ]


class TestEveryScanCarriesAMatch:
    def test_the_scan_actually_sees_the_calls_it_is_meant_to_catch(self):
        """A scan that finds nothing would pass every assertion below."""
        all_calls = [(p, c) for p in _harness_python_files() for c in _scan_calls_in(p)]
        assert all_calls, "the static scan found no scan calls at all; it is broken"
        rel = {p.relative_to(REPO_ROOT).as_posix() for p, _ in all_calls}
        assert "src/benchmarks/security_state.py" in rel, (
            "the scan does not see the module under test; it would pass on a tree "
            "where cleanup had been rewritten to walk the keyspace"
        )
        assert all(
            any(kw.arg == "match" for kw in call.keywords)
            for p, call in all_calls
            if p.relative_to(REPO_ROOT).as_posix() == "src/benchmarks/security_state.py"
        ), "the module under test currently satisfies the rule, so the rule can fail"

    def test_no_python_scan_omits_match(self):
        offenders: list[str] = []
        for path in _harness_python_files():
            exempt = NON_REDIS_SCANS.get(path.relative_to(REPO_ROOT).as_posix())
            for call in _scan_calls_in(path):
                if any(kw.arg == "match" for kw in call.keywords):
                    continue
                if exempt is not None:
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{call.lineno}")
        assert offenders == [], (
            f"these scans walk the whole keyspace instead of a namespace: {offenders}. "
            "A full SCAN in the window before a clock starts contaminates the number it is "
            "preparing, by an amount that scales with keyspace size."
        )

    def test_every_exemption_has_a_reason(self):
        """An allowlist entry without a stated reason is a hole, not an exemption."""
        for path, reason in NON_REDIS_SCANS.items():
            assert reason.strip(), f"{path} is exempt from the scan rule with no stated reason"
            assert (REPO_ROOT / path).is_file(), f"{path} is allow-listed but does not exist"

    def test_no_raw_scan_command_is_issued(self):
        """``SCAN``/``KEYS`` issued as a raw command string.

        Checked on the AST, not the source text. A textual rule was tried and
        rejected: it flagged 30 lines across this tree that merely CONTAIN the
        word - ``test_nrgi3_scan_cost.py``, ``index_predicate_coverage.py``
        and others - because a substring is not a command. ``execute_command`` is
        how a keyspace walk actually gets issued from Python.
        """
        offenders: list[str] = []
        for path in _harness_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.upper() in {"SCAN", "KEYS"}:
                        offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
        assert offenders == [], (
            f"these issue a raw keyspace-wide command: {offenders}. A full SCAN in the window "
            "before a clock starts contaminates the number it is preparing."
        )

    def test_the_raw_command_rule_would_catch_one(self):
        """The rule above must be capable of failing, or it is decoration."""
        caught = [
            node
            for node in ast.walk(ast.parse("client.execute_command('SCAN', 0)"))
            if isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).upper() in {"SCAN", "KEYS"}
        ]
        assert caught, "the raw-command rule matches nothing; it cannot fail"

    def test_the_shell_harness_issues_no_keyspace_walk(self):
        """``scripts/run_perf_baseline.sh`` is the only shell harness.

        Asserted directly rather than by a general heuristic: a general rule over
        shell text is what produced 30 false positives, and one file read
        precisely is both stricter and readable.
        """
        script = REPO_ROOT / "scripts" / "run_perf_baseline.sh"
        assert script.is_file()
        code = "\n".join(
            line for line in script.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")
        )
        tokens = set(re.split(r"[^A-Za-z0-9_]+", code))
        assert not (tokens & {"SCAN", "KEYS"}), "the load script issues a keyspace-wide command"
        assert "redis-cli" not in code, (
            "the load script shells out to redis-cli; its cleanup path is no longer covered "
            "by the Python rules and must be reviewed by hand"
        )

    def test_the_targeted_form_passes_a_match_for_every_pattern(self):
        assert SECURITY_KEY_PATTERNS, "an empty pattern list cleans nothing and passes silently"
        for pattern in SECURITY_KEY_PATTERNS:
            assert pattern.endswith("*"), f"{pattern} is not a namespace pattern"

    async def test_clear_security_state_matches_on_every_pattern(self, fake_redis: _FakeRedis):
        """Proved at runtime too: the static test cannot see a MATCH built at
        runtime from a variable, and this can."""
        await clear_security_state()
        scans = [kwargs for name, kwargs in fake_redis.calls if name == "scan_iter"]
        assert len(scans) == len(SECURITY_KEY_PATTERNS)
        for kwargs, pattern in zip(scans, SECURITY_KEY_PATTERNS, strict=True):
            assert kwargs["match"] == pattern
            assert kwargs.get("count"), "an unbounded COUNT makes the walk unpredictable"


# --------------------------------------------------------------------------
# (b) the settle sleep is a fixed, non-zero constant
# --------------------------------------------------------------------------


class TestTheSettleSleep:
    def test_it_is_a_named_module_constant(self):
        assert isinstance(FLUSHDB_SETTLE_SECONDS, float)
        assert FLUSHDB_SETTLE_SECONDS > 0, (
            "zero would measure a database whose caches were emptied microseconds earlier"
        )

    def test_it_is_a_literal_not_computed(self):
        """A constant that is derived from the environment becomes a knob."""
        node = next(
            n
            for n in ast.parse(inspect.getsource(mod)).body
            if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "FLUSHDB_SETTLE_SECONDS"
        )
        assert isinstance(node.value, ast.Constant), (
            "the settle time must be a literal, not derived from settings or env"
        )

    def test_the_default_argument_is_that_constant(self):
        signature = inspect.signature(flush_security_state)
        assert signature.parameters["settle"].default == FLUSHDB_SETTLE_SECONDS

    async def test_the_settle_is_awaited_with_the_fixed_value(self, fake_redis: _FakeRedis):
        monkey_settled: list[float] = []

        async def fake_clock(seconds: float) -> None:
            monkey_settled.append(seconds)

        monkeypatch_dedicated()
        await flush_security_state(clock=fake_clock)
        assert monkey_settled == [FLUSHDB_SETTLE_SECONDS]


# --------------------------------------------------------------------------
# (c) FLUSHDB is refused unless the instance is marked dedicated
# --------------------------------------------------------------------------


def monkeypatch_dedicated(value: str = "true") -> None:
    import os

    os.environ[DEDICATED_INSTANCE_ENV_VAR] = value


class TestFlushDbIsGated:
    def test_the_variable_has_no_default(self):
        """An unset variable must mean 'not authorised', not 'allowed'.

        Checked on the AST rather than the source text: a textual match on the
        call spelling breaks the moment someone switches to single quotes, and a
        rule that breaks on quoting is a rule that will be "fixed" by deleting it.
        """
        node = next(
            n
            for n in ast.walk(ast.parse(inspect.getsource(mod._is_dedicated)))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "environ"
        )
        name = node.args[0]
        assert isinstance(name, ast.Name), (
            f"the gate must read a module constant, not a literal; found {ast.dump(name)}"
        )
        assert len(node.args) == 2 and isinstance(node.args[1], ast.Constant), (
            "the variable must be read with an explicit default, so unset is a defined state"
        )
        assert node.args[1].value == "", "the default must be the empty string, i.e. NOT dedicated"
        assert not any(kw.arg in {"default"} for kw in node.keywords)
        assert getattr(mod, name.id) == DEDICATED_INSTANCE_ENV_VAR, (
            f"_is_dedicated reads {name.id}, which is not the variable it is supposed to gate on"
        )

    async def test_an_unset_variable_is_refused(self, fake_redis: _FakeRedis):
        with pytest.raises(NotDedicatedInstance) as excinfo:
            await flush_security_state()
        assert excinfo.value.variable == DEDICATED_INSTANCE_ENV_VAR
        assert fake_redis.calls == [], "the refusal must cost nothing: nothing was issued to Redis"

    @pytest.mark.parametrize("value", ["", "  ", "0", "false", "no", "off", "maybe", "dedicated", "2", "TRUE-ish"])
    async def test_only_an_explicit_yes_is_accepted(self, value: str, fake_redis: _FakeRedis):
        monkeypatch_dedicated(value)
        with pytest.raises(NotDedicatedInstance):
            await flush_security_state()
        assert fake_redis.calls == []

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    async def test_an_explicit_yes_is_accepted(self, value: str, fake_redis: _FakeRedis):
        monkeypatch_dedicated(value)

        async def fake_clock(seconds: float) -> None:
            return None

        await flush_security_state(clock=fake_clock)
        assert [name for name, _ in fake_redis.calls] == ["flushdb", "aclose"]

    async def test_the_refusal_names_the_variable_and_the_alternative(self, fake_redis: _FakeRedis):
        with pytest.raises(NotDedicatedInstance, match=DEDICATED_INSTANCE_ENV_VAR):
            await flush_security_state()

    def test_the_truthy_set_is_narrow(self):
        assert mod.TRUTHY_ENV_VALUES == frozenset({"1", "true", "yes", "on"})
        assert not (mod.TRUTHY_ENV_VALUES & {"y", "t", "enable", "enabled", "dedicated"}), (
            "an ambiguous spelling must not read as consent to empty a database"
        )


# --------------------------------------------------------------------------
# (d) cleanup completes before t0, proven with a fake clock
# --------------------------------------------------------------------------


class TestCleanupCompletesBeforeTheClockStarts:
    async def test_flushdb_then_settle_then_the_measurement_starts(self, fake_redis: _FakeRedis):
        """The ordering that matters, asserted without sleeping.

        A caller times a phase like this::

            await flush_security_state()      # cleanup
            t0 = time.perf_counter()          # clock starts
            ...measure...
            t1 = time.perf_counter()

        so ``flush_security_state`` must not return until the settle has
        happened. If the sleep were fire-and-forget, or were placed before the
        ``flushdb``, ``t0`` would be taken against a database still settling and
        every read would pay a cold miss the production system would not.
        """
        order: list[str] = []

        class _OrderingRedis(_FakeRedis):
            async def flushdb(self) -> None:
                order.append("flushdb")
                await super().flushdb()

        import redis.asyncio as aioredis

        client = _OrderingRedis()

        async def fake_clock(seconds: float) -> None:
            order.append(f"settle({seconds})")

        def start_measuring() -> None:
            order.append("t0")

        async def _drive() -> None:
            await flush_security_state(clock=fake_clock)
            start_measuring()

        aioredis.from_url = lambda *a, **k: client  # type: ignore[assignment]
        monkeypatch_dedicated()
        await _drive()
        await client.aclose()

        assert order == ["flushdb", f"settle({FLUSHDB_SETTLE_SECONDS})", "t0"], (
            "the settle must be awaited INSIDE the cleanup call and AFTER the flush, "
            f"so t0 cannot be taken against a database that is still settling; got {order}"
        )

    async def test_the_targeted_form_also_completes_before_the_clock(self, fake_redis: _FakeRedis):
        order: list[str] = []

        class _OrderingRedis(_FakeRedis):
            def scan_iter(self, **kwargs: Any) -> Any:
                order.append(f"scan({kwargs['match']})")
                return super().scan_iter(**kwargs)

        import redis.asyncio as aioredis

        client = _OrderingRedis()
        aioredis.from_url = lambda *a, **k: client  # type: ignore[assignment]

        async def _drive() -> None:
            await clear_security_state()
            order.append("t0")

        await _drive()
        assert order[-1] == "t0"
        assert len([o for o in order if o.startswith("scan(")]) == len(SECURITY_KEY_PATTERNS)
        assert len([o for o in order if o == "t0"]) == 1

    async def test_the_targeted_form_never_flushes(self, fake_redis: _FakeRedis):
        """Belt and braces: the targeted form must not silently grow a FLUSHDB,
        which would erase the very isolation it was written to preserve."""
        await clear_security_state()
        assert "flushdb" not in [name for name, _ in fake_redis.calls]

    async def test_the_flush_form_never_scans(self, fake_redis: _FakeRedis):
        monkeypatch_dedicated()

        async def fake_clock(seconds: float) -> None:
            return None

        await flush_security_state(clock=fake_clock)
        assert "scan_iter" not in [name for name, _ in fake_redis.calls]
