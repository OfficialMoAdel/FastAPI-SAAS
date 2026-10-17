"""Perf account naming: ONE definition, so the seeder and the load generator cannot drift.

The defect this exists to prevent
--------------------------------
``setup_locust_test_data.py`` created each account with
``username = email.split("@")[0]``, while ``locustfile.py`` authenticated with
``{"username": self.email}``. Every login therefore looked up a ``username`` that
did not exist and returned 403 - and it could not be fixed by storing the email
as the username either, because ``users.username`` is ``varchar(20)`` and a
generated address like ``perf_user_0@example.com`` is 23 characters.

So the two halves of the harness could not authenticate the accounts the other
half created. Both now derive the username through :func:`perf_username`, and a
test asserts the seeder's SQL parameter matches what the locustfile sends.

Why a helper rather than a comment
----------------------------------
The two files are already coupled through the database and through the account
pool. A comment saying "keep these in step" is a coupling with no enforcement; a
function they both call is the coupling the compiler and the test suite can see.
"""

from __future__ import annotations

from collections.abc import Iterable

#: ``users.username`` is ``varchar(20)``. The local part of a generated address
#: is well inside that, which a full email is not - so the local part is the only
#: value that can serve as both the stored username and the login identifier.
USERNAME_MAX_LENGTH = 20

#: The Pro tier the seeder assigns, and the row it therefore requires to exist.
#: ``create_tables()`` creates the TABLE but never the ROW, so a seeded account
#: fails its foreign key with nothing in the log to point at the missing tier.
PERF_TIER_ID = 2
PERF_TIER_NAME = "premium"

#: Tiers created before seeding, lowest id first. The anonymous/fallback tier is
#: present because other suites and the app's own defaults expect a tier table
#: with at least one row in it.
PERF_TIERS: tuple[tuple[int, str], ...] = ((1, "free"), (PERF_TIER_ID, PERF_TIER_NAME))


def perf_username(email: str) -> str:
    """The username a perf account is stored and authenticated under.

    The local part of the address, truncated to the column width. Truncation is
    explicit rather than assumed: an address whose local part exceeded 20
    characters would silently collide with another account, and two load users
    sharing one account is the failure mode the locustfile exists to avoid.
    """
    local = email.split("@", 1)[0]
    return local[:USERNAME_MAX_LENGTH]


def collision_risk(emails: Iterable[str]) -> list[str]:
    """Addresses in a pool whose derived usernames would collide.

    Reported rather than tolerated: a collision means two virtual users share an
    account, which silently changes the session behaviour being measured because
    logging in as the same account evicts the earlier session.
    """
    seen: dict[str, str] = {}
    clashes: set[str] = set()
    for email in emails:
        username = perf_username(str(email))
        if username in seen and seen[username] != str(email):
            clashes.add(username)
        seen[username] = str(email)
    return sorted(clashes)
