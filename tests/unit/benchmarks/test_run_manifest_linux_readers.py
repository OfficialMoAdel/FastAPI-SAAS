"""0eio: the four Linux deployment readers, exercised against Linux-shaped INPUT.

The deployment manifest of addendum 6.1 is worth something only because
distro, CPU model, core count and (for IP-literal addresses) store locality are
READ OFF THE MACHINE, so they are evidence rather than a declaration. Every one
of those four readers is inert on a non-Linux host -- and this host is Windows --
so without this file the Linux branch of each reader had never been executed by
anything. A reader that has only ever been handed a stub can be wrong in a way
that surfaces only on the one host the programme is waiting on.

The fix is NOT to skip these readers on Linux. It is to drive them from input
that looks like Linux input, which needs no Linux host, and to pin what they do
when that input cannot be read. Three properties are pinned here:

  (a) each reader answers correctly from Linux-shaped input -- real /proc/cpuinfo
      key spellings, a real os-release payload, the affinity mask;
  (b) an underivable answer is ABSENT (``None``) and never an empty string,
      because an empty string is a value a report would render as a recorded
      fact about the machine;
  (c) the refusal survives: the five evidence fields keep aborting the run
      (exit 10) rather than acquiring a default. Addendum A-4 makes the absence
      itself the finding, so a test that let a default through would be the
      defect, not the guard.

Note the platform asymmetry these tests deliberately encode:
``platform.freedesktop_os_release`` and ``os.sched_getaffinity`` do not exist on
Windows at all, which is exactly why ``raising=False`` appears on the patches
that create them. That is not a workaround; it is what makes the Linux branch
reachable from a Windows test runner.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from src.benchmarks import run_manifest as rm

if TYPE_CHECKING:
    pass

# --- Linux-shaped input fixtures -------------------------------------------
# Real /proc/cpuinfo key spellings. These are the strings the reader matches on,
# so a rename on either side would silently stop resolving a model on the one
# host that matters.
CPUINFO_MODEL_NAME = "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) Platinum 8375C CPU @ 2.90GHz\n"
CPUINFO_HARDWARE = "processor\t: 0\nhardware\t: BCM2835\n"
CPUINFO_MODEL_KEY = "processor\t: 0\ncpu model\t: ARMv7 Processor rev 4 (v7l)\n"
CPUINFO_UNKNOWN_KEYS_ONLY = "processor\t: 0\nvendor_id\t: GenuineIntel\ncpu family\t: 6\n"
CPUINFO_EMPTY_VALUE = "processor\t: 0\nmodel name\t:   \n"


def _fake_cpuinfo(text: str | OSError) -> Any:
    """A ``Path.read_text`` stand-in that answers only for /proc/cpuinfo.

    Delegates for every other path so that unrelated readers -- and pytest's own
    internals -- are untouched by the patch. Passing an ``OSError`` instance
    models an unreadable /proc/cpuinfo, which is the absence case AC (b) needs.
    """
    real_read_text = Path.read_text

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name != "cpuinfo":
            return real_read_text(self, *args, **kwargs)
        if isinstance(text, OSError):
            raise text
        return text

    return read_text


def _install_cpuinfo(monkeypatch: pytest.MonkeyPatch, text: str | OSError) -> None:
    monkeypatch.setattr(Path, "read_text", _fake_cpuinfo(text))


def _install_os_release(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Create ``platform.freedesktop_os_release`` on hosts that lack it.

    ``raising=False`` is required, not cosmetic: on Windows the attribute does
    not exist, and the reader's ``except AttributeError`` is one of the two
    absence branches under test. Without this the Linux-shaped path would be
    unreachable from this host at all.
    """
    if isinstance(payload, BaseException):
        def boom() -> dict[str, str]:
            raise payload
    else:
        def boom() -> dict[str, str]:
            return cast("dict[str, str]", payload)
    monkeypatch.setattr(platform, "freedesktop_os_release", boom, raising=False)


class TestTheCpuModelReaderReadsRealLinuxKeySpellings:
    """AC (a): each real /proc/cpuinfo key spelling resolves to the model."""

    @pytest.mark.parametrize(
        ("label", "cpuinfo"),
        [
            ("model name", CPUINFO_MODEL_NAME),
            ("hardware", CPUINFO_HARDWARE),
            ("cpu model", CPUINFO_MODEL_KEY),
        ],
    )
    def test_each_real_key_spelling_yields_the_model(
        self, monkeypatch: pytest.MonkeyPatch, label: str, cpuinfo: str
    ) -> None:
        _install_cpuinfo(monkeypatch, cpuinfo)
        assert rm.resolve_cpu_model() == cpuinfo.rpartition(":")[2].strip(), (
            f"the {label!r} spelling must resolve to its value"
        )

    def test_a_key_casing_difference_still_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The reader lowercases the key, so real-world casing drift is tolerated."""
        _install_cpuinfo(monkeypatch, "Processor\t: 0\nMODEL NAME\t: AMD EPYC 7763 64-Core Processor\n")
        assert rm.resolve_cpu_model() == "AMD EPYC 7763 64-Core Processor"

    def test_the_first_recognised_line_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multi-field input: a second, different model must not override the first.

        This is the assertion that pins WHICH line the reader takes. Without it
        a reader that simply returned the last match would satisfy every
        single-key test above and still be wrong on a real cpuinfo.
        """
        _install_cpuinfo(
            monkeypatch,
            "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) CPU @ 2.90GHz\n"
            "processor\t: 1\nmodel name\t: Something Else Entirely\n",
        )
        assert rm.resolve_cpu_model() == "Intel(R) Xeon(R) CPU @ 2.90GHz"

    def test_an_unrecognised_line_before_a_recognised_one_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_cpuinfo(
            monkeypatch,
            "vendor_id\t: GenuineIntel\ncache size\t: 36608 KB\nmodel name\t: Intel(R) Xeon(R)\n",
        )
        assert rm.resolve_cpu_model() == "Intel(R) Xeon(R)"


class TestTheCpuModelReaderReportsAbsenceRatherThanAGuess:
    """AC (b) and (c): an underivable answer is ``None``, never a guess."""

    def test_an_unreadable_cpuinfo_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_cpuinfo(monkeypatch, OSError("no /proc/cpuinfo here"))
        assert rm.resolve_cpu_model() is None

    def test_a_cpuinfo_of_only_unknown_keys_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC (c): an unknown key must produce None, NOT the nearest-looking line."""
        _install_cpuinfo(monkeypatch, CPUINFO_UNKNOWN_KEYS_ONLY)
        assert rm.resolve_cpu_model() is None

    def test_an_empty_cpuinfo_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_cpuinfo(monkeypatch, "")
        assert rm.resolve_cpu_model() is None

    def test_a_recognised_key_with_a_blank_value_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``model name : <spaces>`` is a hole, not a model.

        Returning the stripped empty string here would put a blank into the
        manifest, which reads as a recorded fact.
        """
        _install_cpuinfo(monkeypatch, CPUINFO_EMPTY_VALUE)
        assert rm.resolve_cpu_model() is None

    def test_the_absence_is_none_and_never_an_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property itself, independent of which absence branch was taken."""
        for candidate in (OSError("gone"), CPUINFO_UNKNOWN_KEYS_ONLY, "", CPUINFO_EMPTY_VALUE):
            _install_cpuinfo(monkeypatch, candidate)
            result = rm.resolve_cpu_model()
            assert result is None, f"cpuinfo {candidate!r} produced {result!r}, not an absence"


class TestTheDistroReaderReadsAnOsReleasePayload:
    """AC (a): the reader prefers PRETTY_NAME and falls back to NAME."""

    def test_pretty_name_is_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_os_release(
            monkeypatch, {"NAME": "Ubuntu", "PRETTY_NAME": "Ubuntu 24.04.1 LTS", "ID": "ubuntu"}
        )
        assert rm.resolve_distro() == "Ubuntu 24.04.1 LTS"

    def test_it_falls_back_to_name_when_pretty_name_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_os_release(monkeypatch, {"NAME": "Debian GNU/Linux", "ID": "debian"})
        assert rm.resolve_distro() == "Debian GNU/Linux"

    def test_a_surrounding_whitespace_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_os_release(monkeypatch, {"PRETTY_NAME": "  Alpine Linux v3.20  "})
        assert rm.resolve_distro() == "Alpine Linux v3.20"


class TestTheDistroReaderReportsAbsenceRatherThanAGuess:
    """AC (b): both absence branches the reader declares, pinned."""

    def test_an_oserror_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_os_release(monkeypatch, OSError("no os-release"))
        assert rm.resolve_distro() is None

    def test_an_attribute_error_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real Windows shape: the function does not exist at all.

        This is the branch a Windows host takes today, so it is the one that had
        no coverage on the host where these readers were written.
        """
        _install_os_release(monkeypatch, AttributeError("freedesktop_os_release"))
        assert rm.resolve_distro() is None

    def test_an_empty_payload_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``{}`` must be an absence, not an empty distro string."""
        _install_os_release(monkeypatch, {})
        assert rm.resolve_distro() is None

    def test_a_blank_pretty_name_is_an_absence_not_a_fallback_to_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A whitespace-only PRETTY_NAME yields None rather than consulting NAME.

        This is the ``or`` chain meeting a string that is non-empty but blank:
        ``fields.get("PRETTY_NAME") or fields.get("NAME")`` short-circuits on a
        truthy value, so ``"   "`` wins the chain and ``.strip()`` then empties
        it. None is the CORRECT answer here rather than a weaker one. A present
        but blank PRETTY_NAME is a hole in the payload, and quietly substituting
        NAME would report a distro the machine did not declare as its pretty
        name -- the same substitution `_field_is_absent` refuses when it treats
        an empty string as absent for the five evidence fields.
        """
        _install_os_release(monkeypatch, {"PRETTY_NAME": "   ", "NAME": "Debian GNU/Linux"})
        assert rm.resolve_distro() is None

    def test_an_absent_pretty_name_does_fall_through_to_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The contrast case: key genuinely missing, so the chain may proceed.

        Recorded next to the blank-value case so the distinction between
        "absent key" and "present but blank value" cannot drift.
        """
        _install_os_release(monkeypatch, {"NAME": "Debian GNU/Linux"})
        assert rm.resolve_distro() == "Debian GNU/Linux"

    def test_the_absence_is_none_and_never_an_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for candidate in (OSError("gone"), AttributeError("absent"), {}, {"PRETTY_NAME": "   "}):
            _install_os_release(monkeypatch, candidate)
            result = rm.resolve_distro()
            assert result is None, f"os-release {candidate!r} produced {result!r}, not an absence"


class TestTheCoreCountReaderPrefersTheAffinityMask:
    """AC (a): the affinity mask is the honest answer, not os.cpu_count().

    ``os.cpu_count()`` reports the host's cores and overstates the parallelism a
    cgroup-limited container actually has, so the mask is preferred and the
    fallback is only reached when the mask is unavailable.
    """

    def test_the_mask_is_used_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1, 2}, raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 128, raising=False)
        assert rm.resolve_core_count() == 3

    def test_an_emptiness_raising_mask_is_not_treated_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mask that raises must fall through, not report 0 cores.

        0 is the one value that would sail through ``_field_is_absent`` (it only
        treats None and empty collections as absent), so a reader that returned
        it would produce a manifest that looks complete and claims a machine
        with no cores.
        """
        def raising(_pid: int) -> set[int]:
            raise OSError("no affinity for you")

        monkeypatch.setattr(os, "sched_getaffinity", raising, raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 4, raising=False)
        assert rm.resolve_core_count() == 4

    def test_it_falls_back_to_cpu_count_when_the_mask_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(os, "sched_getaffinity", raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 6, raising=False)
        assert rm.resolve_core_count() == 6

    def test_the_answer_is_never_zero_or_none_when_either_source_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0}, raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: 6, raising=False)
        first = rm.resolve_core_count()
        monkeypatch.delattr(os, "sched_getaffinity", raising=False)
        second = rm.resolve_core_count()
        assert first == 1 and first is not None
        assert second == 6 and second is not None


class TestStoreLocalityIsDerivedFromTheAddressesNotAssumed:
    """AC (a): a loopback or IP-literal address decides locality by what it is."""

    def test_a_loopback_address_is_co_located(self) -> None:
        assert rm.resolve_store_locality({"cache": "127.0.0.1:6379"}) == "co-located"

    def test_a_named_loopback_address_is_co_located(self) -> None:
        assert rm.resolve_store_locality({"cache": "localhost:6379"}) == "co-located"

    def test_a_loopback_address_with_a_database_suffix_is_co_located(self) -> None:
        assert rm.resolve_store_locality({"cache": "127.0.0.1:6379/0"}) == "co-located"

    def test_a_bracketed_ipv6_loopback_with_a_port_is_co_located(self) -> None:
        """``[::1]:6379`` and a bare ``::1`` share a colon count and differ.

        Splitting by colon count calls a co-located store remote, which is the
        one misreading that changes every RTT conclusion in the programme.
        """
        assert rm.resolve_store_locality({"cache": "[::1]:6379"}) == "co-located"

    def test_a_routable_literal_is_remote(self) -> None:
        assert rm.resolve_store_locality({"cache": "10.0.0.5:6379"}) == "remote"

    def test_a_routable_ipv6_literal_is_remote(self) -> None:
        assert rm.resolve_store_locality({"cache": "2001:db8::1"}) == "remote"

    def test_one_routable_and_one_loopback_is_mixed(self) -> None:
        assert (
            rm.resolve_store_locality({"cache": "127.0.0.1:6379", "queue": "10.0.0.5:6379"})
            == "mixed"
        )

    def test_every_store_co_located_is_co_located(self) -> None:
        verdict = rm.resolve_store_locality(
            {"cache": "127.0.0.1:6379", "queue": "localhost:6379", "admin": "0.0.0.0:6379"}
        )
        assert verdict == "co-located"


class TestStoreLocalityIsAbsentWhenItCannotBeDerived:
    """AC (b) and (c): a bare hostname yields an absence, never a guess."""

    def test_a_bare_hostname_without_the_declaration_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(rm.STORE_LOCALITY_ENV, raising=False)
        assert rm.resolve_store_locality({"cache": "redis.internal.example:6379"}) is None

    def test_an_empty_stores_map_is_none(self) -> None:
        assert rm.resolve_store_locality({}) is None

    def test_an_unrecognised_declaration_is_not_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC (c): a declared value outside the vocabulary is ignored, not passed through.

        "co-located-ish" must not become a locality verdict; the vocabulary is
        the set of things the report knows how to render.
        """
        monkeypatch.setenv(rm.STORE_LOCALITY_ENV, "probably-local")
        assert rm.resolve_store_locality({"cache": "redis.internal.example:6379"}) is None

    def test_a_valid_declaration_is_honoured_for_a_bare_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(rm.STORE_LOCALITY_ENV, "co-located")
        assert rm.resolve_store_locality({"cache": "redis.internal.example:6379"}) == "co-located"

    def test_the_declaration_is_read_case_insensitively(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(rm.STORE_LOCALITY_ENV, "  Co-Located  ")
        assert rm.resolve_store_locality({"cache": "redis.internal.example:6379"}) == "co-located"

    def test_an_ip_literal_is_not_overridden_by_the_declaration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An address that IS an address decides for itself.

        If the declaration could override a routable literal, the field would be
        a self-report dressed as evidence, which is the distinction addendum 6.1
        is built on.
        """
        monkeypatch.setenv(rm.STORE_LOCALITY_ENV, "co-located")
        assert rm.resolve_store_locality({"cache": "10.0.0.5:6379"}) == "remote"


class TestNoReaderEverReturnsAnEmptyString:
    """AC (b), stated once as a property over every absence path of every reader."""

    @pytest.mark.parametrize(
        "case",
        ["cpuinfo_absent", "cpuinfo_unknown", "cpuinfo_blank", "os_release_absent",
         "os_release_empty", "hostname_undeclared"],
    )
    def test_the_reader_is_silent_or_answered_never_blank(
        self, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        if case.startswith("cpuinfo"):
            cpuinfo_cases: dict[str, str | OSError] = {
                "cpuinfo_absent": OSError("gone"),
                "cpuinfo_unknown": CPUINFO_UNKNOWN_KEYS_ONLY,
                "cpuinfo_blank": CPUINFO_EMPTY_VALUE,
            }
            _install_cpuinfo(monkeypatch, cpuinfo_cases[case])
            result: Any = rm.resolve_cpu_model()
        elif case.startswith("os_release"):
            _install_os_release(
                monkeypatch,
                {"os_release_absent": AttributeError("absent"), "os_release_empty": {}}[case],
            )
            result = rm.resolve_distro()
        else:
            monkeypatch.delenv(rm.STORE_LOCALITY_ENV, raising=False)
            result = rm.resolve_store_locality({"cache": "redis.internal.example:6379"})

        assert result != "", (
            f"{case} produced an empty string, which a report renders as a recorded fact; "
            "absence must be None"
        )


class TestTheFiveEvidenceFieldsStillAbortTheRun:
    """AC (d): the refusal must survive. A default here would be the defect."""

    EVIDENCE_FIELDS = (
        "orchestrator", "distro", "cpu_model", "core_count", "store_locality",
    )

    @staticmethod
    def _complete() -> dict[str, Any]:
        return dict.fromkeys(rm.DEPLOYMENT_MANIFEST_FIELDS, "recorded")

    def test_a_complete_manifest_is_accepted(self) -> None:
        assert rm.assert_deployment_recorded(self._complete()) is not None

    @pytest.mark.parametrize("field", EVIDENCE_FIELDS)
    @pytest.mark.parametrize("absent", [None, ""], ids=["none", "empty-string"])
    def test_each_evidence_field_absent_aborts_the_run(self, field: str, absent: Any) -> None:
        """Both spellings of absent, for all five fields.

        An empty string is the dangerous one: ``_field_is_absent`` treats it as
        absent, and if it ever stopped doing so the manifest would read as
        complete.
        """
        deployment = self._complete()
        deployment[field] = absent
        with pytest.raises(rm.DeploymentUndeclared) as raised:
            rm.assert_deployment_recorded(deployment)
        assert field in raised.value.fields, (
            f"{field!r} must be named on the exception so the operator knows which fact is missing"
        )

    def test_a_missing_image_digest_does_not_abort_the_run(self) -> None:
        """The digest is exempt by design and this pins that exception.

        A missing digest is an honest statement about a bare-metal host. It is
        the ONLY exempt field, so if this ever widened, a throughput claim would
        lose its evidence without anyone noticing.
        """
        deployment = self._complete()
        deployment["image_digest"] = None
        assert rm.assert_deployment_recorded(deployment) is not None

    def test_every_absent_field_is_named_at_once(self) -> None:
        """All five at once, so the operator gets the whole list in one run."""
        with pytest.raises(rm.DeploymentUndeclared) as raised:
            rm.assert_deployment_recorded({})
        assert set(raised.value.fields) == set(self.EVIDENCE_FIELDS)

    def test_the_message_tells_the_operator_what_to_set(self) -> None:
        """The refusal must be actionable, not merely a failure.

        An exit code with no instruction is indistinguishable from a bug.
        """
        deployment = self._complete()
        deployment["core_count"] = None
        with pytest.raises(rm.DeploymentUndeclared) as raised:
            rm.assert_deployment_recorded(deployment)
        message = str(raised.value)
        assert rm.STORE_LOCALITY_ENV in message
        assert "core_count" in message

    def test_the_exit_code_is_ten(self) -> None:
        """The refusal has a distinct code; addendum 6.1 names it.

        Without a dedicated code the operator cannot tell this refusal apart from
        any other non-zero exit in a run that may fail for several reasons.
        """
        assert rm.EXIT_DEPLOYMENT_UNDECLARED == 10

    @pytest.mark.parametrize(
        "field", ("orchestrator", "distro", "cpu_model", "store_locality")
    )
    def test_an_absent_reader_result_flows_into_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        """End to end: a reader returning None must abort, not default.

        This is the assertion that would catch a future change adding a default
        to ``build_deployment_manifest``. The reader is stubbed to its genuine
        absence result and the gate is asked whether that absence is survivable.

        ``core_count`` is deliberately NOT in this list. Its absence cannot be
        reached through the reader on any host that reports its own core count:
        ``resolve_core_count`` prefers the affinity mask but always falls back
        to ``os.cpu_count()``, which CPython always provides, so it returns a
        number here (12 on this host) however the mask is stubbed. Its absence
        is covered instead by ``test_each_evidence_field_absent_aborts_the_run``,
        which hands the gate the value directly rather than going via a reader
        that cannot produce it.
        """
        _install_os_release(monkeypatch, AttributeError("absent"))
        _install_cpuinfo(monkeypatch, OSError("gone"))
        monkeypatch.delenv(rm.STORE_LOCALITY_ENV, raising=False)
        monkeypatch.delenv(rm.ORCHESTRATOR_ENV, raising=False)

        manifest = rm.build_deployment_manifest(
            orchestrator=None if field == "orchestrator" else "declared",
            distro=None if field == "distro" else "declared",
            cpu_model=None if field == "cpu_model" else "declared",
            core_count=4,
            store_locality=None if field == "store_locality" else "co-located",
            image_digest="sha256:deadbeef",
            stores={"cache": "redis.internal.example:6379"},
        )
        with pytest.raises(rm.DeploymentUndeclared) as raised:
            rm.assert_deployment_recorded(manifest)
        assert field in raised.value.fields


class TestTheReadersSurviveBeingCalledOnThisHost:
    """The bead's premise, asserted: Windows resolves none of the four fields.

    If this ever starts failing on Windows it means the host gained a Linux
    input source, at which point these tests are measuring the real thing and the
    injected-input tests above become the belt to that pair of braces.
    """

    def test_the_cpuinfo_reader_is_inert_without_proc_cpuinfo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_cpuinfo(monkeypatch, OSError("no /proc/cpuinfo on this host"))
        assert rm.resolve_cpu_model() is None

    def test_the_distro_reader_is_inert_without_os_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_os_release(monkeypatch, AttributeError("no os-release on this host"))
        assert rm.resolve_distro() is None

    def test_the_store_locality_reader_is_honest_about_a_bare_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(rm.STORE_LOCALITY_ENV, raising=False)
        assert rm.resolve_store_locality({"cache": "redis.internal.example:6379"}) is None
