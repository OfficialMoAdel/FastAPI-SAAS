"""T-4..T-8: the void conditions, the pipeline-aware counter, the process series, and the manifest.

The properties pinned here are the ones whose violation yields a number that looks
admissible and is not:

- a status distribution that ANNOTATES a rejection rather than voiding the phase,
  which produces a latency distribution of fast rejections and flatters the system;
- a security-event counter that cannot prove ABSENCE, which is the only form of
  the evidence Gate A checkbox 6 accepts;
- a Redis counter blind to pipelined commands, which reads as "fewer commands"
  rather than as a bug in the instrument;
- an RSS series whose window is too short to tell a leak from arena growth;
- a manifest that DEFAULTS a field it could not read, which makes an incomplete
  run look complete.
"""

from __future__ import annotations

import pytest

from src.app.core.monitoring.app_config_manifest import (
    REQUIRED_FIELDS,
    ManifestIncomplete,
    assert_manifest_complete,
    assert_no_credentials,
    assert_runtime_identity,
    build_app_config_manifest,
    collect_app_config,
)
from src.app.core.monitoring.process_series import (
    MINIMUM_WINDOW_SECONDS,
    PACK_WINDOW_SECONDS,
    ProcessSeriesSampler,
    assert_host_recorded,
    assert_tracemalloc_is_attribution_only,
    assert_window_long_enough,
    measure_sampler_cost,
)
from src.app.core.monitoring.redis_commands import (
    CANDIDATE_SEND_PATHS,
    RedisCommandShim,
    assert_counts_agree,
    assert_pipeline_aware,
    commandstats_total,
    measure_shim_cost,
)
from src.app.core.monitoring.void_conditions import (
    SecurityEventCounter,
    StatusDistribution,
    assert_phase_admissible,
    assert_run_saw_zero_security_events,
    measure_disabled_cost,
    measure_enabled_cost,
    phase_is_void,
)

def _runtime(**overrides: object) -> dict:
    """A runtime block carrying all five A-2 values, correct unless overridden."""
    runtime = {
        "python_version": "3.13.7",
        "event_loop_module": "uvloop.loop",
        "event_loop_resolved": "uvloop",
        "http_impl": "httptools",
        "image_digest": "sha256:" + "b" * 64,
    }
    runtime.update(overrides)
    return {"runtime": runtime}


# -- T-4: status distribution ------------------------------------------------


class TestStatusDistribution:
    def test_distribution_is_keyed_by_phase(self) -> None:
        """A run-level total would discard the granularity needed to re-run one phase."""
        counter = StatusDistribution(enabled=True)
        for code in (200, 200, 429):
            counter.record("cold", code)
        for code in (200, 204):
            counter.record("warm", code)

        assert counter.phase_distribution("cold") == {"2xx": 2, "4xx": 1}
        assert counter.phase_distribution("warm") == {"2xx": 2}

    def test_disabled_counter_records_nothing(self) -> None:
        counter = StatusDistribution(enabled=False)
        for code in (200, 429, 500):
            counter.record("cold", code)
        assert counter.export()["phases"] == {}

    def test_a_phase_with_a_rejection_is_void_not_annotated(self) -> None:
        """Unified plan 4.5 verbatim: a phase returning 429/401 IS VOID."""
        counter = StatusDistribution(enabled=True)
        for code in (200, 200, 200, 429):
            counter.record("cold", code)

        distribution = counter.phase_distribution("cold")
        assert phase_is_void(distribution) == ["4xx"]
        with pytest.raises(AssertionError, match="VOID"):
            assert_phase_admissible(distribution, "cold")

    def test_a_clean_phase_is_admissible(self) -> None:
        counter = StatusDistribution(enabled=True)
        for code in (200, 201, 204, 301):
            counter.record("warm", code)
        assert phase_is_void(counter.phase_distribution("warm")) == []
        assert_phase_admissible(counter.phase_distribution("warm"), "warm")

    def test_void_message_names_the_offending_class(self) -> None:
        """An artefact that says 'void' without saying why sends the reader back."""
        counter = StatusDistribution(enabled=True)
        counter.record("cold", 503)
        with pytest.raises(AssertionError, match=r"\['5xx'\]"):
            assert_phase_admissible(counter.phase_distribution("cold"), "cold")

    def test_enabled_state_is_in_the_export(self) -> None:
        """An absent series must be distinguishable from a series with no events."""
        counter = StatusDistribution(enabled=False)
        counter.record("cold", 200)
        assert counter.export()["enabled"] is False


# -- T-5: rate-limit / auto-ban events ---------------------------------------


class TestSecurityEventCounter:
    def test_zero_events_proves_absence(self) -> None:
        """Gate A checkbox 6: the requirement is ZERO, which only a count can show."""
        counter = SecurityEventCounter(enabled=True)
        assert counter.total_events() == 0
        assert counter.export()["zero_assertion"] is True
        assert_run_saw_zero_security_events(counter, when="run-start")

    def test_a_rate_limit_event_voids_the_run(self) -> None:
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit(phase="warm")
        with pytest.raises(AssertionError, match="VOID"):
            assert_run_saw_zero_security_events(counter, when="run-end")

    def test_an_auto_ban_event_voids_the_run(self) -> None:
        counter = SecurityEventCounter(enabled=True)
        counter.record_auto_ban(phase="warm")
        with pytest.raises(AssertionError, match="auto_ban=1"):
            assert_run_saw_zero_security_events(counter, when="run-end")

    def test_rate_limit_and_auto_ban_are_reported_separately(self) -> None:
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit()
        counter.record_rate_limit()
        counter.record_auto_ban()
        export = counter.export()
        assert export["rate_limit_events"] == 2
        assert export["auto_ban_events"] == 1
        assert export["total_events"] == 3

    def test_the_counter_changes_no_policy(self) -> None:
        """Whether a 429 counts toward auto-ban is ADR-0017's question, not this one's.

        The counter records what the code does. An instrument that altered the
        behaviour it measures could not then be used to argue about that
        behaviour.
        """
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit()
        assert counter.export()["changes_policy"] is False

    def test_identity_is_not_recorded_verbatim(self) -> None:
        """A performance run must not become a client-identity disclosure surface."""
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit(identity="203.0.113.7")
        export = counter.export()
        assert "203.0.113.7" not in str(export)

    def test_disabled_counter_records_nothing(self) -> None:
        counter = SecurityEventCounter(enabled=False)
        counter.record_rate_limit()
        counter.record_auto_ban()
        assert counter.total_events() == 0

    def test_counter_costs_are_under_their_ceiling(self) -> None:
        assert measure_disabled_cost()["measured_us"] <= measure_disabled_cost()["ceiling_us"]
        assert measure_enabled_cost()["measured_us"] <= measure_enabled_cost()["ceiling_us"]


# -- T-6: pipeline-aware Redis counter ---------------------------------------


class _FakeConnection:
    """A stand-in with the send methods the shim looks for."""

    def __init__(self) -> None:
        self.single: list[tuple] = []
        self.packed: list = []
        self.pipelined: list = []

    def send_command(self, *args, **kwargs) -> str:
        self.single.append(args)
        return "single"

    def send_packed_command(self, all_cmds, **kwargs) -> str:
        self.packed.append(all_cmds)
        return "packed"

    def send_pipelined_command(self, all_cmds, **kwargs) -> str:
        self.pipelined.append(all_cmds)
        return "pipelined"


def _resp(verb: str, *args: bytes) -> bytes:
    parts = [f"${len(verb)}\r\n{verb}\r\n".encode()]
    for arg in args:
        parts.append(f"${len(arg)}\r\n".encode() + arg + b"\r\n")
    return b"*" + str(1 + len(args)).encode() + b"\r\n" + b"".join(parts)


class TestRedisCommandShim:
    def test_pipelined_commands_are_counted_not_only_single_ones(self) -> None:
        """perf J6: a counter on the single path is blind to a pipeline."""
        shim = RedisCommandShim(enabled=True)
        connection = _FakeConnection()
        shim.install(type(connection))

        connection.send_command(b"GET", b"k")
        connection.send_packed_command(
            [_resp("MULTI"), _resp("ZADD", b"k", b"1", b"m"), _resp("EXEC")]
        )
        connection.send_pipelined_command([_resp("GET", b"a"), _resp("SET", b"b", b"v")])
        shim.uninstall(type(connection))

        export = shim.export()
        assert export["redis_total_commands"] == 6
        assert export["redis_commands_by_path"]["send_command"] == 1
        assert export["redis_commands_by_path"]["send_packed_command"] == 3
        assert export["redis_commands_by_path"]["send_pipelined_command"] == 2

    def test_commands_and_round_trips_are_reported_separately(self) -> None:
        """Unified plan 4.5: a pipeline is N commands and 1 round trip."""
        shim = RedisCommandShim(enabled=True)
        connection = _FakeConnection()
        shim.install(type(connection))
        connection.send_pipelined_command([_resp("GET", b"a"), _resp("SET", b"b", b"v")])
        shim.uninstall(type(connection))

        export = shim.export()
        assert export["redis_total_commands"] == 2
        assert export["redis_total_round_trips"] == 1
        assert export["redis_commands_per_round_trip"] == 2.0

    def test_command_names_are_parsed_from_resp_not_guessed(self) -> None:
        """The packed buffer is serialised RESP, not a flat name/arg array.

        Reading every second element as a name yields argument bytes - a
        ``ZADD key 1 member`` would be reported as MULTI, ZADD, 1, EXEC.
        """
        shim = RedisCommandShim(enabled=True)
        connection = _FakeConnection()
        shim.install(type(connection))
        connection.send_packed_command(
            [_resp("MULTI"), _resp("ZADD", b"k", b"1", b"member"), _resp("EXEC")]
        )
        shim.uninstall(type(connection))

        names = shim.export()["redis_commands_by_name"]
        assert names == {"MULTI": 1, "ZADD": 1, "EXEC": 1}
        assert "1" not in names and "member" not in names

    def test_unparseable_input_is_reported_not_invented(self) -> None:
        shim = RedisCommandShim(enabled=True)
        connection = _FakeConnection()
        shim.install(type(connection))
        connection.send_packed_command([b"not-resp-at-all"])
        shim.uninstall(type(connection))
        assert "<unparsed>" in shim.export()["redis_commands_by_name"]

    def test_uninstall_restores_every_wrapped_path_exactly(self) -> None:
        """Leaving a patched send_command behind changes every later user."""
        before = {path: getattr(_FakeConnection, path) for path in CANDIDATE_SEND_PATHS}
        shim = RedisCommandShim(enabled=True)
        shim.install(_FakeConnection)
        shim.uninstall(_FakeConnection)
        for path, original in before.items():
            assert getattr(_FakeConnection, path) is original

    def test_absent_send_paths_are_recorded_not_silently_skipped(self) -> None:
        """A path the library lacks must be visible, or it reads as zero traffic."""

        class OnlyTwo:
            def send_command(self, *a, **k) -> str:
                return ""

            def send_packed_command(self, *a, **k) -> str:
                return ""

        shim = RedisCommandShim(enabled=True)
        shim.install(OnlyTwo)
        export = shim.export()
        shim.uninstall(OnlyTwo)

        assert "send_pipelined_command" in export["send_paths_absent_in_this_redis"]

    def test_pipeline_awareness_is_asserted_not_assumed(self) -> None:
        """Equal counts mean either no pipelining or an unwrapped path - both need saying."""
        with pytest.raises(AssertionError, match="identical to the"):
            assert_pipeline_aware(5, 5)

    def test_two_independent_counts_must_agree(self) -> None:
        assert_counts_agree(100, 101)
        with pytest.raises(AssertionError, match="DISAGREE"):
            assert_counts_agree(100, 5000)

    def test_commandstats_totals_are_summed_across_verbs(self) -> None:
        assert commandstats_total({"cmdstat_get": "calls=10,usec=5", "cmdstat_set": "calls=3"}) == 13
        assert commandstats_total({"cmdstat_get": {"calls": 7, "usec": 1}}) == 7
        assert commandstats_total({}) == 0

    def test_shim_cost_is_under_its_ceiling(self) -> None:
        measured = measure_shim_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]


# -- T-7: CPU and RSS series --------------------------------------------------


class TestProcessSeriesSampler:
    def test_series_are_recorded_on_start_and_stop(self) -> None:
        sampler = ProcessSeriesSampler(enabled=True, interval=0.01)
        sampler.start()
        sampler.sample()
        sampler.stop()
        export = sampler.export()
        assert export["sample_count"] >= 1
        assert export["rss_series_bytes"]["count"] >= 1
        assert export["cpu_series_seconds"]["count"] >= 1

    def test_disabled_sampler_records_nothing(self) -> None:
        sampler = ProcessSeriesSampler(enabled=False)
        sampler.start()
        sampler.stop()
        assert sampler.export()["sample_count"] == 0

    def test_series_without_a_recorded_host_is_refused(self) -> None:
        """H-9: the load host and the app host are different machines."""
        sampler = ProcessSeriesSampler(enabled=True)
        sampler.start()
        sampler.stop()
        with pytest.raises(AssertionError, match="host identity"):
            assert_host_recorded(sampler.export())

    def test_series_with_a_recorded_host_is_accepted(self) -> None:
        sampler = ProcessSeriesSampler(enabled=True)
        sampler.record_host_identity("app-host-1")
        sampler.start()
        sampler.stop()
        assert assert_host_recorded(sampler.export())["host_recorded"] is True

    def test_a_short_window_is_refused_not_annotated(self) -> None:
        """perf G14: a 5.81 s window cannot tell a leak from arena growth."""
        assert MINIMUM_WINDOW_SECONDS == 1800
        with pytest.raises(AssertionError, match="minimum"):
            assert_window_long_enough(PACK_WINDOW_SECONDS)
        assert_window_long_enough(1800)

    def test_the_superseded_window_is_recorded_in_the_export(self) -> None:
        """The artefact must show what this series replaced, not just its own numbers."""
        assert ProcessSeriesSampler(enabled=True).export()["pack_window_seconds_superseded"] == 5.81

    def test_tracemalloc_attributes_and_never_concludes(self) -> None:
        sampler = ProcessSeriesSampler(enabled=True)
        sampler.start()
        sampler.sample()
        sampler.stop()
        sampler.record_tracemalloc_peak(1_865_000)
        export = sampler.export()

        assert export["tracemalloc_role"] == "attribution-only"
        assert export["rss_summary_bytes"]["count"] > 0
        assert assert_tracemalloc_is_attribution_only(export) is export

    def test_tracemalloc_alone_is_refused(self) -> None:
        """A tracemalloc peak with no RSS beside it has been promoted to a conclusion."""
        with pytest.raises(AssertionError, match="tracemalloc peak with no RSS"):
            assert_tracemalloc_is_attribution_only(
                {"tracemalloc_peak_bytes": 1_865_000, "rss_summary_bytes": {"count": 0}}
            )

    def test_sampler_cost_is_under_its_ceiling(self) -> None:
        measured = measure_sampler_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]


# -- T-8: the application emits its own configuration ------------------------


class TestAppConfigManifest:
    def test_manifest_is_emitted_from_the_running_configuration(self) -> None:
        """The production-code side: what the process resolved, not what was passed to it."""
        manifest = collect_app_config()
        assert manifest["runtime"]["python_version"]
        assert manifest["runtime"]["cpu_count"]
        assert manifest["stores"]["postgres_host"]
        assert manifest["stores"]["redis_cache_host"]

    def test_every_security_budget_is_recorded_with_its_window(self) -> None:
        """A budget recorded without its window is half a configuration.

        The pack ran at 10000/60 instead of (5,60) with no report disclosing it, and
        the whole Redis-cost programme was shaped by the limiter never being
        exercised.
        """
        budgets = collect_app_config()["limits"]["security_budgets"]
        assert "auth" in budgets
        assert budgets["auth"]["limit"] is not None
        assert budgets["auth"]["window_seconds"] is not None
        assert "default_anonymous" in budgets

    def test_a_complete_manifest_passes(self) -> None:
        assert_manifest_complete(build_app_config_manifest())

    def test_a_missing_field_aborts_rather_than_defaulting(self) -> None:
        """Acceptance criterion (a): no silent defaults for required config."""
        manifest = build_app_config_manifest()
        del manifest["limits"]["security_budgets"]
        with pytest.raises(ManifestIncomplete, match="security_budgets"):
            assert_manifest_complete(manifest)

    def test_every_missing_field_is_named_at_once(self) -> None:
        """One field per re-run turns a two-minute fix into a two-hour one."""
        manifest = build_app_config_manifest()
        del manifest["stores"]["redis_queue_host"]
        del manifest["runtime"]["cpu_count"]
        with pytest.raises(ManifestIncomplete) as caught:
            assert_manifest_complete(manifest)
        message = str(caught.value)
        assert "redis_queue_host" in message and "cpu_count" in message

    def test_an_empty_collection_counts_as_absent(self) -> None:
        manifest = build_app_config_manifest()
        manifest["limits"]["security_budgets"] = {}
        with pytest.raises(ManifestIncomplete):
            assert_manifest_complete(manifest)

    def test_a_credential_in_the_manifest_is_refused(self) -> None:
        with pytest.raises(ManifestIncomplete, match="credential"):
            assert_no_credentials({"stores": {"redis_password": "hunter2"}})

    def test_the_manifest_carries_no_credential_from_the_real_settings(self) -> None:
        assert_no_credentials(collect_app_config())

    def test_addendum_a2_refuses_a_non_production_runtime(self) -> None:
        """A run on asyncio is a different instrument, not a faster version of this one."""
        with pytest.raises(ManifestIncomplete, match="uvloop"):
            assert_runtime_identity(
                _runtime(event_loop_resolved="asyncio", event_loop_module="asyncio.unix_events")
            )

    def test_addendum_a2_refuses_an_h11_runtime(self) -> None:
        """The HTTP half is asserted too. Recording it and forgetting to check it
        is how a run on h11 gets quoted as a run on httptools."""
        with pytest.raises(ManifestIncomplete, match="httptools"):
            assert_runtime_identity(_runtime(http_impl="h11"))

    def test_addendum_a2_accepts_the_production_runtime(self) -> None:
        manifest = _runtime()
        assert assert_runtime_identity(manifest) is manifest

    def test_required_fields_are_resolvable_paths_into_the_manifest(self) -> None:
        """Every required field must actually resolve, or the check is theatre.

        A typo in a dotted path makes ``assert_manifest_complete`` demand a field
        that no manifest can contain, which would refuse every run forever - a
        gate that can never open is indistinguishable from a gate that is never
        checked, so the paths are proved against a real manifest here.
        """
        manifest = build_app_config_manifest()
        for dotted in REQUIRED_FIELDS:
            node = manifest
            for part in dotted.split("."):
                assert isinstance(node, dict) and part in node, (
                    f"required field {dotted!r} does not resolve: {part!r} is absent"
                )
                node = node[part]
