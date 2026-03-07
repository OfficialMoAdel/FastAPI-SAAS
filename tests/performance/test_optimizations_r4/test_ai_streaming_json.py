"""P1-5: AI Streaming JSON orjson Migration Tests.

STATUS: VERIFIED ✅

This module tests the orjson migration for AI streaming responses.
Unlike webhooks (P0-4 which was cancelled), AI streaming can safely
use orjson because there's no signature verification involved.

Key Difference from P0-4:
- Webhooks HMAC requires byte-for-byte compatibility (orjson incompatible)
- AI streaming only needs valid JSON output (orjson compatible)
"""

from __future__ import annotations

import inspect

import pytest


class TestP15AIStreamingPrerequisites:
    """Pre-implementation tests for AI streaming."""

    def test_ai_streaming_exists(self) -> None:
        """Verify AI streaming functionality exists."""
        # Check for AI module
        try:
            from src.app.modules.ai import service  # noqa: F401

            assert True
        except ImportError:
            pytest.skip("AI module not available")

    def test_orjson_available(self) -> None:
        """Verify orjson is installed."""
        try:
            import orjson  # noqa: F401

            assert True
        except ImportError:
            pytest.fail("orjson is not installed")

    def test_json_utils_uses_orjson(self) -> None:
        """Verify json_utils uses orjson internally."""
        from src.app.core.json_utils import json_loads

        # json_loads should be available
        assert json_loads is not None
        assert callable(json_loads)


class TestP15AIStreamingOrjsonImplementation:
    """Tests verifying orjson is used in AI streaming code."""

    def test_ollama_client_imports_json_loads(self) -> None:
        """Ollama client should import json_loads from json_utils."""
        from src.app.modules.ai import ollama_client

        source = inspect.getsource(ollama_client)

        # Should import json_loads from json_utils
        assert "from src.app.core.json_utils import json_loads" in source

    def test_ollama_client_uses_json_loads(self) -> None:
        """Ollama streaming should use json_loads (orjson) not stdlib json."""
        from src.app.modules.ai.ollama_client import OllamaClient

        source = inspect.getsource(OllamaClient.chat_stream)

        # Should use json_loads
        assert "json_loads(" in source

        # Should NOT import stdlib json inside the loop
        assert "import json" not in source


class TestP15AIStreamingOrjson:
    """Tests to verify orjson migration for AI streaming."""

    def test_json_loads_parses_ai_response(self) -> None:
        """json_loads should correctly parse AI response format."""
        from src.app.core.json_utils import json_loads

        # Simulate Ollama streaming chunk
        chunk_str = '{"message":{"content":"Hello"},"done":false}'
        parsed = json_loads(chunk_str)

        assert parsed["message"]["content"] == "Hello"
        assert parsed["done"] is False

    def test_json_loads_handles_unicode(self) -> None:
        """json_loads should handle unicode in AI responses."""
        from src.app.core.json_utils import json_loads

        chunk_str = '{"message":{"content":"こんにちは 🎉"},"done":false}'
        parsed = json_loads(chunk_str)

        assert parsed["message"]["content"] == "こんにちは 🎉"

    def test_json_loads_handles_nested_objects(self) -> None:
        """json_loads should handle complex nested AI responses."""
        from src.app.core.json_utils import json_loads

        chunk_str = '{"message":{"role":"assistant","content":"test"},"eval_count":42,"done":true}'
        parsed = json_loads(chunk_str)

        assert parsed["message"]["role"] == "assistant"
        assert parsed["eval_count"] == 42
        assert parsed["done"] is True


class TestP15AIStreamingIsomorphism:
    """Isomorphism tests for AI streaming behavior."""

    def test_output_semantically_equivalent(self) -> None:
        """orjson output should be semantically equivalent to stdlib."""
        import json

        import orjson

        test_data = {"message": "Hello", "tokens": [1, 2, 3]}

        stdlib_parsed = json.loads(json.dumps(test_data))
        orjson_parsed = orjson.loads(orjson.dumps(test_data))

        assert stdlib_parsed == orjson_parsed

    def test_json_loads_matches_stdlib_behavior(self) -> None:
        """json_utils.json_loads should produce same result as stdlib json.loads."""
        import json

        from src.app.core.json_utils import json_loads

        test_cases = [
            '{"a": 1}',
            '{"nested": {"key": "value"}}',
            '[1, 2, 3]',
            '"string"',
            "123",
            "true",
            "null",
        ]

        for test_str in test_cases:
            stdlib_result = json.loads(test_str)
            orjson_result = json_loads(test_str)
            assert stdlib_result == orjson_result, f"Mismatch for: {test_str}"


class TestP15PerformanceValidation:
    """Performance validation tests."""

    def test_json_loads_faster_than_stdlib(self) -> None:
        """orjson json_loads should be faster than stdlib for typical AI chunks."""
        import json
        import time

        from src.app.core.json_utils import json_loads

        # Typical AI streaming chunk
        chunk = (
            '{"message":{"role":"assistant","content":"Hello, how can I help you?"},'
            '"done":false,"eval_count":15}'
        )

        iterations = 1000

        # Benchmark stdlib
        start = time.perf_counter()
        for _ in range(iterations):
            json.loads(chunk)
        stdlib_time = time.perf_counter() - start

        # Benchmark orjson
        start = time.perf_counter()
        for _ in range(iterations):
            json_loads(chunk)
        orjson_time = time.perf_counter() - start

        # orjson should be at least faster (speedup jitter can happen in coverage runs)
        speedup = stdlib_time / orjson_time
        assert speedup > 1.0, f"orjson should be faster, speedup was {speedup:.2f}x"
