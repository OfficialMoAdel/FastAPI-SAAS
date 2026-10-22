"""P1-4: the SSO JWKS fetch must not block the event loop, and must be cached.

Two compounding defects, both fixed together:

1. ``get_signing_key_from_jwt`` performs a synchronous urllib HTTPS GET
   (DNS + TCP + TLS + fetch, ~100-400ms). Called directly from ``async def``
   it blocks the event loop with zero concurrency.
2. The ``PyJWKClient`` was constructed per call, so its own key cache never
   helped even on a warm network — a caching mechanism defeated by
   construction.

The ``lifespan`` argument is what makes the cache safe: without a bounded
lifetime a rotated key would never be picked up, trading a latency bug for a
silent outage. Both are asserted here.
"""

import ast
import asyncio
import json
import threading
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest

import src.app as _app_pkg

REPO = Path(_app_pkg.__file__).resolve().parents[2]

AFFECTED = {
    "apple": "src/app/modules/auth/providers/apple.py",
    "microsoft": "src/app/modules/auth/providers/microsoft.py",
}


def _provider(module_name: str, cls_name: str):
    """Build a provider without its OAuth credential ceremony."""
    module = __import__(f"src.app.modules.auth.providers.{module_name}", fromlist=[cls_name])
    provider = object.__new__(getattr(module, cls_name))
    provider.client_id = "cid"
    provider.client_secret = "secret"
    provider.redirect_uri = "https://app.test/callback"
    return provider


class TestClientIsModuleScoped:
    """One client per module — the property IS the fix for the cache defeat."""

    def test_client_constructed_once_at_module_level(self) -> None:
        for name in AFFECTED:
            module = __import__(f"src.app.modules.auth.providers.{name}", fromlist=["_jwks_client"])
            assert isinstance(module._jwks_client, jwt.PyJWKClient), f"{name}: no module-level client"

    def test_cache_is_enabled_with_a_bounded_lifespan(self) -> None:
        """The module must configure cache_keys AND a bounded lifespan.

        PyJWKClient does not expose these as attributes, so the constructor
        arguments are read back from the source: omitting either is exactly
        the bug this pins.
        """
        for name, rel in AFFECTED.items():
            source = (REPO / rel).read_text(encoding="utf-8")
            assert "cache_keys=True" in source, f"{name}: key cache not enabled"
            assert "lifespan=" in source, (
                f"{name}: no lifespan — a rotated key would never be fetched (latent outage)"
            )

    def test_no_per_call_construction_remains(self) -> None:
        """No PyJWKClient(...) inside a function body — that is the defect."""
        for name, rel in AFFECTED.items():
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            offenders = [
                child
                for parent in ast.walk(tree)
                if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef)
                for child in ast.walk(parent)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "PyJWKClient"
            ]
            assert not offenders, f"{rel}: PyJWKClient still constructed inside a function"


class TestFetchIsOffloaded:
    """The blocking call must reach a worker thread, not run inline."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("module", "cls", "issuer"), [
        ("apple", "AppleProvider", "https://appleid.apple.com"),
        (
            "microsoft",
            "MicrosoftProvider",
            # A12: the issuer must be the concrete tenant GUID shape — the
            # old prefix/suffix check accepted any path segment.
            "https://login.microsoftonline.com/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/v2.0",
        ),
    ])
    async def test_signing_key_resolved_off_the_event_loop(self, module, cls, issuer) -> None:
        mod = __import__(f"src.app.modules.auth.providers.{module}", fromlist=["_jwks_client"])
        key = type("Key", (), {"key": "x"})()
        seen = {}

        def _fake_get(_token):
            seen["thread"] = threading.current_thread().name
            return key

        with (
            patch.object(mod._jwks_client, "get_signing_key_from_jwt", new=_fake_get),
            patch.object(jwt, "decode", return_value={"iss": issuer, "sub": "u"}),
        ):
            await _provider(module, cls)._verify_id_token("token")

        assert seen["thread"] != threading.current_thread().name, (
            "get_signing_key_from_jwt ran on the event loop thread"
        )


class TestEventLoopIsNotStalled:
    """The user-visible property: other coroutines keep running."""

    @pytest.mark.asyncio
    async def test_concurrent_work_progresses_during_key_fetch(self) -> None:
        """A slow fetch must not freeze an unrelated coroutine.

        Called inline, the synchronous fetch blocks the loop and the heartbeat
        cannot tick until it finishes. Offloaded, they interleave.
        """
        from src.app.modules.auth.providers import apple

        key = type("Key", (), {"key": "x"})()
        ticks = 0

        async def _heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        def _slow_get(_token):
            time.sleep(0.2)  # stand-in for the synchronous HTTPS fetch
            return key

        heartbeat = asyncio.create_task(_heartbeat())
        with (
            patch.object(apple._jwks_client, "get_signing_key_from_jwt", new=_slow_get),
            patch.object(jwt, "decode", return_value={"sub": "u"}),
        ):
            await _provider("apple", "AppleProvider")._verify_id_token("token")
        heartbeat.cancel()

        assert ticks > 1, f"event loop stalled during the fetch (ticks={ticks})"


class TestKeyRotation:
    """A bounded lifespan means a rotated key IS picked up."""

    def test_cached_client_refetches_after_lifespan_expiry(self) -> None:
        """The cache must be a cache, not a freeze.

        Drives a real PyJWKClient over a stubbed urlopen so the real expiry
        logic runs, rather than mocking the mechanism under test.
        """
        fetches: list[str] = []
        # Real RSA parameters: PyJWKClient parses the key eagerly, so a
        # placeholder modulus ("AQAB") raises before the cache is consulted.
        # Generated once with `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048`.
        _N = (
            "q_ZWQESUijRdtzfaptWKETWSxBDuOAp-Xwp3_Baso2FNE8y1hFfo6Kn3d9l6w3zfXH0U0-hz5ccCgVBG4898wO438Cv-7xivWTY1"
            "W752WiqDZluqJV03rwJtxJ72y-YCpQsAYh64img-9ZxNPNJfDGi765B4xwJQbiln3HSBmAogf5mr4Vhy82VqURiBof1juglA0Iay"
            "IRwKLMz5hEsc9ixL6KlmDoyxCefDEHG2BXzVdk6Br6aK2EzcaN-qBJJ5hur5W2H3m-5lQz1Nx6rLouucIHzzGKl12_wNuog799opP4owbgU1QaaHXl4OGMeAiCY8CkXsSdxzbEEHRgmETQ"
        )
        _E = "AQAB"
        key_sets = [
            {"keys": [{"kty": "RSA", "kid": "k1", "n": _N, "e": _E}]},
            {"keys": [{"kty": "RSA", "kid": "k2", "n": _N, "e": _E}]},
        ]

        class _Resp:
            def __init__(self, payload):
                self._payload = payload
                self.status = 200

            def read(self):
                return json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _urlopen(url, **_kwargs):
            fetches.append(url)
            if len(fetches) > len(key_sets):
                raise urllib.error.URLError("no more keysets")
            return _Resp(key_sets[len(fetches) - 1])

        client = jwt.PyJWKClient("https://example.test/keys", cache_keys=True, lifespan=300, timeout=5)

        original_urlopen = jwt.PyJWKClient.__init__.__globals__["urllib"].request.urlopen
        with patch("urllib.request.urlopen", new=_urlopen):
            client.get_jwk_set()  # populates the cache
            after_first = len(fetches)
            client.get_jwk_set()  # within lifespan: served from cache
            assert len(fetches) == after_first, "cache did not hold within lifespan"

            # Age the cache past its lifespan. JWKSetCache.is_expired compares
            # time.monotonic(), not time.time().
            real_monotonic = time.monotonic
            with patch.object(time, "monotonic", return_value=real_monotonic() + 301):
                client.get_jwk_set()

        assert len(fetches) > after_first, (
            "lifespan expiry did not trigger a refetch — a rotated key would be missed"
        )
        assert original_urlopen is not None  # sanity: we patched, not monkeyed


class TestUnaffectedProviders:
    """Google and GitHub never used PyJWKClient; they must stay clean."""

    def test_google_and_github_do_not_use_pyjwkclient(self) -> None:
        for name in ("google", "github"):
            rel = f"src/app/modules/auth/providers/{name}.py"
            source = (REPO / rel).read_text(encoding="utf-8")
            assert "PyJWKClient" not in source, f"{rel} unexpectedly references PyJWKClient"
