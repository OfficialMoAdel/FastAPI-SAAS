"""Wiring ratchet: the API-key arm is in the Phase-0 set, and both halves agree.

Bead: FastAPI_saas-86p9, filed by M-7. M-7 established the FIRST API-key
baseline but had to stand outside the Phase-0 harness to get it, because
``scripts/step4_campaign.py`` had no way to present an ``X-API-Key``. This file
is that arm's wiring, made permanent: the eleventh entry in the Phase-0 set is
``users_me_apikey`` -- the same handler as ``users_me``, reached through the
credential seam instead of a bearer token -- and the gate refuses to call the
campaign complete without it.

Every assertion here is a claim the two scripts make about EACH OTHER, or
about the live settings object. A Phase-0 set whose campaign and gate disagree
reports PASS while measuring a set that does not exist, so the disagreements
are what this file exists to catch:

* an endpoint added to the campaign but not to ``REQUIRED_ENDPOINTS`` is measured
  and then never gated, so its regressions pass silently;
* an endpoint added to ``REQUIRED_ENDPOINTS`` but not to the campaign can never
  be satisfied, so the gate fails forever on a name nothing produces (EM-9 and
  EM-10 are the two documented exceptions -- they arrive under their own names
  from ``scripts/body_scan_regimes.py``);
* an arm whose ``rate_budget`` resolves to the ANONYMOUS default would be paced
  at 10 per 3600 while the code admits it at 1000 per 3600, which is not a
  budget but a throttling bug hiding in the harness;
* a pinned control that no longer matches the live settings would let a run
  proceed against budgets nobody is enforcing.

The scripts are loaded by path rather than imported as a package: they are
``scripts/`` entry points, not library code, and the load helper below must
register the module in ``sys.modules`` before executing it because
``step4_campaign`` defines dataclasses, which resolve their own module at class
creation time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN_PATH = REPO_ROOT / "scripts" / "step4_campaign.py"
GATE_PATH = REPO_ROOT / "scripts" / "step4_gate.py"

API_KEY_ARM = "users_me_apikey"
# The only names allowed to be in the gate without being a campaign endpoint,
# and why each exists. A new gate-only name has to be written down here before
# this file will pass, which is the point: an unexplained gate-only name is a
# criterion no artefact can satisfy.
GATE_ONLY_ENDPOINTS = {
    "em9_miss_60kb": "produced by scripts/body_scan_regimes.py, not by the campaign",
    "em10_hit_40kb": "produced by scripts/body_scan_regimes.py, not by the campaign",
}


def load(name: str, path: Path) -> ModuleType:
    """Execute ``path`` as ``name``.

    ``sys.modules[name] = mod`` before ``exec_module`` is mandatory, not
    incidental: ``dataclasses._is_type`` looks the defining module up in
    ``sys.modules``, and without the entry every dataclass in
    ``step4_campaign`` raises ``AttributeError: 'NoneType' object has no
    attribute '__dict__'`` at import time.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def campaign_module() -> ModuleType:
    return load("m7_step4_campaign_probe", CAMPAIGN_PATH)


def gate_module() -> ModuleType:
    return load("m7_step4_gate_probe", GATE_PATH)


def arm() -> Any:
    matches = [ep for ep in campaign_module().ENDPOINTS if ep.name == API_KEY_ARM]
    assert len(matches) == 1, f"expected exactly one {API_KEY_ARM} endpoint, found {len(matches)}"
    return matches[0]


class TestTheGateRequiresTheArm:
    """The gate is what makes the arm part of the Phase-0 SET rather than a script."""

    def test_the_api_key_arm_is_a_required_endpoint(self) -> None:
        assert API_KEY_ARM in gate_module().REQUIRED_ENDPOINTS

    def test_no_campaign_endpoint_is_left_ungated(self) -> None:
        # An endpoint the campaign measures but the gate does not require can
        # regress freely: nothing in the gate reads its artefact.
        gated = set(gate_module().REQUIRED_ENDPOINTS)
        ungated = sorted(ep.name for ep in campaign_module().ENDPOINTS if ep.name not in gated)
        assert ungated == [], f"campaign endpoints missing from REQUIRED_ENDPOINTS: {ungated}"

    def test_every_gate_only_name_has_a_written_reason(self) -> None:
        # The converse rot: a name in the gate that no endpoint produces makes
        # the criterion unsatisfiable, and an UNEXPLAINED one means somebody
        # added a requirement without adding the thing that satisfies it.
        produced = {ep.name for ep in campaign_module().ENDPOINTS}
        gate_only = set(gate_module().REQUIRED_ENDPOINTS) - produced
        assert gate_only == set(GATE_ONLY_ENDPOINTS), (
            f"gate-only endpoints changed: {sorted(gate_only)}; every one needs a reason added to GATE_ONLY_ENDPOINTS"
        )

    def test_criterion_1_does_not_spell_out_the_endpoint_count(self) -> None:
        # C1 used to read "ten endpoints measured at the degrading concurrency".
        # The eleventh arm made that a lie the gate printed in its own output,
        # so the count now lives in REQUIRED_ENDPOINTS and the name derives from
        # the tuple. This ratchets against the number being reintroduced here,
        # where adding a future arm would silently make it wrong again.
        name = gate_module().C1.lower()
        assert "ten" not in name
        assert "10 endpoints" not in name


class TestTheArmIsAnApiKeyArm:
    """Shape, so a rename or a copied auth kind cannot quietly un-measure it."""

    def test_the_arm_presents_an_api_key(self) -> None:
        assert arm().auth == "api_key"

    def test_the_arm_targets_the_same_handler_as_users_me(self) -> None:
        # The point of the arm is the credential seam: same path, different
        # credential. A different path would make it a different endpoint and
        # the delta against users_me meaningless.
        users_me = next(ep for ep in campaign_module().ENDPOINTS if ep.name == "users_me")
        assert (arm().method, arm().path) == (users_me.method, users_me.path)

    def test_the_arm_expects_only_success(self) -> None:
        assert set(arm().expected_statuses) == {200}

    def test_the_arm_names_a_source_ip_pool_and_it_is_distinct(self) -> None:
        # The bucket g4my introduced is keyed by source IP
        # (rate_limit:api_key:ip:<ip>:<path>), so a phase driven from ONE address
        # would exhaust API_KEY_LIMIT and every later repetition would measure
        # 429s. The pool is rotated through X-Forwarded-For, which
        # is_trusted_proxy honours for a loopback peer, so the addresses are
        # real to the limiter rather than cosmetic.
        pool = campaign_module().API_KEY_SOURCE_IPS
        assert pool, "the API-key arm has no source-IP pool to rotate through"
        assert len(set(pool)) == len(pool), f"source-IP pool repeats an address: {pool}"


class TestTheArmIsPacedAgainstTheBudgetItIsActuallyAdmittedAt:
    """The harness paces, so a wrong budget is throttling wearing a harness's clothes.

    ``rate_budget`` names a control the pacer reads rather than a relaxation of
    one. If the api-key selector resolved to the ANONYMOUS default the phase
    would be paced at 10 requests per 3600 while admission grants 1000 per 3600,
    and the reported latency would be the pacer's, not the endpoint's -- with
    nothing in the artefact saying so. The second test pins the pinned value to
    the live settings object, because a hard-coded expectation that drifts from
    ``settings.rate_limit.API_KEY_LIMIT`` lets a run proceed against a budget
    nobody is enforcing.
    """

    def test_the_api_key_budget_resolves_to_the_api_key_control(self) -> None:
        module = campaign_module()
        budgets, names = module._resolve_rate_budget(("api_key",), module.EXPECTED_EIGHT)
        assert names == ["api_key"]
        assert budgets == [(1000, 3600)]

    def test_the_api_key_budget_is_not_the_anonymous_default(self) -> None:
        # 100x lower: the defect M-7 measured before g4my existed. If this ever
        # goes equal the arm is pacing at the anonymous default again and the
        # latency it publishes is the pacer's.
        module = campaign_module()
        assert module.EXPECTED_EIGHT["api_key_limit"] != module.EXPECTED_EIGHT["default_anonymous_limit"]

    def test_the_pinned_control_matches_the_live_settings(self) -> None:
        from src.app.core.config import settings

        module = campaign_module()
        assert module.EXPECTED_EIGHT["api_key_limit"] == list(settings.rate_limit.API_KEY_LIMIT)

    def test_the_arm_names_the_api_key_budget(self) -> None:
        # rate_budget=() would mean "no pacing", which for an IP-keyed bucket is
        # a run that measures its own limiter rejections as endpoint latency.
        assert arm().rate_budget == ("api_key",)
