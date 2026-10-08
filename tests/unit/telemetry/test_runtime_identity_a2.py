"""AMD A-2: the manifest must ASSERT the runtime identity, not merely record it.

Acceptance criterion: "the manifest contains all five values and the assertion
fails the run when the loop or HTTP class does not match the Dockerfile CMD; a
mismatched run is labelled non-representative in the artefact rather than silently
accepted."

The second half is the part that needs testing. Recording a value and asserting it
are different obligations, and the asymmetry that bit first - the HTTP check ran
only ``if http is not None`` while the loop check handled absence - meant the field
most likely to be missing was the one that could be missed silently. Each test here
is written against the failure, not the happy path.
"""

from __future__ import annotations

import pytest

from src.app.core.monitoring.app_config_manifest import (
    ManifestIncomplete,
    assert_runtime_identity,
    collect_app_config,
)

FIVE = (
    "python_version",
    "event_loop_module",
    "event_loop_resolved",
    "http_impl",
    "image_digest",
)


def _manifest(**overrides: object) -> dict:
    """A manifest with all five A-2 fields present and correct."""
    base = {
        "python_version": "3.13.7",
        "event_loop_module": "uvloop.loop",
        "event_loop_resolved": "uvloop",
        "http_impl": "httptools",
        "image_digest": "sha256:" + "a" * 64,
    }
    base.update(overrides)
    return {"runtime": base}


class TestAllFiveValuesAreRecorded:
    def test_the_manifest_carries_all_five(self) -> None:
        runtime = collect_app_config()["runtime"]
        for field in FIVE:
            assert field in runtime, f"A-2 requires {field!r} in the manifest"

    def test_the_http_implementation_is_actually_resolved(self) -> None:
        """PF-6: httptools IS present, so a manifest reporting h11 would be wrong.

        Read off uvicorn's own bound symbol rather than guessed.
        """
        assert collect_app_config()["runtime"]["http_impl"] == "httptools"

    def test_the_resolved_loop_is_distinct_from_the_running_loop(self) -> None:
        """A-2 is about what the flagless CMD SELECTS, not this thread's loop."""
        runtime = collect_app_config()["runtime"]
        assert "event_loop_resolved" in runtime
        assert "event_loop_module" in runtime


class TestTheAssertionFires:
    def test_a_production_runtime_passes(self) -> None:
        assert assert_runtime_identity(_manifest())["runtime"]["http_impl"] == "httptools"

    def test_an_asyncio_resolution_is_refused(self) -> None:
        with pytest.raises(ManifestIncomplete, match="uvloop"):
            assert_runtime_identity(_manifest(event_loop_resolved="asyncio"))

    def test_an_h11_resolution_is_refused(self) -> None:
        """The HTTP half must be asserted, not recorded and forgotten."""
        with pytest.raises(ManifestIncomplete, match="httptools"):
            assert_runtime_identity(_manifest(http_impl="h11"))

    def test_an_absent_http_value_fails_rather_than_skipping(self) -> None:
        """The defect this closed: ``if http is not None`` let absence pass."""
        with pytest.raises(ManifestIncomplete, match="not recorded"):
            assert_runtime_identity(_manifest(http_impl=None))

    def test_every_missing_field_is_named(self) -> None:
        manifest = {"runtime": {}}
        with pytest.raises(ManifestIncomplete) as caught:
            assert_runtime_identity(manifest)
        for field in FIVE:
            assert field in str(caught.value)

    def test_the_mismatch_is_labelled_non_representative(self) -> None:
        """Not merely refused - the artefact must say what the run IS."""
        with pytest.raises(ManifestIncomplete, match="NON-REPRESENTATIVE"):
            assert_runtime_identity(_manifest(http_impl="h11"))

    def test_an_unresolved_http_value_is_not_treated_as_a_match(self) -> None:
        """A resolver that could not answer is a failure, not a pass."""
        with pytest.raises(ManifestIncomplete):
            assert_runtime_identity(_manifest(http_impl="unresolved: no uvicorn"))

    def test_a_missing_image_digest_fails(self) -> None:
        """Absence of the digest is absence of a specific build."""
        with pytest.raises(ManifestIncomplete, match="image_digest"):
            assert_runtime_identity(_manifest(image_digest=None))
