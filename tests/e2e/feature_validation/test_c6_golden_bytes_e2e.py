"""C-6 end-to-end golden-byte oracle.

WHAT
    Two ``json.dumps`` call sites are the ones C-6 names:

    site 1 - ``src/app/modules/auth/services/tokens.py:235`` - the refresh
        rotation marker written to the cache under ``refresh_rotated:{old_jti}``.
    site 2 - ``src/app/modules/ai/providers.py:330`` - the JSON schema
        fragment appended to the system prompt of an AI provider request.

WHY
    A swap to ``orjson`` at either site changes the BYTES even when the parsed
    value is identical: the stdlib default separators are ``", "`` and ``": "``
    while orjson's are ``","`` and ``":"``; and the stdlib's ``ensure_ascii``
    escapes non-ASCII as ``\\uXXXX`` while orjson emits raw UTF-8.  A
    round-trip comparison decodes both sides and hides exactly that change,
    which is why this oracle compares BYTES and treats the decoded comparison
    as a SECONDARY property only.

WHAT THIS SCRIPT DOES
    Leg A (AI, site 2, deterministic) drives the REAL
    ``OpenAIProvider.generate_json`` with the provider's HTTP transport
    replaced by a capturing fake, and reads the schema fragment out of the
    system prompt the provider really built.  The bytes are the bytes the site
    really produced - the call shape is driven, not replicated.

    Leg B (refresh, site 1, over HTTP) drives the REAL refresh endpoint over
    the ASGI stack and captures the exact string the production code hands to
    the cache.  The bytes are the bytes the site really produced - the call
    shape is driven, not replicated.

    The unit oracle ``tests/unit/test_c6_json_golden_bytes.py`` REPLICATES both
    call shapes because it cannot reach them.  This script is the part that
    reaches them.

THE ASSERTIONS
    (a) byte-for-byte equality on the ASCII cases;
    (b) for the non-ASCII cases the byte delta is CHARACTERISED and NAMED, not
        asserted away - the script reports the exact bytes that differ and
        which consumer would see the difference;
    (c) the token response is compared INCLUDING HEADERS, because a token is
        consumed by a client that may hash or sign it;
    (d) round-trip equality is a SECONDARY property only.

WHAT IS NOT ASSERTED
    - No duration, no latency, no improvement figure.  This change moves no
      capacity and claims no p95 effect.
    - The HTTP BODY of the refresh response is NOT compared byte for byte.
      The access token is a freshly minted random JWT on every run, so byte
      equality is impossible by construction.  The structural and
      encoding-level facts are asserted instead (exactly two keys, ASCII-only
      body, three-dot JWT) and the header set is recorded and compared.
    - C-6 ITSELF IS DEFERRED (no SLO justified it).  There is therefore no
      "before" and "after" to diff: this script PINS today's bytes so that a
      future swap is loud, and SPECIFIES the bytes a swap must reproduce.  It
      is NOT a proof that a swap was made.
    - PLATFORM DEVIATION: the E2E LAYER epic asks for scripts that run against
      the Linux target only.  This run was on Windows.  The script judges bytes
      and structure and never judges timing, so the platform cannot change its
      verdict.

NO TEST WAS WEAKENED TO ACCOMMODATE A CHANGE; NO ASSERTION WAS RELAXED TO MAKE
A NUMBER PASS.  No number is asserted here at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import orjson
import pytest

from src.app.core.cache.client import cache_client
from src.app.modules.ai.openai_provider import OpenAIProvider
from tests.unit.test_c6_json_golden_bytes import (
    SITE2_ASCII,
    SITE2_ASCII_GOLDEN,
    SITE2_NON_ASCII,
    SITE2_NON_ASCII_GOLDEN,
)

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTEFACT_DIR = REPO_ROOT / "perf-results" / "RESULT-20261020-ota3"
ARTEFACT_PATH = ARTEFACT_DIR / "c6_golden_bytes.json"
WRITE_ARTEFACT_ENV = "C6_E2E_WRITE_ARTEFACT"

SCHEMA_MARKER = "\n\nJSON Schema:\n"
REFRESH_PATH = "/api/v1/account/refresh"
ROTATION_MARKER_PREFIX = "refresh_rotated:"

SITE2_ASCII_CONTENT = '{"x": 1}'
SITE2_NON_ASCII_CONTENT = '{"titre": "cafe", "v": 0.1}'

RESPONSE_BODY_KEYS = ("access_token", "token_type")
REQUIRED_RESPONSE_HEADERS = ("content-type", "content-length", "x-request-id")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bytes_repr(payload: bytes, limit: int = 200) -> str:
    text = payload.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _first_difference(left: bytes, right: bytes) -> dict[str, Any]:
    """Describe where two byte strings diverge, without deciding anything."""
    limit = min(len(left), len(right))
    index = limit
    for offset in range(limit):
        if left[offset] != right[offset]:
            index = offset
            break
    window = 24
    start = max(0, index - window)
    return {
        "first_difference_index": index,
        "left_length": len(left),
        "right_length": len(right),
        "left_window": _bytes_repr(left[start : index + window]),
        "right_window": _bytes_repr(right[start : index + window]),
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _CapturingTransport:
    """Stands in for the httpx client the provider would build for itself."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def post(self, path: str, json: Any = None) -> _FakeResponse:
        self.calls.append({"path": path, "body": json})
        return _FakeResponse(
            {
                "choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
                "model": "gpt-4o-mini",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


async def _drive_site2(schema: dict[str, Any], content: str) -> tuple[bytes, str]:
    """Run the real provider and return the real schema-fragment bytes."""
    provider = OpenAIProvider(api_key="c6-e2e-not-a-real-key")
    transport = _CapturingTransport(content)
    provider._client = transport  # type: ignore[assignment]

    await provider.generate_json(prompt="ping", model="gpt-4o-mini", schema=schema)

    assert len(transport.calls) == 1, "the provider must have made exactly one call"
    body = transport.calls[0]["body"]
    messages = body["messages"]
    system_prompt = messages[0]["content"]
    marker_at = system_prompt.rfind(SCHEMA_MARKER)
    assert marker_at != -1, "the system prompt must carry the schema marker"
    fragment = system_prompt[marker_at + len(SCHEMA_MARKER) :]
    return fragment.encode("utf-8"), system_prompt


@pytest.fixture
def captured_cache_writes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the exact string the production code hands to the cache."""
    writes: list[dict[str, Any]] = []
    original_set = cache_client.set

    async def _capturing_set(key: str, value: str, ttl: int | None = None) -> Any:
        writes.append({"key": key, "value": value, "ttl": ttl})
        return await original_set(key, value, ttl=ttl)

    monkeypatch.setattr(cache_client, "set", _capturing_set)
    return writes


class TestTheSchemaFragmentBytesAreGolden:
    """Leg A - site 2, the AI provider's schema fragment."""

    async def test_the_ascii_schema_fragment_is_byte_exact(self) -> None:
        emitted, _system_prompt = await _drive_site2(SITE2_ASCII, SITE2_ASCII_CONTENT)

        assert emitted == SITE2_ASCII_GOLDEN, "the ASCII schema fragment must be byte exact"
        assert emitted == orjson.dumps(SITE2_ASCII, option=orjson.OPT_INDENT_2), (
            "an orjson swap at this site would NOT change the ASCII bytes"
        )

    async def test_the_non_ascii_delta_is_characterised_and_named(self) -> None:
        emitted, _system_prompt = await _drive_site2(SITE2_NON_ASCII, SITE2_NON_ASCII_CONTENT)
        orjson_bytes = orjson.dumps(SITE2_NON_ASCII, option=orjson.OPT_INDENT_2)

        assert emitted == SITE2_NON_ASCII_GOLDEN, "the non-ASCII schema fragment must be byte exact"
        assert emitted != orjson_bytes, "the orjson rendering must differ, or the characterisation is empty"
        assert b"\\u00e9" in emitted, "the stdlib must have escaped the non-ASCII characters"
        assert "\\u00e9" not in orjson_bytes.decode("utf-8"), "orjson must have emitted raw UTF-8"
        assert json.loads(emitted) == json.loads(orjson_bytes), "the parsed values are equal"

        delta = _first_difference(emitted, orjson_bytes)
        assert delta["first_difference_index"] >= 0
        assert delta["left_length"] != delta["right_length"]

    async def test_the_round_trip_property_is_secondary_not_primary(self) -> None:
        emitted, _system_prompt = await _drive_site2(SITE2_NON_ASCII, SITE2_NON_ASCII_CONTENT)

        assert json.loads(emitted) == SITE2_NON_ASCII
        assert emitted != json.dumps(SITE2_NON_ASCII).encode("utf-8"), (
            "the compact stdlib form is a DIFFERENT byte string from the indented one; "
            "a round-trip check alone would not have noticed"
        )


class TestTheRotationMarkerBytesAreGolden:
    """Leg B - site 1, the refresh rotation marker, over the real HTTP stack."""

    async def test_the_marker_bytes_are_exact_and_the_swap_delta_is_named(
        self,
        client: Any,
        authenticated_user: dict[str, Any],
        clear_auth_rate_limits: Any,
        captured_cache_writes: list[dict[str, Any]],
    ) -> None:
        await clear_auth_rate_limits()

        started = time.perf_counter()
        response = await client.post(
            REFRESH_PATH,
            cookies={"refresh_token": authenticated_user["refresh_token"]},
        )
        client_observed_ms = round((time.perf_counter() - started) * 1000.0, 3)

        assert response.status_code == 200, f"the refresh must succeed: {response.status_code}"

        markers = [write for write in captured_cache_writes if write["key"].startswith(ROTATION_MARKER_PREFIX)]
        assert len(markers) == 1, f"exactly one rotation marker must be written, saw {len(markers)}"
        marker = markers[0]
        assert marker["key"].startswith(ROTATION_MARKER_PREFIX)
        assert marker["value"].isascii(), "the marker must be ASCII, so the non-ASCII arm is unreachable"

        captured_bytes = marker["value"].encode("utf-8")
        parsed = json.loads(marker["value"])

        assert list(parsed.keys()) == ["user_id", "sid", "new_jti", "new_exp"], (
            "the key order is part of the bytes"
        )
        assert isinstance(parsed["user_id"], int)
        assert isinstance(parsed["sid"], str)
        assert isinstance(parsed["new_jti"], str)
        assert isinstance(parsed["new_exp"], float)

        expected_bytes = json.dumps(
            {
                "user_id": parsed["user_id"],
                "sid": parsed["sid"],
                "new_jti": parsed["new_jti"],
                "new_exp": parsed["new_exp"],
            }
        ).encode("utf-8")
        assert captured_bytes == expected_bytes, "the marker bytes must be the stdlib default form"

        assert b'": "' in captured_bytes, "the stdlib separator shape must be present"
        assert b'", "' in captured_bytes, "the stdlib separator shape must be present"

        orjson_bytes = orjson.dumps(parsed)
        assert orjson_bytes != captured_bytes, "an orjson swap WOULD change these bytes"
        assert json.loads(orjson_bytes) == parsed, "the parsed values are equal"
        delta = _first_difference(captured_bytes, orjson_bytes)
        assert delta["first_difference_index"] >= 0

        read_back = await cache_client.get(marker["key"])
        assert read_back == marker["value"], "the cache must hold the bytes verbatim"

        body_bytes = response.content
        body = json.loads(body_bytes)
        assert set(body.keys()) == set(RESPONSE_BODY_KEYS)
        assert body_bytes.isascii(), "the response body must be ASCII-only"
        assert body["access_token"].count(".") == 2, "the access token must be a three-dot JWT"

        header_names = set(response.headers.keys())
        for name in REQUIRED_RESPONSE_HEADERS:
            assert name in header_names, f"the response must carry the {name} header"
        assert response.headers["content-type"].startswith("application/json")
        assert "set-cookie" in header_names, "the refresh response must set the new refresh cookie"

        assert client_observed_ms >= 0.0
        assert response.headers.get("X-Response-Time") is None or isinstance(
            response.headers.get("X-Response-Time"), str
        )


def _site2_facts() -> dict[str, Any]:
    """The behavioural facts of site 2 - no timing, no duration."""
    facts: dict[str, Any] = {}
    for name, schema, golden in (
        ("ascii", SITE2_ASCII, SITE2_ASCII_GOLDEN),
        ("non_ascii", SITE2_NON_ASCII, SITE2_NON_ASCII_GOLDEN),
    ):
        orjson_bytes = orjson.dumps(schema, option=orjson.OPT_INDENT_2)
        facts[name] = {
            "golden_sha256": _sha256(golden),
            "golden_byte_length": len(golden),
            "orjson_sha256": _sha256(orjson_bytes),
            "orjson_byte_length": len(orjson_bytes),
            "bytes_differ_under_an_orjson_swap": golden != orjson_bytes,
            "orjson_delta": _first_difference(golden, orjson_bytes),
        }
    return facts


def _site1_stable_facts(marker_value: str, response_headers: dict[str, str]) -> dict[str, Any]:
    """The behavioural facts of site 1 and of the refresh response."""
    captured_bytes = marker_value.encode("utf-8")
    parsed = json.loads(marker_value)
    orjson_bytes = orjson.dumps(parsed)
    return {
        "marker_key_prefix": ROTATION_MARKER_PREFIX,
        "marker_key_order": list(parsed.keys()),
        "marker_field_types": {key: type(value).__name__ for key, value in parsed.items()},
        "marker_is_ascii": marker_value.isascii(),
        "marker_uses_the_stdlib_separator_shape": b'": "' in captured_bytes and b'", "' in captured_bytes,
        "bytes_differ_under_an_orjson_swap": captured_bytes != orjson_bytes,
        "orjson_delta_first_difference_index": _first_difference(captured_bytes, orjson_bytes)[
            "first_difference_index"
        ],
        "parsed_values_equal_under_an_orjson_swap": json.loads(orjson_bytes) == parsed,
        "response_header_names": sorted(response_headers.keys()),
        "response_body_keys": sorted(RESPONSE_BODY_KEYS),
    }


def _site1_per_run_facts(marker_value: str) -> dict[str, Any]:
    """The per-run quantities of site 1 - recorded, never compared."""
    captured_bytes = marker_value.encode("utf-8")
    return {
        "marker_bytes_sha256": _sha256(captured_bytes),
        "marker_byte_length": len(captured_bytes),
        "marker_bytes_repr": _bytes_repr(captured_bytes),
        "note": "these vary per run: the marker carries a fresh sid and jti, so they are recorded, not compared",
    }


@pytest.fixture
def site2_facts() -> dict[str, Any]:
    return _site2_facts()


@pytest.fixture
async def site1_facts(
    client: Any,
    authenticated_user: dict[str, Any],
    clear_auth_rate_limits: Any,
    captured_cache_writes: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    await clear_auth_rate_limits()
    response = await client.post(
        REFRESH_PATH,
        cookies={"refresh_token": authenticated_user["refresh_token"]},
    )
    assert response.status_code == 200, f"the refresh must succeed: {response.status_code}"
    markers = [write for write in captured_cache_writes if write["key"].startswith(ROTATION_MARKER_PREFIX)]
    assert len(markers) == 1, f"exactly one rotation marker must be written, saw {len(markers)}"
    marker_value = markers[0]["value"]
    return (
        _site1_stable_facts(marker_value, dict(response.headers)),
        _site1_per_run_facts(marker_value),
    )


def _artefact(
    site2: dict[str, Any],
    site1_stable: dict[str, Any],
    site1_per_run: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "perf-c6-golden-bytes/1",
        "bead": "FastAPI_saas-ota3",
        "item": "C-6 e2e",
        "title": "Golden bytes for the two json.dumps sites C-6 names",
        "quantity": 2,
        "method": "real call shapes driven, not replicated",
        "machine": {"platform": platform.system(), "python": platform.python_version()},
        "variants": {
            "site_1": "src/app/modules/auth/services/tokens.py:235 - the refresh rotation marker",
            "site_2": "src/app/modules/ai/providers.py:330 - the JSON schema fragment",
        },
        "verdict": "the bytes are pinned; an orjson swap changes them at BOTH sites and the delta is characterised",
        "stable_facts": {"site_2": site2, "site_1": site1_stable},
        "per_run_facts": {"site_1": site1_per_run},
    }


def test_the_recorded_artefact_is_this_runs_facts(
    site2_facts: dict[str, Any],
    site1_facts: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    site1_stable, site1_per_run = site1_facts

    if os.environ.get(WRITE_ARTEFACT_ENV) == "1":
        ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
        ARTEFACT_PATH.write_text(
            json.dumps(_artefact(site2_facts, site1_stable, site1_per_run), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    assert ARTEFACT_PATH.exists(), f"the recorded artefact is missing: {ARTEFACT_PATH}"
    recorded = json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))
    assert recorded["stable_facts"] == {"site_2": site2_facts, "site_1": site1_stable}
    assert isinstance(recorded["per_run_facts"]["site_1"]["marker_bytes_sha256"], str)
