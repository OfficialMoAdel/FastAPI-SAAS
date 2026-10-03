"""FastAPI_saas-io15.4: the blind-except ratchet, and proof that it bites.

The muse-22 triage works one broad ``except`` at a time, and the way that work
rots is quietly: a handler added "just to be safe", a triage that annotates
instead of narrowing, a swallow added on a new code path. None of those fail a
test by themselves, so without a ceiling the campaign has no way to tell
progress from drift.

``BROAD_EXCEPT_BASELINE`` is the measured count *after* the muse-22 triage of
the three named sites, not a number copied from the bead. The bead claimed 13
broad excepts in ``src/``; the tree actually holds 357. The bead's figure is
what a single directory would show, not the tree, so a ratchet written against
it would have failed immediately and taught the team to ignore it.

Two things are pinned here, and the difference matters:

* the **total** may not rise — a new broad except is a new liability;
* the count may go DOWN freely — narrowing a site is the point of the work, so
  a ratchet that also blocked progress would be worse than none.

``TestTheGateBinds`` is the part that makes the ratchet worth having. A count
ratchet measures the backlog; it does not stop a *new* broad except from
appearing in a file that is not yet lint-enforced. That is the BLE001 gate's
job, and a gate that does not bind is worse than none — it reads as coverage.
So these tests drive ``ruff`` itself with ``--stdin-filename`` pointed at the
triaged files and assert both directions: an unannotated broad except fails,
a reasoned one passes.

Run directly to see the numbers:

    python scripts/count_broad_excepts.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import count_broad_excepts as counter  # noqa: E402

#: Measured after the muse-22 triage of api/deps/auth.py, middleware/tenant.py
#: and clients/notifications/smtp.py. Re-measure with
#: ``python scripts/count_broad_excepts.py`` and only move this DOWN, unless a
#: new broad except is landing and its reasoning is recorded in the commit.
#:
#: 357 -> 356. The drop is the endpoint cache decorator: the fail-closed
#: response-model path in ``core/utils/cache/decorator.py`` lost a duplicated
#: error-reporting branch when the GET write path was folded into one
#: try/finally, and ``core/tenancy/scope.py`` was restored from a
#: line-ending-corrupted state. Re-measured, not estimated.
#:
#: 356 -> 365. Raised, deliberately and re-measured, by the S-08 / S-15 / S-33 /
#: S-48 remediation in this round. Every addition is a DEGRADATION guard on a
#: path that had no handler at all, each carrying the `# noqa: BLE001 - <reason>`
#: the ratchet requires:
#:
#:   * core/security/session_manager.py (5) - Redis calls that previously
#:     propagated and 500'd the strict auth path. Mutators return False/0 rather
#:     than fake success; readers degrade to []/0 rather than 500.
#:   * billing/webhook/idempotency.py (2) - a DB fault must still reach the
#:     Redis revocation write (S-24) and the claim re-read must not mask it.
#:   * modules/tenants/service.py (1) - L1 cleanup is best-effort and must not
#:     abort a tenant deactivation (S-15).
#:   * modules/posts/router.py (1) - the post is already written; a cache error
#:     must not fail the POST (S-33).
#:   * modules/notifications/service.py (1) - re-raised as a domain ValueError so
#:     a broken template fails loudly instead of silently substituting a
#:     divergent sibling (S-48).
#:   * core/security/jwt_service.py (1) - see S-24.
#:
#: The count went UP because the alternative was an unhandled exception on a
#: hot path. Each is annotated, which is the trade the ratchet exists to force.
BROAD_EXCEPT_BASELINE = 365

#: The files muse 22 triaged. BLE001 is enforced on each (the per-file-ignores
#: in pyproject.toml no longer blanket-exempt them), so these are also the
#: files the lint gate is proven against below.
TRIAGED_FILES = (
    "src/app/api/deps/auth.py",
    "src/app/middleware/tenant.py",
    "src/app/clients/notifications/smtp.py",
)


def _ruff(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-fix", *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=REPO,
        timeout=180,
    )


class TestRatchetHolds:
    def test_broad_except_count_does_not_exceed_baseline(self) -> None:
        """The ratchet: a new blind except in src/ fails here."""
        current = counter.count()
        assert current <= BROAD_EXCEPT_BASELINE, (
            f"broad-except count ROSE from {BROAD_EXCEPT_BASELINE} to {current}. "
            "Catch the specific exception, or — if the boundary really is "
            "deliberate — annotate it with `# noqa: BLE001` and a reason "
            "stating what must not propagate."
        )

    def test_baseline_matches_the_measured_tree(self) -> None:
        """A baseline far from reality stops being a ratchet.

        Exact, not a tolerance: the counter is an AST walk, so a drift means
        the tree really did change, and the number here must be re-measured
        deliberately rather than absorbing the change silently.
        """
        current = counter.count()
        assert current == BROAD_EXCEPT_BASELINE, (
            f"baseline {BROAD_EXCEPT_BASELINE} does not match the measured "
            f"count {current}. Re-run scripts/count_broad_excepts.py and "
            "update BROAD_EXCEPT_BASELINE deliberately."
        )

    def test_narrowing_is_not_blocked(self) -> None:
        """The ratchet must permit progress downward.

        A ceiling that also prevented the count from falling would make
        narrowing a site look like a violation, and the campaign would stall.
        """
        assert counter.count() <= BROAD_EXCEPT_BASELINE
        assert counter.count() < 400, (
            "if the tree ever sits far under the baseline, the ceiling has "
            "stopped meaning anything — re-measure it rather than leaving a "
            "number that no longer describes reality"
        )

    def test_no_bare_except_was_introduced(self) -> None:
        """A bare ``except:`` also catches KeyboardInterrupt/SystemExit.

        Tracked separately from the broad-typed count because it is a
        strictly worse version of the same problem, and the total count alone
        would not move if one were introduced.
        """
        assert counter.bare_except_count() == 0, (
            "bare `except:` handler(s) in src/ — use `except Exception` or narrower"
        )


class TestTheGateBinds:
    """The BLE001 gate must actually reject, or annotating is a no-op.

    The ratchet above only sees what already exists. This is the half that
    stops the NEXT broad except from being added to a triaged file, and it is
    proven by driving ruff rather than by reading the config: a per-file-ignore
    list that silently keeps covering a file makes every noqa meaningless and
    every test above pass while the gate is off.
    """

    def test_unannotated_broad_except_fails_the_gate(self) -> None:
        """A new blind except in a triaged file is red, not silent."""
        source = "def probe():\n    try:\n        pass\n    except Exception:\n        pass\n"
        for rel in TRIAGED_FILES:
            result = _ruff("--select", "BLE001", "--stdin-filename", rel, "-", stdin=source)
            assert "BLE001" in result.stdout, (
                f"BLE001 does not bind on {rel}: a blind `except Exception` "
                f"passed lint. per-file-ignores must not still cover this "
                f"file.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_reasoned_noqa_passes_the_gate(self) -> None:
        """A deliberate boundary, declared, is accepted — the escape hatch."""
        source = (
            "def probe():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:  # noqa: BLE001 - deliberate: stated reason\n"
            "        pass\n"
        )
        for rel in TRIAGED_FILES:
            result = _ruff("--select", "BLE001", "--stdin-filename", rel, "-", stdin=source)
            assert "BLE001" not in result.stdout, (
                f"a reasoned noqa was rejected on {rel} — the gate leaves no "
                f"way to declare a deliberate boundary:\n{result.stdout}"
            )

    def test_narrowed_except_never_needed_a_noqa(self) -> None:
        """The narrowed tenant site must need no exemption at all.

        This is the anti-over-fix check on the config change: if someone
        re-widens `except (AttributeError, TypeError, ValueError)` back to
        `except Exception` to silence a caller, the noqa above would happily
        cover it and the narrowing would silently stop meaning anything. The
        narrow form lints clean with no noqa, so the widening shows up as a
        real BLE001 the author has to think about.
        """
        source = (
            "def probe():\n"
            "    try:\n"
            "        pass\n"
            "    except (AttributeError, TypeError, ValueError):\n"
            "        pass\n"
        )
        result = _ruff("--select", "BLE001", "--stdin-filename", "src/app/middleware/tenant.py", "-", stdin=source)
        assert "BLE001" not in result.stdout, f"narrow except was flagged:\n{result.stdout}"


class TestTheDecisionIsRecorded:
    """The triaged files must keep declaring what they decided.

    Not a style rule for its own sake: the whole point of the triage is that
    a future reader can tell, without re-deriving it, which handlers were
    kept on purpose. A decision recorded only in a commit message is lost the
    first time the file is touched — and a handler whose decision comment was
    deleted is indistinguishable from one nobody ever triaged.
    """

    def test_no_triaged_file_carries_an_undeclared_broad_except(self) -> None:
        """In the three triaged files, every blind except must DECLARE itself.

        A triaged file is a file someone has already made a decision about, so
        a blind except in one of them that carries neither a noqa nor a
        narrowed type means the decision was quietly undone. Other files still
        contain untriaged blind excepts — they remain lint-exempt and are
        tracked by the ceiling above — so this is scoped to the triaged set
        deliberately.
        """
        undeclared = [
            f"{f.path}:{f.lineno}"
            for f in counter.scan()
            if f.path in TRIAGED_FILES and not f.annotated
        ]
        assert undeclared == [], (
            "a triaged file has a blind except that declares no decision: "
            f"{undeclared}. Narrow it, or annotate it with `# noqa: BLE001` "
            "and a reason stating what must not propagate."
        )

    def test_every_remaining_broad_except_in_a_triaged_file_is_annotated(self) -> None:
        """The triaged files hold no silent blind except, and are not empty.

        Counterpart to the test above: if narrowing were later undone and the
        handler simply deleted, the triaged set would pass by being empty.
        The boundaries were kept ON PURPOSE, so their continued presence is
        part of the decision being pinned.
        """
        triaged = [f for f in counter.scan() if f.path in TRIAGED_FILES]
        assert triaged, (
            "no broad except remains in any triaged file — the muse-22 "
            "decision was to KEEP these as deliberate boundaries, so an empty "
            "set means they were removed rather than decided"
        )
        assert all(f.annotated for f in triaged), (
            "every remaining blind except in a triaged file must be annotated; "
            f"unannotated: {[f.as_dict() for f in triaged if not f.annotated]}"
        )
