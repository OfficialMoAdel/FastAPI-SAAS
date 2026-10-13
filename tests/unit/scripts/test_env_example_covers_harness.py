"""Every env var the perf harness reads must appear in ``src/.env.example``.

Criterion (e) of the Phase-0 Step 1 exit gate asks that "a clean-checkout dry run
reproduces the DECLARED configuration". A clean checkout has no ``src/.env`` -
it is gitignored, and it must stay that way, because a committed ``SECRET_KEY`` is
a credential leak and this repository has real keys in its own history
(``crypt.KNOWN_LEAKED_SECRET_KEYS``). So the only thing that can make a clean
checkout able to run the harness is the *shape* of the configuration living in
the repository.

``src/.env.example`` is that shape. This test is what keeps it honest.

WHY A GATE AND NOT A NOTE
-------------------------
Perf finding P-4 was not that a value was wrong. It was that every locust
baseline in the Round-1 pack was produced with ``AUTO_BAN_ENABLED=false`` and
relaxed budgets, **and no report disclosed the override**. The values were wrong
AND invisible, which is what made them survive long enough to be published.

An undocumented variable is the same defect in embryo: nothing reads it, so
nothing checks it, so the first person to set it discovers the gap only by
running the harness and hitting something unexplained. So this fails on
*undocumented*, not merely on unset - the asymmetry is the point.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "src" / ".env.example"

#: Sources whose ``os.environ`` reads describe what the perf harness requires.
#: Deliberately the harness and its fixture only - not src/app, whose settings
#: are covered by pydantic's own required-field validation, and not every
#: operator knob, which is a different question.
HARNESS_SOURCES = (
    "scripts/run_perf_baseline.sh",
    "scripts/setup_locust_test_data.py",
    "src/benchmarks/perf_accounts.py",
    "src/benchmarks/run_manifest.py",
    "tests/performance/locustfile.py",
)

#: Variables the harness reads that are NOT configuration - a run's own outputs
#: or the test runner's plumbing. Documenting these would be noise, and a
#: measured value in a template invites someone to "fix" it.
EXEMPT = frozenset(
    {
        "APP_HOST_REPORT",
        "CLEANUP_ACTUAL",
        "CLEANUP_LOG",
        "COLD_WINDOW",
        "HOST_ADDR",
        "HOST_URL",
        "LOCUST_RC",
        "MANIFEST",
        "MANIFEST_END",
        "MIN_COLD_SECONDS",
        "PERF_APP_HOST_REPORT",
        "PERF_IMAGE_DIGEST",
        "PERF_ORCHESTRATOR",
        "PERF_STORE_LOCALITY",
        "PERF_TEST_AI_ENABLED",
        "PERF_THRESHOLDS_MS",
        "PHASE_RC",
        "PHASES",
        "PROVENANCE_RC",
        "CI",
        "GITHUB_ACTIONS",
    }
)

_PY_ENV_READ = re.compile(r"""(?:os\.environ(?:\.get)?\[?|environ\.get\()\s*["']([A-Z_][A-Z0-9_]*)["']""")


def _documented_keys() -> set[str]:
    """Keys the template mentions, whether assigned or commented out.

    Commented-out counts: a line like ``# AUTO_BAN_ENABLED=true`` documents the
    variable AND its production value, which is the disclosure P-4 lacked.
    """
    if not EXAMPLE.is_file():
        return set()
    return set(re.findall(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)=", EXAMPLE.read_text(encoding="utf-8"), re.M))


def _referenced_keys() -> set[str]:
    """Env vars read by the harness sources."""
    found: set[str] = set()
    for relative in HARNESS_SOURCES:
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Only literals are readable without executing the module, and these
            # are all literals. A computed key is out of scope deliberately:
            # it cannot be documented statically, so pretending otherwise would
            # make this gate report a false clean bill.
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript):
                    _collect_subscript(node, found)
                elif isinstance(node, ast.Call):
                    _collect_get(node, found)
        else:
            found |= set(_PY_ENV_READ.findall(path.read_text(encoding="utf-8")))
    return found - EXEMPT


def _collect_subscript(node: ast.Subscript, out: set[str]) -> None:
    """``os.environ["X"]`` / ``os.environ.get("X")`` written as a subscript."""
    target = node.value
    if isinstance(target, ast.Attribute) and target.attr == "environ":
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out.add(key.value)


def _collect_get(node: ast.Call, out: set[str]) -> None:
    """``os.environ.get("X")`` and ``os.getenv("X")`` - Calls, not Subscripts.

    ``os.getenv`` matters as much as the ``os.environ`` spelling: the harness
    reads PERF_TEST_USER_COUNT and several others through it. An earlier version
    handled only ``os.environ``, which made this gate silently check less than it
    claimed - the exact failure this file exists to prevent, committed inside the
    file meant to catch it.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return
    owner = func.value
    if not (isinstance(owner, ast.Attribute) and owner.attr == "environ") and not (
        isinstance(owner, ast.Name) and owner.id == "os"
    ):
        return
    if func.attr not in {"get", "getenv"}:
        return
    if node.args and isinstance(node.args[0], ast.Constant):
        value = node.args[0].value
        if isinstance(value, str):
            out.add(value)


class TestTheTemplateExists:

    def test_env_example_is_committed(self) -> None:
        assert EXAMPLE.is_file(), (
            "src/.env.example is missing. src/.env is gitignored and must stay "
            "that way, so without the template a clean checkout cannot know which "
            "variables the harness reads and criterion (e) is unsatisfiable by "
            "construction."
        )

    def test_env_example_carries_no_secret_value(self) -> None:
        """The template must not become the thing it exists to prevent.

        Checked by shape rather than by pattern-matching the whole file: every
        assigned value must either be a placeholder, an obvious non-credential,
        or empty. A real 64-hex SECRET_KEY here would leak on first push.
        """
        suspicious: list[str] = []
        for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            value = value.strip().strip("\"'")
            if not value or value.lower() in {"true", "false", "none", "null", "[]", "{}"}:
                continue
            if value.isdigit():
                continue
            # A real SECRET_KEY is 64 hex chars. Nothing else in this template
            # legitimately is, so this is a cheap and specific check.
            if key.strip() == "SECRET_KEY" and re.fullmatch(r"[0-9a-fA-F]{32,}", value):
                suspicious.append(f"{key.strip()}=<{len(value)} hex chars>")
            elif "PASSWORD" in key.upper() and "replace_me" not in value.lower():
                suspicious.append(f"{key.strip()}=<value present>")
        assert not suspicious, (
            "src/.env.example appears to contain a real credential: "
            + ", ".join(suspicious)
            + ". This file is committed; it must carry placeholders only."
        )


class TestTheHarnessIsDocumented:
    def test_every_referenced_variable_is_documented(self) -> None:
        documented = _documented_keys()
        referenced = _referenced_keys()
        missing = sorted(referenced - documented)
        assert not missing, (
            f"the perf harness reads {missing} but src/.env.example does not "
            f"mention them.\n\n"
            f"An undocumented variable is how P-4 happened: the Round-1 baselines "
            f"were produced with AUTO_BAN_ENABLED=false and relaxed budgets, and "
            f"no report disclosed the override. A var nobody can see is a var "
            f"nobody checks.\n\n"
            f"Add each one to src/.env.example with its production value and a "
            f"line on what it changes."
        )

    def test_the_harness_sources_are_all_present(self) -> None:
        """A moved or renamed harness must not silently reduce coverage.

        Otherwise this gate would keep passing while checking less, which is the
        same shape of failure as the five-suite collection gap.
        """
        missing = [p for p in HARNESS_SOURCES if not (REPO_ROOT / p).is_file()]
        assert not missing, (
            f"these harness sources no longer exist: {missing}. Either the "
            f"harness moved or this gate is checking stale paths. Update "
            f"HARNESS_SOURCES - do not delete the entry, because that would "
            f"quietly stop checking whatever now reads the environment."
        )


@pytest.mark.parametrize(
    "name",
    [
        # The two gotchas that cost real time on the Linux VM.
        "PERF_TEST_EMAILS",
        "PERF_TEST_PASSWORD",
        # The P-4 override that voided the pack.
        "AUTO_BAN_ENABLED",
        "SECURITY_BUDGETS",
        # The fixture defect found this session.
        "PERF_PERMISSION_SLUGS",
    ],
)
def test_high_risk_variable_is_documented(name: str) -> None:
    """Named individually so a regression says which control regressed.

    These five each have a documented history of silently changing a run's
    meaning. A blanket rule would catch them too, but the failure would name a
    set difference rather than the control - and this is the failure mode the
    whole gate exists to make legible.
    """
    assert name in _documented_keys(), (
        f"{name} is no longer documented in src/.env.example. It is a variable "
        f"that has changed the meaning of a measurement before."
    )
