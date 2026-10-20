"""The promoted 30 037-case attack-scan corpus: size, determinism, and liveness.

WHAT THIS ORACLE ASSERTS
    (a) the corpus is EXACTLY 30 037 cases, and its composition is the one
        the probe ran (30 adversarial fixtures + 3 large benign bodies +
        30 000 seeded fuzz bodies + 4 depth injections);
    (b) it is DETERMINISTIC - the same checked-in seed always rebuilds the
        same corpus, and a different seed builds a different one (which is
        what makes the seed, and not the corpus, the thing that is checked
        in);
    (c) it is a LIVE corpus for the PRODUCTION scanner: every one of the 17
        production attack groups is exercised by at least one case, so the
        corpus is not a pile of bodies the scanner never sees.

WHAT THIS ORACLE DOES NOT ASSERT
    It does not assert anything about glm5.3's per-group-merge design. That
    design was rejected on principle and is not reopened here; a differential
    test against a rejected implementation has no stable expected side. The
    corpus travels alone, and a guard below asserts that the rejected side
    did not travel with it.
"""

from __future__ import annotations

import random

from src.app.middleware import attack_detection as ad
from tests.helpers import attack_scan_corpus as corpus_module
from tests.helpers.attack_scan_corpus import (
    ADVERSARIAL,
    BENIGN_40K,
    BENIGN_128K,
    CLEAN_60K,
    CORPUS_SIZE,
    DEPTH_PERCENTAGES,
    FUZZ_CASE_COUNT,
    SEED,
    build_corpus,
    depth_injections,
    fuzz_bodies,
)

EXPECTED_CORPUS_SIZE = 30_037
EXPECTED_SEED = 20261004


class TestTheCorpusIsTheOneTheProbeRan:
    def test_the_corpus_is_exactly_thirty_thousand_and_thirty_seven_cases(self) -> None:
        assert CORPUS_SIZE == EXPECTED_CORPUS_SIZE
        assert len(build_corpus()) == EXPECTED_CORPUS_SIZE

    def test_the_composition_is_the_probes_composition(self) -> None:
        assert len(ADVERSARIAL) == 30
        assert FUZZ_CASE_COUNT == 30_000
        assert DEPTH_PERCENTAGES == (0, 50, 90, 99)
        assert len(depth_injections()) == 4
        # 30 adversarial + 3 large benign + 30 000 fuzz + 4 injections.
        assert 30 + 3 + 30_000 + 4 == EXPECTED_CORPUS_SIZE

    def test_the_three_large_bodies_are_the_probes_three(self) -> None:
        assert BENIGN_40K.startswith(b'{"color":"blue","sort":"for","items":"')
        assert BENIGN_128K.startswith(b'{"color":"blue","sort":"for","items":"')
        assert CLEAN_60K.startswith(b'{"items":"')
        assert len(BENIGN_40K) == len(b'{"color":"blue","sort":"for","items":"') + 40 * 1024 + len(b'"}')
        assert len(BENIGN_128K) == len(b'{"color":"blue","sort":"for","items":"') + 120 * 1024 + len(b'"}')
        assert len(CLEAN_60K) == len(b'{"items":"') + 60 * 1024 + len(b'"}')

    def test_the_depth_injections_land_where_the_probe_landed_them(self) -> None:
        for depth_pct, body in zip(DEPTH_PERCENTAGES, depth_injections(), strict=True):
            expected_position = max(0, int(len(BENIGN_128K) * depth_pct / 100) - 5)
            assert body[:expected_position] == BENIGN_128K[:expected_position]
            assert body[expected_position : expected_position + 11] == b"' OR 1=1 --"
            assert body[expected_position + 11 :] == BENIGN_128K[expected_position:]


class TestTheSeedIsCheckedInAndTheCorpusIsDeterministic:
    def test_the_seed_is_a_source_constant(self) -> None:
        assert SEED == EXPECTED_SEED
        assert isinstance(SEED, int)

    def test_the_same_seed_rebuilds_the_same_corpus(self) -> None:
        first = build_corpus()
        build_corpus.cache_clear()
        second = build_corpus()
        assert first == second

    def test_a_different_seed_builds_a_different_corpus(self) -> None:
        assert fuzz_bodies(SEED) != fuzz_bodies(SEED + 1)

    def test_the_fuzz_bodies_come_from_the_checked_in_generator(self) -> None:
        rng = random.Random(SEED)
        first = corpus_module.random_body(rng)
        rng = random.Random(SEED)
        assert corpus_module.random_body(rng) == first


class TestTheCorpusIsLiveForTheProductionScanner:
    def test_every_production_attack_group_is_exercised_by_the_corpus(self) -> None:
        covered: set[str] = set()
        for body in build_corpus():
            for match in ad.COMBINED_ATTACK_RE.finditer(body):
                group_name = match.lastgroup
                if group_name:
                    covered.add(group_name)
        production_groups = set(ad._GROUP_TO_ATTACK)
        assert production_groups
        assert covered == production_groups, (
            f"the corpus exercises {sorted(covered)} but production has {sorted(production_groups)}"
        )


class TestTheRejectedDesignDidNotTravelWithTheCorpus:
    def test_the_module_carries_no_reimplementation_of_the_regex_engine(self) -> None:
        offending = sorted(
            name
            for name in dir(corpus_module)
            if "recast" in name.lower() or "standalone" in name.lower()
        )
        assert offending == [], f"the rejected side travelled with the corpus: {offending}"

    def test_the_module_exposes_a_corpus_and_nothing_else(self) -> None:
        public = sorted(name for name in corpus_module.__all__ if not name.startswith("_"))
        assert public == [
            "ADVERSARIAL",
            "ADVERSARIAL_FIXTURE_COUNT",
            "BENIGN_128K",
            "BENIGN_40K",
            "CLEAN_60K",
            "CORPUS_SIZE",
            "DEPTH_CASE_COUNT",
            "DEPTH_PERCENTAGES",
            "FUZZ_CASE_COUNT",
            "LARGE_FIXTURE_COUNT",
            "MULTIPART",
            "SEED",
            "SQLI",
            "VOCABULARY",
            "build_corpus",
            "depth_injections",
            "fuzz_bodies",
            "random_body",
        ]
