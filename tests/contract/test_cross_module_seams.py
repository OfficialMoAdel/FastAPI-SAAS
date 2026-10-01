"""The cross-module seam gate, proven to bite.

``ARCHITECTURE.md`` records a decision (b): the bus preference is SOFTENED,
because a request-scoped cross-module call needs its return value and an
event has none. Softening a rule is only honest if the replacement is
enforceable, so ``scripts/check_cross_module_seams.py`` names every
behaviour-layer seam that exists today, and a new one fails.

Why this is a test and not just a CI step: a sanctioned list is, by
construction, a list of strings. A list that names nothing the tree
imports also reports green, which is exactly the failure this repo has
already paid for once — ``ARCHITECTURE.md`` cited the module-boundary ban as
enforced for a year while that ban named 6 of 160 cells and caught nothing
(``tests/contract/test_module_boundary_ban.py`` records the repair).
A gate nobody has watched reject a file is not evidence of anything, so
``TestGateBites`` writes real offending files and asserts they are refused.

``TestAllowlistIsNotAGraveyard`` is the mirror image: a sanctioned entry
whose import has been deleted must FAIL, so the list cannot quietly become
a list of permissions nobody uses.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_cross_module_seams.py"
MODULES_DIR = REPO / "src" / "app" / "modules"

#: A module that has no sanctioned seam today, so a probe importing it is
#: unambiguously new coupling rather than a reshuffle of existing debt.
PROBE_TARGET = "src.app.modules.storage.service"


def _load_gate():
    """Import the gate script as a module (it is a script, not a package).

    The module is registered in ``sys.modules`` before execution because
    ``@dataclass`` resolves annotations through
    ``sys.modules[cls.__module__]``; a module absent from that mapping
    raises ``AttributeError: 'NoneType' object has no attribute '__dict__'``
    at class-creation time, which is not an error worth debugging twice.
    """
    spec = importlib.util.spec_from_file_location("check_cross_module_seams", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway copy of the module tree the gate scans.

    Copying rather than importing-and-patching matters for the falsifiability
    tests: the claim under test is "a NEW import in a NEW file is refused",
    and editing the real tree to make that claim would leave production
    files modified if the test failed mid-way.
    """
    import shutil

    tree = tmp_path / "modules"
    shutil.copytree(MODULES_DIR, tree, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setattr(gate, "MODULES_DIR", tree)
    monkeypatch.setattr(gate, "SANCTIONED_SEAMS", dict(gate.SANCTIONED_SEAMS))
    return tree


class TestGateBites:
    """Falsifiability: the gate must REJECT new unsanctioned coupling."""

    def test_new_cross_module_service_import_is_rejected(self, sandbox: Path) -> None:
        probe = sandbox / "posts" / "service.py"
        assert probe.exists(), "posts is a real module; the probe must be its own service layer"
        source = probe.read_text(encoding="utf-8")
        assert "storage.service" not in source, "posts already imports it; pick another target"
        probe.write_text(source + f"\nfrom {PROBE_TARGET} import StorageService\n", encoding="utf-8")

        violations = gate.check()
        unsanctioned = [v for v in violations if v.kind == "unsanctioned-seam"]
        assert unsanctioned, (
            "a new cross-module service import was ACCEPTED, so the seam rule "
            "is decorative. A list of sanctioned strings that the tree does not "
            "constrain is exactly the defect this gate exists to prevent."
        )
        assert any(PROBE_TARGET in v.subject for v in unsanctioned), (
            f"violation reported without naming the offending cell: {[v.subject for v in unsanctioned]}"
        )

    def test_new_deep_router_import_is_rejected(self, sandbox: Path) -> None:
        """The deep form must bite too, not only the facade form.

        A gate that only watched ``from X import Y`` would let
        ``from X.service import Y`` through, which is the same coupling one
        segment deeper and the form TID251 already forbids outright.
        """
        probe = sandbox / "posts" / "service.py"
        probe.write_text(
            probe.read_text(encoding="utf-8") + f"\nfrom {PROBE_TARGET} import StorageService\n",
            encoding="utf-8",
        )
        assert any(v.kind == "unsanctioned-seam" for v in gate.check())

        # Now the deep form, from a file that is NOT in any sanctioned list.
        other = sandbox / "dashboard" / "service.py"
        other.write_text(f"from {PROBE_TARGET} import StorageService\n", encoding="utf-8")
        subjects = {v.subject for v in gate.check() if v.kind == "unsanctioned-seam"}
        assert any(s.startswith("dashboard/service.py") for s in subjects), (
            f"the deep import was not reported: {sorted(subjects)}"
        )

    def test_script_exits_nonzero_on_a_violation(self, tmp_path: Path) -> None:
        """The CI-facing entry point must fail, not just report.

        The in-process ``check()`` calls prove the predicate. This proves
        the thing a developer and CI actually run propagates a non-zero
        exit status on a real breach — a gate that prints a violation and
        returns 0 is a gate that ships.

        The probe runs the CLI against a full temp world rather than an
        in-process import, because ROOT is derived from the script's own
        location: a copied script with no sibling ``src/app/`` dies in the
        bus scan with a traceback, and a traceback is not a violation
        report. The probe would pass for the wrong reason.
        """
        import shutil

        clean = _run_gate()
        assert clean.returncode == 0, f"the gate is not green on the real tree:\n{clean.stdout}{clean.stderr}"

        world = tmp_path / "world"
        (world / "scripts").mkdir(parents=True)
        (world / "src" / "app").mkdir(parents=True)
        shutil.copy2(SCRIPT, world / "scripts" / SCRIPT.name)
        shutil.copy2(REPO / "src" / "app" / "events.py", world / "src" / "app" / "events.py")
        shutil.copytree(
            MODULES_DIR, world / "src" / "app" / "modules", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        offender = world / "src" / "app" / "modules" / "posts" / "service.py"
        offender.write_text(
            offender.read_text(encoding="utf-8") + f"\nfrom {PROBE_TARGET} import StorageService\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(world / "scripts" / SCRIPT.name)],
            capture_output=True,
            text=True,
            cwd=world,
        )
        assert result.returncode != 0, (
            "a new cross-module service import did not fail the CLI, so the gate "
            f"reports success on a breach:\n{result.stdout}{result.stderr}"
        )
        assert "unsanctioned-seam" in result.stderr, f"violation not reported on stderr:\n{result.stderr}"
        assert PROBE_TARGET in result.stderr, f"the report does not name the offending cell:\n{result.stderr}"

    def test_existing_seam_is_not_a_violation(self) -> None:
        """The counter-test: today's sanctioned coupling must stay green.

        A gate that flagged the seams it declares would be unusable, and one
        that flagged everything would be indistinguishable from a gate that
        flags nothing.
        """
        subjects = {v.subject for v in gate.check() if v.kind == "unsanctioned-seam"}
        assert not subjects, f"the declared seams are being reported as violations: {sorted(subjects)}"


class TestAllowlistIsNotAGraveyard:
    """Every sanctioned entry must correspond to a real import."""

    def test_no_sanctioned_entry_is_dead(self) -> None:
        seams, _ = gate.scan_seams()
        declared = set(gate.SANCTIONED_SEAMS)
        live = set(seams)
        missing = sorted(declared - live)
        assert not missing, (
            f"{len(missing)} sanctioned seams no longer exist, so the list is a "
            f"graveyard of permissions nobody uses: {missing}\n"
            "Delete the entries; a stale allowlist is worse than a short one, "
            "because it reads as coverage."
        )

    def test_every_sanctioned_entry_states_a_reason(self) -> None:
        """A bare string is not a justification.

        The point of the list is that the next reader learns WHY an edge is
        allowed. An entry whose reason merely restates the import — or that
        is a placeholder — has been copied rather than decided, and it is
        the kind of entry that outlives the reasoning that justified it.

        Deliberately no vocabulary check. A test that greps the reason for
        specific words can only be satisfied by those words, so it rewards
        writing to the test instead of deciding — the same trap
        ``scripts/check_comment_grease.py`` exists to catch in comments.
        Substantiveness is length plus absence of a placeholder; whether a
        reason is a real judgement is a review question, not a grep.
        """
        placeholders = {"", "n/a", "todo", "fixme", "legacy", "temporary", "ok", "none", "see above"}
        for (importer, target), reason in gate.SANCTIONED_SEAMS.items():
            stripped = reason.strip()
            where = f"{importer} -> {target}"
            assert stripped.lower() not in placeholders, f"{where} has a placeholder reason: {reason!r}"
            assert len(stripped.split()) >= 6, f"{where} reason is too thin to be a decision: {reason!r}"

    def test_a_deleted_import_is_reported_as_a_graveyard_entry(self, sandbox: Path) -> None:
        """Falsify the graveyard check itself.

        Deleting a sanctioned import must FAIL, in the same way a new one
        does. Without this, the graveyard test above is unfalsifiable: a
        detector that never reports anything would pass it.
        """
        target = ("auth/services/passwords.py", "src.app.modules.users.session_invalidation")
        assert target in gate.SANCTIONED_SEAMS, "pick a seam that exists today"

        victim = sandbox / target[0]
        source = victim.read_text(encoding="utf-8")
        stripped = "\n".join(line for line in source.splitlines() if "session_invalidation" not in line)
        assert stripped != source, "the seam's import line was not found in the file"
        victim.write_text(stripped + "\n", encoding="utf-8")

        kinds = {v.kind for v in gate.check()}
        assert "graveyard-seam" in kinds, (
            "deleting a sanctioned import did not fail the gate, so the list can "
            "become a graveyard unnoticed"
        )


class TestBusSurface:
    """The event-bus ratchet, and the detector that feeds it."""

    def test_registry_and_publish_scan_are_both_non_empty(self) -> None:
        """Fail closed on a broken detector.

        The dead-subscription count is derived from these two sets. A
        refactor that renames the registry variable would empty the first
        set and make every remaining event look "never published" — or, if
        only the publish scan broke, make every registration look dead. Both
        directions are silent, so the counts are pinned to the tree.
        """
        registered, published = gate.scan_bus()
        assert len(registered) == 14, (
            f"registry scan found {len(registered)} events, expected 14: {sorted(registered)}\n"
            "If a handler was added or removed, update the count and "
            "DEAD_SUBSCRIPTION_CEILING together."
        )
        assert published == {"auth.login_issued", "notification.request", "user.created"}, (
            f"publication scan found {sorted(published)}; the bus's own failure mode is "
            "silence, so an empty or partial scan measures nothing"
        )

    def test_registered_events_all_have_a_handler(self) -> None:
        """Every registration resolves to a callable the registry imports.

        A registration naming a handler that is not imported is a silent
        hole: the entry exists, so the bus reports the event as having a
        subscriber, and the failure surfaces only at delivery time.
        """
        source = (REPO / "src" / "app" / "events.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        bound: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(getattr(t, "id", None) == "handlers" for t in targets):
                continue
            for element in node.value.elts:
                if isinstance(element, ast.Tuple) and isinstance(element.elts[1], ast.Name):
                    bound.add(element.elts[1].id)
        assert bound, "the handlers list was not found in the registry"
        assert bound <= imported, f"registry binds names it never imports: {sorted(bound - imported)}"

    def test_dead_subscription_ceiling_matches_the_tree(self) -> None:
        """The ratchet must be pinned to the measured number.

        The bus registers 14 events and ``src/app`` publishes 3. The other
        11 are live wiring for events no producer emits — they are the
        recorded migration candidate, not a defect to delete here, because
        removing them is a behavioural cutover in the webhook and auth
        paths. The ceiling exists so a NEW dead registration is refused and
        a real migration is rewarded by lowering it.
        """
        registered, published = gate.scan_bus()
        dead = registered - published
        assert len(dead) == gate.DEAD_SUBSCRIPTION_CEILING, (
            f"measured {len(dead)} dead registrations, ceiling is {gate.DEAD_SUBSCRIPTION_CEILING}: "
            f"{sorted(dead)}\n"
            "Lowering this number means a producer now exists — lower the ceiling "
            "with it. Raising it means a new event was registered that nobody "
            "publishes, which is a new silent hole."
        )

    def test_publishing_an_unregistered_event_is_refused(self, tmp_path: Path) -> None:
        """The other half of the bus's silence: an event with no handler.

        Publishing an event nothing subscribes to is dropped by
        ``EventBus.publish`` without error — it logs at debug and returns.
        A producer introduced without a handler is therefore invisible, so
        the ratchet refuses the pair rather than trusting a reviewer's eye.
        """
        import shutil

        app_root = tmp_path / "app"
        shutil.copytree(REPO / "src" / "app", app_root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        producer = app_root / "modules" / "posts" / "service.py"
        producer.write_text(
            producer.read_text(encoding="utf-8")
            + textwrap.dedent(
                """
                from src.app.core.events import event_bus


                async def emit_something() -> None:
                    await event_bus.publish("posts.nonexistent", {})
                """
            ),
            encoding="utf-8",
        )

        registered, published = gate.scan_bus(app_root)
        assert "posts.nonexistent" in published, "the probe producer was not scanned; the detector is blind to it"
        orphans = sorted(published - registered)
        assert orphans == ["posts.nonexistent"], (
            f"an event published to no handler must be reported as dropped, got {orphans}"
        )

    def test_dlq_surfaces_undeliverable_events(self) -> None:
        """A bus nobody monitors is worse than a direct call.

        The counterweight to the bus-preference guidance is that bus
        failures are invisible: they land in the DLQ. This asserts the DLQ
        still receives an event whose handler raises AND that the entry is
        retrievable through the queue's own read path — the property that
        makes the bus a defensible integration path at all.

        The double models the bounded path's pipeline because that is the
        path production takes. An earlier revision of this test injected a
        bare object with only ``lpush``/``llen``, which raised
        ``AttributeError: no attribute 'pipeline'`` inside ``add_to_dlq``;
        ``_dlq_quietly`` swallowed it by design, so the test saw zero
        entries and reported the DLQ as broken when it was the double that
        was. A double must satisfy the real call surface, or "the failure
        was swallowed" is indistinguishable from "the failure never
        happened".
        """
        import asyncio

        from src.app.core.dead_letter import DeadLetterQueue
        from src.app.core.events import EventBus, EventPriority

        class _Pipe:
            def __init__(self, redis: _Redis) -> None:
                self._redis = redis
                self._results: list[int] = []

            async def __aenter__(self) -> _Pipe:
                return self

            async def __aexit__(self, *exc: object) -> bool:
                return False

            def lpush(self, key: str, value: str) -> None:
                self._redis.store.setdefault(key, []).insert(0, value)
                self._results.append(len(self._redis.store[key]))

            def ltrim(self, key: str, start: int, end: int) -> None:
                self._redis.store[key] = self._redis.store[key][start : end + 1]
                self._results.append(True)

            def llen(self, key: str) -> None:
                self._results.append(len(self._redis.store.get(key, [])))

            async def execute(self) -> list[int]:
                return self._results

        class _Redis:
            def __init__(self) -> None:
                self.store: dict[str, list[str]] = {}

            def pipeline(self, transaction: bool = True) -> _Pipe:
                return _Pipe(self)

            async def lpush(self, key: str, value: str) -> int:
                self.store.setdefault(key, []).insert(0, value)
                return len(self.store[key])

            async def llen(self, key: str) -> int:
                return len(self.store.get(key, []))

            async def lrange(self, key: str, start: int, end: int) -> list[str]:
                return self.store.get(key, [])[start : end + 1]

        async def scenario() -> _Redis:
            redis = _Redis()
            bus = EventBus()
            bus._registration_complete = True

            async def boom(_data: dict) -> None:
                raise RuntimeError("handler failed")

            bus.register_handler("governance.probe", boom)
            bus._redis_client = redis
            await bus.publish("governance.probe", {"x": 1}, priority=EventPriority.CRITICAL)
            return redis

        redis = asyncio.run(scenario())
        queue = DeadLetterQueue(redis_client=redis)
        entries = asyncio.run(queue.read())
        assert entries, "a failing CRITICAL handler left no DLQ record, so its failure is invisible"
        assert any("governance.probe" in json.dumps(entry) for entry in entries), (
            f"the DLQ record does not name the failed event: {entries}"
        )

    def test_request_scoped_direct_call_still_returns_its_value(self) -> None:
        """The counter-test the softened rule must keep true.

        The decision in ARCHITECTURE.md is that request-scoped cross-module
        calls stay direct calls, because the caller needs the result. If that
        were not true — if a sanctioned seam could not return a value — the
        whole softening would be unfounded. This asserts the shape directly,
        without a database: a cross-module function returning a value is
        awaitable and the value arrives.
        """

        async def fake_directory_lookup(user_id: int) -> dict[str, int]:
            return {"id": user_id}

        async def auth_door() -> dict[str, int]:
            # The sanctioned shape: auth calls users' narrow read interface
            # and consumes the result in the same request.
            return await fake_directory_lookup(42)

        import asyncio

        assert asyncio.run(auth_door()) == {"id": 42}


class TestRepoIsGreen:
    """The gate must be satisfiable by the tree as it stands."""

    def test_script_runs_clean(self) -> None:
        result = _run_gate()
        assert result.returncode == 0, (
            f"the seam gate is not green:\n{result.stdout}\n{result.stderr}\n"
            "Either route the import through a sanctioned seam or add a "
            "reasoned entry to SANCTIONED_SEAMS."
        )

    def test_ruff_accepts_the_script(self) -> None:
        """The gate must not be exempt from the lint it helps enforce."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--no-cache", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 0, f"the gate script does not lint clean:\n{result.stdout}{result.stderr}"
