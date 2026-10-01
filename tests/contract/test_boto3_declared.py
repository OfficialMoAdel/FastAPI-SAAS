"""dxub.9 — boto3: DECIDED (a) DECLARE IT. This file is the record of why.

The question the bead posed was binary and had never been answered: declare
boto3, or document that S3 is deliberately not shipped. The answer is
**declare it**, and the evidence that decided it is narrow and checkable:

- ``"s3"`` is a member of the public ``StorageProviderType`` enum
  (``schemas.py:28``, ``provider_type.py:25``) that ``POST /storage`` accepts
  in its request body. A backend no tenant can select is a different thing
  from a selectable one; this is the latter.
- ``StorageSettingsCreate``'s own OpenAPI example ships ``"provider": "s3"``
  (``schemas.py:134``) — the docs advertise the backend the manifest denied.
- ``azure-storage-blob``, its exact counterpart, WAS declared. Declaring one
  cloud vendor SDK and leaving the other undeclared made the sibling backend
  a broken feature on every clean install, not an optional extra.

So boto3 is now in ``pyproject.toml`` and ``uv.lock``. That is the fix; this
file is the ratchet that keeps it fixed and, more usefully, pins the two
halves that must both stay true for the decision to be honest:

- boto3 is declared in the manifest AND resolved in the lock — a declared
  dependency that the lock never resolves is the same defect one layer down.
- the guard in ``_s3_factory`` still names the package when boto3 is absent.
  Declaration does not make the guard redundant: the import is function-local,
  so a deployment built from a partial wheel subset can still be missing it,
  and "missing because we decided not to ship it" and "missing because your
  build dropped it" must not look alike to an operator.

The previously-skipped case is now the DEFAULT case. ``test_s3_factory_contract.py``
used to ``pytest.skip`` its guard tests whenever boto3 happened to be
installed, so on any machine that had it, the two most important assertions
silently stopped running. Both sides are simulated here with ``without_boto3()``
so the assertions hold either way.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import StorageProviderRegistry, _s3_factory
from src.app.clients.storage.s3_storage import S3Storage
from tests.missing_package import without_boto3

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
UV_LOCK = REPO / "uv.lock"

S3_CREDS = {"bucket_name": "my-bucket", "region": "eu-west-1"}

#: The remedy half of the message a caller needs: not just the package name,
#: but what to do about it.
INSTALL_HINT = "uv pip install boto3"


def _boto3_installed() -> bool:
    try:
        import boto3  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _declared_dependencies() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def _locked_packages() -> list[dict]:
    return tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))["package"]


# ---------------------------------------------------------------------------
# The decision, pinned where a future reader hits it.
# ---------------------------------------------------------------------------


class TestBoto3IsDeclared:
    def test_boto3_is_in_the_manifest(self) -> None:
        """The decision, as an assertion — this is what "DECLARE" means."""
        declared = _declared_dependencies()
        matching = [d for d in declared if d.split(">=")[0].split("[")[0].strip().lower() == "boto3"]
        assert matching, (
            "boto3 is a reachable backend ('s3' is in the public StorageProviderType enum "
            f"that POST /storage accepts) so it must be a declared dependency; got {declared}"
        )

    def test_boto3_is_resolved_in_the_lockfile(self) -> None:
        """A declared dependency the lock never resolves is the same defect, one layer down.

        ``uv sync`` builds the venv from the lock, not from ``pyproject.toml``,
        so a manifest entry without a lock entry yields a machine that still
        cannot import boto3 — the exact "selectable backend that 500s" state
        this bead exists to end.

        This parses ``[[package]]`` blocks rather than grepping the file: a
        substring match also hits the ``requires-dist`` line recording what the
        *manifest* asked for, and that line survives when the resolved package
        block is deleted. The weaker form of this assertion was observed
        passing against exactly such a lock.
        """
        packages = _locked_packages()
        resolved = {p["name"] for p in packages}
        assert "boto3" in resolved, (
            "boto3 is declared in pyproject.toml but no [[package]] block resolves it; "
            f"uv.lock resolves {len(resolved)} packages and boto3 is not among them"
        )
        boto3 = next(p for p in packages if p["name"] == "boto3")
        assert boto3.get("version"), "a resolved boto3 entry must carry the version it pinned"

    def test_boto3_is_actually_importable_in_this_environment(self) -> None:
        """A clean install must be able to construct an S3 provider.

        This is the bead's clean-install assertion. It runs in the same
        interpreter and environment pytest itself is using, so if the lock
        ever stops installing it, this is the test that notices.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import boto3; print(boto3.__version__)"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"boto3 is declared but not importable: {result.stderr}"
        assert result.stdout.strip(), "boto3 imported but reported no version"


# ---------------------------------------------------------------------------
# Both halves of the guard, none of them skipped.
# ---------------------------------------------------------------------------


class TestGuardWhenBoto3IsAbsent:
    """The fail-fast error a stripped deployment still gets."""

    def test_raises_valueerror_not_modulenotfound(self) -> None:
        with without_boto3():
            with pytest.raises(ValueError) as exc:
                _s3_factory(S3_CREDS)

        assert not isinstance(exc.value, ModuleNotFoundError), (
            "a bare ModuleNotFoundError would surface as an opaque 500 on first upload"
        )

    def test_message_names_the_package_and_the_remedy(self) -> None:
        """A caller must be able to choose: install it, or change backend.

        Both halves are asserted, not just that the word appears — the whole
        point of the message is that it distinguishes these two remedies, and
        a message naming boto3 without saying what to do would leave the
        reader no better off than the ModuleNotFoundError it replaced.
        """
        with without_boto3():
            with pytest.raises(ValueError) as exc:
                _s3_factory(S3_CREDS)

        message = str(exc.value)
        assert "boto3" in message, "the missing package must be named"
        assert INSTALL_HINT in message, "the message must carry the install remedy"

    def test_registry_create_surfaces_the_same_error(self) -> None:
        """The public entry point, not just the private factory."""
        with without_boto3():
            with pytest.raises(ValueError, match="boto3"):
                StorageProviderRegistry.create(StorageProviderType.S3, S3_CREDS)

    def test_config_error_wins_over_the_dependency_error(self) -> None:
        """A tenant with no bucket must not be told to install boto3.

        Ordering is deliberate (registry.py): a misconfigured tenant needs to
        fix their credentials, and telling them to install a package they may
        already have sends them down the wrong path.
        """
        with without_boto3():
            with pytest.raises(ValueError, match="missing required key"):
                _s3_factory({"region": "eu-west-1"})


class TestGuardWhenBoto3IsPresent:
    """THE COUNTER-TEST — the assertion that decides (a) from (b)."""

    def test_registry_constructs_an_s3_provider(self) -> None:
        """With boto3 importable, the shipped factory builds a real provider.

        Before the decision this failed on every clean install — which is the
        fact that proved S3 was a latent broken feature rather than a
        documented optional extra.
        """
        with patch("boto3.client", MagicMock(return_value=MagicMock())):
            provider = StorageProviderRegistry.create(StorageProviderType.S3, S3_CREDS)

        assert isinstance(provider, S3Storage)
        assert provider.bucket == "my-bucket"
        assert provider.region == "eu-west-1"

    def test_environment_is_not_silently_skipping_the_above(self) -> None:
        """Guards against a green run where the decisive assertion never ran.

        The old guard tests skipped whenever boto3 was importable, so the very
        environment this bead is about was the one where they stopped
        testing. This fails if boto3 is missing from the venv, which would
        silently reduce the suite back to skip-only coverage.
        """
        if not _boto3_installed():  # pragma: no cover - only on a broken install
            pytest.fail(
                "boto3 is declared and locked, so it must be installed in the test environment; "
                "run `uv sync` before concluding anything about S3"
            )
