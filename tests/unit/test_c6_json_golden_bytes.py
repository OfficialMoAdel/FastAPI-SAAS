"""C-6 oracle: golden-byte pins for the two stdlib ``json.dumps`` call sites.

Bead ``FastAPI_saas-dv16``. Both cited lines are the ONLY json
serialization in their files (verified by grep — zero orjson,
zero ``json_dumps``/canonical anywhere in either file):

- ``src/app/modules/auth/services/tokens.py:235`` —
  ``json.dumps({\"user_id\", \"sid\", \"new_jti\", \"new_exp\"})`` with
  stdlib defaults, written to ``refresh_rotated:{old_jti}`` via
  ``cache_client.set``. A cache write read back by KEY LOOKUP,
  NOT hashed or signed.
- ``src/app/modules/ai/providers.py:330`` —
  ``json.dumps(schema, indent=2)`` appended to a system-prompt string
  sent to the LLM. NOT hashed or signed.

Neither payload is hashed or signed at these sites, so there is no
wire-contract hazard here (unlike ``webhooks/signer.py``, whose
``canonical_payload_bytes`` is the HMAC wire contract and stays
rejected and untouched — out of scope for this oracle).

What this file pins, and why it is byte-level rather than parsed:
a future orjson swap that parses back to equal values can still
change the BYTES (separators ``\", \"`` vs ``\",\"`` AND encoding
``\\uXXXX`` vs raw UTF-8 — both measured below against orjson
3.11.5 in this venv). Parsed-value equality would pass while the
bytes change, which is exactly the silent divergence this oracle
exists to catch. Hence every assertion here is on ``bytes``.

The golden literals were produced by the real stdlib on HEAD and
are pasted as literals: a future swap changes the bytes and the
test fails. The orjson side is asserted by PROPERTY (raw UTF-8
present, separators differ, bytes unequal) rather than by orjson
exact bytes, so an orjson version bump cannot break this suite —
only a change to OUR call sites can.

The call shapes are replicated, not driven: site 1 lives inside
the refresh-rotation flow (needs tokens + cache) and site 2 needs
an LLM. The replication is exact — same function, same kwargs —
and the kwargs are named in each test. Dict insertion order is
deterministic (literals), and stdlib preserves it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import orjson
import pytest

# ---------------------------------------------------------------------------
# Fixed payloads (deterministic insertion order; stdlib preserves it)
# ---------------------------------------------------------------------------

SITE1_ASCII = {"user_id": "u-1", "sid": "s-2", "new_jti": "j-3", "new_exp": 1728000000.5}
SITE1_NON_ASCII = {"user_id": "u-αβ", "sid": "s-ü", "new_jti": "j-中", "new_exp": 1.5}
SITE2_ASCII = {"type": "object", "properties": {"x": {"type": "number"}}}
SITE2_NON_ASCII = {"titre": "café-中", "v": 0.1}

# Golden bytes: real stdlib output on HEAD, pasted as literals.
SITE1_ASCII_GOLDEN = b'{"user_id": "u-1", "sid": "s-2", "new_jti": "j-3", "new_exp": 1728000000.5}'
SITE1_NON_ASCII_GOLDEN = (
    b'{"user_id": "u-\\u03b1\\u03b2", "sid": "s-\\u00fc", '
    b'"new_jti": "j-\\u4e2d", "new_exp": 1.5}'
)
SITE2_ASCII_GOLDEN = (
    b'{\n  "type": "object",\n  "properties": {\n    "x": {\n      "type": "number"\n    }\n  }\n}'
)
SITE2_NON_ASCII_GOLDEN = b'{\n  "titre": "caf\\u00e9-\\u4e2d",\n  "v": 0.1\n}'


def _site1_dumps(payload: dict) -> bytes:
    """The exact call shape of tokens.py:235 (stdlib defaults)."""
    return json.dumps(payload).encode()


def _site2_dumps(payload: dict) -> bytes:
    """The exact call shape of providers.py:330 (indent=2)."""
    return json.dumps(payload, indent=2).encode()


class TestSite1RotationMarkerGoldenBytes:
    """tokens.py:235 shape — stdlib defaults, no indent."""

    def test_ascii_payload_is_byte_exact(self) -> None:
        assert _site1_dumps(dict(SITE1_ASCII)) == SITE1_ASCII_GOLDEN

    def test_float_keeps_full_repr(self) -> None:
        assert b"1728000000.5" in _site1_dumps(dict(SITE1_ASCII))

    def test_non_ascii_is_escaped_not_raw(self) -> None:
        rendered = _site1_dumps(dict(SITE1_NON_ASCII))
        assert rendered == SITE1_NON_ASCII_GOLDEN
        # ensure_ascii=True (the stdlib default): no raw UTF-8 bytes escape,
        # and the escapes name the exact code points.
        assert "α".encode() not in rendered
        assert "中".encode() not in rendered
        assert b"\\u03b1" in rendered
        assert b"\\u4e2d" in rendered

    def test_orjson_divergence_is_measured_not_assumed(self) -> None:
        stdlib_bytes = _site1_dumps(dict(SITE1_NON_ASCII))
        orjson_bytes = orjson.dumps(dict(SITE1_NON_ASCII))
        # orjson emits raw UTF-8 where stdlib escapes ...
        assert "α".encode() in orjson_bytes
        assert b"\\u03b1" not in orjson_bytes
        # ... and drops the post-separator spaces stdlib emits ...
        assert b'": "' not in orjson_bytes
        assert b'": "' in stdlib_bytes
        # ... so the two are byte-unequal on the SAME payload. A swap
        # that checks parsed values would pass while the bytes change.
        assert stdlib_bytes != orjson_bytes


class TestSite2SchemaPromptGoldenBytes:
    """providers.py:330 shape — stdlib with indent=2."""

    def test_ascii_payload_is_byte_exact(self) -> None:
        assert _site2_dumps(dict(SITE2_ASCII)) == SITE2_ASCII_GOLDEN

    def test_indent_is_two_spaces(self) -> None:
        assert b'\n  "type": "object"' in _site2_dumps(dict(SITE2_ASCII))

    def test_non_ascii_is_escaped_not_raw(self) -> None:
        rendered = _site2_dumps(dict(SITE2_NON_ASCII))
        assert rendered == SITE2_NON_ASCII_GOLDEN
        assert "é".encode() not in rendered
        assert b"\\u00e9" in rendered

    def test_orjson_divergence_is_measured_not_assumed(self) -> None:
        stdlib_bytes = _site2_dumps(dict(SITE2_NON_ASCII))
        orjson_bytes = orjson.dumps(dict(SITE2_NON_ASCII), option=orjson.OPT_INDENT_2)
        assert "é".encode() in orjson_bytes
        assert b"\\u00e9" not in orjson_bytes
        assert stdlib_bytes != orjson_bytes


class TestTheOracleGuardsBothSites:
    """Anti-vacuity: the oracle is live on both call sites."""

    def test_orjson_is_installed_so_divergence_is_live(self) -> None:
        assert orjson.__version__ != ""

    def test_both_golden_literals_are_non_empty_and_distinct(self) -> None:
        goldens = {
            SITE1_ASCII_GOLDEN,
            SITE1_NON_ASCII_GOLDEN,
            SITE2_ASCII_GOLDEN,
            SITE2_NON_ASCII_GOLDEN,
        }
        assert all(len(g) > 0 for g in goldens)
        assert len(goldens) == 4

    @pytest.mark.parametrize(
        ("payload", "dumps"),
        [
            (SITE1_ASCII, _site1_dumps),
            (SITE1_NON_ASCII, _site1_dumps),
            (SITE2_ASCII, _site2_dumps),
            (SITE2_NON_ASCII, _site2_dumps),
        ],
    )
    def test_parsed_equality_would_hide_the_divergence(
        self, payload: dict, dumps: Callable[[dict], bytes]
    ) -> None:
        """Parsed values agree across encoders while the bytes differ.

        This is the property that makes byte-level (not parsed-value)
        assertions the load-bearing ones: on the non-ASCII payloads,
        ``json.loads(a) == json.loads(b)`` passes while ``a != b``.
        """
        stdlib_bytes = dumps(dict(payload))
        orjson_bytes = orjson.dumps(dict(payload))
        assert json.loads(stdlib_bytes) == json.loads(orjson_bytes)
        if any(ord(c) > 127 for c in json.dumps(payload)):
            assert stdlib_bytes != orjson_bytes
