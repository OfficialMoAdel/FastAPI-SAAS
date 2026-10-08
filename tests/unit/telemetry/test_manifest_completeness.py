"""T-8 verification: prove the manifest is COMPLETE and that a missing field ABORTS.

Named by the bead, so the path is the bead's.

The negative tests are the substance. A completeness check that has never been
seen to refuse is indistinguishable from a check that always passes, and this
programme's standing rule is that a manifest which omits what it could not
establish must NOT be emitted - because a manifest that quietly omits reads as
complete.

Every refusal test deletes or blanks one required field and asserts the run is
refused. :func:`assert_manifest_complete` must name the field, because a run that
reports one missing field per attempt turns a two-minute fix into a two-hour one.
"""

from __future__ import annotations

import copy
import json

import pytest

from src.app.core.monitoring.app_config_manifest import (
    MANIFEST_SCHEMA,
    REQUIRED_FIELDS,
    ManifestIncomplete,
    assert_manifest_complete,
    assert_no_credentials,
    assert_runtime_identity,
    build_app_config_manifest,
    collect_app_config,
    write_app_config_manifest,
)
from src.app.core.monitoring.telemetry import (
    measure_call_cost_robust,
    telemetry_enabled_flags,
)

# The A-4 deployment target (addendum 5.1 A-4 / 6.1 L-2). These live in the
# harness-side manifest rather than this one, because no process can read its own
# orchestrator's name or its store locality - they are DECLARED by the deployment.
A4_DEPLOYMENT_FIELDS = (
    "orchestrator",
    "distro",
    "cpu_model",
    "core_count",
    "store_locality",
    "image_digest",
)


@pytest.fixture
def manifest() -> dict:
    """A complete manifest, deep-copied per test so deletions cannot leak."""
    return copy.deepcopy(build_app_config_manifest())


class TestTheManifestIsComplete:
    def test_it_builds_and_passes_its_own_check(self, manifest: dict) -> None:
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert assert_manifest_complete(manifest) is manifest

    def test_every_required_field_is_present_and_non_empty(self, manifest: dict) -> None:
        """Acceptance (b). Resolved by walking, not by listing - a dotted path that
        does not resolve would make the check demand a field no manifest can hold."""
        for dotted in REQUIRED_FIELDS:
            node: object = manifest
            for part in dotted.split("."):
                assert isinstance(node, dict), f"{dotted}: {part} is not reachable"
                assert part in node, f"{dotted}: {part} is absent"
                node = node[part]
            assert node not in (None, "", {}, []), f"{dotted} resolved to an empty value"

    def test_the_effective_security_budgets_are_recorded(self, manifest: dict) -> None:
        """The pack ran at 10000/60 instead of (5,60) and no report disclosed it, so
        the rate limiter was never exercised and the Redis cost attributable to it
        was structurally absent from the measurement."""
        budgets = manifest["limits"]["security_budgets"]
        assert budgets["auth"] == {"limit": 5, "window_seconds": 60}
        assert budgets["default_anonymous"] == {"limit": 10, "window_seconds": 3600}
        assert manifest["limits"]["auto_ban_enabled"] is not None

    def test_the_enabled_state_of_every_instrument_is_recorded(self, manifest: dict) -> None:
        """Acceptance (f), second half: an absent series and a series that was
        never enabled are different facts."""
        flags = manifest["telemetry"]["enabled_flags"]
        assert set(flags) == set(telemetry_enabled_flags())


class TestAMissingFieldAbortsRatherThanDefaulting:
    def test_removing_a_required_field_aborts_the_run(self, manifest: dict) -> None:
        """Acceptance (c): the NEGATIVE test."""
        del manifest["limits"]["security_budgets"]
        with pytest.raises(ManifestIncomplete, match="security_budgets"):
            assert_manifest_complete(manifest)

    def test_blanking_a_required_field_aborts(self, manifest: dict) -> None:
        """Empty is as fatal as absent. ``{}`` would read as 'no budgets' rather
        than as 'the budgets could not be read'."""
        manifest["stores"]["redis_cache_host"] = ""
        with pytest.raises(ManifestIncomplete, match="redis_cache_host"):
            assert_manifest_complete(manifest)

    def test_zero_and_false_are_present_values_not_holes(self) -> None:
        """A disabled AUTO_BAN_ENABLED is a configuration, not a missing field -
        and it is the field that most needs recording."""
        from src.app.core.monitoring.app_config_manifest import _is_absent

        assert _is_absent(0) is False
        assert _is_absent(False) is False
        assert _is_absent(None) is True

    def test_the_missing_field_is_named(self, manifest: dict) -> None:
        with pytest.raises(ManifestIncomplete) as caught:
            assert_manifest_complete({**manifest, "schema": ""})
        assert "schema" in str(caught.value)

    def test_every_absent_field_is_reported_at_once(self, manifest: dict) -> None:
        manifest["stores"].pop("redis_queue_host")
        manifest["runtime"]["cpu_count"] = None
        with pytest.raises(ManifestIncomplete) as caught:
            assert_manifest_complete(manifest)
        message = str(caught.value)
        assert "redis_queue_host" in message and "cpu_count" in message

    def test_the_writer_refuses_by_default(self, tmp_path: object) -> None:
        """A refused manifest must not reach an artefact either.

        Driven by INJECTING unreadable settings rather than by breaking a real
        manifest: on a correctly configured host the manifest IS complete, so a
        test that relied on the live configuration would stop testing anything the
        moment the environment was right. A bare object exposes no budget
        attributes, so ``auto_ban_enabled`` resolves to nothing and the run must be
        refused.
        """
        from pathlib import Path

        output = Path(tmp_path) / "app_config.json"
        unreadable = object()  # no settings attributes at all
        with pytest.raises(ManifestIncomplete):
            write_app_config_manifest(output, security=unreadable, services=unreadable)
        assert not output.exists(), "a refused manifest reached the artefact"

    def test_the_escape_hatch_is_explicit_not_accidental(self, tmp_path: object) -> None:
        """require_complete=False is the ONLY way to write a partial manifest, and
        it has to be asked for by name.

        Pinned because the alternative - a partial manifest written silently - is
        the failure T-8 exists to prevent, and a reviewer should be able to see
        that reaching it costs a deliberate argument.
        """
        from pathlib import Path

        output = Path(tmp_path) / "partial.json"
        write_app_config_manifest(output, require_complete=False)
        assert output.exists(), "the explicit opt-in did not write a partial manifest"


class TestRuntimeIdentityIsAssertedNotJustRecorded:
    def test_the_loop_and_http_values_are_present(self, manifest: dict) -> None:
        runtime = manifest["runtime"]
        assert runtime["event_loop_resolved"]
        assert runtime["http_impl"]

    def test_an_asyncio_run_is_refused_and_labelled(self, manifest: dict) -> None:
        """Acceptance (d). Dockerfile's CMD passes no --loop and no --http, so
        uvicorn selects both by importability and a run can resolve differently
        than production with nobody changing a flag."""
        manifest["runtime"]["event_loop_resolved"] = "asyncio"
        manifest["runtime"]["event_loop_module"] = "asyncio.unix_events"
        with pytest.raises(ManifestIncomplete, match="NON-REPRESENTATIVE"):
            assert_runtime_identity(manifest)

    def test_an_h11_run_is_refused(self, manifest: dict) -> None:
        manifest["runtime"]["http_impl"] = "h11"
        with pytest.raises(ManifestIncomplete, match="httptools"):
            assert_runtime_identity(manifest)

    def test_an_unresolved_value_is_not_treated_as_a_match(self, manifest: dict) -> None:
        manifest["runtime"]["http_impl"] = "unresolved: no uvicorn"
        with pytest.raises(ManifestIncomplete):
            assert_runtime_identity(manifest)


class TestTheDeploymentTargetBlocksTheRun:
    def test_a_missing_deployment_field_is_a_blocking_failure(self) -> None:
        """Acceptance (e): A-4's six fields, and absence is BLOCKING.

        N-3 found no in-repo documentation of the deployment target, and Phase 0's
        RTT regime depends entirely on topology - so a run without these cannot be
        interpreted rather than merely being incomplete.
        """
        from src.benchmarks.run_manifest import (
            DeploymentUndeclared,
            assert_deployment_recorded,
        )

        assert len(A4_DEPLOYMENT_FIELDS) == 6
        with pytest.raises(DeploymentUndeclared):
            assert_deployment_recorded({"orchestrator": "github-actions-shared-runner"})

    def test_a_complete_deployment_record_passes(self) -> None:
        from src.benchmarks.run_manifest import assert_deployment_recorded

        complete = dict.fromkeys(A4_DEPLOYMENT_FIELDS, "value")
        assert assert_deployment_recorded(complete) == complete


class TestTheManifestIsWrittenBesideTheArtefact:
    def test_it_is_written_and_readable(self, tmp_path: object) -> None:
        from pathlib import Path

        output = write_app_config_manifest(Path(tmp_path) / "reports" / "app_config.json")
        assert output.exists()
        assert json.loads(output.read_text())["schema"] == MANIFEST_SCHEMA

    def test_no_credential_reaches_the_artefact(self) -> None:
        assert_no_credentials(collect_app_config())

    def test_a_credential_key_is_refused(self) -> None:
        with pytest.raises(ManifestIncomplete, match="credential"):
            assert_no_credentials({"stores": {"redis_password": "hunter2"}})


class TestTheDisabledPathIsCostFree:
    def test_collecting_with_telemetry_off_costs_under_its_ceiling(self) -> None:
        """Acceptance (f), first half.

        The instrument's own cost is not the subject here - this is the cost of the
        production path when every instrument is off, which is what every
        deployment that never reads a series pays.
        """
        from src.app.core.monitoring.telemetry import TELEMETRY_ENABLED_FLAGS

        assert not any(TELEMETRY_ENABLED_FLAGS.values()), (
            "a default-on instrument charges its cost to deployments that never "
            "read its output"
        )

        def one_read() -> None:
            telemetry_enabled_flags()

        assert measure_call_cost_robust(one_read) <= 50.0
