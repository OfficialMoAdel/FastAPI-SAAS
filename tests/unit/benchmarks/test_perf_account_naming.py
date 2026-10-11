"""The perf harness must be able to authenticate the accounts it seeds.

A regression guard, not a new capability. The two halves of the harness were
silently incompatible: the seeder stored ``username = email.split("@")[0]`` while
the load file posted ``{"username": self.email}``, and ``users.username`` is
``varchar(20)`` so a generated address could not be stored as the username either.
Every login returned 403 and the run was voided - with a message that pointed at
authentication rather than at the mismatch.

These tests therefore assert a PROPERTY ("the identifier the load file sends is
the one the seeder stores") rather than restating either implementation, so they
keep holding if either side is rewritten.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from src.benchmarks.perf_accounts import (
    PERF_TIER_ID,
    PERF_TIERS,
    USERNAME_MAX_LENGTH,
    collision_risk,
    perf_username,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDER = REPO_ROOT / "scripts" / "setup_locust_test_data.py"
LOCUSTFILE = REPO_ROOT / "tests" / "performance" / "locustfile.py"


class TestUsernameFitsTheColumn:
    def test_a_derived_username_fits_varchar_20(self) -> None:
        """The column is the constraint that made this a bug rather than a typo."""
        assert len(perf_username("perf_user_0@example.com")) <= USERNAME_MAX_LENGTH

    def test_a_full_email_never_would_have(self) -> None:
        """Pin WHY the email cannot be the username, so nobody tries it again."""
        assert len("perf_user_0@example.com") > USERNAME_MAX_LENGTH


class TestBothHalvesAgree:
    def test_the_seeder_stores_the_derived_username(self) -> None:
        source = SEEDER.read_text(encoding="utf-8")
        assert '"username": perf_username(email)' in source, (
            "the seeder must store perf_username(email); if it goes back to "
            "email.split('@')[0] the two halves can drift apart again"
        )

    def test_the_load_file_sends_the_derived_username(self) -> None:
        source = LOCUSTFILE.read_text(encoding="utf-8")
        assert '"username": perf_username(self.email)' in source, (
            "the load file must authenticate with perf_username(self.email); "
            "sending the raw email looks up a username that is never stored"
        )

    def test_the_load_file_does_not_send_the_raw_email_as_username(self) -> None:
        source = LOCUSTFILE.read_text(encoding="utf-8")
        assert '"username": self.email' not in source


class TestCollisionsAreSurfaced:
    def test_distinct_local_parts_do_not_collide(self) -> None:
        assert collision_risk(["pu0@e.io", "pu1@e.io", "perf_user_0@x.io"]) == []

    def test_two_addresses_with_one_local_part_are_reported(self) -> None:
        """Two virtual users sharing an account evict each other's sessions, and
        nothing else in the run would reveal it."""
        assert collision_risk(["pu0@a.io", "pu0@b.io"]) == ["pu0"]

    def test_the_same_address_twice_is_not_a_collision(self) -> None:
        assert collision_risk(["pu0@a.io", "pu0@a.io"]) == []


class TestTierRowsExist:
    def test_the_seeder_creates_the_tiers_its_accounts_reference(self) -> None:
        """Without a tier row, seeding fails its foreign key and the message
        points at the seeder rather than at the missing row."""
        source = SEEDER.read_text(encoding="utf-8")
        assert "_ensure_tiers" in source
        assert "INSERT INTO tier" in source

    def test_the_pro_tier_id_matches_what_accounts_are_given(self) -> None:
        assert PERF_TIER_ID in {tier_id for tier_id, _ in PERF_TIERS}
        assert PERF_TIER_ID == 2

    def test_a_free_tier_exists_alongside_it(self) -> None:
        """A NULL tier falls through to the anonymous budget, which a load test
        exhausts in seconds - so an un-tiered run voids on 429s instead of measuring."""
        assert any(name == "free" for _, name in PERF_TIERS)


class TestTheHarnessCanReachItsOwnScripts:
    def test_the_perf_scripts_survive_the_dockerignore(self) -> None:
        """They did not: `scripts` was excluded wholesale, so the image could
        not run the measurement it was built for, and seeding inside the
        container needed a docker cp and a reconstructed src/ layout."""
        ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        assert "scripts/*" in ignored, "scripts is excluded wholesale again"
        assert "!scripts/run_perf_baseline.sh" in ignored
        assert "!scripts/setup_locust_test_data.py" in ignored

    def test_the_seeder_is_still_importable_from_src_layout(self) -> None:
        """It inserts src/ on sys.path and imports `app.*`; the tier and username
        helpers come from the same tree under `benchmarks.*`."""
        tree = ast.parse(SEEDER.read_text(encoding="utf-8"))
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "benchmarks.perf_accounts" in modules, (
            f"the seeder no longer imports the shared username/tier helpers; "
            f"it imports {sorted(modules)}"
        )


def test_the_locustfile_imports_the_same_helper_the_seeder_does() -> None:
    """One definition, two callers. A comment is a coupling with no enforcement."""
    tree = ast.parse(LOCUSTFILE.read_text(encoding="utf-8"))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.benchmarks.perf_accounts" in modules
    assert re.search(r"perf_username\(", LOCUSTFILE.read_text(encoding="utf-8"))
