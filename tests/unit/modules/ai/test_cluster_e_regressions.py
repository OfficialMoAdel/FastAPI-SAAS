"""Cluster E regressions: the AI provider must not log a whole model body.

A JSON parse failure means the model answered with prose where a JSON
document was expected. That prose is whatever the caller's prompt made it
echo, and the log sanitizer only masks SECRET-SHAPED values (JWTs, API keys,
emails, cards, ...) - free-form prose is not one of those, so it survives
masking intact and lands in the log verbatim.

The fix caps the logged body rather than dropping the log: an operator still
needs to see the shape of the failure, and the full length is reported
alongside so "truncated" is never mistaken for "short".
"""

from __future__ import annotations

import pytest
import structlog

from src.app.modules.ai.providers import (
    MAX_LOGGED_BODY_CHARS,
    AIProvider,
    AIProviderError,
    _truncate_body_for_log,
)


class _EchoProvider(AIProvider):
    """Minimal provider that only exercises the JSON extraction path."""

    @property
    def provider_name(self) -> str:
        return "test-provider"

    async def generate_text(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    def generate_text_stream(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError
        yield ""  # pragma: no cover - makes this an async generator

    async def embed_text(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def list_models(self) -> list[str]:  # pragma: no cover - unused
        return []

    async def health_check(self) -> bool:  # pragma: no cover - unused
        return True


class TestTruncateBodyForLog:
    """The helper caps the body and says how much it dropped."""

    def test_short_body_is_untouched(self) -> None:
        assert _truncate_body_for_log("hello") == "hello"

    def test_body_at_the_limit_is_untouched(self) -> None:
        body = "x" * MAX_LOGGED_BODY_CHARS

        assert _truncate_body_for_log(body) == body

    def test_long_body_is_capped_and_the_drop_is_reported(self) -> None:
        body = "x" * (MAX_LOGGED_BODY_CHARS + 500)

        result = _truncate_body_for_log(body)

        assert len(result) < len(body)
        assert result.startswith("x" * MAX_LOGGED_BODY_CHARS)
        assert "500 more chars" in result


class TestParseFailureLogging:
    """The parse failure still logs, but only the capped body."""

    def test_logged_body_is_capped(self) -> None:
        provider = _EchoProvider()
        body = "SECRET_PROSE " + ("y" * (MAX_LOGGED_BODY_CHARS + 1000))

        with structlog.testing.capture_logs() as captured:
            with pytest.raises(AIProviderError):
                provider._extract_json(body)

        errors = [entry for entry in captured if entry.get("event", "").startswith("Failed to parse JSON")]
        assert errors, "the parse failure must still be logged"

        logged = errors[0]["content"]
        assert len(logged) < len(body)
        assert "more chars" in logged
        # The original length is reported, so a capped log is never mistaken
        # for a short body.
        assert errors[0]["content_length"] == len(body)

    def test_short_body_is_logged_in_full(self) -> None:
        provider = _EchoProvider()

        with structlog.testing.capture_logs() as captured:
            with pytest.raises(AIProviderError):
                provider._extract_json("not valid json")

        errors = [entry for entry in captured if entry.get("event", "").startswith("Failed to parse JSON")]
        assert errors
        assert errors[0]["content"] == "not valid json"
        assert errors[0]["content_length"] == len("not valid json")
