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
#: The harness and its fixture, PLUS the operator probes and the run manifest.
#:
#: The probe/manifest entries are not padding: they were the gap. A rewrite of
#: ``src/.env.example`` dropped REDIS_HOST / REDIS_PORT / REDIS_PASSWORD /
#: REDIS_RATE_LIMIT_PASSWORD and the PERF_* manifest keys, and this gate stayed
#: GREEN because it only looked at the locust harness. A gate whose source list
#: is narrower than its docstring's claim is the exact failure it exists to
#: prevent, so the list now covers every first-party script that reads the
#: environment for configuration.
HARNESS_SOURCES = (
    "scripts/run_perf_baseline.sh",
    "scripts/setup_locust_test_data.py",
    "scripts/span_cost.py",
    "scripts/store_rtt_probe.py",
    "scripts/users_list_probe.py",
    "scripts/clear_rate_limit_state.py",
    "src/benchmarks/perf_accounts.py",
    "src/benchmarks/run_manifest.py",
    "src/benchmarks/m2_refresh_commit_ab.py",
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

_PY_ENV_READ = re.compile(
    r"""(?:os\.environ(?:\.get)?\[?|environ\.get\(|os\.getenv\()\s*["']([A-Z_][A-Z0-9_]*)["']"""
)


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


# ============================================================================
# The Settings model is the other half of the contract
# ============================================================================
# The harness half above answers "can a clean checkout RUN the harness". These
# answer "can a clean checkout RUN THE APP", and they exist because the template
# was rewritten once from memory instead of from the model: 22 real Settings
# fields went missing (they silently fell back to defaults, including
# SECURITY_BUDGETS) and 18 keys were declared that bound to nothing
# (S3_BUCKET_NAME instead of STORAGE_BUCKET, TIER_NAME instead of
# DEFAULT_FREE_TIER_NAME, eight SSO *_CLIENT_ID/_SECRET that no code reads).
# Both directions are silent: a missing key looks like a default, a dead key
# looks configured.

def _bindable_env_names() -> set[str]:
    """Every env name the Settings tree can bind, honouring each class's prefix.

    Walks ``model_fields`` recursively with the accumulated ``env_prefix``. This
    is the only reliable source: a hand-maintained list is what drifted.
    """
    from pydantic_settings import BaseSettings

    from src.app.core.config import Settings

    seen: set[type] = set()

    def walk(cls: type[BaseSettings], inherited: str) -> set[str]:
        if cls in seen:
            return set()
        seen.add(cls)
        prefix = inherited + (cls.model_config.get("env_prefix") or "")
        out: set[str] = set()
        for name, field in cls.model_fields.items():
            annotation = field.annotation
            nested = annotation if isinstance(annotation, type) and issubclass(annotation, BaseSettings) else None
            if nested is None:
                for arg in getattr(annotation, "__args__", ()) or ():
                    if isinstance(arg, type) and issubclass(arg, BaseSettings):
                        nested = arg
            if nested is not None:
                out |= walk(nested, prefix)
            else:
                out.add(prefix + name)
        return out

    return walk(Settings, "")


def _required_env_names() -> set[str]:
    """Settings fields with no default: the app refuses to boot without them."""
    from pydantic_settings import BaseSettings

    from src.app.core.config import Settings

    seen: set[type] = set()

    def walk(cls: type[BaseSettings], inherited: str) -> set[str]:
        if cls in seen:
            return set()
        seen.add(cls)
        prefix = inherited + (cls.model_config.get("env_prefix") or "")
        out: set[str] = set()
        for name, field in cls.model_fields.items():
            annotation = field.annotation
            nested = annotation if isinstance(annotation, type) and issubclass(annotation, BaseSettings) else None
            if nested is None:
                for arg in getattr(annotation, "__args__", ()) or ():
                    if isinstance(arg, type) and issubclass(arg, BaseSettings):
                        nested = arg
            if nested is not None:
                out |= walk(nested, prefix)
            elif field.is_required():
                out.add(prefix + name)
        return out

    return walk(Settings, "")


def _directly_read_keys() -> set[str]:
    """Env keys read by first-party src/ and scripts/ code with ``os.environ``.

    Not Settings fields - the keys that configure a script, a probe or the
    manifest. A template that omits them leaves an operator with a silent
    default (the probe scripts fall back to 127.0.0.1 with no warning).
    """
    found: set[str] = set()
    for root in ("src", "scripts"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            found |= set(_PY_ENV_READ.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


class TestEverySettingsFieldIsDeclared:
    """A required field missing from the template makes a clean checkout unbootable."""

    def test_required_settings_fields_are_declared(self) -> None:
        missing = sorted(_required_env_names() - _documented_keys())
        assert not missing, (
            f"{missing} are REQUIRED Settings fields (no default) but src/.env.example does not "
            f"declare them. A clean checkout cannot boot: this is exactly how "
            f"ADMIN_PASSWORD=replace_me shipped, failing min_length=16 and four complexity "
            f"validators. Add each with a placeholder that satisfies its validators."
        )

    def test_no_settings_field_is_missing(self) -> None:
        """Softer than "required": every field should be visible, not just the mandatory ones.

        A field that is absent silently takes its default. That is how 22 real
        fields - including SECURITY_BUDGETS - became invisible.
        """
        missing = sorted(_bindable_env_names() - _documented_keys())
        assert not missing, (
            f"{missing} are Settings fields that src/.env.example never declares, so their "
            f"effective values are invisible to an operator. Declare each at its default with a "
            f"note on what it changes."
        )


class TestNoDeadKeys:
    """A declared key that binds nothing is worse than an absent one.

    It reads as configured. S3_BUCKET_NAME, TIER_NAME and the eight SSO
    *_CLIENT_ID/_SECRET keys all looked like configuration and were ignored.
    """

    def test_every_declared_key_is_reachable(self) -> None:
        bindable = _bindable_env_names()
        readable = _directly_read_keys()
        # Keys whose consumers live outside src/ + scripts/ (tests bootstrap,
        # shell, CI) or that are deliberately documented as inert constants.
        outside_app = {
            "TEST_POSTGRES_DB",
            "TEST_POSTGRES_PORT",
            "TEST_REDIS_PORT",
            "TEST_REDIS_PASSWORD",
            "TEST_ENCRYPTION_KEY",
            "TEST_ENCRYPTION_SALT",
            "API_HOST",
            "PERF_LOGOUT_WEIGHT",
            "PERF_ROLE_NAME",
            "PERF_PERMISSION_SLUGS",
            "PERF_TEST_EMAILS",
            "PERF_TEST_USER_COUNT",
            "PERF_TEST_PASSWORD",
            "PERF_TEST_TENANT",
            "PERF_STORE_LOCALITY",
            "PERF_ORCHESTRATOR",
            "PERF_IMAGE_DIGEST",
            "PERF_TEST_AI_ENABLED",
            "PERF_TEST_EMAIL",
            "DEBUG",
        }
        dead = sorted(_documented_keys() - bindable - readable - outside_app)
        assert not dead, (
            f"{dead} are declared in src/.env.example but bind no Settings field and are read by "
            f"no src/ or scripts/ code. Either the name is misspelled (a real field is silently "
            f"taking its default) or it is decoration that will mislead the next operator. "
            f"Fix the name, or delete the key and document why in prose."
        )
