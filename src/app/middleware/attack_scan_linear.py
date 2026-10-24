"""Linear scanner for the four super-linear attack-scan ordering branches.

WHY THIS EXISTS (F-1)
---------------------
``COMBINED_ATTACK_RE`` carries four branches of the form
``\\b<open>\\b .*? \\b<close>\\b`` with ``re.IGNORECASE | re.MULTILINE`` and **no
``re.DOTALL``** (``sql_union``, ``sql_drop``, ``sql_delete``, ``sql_exec``).
With no ``DOTALL`` the ``.*?`` cannot cross ``\\n``, so each branch means
"an <open> token somewhere, then a <close> token later ON THE SAME LINE".
Python's ``re`` rescans to end-of-line from *every* start position, so a body
of ``b"union " * k`` with no ``select`` costs **O(n^2)** -- measured at
~1.33 s for a single 32 KB body and ~14 s for 96 KB (the scanner is reachable
from the query string, the headers and a <=128 KB body, all attacker-chosen).
The ``asyncio`` offload does not help: CPython's ``re`` holds the GIL while
matching, so the work starves the event loop from a worker thread instead.

This module reads the SAME semantics as a single left-to-right pass. The four
branches are an ordering predicate over tokens within a line, decidable in
O(n) with one pending-openers list per branch. It never touches the other
thirteen branches: those are already linear and stay in ``re``.

ISOMORPHISM (the differential gate)
-----------------------------------
The observable of the scanner is ``COMBINED_ATTACK_RE.finditer``'s sequence of
``(lastgroup, start, end)`` -- findings are consumed in that order and the
ban reason uses ``detected_attacks[0]``. Merging the two sources is exact:

  * at any cursor ``c`` the combined regex takes the leftmost match; a match
    either starts at a 4-branch opener or at a 13-branch match;
  * the 4-branch candidate set (this module) contains, for every opener
    position, the identical ``(start, end)`` the regex would take from there
    (first same-line closer; ASCII-only ``re.IGNORECASE``; identical ``\\b``);
  * the 13-branch stream is ``re.finditer`` on the same-but-four-branches-
    removed pattern, so it is the non-overlapping leftmost sequence of the
    13-branch sub-language.

A match the 13-branch ``finditer`` *omitted* because it overlapped an earlier
13-branch match cannot be needed: if that earlier match starts at/after ``c``
the regex would have taken it instead (so the later match is never the
leftmost), and if it starts before ``c`` our cursor already sits at its end,
which is past the later match's start. So the merged greedy reproduces the
regex exactly -- proven by
``tests/unit/middleware/test_attack_scan_linear_differential.py`` over the
30 037-case corpus and per-group minimal probes.

NO COPIES (F-5)
---------------
The token search runs on the RAW bytes with ``re.IGNORECASE`` (ASCII fold),
so it needs neither the ``.lower()`` copy nor the ``.decode("latin-1")`` copy
that the ``pyahocorasick`` prefilter is forced into (its C automaton accepts
``str`` only). That is the ``latin-1`` equivalence argument gone, not moved.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final

#: The four super-linear branches, in the exact alternation order of
#: ``COMBINED_ATTACK_RE`` (a lower index wins a same-start tie). ``open`` and
#: ``close`` are the two literal tokens the branch orders; ``close`` is the
#: NON-capturing end of the match, so it is matched with the same ``\b`` the
#: production pattern uses (``sql_exec`` uses ``\bxp_`` -- no trailing
#: boundary -- and is spelled as such below).
_ORDERING_BRANCHES: Final[tuple[tuple[str, bytes, bytes, bool], ...]] = (
    ("sql_union", b"union", b"select", True),
    ("sql_drop", b"drop", b"table", True),
    ("sql_delete", b"delete", b"from", True),
    ("sql_exec", b"exec", b"xp_", False),
)

QUADRATIC_GROUPS: Final[frozenset[str]] = frozenset(name for name, _, _, _ in _ORDERING_BRANCHES)


def _token_pattern() -> re.Pattern[bytes]:
    """One pass finds every opener and closer token, newline detection aside.

    A single alternation (rather than eight scans) keeps the pass O(n). The
    named groups let the caller dispatch on ``lastgroup`` without re-testing
    each token.
    """
    parts: list[bytes] = []
    for index, (_name, opener, _closer, _trailing_boundary) in enumerate(_ORDERING_BRANCHES):
        parts.append(rb"(?P<open_%d>\b%s\b)" % (index, opener))
    for index, (_name, _opener, closer, trailing_boundary) in enumerate(_ORDERING_BRANCHES):
        if trailing_boundary:
            parts.append(rb"(?P<close_%d>\b%s\b)" % (index, closer))
        else:
            parts.append(rb"(?P<close_%d>\b%s)" % (index, closer))
    return re.compile(b"|".join(parts), re.IGNORECASE)


_TOKEN_RE: Final[re.Pattern[bytes]] = _token_pattern()


def ordering_candidates(content: bytes) -> list[tuple[int, int, str]]:
    """Every 4-branch match, ascending by start, as ``(start, end, group)``.

    For each opener on a line the candidate is the FIRST closer for the same
    pair later on the SAME line (the lazy ``.*?``), which at most one per
    opener keeps the output O(n). A newline between two tokens clears every
    pending opener, which is exactly what ``.`` (without ``DOTALL``) enforces.
    """
    pending: list[list[int]] = [[] for _ in _ORDERING_BRANCHES]
    matches: list[tuple[int, int, str]] = []
    previous_end = 0
    # An opener is remembered by its START only; its END is never needed (the
    # candidate's span runs from the opener's start to the closer's end).
    for token in _TOKEN_RE.finditer(content):
        if content.find(b"\n", previous_end, token.start()) != -1:
            for bucket in pending:
                bucket.clear()
        previous_end = token.end()
        group = token.lastgroup or ""
        kind, _, raw_index = group.partition("_")
        if not raw_index:
            continue
        index = int(raw_index)
        if kind == "open":
            pending[index].append(token.start())
        else:
            end = token.end()
            group_name = _ORDERING_BRANCHES[index][0]
            for start in pending[index]:
                matches.append((start, end, group_name))
            pending[index].clear()
    # Emitted in closer order, so ascending by start already.
    return matches


def _branch_split(pattern: bytes) -> list[bytes]:
    """Split a top-level ``A|B|C`` alternation, respecting parentheses/escapes."""
    branches: list[bytes] = []
    depth = 0
    start = 0
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index : index + 1]
        if char == b"\\":
            index += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
        elif char == b"|" and depth == 0:
            branches.append(pattern[start:index])
            start = index + 1
        index += 1
    branches.append(pattern[start:])
    return branches


_BRANCH_NAME_RE: Final[re.Pattern[bytes]] = re.compile(rb"^\(\?P<([A-Za-z_][A-Za-z0-9_]*)>")


def split_branches(pattern: bytes) -> list[tuple[str, bytes]]:
    """Return ``(group_name, branch_source)`` for each top-level branch.

    Raises ``ValueError`` if a branch is not named -- the caller relies on the
    names for both the 13-branch rebuild and the priority map, so a silent
    unnamed branch would be a correctness hole rather than a style issue.
    """
    out: list[tuple[str, bytes]] = []
    for branch in _branch_split(pattern):
        match = _BRANCH_NAME_RE.match(branch)
        if match is None:
            raise ValueError(f"unnamed attack-scan branch: {branch[:40]!r}")
        out.append((match.group(1).decode("ascii"), branch))
    return out


def build_linear_pattern(full_pattern: bytes) -> bytes:
    """The production pattern with the four super-linear branches removed."""
    branches = split_branches(full_pattern)
    kept = [branch for name, branch in branches if name not in QUADRATIC_GROUPS]
    return b"|".join(kept)


def build_priority_map(full_pattern: bytes) -> dict[str, int]:
    """Group name -> alternation index (lower wins a same-start tie)."""
    return {name: index for index, (name, _branch) in enumerate(split_branches(full_pattern))}


def iter_combined_matches(
    content: bytes,
    linear_re: re.Pattern[bytes],
    priority: dict[str, int],
) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, group)`` in ``COMBINED_ATTACK_RE.finditer`` order.

    The scan is cursor-driven, which is what makes it exact: at every cursor
    the combined regex takes the LEFTMOST match, so the 13-branch side must be
    asked ``search(content, cursor)`` -- the leftmost 13-branch match at/after
    the cursor -- and NOT a precomputed non-overlapping stream. (A precomputed
    stream is wrong precisely when a 4-branch match ends *inside* an earlier
    13-branch match: that 13-branch match is then correctly skipped, but its
    span has already suppressed the LATER 13-branch match the cursor will
    need. The differential corpus caught exactly this.) The 4-branch side is
    the precomputed candidate list, advanced monotonically.

    Exactness: at a cursor ``c`` a branch can match at ``c`` only if its first
    byte is the byte at ``c``, and the four 4-branch first bytes (u/d/e) are
    disjoint from the thirteen 13-branch first bytes, so at most one family
    matches at any position; taking the smaller start (priority on a tie)
    therefore reproduces the regex's leftmost, ordered-alternation choice.
    Cost is O(n): the token pass is one scan, and every successful
    ``search`` covers a region the cursor afterwards advances past (either the
    match it found is emitted, or a 4-branch candidate ending past that match's
    start is), so the regions scanned by successive searches are disjoint and
    sum to at most n. The one ``None`` result is terminal. A long run of
    4-branch candidates is served entirely from the memo, which is what makes
    ``b"union select " * k`` linear instead of one O(n) rescan per candidate.
    """
    candidates = ordering_candidates(content)
    candidates.sort(key=lambda item: (item[0], priority[item[2]]))
    length = len(candidates)
    index = 0
    cursor = 0
    size = len(content)
    # MEMOISED 13-branch side. ``search`` is the expensive call, so its result
    # is carried across loop iterations instead of being recomputed once per
    # 4-branch candidate. Two facts make the reuse exact:
    #
    #   * ``search(content, cursor)`` returns the LEFTMOST match at/after the
    #     cursor, and the cursor only ever advances. A memo whose ``start()``
    #     is still ``>= cursor`` is therefore still that leftmost match -- the
    #     region between the cursor it was found from and its own start is
    #     match-free by construction -- so it is reused as-is.
    #   * once a search returns ``None`` no 13-branch match exists at/after
    #     that cursor, and the cursor never moves back, so no later search can
    #     succeed. ``exhausted`` records that and stops the loop from paying an
    #     O(n) rescan for every remaining candidate -- that rescan is what
    #     made ``b"union select " * k`` quadratic in the previous revision.
    #
    # A memo is re-searched only when the cursor has moved past its start,
    # which happens when a 4-branch candidate or an earlier 13-branch match was
    # chosen and the cursor jumped to its end.
    match_13 = linear_re.search(content, cursor)
    exhausted = match_13 is None
    while True:
        while index < length and candidates[index][0] < cursor:
            index += 1
        candidate_4 = candidates[index] if index < length else None
        if not exhausted:
            assert match_13 is not None  # `exhausted` is the only route to None
            if match_13.start() < cursor:
                match_13 = linear_re.search(content, cursor)
                exhausted = match_13 is None

        chosen: tuple[int, int, str]
        if match_13 is None and candidate_4 is None:
            return
        if match_13 is None:
            assert candidate_4 is not None
            chosen = candidate_4
        elif candidate_4 is None:
            chosen = (match_13.start(), match_13.end(), match_13.lastgroup or "")
        else:
            match_13_tuple = (match_13.start(), match_13.end(), match_13.lastgroup or "")
            if candidate_4[0] < match_13_tuple[0]:
                chosen = candidate_4
            elif match_13_tuple[0] < candidate_4[0]:
                chosen = match_13_tuple
            else:
                chosen = candidate_4 if priority[candidate_4[2]] <= priority[match_13_tuple[2]] else match_13_tuple
        yield chosen

        if chosen[1] > cursor:  # every branch requires >=1 byte; guard progress anyway
            cursor = chosen[1]
        else:
            cursor += 1
        if cursor > size:
            return
